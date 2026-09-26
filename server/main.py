import datetime
import logging
import os
import sys
from argparse import Namespace
import asyncio
from functools import lru_cache

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


import re
import json
import tempfile
from typing import Any, Awaitable, BinaryIO, Callable, Iterable, Iterator, Optional, List

try:
    import resource
    def _raise_nofile_limit():
        try:
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            target = 65536
            if hard > 0 and hard < target:
                target = hard
            if soft < target:
                resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except Exception:
            pass
    _raise_nofile_limit()
except ImportError:
    def _raise_nofile_limit():
        pass

from fastapi import BackgroundTasks, FastAPI, Request, HTTPException, Header, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from pathlib import Path

import gc
from PIL import Image, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = None

from manga_translator.config import Config, MAX_MANGA_TITLE_LENGTH
from manga_translator.pipeline.cpu import (
    CPU_PRIORITY_INTERACTIVE,
    configure_cpu_stage_workers,
    run_cpu_stage,
    shutdown_cpu_stage_executor,
    shutdown_shared_cpu_stage_executor,
)
from manga_translator.pipeline.stages import ResourceClass
from manga_translator.pipeline.run import set_document_saver
from manga_translator.utils.device_memory import empty_device_cache
from manga_translator.utils.model_cache import MODEL_EXECUTOR_CONCURRENCY
from server.instance import ExecutorInstance, executor_instances
from server.worker_runtime import (
    cpu_stage_worker_count as _cpu_stage_worker_count,
    cpu_threads_per_worker as _cpu_threads_per_worker,
    generate_nonce,
    initialize_server_environment as _initialize_server_environment,
    setup_inprocess_workers as _setup_inprocess_workers_impl,
    setup_subprocess_workers as _setup_subprocess_workers_impl,
    start_translator_client_proc as _start_translator_client_proc_impl,
    supervise_subprocess_workers as _supervise_subprocess_workers_impl,
)
from server.server_preparation import prepare_server as _prepare_server_runtime
from server.myqueue import SummaryQueueElement, task_queue, wait_in_queue
from server.request_extraction import (
    get_ctx,
    while_streaming,
    TranslateRequest,
    BatchTranslateRequest,
    get_batch_ctx,
    set_result_indexer,
    set_request_lookup,
)
from server.to_json import to_translation, TranslationResponse
from server.constants import UPLOAD_CACHE_DIR, SUPERVISOR_INTERVAL_SEC, EXECUTOR_MODE_INPROCESS
from server.constants import (
    BATCH_ROOT,
    DATABASE_URL,
    LEGACY_RESULT_ROOT,
    MIGRATION_MAP_PATH,
    MAX_BATCH_ITEMS,
    SERVER_RESULT_ROOT,
)
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
from server.api.schemas.manga import DeletePagesRequest
from server.api.schemas.pipeline import PipelineRerunRequest, RerenderRequest
from server.batch_scheduler import BatchScheduler, stage_resource_limits
from server.app_lifecycle import create_lifespan
from server.summary_scheduler import SummaryScheduler
from server.summary_jobs import SummaryJobController
from server.summary_task import run_summary_task as _run_summary_task_impl
from server.batch_store import (
    BatchConflict,
    BatchNotFound,
    BatchStore,
    InvalidBatch,
)
from server.logger import setup_server_logging, get_logger, correlation_id_ctx, set_correlation_id
from server.api.middleware import (
    _original_import_lock,
    correlation_id_middleware,
    serialize_original_manga_imports,
)
from server.manga_summary import (
    group_pages,
    is_page_text_extracted,
    load_summary,
    remove_summary,
    rename_summary,
    save_summary,
    source_snapshot,
    synopsis_status,
    dismiss_summary_job,
    list_summary_jobs,
    read_page_text,
    update_summary_job,
    transcript_pages,
    generate_synopsis,
    resolve_summary_model,
    summary_error_details,
    DEFAULT_SUMMARY_MODEL,
)
from server.summary_ocr import (
    json_value as _summary_json_value,
    ocr_config as _summary_ocr_config,
    persist_summary_ocr as _summary_persist_ocr,
    repaired_regions as _summary_repaired_regions,
    run_summary_ocr as _summary_ocr_run,
    run_summary_ocr_batch as _summary_ocr_batch,
    safe_setting as _safe_setting_impl,
    summary_input_file as _summary_input_file_impl,
    summary_target_language as _summary_target_language_impl,
)
from server.summary_status import (
    summary_status_for as _summary_status_for_impl,
    update_summary_job_for as _update_summary_job_for_impl,
)
from server.summary_generation import (
    SummaryGenerationRuntime,
    generate_manga_summary as _generate_manga_summary_impl,
)
from server.postgres_store import PostgresBatchStore, PostgresStore
from server.postgres_store import (
    GroupConflict,
    GroupNotFound,
    InvalidSeries,
    SeriesConflict,
    SeriesNotFound,
)
from server.result_queries import _image_urls, _scan_manga_groups, _scan_results
from server.result_metadata import (
    _META_CACHE,
    _compact_file_backed_group,
    _delete_file_backed_results,
    _get_cached_meta,
    _input_file,
    _invalidate_meta_cache,
    _manga_id,
    _reorder_file_backed_pages,
    _review_status,
    _review_status_for_regions,
    _source_type,
    _update_file_backed_meta,
    _write_file_backed_meta,
    meta_page_order,
    natural_keys,
    page_order_value,
)
from server.original_import import (
    is_archive_upload as _is_archive_upload_impl,
    iter_archive_pages as _iter_archive_pages_impl,
    iter_original_upload_pages as _iter_original_upload_pages_impl,
    validate_original_upload as _validate_original_upload_impl,
    warm_preview_variants as _warm_preview_variants_impl,
    write_original_import as _write_original_import_impl,
)
from server.image_variants import asset_version, final_file, generate_image_variants, generate_reader_asset
from server.result_migration import migrate_legacy_results
from server.result_artifacts import (
    ensure_bbox_artifact as _ensure_bbox_artifact_impl,
    ensure_thumbnail_artifact as _ensure_thumbnail_artifact_impl,
)
from server.search_service import SearchService
from server.api.routes.internal_translation import execute_batch_stream, simple_execute_batch
from server.api.routes.translation import transform_to_bytes, transform_to_image, transform_to_json
from server.api.routes.translation_batch import batch_images, batch_json
from server.api.schemas.summary import (
    MangaSummaryRequest,
    SummaryControlRequest,
    SummaryDismissRequest,
)
from server.api.schemas.manga import (
    ExportCbzRequest,
    ReadingProgressRequest,
    ReorderPagesRequest,
    UpdateMetaRequest,
)
from server.api.schemas.series import (
    AddSeriesMembersRequest,
    CreateSeriesRequest,
    MoveMangaSeriesRequest,
    ReplaceSeriesMembersRequest,
    UpdateSeriesRequest,
)
from manga_translator.utils.image_storage import find_asset, save_jpeg

