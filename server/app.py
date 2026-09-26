"""FastAPI application construction and route registration."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from server.api.routes.batches import batch_http_error, create_batch_router
from server.api.routes.editor import create_editor_router
from server.api.routes.health import router as health_router
from server.api.routes.instances import create_instance_router
from server.api.routes.internal_translation import (
    execute_batch_stream,
    router as internal_translation_router,
    simple_execute_batch,
)
from server.api.routes.manual import router as manual_router
from server.api.routes.manga import create_manga_group_router, create_manga_router
from server.api.routes.manga_export import create_manga_export_router
from server.api.routes.manga_management import create_manga_management_router
from server.api.routes.pipeline_reruns import create_pipeline_rerun_router
from server.api.routes.reading_progress import create_reading_progress_router
from server.api.routes.result_files import create_result_files_router
from server.api.routes.result_import import create_result_import_router
from server.api.routes.result_pages import create_result_pages_router
from server.api.routes.series import create_series_router
from server.api.routes.summaries import create_summary_control_router, create_summary_read_router
from server.api.routes.translation import create_translation_router
from server.api.routes.translation_batch import router as translation_batch_router
from server.search_api import search_router


def create_app(runtime) -> FastAPI:
    """Build the app while keeping ``server.main`` as the compatibility runtime."""
    app = FastAPI(lifespan=runtime.lifespan)
    runtime.app = app
    app.include_router(search_router(lambda: runtime.search_service))
    app.include_router(search_router(lambda: runtime.search_service), prefix="/api")
    app.middleware("http")(runtime.serialize_original_manga_imports)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(runtime.correlation_id_middleware)

    runtime._instance_router, runtime.register_instance = create_instance_router(
        lambda: runtime.nonce, runtime.executor_instances
    )
    app.include_router(runtime._instance_router)

    runtime._translation_router, translation_exports = create_translation_router(
        lambda *args, **kwargs: runtime.get_ctx(*args, **kwargs),
        lambda *args, **kwargs: runtime.while_streaming(*args, **kwargs),
        lambda ctx: runtime.to_translation(ctx),
        lambda ctx: runtime._index_context_result(ctx),
        lambda raw_config: runtime.Config.parse_raw(raw_config),
        lambda config, raw_config: runtime._apply_manga_title_alias(config, raw_config),
    )
    _bind(runtime, translation_exports, (
        "translate_json", "translate_bytes", "translate_image", "stream_json",
        "stream_bytes", "stream_image", "json_form", "bytes_form", "image_form",
        "stream_json_form", "stream_bytes_form", "stream_image_form",
        "stream_image_form_web",
    ))
    app.include_router(runtime._translation_router)
    app.include_router(health_router)

    runtime._batch_router, batch_exports = create_batch_router(
        lambda: runtime.batch_store,
        lambda: runtime.batch_scheduler,
        max_items=runtime.MAX_BATCH_ITEMS,
        max_item_bytes=runtime.MAX_BATCH_ITEM_BYTES,
        max_upload_bytes=runtime.MAX_BATCH_UPLOAD_BYTES,
    )
    _bind(runtime, batch_exports, (
        "list_batches", "_batch_events", "batch_events", "get_batch", "put_batch",
        "get_batch_input", "pause_batch", "resume_batch", "dismiss_batch",
        "delete_batch", "update_batch", "retry_batch_item", "update_batch_item",
        "delete_batch_item",
    ))
    app.include_router(runtime._batch_router)

    runtime._result_files_router, result_file_exports = create_result_files_router(
        lambda: runtime._postgres(),
        lambda: runtime.RESULT_ROOT,
        lambda path: runtime.final_file(path),
        lambda path, name: runtime.find_asset(path, name),
        lambda path, name: runtime.generate_reader_asset(path, name),
        lambda *args, **kwargs: runtime.generate_image_variants(*args, **kwargs),
        lambda *args, **kwargs: runtime._ensure_bbox_artifact(*args, **kwargs),
        lambda path, name: runtime._ensure_thumbnail_artifact(path, name),
    )
    _bind(runtime, result_file_exports, (
        "get_pipeline_manifest", "get_result_file_by_folder", "get_result_by_folder",
    ))
    app.include_router(runtime._result_files_router)

    runtime._pipeline_rerun_router, rerun_exports = create_pipeline_rerun_router(
        lambda: runtime._postgres(),
        lambda: runtime.RESULT_ROOT,
        lambda: runtime.LEGACY_RESULT_ROOT,
        lambda *args, **kwargs: runtime._scan_results(*args, **kwargs),
        lambda: runtime.batch_store,
        lambda: runtime.batch_scheduler,
        batch_http_error,
    )
    _bind(runtime, rerun_exports, ("rerun_pipeline", "rerender_results"))
    app.include_router(runtime._pipeline_rerun_router)

    runtime._editor_router, editor_exports = create_editor_router(
        runtime._postgres,
        lambda: runtime.RESULT_ROOT,
        runtime.run_cpu_stage,
        runtime.find_asset,
        runtime.final_file,
        runtime.save_jpeg,
        lambda path: runtime._get_cached_meta(path),
        lambda folder=None: runtime._invalidate_meta_cache(folder),
        lambda regions: runtime._review_status_for_regions(regions),
        lambda folder, needs_review: runtime._sync_batch_review(folder, needs_review),
    )
    _bind(runtime, editor_exports, ("layout_preview", "save_edits", "update_page_review"))
    app.include_router(runtime._editor_router)
    app.include_router(translation_batch_router)
    app.include_router(manual_router)
    app.include_router(internal_translation_router)

    runtime._manga_group_router, runtime.list_result_groups = create_manga_group_router(
        runtime._postgres, lambda: runtime.RESULT_ROOT, runtime._scan_manga_groups
    )
    app.include_router(runtime._manga_group_router)

    runtime._series_router, series_exports = create_series_router(runtime._postgres_required)
    _bind(runtime, series_exports, (
        "list_series", "get_series", "create_series", "update_series",
        "replace_series_members", "add_series_members", "move_manga_series",
        "remove_manga_series", "delete_series", "get_manga_series",
    ))
    app.include_router(runtime._series_router)

    runtime._reading_progress_router, progress_exports = create_reading_progress_router(
        runtime._postgres, runtime._validate_installation_id
    )
    _bind(runtime, progress_exports, ("get_reading_progress", "save_reading_progress"))
    app.include_router(runtime._reading_progress_router)

    runtime._result_import_router, import_exports = create_result_import_router(
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
    _bind(runtime, import_exports, ("import_original_manga",))
    app.include_router(runtime._result_import_router)

    runtime._summary_read_router, summary_read_exports = create_summary_read_router(
        runtime._postgres,
        lambda: runtime.RESULT_ROOT,
        runtime.group_pages,
        runtime._summary_status_for,
        runtime.list_summary_jobs,
    )
    _bind(runtime, summary_read_exports, (
        "get_manga_summary", "get_manga_summary_config", "get_manga_summary_jobs",
        "_summary_job_events", "manga_summary_job_events",
    ))
    app.include_router(runtime._summary_read_router)

    runtime._summary_control_router, summary_control_exports = create_summary_control_router(
        runtime._postgres,
        lambda: runtime.RESULT_ROOT,
        runtime.group_pages,
        runtime._summary_status_for,
        runtime._update_summary_job_for,
        lambda: runtime._summary_controller,
        lambda: runtime.summary_scheduler,
        lambda *args, **kwargs: runtime._run_summary_task(*args, **kwargs),
        runtime._summary_submit_lock,
        runtime.resolve_summary_model,
        runtime.read_page_text,
        runtime.is_page_text_extracted,
        runtime.dismiss_summary_job,
        runtime._summary_log,
    )
    _bind(runtime, summary_control_exports, (
        "dismiss_manga_summary_job", "pause_manga_summary_job",
        "resume_manga_summary_job", "stop_manga_summary_job", "create_manga_summary",
    ))
    app.include_router(runtime._summary_control_router)

    runtime._manga_router, manga_exports = create_manga_router(
        runtime._postgres, lambda: runtime.RESULT_ROOT, runtime._scan_results
    )
    _bind(runtime, manga_exports, ("list_results", "list_manga_pages"))
    app.include_router(runtime._manga_router)

    runtime._manga_management_router, management_exports = create_manga_management_router(
        runtime._postgres,
        lambda: runtime.RESULT_ROOT,
        runtime._reorder_file_backed_pages,
        runtime._update_file_backed_meta,
        runtime.final_file,
        runtime._invalidate_meta_cache,
        runtime.rename_summary,
        runtime.remove_summary,
    )
    _bind(runtime, management_exports, (
        "reorder_manga_pages", "update_meta", "delete_manga_group_endpoint",
    ))
    app.include_router(runtime._manga_management_router)

    runtime._manga_export_router, export_exports = create_manga_export_router(
        runtime._postgres,
        lambda: runtime.RESULT_ROOT,
        lambda value: runtime.natural_keys(value),
        lambda metadata: runtime.meta_page_order(metadata),
        lambda metadata: runtime._source_type(metadata),
        lambda folder: runtime._input_file(folder),
        lambda folder: runtime.final_file(folder),
    )
    _bind(runtime, export_exports, (
        "_build_cbz_archive", "create_cbz_stream", "export_cbz_post", "export_cbz_get",
    ))
    app.include_router(runtime._manga_export_router)

    runtime._result_pages_router, page_exports = create_result_pages_router(
        runtime._postgres,
        lambda: runtime.RESULT_ROOT,
        runtime.final_file,
        runtime._get_cached_meta,
        runtime._input_file,
        runtime._source_type,
        runtime.generate_image_variants,
        runtime._image_urls,
        runtime.asset_version,
        runtime.find_asset,
        runtime.meta_page_order,
        runtime._review_status,
        runtime._manga_id,
        runtime._invalidate_meta_cache,
        runtime._compact_file_backed_group,
        runtime._delete_file_backed_results,
    )
    _bind(runtime, page_exports, ("clear_results", "get_result_detail", "delete_result", "delete_results"))
    app.include_router(runtime._result_pages_router)

    if runtime.RESULT_ROOT.exists():
        app.mount("/result", StaticFiles(directory=str(runtime.RESULT_ROOT)), name="result")
    return app


def _bind(runtime, values, names: tuple[str, ...]) -> None:
    for name, value in zip(names, values):
        setattr(runtime, name, value)
