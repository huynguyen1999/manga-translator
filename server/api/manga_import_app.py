"""Register the legacy and queued manga import routes."""

from server.api.routes.manga_import_jobs import create_manga_import_job_router
from server.api.routes.result_import import (
    create_manga_import_job_processor,
    create_result_import_router,
)
from server.original_import_writer import write_original_import as write_original_import_job


def register_manga_import_routes(app, runtime) -> None:
    import_dependencies = {
        "get_store": lambda: runtime._postgres(),
        "get_result_root": lambda: runtime.RESULT_ROOT,
        "get_max_batch_items": lambda: runtime.MAX_BATCH_ITEMS,
        "get_max_batch_item_bytes": lambda: runtime.MAX_BATCH_ITEM_BYTES,
        "get_max_title_length": lambda: runtime.MAX_MANGA_TITLE_LENGTH,
        "has_file_backed_group": lambda title: bool(runtime.group_pages(runtime.RESULT_ROOT, title)),
        "manga_id": lambda title: runtime._manga_id(title),
        "write_original_import": lambda *args, **kwargs: runtime._write_original_import(*args, **kwargs),
        "iter_original_upload_pages": lambda *args, **kwargs: runtime._iter_original_upload_pages(*args, **kwargs),
        "compact_file_backed_group": lambda root, title, imported: runtime._compact_file_backed_group(root, title, imported),
        "write_file_backed_meta": lambda root, folder, metadata: runtime._write_file_backed_meta(root / folder, metadata),
        "scan_manga_groups": lambda *args, **kwargs: runtime._scan_manga_groups(*args, **kwargs),
        "scan_results": lambda *args, **kwargs: runtime._scan_results(*args, **kwargs),
        "invalidate_meta_cache": lambda: runtime._invalidate_meta_cache(),
        "warm_preview_variants": lambda folders: runtime._warm_preview_variants(folders),
        "logger": runtime.logger,
    }
    runtime._result_import_router, import_exports = create_result_import_router(**import_dependencies)
    runtime.import_original_manga = import_exports[0]
    processor_dependencies = {
        name: import_dependencies[name]
        for name in (
            "get_store", "get_result_root", "has_file_backed_group", "manga_id",
            "iter_original_upload_pages", "compact_file_backed_group", "write_file_backed_meta",
            "scan_manga_groups", "scan_results", "invalidate_meta_cache", "warm_preview_variants", "logger",
        )
    }
    processor_dependencies.update(
        write_original_import=lambda title, pages, group_id=None, *, import_job_id: write_original_import_job(
            title, pages, group_id, result_root=runtime.RESULT_ROOT, logger=runtime.logger,
            save_jpeg=runtime.save_jpeg, import_job_id=import_job_id,
        ),
        file_backed_group_pages=lambda root, title: runtime.group_pages(root, title),
    )
    runtime.process_manga_import_job = create_manga_import_job_processor(**processor_dependencies)
    app.include_router(runtime._result_import_router)

    runtime._manga_import_job_router, job_exports = create_manga_import_job_router(
        lambda: runtime.manga_import_job_queue,
        lambda: runtime.MAX_BATCH_ITEMS,
        lambda: runtime.MAX_BATCH_ITEM_BYTES,
        lambda: runtime.MAX_MANGA_IMPORT_BYTES,
        lambda: runtime.MAX_MANGA_TITLE_LENGTH,
    )
    for name, value in zip((
        "create_manga_import_job", "list_manga_import_jobs", "get_manga_import_job",
        "retry_manga_import_job", "dismiss_manga_import_job",
    ), job_exports):
        setattr(runtime, name, value)
    app.include_router(runtime._manga_import_job_router)