logger = get_logger("server")
summary_logger = get_logger("summary")


def _summary_log(
    event: str,
    title: str,
    group: str,
    *,
    level: int = logging.INFO,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    details = " ".join(f"{key}={value!r}" for key, value in fields.items())
    summary_logger.log(
        level,
        "summary_job event=%s title=%r group=%r%s",
        event,
        title,
        group,
        f" {details}" if details else "",
        exc_info=exc_info,
    )

postgres_store: PostgresStore | None = None
search_service: SearchService | None = None
batch_resource_limits = stage_resource_limits(3, 1)
inference_page_batch_size = 3
model_executor_concurrency = MODEL_EXECUTOR_CONCURRENCY
batch_store = BatchStore(BATCH_ROOT, SERVER_RESULT_ROOT)
batch_scheduler = BatchScheduler(
    batch_store, executor_instances, SERVER_RESULT_ROOT,
    resource_limits=batch_resource_limits,
    inference_page_batch_size=inference_page_batch_size,
)
summary_scheduler: SummaryScheduler | None = None


lifespan = create_lifespan(sys.modules[__name__])

def _postgres() -> PostgresStore | None:
    return postgres_store if postgres_store is not None and postgres_store.ready else None


def _postgres_required() -> PostgresStore:
    store = _postgres()
    if store is None:
        raise HTTPException(503, detail="PostgreSQL is not available")
    return store


async def _index_context_result(ctx: Any) -> None:
    store = _postgres()
    folder = getattr(ctx, "debug_folder", None)
    if (
        store is not None
        and isinstance(folder, str)
        and folder
        and final_file(RESULT_ROOT / folder) is not None
    ):
        await store.sync_result_folder(folder)


nonce = None

BASE_DIR = Path(__file__).resolve().parent
RESULT_ROOT = SERVER_RESULT_ROOT.resolve()
MAX_BATCH_ITEM_BYTES = 2 * 1024 * 1024 * 1024
MAX_BATCH_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
MAX_MANGA_IMPORT_BYTES = 20 * 1024 * 1024 * 1024
SUPPORTED_IMPORT_FORMATS = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "JPG": ".jpg",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tiff",
    "TIF": ".tiff",
    "GIF": ".gif",
    "MPO": ".jpg",
    "JPEG2000": ".jp2",
    "AVIF": ".avif",
    "TGA": ".tga",
    "PPM": ".ppm",
    "ICNS": ".icns",
    "ICO": ".ico",
}

