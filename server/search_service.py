"""Resumable local embedding jobs and manga-level semantic retrieval."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import time
from urllib.parse import quote

from server.search_embeddings import (
    COLLECTION, DIMENSIONS, IMAGE_MODEL, IMAGE_REVISION, PROFILE, TEXT_MODEL,
    TEXT_REVISION, VECTOR_NAMES, SearchEncoders, fingerprint, point_id, rank_results,
)
from server.search_store import SearchStore, decoded

logger = logging.getLogger("manga-translator.search")


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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

    async def ensure_collection(self):
        if self.schema_ready:
            return
        async with self.collection_lock:
            if self.schema_ready:
                return
            from qdrant_client import models
            client = self.qdrant()
            if not await client.collection_exists(COLLECTION):
                await client.create_collection(COLLECTION, vectors_config={
                    VECTOR_NAMES[modality]: models.VectorParams(size=size, distance=models.Distance.COSINE, on_disk=True)
                    for modality, size in DIMENSIONS.items()
                })
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
            "profile": PROFILE, "models": {"summary": TEXT_MODEL, "image": IMAGE_MODEL},
            "revisions": {"summary": TEXT_REVISION, "image": IMAGE_REVISION},
            "queryLimits": {"summary": 512, "image": 64, "combined": 64},
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
        modality, key = item["modality"], item["source_key"]
        if modality == "summary":
            status = await self.db.store.summary_status(item["group_id"])
            if not status or not (status.get("summary") or "").strip() or status.get("stale"):
                return "skipped", "Generate a fresh manga summary in Gallery, then resume."
            text = status["summary"]
            version = fingerprint(text)
            metadata = {}
        else:
            source = await self.db.image_source(item["page_id"])
            if not source or not source["path"]:
                return "skipped", "Original image is missing; translated images are never substituted."
            item["group_id"] = source["manga_group_id"]
            version = await asyncio.to_thread(hash_file, source["path"])
            metadata = {"signature": source["signature"]}
        previous = await self.db.source(key)
        if previous and previous["fingerprint"] == version and previous["profile"] == PROFILE:
            points = await self.qdrant().retrieve(COLLECTION, previous["point_ids"], with_payload=True)
            if len(points) == len(previous["point_ids"]):
                async with self.write_lock:
                    await self._activate(item, version, previous["point_ids"], metadata)
                return "unchanged", None
        started = time.monotonic()
        if modality == "summary":
            chunks = await self.infer(self.encoders.chunks, text, background=True)
        else:
            chunks = [{"text": None}]
        from qdrant_client import models
        ids = [point_id(key, version, i) for i in range(len(chunks))]
        # ponytail: batches of four fit the M2 baseline; tune only after measuring peak memory.
        for offset in range(0, len(chunks), 4):
            if await self.db.pool.fetchval("SELECT cancel_requested FROM search_jobs WHERE id=$1", item["job_id"]):
                return "pending", None
            batch = chunks[offset:offset + 4]
            values = [chunk["text"] for chunk in batch] if modality == "summary" else [source["path"]]
            vectors = await self.infer(self.encoders.encode, modality, values, background=True)
            points = [models.PointStruct(id=ids[offset + i], vector={VECTOR_NAMES[modality]: vector}, payload={
                "sourceKey": key, "groupId": item["group_id"], "pageId": item["page_id"],
                "profile": PROFILE, "fingerprint": version, "published": False,
                "excerpt": chunk.get("text"), "start": chunk.get("start"), "end": chunk.get("end"),
            }) for i, (chunk, vector) in enumerate(zip(batch, vectors))]
            await self.qdrant().upsert(COLLECTION, points=points, wait=True)
        if await self.db.pool.fetchval("SELECT cancel_requested FROM search_jobs WHERE id=$1", item["job_id"]):
            return "pending", None
        # Do not publish a mixed snapshot if content changed while the model was running.
        if modality == "summary":
            latest = await self.db.store.summary_status(item["group_id"])
            stable = bool(latest and not latest.get("stale") and fingerprint(latest.get("summary") or "") == version)
        else:
            latest = await self.db.image_source(item["page_id"])
            stable = bool(latest and latest["signature"] == metadata["signature"] and latest["manga_group_id"] == item["group_id"])
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
        """Remove deleted records; repair page ownership without recomputing vectors."""
        from qdrant_client import models
        rows = await self.db.pool.fetch("""
            SELECT s.*, p.manga_group_id AS current_group FROM search_sources s
            LEFT JOIN manga_groups g ON g.id=s.group_id LEFT JOIN pages p ON p.id=s.page_id AND p.active
            WHERE g.id IS NULL OR (s.modality='image' AND (p.id IS NULL OR p.manga_group_id<>s.group_id))
            """)
        for row in rows:
            selector = models.FilterSelector(filter=models.Filter(must=[models.FieldCondition(key="sourceKey", match=models.MatchValue(value=row["source_key"]))]))
            if row["modality"] == "image" and row["current_group"]:
                await self.qdrant().set_payload(COLLECTION, {"groupId": row["current_group"]}, points=selector, wait=True)
                await self.db.pool.execute("UPDATE search_sources SET group_id=$2 WHERE source_key=$1", row["source_key"], row["current_group"])
            else:
                await self.qdrant().delete(COLLECTION, selector, wait=True)
                await self.db.pool.execute("DELETE FROM search_sources WHERE source_key=$1", row["source_key"])

    async def grouped(self, modality, vector, group_ids=None, limit=100):
        from qdrant_client import models
        conditions = [models.FieldCondition(key="published", match=models.MatchValue(value=True)),
                      models.FieldCondition(key="profile", match=models.MatchValue(value=PROFILE))]
        if group_ids is not None:
            if not group_ids:
                return {}
            conditions.append(models.FieldCondition(key="groupId", match=models.MatchAny(any=group_ids)))
        # Refetch after removing uncommitted/orphan hits so they cannot crowd out valid manga.
        while True:
            found = await self.qdrant().query_points_groups(COLLECTION, query=vector, using=VECTOR_NAMES[modality],
                query_filter=models.Filter(must=conditions), group_by="groupId", limit=limit,
                group_size=3 if modality == "image" else 1, with_payload=True)
            hits = [hit for group in found.groups for hit in group.hits]
            keys = list({hit.payload["sourceKey"] for hit in hits})
            rows = await self.db.pool.fetch("""
                SELECT s.* FROM search_sources s JOIN manga_groups g ON g.id=s.group_id
                LEFT JOIN pages p ON p.id=s.page_id AND p.active
                WHERE s.source_key=ANY($1::text[]) AND (s.modality='summary' OR p.manga_group_id=s.group_id)
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
                    result[str(group.id)] = sorted(valid, key=lambda hit: (-hit["score"], hit.get("pageId") or ""))
            if not invalid:
                return result
            await self.qdrant().delete(COLLECTION, models.PointIdsList(points=invalid), wait=True)

    async def query(self, query, mode, group_ids=None, limit=20):
        started = time.monotonic()
        self.searches += 1
        self.searches_done.clear()
        try:
            if group_ids is not None:
                known = await self.db.pool.fetchval("SELECT count(*) FROM manga_groups WHERE id=ANY($1::text[])", group_ids)
                if known != len(set(group_ids)):
                    raise ValueError("Some selected manga no longer exist. Refresh the collection and selection.")
            await self.ensure_collection()
            modalities = ["summary", "image"] if mode == "combined" else [mode]
            vectors = {modality: (await self.infer(self.encoders.encode, modality, [query], query=True))[0] for modality in modalities}
            async with self.write_lock:
                await self.reconcile()
                groups = {modality: await self.grouped(modality, vector, group_ids) for modality, vector in vectors.items()}
                ranked = rank_results(groups.get("summary", {}), groups.get("image", {}), mode, limit)
                ids = [group_id for group_id, _ in ranked]
                if mode == "combined" and ids:
                    for modality in modalities:
                        missing = [group_id for group_id in ids if group_id not in groups[modality]]
                        if missing:
                            groups[modality].update(await self.grouped(modality, vectors[modality], missing, len(missing)))
            coverage = (await self.db.manga(group_ids=ids, limit=50))["items"] if ids else []
            by_id = {item["id"]: item for item in coverage}
            results = []
            for group_id, score in ranked:
                if group_id not in by_id:
                    continue
                summary = groups.get("summary", {}).get(group_id, [])
                images = groups.get("image", {}).get(group_id, [])
                pages = []
                for hit in images:
                    source = await self.db.image_source(hit["pageId"])
                    if source and source["path"] and source["manga_group_id"] == group_id:
                        pages.append({"pageId": hit["pageId"], "pageNumber": source["page_order"], "similarity": hit["score"],
                            "imageUrl": f"/result/{quote(source['id'], safe='')}/{quote(source['path'].name, safe='')}",
                            "readerUrl": f"/gallery/pages/{quote(source['id'], safe='')}"})
                results.append({"rank": len(results) + 1, "groupId": group_id, "title": by_id[group_id]["title"],
                    "summarySimilarity": summary[0]["score"] if summary else None,
                    "imageSimilarity": images[0]["score"] if images else None,
                    "combinedScore": score if mode == "combined" else None,
                    "excerpt": summary[0].get("excerpt") if summary else None,
                    "pages": pages, "coverage": by_id[group_id],
                    "readerUrl": f"/read?manga={quote(group_id, safe='')}"})
            scope_coverage = await self.db.coverage(group_ids)
            return {"results": results, "mode": mode, "query": query, "elapsedMs": round((time.monotonic() - started) * 1000),
                    "partial": any(result["coverage"]["partial"] for result in results),
                    "indexing": bool(self.task and not self.task.done()), "profile": PROFILE,
                    "coverage": scope_coverage}
        finally:
            self.searches -= 1
            if not self.searches:
                self.searches_done.set()
