"""Register the legacy and queued manga import routes."""

from server.api.routes.manga_import_jobs import create_manga_import_job_router
from server.api.routes.result_import import (
    create_manga_import_job_processor,
    create_result_import_router,
)


def register_manga_import_routes(app, runtime) -> None:
    import_dependencies = (
        lambda: runtime._postgres(),
        lambda: runtime.RESULT_ROOT,
        lambda: runtime.MAX_BATCH_ITEMS,
        lambda: runtime.MAX_BATCH_ITEM_BYTES,
        lambda: runtime.MAX_MANGA_TITLE_LENGTH,
        lambda title: bool(runtime.group_pages(runtime.RESULT_ROOT, title)),
        lambda title: runtime._manga_id(title),
        lambda *args, **kwargs: runtime._write_original_import(*args, **kwargs),
        lambda *args, **kwargs: runtime._iter_original_upload_pages(*args, **kwargs),
        lambda root, title, imported: runtime._compact_file_backed_group(root, title, imported),
        lambda root, folder, metadata: runtime._write_file_backed_meta(root / folder, metadata),
        lambda *args, **kwargs: runtime._scan_manga_groups(*args, **kwargs),
        lambda *args, **kwargs: runtime._scan_results(*args, **kwargs),
        lambda: runtime._invalidate_meta_cache(),
        lambda folders: runtime._warm_preview_variants(folders),
        runtime.logger,
    )
    runtime._result_import_router, import_exports = create_result_import_router(
        *import_dependencies
    )
    runtime.import_original_manga = import_exports[0]
    processor_dependencies = (
        *import_dependencies[:10],
        lambda root, title: runtime.group_pages(root, title),
        *import_dependencies[10:],
    )
    runtime.process_manga_import_job = create_manga_import_job_processor(
        *processor_dependencies[:2], *processor_dependencies[5:]
    )
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