_summary_submit_lock = asyncio.Lock()
_summary_ocr_semaphore = asyncio.Semaphore(1)


_summary_controller = SummaryJobController()





_INSTALLATION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _apply_manga_title_alias(config: Config, raw_config: dict[str, Any]) -> None:
    raw_title = raw_config.get("mangaTitle")
    if raw_title is None and getattr(config, "manga_title", None):
        raw_title = config.manga_title
    raw_group_id = raw_config.get("mangaGroupId") or raw_config.get("groupId")
    if raw_group_id is None and getattr(config, "manga_group_id", None):
        raw_group_id = config.manga_group_id
    if raw_group_id and isinstance(raw_group_id, str):
        config.manga_group_id = raw_group_id.strip() or None
    if not raw_title:
        return
    if not isinstance(raw_title, str):
        raise HTTPException(422, detail="Manga title must be a string")
    clean_title = raw_title.strip()
    if len(clean_title) > MAX_MANGA_TITLE_LENGTH:
        raise HTTPException(
            422,
            detail=f"Manga title must be at most {MAX_MANGA_TITLE_LENGTH} characters",
        )
    config.manga_title = clean_title or None


def _validate_installation_id(value: str) -> str:
    if not _INSTALLATION_ID_RE.fullmatch(value):
        raise HTTPException(400, detail="Invalid installation ID")
    return value








def _ensure_bbox_artifact(
    folder_path: Path,
    file_name: str,
    regions_data: list[dict] | None = None,
) -> bool:
    return _ensure_bbox_artifact_impl(folder_path, file_name, regions_data)


def _ensure_thumbnail_artifact(folder_path: Path, file_name: str) -> bool:
    return _ensure_thumbnail_artifact_impl(
        folder_path, file_name, final_file, _input_file, logger
    )








worker_procs = []
shutting_down = False

def start_translator_client_proc(
    host: str,
    port: int,
    nonce: str,
    params: Namespace,
    worker_id: int = 0,
    gpu_id: Optional[str] = None,
):
    return _start_translator_client_proc_impl(
        host,
        port,
        nonce,
        params,
        worker_id=worker_id,
        gpu_id=gpu_id,
        result_root=RESULT_ROOT,
        register_executor=executor_instances.register,
    )

def _init_server_environment(args):
    def set_nonce(value: str) -> None:
        global nonce
        nonce = value

    return _initialize_server_environment(
        args,
        result_root=RESULT_ROOT,
        upload_cache_dir=UPLOAD_CACHE_DIR,
        nonce_factory=generate_nonce,
        set_nonce=set_nonce,
    )






def _setup_inprocess_workers(args, num_workers: int, model_concurrency: int):
    return _setup_inprocess_workers_impl(
        args,
        num_workers,
        model_concurrency,
        result_root=RESULT_ROOT,
        register_executor=executor_instances.register,
        logger=logger,
        cpu_threads_per_worker_fn=_cpu_threads_per_worker,
    )

def _supervise_subprocess_workers():
    return _supervise_subprocess_workers_impl(
        worker_procs,
        is_shutting_down=lambda: shutting_down,
        get_nonce=lambda: nonce,
        start_worker=lambda *args, **kwargs: start_translator_client_proc(*args, **kwargs),
        logger=logger,
        interval=SUPERVISOR_INTERVAL_SEC,
    )

def _setup_subprocess_workers(args, num_workers: int):
    def set_shutting_down() -> None:
        global shutting_down
        shutting_down = True

    return _setup_subprocess_workers_impl(
        args,
        num_workers,
        get_nonce=lambda: nonce,
        worker_procs=worker_procs,
        start_worker=lambda *worker_args, **kwargs: start_translator_client_proc(*worker_args, **kwargs),
        supervise=_supervise_subprocess_workers,
        set_shutting_down=set_shutting_down,
        logger=logger,
    )

def prepare(args):
    return _prepare_server_runtime(args, sys.modules[__name__])











async def _sync_batch_review(folder_name: str, needs_review: bool) -> None:
    try:
        await batch_store.update_review_for_result(folder_name, needs_review)
    except Exception:
        logger.exception("Failed to sync batch review state for %s", folder_name)










