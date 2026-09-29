"""Resumable local embedding jobs and manga-level hybrid retrieval with Typesense."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from urllib.parse import quote

from server.search_embeddings import (
    CANDIDATE_LIMIT, COLLECTION, DIMENSIONS, PROFILE,
    TEXT_MODEL, TEXT_REVISION, SearchEncoders,
    fingerprint, point_id, rank_results,
)
from server.search_store import SearchStore, decoded
from server.search_typesense import SearchTypesense

logger = logging.getLogger("manga-translator.search")


class SearchService:
    def __init__(self, store, executor_provider=lambda: None, client=None, encoders=None, typesense_client=None):
        self.db = SearchStore(store)
        self.executor_provider = executor_provider
        self.executor = None
        self.owns_executor = False
        self.encoders = encoders or SearchEncoders()
        self.typesense = typesense_client or (client if isinstance(client, SearchTypesense) else SearchTypesense(client=client))
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
        await self.typesense.close()

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
            self.schema_ready = False
            await self.typesense.ensure_collection(reset=reset)
            self.schema_ready = True

    async def status(self):
        error = None
        try:
            if not await self.typesense.health():
                error = "Typesense service is unhealthy. Start the search service and retry."
        except Exception as exc:
            error = f"Typesense is unavailable. Start the search service and retry. {type(exc).__name__}: {exc}"
        from manga_translator.utils.device_memory import get_memory_stats
        memory = get_memory_stats(self.encoders.device)
        try:
            import resource
            memory["process_peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024), 2)
        except ImportError:
            pass
        return {
            "available": error is None, "error": error, "engine": "typesense",
            "device": self.encoders.device or "Not loaded (CUDA preferred, then MPS, CPU fallback)",
            "profile": PROFILE, "models": {"summary": TEXT_MODEL},
            "revisions": {"summary": TEXT_REVISION},
            "queryLimits": {"summary": 512},
            "jobs": await self.db.jobs(), "metrics": self.metrics, "memory": memory,
        }

    async def submit(self, group_ids=None, resume_id=None):
        async with self.submit_lock:
            if self.task and not self.task.done():
                raise ValueError("An embedding job is already running")
            await self.ensure_collection()
            job_id = resume_id if resume_id else await self.db.create_job(group_ids)
            if resume_id:
                await self.db.resume(resume_id)
            self.task = asyncio.create_task(self.run_job(job_id))
            return (await self.db.jobs(job_id))[0]

    async def cancel(self, job_id):
        result = await self.db.pool.execute("UPDATE search_jobs SET cancel_requested=true WHERE id=$1 AND status IN ('queued','running')", job_id)
        if result == "UPDATE 0":
            raise ValueError("No active embedding job with that ID")

    async def remove_group(self, group_id=None):
        async with self.submit_lock:
            if self.task and not self.task.done():
                raise ValueError("Wait for embedding to finish before removing indexed data")
            async with self.write_lock:
                await self.ensure_collection(reset=group_id is None)
                if group_id is None:
                    return await self.db.delete_sources()
                await self.typesense.delete_group(group_id)
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
        if not status or not (status.get("summary") or "").strip():
            return "skipped", "Generate a manga summary in Gallery, then resume."
        text = status["summary"]
        version = fingerprint(text)
        metadata = {}
        previous = await self.db.source(key)
        if previous and previous["fingerprint"] == version and previous["profile"] == PROFILE:
            docs = await self.typesense.retrieve_documents(previous["point_ids"])
            if len(docs) == len(previous["point_ids"]):
                async with self.write_lock:
                    await self._activate(item, version, previous["point_ids"], metadata)
                return "unchanged", None
        started = time.monotonic()
        chunks = await self.infer(self.encoders.chunks, text, background=True)
        ids = [point_id(key, version, i) for i in range(len(chunks))]
        for offset in range(0, len(chunks), 4):
            if await self.db.pool.fetchval("SELECT cancel_requested FROM search_jobs WHERE id=$1", item["job_id"]):
                return "pending", None
            batch = chunks[offset:offset + 4]
            values = [chunk["text"] for chunk in batch]
            vectors = await self.infer(self.encoders.encode, "summary", values, background=True)
            docs = [{
                "id": ids[offset + i], "sourceKey": key, "groupId": item["group_id"],
                "title": status.get("title") or item.get("title") or "",
                "content": chunk.get("text", ""), "chunkIndex": offset + i,
                "start": chunk.get("start", 0), "end": chunk.get("end", 0),
                "fingerprint": version, "profile": PROFILE, "published": False,
                "embedding": vector,
            } for i, (chunk, vector) in enumerate(zip(batch, vectors))]
            await self.typesense.upsert_chunks(docs)
        if await self.db.pool.fetchval("SELECT cancel_requested FROM search_jobs WHERE id=$1", item["job_id"]):
            return "pending", None
        latest = await self.db.store.summary_status(item["group_id"])
        stable = bool(latest and fingerprint(latest.get("summary") or "") == version)
        if not stable:
            await self.typesense.delete_documents(ids)
            return "error", "Source changed during embedding. Resume to index its current version."
        async with self.write_lock:
            await self._activate(item, version, ids, metadata)
        self.metrics["embeddingSeconds"] += time.monotonic() - started
        self.metrics["embeddedItems"] += 1
        return "complete", None

    async def _activate(self, item, version, ids, metadata):
        await self.typesense.publish_chunks(ids, item["group_id"])
        await self.db.save_source(item, version, ids, metadata)
        await self.typesense.delete_source_except(item["source_key"], ids)

    async def reconcile(self):
        await self.db.pool.execute("DELETE FROM search_job_items WHERE modality<>'summary'")
        rows = await self.db.pool.fetch("""
            SELECT s.* FROM search_sources s
            LEFT JOIN manga_groups g ON g.id=s.group_id
            WHERE g.id IS NULL OR s.modality<>'summary'
            """)
        for row in rows:
            await self.typesense.delete_source(row["source_key"])
            await self.db.pool.execute("DELETE FROM search_sources WHERE source_key=$1", row["source_key"])

    async def grouped(self, vector, query="", group_ids=None, limit=CANDIDATE_LIMIT, num_typos=2, alpha=0.7, hybrid_rerank=True):
        if group_ids is not None and not group_ids:
            return {}
        while True:
            found = await self.typesense.hybrid_search(
                query=query, query_vector=vector, group_ids=group_ids, limit=limit,
                num_typos=num_typos, alpha=alpha, hybrid_rerank=hybrid_rerank, profile=PROFILE,
            )
            keys = list({hit["sourceKey"] for hits in found.values() for hit in hits if "sourceKey" in hit})
            rows = await self.db.pool.fetch("""
                SELECT s.* FROM search_sources s JOIN manga_groups g ON g.id=s.group_id
                WHERE s.source_key=ANY($1::text[]) AND s.modality='summary'
                """, keys)
            sources = {row["source_key"]: row for row in rows}
            invalid = []
            result = {}
            for group_id, hits in found.items():
                valid = []
                for hit in hits:
                    source = sources.get(hit["sourceKey"])
                    if not source or str(hit["id"]) not in decoded(source["point_ids"], []) or source["group_id"] != hit["groupId"]:
                        invalid.append(hit["id"])
                    else:
                        valid.append(hit)
                if valid:
                    result[group_id] = sorted(valid, key=lambda h: -h["score"])
            if not invalid:
                return result
            await self.typesense.delete_documents(invalid)

    async def query(self, query, group_ids=None, limit=20, typo_tolerance=2, alpha=0.7, hybrid_rerank=True):
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
            embedding_ms = round((time.monotonic() - embed_started) * 1000)
            retrieval_started = time.monotonic()
            async with self.write_lock:
                await self.reconcile()
                summary_groups = await self.grouped(vector, query=query, group_ids=group_ids, limit=CANDIDATE_LIMIT, num_typos=typo_tolerance, alpha=alpha, hybrid_rerank=hybrid_rerank)
                ranked_initial = rank_results(summary_groups, limit=CANDIDATE_LIMIT)
                ids = [group_id for group_id, _ in ranked_initial]
            retrieval_ms = round((time.monotonic() - retrieval_started) * 1000)
            coverage = (await self.db.manga(group_ids=ids, limit=max(50, len(ids))))["items"] if ids else []
            by_id = {item["id"]: item for item in coverage}
            initial_results = []
            for group_id, score in ranked_initial:
                if group_id not in by_id:
                    continue
                summary = summary_groups.get(group_id, [])
                best_hit = summary[0] if summary else {}
                initial_results.append({
                    "rank": len(initial_results) + 1, "groupId": group_id, "title": by_id[group_id]["title"],
                    "summarySimilarity": float(score), "vectorDistance": best_hit.get("vectorDistance"),
                    "textMatch": best_hit.get("textMatch"), "rankFusionScore": best_hit.get("rankFusionScore"),
                    "excerpt": best_hit.get("content") or best_hit.get("excerpt"),
                    "coverage": by_id[group_id], "readerUrl": f"/read?manga={quote(group_id, safe='')}",
                })
            results = [{**item, "initialRank": item["rank"], "rankDelta": 0} for item in initial_results[:limit]]
            elapsed_ms = round((time.monotonic() - started) * 1000)
            scope_coverage = await self.db.coverage(group_ids)
            return {
                "query": query,
                "retrieval": {
                    "engine": "typesense", "mode": "hybrid", "candidateLimit": CANDIDATE_LIMIT,
                    "alpha": alpha, "typoTolerance": typo_tolerance, "hybridRerank": hybrid_rerank,
                },
                "initialResults": initial_results, "results": results,
                "timings": {
                    "embeddingMs": embedding_ms, "retrievalMs": retrieval_ms, "elapsedMs": elapsed_ms,
                },
                "elapsedMs": elapsed_ms, "embeddingMs": embedding_ms, "retrievalMs": retrieval_ms,
                "partial": any(result["coverage"]["partial"] for result in initial_results),
                "indexing": bool(self.task and not self.task.done()), "profile": PROFILE, "coverage": scope_coverage,
            }
        finally:
            self.searches -= 1
            if not self.searches:
                self.searches_done.set()
