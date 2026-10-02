"""Run the durable manga import queue inside the server lifespan."""

from contextlib import asynccontextmanager

from server.manga_import_jobs import MangaImportJobQueue, MangaImportJobStore


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