def _validate_original_upload(content: bytes, filename: str) -> tuple[bytes, str]:
    return _validate_original_upload_impl(
        content, filename, supported_formats=SUPPORTED_IMPORT_FORMATS, logger=logger
    )


def _is_archive_upload(filename: str, content: bytes) -> bool:
    return _is_archive_upload_impl(filename, content)


def _iter_archive_pages(
    source: BinaryIO,
    archive_filename: str,
) -> Iterator[tuple[str, str, bytes, bytes, str]]:
    return _iter_archive_pages_impl(
        source,
        archive_filename,
        max_item_bytes=MAX_BATCH_ITEM_BYTES,
        natural_keys=natural_keys,
        validate_upload=_validate_original_upload,
        logger=logger,
    )


def _iter_original_upload_pages(
    uploads: list[UploadFile],
    source_paths: list[str] | None = None,
) -> Iterator[tuple[str, str, bytes, bytes, str]]:
    return _iter_original_upload_pages_impl(
        uploads,
        source_paths,
        max_batch_items=MAX_BATCH_ITEMS,
        max_item_bytes=MAX_BATCH_ITEM_BYTES,
        max_import_bytes=MAX_MANGA_IMPORT_BYTES,
        is_archive_upload=_is_archive_upload,
        iter_archive_pages=_iter_archive_pages,
        validate_upload=_validate_original_upload,
    )


def _write_original_import(
    title: str,
    pages: Iterable[tuple[str, str, bytes, bytes, str]],
    group_id: Optional[str] = None,
) -> dict:
    return _write_original_import_impl(
        title, pages, group_id, result_root=RESULT_ROOT, logger=logger, save_jpeg=save_jpeg
    )


def _warm_preview_variants(folders: list[str]) -> None:
    return _warm_preview_variants_impl(
        folders,
        result_root=RESULT_ROOT,
        generate_variants=generate_image_variants,
        logger=logger,
    )




def _summary_target_language(pages: list[dict]) -> str:
    return _summary_target_language_impl(pages)


def _safe_setting(value, allowed: set[str], default: str) -> str:
    return _safe_setting_impl(value, allowed, default)


def _ocr_config(page: dict, target_language: str) -> Config:
    return _summary_ocr_config(page, target_language, _safe_setting)


def _json_value(value):
    return _summary_json_value(value)


def _repaired_regions(ctx) -> list[dict]:
    return _summary_repaired_regions(ctx, _json_value)


async def _run_summary_ocr(
    request: Request,
    page: dict,
    target_language: str,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
    worker=None,
    overwrite: bool = False,
) -> list[dict]:
    return await _summary_ocr_run(
        request,
        page,
        target_language,
        on_progress,
        worker,
        overwrite,
        get_context=get_ctx,
        ocr_config_fn=_ocr_config,
        input_file_fn=_summary_input_file,
        repaired_regions_fn=_repaired_regions,
        persist_regions=_persist_summary_ocr,
    )


def _summary_input_file(page: dict) -> Path:
    return _summary_input_file_impl(page, _input_file, final_file)


async def _persist_summary_ocr(page: dict, regions: list[dict]) -> None:
    return await _summary_persist_ocr(page, regions, get_store=lambda: _postgres())


async def _run_summary_ocr_batch(
    pages: list[dict],
    target_language: str,
    worker,
    batch_size: int,
    on_progress: Callable[[str, int], Awaitable[None]] | None = None,
) -> list[tuple[list[dict] | None, Exception | None]]:
    return await _summary_ocr_batch(
        pages,
        target_language,
        worker,
        batch_size,
        on_progress,
        ocr_config_fn=_ocr_config,
        input_file_fn=_summary_input_file,
        repaired_regions_fn=_repaired_regions,
        persist_regions=_persist_summary_ocr,
    )


