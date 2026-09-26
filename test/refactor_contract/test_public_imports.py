import server.main as main
from server import constants
from server.batch_store import BatchConflict, BatchNotFound, InvalidBatch
from server.postgres_store import InvalidSeries, SeriesConflict, SeriesNotFound, SeriesStoreError
from server.api.schemas.batches import (
    RetryBatchItemRequest,
    UpdateBatchItemRequest,
    UpdateBatchRequest,
)
from server.api.schemas.editor import (
    LayoutPreviewRequest,
    LayoutSegmentRequest,
    ReviewStatusRequest,
    SaveEditsRequest,
)
from server.api.schemas.manga import (
    DeletePagesRequest,
    ExportCbzRequest,
    ReadingProgressRequest,
    ReorderPagesRequest,
    UpdateMetaRequest,
)
from server.api.schemas.pipeline import PipelineRerunRequest, RerenderRequest
from server.api.schemas.series import (
    AddSeriesMembersRequest,
    CreateSeriesRequest,
    MoveMangaSeriesRequest,
    ReplaceSeriesMembersRequest,
    UpdateSeriesRequest,
)
from server.api.schemas.summary import (
    MangaSummaryRequest,
    SummaryControlRequest,
    SummaryDismissRequest,
)


def test_server_main_keeps_extracted_schema_imports_compatible():
    schemas = (
        RetryBatchItemRequest,
        UpdateBatchItemRequest,
        UpdateBatchRequest,
        LayoutPreviewRequest,
        LayoutSegmentRequest,
        ReviewStatusRequest,
        SaveEditsRequest,
        DeletePagesRequest,
        ExportCbzRequest,
        ReadingProgressRequest,
        ReorderPagesRequest,
        UpdateMetaRequest,
        PipelineRerunRequest,
        RerenderRequest,
        AddSeriesMembersRequest,
        CreateSeriesRequest,
        MoveMangaSeriesRequest,
        ReplaceSeriesMembersRequest,
        UpdateSeriesRequest,
        MangaSummaryRequest,
        SummaryControlRequest,
        SummaryDismissRequest,
    )

    assert all(getattr(main, schema.__name__) is schema for schema in schemas)
    assert main.MAX_BATCH_ITEMS == constants.MAX_BATCH_ITEMS
    assert all(callable(getattr(main, name)) for name in (
        "_batch_events",
        "_build_cbz_archive",
        "_ensure_bbox_artifact",
        "_ensure_thumbnail_artifact",
        "register_instance",
        "_is_archive_upload",
        "_iter_archive_pages",
        "_iter_original_upload_pages",
        "_json_value",
        "_ocr_config",
        "_persist_summary_ocr",
        "_repaired_regions",
        "_run_summary_ocr",
        "_run_summary_ocr_batch",
        "_safe_setting",
        "_summary_job_events",
        "_summary_input_file",
        "_generate_manga_summary",
        "_summary_target_language",
        "_cpu_stage_worker_count",
        "_cpu_threads_per_worker",
        "_init_server_environment",
        "_setup_inprocess_workers",
        "_setup_subprocess_workers",
        "_supervise_subprocess_workers",
        "generate_nonce",
        "prepare",
        "start_translator_client_proc",
        "_validate_original_upload",
        "_warm_preview_variants",
        "_write_original_import",
        "batch_events",
        "clear_results",
        "create_series",
        "create_cbz_stream",
        "create_manga_summary",
        "rerun_pipeline",
        "rerender_results",
        "delete_manga_group_endpoint",
        "delete_result",
        "delete_results",
        "export_cbz_get",
        "export_cbz_post",
        "get_result_detail",
        "import_original_manga",
        "get_manga_series",
        "get_manga_summary",
        "get_manga_summary_config",
        "get_manga_summary_jobs",
        "get_pipeline_manifest",
        "get_reading_progress",
        "get_result_by_folder",
        "get_result_file_by_folder",
        "get_series",
        "list_manga_pages",
        "list_batches",
        "list_result_groups",
        "list_results",
        "list_series",
        "layout_preview",
        "manga_summary_job_events",
        "dismiss_manga_summary_job",
        "pause_manga_summary_job",
        "resume_manga_summary_job",
        "stop_manga_summary_job",
        "translate_json",
        "translate_bytes",
        "translate_image",
        "stream_json",
        "stream_bytes",
        "stream_image",
        "json_form",
        "bytes_form",
        "image_form",
        "stream_json_form",
        "stream_bytes_form",
        "stream_image_form",
        "stream_image_form_web",
        "simple_execute_batch",
        "execute_batch_stream",
        "transform_to_image",
        "transform_to_json",
        "transform_to_bytes",
        "save_edits",
        "update_page_review",
        "put_batch",
        "reorder_manga_pages",
        "save_reading_progress",
        "update_meta",
    ))
    assert main.BatchConflict is BatchConflict
    assert main.BatchNotFound is BatchNotFound
    assert main.InvalidBatch is InvalidBatch
    assert main.InvalidSeries is InvalidSeries
    assert main.SeriesConflict is SeriesConflict
    assert main.SeriesNotFound is SeriesNotFound
    assert issubclass(SeriesNotFound, SeriesStoreError)
