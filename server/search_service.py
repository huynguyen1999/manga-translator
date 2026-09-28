"""Resumable local embedding jobs and manga-level semantic retrieval."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from urllib.parse import quote

from server.search_embeddings import (
    CANDIDATE_LIMIT, COLLECTION, DIMENSIONS, PROFILE, RERANK_MODEL,
    RERANK_REVISION, TEXT_MODEL, TEXT_REVISION, VECTOR_NAMES, SearchEncoders,
    fingerprint, point_id, rank_results, rerank_candidates,
)
from server.search_store import SearchStore, decoded

logger = logging.getLogger("manga-translator.search")


class SearchService:
    def __init__(self, store, executor_provider=lambda: None, client=None, encoders=None):
        self.db = SearchStore(store)
        self.executor_provider = executor_provider
        self.executor = None
        self.owns_executor = False
        self.client = client
        self.encoders = encoders or SearchEncoders()
        self.task = None
        self.write_lock = asyncio.Lock()
        self.collection_lock = asyncio.Lock()
        self.inference_lock = asyncio.Lock()
        self.submit_lock = asyncio.Lock()
        self.searches = 0
        self.searches_done = asyncio.Event()
        self.searches_done.set()
        self.schema_ready = False
        self.metrics = {"embeddingSeconds": 0.0, "embeddedItems": 0}

    def qdrant(self):
        if self.client is None:
            try:
                from qdrant_client import AsyncQdrantClient
            except ImportError as error:
                raise RuntimeError("Install requirements-search.txt to enable Search Lab.") from error
            self.client = AsyncQdrantClient(url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), timeout=15)
        return self.client

    async def start(self):
        await self.db.recover()

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.executor:
            async def unload():
                self.encoders.unload()
            await self.executor.run(unload)
            if self.owns_executor:
                await asyncio.to_thread(self.executor.close)
        if self.client:
            await self.client.close()

    async def infer(self, operation, *args, background=False, **kwargs):
        if background:
            await self.searches_done.wait()
        async with self.inference_lock:
            if self.executor is None:
                from manga_translator.utils.model_cache import SharedModelExecutor
                self.executor = self.executor_provider()
                self.owns_executor = self.executor is None
                self.executor = self.executor or SharedModelExecutor()
            async def run():
                return operation(*args, **kwargs)
            return await self.executor.run(run)

    async def ensure_collection(self, reset=False):
        if self.schema_ready and not reset:
            return
        async with self.collection_lock:
            if self.schema_ready and not reset:
                return
            from qdrant_client import models
            client = self.qdrant()
            self.schema_ready = False
            if reset and await client.collection_exists(COLLECTION): await client.delete_collection(COLLECTION)
            if not await client.collection_exists(COLLECTION):
                await client.create_collection(COLLECTION, vectors_config={VECTOR_NAMES[modality]: models.VectorParams(size=size, distance=models.Distance.COSINE, on_disk=True) for modality, size in DIMENSIONS.items()})
            for field in ("groupId", "sourceKey", "profile"):
                await client.create_payload_index(COLLECTION, field, models.PayloadSchemaType.KEYWORD, wait=True)
            # Interrupted staging is never visible; discard it before accepting new work.
            await client.delete(COLLECTION, models.FilterSelector(filter=models.Filter(must=[
                models.FieldCondition(key="published", match=models.MatchValue(value=False))
            ])), wait=True)
            self.schema_ready = True

    async def status(self):
        error = None
        try:
            await self.qdrant().get_collections()
        except Exception as exc:
            error = f"Qdrant is unavailable. Start the search service and retry. {type(exc).__name__}: {exc}"
        from manga_translator.utils.device_memory import get_memory_stats
        memory = get_memory_stats(self.encoders.device)
        try:
            import resource
            memory["process_peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024), 2)
        except ImportError:
            pass
        return {
            "available": error is None, "error": error,
            "device": self.encoders.device or "Not loaded (CUDA preferred, then MPS, CPU fallback)",
            "profile": PROFILE, "models": {"summary": TEXT_MODEL, "reranker": RERANK_MODEL},
            "revisions": {"summary": TEXT_REVISION, "reranker": RERANK_REVISION},
            "queryLimits": {"summary": 512},
            "jobs": await self.db.jobs(), "metrics": self.metrics,
            "memory": memory,
        }

    async def submit(self, group_ids=None, resume_id=None):
        async with self.submit_lock:
            if self.task and not self.task.done():
                raise ValueError("An embedding job is already running")
            await self.ensure_collection()
            if resume_id:
                await self.db.resume(resume_id)
                job_id = resume_id
            else:
                job_id = await self.db.create_job(group_ids)
            self.task = asyncio.create_task(self.run_job(job_id))
            return (await self.db.jobs(job_id))[0]

    async def cancel(self, job_id):
        result = await self.db.pool.execute("UPDATE search_jobs SET cancel_requested=true WHERE id=$1 AND status IN ('queued','running')", job_id)
        if result == "UPDATE 0":
            raise ValueError("No active embedding job with that ID")

    async def remove_group(self, group_id=None):
        async with self.submit_lock:
            if self.task and not self.task.done(): raise ValueError("Wait for embedding to finish before removing indexed data")
            async with self.write_lock:
                await self.ensure_collection(reset=group_id is None)
                if group_id is None: return await self.db.delete_sources()
                from qdrant_client import models
                await self.qdrant().delete(COLLECTION, models.FilterSelector(filter=models.Filter(must=[
                    models.FieldCondition(key="groupId", match=models.MatchValue(value=group_id))
                ])), wait=True)
                return await self.db.delete_sources(group_id)

    async def run_job(self, job_id):
        try:
            await self.db.job_state(job_id, "running")
            await self.reconcile()
            while item := await self.db.pending(job_id):
                if await self.db.pool.fetchval("SELECT cancel_requested FROM search_jobs WHERE id=$1", job_id):
                    await self.db.job_state(job_id, "cancelled")
                    return
                try:
                    outcome, message = await self.index_item(dict(item))
                except (OSError, ValueError) as exc:
                    outcome, message = "error", str(exc)
                    logger.warning("Search item failed: %s: %s", item["source_key"], exc)
                await self.db.finish_item(job_id, item["source_key"], outcome, message)
                await asyncio.sleep(0)
            job = (await self.db.jobs(job_id))[0]
            await self.db.job_state(job_id, "partial" if job["failed"] or job["skipped"] else "complete")
        except asyncio.CancelledError:
            await self.db.job_state(job_id, "interrupted")
            raise
        except Exception as exc:
            logger.exception("Embedding job failed")
            await self.db.job_state(job_id, "error", f"{type(exc).__name__}: {exc}. Fix the service or model issue, then Resume.")

    async def index_item(self, item):
        key = item["source_key"]
        status = await self.db.store.summary_status(item["group_id"])
        if not status or not (status.get("summary") or "").strip() or status.get("stale"):
            return "skipped", "Generate a fresh manga summary in Gallery, then resume."
        text = status["summary"]
        version = fingerprint(text)
        metadata = {}
        previous = await self.db.source(key)
        if previous and previous["fingerprint"] == version and previous["profile"] == PROFILE:
            points = await self.qdrant().retrieve(COLLECTION, previous["point_ids"], with_payload=True)
            if len(points) == len(previous["point_ids"]):
                async with self.write_lock:
                    await self._activate(item, version, previous["point_ids"], metadata)
                return "unchanged", None
        started = time.monotonic()
        chunks = await self.infer(self.encoders.chunks, text, background=True)
        from qdrant_client import models
        ids = [point_id(key, version, i) for i in range(len(chunks))]
        # ponytail: batches of four fit the M2 baseline; tune only after measuring peak memory.
        for offset in range(0, len(chunks), 4):
            if await self.db.pool.fetchval("SELECT cancel_requested FROM search_jobs WHERE id=$1", item["job_id"]):
                return "pending", None
            batch = chunks[offset:offset + 4]
            values = [chunk["text"] for chunk in batch]
            vectors = await self.infer(self.encoders.encode, "summary", values, background=True)
            points = [models.PointStruct(id=ids[offset + i], vector={VECTOR_NAMES["summary"]: vector}, payload={
                "sourceKey": key, "groupId": item["group_id"], "pageId": None,
                "profile": PROFILE, "fingerprint": version, "published": False,
                "excerpt": chunk.get("text"), "start": chunk.get("start"), "end": chunk.get("end"),
            }) for i, (chunk, vector) in enumerate(zip(batch, vectors))]
            await self.qdrant().upsert(COLLECTION, points=points, wait=True)
        if await self.db.pool.fetchval("SELECT cancel_requested FROM search_jobs WHERE id=$1", item["job_id"]):
            return "pending", None
        # Do not publish a mixed snapshot if content changed while the model was running.
        latest = await self.db.store.summary_status(item["group_id"])
        stable = bool(latest and not latest.get("stale") and fingerprint(latest.get("summary") or "") == version)
        if not stable:
            await self.qdrant().delete(COLLECTION, models.PointIdsList(points=ids), wait=True)
            return "error", "Source changed during embedding. Resume to index its current version."
        async with self.write_lock:
            await self._activate(item, version, ids, metadata)
        self.metrics["embeddingSeconds"] += time.monotonic() - started
        self.metrics["embeddedItems"] += 1
        return "complete", None

    async def _activate(self, item, version, ids, metadata):
        from qdrant_client import models
        # Reads validate against the PostgreSQL commit; writes are recoverable on resume.
        await self.qdrant().set_payload(COLLECTION, {"published": True, "groupId": item["group_id"]}, points=ids, wait=True)
        await self.db.save_source(item, version, ids, metadata)
        await self.qdrant().delete(COLLECTION, models.FilterSelector(filter=models.Filter(
            must=[models.FieldCondition(key="sourceKey", match=models.MatchValue(value=item["source_key"]))],
            must_not=[models.HasIdCondition(has_id=ids)],
        )), wait=True)

    async def reconcile(self):
        """Remove deleted records and any legacy non-summary sources."""
        from qdrant_client import models
        await self.db.pool.execute("DELETE FROM search_job_items WHERE modality<>'summary'")
        rows = await self.db.pool.fetch("""
            SELECT s.* FROM search_sources s
            LEFT JOIN manga_groups g ON g.id=s.group_id
            WHERE g.id IS NULL OR s.modality<>'summary'
            """)
        for row in rows:
            selector = models.FilterSelector(filter=models.Filter(must=[models.FieldCondition(key="sourceKey", match=models.MatchValue(value=row["source_key"]))]))
            await self.qdrant().delete(COLLECTION, selector, wait=True)
            await self.db.pool.execute("DELETE FROM search_sources WHERE source_key=$1", row["source_key"])

    async def grouped(self, vector, group_ids=None, limit=CANDIDATE_LIMIT):
        from qdrant_client import models
        conditions = [models.FieldCondition(key="published", match=models.MatchValue(value=True)),
                      models.FieldCondition(key="profile", match=models.MatchValue(value=PROFILE))]
        if group_ids is not None:
            if not group_ids:
                return {}
            conditions.append(models.FieldCondition(key="groupId", match=models.MatchAny(any=group_ids)))
        # Refetch after removing uncommitted/orphan hits so they cannot crowd out valid manga.
        while True:
            found = await self.qdrant().query_points_groups(COLLECTION, query=vector, using=VECTOR_NAMES["summary"],
                query_filter=models.Filter(must=conditions), group_by="groupId", limit=limit,
                group_size=1, with_payload=True)
            hits = [hit for group in found.groups for hit in group.hits]
            keys = list({hit.payload["sourceKey"] for hit in hits})
            rows = await self.db.pool.fetch("""
                SELECT s.* FROM search_sources s JOIN manga_groups g ON g.id=s.group_id
                WHERE s.source_key=ANY($1::text[]) AND s.modality='summary'
                """, keys)
            sources = {row["source_key"]: row for row in rows}
            invalid = []
            result = {}
            for group in found.groups:
                valid = []
                for hit in group.hits:
                    payload = hit.payload
                    source = sources.get(payload["sourceKey"])
                    if not source or str(hit.id) not in decoded(source["point_ids"], []) or source["group_id"] != payload["groupId"]:
                        invalid.append(hit.id)
                    else:
                        valid.append({**payload, "score": float(hit.score)})
                if valid:
                    result[str(group.id)] = sorted(valid, key=lambda hit: -hit["score"])
            if not invalid:
                return result
            await self.qdrant().delete(COLLECTION, models.PointIdsList(points=invalid), wait=True)

    async def query(self, query, group_ids=None, limit=20, min_score=None):
        started = time.monotonic()
        self.searches += 1
        self.searches_done.clear()
        try:
            if group_ids is not None:
                known = await self.db.pool.fetchval("SELECT count(*) FROM manga_groups WHERE id=ANY($1::text[])", group_ids)
                if known != len(set(group_ids)):
                    raise ValueError("Some selected manga no longer exist. Refresh the collection and selection.")
            await self.ensure_collection()
            embed_started = time.monotonic()
            vector = (await self.infer(self.encoders.encode, "summary", [query], query=True))[0]
            async with self.write_lock:
                await self.reconcile()
                summary_groups = await self.grouped(vector, group_ids, limit=CANDIDATE_LIMIT)
                ranked_initial = rank_results(summary_groups, limit=CANDIDATE_LIMIT)
                ids = [group_id for group_id, _ in ranked_initial]
            coverage = (await self.db.manga(group_ids=ids, limit=max(50, len(ids))))["items"] if ids else []
            by_id = {item["id"]: item for item in coverage}
            initial_results = []
            for group_id, score in ranked_initial:
                if group_id not in by_id:
                    continue
                summary = summary_groups.get(group_id, [])
                initial_results.append({
                    "rank": len(initial_results) + 1, "groupId": group_id, "title": by_id[group_id]["title"],
                    "summarySimilarity": float(score), "excerpt": summary[0].get("excerpt") if summary else None,
                    "coverage": by_id[group_id], "readerUrl": f"/read?manga={quote(group_id, safe='')}",
                })
            embedding_ms = round((time.monotonic() - embed_started) * 1000)
            rerank_started = time.monotonic()
            if initial_results:
                rerank_scores = await self.infer(self.encoders.rerank, query, [item["excerpt"] or "" for item in initial_results])
                results = rerank_candidates(initial_results, rerank_scores, limit=limit, min_score=min_score)
            else:
                results = []
            rerank_ms = round((time.monotonic() - rerank_started) * 1000)
            scope_coverage = await self.db.coverage(group_ids)
            return {
                "results": results, "initialResults": initial_results, "query": query, "minScore": min_score,
                "elapsedMs": round((time.monotonic() - started) * 1000), "embeddingMs": embedding_ms, "rerankMs": rerank_ms,
                "partial": any(result["coverage"]["partial"] for result in initial_results),
                "indexing": bool(self.task and not self.task.done()), "profile": PROFILE, "coverage": scope_coverage,
            }
        finally:
            self.searches -= 1
            if not self.searches:
                self.searches_done.set()
