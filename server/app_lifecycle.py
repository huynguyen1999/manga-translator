"""Server startup and shutdown lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path


def create_lifespan(runtime):
    """Create the server lifespan while resolving dependencies from the runtime module."""

    @asynccontextmanager
    async def lifespan(_app):
        runtime._raise_nofile_limit()
        runtime.setup_server_logging()
        await asyncio.to_thread(
            runtime.migrate_legacy_results,
            runtime.LEGACY_RESULT_ROOT,
            runtime.SERVER_RESULT_ROOT,
            runtime.MIGRATION_MAP_PATH,
        )
        runtime.RESULT_ROOT.mkdir(parents=True, exist_ok=True)
        use_postgres = (
            os.getenv("PERSISTENCE_MODE", "postgres").strip().lower() != "filesystem"
            and bool(runtime.DATABASE_URL)
        )
        if use_postgres:
            runtime.postgres_store = runtime.PostgresStore(
                runtime.DATABASE_URL, runtime.SERVER_RESULT_ROOT
            )
            await runtime.postgres_store.start()
            database_loop = asyncio.get_running_loop()

            async def save_documents(folder: str, documents: dict) -> None:
                case_dir = (runtime.SERVER_RESULT_ROOT / folder).resolve()
                if (
                    Path(folder).name == folder
                    and case_dir.parent == runtime.SERVER_RESULT_ROOT.resolve()
                    and (case_dir / ".ai-case").is_file()
                ):
                    for name, payload in documents.items():
                        if Path(name).name == name and name.endswith(".json"):
                            (case_dir / name).write_text(
                                json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                    return
                operation = runtime.postgres_store.save_documents(folder, documents)
                if asyncio.get_running_loop() is database_loop:
                    await operation
                    return
                await asyncio.wrap_future(
                    asyncio.run_coroutine_threadsafe(operation, database_loop)
                )

            runtime.set_document_saver(save_documents)
            runtime.batch_store = runtime.PostgresBatchStore(
                runtime.postgres_store, runtime.BATCH_ROOT, runtime.SERVER_RESULT_ROOT
            )
            runtime.batch_scheduler = runtime.BatchScheduler(
                runtime.batch_store,
                runtime.executor_instances,
                runtime.SERVER_RESULT_ROOT,
                resource_limits=runtime.batch_resource_limits,
                inference_page_batch_size=runtime.inference_page_batch_size,
            )
            runtime.set_result_indexer(runtime.postgres_store.sync_result_folder)
            runtime.set_request_lookup(runtime.postgres_store.find_request)
            await runtime.postgres_store.index_untracked_results()
        else:
            runtime.postgres_store = None
            runtime.batch_store = runtime.BatchStore(
                runtime.BATCH_ROOT, runtime.SERVER_RESULT_ROOT
            )
            runtime.batch_scheduler = runtime.BatchScheduler(
                runtime.batch_store,
                runtime.executor_instances,
                runtime.SERVER_RESULT_ROOT,
                resource_limits=runtime.batch_resource_limits,
                inference_page_batch_size=runtime.inference_page_batch_size,
            )
            runtime.set_document_saver(None)
            runtime.set_result_indexer(None)
            runtime.set_request_lookup(None)
        runtime.summary_scheduler = runtime.SummaryScheduler(
            store_getter=runtime._postgres,
            controller=runtime._summary_controller,
            run_task_fn=runtime._run_summary_task,
            request_cls=runtime.MangaSummaryRequest,
            result_root=runtime.SERVER_RESULT_ROOT,
        )
        await runtime.summary_scheduler.start()
        await runtime.batch_scheduler.start()
        if runtime.postgres_store is not None:
            runtime.search_service = runtime.SearchService(
                runtime.postgres_store,
                lambda: next(
                    (
                        instance._model_executor
                        for instance in runtime.executor_instances.list
                        if hasattr(instance, "_model_executor")
                    ),
                    None,
                ),
            )
            await runtime.search_service.start()
        try:
            yield
        finally:
            if runtime.search_service is not None:
                await runtime.search_service.close()
                runtime.search_service = None
            if runtime.summary_scheduler is not None:
                await runtime.summary_scheduler.stop()
                runtime.summary_scheduler = None
            await runtime.batch_scheduler.stop()
            await runtime.shutdown_cpu_stage_executor()
            runtime.set_result_indexer(None)
            runtime.set_request_lookup(None)
            runtime.set_document_saver(None)
            if runtime.postgres_store is not None:
                await runtime.postgres_store.close()
                runtime.postgres_store = None

    return lifespan