@lru_cache(maxsize=1)
def _deepseek_token_count_factory():
    try:
        from manga_translator.translators.tokenizers.token_counters import deepseekTokenCounter

        counter = deepseekTokenCounter()
        return counter.count_tokens
    except Exception:
        return lambda text: max(1, len(text) // 2)


async def _summary_status_for(
    store: PostgresStore | None,
    group_value: str,
    title: str,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    return await _summary_status_for_impl(
        store,
        group_value,
        title,
        pages,
        result_root=RESULT_ROOT,
        file_status_fn=synopsis_status,
    )


async def _update_summary_job_for(
    store: PostgresStore | None,
    group_value: str,
    title: str,
    status: str,
    error: str | None = None,
    stage: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    current_page: int | None = None,
    page_count: int | None = None,
    pages_with_text: int | None = None,
    extraction_required: bool | None = None,
    provider: str | None = None,
    model: str | None = None,
    refresh_text: bool | None = None,
    regenerate: bool | None = None,
    stage_passed_count: int | None = None,
) -> None:
    return await _update_summary_job_for_impl(
        store,
        group_value,
        title,
        status,
        error,
        stage,
        progress,
        message,
        current_page,
        page_count,
        pages_with_text,
        extraction_required,
        provider,
        model,
        refresh_text,
        regenerate,
        stage_passed_count,
        result_root=RESULT_ROOT,
        file_update_fn=update_summary_job,
    )






async def _run_summary_task(
    request: Request | None,
    data: MangaSummaryRequest,
    store: PostgresStore | None,
    group_value: str,
    clean_title: str,
    worker=None,
    pause_event: asyncio.Event | None = None,
) -> None:
    await _run_summary_task_impl(
        request,
        data,
        store,
        group_value,
        clean_title,
        worker,
        pause_event,
        generate_summary=_generate_manga_summary,
        log=_summary_log,
        update_summary_job=_update_summary_job_for,
        controller=_summary_controller,
        reclaim_memory=_reclaim_summary_memory,
    )


async def _reclaim_summary_memory(worker=None) -> None:
    executors_to_reclaim = set(executor_instances.list)
    if worker is not None:
        executors_to_reclaim.add(worker)
    for executor in executors_to_reclaim:
        reclaim = getattr(executor, "reclaim_memory", None)
        if reclaim is not None:
            try:
                await reclaim()
            except Exception:
                pass
    empty_device_cache()
    gc.collect()


async def _generate_manga_summary(
    request: Request,
    data: MangaSummaryRequest,
    worker=None,
    pause_event: asyncio.Event | None = None,
):
    return await _generate_manga_summary_impl(
        request,
        data,
        worker,
        pause_event,
        runtime=SummaryGenerationRuntime(
            result_root=RESULT_ROOT,
            get_store=_postgres,
            summary_queue_element=SummaryQueueElement,
            run_summary_ocr=_run_summary_ocr,
            run_summary_ocr_batch=_run_summary_ocr_batch,
            summary_controller=_summary_controller,
            summary_log=_summary_log,
            summary_ocr_semaphore=_summary_ocr_semaphore,
            summary_status_for=_summary_status_for,
            summary_target_language=_summary_target_language,
            update_summary_job_for=_update_summary_job_for,
            get_inference_page_batch_size=lambda: inference_page_batch_size,
            task_queue=task_queue,
            wait_in_queue=wait_in_queue,
            empty_device_cache=empty_device_cache,
            deepseek_token_count_factory=_deepseek_token_count_factory,
            generate_synopsis=generate_synopsis,
            group_pages=group_pages,
            is_page_text_extracted=is_page_text_extracted,
            read_page_text=read_page_text,
            resolve_summary_model=resolve_summary_model,
            save_summary=save_summary,
            source_snapshot=source_snapshot,
            summary_error_details=summary_error_details,
            transcript_pages=transcript_pages,
        ),
    )






#todo: restart if crash
#todo: cache results
#todo: cleanup cache

from server.app import create_app
app = create_app(sys.modules[__name__])

if __name__ == '__main__':
    import logging
    import uvicorn
    from args import parse_arguments
    from server.logger import setup_server_logging

    args = parse_arguments()
    log_level = getattr(logging, getattr(args, 'log_level', 'INFO').upper(), logging.INFO)
    setup_server_logging(
        log_dir=getattr(args, 'log_dir', 'logs'),
        console_level=log_level,
        file_level=logging.DEBUG,
        retention_days=14,
        force_reconfigure=True,
    )
    args.start_instance = True
    procs = prepare(args)
    logger.info("Nonce: " + str(nonce))
    try:
        uvicorn.run(app, host=args.host, port=args.port, lifespan="on", log_config=None)
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        shutting_down = True
        model_executors = set()
        for instance in executor_instances.list:
            if hasattr(instance, '_model_executor'):
                model_executors.add(instance._model_executor)
                instance.close()
        for model_executor in model_executors:
            model_executor.close()
        shutdown_shared_cpu_stage_executor()
        if procs:
            for p in procs:
                try:
                    p.terminate()
                    p.wait(timeout=2)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
