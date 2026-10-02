"""Run the durable manga import queue inside the server lifespan."""

import asyncio
from contextlib import asynccontextmanager
import shutil

from server.manga_import_jobs import (
    MangaImportJobQueue,
    MangaImportJobStore,
    _lock_file,
    _now_ms,
    _unlock_file,
)

_WORKER_LOCK_KEY = (718239114, 2)


async def acquire_worker_lease(store):
    if store.database is not None:
        store._pool
        import asyncpg

        connection = await asyncpg.connect(dsn=store.database.database_url, command_timeout=30)
        try:
            acquired = await connection.fetchval(
                "SELECT pg_try_advisory_lock($1, $2)", *_WORKER_LOCK_KEY
            )
        except BaseException:
            await connection.close()
            raise
        if not acquired:
            await connection.close()
            return None
        return connection
    return await asyncio.to_thread(
        _lock_file, store.root / ".worker.lock", blocking=False
    )


async def release_worker_lease(store, lease):
    if lease is None:
        return
    if store.database is not None:
        try:
            await lease.execute("SELECT pg_advisory_unlock($1, $2)", *_WORKER_LOCK_KEY)
        finally:
            await lease.close()
    else:
        await asyncio.to_thread(_unlock_file, lease)


async def run_manga_import_queue(queue):
    while not queue._stopping:
        lease = None
        try:
            lease = await queue.store.acquire_worker_lease()
            if lease is None:
                await queue._wait_for_work_or_lease()
                continue

            await queue.store.recover_processing()
            await queue.store.cleanup_staging()
            while not queue._stopping:
                queue._wake.clear()
                job = await queue.store.claim_next()
                if job is None:
                    await queue._wait_for_work_or_lease()
                    continue
                try:
                    async def update_progress(processed: int, total: int | None = None, phase: str | None = None):
                        current = await queue.store.get(job["id"], include_items=False)
                        current["processedPages"] = processed
                        if total is not None:
                            current["totalPages"] = total
                            current["progress"] = round(100 * processed / total) if total else 0
                        if phase:
                            current["phase"] = phase
                        await queue.store.update(current)

                    result = await queue.processor(
                        job, queue.store.stage_path(job["id"]), update_progress
                    )
                    current = await queue.store.get(job["id"], include_items=False)
                    total = int(result.get("totalPages", current.get("totalPages") or 0))
                    current.update(
                        status="completed", completedAt=_now_ms(), processedPages=total,
                        totalPages=total, progress=100, error=None,
                        group=result.get("group"),
                        items=result.get("items", []),
                        groupId=(result.get("group") or {}).get("id"),
                        totalImages=result.get("totalImages"),
                    )
                    await queue.store.update(current)
                    try:
                        await asyncio.to_thread(
                            shutil.rmtree, queue.store.stage_path(job["id"]), True
                        )
                    except Exception:
                        queue.logger.exception(
                            "Failed to clean up completed manga import staging: id=%s",
                            job["id"],
                        )
                except Exception as error:
                    queue.logger.exception("Manga import job failed: id=%s", job["id"])
                    current = await queue.store.get(job["id"], include_items=False)
                    detail = getattr(error, "detail", None)
                    current.update(status="failed", error=str(detail or error), completedAt=None)
                    await queue.store.update(current)
        except Exception:
            queue.logger.exception("Manga import queue worker failed")
            await queue._wait_for_work_or_lease()
        finally:
            if lease is not None:
                await queue.store.release_worker_lease(lease)


@asynccontextmanager
async def running_manga_import_jobs(runtime):
    runtime.manga_import_job_store = MangaImportJobStore(
        runtime.BATCH_ROOT / "manga-import-jobs", runtime._postgres()
    )
    queue = MangaImportJobQueue(
        runtime.manga_import_job_store, runtime.process_manga_import_job, runtime.logger
    )
    runtime.manga_import_job_queue = queue
    try:
        await queue.start()
        yield
    finally:
        await queue.stop()
        runtime.manga_import_job_queue = None
