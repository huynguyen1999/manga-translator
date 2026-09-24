import datetime
import io
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from argparse import Namespace
import asyncio
from contextlib import asynccontextmanager, nullcontext
from functools import lru_cache

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


import re
import json
import zipfile
import tempfile
from typing import Any, Awaitable, BinaryIO, Callable, Iterable, Iterator, Optional, List, Literal
from pydantic import BaseModel, Field, field_validator

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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse, JSONResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from pathlib import Path, PurePosixPath

import gc
from PIL import Image, ImageFile, ImageOps
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
from manga_translator.pipeline.stages import STAGE_DEPENDENCIES, STAGE_ORDER, PipelineStage, ResourceClass
from manga_translator.pipeline.run import set_document_saver
from manga_translator.utils.device_memory import empty_device_cache
from manga_translator.utils.model_cache import MODEL_EXECUTOR_CONCURRENCY
from server.instance import ExecutorInstance, executor_instances
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
    SERVER_RESULT_ROOT,
)
from server.batch_scheduler import BatchScheduler, stage_resource_limits
from server.summary_scheduler import SummaryScheduler
from server.batch_store import (
    BatchConflict,
    BatchNotFound,
    BatchStore,
    InvalidBatch,
)
from server.logger import setup_server_logging, get_logger, correlation_id_ctx, set_correlation_id
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
from server.postgres_store import PostgresBatchStore, PostgresStore
from server.postgres_store import (
    GroupConflict,
    GroupNotFound,
    InvalidSeries,
    SeriesConflict,
    SeriesNotFound,
)
from server.image_variants import asset_version, final_file, generate_image_variants
from server.result_migration import migrate_legacy_results
from server.search_api import search_router
from server.search_service import SearchService
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global batch_store, batch_scheduler, summary_scheduler, postgres_store, search_service
    _raise_nofile_limit()
    setup_server_logging()
    await asyncio.to_thread(
        migrate_legacy_results,
        LEGACY_RESULT_ROOT,
        SERVER_RESULT_ROOT,
        MIGRATION_MAP_PATH,
    )
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    use_postgres = (
        os.getenv("PERSISTENCE_MODE", "postgres").strip().lower() != "filesystem"
        and bool(DATABASE_URL)
    )
    if use_postgres:
        postgres_store = PostgresStore(DATABASE_URL, SERVER_RESULT_ROOT)
        await postgres_store.start()
        database_loop = asyncio.get_running_loop()

        async def save_documents(folder: str, documents: dict[str, Any]) -> None:
            operation = postgres_store.save_documents(folder, documents)
            if asyncio.get_running_loop() is database_loop:
                await operation
                return
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(operation, database_loop))

        set_document_saver(save_documents)
        batch_store = PostgresBatchStore(postgres_store, BATCH_ROOT, SERVER_RESULT_ROOT)
        batch_scheduler = BatchScheduler(
            batch_store, executor_instances, SERVER_RESULT_ROOT,
            resource_limits=batch_resource_limits,
            inference_page_batch_size=inference_page_batch_size,
        )
        set_result_indexer(postgres_store.sync_result_folder)
        set_request_lookup(postgres_store.find_request)
        await postgres_store.index_untracked_results()
    else:
        postgres_store = None
        batch_store = BatchStore(BATCH_ROOT, SERVER_RESULT_ROOT)
        batch_scheduler = BatchScheduler(
            batch_store, executor_instances, SERVER_RESULT_ROOT,
            resource_limits=batch_resource_limits,
            inference_page_batch_size=inference_page_batch_size,
        )
        set_document_saver(None)
        set_result_indexer(None)
        set_request_lookup(None)
    summary_scheduler = SummaryScheduler(
        store_getter=_postgres,
        controller=_summary_controller,
        run_task_fn=_run_summary_task,
        request_cls=MangaSummaryRequest,
        result_root=SERVER_RESULT_ROOT,
    )
    await summary_scheduler.start()
    await batch_scheduler.start()
    if postgres_store is not None:
        search_service = SearchService(postgres_store, lambda: next(
            (instance._model_executor for instance in executor_instances.list if hasattr(instance, '_model_executor')), None
        ))
        await search_service.start()
    try:
        yield
    finally:
        if search_service is not None:
            await search_service.close()
            search_service = None
        if summary_scheduler is not None:
            await summary_scheduler.stop()
            summary_scheduler = None
        await batch_scheduler.stop()
        await shutdown_cpu_stage_executor()
        set_result_indexer(None)
        set_request_lookup(None)
        set_document_saver(None)
        if postgres_store is not None:
            await postgres_store.close()
            postgres_store = None


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


app = FastAPI(lifespan=lifespan)
app.include_router(search_router(lambda: search_service))
app.include_router(search_router(lambda: search_service), prefix="/api")
nonce = None

BASE_DIR = Path(__file__).resolve().parent
RESULT_ROOT = SERVER_RESULT_ROOT.resolve()
MAX_BATCH_ITEMS = 10_000
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
# ponytail: one process-local gate is enough for the local server; use upload sessions if concurrent imports matter.
_original_import_lock = asyncio.Lock()


@app.middleware("http")
async def serialize_original_manga_imports(request: Request, call_next):
    if request.url.path in {"/results/import", "/api/results/import"}:
        async with _original_import_lock:
            return await call_next(request)
    return await call_next(request)


class MangaSummaryRequest(BaseModel):
    groupId: Optional[str] = None
    mangaTitle: Optional[str] = Field(None, max_length=MAX_MANGA_TITLE_LENGTH)
    summaryModel: Optional[str] = Field(None, max_length=100)
    regenerate: bool = False
    refreshText: bool = False

    @field_validator("mangaTitle", mode="before")
    @classmethod
    def _strip_manga_title(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    @field_validator("summaryModel", mode="before")
    @classmethod
    def _strip_summary_model(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


class SummaryControlRequest(BaseModel):
    groupId: Optional[str] = None
    mangaTitle: Optional[str] = Field(None, max_length=MAX_MANGA_TITLE_LENGTH)

    @field_validator("mangaTitle", mode="before")
    @classmethod
    def _strip_title(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


SummaryDismissRequest = SummaryControlRequest


class SummaryJobController:
    def __init__(self):
        self._pause_events: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._requests: dict[str, MangaSummaryRequest] = {}
        self._ocr_tasks: dict[str, SummaryQueueElement] = {}

    def register_task(
        self,
        group_key: str,
        request: MangaSummaryRequest,
        task: asyncio.Task,
    ) -> asyncio.Event:
        pause_event = self._pause_events.setdefault(group_key, asyncio.Event())
        pause_event.set()
        self._tasks[group_key] = task
        self._requests[group_key] = request
        return pause_event

    def unregister_task(self, group_key: str, task: asyncio.Task | None = None) -> None:
        if task is None or self._tasks.get(group_key) == task:
            self._tasks.pop(group_key, None)
            self._pause_events.pop(group_key, None)
            self._requests.pop(group_key, None)
            self._ocr_tasks.pop(group_key, None)

    def register_ocr_task(self, group_key: str, ocr_task: SummaryQueueElement) -> None:
        self._ocr_tasks[group_key] = ocr_task

    def unregister_ocr_task(self, group_key: str) -> None:
        self._ocr_tasks.pop(group_key, None)

    def get_pause_event(self, group_key: str) -> asyncio.Event:
        event = self._pause_events.get(group_key)
        if event is None:
            event = asyncio.Event()
            event.set()
            self._pause_events[group_key] = event
        return event

    def pause(self, group_key: str) -> bool:
        event = self.get_pause_event(group_key)
        event.clear()
        return True

    def resume(self, group_key: str) -> bool:
        event = self.get_pause_event(group_key)
        event.set()
        return True

    def stop(self, group_key: str) -> bool:
        event = self._pause_events.pop(group_key, None)
        if event is not None:
            event.set()
        ocr_task = self._ocr_tasks.pop(group_key, None)
        if ocr_task is not None:
            asyncio.create_task(task_queue.remove(ocr_task))
        self._requests.pop(group_key, None)
        task = self._tasks.pop(group_key, None)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def is_paused(self, group_key: str) -> bool:
        event = self._pause_events.get(group_key)
        return event is not None and not event.is_set()

    def get_task(self, group_key: str) -> asyncio.Task | None:
        return self._tasks.get(group_key)

    def get_request(self, group_key: str) -> MangaSummaryRequest | None:
        return self._requests.get(group_key)


_summary_controller = SummaryJobController()


class CreateSeriesRequest(BaseModel):
    title: str = Field(..., max_length=MAX_MANGA_TITLE_LENGTH)
    groupIds: list[str]

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v


class UpdateSeriesRequest(BaseModel):
    title: str = Field(..., max_length=MAX_MANGA_TITLE_LENGTH)

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v


class ReplaceSeriesMembersRequest(BaseModel):
    groupIds: list[str]


class AddSeriesMembersRequest(BaseModel):
    groupIds: list[str]


class MoveMangaSeriesRequest(BaseModel):
    targetSeriesId: str

def natural_keys(text: str):
    """Natural sort key for strings with numbers (e.g. page_1 before page_10)"""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', str(text))]


def page_order_value(value: Any) -> Optional[int]:
    try:
        order = int(value)
    except (TypeError, ValueError):
        return None
    return order if order > 0 else None


def meta_page_order(metadata: dict[str, Any]) -> Optional[int]:
    return page_order_value(metadata.get("pageOrder"))


class UpdateMetaRequest(BaseModel):
    folders: Optional[List[str]] = None
    pageIds: Optional[List[str]] = None
    groupId: Optional[str] = None
    oldMangaTitle: Optional[str] = None
    mangaTitle: str = Field(..., max_length=MAX_MANGA_TITLE_LENGTH)

    @field_validator("mangaTitle", "oldMangaTitle", mode="before")
    @classmethod
    def _strip_titles(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v


class ReorderPagesRequest(BaseModel):
    pageIds: List[str]

class ExportCbzRequest(BaseModel):
    groupId: Optional[str] = None
    mangaTitle: Optional[str] = Field("manga", max_length=MAX_MANGA_TITLE_LENGTH)
    folders: Optional[List[str]] = None

    @field_validator("mangaTitle", mode="before")
    @classmethod
    def _strip_manga_title(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v if v else "manga"
        return v


class ReadingProgressRequest(BaseModel):
    installationId: str
    groupId: Optional[str] = None
    mangaTitle: Optional[str] = Field(None, max_length=MAX_MANGA_TITLE_LENGTH)
    pageId: Optional[str] = None
    page: Optional[int] = None
    scrollTop: int = 0
    complete: bool = False
    updatedAt: Optional[str] = None

    @field_validator("mangaTitle", mode="before")
    @classmethod
    def _strip_manga_title(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id")
    if not request_id:
        request_id = f"req-{secrets.token_hex(4)}"
    token = correlation_id_ctx.set(request_id)
    start_time = time.perf_counter()
    client_ip = request.client.host if request.client else "-"
    logger.info(f"{request.method} {request.url.path} from {client_ip}")

    try:
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({elapsed_ms:.1f}ms)")
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.error(f"{request.method} {request.url.path} -> Exception: {exc} ({elapsed_ms:.1f}ms)")
        raise
    finally:
        correlation_id_ctx.reset(token)

@app.post("/register", response_description="no response", tags=["internal-api"])
async def register_instance(instance: ExecutorInstance, req: Request, req_nonce: str = Header(alias="X-Nonce")):
    if req_nonce != nonce:
        raise HTTPException(401, detail="Invalid nonce")
    instance.ip = req.client.host
    executor_instances.register(instance)

def transform_to_image(ctx):
    # 检查是否使用占位符（在web模式下final.png保存后会设置此标记）
    if hasattr(ctx, 'use_placeholder') and ctx.use_placeholder:
        # ctx.result已经是1x1占位符图片，快速传输
        img_byte_arr = io.BytesIO()
        ctx.result.save(img_byte_arr, format="PNG")
        return img_byte_arr.getvalue()

    # 返回完整的翻译结果
    img_byte_arr = io.BytesIO()
    ctx.result.save(img_byte_arr, format="PNG")
    return img_byte_arr.getvalue()

def transform_to_json(ctx):
    return to_translation(ctx).model_dump_json().encode("utf-8")

def transform_to_bytes(ctx):
    return to_translation(ctx).to_bytes()

@app.post("/translate/json", response_model=TranslationResponse, tags=["api", "json"],response_description="json strucure inspired by the ichigo translator extension")
async def translate_json(req: Request, data: TranslateRequest):
    ctx = await get_ctx(req, data.config, data.image)
    await _index_context_result(ctx)
    return to_translation(ctx)

@app.post("/translate/bytes", response_class=StreamingResponse, tags=["api", "json"],response_description="custom byte structure for decoding look at examples in 'examples/response.*'")
async def translate_bytes(req: Request, data: TranslateRequest):
    ctx = await get_ctx(req, data.config, data.image)
    await _index_context_result(ctx)
    return StreamingResponse(content=to_translation(ctx).to_bytes())

@app.post("/translate/image", response_description="the result image", tags=["api", "json"],response_class=StreamingResponse)
async def translate_image(req: Request, data: TranslateRequest) -> StreamingResponse:
    ctx = await get_ctx(req, data.config, data.image)
    await _index_context_result(ctx)
    def _save():
        img_byte_arr = io.BytesIO()
        ctx.result.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        return img_byte_arr
    img_byte_arr = await asyncio.to_thread(_save)

    return StreamingResponse(img_byte_arr, media_type="image/png")

@app.post("/translate/json/stream", response_class=StreamingResponse,tags=["api", "json"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
async def stream_json(req: Request, data: TranslateRequest) -> StreamingResponse:
    return await while_streaming(req, transform_to_json, data.config, data.image)

@app.post("/translate/bytes/stream", response_class=StreamingResponse, tags=["api", "json"],response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
async def stream_bytes(req: Request, data: TranslateRequest)-> StreamingResponse:
    return await while_streaming(req, transform_to_bytes,data.config, data.image)

@app.post("/translate/image/stream", response_class=StreamingResponse, tags=["api", "json"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
async def stream_image(req: Request, data: TranslateRequest) -> StreamingResponse:
    return await while_streaming(req, transform_to_image, data.config, data.image)

@app.post("/translate/with-form/json", response_model=TranslationResponse, tags=["api", "form"],response_description="json strucure inspired by the ichigo translator extension")
async def json_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    img = await image.read()
    conf = Config.parse_raw(config)
    try:
        raw_conf = json.loads(config)
        if isinstance(raw_conf, dict):
            _apply_manga_title_alias(conf, raw_conf)
    except Exception:
        pass
    if not conf.original_name and image.filename:
        conf.original_name = image.filename
    ctx = await get_ctx(req, conf, img)
    await _index_context_result(ctx)
    return to_translation(ctx)

@app.post("/translate/with-form/bytes", response_class=StreamingResponse, tags=["api", "form"],response_description="custom byte structure for decoding look at examples in 'examples/response.*'")
async def bytes_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    img = await image.read()
    conf = Config.parse_raw(config)
    try:
        raw_conf = json.loads(config)
        if isinstance(raw_conf, dict):
            _apply_manga_title_alias(conf, raw_conf)
    except Exception:
        pass
    if not conf.original_name and image.filename:
        conf.original_name = image.filename
    ctx = await get_ctx(req, conf, img)
    await _index_context_result(ctx)
    return StreamingResponse(content=to_translation(ctx).to_bytes())

@app.post("/translate/with-form/image", response_description="the result image", tags=["api", "form"],response_class=StreamingResponse)
async def image_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    img = await image.read()
    conf = Config.parse_raw(config)
    try:
        raw_conf = json.loads(config)
        if isinstance(raw_conf, dict):
            _apply_manga_title_alias(conf, raw_conf)
    except Exception:
        pass
    if not conf.original_name and image.filename:
        conf.original_name = image.filename
    ctx = await get_ctx(req, conf, img)
    await _index_context_result(ctx)
    def _save():
        img_byte_arr = io.BytesIO()
        ctx.result.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        return img_byte_arr
    img_byte_arr = await asyncio.to_thread(_save)

    return StreamingResponse(img_byte_arr, media_type="image/png")

@app.post("/translate/with-form/json/stream", response_class=StreamingResponse, tags=["api", "form"],response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
async def stream_json_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    img = await image.read()
    conf = Config.parse_raw(config)
    try:
        raw_conf = json.loads(config)
        if isinstance(raw_conf, dict):
            _apply_manga_title_alias(conf, raw_conf)
    except Exception:
        pass
    if not conf.original_name and image.filename:
        conf.original_name = image.filename
    # 标记这是Web前端调用，用于占位符优化
    conf._is_web_frontend = True
    return await while_streaming(req, transform_to_json, conf, img)



@app.post("/translate/with-form/bytes/stream", response_class=StreamingResponse,tags=["api", "form"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
async def stream_bytes_form(req: Request, image: UploadFile = File(...), config: str = Form("{}"))-> StreamingResponse:
    img = await image.read()
    conf = Config.parse_raw(config)
    try:
        raw_conf = json.loads(config)
        if isinstance(raw_conf, dict):
            _apply_manga_title_alias(conf, raw_conf)
    except Exception:
        pass
    if not conf.original_name and image.filename:
        conf.original_name = image.filename
    return await while_streaming(req, transform_to_bytes, conf, img)

@app.post("/translate/with-form/image/stream", response_class=StreamingResponse, tags=["api", "form"], response_description="Standard streaming endpoint - returns complete image data. Suitable for API calls and scripts.")
async def stream_image_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    """通用流式端点：返回完整图片数据，适用于API调用和comicread脚本"""
    img = await image.read()
    conf = Config.parse_raw(config)
    _apply_manga_title_alias(conf, json.loads(config))
    if not conf.original_name and image.filename:
        conf.original_name = image.filename
    # 标记为通用模式，不使用占位符优化
    conf._web_frontend_optimized = False
    return await while_streaming(req, transform_to_image, conf, img)

@app.post("/translate/with-form/image/stream/web", response_class=StreamingResponse, tags=["api", "form"], response_description="Web frontend optimized streaming endpoint - uses placeholder optimization for faster response.")
@app.post("/api/translate/with-form/image/stream/web", response_class=StreamingResponse, tags=["api", "form"], response_description="Web frontend optimized streaming endpoint - uses placeholder optimization for faster response.")
async def stream_image_form_web(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    """Web前端专用端点：使用占位符优化，提供极速体验"""
    img = await image.read()
    conf = Config.parse_raw(config)
    _apply_manga_title_alias(conf, json.loads(config))
    if not conf.original_name and image.filename:
        conf.original_name = image.filename
    # 标记为Web前端优化模式，使用占位符优化
    conf._web_frontend_optimized = True
    return await while_streaming(req, transform_to_image, conf, img)

@app.post("/queue-size", response_model=int, tags=["api", "json"])
@app.get("/queue-size", response_model=int, tags=["api", "json"])
async def queue_size() -> int:
    return len(task_queue.queue)

@app.get("/status", tags=["api"])
@app.get("/workers", tags=["api"])
@app.get("/api/status", tags=["api"])
@app.get("/api/workers", tags=["api"])
async def get_server_status():
    return {
        "status": "online",
        **executor_instances.get_status(),
        "queue_size": len(task_queue.queue)
    }


def _batch_http_error(error: Exception) -> HTTPException:
    if isinstance(error, BatchNotFound):
        return HTTPException(404, detail="Batch not found")
    if isinstance(error, BatchConflict):
        return HTTPException(409, detail=str(error))
    if isinstance(error, InvalidBatch):
        return HTTPException(400, detail=str(error))
    return HTTPException(500, detail=str(error))


def _series_http_error(error: Exception) -> HTTPException:
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, SeriesNotFound):
        return HTTPException(404, detail="Series not found")
    if isinstance(error, SeriesConflict):
        return HTTPException(409, detail=str(error))
    if isinstance(error, InvalidSeries):
        return HTTPException(400, detail=str(error))
    return HTTPException(500, detail=str(error))


@app.get("/batches", tags=["api", "batches"])
@app.get("/api/batches", tags=["api", "batches"])
async def list_batches():
    try:
        return await batch_store.list_batch_summaries()
    except Exception as error:
        raise _batch_http_error(error) from error


async def _batch_events():
    last_snapshot = None
    previous_batches = {}
    has_previous_snapshot = False
    while True:
        batches = await batch_store.list_batch_summaries()
        snapshot = json.dumps(batches, sort_keys=True, separators=(",", ":"))
        if snapshot != last_snapshot:
            current_batches = {
                batch["id"]: json.dumps(batch, sort_keys=True, separators=(",", ":"))
                for batch in batches
            }
            last_snapshot = snapshot
            yield f"data: {snapshot}\n\n"
            if has_previous_snapshot:
                # ponytail: broadcast changed full batches; scoped subscriptions if payload fanout grows.
                for batch in batches:
                    encoded = current_batches[batch["id"]]
                    if previous_batches.get(batch["id"]) == encoded:
                        continue
                    try:
                        details = await batch_store.get_batch(batch["id"])
                    except BatchNotFound:
                        continue
                    yield f"event: batch_details\ndata: {json.dumps(details)}\n\n"
            previous_batches = current_batches
            has_previous_snapshot = True
        else:
            yield ": keep-alive\n\n"
        await asyncio.sleep(1)


@app.get("/batches/events", tags=["api", "batches"])
@app.get("/api/batches/events", tags=["api", "batches"])
async def batch_events():
    return StreamingResponse(
        _batch_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/batches/{batch_id}", tags=["api", "batches"])
@app.get("/api/batches/{batch_id}", tags=["api", "batches"])
async def get_batch(batch_id: str):
    try:
        return await batch_store.get_batch(batch_id)
    except Exception as error:
        raise _batch_http_error(error) from error


@app.put("/batches/{batch_id}", tags=["api", "batches"])
@app.put("/api/batches/{batch_id}", tags=["api", "batches"])
async def put_batch(batch_id: str, request: Request):
    """Accept a complete batch and expose it only after all inputs are written."""
    manifest: dict
    files: dict[str, tuple[str, bytes]] = {}
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/"):
        form = await request.form(
            max_files=MAX_BATCH_ITEMS,
            max_fields=MAX_BATCH_ITEMS,
            max_part_size=MAX_BATCH_ITEM_BYTES,
        )
        raw_manifest = form.get("manifest") or form.get("metadata")
        if hasattr(raw_manifest, "read"):
            raw_manifest = (await raw_manifest.read()).decode("utf-8")
        if not isinstance(raw_manifest, str):
            raise HTTPException(400, detail="Multipart upload requires a manifest field")
        try:
            manifest = json.loads(raw_manifest)
        except json.JSONDecodeError as error:
            raise HTTPException(400, detail="Malformed batch manifest") from error
        if not isinstance(manifest, dict):
            raise HTTPException(400, detail="Batch manifest must be an object")

        expected_ids = {
            item.get("id")
            for item in manifest.get("items", [])
            if isinstance(item, dict) and item.get("status") not in {"completed", "finished"}
        }
        if len(expected_ids) > MAX_BATCH_ITEMS:
            raise HTTPException(413, detail="Batch contains too many items")
        uploads = []
        for key, value in form.multi_items():
            if not hasattr(value, "read") or not hasattr(value, "filename"):
                continue
            uploads.append((key, value))
        remaining = set(expected_ids)
        total_upload_bytes = 0
        for key, upload in uploads:
            item_id = key
            if item_id not in remaining:
                for prefix in ("item_", "file_", "input_"):
                    candidate = key.removeprefix(prefix)
                    if candidate in remaining:
                        item_id = candidate
                        break
            if item_id not in remaining:
                stem = Path(upload.filename or "").stem
                if stem in remaining:
                    item_id = stem
            if item_id not in remaining:
                continue
            if hasattr(upload, "file"):
                upload.file.seek(0, os.SEEK_END)
                item_size = upload.file.tell()
                upload.file.seek(0)
                if item_size > MAX_BATCH_ITEM_BYTES:
                    raise HTTPException(413, detail="Batch item is too large")
                total_upload_bytes += item_size
                if total_upload_bytes > MAX_BATCH_UPLOAD_BYTES:
                    raise HTTPException(413, detail="Batch upload is too large")
                files[item_id] = (upload.filename or item_id, upload)
            else:
                content = await upload.read(MAX_BATCH_ITEM_BYTES + 1)
                if len(content) > MAX_BATCH_ITEM_BYTES:
                    raise HTTPException(413, detail="Batch item is too large")
                total_upload_bytes += len(content)
                if total_upload_bytes > MAX_BATCH_UPLOAD_BYTES:
                    raise HTTPException(413, detail="Batch upload is too large")
                files[item_id] = (upload.filename or item_id, content)
            remaining.remove(item_id)
    else:
        try:
            manifest = await request.json()
        except ValueError as error:
            raise HTTPException(400, detail="Malformed batch manifest") from error

    if not isinstance(manifest, dict):
        raise HTTPException(400, detail="Batch manifest must be an object")

    try:
        result = await batch_store.put_batch(batch_id, manifest, files)
        batch_scheduler.wake()
        return result
    except Exception as error:
        raise _batch_http_error(error) from error
    finally:
        if content_type.startswith("multipart/"):
            await asyncio.gather(
                *(upload.close() for _, upload in uploads if hasattr(upload, "close")),
                return_exceptions=True,
            )


@app.get("/batches/{batch_id}/items/{item_id}/input", tags=["api", "batches", "file"])
@app.get("/api/batches/{batch_id}/items/{item_id}/input", tags=["api", "batches", "file"])
async def get_batch_input(batch_id: str, item_id: str):
    try:
        path = await batch_store.input_path(batch_id, item_id)
    except Exception as error:
        raise _batch_http_error(error) from error
    media_type = "image/png"
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        media_type = "image/jpeg"
    elif path.suffix.lower() == ".webp":
        media_type = "image/webp"
    return FileResponse(path, media_type=media_type, headers={"Content-Disposition": f"inline; filename={path.name}"})


@app.post("/batches/{batch_id}/pause", tags=["api", "batches"])
@app.post("/api/batches/{batch_id}/pause", tags=["api", "batches"])
async def pause_batch(batch_id: str):
    try:
        return await batch_scheduler.pause(batch_id)
    except Exception as error:
        raise _batch_http_error(error) from error


@app.post("/batches/{batch_id}/resume", tags=["api", "batches"])
@app.post("/api/batches/{batch_id}/resume", tags=["api", "batches"])
async def resume_batch(batch_id: str):
    try:
        return await batch_scheduler.resume(batch_id)
    except Exception as error:
        raise _batch_http_error(error) from error


@app.post("/batches/{batch_id}/dismiss", tags=["api", "batches"])
@app.post("/api/batches/{batch_id}/dismiss", tags=["api", "batches"])
async def dismiss_batch(batch_id: str):
    try:
        return await batch_scheduler.dismiss(batch_id)
    except Exception as error:
        raise _batch_http_error(error) from error


@app.delete("/batches/{batch_id}", tags=["api", "batches"])
@app.delete("/api/batches/{batch_id}", tags=["api", "batches"])
async def delete_batch(batch_id: str):
    try:
        await batch_scheduler.remove_batch(batch_id)
    except Exception as error:
        raise _batch_http_error(error) from error
    return {"status": "deleted", "id": batch_id}


class UpdateBatchRequest(BaseModel):
    translator: str | None = None
    mangaTitle: str | None = Field(None, max_length=MAX_MANGA_TITLE_LENGTH)
    priority: bool | None = None
    keep_failed_pages_for_editing: bool | None = None

    @field_validator("mangaTitle", mode="before")
    @classmethod
    def _strip_manga_title(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


@app.patch("/batches/{batch_id}", tags=["api", "batches"])
@app.patch("/api/batches/{batch_id}", tags=["api", "batches"])
async def update_batch(batch_id: str, data: UpdateBatchRequest):
    try:
        if data.translator is not None:
            from manga_translator.config import Translator

            if data.translator not in {value.value for value in Translator}:
                raise InvalidBatch("Unknown translator")
            result = await batch_scheduler.update_translator(batch_id, data.translator)
        elif data.mangaTitle is not None:
            result = await batch_scheduler.update_title(batch_id, data.mangaTitle)
        elif data.priority is not None:
            result = await batch_scheduler.update_priority(batch_id, data.priority)
        elif data.keep_failed_pages_for_editing is not None:
            result = await batch_scheduler.update_manual_review(
                batch_id, data.keep_failed_pages_for_editing
            )
        else:
            raise InvalidBatch("Batch patch is empty")
        return result
    except Exception as error:
        raise _batch_http_error(error) from error


class RetryBatchItemRequest(BaseModel):
    keep_failed_pages_for_editing: bool | None = None
    from_stage: str | None = Field(None, alias="fromStage")


@app.post("/batches/{batch_id}/items/{item_id}/retry", tags=["api", "batches"])
@app.post("/api/batches/{batch_id}/items/{item_id}/retry", tags=["api", "batches"])
async def retry_batch_item(
    batch_id: str,
    item_id: str,
    data: RetryBatchItemRequest | None = None,
):
    try:
        return await batch_scheduler.retry_item(
            batch_id,
            item_id,
            data.keep_failed_pages_for_editing if data else None,
            data.from_stage if data else None,
        )
    except Exception as error:
        raise _batch_http_error(error) from error


class UpdateBatchItemRequest(BaseModel):
    excludeColor: bool


@app.patch("/batches/{batch_id}/items/{item_id}", tags=["api", "batches"])
@app.patch("/api/batches/{batch_id}/items/{item_id}", tags=["api", "batches"])
async def update_batch_item(batch_id: str, item_id: str, data: UpdateBatchItemRequest):
    try:
        return await batch_scheduler.update_item(batch_id, item_id, data.excludeColor)
    except Exception as error:
        raise _batch_http_error(error) from error


@app.delete("/batches/{batch_id}/items/{item_id}", tags=["api", "batches"])
@app.delete("/api/batches/{batch_id}/items/{item_id}", tags=["api", "batches"])
async def delete_batch_item(batch_id: str, item_id: str):
    try:
        return await batch_scheduler.remove_item(batch_id, item_id)
    except Exception as error:
        raise _batch_http_error(error) from error

@app.get("/pipeline-runs/{folder_name}/manifest", tags=["api", "pipeline"])
@app.get("/api/pipeline-runs/{folder_name}/manifest", tags=["api", "pipeline"])
async def get_pipeline_manifest(folder_name: str):
    store = _postgres()
    manifest = None
    if store is not None:
        try:
            manifest = await store.get_document(folder_name, "pipeline_manifest.json")
        except Exception:
            manifest = None
    if manifest is None:
        disk_path = Path(RESULT_ROOT) / folder_name / "pipeline_manifest.json"
        if disk_path.is_file():
            try:
                manifest = json.loads(disk_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    if manifest is None:
        meta = None
        if store is not None:
            try:
                meta = await store.get_document(folder_name, "meta.json")
            except Exception:
                meta = None
        if meta is None:
            disk_meta = Path(RESULT_ROOT) / folder_name / "meta.json"
            if disk_meta.is_file():
                try:
                    meta = json.loads(disk_meta.read_text(encoding="utf-8"))
                except Exception:
                    pass
        if meta is not None:
            created_at = meta.get("startedAt") or meta.get("finishedAt")
            finished_at = meta.get("finishedAt")
            duration_ms = meta.get("durationMs")
            manifest = {
                "version": 1,
                "kind": "pipeline-run",
                "folder": folder_name,
                "status": "completed",
                "createdAt": created_at,
                "updatedAt": finished_at or created_at,
                "source": {
                    "filename": meta.get("originalName") if meta.get("originalName") and meta.get("originalName") != "Unknown" else f"{folder_name}.png",
                },
                "config": meta.get("settings", {}),
                "stages": [
                    {
                        "id": "rendering",
                        "label": "Rendering / final",
                        "status": "completed",
                        "startedAt": created_at,
                        "finishedAt": finished_at,
                        "durationMs": duration_ms,
                    }
                ],
            }
    if manifest is None:
        raise HTTPException(404, detail="Pipeline run not found")
    manifest_stages = {}
    stage_aliases = {"upscaling": "upscale", "textline_merge": "text_grouping"}

    def canonical_stage_id(stage_id: str) -> str:
        return stage_aliases.get(stage_id, stage_id)

    for stage in manifest.get("stages", []):
        if not isinstance(stage, dict) or not stage.get("id"):
            continue
        stage_id = canonical_stage_id(str(stage["id"]))
        manifest_stages[stage_id] = {**stage, "id": stage_id}
    if store is not None:
        try:
            persisted_stages = await store.get_pipeline_stage_state(folder_name)
        except Exception:
            persisted_stages = []
        if persisted_stages:
            for stage in persisted_stages:
                stage_id = canonical_stage_id(stage["stage"])
                manifest_stages[stage_id] = {
                    **manifest_stages.get(stage_id, {}),
                    "id": stage_id,
                    "label": manifest_stages.get(stage_id, {}).get(
                        "label", stage_id.replace("_", " ").title()
                    ),
                    "status": stage["status"],
                    "startedAt": stage["started_at"],
                    "finishedAt": stage["completed_at"],
                    "durationMs": stage["duration_ms"],
                    "reason": stage["error_message"],
                }
    stage_order = {stage.value: index for index, stage in enumerate(STAGE_ORDER)}
    manifest["stages"] = sorted(
        manifest_stages.values(),
        key=lambda stage: stage_order.get(stage.get("id"), len(stage_order)),
    )
    for stage in manifest["stages"]:
        try:
            dependencies = STAGE_DEPENDENCIES[PipelineStage(stage["id"])]
        except (KeyError, ValueError):
            dependencies = frozenset()
        stage["dependsOn"] = [dependency.value for dependency in STAGE_ORDER if dependency in dependencies]
    return manifest


def _ensure_bbox_artifact(
    folder_path: Path,
    file_name: str,
    regions_data: list[dict] | None = None,
) -> bool:
    if file_name in {"bboxes.png", "bboxes_unfiltered.png"}:
        return False
    target_path = folder_path / file_name
    if target_path.is_file():
        return True

    if regions_data is None:
        text_regions_path = folder_path / "text_regions.json"
        if not text_regions_path.is_file():
            return False
        try:
            regions_data = json.loads(text_regions_path.read_text(encoding="utf-8"))
            if not isinstance(regions_data, list):
                return False
        except Exception:
            return False

    if file_name == "detection.json":
        payload = []
        for region in regions_data:
            lines = region.get("lines") or []
            confidence = region.get("confidence") if region.get("confidence") is not None else region.get("prob")
            if lines:
                payload.extend({"pts": line, **({"confidence": confidence} if confidence is not None else {})} for line in lines)
            else:
                x = region.get("x", 0)
                y = region.get("y", 0)
                w = region.get("width", 0)
                h = region.get("height", 0)
                item: dict[str, Any] = {"pts": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]}
                if confidence is not None:
                    item["confidence"] = confidence
                payload.append(item)
        try:
            target_path.write_text(json.dumps(payload), encoding="utf-8")
            return True
        except OSError:
            return False

    return False


def _ensure_thumbnail_artifact(folder_path: Path, file_name: str) -> bool:
    if file_name not in {"thumbnail.webp", "thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg"}:
        return False

    target_path = (folder_path / file_name).resolve()
    if target_path.is_file() and target_path.stat().st_size > 0:
        return True

    source_path = final_file(folder_path)
    if source_path is None or not source_path.is_file() or source_path.stat().st_size == 0:
        input_file = _input_file(folder_path)
        if input_file and input_file.is_file() and input_file.stat().st_size > 0:
            source_path = input_file
        else:
            return False

    try:
        from PIL import Image
        with Image.open(source_path) as img:
            mode = "RGBA" if "A" in img.getbands() else "RGB"
            converted = img.convert(mode)
            converted.thumbnail((360, 500), Image.Resampling.LANCZOS)
            lower_name = file_name.lower()
            if lower_name.endswith(".webp"):
                converted.save(target_path, format="WEBP", quality=80)
            elif lower_name.endswith((".jpg", ".jpeg")):
                if converted.mode == "RGBA":
                    converted = converted.convert("RGB")
                converted.save(target_path, format="JPEG", quality=82)
            else:
                converted.save(target_path, format="PNG")
            return True
    except Exception as err:
        logger.warning(f"Failed to synthesize thumbnail {file_name} for {folder_path.name}: {err}")
        return False

@app.api_route("/result/{folder_name}/{file_name}", methods=["GET", "HEAD"], tags=["api", "file"])
@app.api_route("/api/result/{folder_name}/{file_name}", methods=["GET", "HEAD"], tags=["api", "file"])
async def get_result_file_by_folder(folder_name: str, file_name: str, request: Request):
    """根据文件夹和文件名获取文件 (final.jpg, thumbnail.webp, inpainted.jpg, input.jpg, text_regions.json等)"""
    store = _postgres()
    if store is not None:
        resolved_folder = await store.resolve_folder(folder_name)
        if resolved_folder is None and Path(folder_name).name == folder_name and (RESULT_ROOT / folder_name).is_dir():
            resolved_folder = folder_name
        if resolved_folder is None:
            raise HTTPException(404, detail=f"Result {folder_name} not found")
        folder_name = resolved_folder
    result_dir = RESULT_ROOT.resolve()
    if not result_dir.exists():
        raise HTTPException(404, detail="Result directory not found")

    folder_path = (result_dir / folder_name).resolve()
    if folder_path.parent != result_dir or not folder_path.is_dir():
        raise HTTPException(404, detail=f"Folder {folder_name} not found")
    if file_name in {"bboxes.png", "bboxes_unfiltered.png"}:
        raise HTTPException(404, detail="Bounding-box images are rendered in the frontend")

    if store is not None and file_name.endswith(".json"):
        payload = await store.get_document(folder_name, file_name)
        if file_name == "text_regions.json":
            payload = await store.get_text_regions(folder_name) if payload is None else payload
        if payload is None and file_name == "detection.json":
            regions = await store.get_text_regions(folder_name)
            if regions is not None:
                payload = []
                for region in regions:
                    lines = region.get("lines") or []
                    confidence = region.get("confidence") if region.get("confidence") is not None else region.get("prob")
                    if lines:
                        payload.extend({"pts": line, **({"confidence": confidence} if confidence is not None else {})} for line in lines)
                    else:
                        x = region.get("x", 0)
                        y = region.get("y", 0)
                        w = region.get("width", 0)
                        h = region.get("height", 0)
                        item: dict[str, Any] = {"pts": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]}
                        if confidence is not None:
                            item["confidence"] = confidence
                        payload.append(item)
        if payload is None:
            raise HTTPException(404, detail=f"{file_name} not found")
        return JSONResponse(
            content=payload,
            headers={"Cache-Control": "no-cache"},
        )

    target_path = (folder_path / file_name).resolve()
    if target_path.parent != folder_path:
        raise HTTPException(404, detail=f"Invalid file path")
    if file_name in {"final.png", "final.jpg", "final.jpeg"} and not target_path.is_file():
        target_path = final_file(folder_path) or target_path
    elif file_name == "input.png" and not target_path.is_file():
        target_path = find_asset(folder_path, "input") or target_path
    elif file_name in {"inpainted.png", "inpainted.jpg", "inpainted.jpeg"} and not target_path.is_file():
        target_path = find_asset(folder_path, "inpainted") or target_path

    if store is not None and hasattr(store, "get_pipeline_artifact"):
        artifact_spec = {
            "final.png": ("rendering", "final_image"),
            "final.jpg": ("rendering", "final_image"),
            "final.jpeg": ("rendering", "final_image"),
            "inpainted.png": ("inpainting", "image"),
            "inpainted.jpg": ("inpainting", "image"),
            "inpainted.jpeg": ("inpainting", "image"),
            "mask_raw.png": ("detection", "mask"),
            "text_mask.png": ("mask_generation", "text_mask"),
            "bubble_mask.png": ("mask_generation", "bubble_mask"),
            "mask_final.png": ("mask_generation", "inpaint_mask"),
            "inpaint_mask.png": ("mask_generation", "inpaint_mask"),
        }
        spec = artifact_spec.get(file_name)
        if spec is not None:
            artifact = await store.get_pipeline_artifact(folder_name, *spec)
            if artifact is not None:
                artifact_path = (result_dir / PurePosixPath(artifact["relative_path"])).resolve()
                if not artifact_path.is_relative_to(result_dir) or not artifact_path.is_file():
                    raise HTTPException(404, detail=f"{file_name} not found in active pipeline artifacts")
                target_path = artifact_path

    if not target_path.is_file():
        if file_name in {"batch.webp", "cover.webp", "preview.webp", "reader.webp"}:
            await asyncio.to_thread(generate_image_variants, folder_path, only=file_name.removesuffix(".webp"))
        if file_name.startswith("thumbnail."):
            if not await asyncio.to_thread(_ensure_thumbnail_artifact, folder_path, file_name):
                raise HTTPException(404, detail=f"{file_name} not found in folder")
        else:
            regions_data = None
            if store is not None and file_name == "detection.json":
                regions_data = await store.get_text_regions(folder_name)
                if regions_data is None:
                    raise HTTPException(404, detail="Text regions not found")
            if not await asyncio.to_thread(_ensure_bbox_artifact, folder_path, file_name, regions_data):
                raise HTTPException(404, detail=f"{file_name} not found in folder")

    media_type = "image/png"
    lower_name = target_path.suffix.lower()
    if lower_name.endswith(".json"):
        media_type = "application/json"
    elif lower_name.endswith(".webp"):
        media_type = "image/webp"
    elif lower_name.endswith((".jpg", ".jpeg")):
        media_type = "image/jpeg"
    elif lower_name.endswith(".bmp"):
        media_type = "image/bmp"

    versioned = bool(request and request.query_params.get("v"))
    return FileResponse(
        str(target_path),
        media_type=media_type,
        headers={
            "Content-Disposition": f"inline; filename={file_name}",
            "Cache-Control": "public, max-age=31536000, immutable" if versioned else "public, max-age=0, must-revalidate",
        }
    )

@app.api_route("/result/{folder_name}/final.png", methods=["GET", "HEAD"], tags=["api", "file"])
@app.api_route("/api/result/{folder_name}/final.png", methods=["GET", "HEAD"], tags=["api", "file"])
async def get_result_by_folder(folder_name: str, request: Request):
    """根据文件夹名称获取翻译结果图片 (兼容旧路由)"""
    return await get_result_file_by_folder(folder_name, "final.png", request)

from pydantic import BaseModel
class SaveEditsRequest(BaseModel):
    text_regions: list[dict]
    final_image_base64: str | None = None


class PipelineRerunRequest(BaseModel):
    pageIds: list[str] = Field(default_factory=list, max_length=MAX_BATCH_ITEMS)
    groupId: str | None = Field(default=None, max_length=MAX_MANGA_TITLE_LENGTH)
    mode: str = Field(default="typesetting")
    settingsOverrides: dict[str, Any] = Field(default_factory=dict)


class RerenderRequest(BaseModel):
    pageIds: list[str] = Field(default_factory=list, max_length=MAX_BATCH_ITEMS)
    groupId: str | None = Field(default=None, max_length=MAX_MANGA_TITLE_LENGTH)
    settingsOverrides: dict[str, Any] = Field(default_factory=dict)


@app.post("/results/rerun", tags=["api", "batches"])
@app.post("/api/results/rerun", tags=["api", "batches"])
async def rerun_pipeline(data: PipelineRerunRequest):
    from server.pipeline_rerun import PipelineRerunMode, resolve_rerun_plan, validate_rerun_prerequisites

    if not data.pageIds and not data.groupId:
        raise HTTPException(400, detail="pageIds or groupId is required")
    if data.pageIds and data.groupId:
        raise HTTPException(400, detail="Choose pageIds or groupId, not both")
    if len(set(data.pageIds)) != len(data.pageIds):
        raise HTTPException(400, detail="pageIds must be unique")

    try:
        plan = resolve_rerun_plan(data.mode)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))

    store = _postgres()
    records: list[dict[str, Any]]
    if store is not None:
        if data.groupId:
            pages = await store.group_pages(data.groupId)
        else:
            pages = []
            for page_id in data.pageIds:
                page = await store.page_detail(page_id)
                if page is None:
                    raise HTTPException(404, detail=f"Page {page_id} not found")
                pages.append({
                    "id": page["id"],
                    "folder": page["folder"],
                    "name": page["originalName"],
                    "sourceType": page.get("sourceType"),
                    "hasRegions": page.get("hasTextRegions", False),
                    "pageOrder": page.get("pageOrder"),
                    "meta": {"settings": page.get("settings", {}), "mangaTitle": page.get("mangaTitle")},
                    "groupId": page.get("groupId"),
                })
        records = [
            {
                "id": page["id"],
                "folder": page["folder"],
                "name": page.get("name") or f"{page['folder']}.png",
                "sourceType": page.get("sourceType"),
                "hasTextRegions": bool(page.get("hasRegions")),
                "pageOrder": page.get("pageOrder"),
                "settings": (page.get("meta") or {}).get("settings", {}),
                "mangaTitle": page.get("mangaTitle") or (page.get("meta") or {}).get("mangaTitle", "Ungrouped"),
                "groupId": page.get("groupId") or (page.get("meta") or {}).get("mangaGroupId"),
            }
            for page in pages
        ]
    else:
        scanned = await asyncio.to_thread(
            _scan_results,
            RESULT_ROOT,
            "order",
            data.groupId,
            None,
            None,
            0,
            None,
        )
        by_id = {item["id"]: item for item in scanned["items"]}
        by_folder = {item["folder"]: item for item in scanned["items"]}
        if data.groupId:
            records = [
                {
                    "id": item["id"],
                    "folder": item["folder"],
                    "name": item["originalName"],
                    "sourceType": item.get("sourceType"),
                    "hasTextRegions": bool(item.get("hasTextRegions")),
                    "pageOrder": item.get("pageOrder"),
                    "settings": item.get("settings", {}),
                    "mangaTitle": item.get("mangaTitle", "Ungrouped"),
                    "groupId": item.get("groupId"),
                }
                for item in scanned["items"]
            ]
        else:
            records = []
            for page_id in data.pageIds:
                item = by_id.get(page_id) or by_folder.get(page_id)
                if item is None:
                    raise HTTPException(404, detail=f"Page {page_id} not found")
                records.append({
                    "id": item["id"],
                    "folder": item["folder"],
                    "name": item["originalName"],
                    "sourceType": item.get("sourceType"),
                    "hasTextRegions": bool(item.get("hasTextRegions")),
                    "pageOrder": item.get("pageOrder"),
                    "settings": item.get("settings", {}),
                    "mangaTitle": item.get("mangaTitle", "Ungrouped"),
                    "groupId": item.get("groupId"),
                })

    # Validate prerequisites for each page
    eligible_records = []
    ineligible = []
    for record in records:
        folder = record.get("folder")
        if not folder:
            ineligible.append({"pageId": record["id"], "reason": "No folder"})
            continue
        result_dir = (RESULT_ROOT / folder).resolve()
        if not result_dir.is_dir() and (LEGACY_RESULT_ROOT / folder).is_dir():
            result_dir = (LEGACY_RESULT_ROOT / folder).resolve()
        valid, reason = validate_rerun_prerequisites(result_dir, plan.mode, database=store, record=record)
        if valid:
            eligible_records.append(record)
        else:
            ineligible.append({"pageId": record["id"], "reason": reason})

    if not eligible_records:
        raise HTTPException(
            400,
            detail=f"No eligible pages found for {plan.mode.value} rerun. {ineligible[0]['reason'] if ineligible else ''}".strip(),
        )

    batch_id = f"rerun-{secrets.token_hex(8)}"
    title = str(eligible_records[0].get("mangaTitle") or "Ungrouped")
    initial_stage = "detection" if plan.mode in {PipelineRerunMode.FULL, PipelineRerunMode.REPROCESS_TEXT} else "translating" if plan.mode == PipelineRerunMode.TRANSLATION_TYPESETTING else "rendering"

    items = [
        {
            "id": f"page-{index}-{secrets.token_hex(4)}",
            "name": record["name"],
            "mangaTitle": record.get("mangaTitle") or title,
            "mangaGroupId": record.get("groupId"),
            "pageId": record["id"],
            "pageOrder": record.get("pageOrder"),
            "resultFolder": record["folder"],
            "rerunMode": plan.mode.value,
            "settings": {**(record.get("settings") or {}), **data.settingsOverrides},
            "status": "queued",
            "stage": initial_stage,
        }
        for index, record in enumerate(eligible_records)
    ]
    manifest = {
        "id": batch_id,
        "kind": "pipeline-rerun",
        "rerunMode": plan.mode.value,
        "title": title,
        "mangaTitle": title,
        "mangaGroupId": eligible_records[0].get("groupId"),
        "settings": data.settingsOverrides or {},
        "status": "waiting",
        "items": items,
        "totalItems": len(items),
    }
    try:
        result = await batch_store.put_batch(batch_id, manifest, {})
        batch_scheduler.wake()
        return result
    except Exception as error:
        raise _batch_http_error(error) from error


@app.post("/results/rerender", tags=["api", "batches"])
@app.post("/api/results/rerender", tags=["api", "batches"])
async def rerender_results(data: RerenderRequest):
    # Backward compatibility alias for typesetting rerun
    return await rerun_pipeline(
        PipelineRerunRequest(
            pageIds=data.pageIds,
            groupId=data.groupId,
            mode="typesetting",
            settingsOverrides=data.settingsOverrides,
        )
    )



class LayoutSegmentRequest(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0, le=10000)
    height: int = Field(gt=0, le=10000)


class LayoutPreviewRequest(BaseModel):
    translation: str = Field(max_length=20000)
    group_id: str | None = Field(default=None, max_length=128)
    segments: list[LayoutSegmentRequest] = Field(min_length=1, max_length=8)
    font_size: int = Field(default=24, ge=8, le=512)
    minimum_font_size: int = Field(default=8, ge=8, le=512)
    alignment: Literal["left", "center", "right"] = "center"
    line_spacing: float = Field(default=1.0, ge=0, le=5)
    target_lang: str = Field(default="ENG", max_length=32)


@app.post("/result/{folder_name}/layout-preview", tags=["api", "editor"])
@app.post("/api/result/{folder_name}/layout-preview", tags=["api", "editor"])
async def layout_preview(folder_name: str, data: LayoutPreviewRequest):
    """Use the production FreeType metrics to reflow one linked bubble group."""
    store = _postgres()
    if store is not None:
        resolved_folder = await store.resolve_folder(folder_name)
        if resolved_folder is None:
            raise HTTPException(404, detail=f"Folder {folder_name} not found")
        folder_name = resolved_folder
    result_dir = RESULT_ROOT.resolve()
    folder_path = (result_dir / folder_name).resolve()
    if folder_path.parent != result_dir or not folder_path.is_dir():
        raise HTTPException(404, detail=f"Folder {folder_name} not found")

    def _layout():
        from manga_translator.rendering import _RENDER_LOCK, text_render, get_default_eng_font
        from manga_translator.rendering.bubble_layout import decode_safe_shape, encode_rendered_box
        from manga_translator.rendering.layout import layout_page
        from manga_translator.utils import TextBlock, Context
        from manga_translator.config import Config, RenderConfig
        import cv2
        import numpy as np

        lines = [[[s.x, s.y], [s.x + s.width, s.y], [s.x + s.width, s.y + s.height], [s.x, s.y + s.height]] for s in data.segments]
        region = TextBlock(lines, texts=[""], translation=data.translation,
                           font_size=data.font_size, target_lang=data.target_lang,
                           fg_color=(0, 0, 0), bg_color=(255, 255, 255),
                           alignment=data.alignment)
        saved_regions_path = folder_path / "text_regions.json"
        saved = json.loads(saved_regions_path.read_text(encoding="utf-8")) if saved_regions_path.is_file() else []
        if not isinstance(saved, list):
            saved = []
        stored = next((item for item in saved if isinstance(item, dict) and item.get("id") == data.group_id), None)
        shape = stored.get("bubble_safe_shape") if stored else None
        original_path = find_asset(folder_path, "original_canvas") or final_file(folder_path)
        original = cv2.imread(str(original_path)) if original_path is not None else None
        if original is None:
            max_x = max((s.x + s.width for s in data.segments), default=500) + 50
            max_y = max((s.y + s.height for s in data.segments), default=500) + 50
            original = np.zeros((max_y, max_x, 3), dtype=np.uint8)

        if shape:
            region._bubble_interior = decode_safe_shape(shape, *original.shape[:2])
        shape_available = getattr(region, "_bubble_interior", None) is not None

        font_path = get_default_eng_font()
        cfg = Config(
            render=RenderConfig(
                font_size=data.font_size if data.font_size and data.font_size > 0 else None,
                font_size_minimum=data.minimum_font_size,
                line_spacing=data.line_spacing,
                alignment=data.alignment,
                direction="auto",
            )
        )
        ctx = Context(img_rgb=original, text_regions=[region])
        with _RENDER_LOCK:
            text_render.set_font(font_path)
            layout_page(ctx, cfg, font_path)

        segments = getattr(region, "layout_segments", None) or []
        if not segments:
            return {"fits": False, "font_size": data.font_size, "layout_segments": [], "needs_review": True}

        font_size = getattr(region, "font_size", data.font_size)
        box = getattr(region, "_bubble_box", None)
        rendered_png = encode_rendered_box(box) if box is not None and np.any(box[:, :, 3]) else None

        return {
            "fits": True,
            "needs_review": not shape_available,
            "font_size": font_size,
            "layout_segments": [
                {
                    "x": segment.get("x", 0),
                    "y": segment.get("y", 0),
                    "width": segment.get("width", 0),
                    "height": segment.get("height", 0),
                    "text": segment.get("text", data.translation),
                    "font_size": segment.get("font_size", font_size),
                    "rendered_png": rendered_png if idx == 0 else None,
                    "positioned_lines": [
                        {"text": line.get("text", ""), "x": line.get("x", 0), "y": line.get("y", 0)}
                        for line in segment.get("lines", [])
                    ],
                }
                for idx, segment in enumerate(segments)
            ],
        }

    return await run_cpu_stage(_layout, priority=CPU_PRIORITY_INTERACTIVE)

@app.post("/result/{folder_name}/save_edits", tags=["api", "editor"])
@app.post("/api/result/{folder_name}/save_edits", tags=["api", "editor"])
@app.put("/api/pages/{folder_name}/edits", tags=["api", "editor"])
async def save_edits(folder_name: str, data: SaveEditsRequest):
    """保存交互式编辑器中修改的文本区域和渲染图像"""
    store = _postgres()
    if store is not None:
        resolved_folder = await store.resolve_folder(folder_name)
        if resolved_folder is None:
            raise HTTPException(404, detail=f"Folder {folder_name} not found")
        folder_name = resolved_folder
    result_dir = RESULT_ROOT.resolve()
    folder_path = (result_dir / folder_name).resolve()
    if folder_path.parent != result_dir or not folder_path.is_dir():
        raise HTTPException(404, detail=f"Folder {folder_name} not found")

    def _do_save():
        import base64
        import binascii
        from PIL import Image
        image_bytes = None
        if data.final_image_base64:
            try:
                b64_content = data.final_image_base64
                if "base64," in b64_content:
                    b64_content = b64_content.split("base64,", 1)[1]
                image_bytes = base64.b64decode(b64_content, validate=True)
                with Image.open(io.BytesIO(image_bytes)) as image:
                    image.verify()
            except (binascii.Error, OSError, ValueError) as e:
                raise ValueError("Invalid final image") from e

        # 若前端传回重新合成渲染的图像 base64，保存更新 final.jpg
        if image_bytes is not None:
            final_path = final_file(folder_path) or (folder_path / "final.jpg")
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.load()
                if final_path.suffix.lower() in {".jpg", ".jpeg"}:
                    save_jpeg(image, final_path)
                else:
                    image.save(final_path, format="PNG", compress_level=6)
            for thumb_name in ("thumbnail.webp", "thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg"):
                (folder_path / thumb_name).unlink(missing_ok=True)

    try:
        await asyncio.to_thread(_do_save)
        status = _review_status_for_regions(data.text_regions)
        reviewed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if status == "approved" else None
        if store is not None:
            if not await store.update_text_regions(folder_name, data.text_regions):
                raise HTTPException(404, detail=f"Result {folder_name} not found")
            await store.sync_result_folder(folder_name)
            if not await store.update_review_status(folder_name, status, reviewed_at):
                raise HTTPException(404, detail=f"Result {folder_name} not found")
        else:
            regions_path = folder_path / "text_regions.json"
            temporary_path: Optional[Path] = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=folder_path, prefix=".regions.", suffix=".tmp", delete=False
                ) as temporary:
                    json.dump(data.text_regions, temporary, ensure_ascii=False, indent=2)
                    temporary_path = Path(temporary.name)
                os.replace(temporary_path, regions_path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            metadata = dict(_get_cached_meta(folder_path))
            metadata.update({"reviewStatus": status, "reviewedAt": reviewed_at})
            meta_path = folder_path / "meta.json"
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=folder_path, prefix=".meta.", suffix=".tmp", delete=False
                ) as temporary:
                    json.dump(metadata, temporary, ensure_ascii=False, indent=2)
                    temporary_path = Path(temporary.name)
                os.replace(temporary_path, meta_path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            _invalidate_meta_cache(folder_name)
        await _sync_batch_review(folder_name, status == "pending")
        return {
            "status": "success",
            "message": "Edits saved successfully",
            "reviewStatus": status,
            "reviewedAt": reviewed_at,
        }
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(400, detail=str(ve))
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to save edits: {str(e)}")


class ReviewStatusRequest(BaseModel):
    status: Literal["approved", "pending"]


@app.patch("/pages/{folder_name}/review", tags=["api", "editor"])
@app.patch("/api/pages/{folder_name}/review", tags=["api", "editor"])
async def update_page_review(folder_name: str, data: ReviewStatusRequest):
    store = _postgres()
    if store is not None:
        resolved = await store.resolve_folder(folder_name)
        if resolved is None:
            raise HTTPException(404, detail=f"Result {folder_name} not found")
        folder_name = resolved
        if data.status == "approved":
            regions = await store.get_text_regions(folder_name) or []
            if _review_status_for_regions(regions) == "pending":
                raise HTTPException(409, detail="Resolve flagged text regions before approving")
        if not await store.update_review_status(
            folder_name,
            data.status,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if data.status == "approved" else None,
        ):
            raise HTTPException(404, detail=f"Result {folder_name} not found")
    else:
        folder_path = (RESULT_ROOT / folder_name).resolve()
        if folder_path.parent != RESULT_ROOT.resolve() or not folder_path.is_dir():
            raise HTTPException(404, detail=f"Result {folder_name} not found")
        if data.status == "approved":
            try:
                regions = json.loads((folder_path / "text_regions.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                regions = []
            if _review_status_for_regions(regions) == "pending":
                raise HTTPException(409, detail="Resolve flagged text regions before approving")
        metadata = dict(_get_cached_meta(folder_path))
        metadata.update({
            "reviewStatus": data.status,
            "reviewedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if data.status == "approved" else None,
        })
        meta_path = folder_path / "meta.json"
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        _invalidate_meta_cache(folder_name)
    await _sync_batch_review(folder_name, data.status == "pending")
    return {"status": data.status, "folder": folder_name}

@app.post("/translate/batch/json", response_model=list[TranslationResponse], tags=["api", "json", "batch"])
async def batch_json(req: Request, data: BatchTranslateRequest):
    """Batch translate images and return JSON format results"""
    results = await get_batch_ctx(req, data.config, data.images, data.batch_size)
    return [to_translation(ctx) for ctx in results]

@app.post("/translate/batch/images", response_description="Zip file containing translated images", tags=["api", "batch"])
async def batch_images(req: Request, data: BatchTranslateRequest):
    """Batch translate images and return zip archive containing translated images"""
    import zipfile
    import tempfile
    
    results = await get_batch_ctx(req, data.config, data.images, data.batch_size)
    
    # Create temporary ZIP file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp_file:
        with zipfile.ZipFile(tmp_file, 'w') as zip_file:
            for i, ctx in enumerate(results):
                if ctx.result:
                    img_byte_arr = io.BytesIO()
                    ctx.result.save(img_byte_arr, format="PNG")
                    zip_file.writestr(f"translated_{i+1}.png", img_byte_arr.getvalue())
            failures = {
                str(i + 1): ctx.translation_error
                for i, ctx in enumerate(results) if ctx.get("translation_error")
            }
            if failures:
                zip_file.writestr("failed_pages.json", json.dumps(failures, ensure_ascii=False, indent=2))
        
        # Return ZIP file
        with open(tmp_file.name, 'rb') as f:
            zip_data = f.read()
        
        # Clean up temporary file
        os.unlink(tmp_file.name)
        
        return StreamingResponse(
            io.BytesIO(zip_data),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=translated_images.zip"}
        )

@app.get("/", response_class=HTMLResponse,tags=["ui"])
async def index() -> HTMLResponse:
    script_directory = Path(__file__).parent
    html_file = script_directory / "index.html"
    html_content = html_file.read_text(encoding="utf-8")
    return HTMLResponse(content=html_content)

@app.get("/manual", response_class=HTMLResponse, tags=["ui"])
async def manual():
    script_directory = Path(__file__).parent
    html_file = script_directory / "manual.html"
    html_content = html_file.read_text(encoding="utf-8")
    return HTMLResponse(content=html_content)

def generate_nonce():
    return secrets.token_hex(16)

worker_procs = []
shutting_down = False

def start_translator_client_proc(host: str, port: int, nonce: str, params: Namespace, worker_id: int = 0, gpu_id: Optional[str] = None):
    cmds = [
        sys.executable,
        '-m', 'manga_translator',
        'shared',
        '--host', host,
        '--port', str(port),
        '--nonce', nonce,
    ]
    if params.use_gpu:
        cmds.append('--use-gpu')
    if params.use_gpu_limited:
        cmds.append('--use-gpu-limited')
    if params.ignore_errors:
        cmds.append('--ignore-errors')
    if params.verbose:
        cmds.append('--verbose')
    if params.models_ttl:
        cmds.append('--models-ttl=%s' % params.models_ttl)
    if getattr(params, 'pre_dict', None):
        cmds.extend(['--pre-dict', params.pre_dict])
    if getattr(params, 'post_dict', None):
        cmds.extend(['--post-dict', params.post_dict])       
    base_path = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(base_path)

    env = os.environ.copy()
    env['MANGA_RESULT_ROOT'] = str(RESULT_ROOT)
    if gpu_id is not None:
        env['CUDA_VISIBLE_DEVICES'] = str(gpu_id)

    # Prevent PyTorch/OpenCV in worker from exhausting all CPU cores and starving the main server
    env.setdefault('OMP_NUM_THREADS', '4')
    env.setdefault('OPENBLAS_NUM_THREADS', '4')
    env.setdefault('MKL_NUM_THREADS', '4')
    env.setdefault('VECLIB_MAXIMUM_THREADS', '4')
    env.setdefault('NUMEXPR_NUM_THREADS', '4')

    proc = subprocess.Popen(cmds, cwd=parent, env=env)
    executor_instances.register(ExecutorInstance(ip=host, port=port, worker_id=worker_id))
    return proc

def _init_server_environment(args):
    global nonce
    if args.nonce is None:
        nonce = os.getenv('MT_WEB_NONCE', generate_nonce())
    else:
        nonce = args.nonce
    os.environ['MANGA_RESULT_ROOT'] = str(RESULT_ROOT)
    if os.path.exists(UPLOAD_CACHE_DIR):
        shutil.rmtree(UPLOAD_CACHE_DIR)
    os.makedirs(UPLOAD_CACHE_DIR, exist_ok=True)


def _cpu_threads_per_worker(cpu_count: int, num_workers: int) -> int:
    """Leave one CPU available for the API while translation is running."""
    return max(1, (max(1, cpu_count) - 1) // max(1, num_workers))


def _cpu_stage_worker_count(
    num_workers: int, configured: int | None = None, cpu_count: int | None = None,
) -> int:
    workers = max(1, num_workers)
    if configured is not None:
        return min(workers, max(1, configured))
    # ponytail: keep two page-stage slots when two pipelines are requested; the API shares those cores.
    cpu_budget = max(2, (cpu_count or os.cpu_count() or 4) - 1)
    return min(workers, 3, cpu_budget)


def _setup_inprocess_workers(args, num_workers: int, model_concurrency: int):
    import torch
    import cv2
    from server.in_process_executor import InProcessExecutorInstance
    from manga_translator.utils.model_cache import SharedModelExecutor

    cpu_count = os.cpu_count() or 4
    threads_per_worker = _cpu_threads_per_worker(cpu_count, num_workers)
    try:
        torch.set_num_threads(threads_per_worker)
        torch.set_num_interop_threads(1)
    except Exception:
        pass
    try:
        cv2.setNumThreads(1)
    except Exception:
        pass

    inpainting_concurrency = getattr(args, 'inpainting_concurrency', 0)
    if inpainting_concurrency > 0:
        from manga_translator.inpainting import set_inpainting_concurrency
        set_inpainting_concurrency(inpainting_concurrency)

    translator_params = {
        'verbose': args.verbose,
        'ignore_errors': args.ignore_errors,
        'models_ttl': args.models_ttl,
        'pre_dict': getattr(args, 'pre_dict', None),
        'post_dict': getattr(args, 'post_dict', None),
        'use_gpu': args.use_gpu,
        'use_gpu_limited': args.use_gpu_limited,
        'result_root': str(RESULT_ROOT),
    }

    model_executor = SharedModelExecutor(max_concurrent_calls=model_concurrency)
    logger.info(
        f"Starting {num_workers} in-process image pipeline(s) with shared models, "
        f"up to {model_concurrency} concurrent model calls, and "
        f"{threads_per_worker} CPU thread(s) per pipeline..."
    )
    for i in range(num_workers):
        instance = InProcessExecutorInstance(worker_id=i, translator_params=translator_params,
                                             model_executor=model_executor)
        executor_instances.register(instance)

    logger.info(f"Ready: {num_workers} in-process parallel translator slot(s) active.")
    return []

def _supervise_subprocess_workers():
    while not shutting_down:
        time.sleep(SUPERVISOR_INTERVAL_SEC)
        if shutting_down:
            break
        for w in worker_procs:
            if shutting_down:
                break
            p = w["proc"]
            if p.poll() is not None:
                logger.warning(f"[Worker Supervisor] Worker {w['worker_id']} (port {w['port']}) exited with code {p.returncode}. Restarting...")
                try:
                    new_proc = start_translator_client_proc(
                        w['host'], w['port'], nonce, w['args'],
                        worker_id=w['worker_id'], gpu_id=w['gpu_id']
                    )
                    w["proc"] = new_proc
                    logger.info(f"[Worker Supervisor] Worker {w['worker_id']} restarted successfully.")
                except Exception as e:
                    logger.error(f"[Worker Supervisor] Error restarting worker {w['worker_id']}: {e}")

def _setup_subprocess_workers(args, num_workers: int):
    gpu_ids = None
    if getattr(args, 'gpu_ids', None):
        gpu_ids = [g.strip() for g in args.gpu_ids.split(',') if g.strip()]

    logger.info(f"Starting {num_workers} parallel translator worker process(es)...")
    for i in range(num_workers):
        worker_port = args.port + 1 + i
        gpu_id = gpu_ids[i % len(gpu_ids)] if gpu_ids else None
        worker_host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
        proc = start_translator_client_proc(worker_host, worker_port, nonce, args, worker_id=i, gpu_id=gpu_id)
        worker_procs.append({
            "proc": proc,
            "worker_id": i,
            "port": worker_port,
            "gpu_id": gpu_id,
            "host": worker_host,
            "args": args
        })

    supervisor_thread = threading.Thread(target=_supervise_subprocess_workers, daemon=True)
    supervisor_thread.start()

    def handle_exit_signals(signum, frame):
        global shutting_down
        shutting_down = True
        logger.info("Shutting down translator worker processes...")
        for w in worker_procs:
            try:
                w["proc"].terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit_signals)
    signal.signal(signal.SIGTERM, handle_exit_signals)

    return [w["proc"] for w in worker_procs]

def prepare(args):
    global worker_procs, shutting_down, batch_resource_limits, inference_page_batch_size
    global model_executor_concurrency
    _init_server_environment(args)

    executor_mode = getattr(args, 'executor_mode', EXECUTOR_MODE_INPROCESS)
    num_workers = max(1, getattr(args, 'workers', 3))
    inference_page_batch_size = num_workers
    cpu_workers = _cpu_stage_worker_count(
        num_workers, getattr(args, 'cpu_stage_workers', None)
    )
    gpu_available = False
    if getattr(args, 'start_instance', False):
        import torch
        requested_gpu = bool(getattr(args, 'use_gpu', False) or getattr(args, 'use_gpu_limited', False))
        gpu_available = requested_gpu and (
            torch.xpu.is_available()
            or torch.backends.mps.is_available()
            or torch.cuda.is_available()
        )
    model_executor_concurrency = 1 if gpu_available else MODEL_EXECUTOR_CONCURRENCY
    configure_cpu_stage_workers(cpu_workers)
    batch_resource_limits = stage_resource_limits(
        num_workers, cpu_workers, gpu_concurrency=model_executor_concurrency
    )
    logger.info(
        "Pipeline resources: workers=%d inference-page-batch=%d CPU-heavy=%d CPU-light=%d GPU=%d network=%d I/O=%d local-model/process=%d",
        num_workers,
        inference_page_batch_size,
        batch_resource_limits[ResourceClass.CPU_HEAVY],
        batch_resource_limits[ResourceClass.CPU_LIGHT],
        batch_resource_limits[ResourceClass.GPU],
        batch_resource_limits[ResourceClass.NETWORK],
        batch_resource_limits[ResourceClass.IO],
        model_executor_concurrency,
    )

    if not args.start_instance:
        return []

    if executor_mode == EXECUTOR_MODE_INPROCESS:
        return _setup_inprocess_workers(args, num_workers, model_executor_concurrency)

    return _setup_subprocess_workers(args, num_workers)

@app.post("/simple_execute/translate_batch", tags=["internal-api"])
async def simple_execute_batch(req: Request, data: BatchTranslateRequest):
    """Internal batch translation execution endpoint"""
    # Implementation for batch translation logic
    # Currently returns empty results, actual implementation needs to call batch translator
    from manga_translator import MangaTranslator
    translator = MangaTranslator({'batch_size': data.batch_size})
    
    # Prepare image-config pairs
    images_with_configs = [(img, data.config) for img in data.images]
    
    # Execute batch translation
    results = await translator.translate_batch(images_with_configs, data.batch_size)
    
    return results

@app.post("/execute/translate_batch", tags=["internal-api"])
async def execute_batch_stream(req: Request, data: BatchTranslateRequest):
    """Internal batch translation streaming execution endpoint"""
    # Streaming batch translation implementation
    from manga_translator import MangaTranslator
    translator = MangaTranslator({'batch_size': data.batch_size})
    
    # Prepare image-config pairs
    images_with_configs = [(img, data.config) for img in data.images]
    
    # Execute batch translation (streaming version requires more complex implementation)
    results = await translator.translate_batch(images_with_configs, data.batch_size)
    
    return results

_META_CACHE: dict[str, tuple[float, dict]] = {}

def _get_cached_meta(folder_path: Path) -> dict:
    folder_name = folder_path.name
    meta_file = folder_path / "meta.json"
    if not meta_file.exists():
        return {}
    try:
        mtime = meta_file.stat().st_mtime
    except OSError:
        return {}
    cached = _META_CACHE.get(folder_name)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        parsed = json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception:
        parsed = {}
    _META_CACHE[folder_name] = (mtime, parsed)
    return parsed

def _invalidate_meta_cache(folder_name: Optional[str] = None):
    if folder_name is None:
        _META_CACHE.clear()
    else:
        _META_CACHE.pop(folder_name, None)


def _write_file_backed_meta(item_path: Path, metadata: dict[str, Any]) -> None:
    meta_path = item_path / "meta.json"
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=item_path, prefix=".meta.", suffix=".tmp", delete=False
        ) as temporary:
            json.dump(metadata, temporary, ensure_ascii=False, indent=2)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, meta_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    _invalidate_meta_cache(item_path.name)


def _compact_file_backed_group(
    result_dir: Path,
    title: str,
    excluded_folders: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    excluded = excluded_folders or set()
    pages = [page for page in group_pages(result_dir, title) if page["folder"] not in excluded]
    for index, page in enumerate(pages, 1):
        metadata = dict(page["meta"])
        if metadata.get("pageOrder") == index:
            continue
        metadata["pageOrder"] = index
        _write_file_backed_meta(page["path"], metadata)
    return pages


def _update_file_backed_meta(
    result_dir: Path,
    folders: Optional[List[str]],
    old_title: Optional[str],
    new_title: str,
    group_id: Optional[str],
) -> tuple[Optional[str], int]:
    requested = set(folders or [])
    matched_title: Optional[str] = None
    matches: list[tuple[Path, dict[str, Any], str]] = []
    rename_group = group_id is not None or (not requested and old_title is not None)

    if not result_dir.exists():
        return old_title, 0

    for item_path in result_dir.iterdir():
        if not item_path.is_dir() or final_file(item_path) is None:
            continue
        metadata = dict(_get_cached_meta(item_path))
        current_title = (metadata.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
        matches_group = group_id is not None and group_id in {_manga_id(current_title), current_title}
        matches_page = item_path.name in requested or metadata.get("id") in requested
        matches_title = rename_group and old_title is not None and current_title == old_title
        if not (matches_group or matches_page or matches_title):
            continue
        matches.append((item_path, metadata, current_title))
        matched_title = matched_title or current_title

    if group_id is not None and not matches:
        raise GroupNotFound("Manga group not found")
    if not matches:
        return matched_title or old_title, 0

    if not rename_group:
        selected_folders = {item_path.name for item_path, _, _ in matches}
        _compact_file_backed_group(result_dir, new_title)
        destination_pages = _compact_file_backed_group(result_dir, new_title, selected_folders)
        next_page_order = len(destination_pages) + 1
        for item_path, metadata, _ in sorted(
            matches,
            key=lambda item: (
                meta_page_order(item[1]) is None,
                meta_page_order(item[1]) or 0,
                natural_keys(str(item[1].get("originalName") or item[0].name)),
                item[0].name,
            ),
        ):
            metadata["mangaTitle"] = new_title
            metadata["pageOrder"] = next_page_order
            next_page_order += 1
            _write_file_backed_meta(item_path, metadata)
        for source_title in {current_title for _, _, current_title in matches}:
            _compact_file_backed_group(result_dir, source_title)
    else:
        for item_path, metadata, _ in matches:
            metadata["mangaTitle"] = new_title
            _write_file_backed_meta(item_path, metadata)

    return matched_title or old_title, len(matches)


def _reorder_file_backed_pages(
    result_dir: Path,
    group_value: str,
    page_ids: list[str],
) -> list[dict[str, Any]]:
    if not page_ids or len(set(page_ids)) != len(page_ids):
        raise ValueError("pageIds must contain every page exactly once")

    titles: set[str] = set()
    for item_path in result_dir.iterdir() if result_dir.is_dir() else []:
        if not item_path.is_dir() or final_file(item_path) is None:
            continue
        metadata = _get_cached_meta(item_path)
        title = (metadata.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
        if group_value in {title, _manga_id(title)}:
            titles.add(title)

    if not titles:
        raise GroupNotFound("Manga group not found")
    if len(titles) > 1:
        raise ValueError("Manga group identifier is ambiguous")

    pages = group_pages(result_dir, next(iter(titles)))
    page_keys = [page["meta"].get("id") or page["folder"] for page in pages]
    if len(set(page_keys)) != len(page_keys) or set(page_keys) != set(page_ids):
        raise ValueError("pageIds must contain every active page in the manga group")

    by_id = {page["meta"].get("id") or page["folder"]: page for page in pages}
    for index, page_id in enumerate(page_ids, 1):
        metadata = dict(by_id[page_id]["meta"])
        metadata["pageOrder"] = index
        _write_file_backed_meta(by_id[page_id]["path"], metadata)
    return [{"id": page_id, "pageOrder": index} for index, page_id in enumerate(page_ids, 1)]


def _source_type(meta: dict) -> str:
    if meta.get("sourceType") == "original":
        return "original"
    settings = meta.get("settings") or {}
    if settings.get("translator") == "none" and settings.get("inpainter") == "original":
        return "original"
    return "translated"


def _review_status(meta: dict, folder_path: Optional[Path] = None) -> str:
    status = meta.get("reviewStatus")
    if status == "pending":
        return status
    if folder_path is not None:
        try:
            regions = json.loads((folder_path / "text_regions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            regions = []
        if any(isinstance(region, dict) and region.get("review_required") for region in regions):
            return "pending"
    return status if status in {"approved", "not_required"} else "not_required"


def _review_status_for_regions(regions: list[dict]) -> str:
    return "pending" if any(region.get("review_required") for region in regions) else "approved"


async def _sync_batch_review(folder_name: str, needs_review: bool) -> None:
    try:
        await batch_store.update_review_for_result(folder_name, needs_review)
    except Exception:
        logger.exception("Failed to sync batch review state for %s", folder_name)


def _manga_id(title: str) -> str:
    value = (title or "Ungrouped").strip() or "Ungrouped"
    hashed = 2166136261
    for byte in value.encode("utf-8"):
        hashed = ((hashed ^ byte) * 16777619) & 0xFFFFFFFF
    return f"manga-{hashed:x}"


def _input_file(folder_path: Path) -> Optional[Path]:
    return find_asset(folder_path, "input")


def _image_urls(
    folder_name: str,
    version: int,
    input_file: Optional[Path],
    *,
    include_cover: bool = False,
) -> dict[str, Optional[str]]:
    suffix = f"?v={version}" if version else ""
    base = f"/result/{folder_name}"
    urls = {
        "resultUrl": f"{base}/{(final_file(Path(RESULT_ROOT) / folder_name) or Path('final.png')).name}",
        "fullUrl": f"{base}/{(final_file(Path(RESULT_ROOT) / folder_name) or Path('final.png')).name}{suffix}",
        "thumbnailUrl": f"{base}/thumbnail.webp",
        "batchPreviewUrl": f"{base}/batch.webp{suffix}",
        "detailPreviewUrl": f"{base}/preview.webp{suffix}",
        "readerUrl": f"{base}/reader.webp{suffix}",
        "inputUrl": f"{base}/{input_file.name}" if input_file else None,
    }
    if include_cover:
        urls["coverUrl"] = f"{base}/cover.webp{suffix}"
    return urls

def _scan_results(
    result_dir: Path,
    sort: str,
    manga: Optional[str] = None,
    detail: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    review: Optional[str] = None,
):
    import datetime
    from pathlib import Path

    result_dir = Path(result_dir)
    items = []

    valid_dirs = [
        d for d in result_dir.iterdir()
        if d.is_dir() and final_file(d) is not None
    ]
    sorted_dirs = sorted(valid_dirs, key=lambda p: p.stat().st_mtime, reverse=True)

    clean_target_manga = manga.strip() if manga is not None else None
    is_slim = detail in ("reader", "slim")

    for item_path in sorted_dirs:
        folder_name = item_path.name
        meta = _get_cached_meta(item_path)

        review_status = _review_status(meta, item_path)
        if review == "pending" and review_status != "pending":
            continue

        manga_title = (meta.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"

        if clean_target_manga is not None and clean_target_manga not in {_manga_id(manga_title), manga_title}:
            continue

        original_name = meta.get("originalName")
        if not original_name or original_name == "Unknown":
            original_name = f"{folder_name}.png"

        finished_at = meta.get("finishedAt")
        if not finished_at:
            finished_at = datetime.datetime.fromtimestamp(
                item_path.stat().st_mtime, datetime.timezone.utc
            ).isoformat()

        input_file = _input_file(item_path)
        source_type = _source_type(meta)
        urls = _image_urls(folder_name, asset_version(item_path), input_file)

        if is_slim:
            items.append({
                "id": meta.get("id") or folder_name,
                "groupId": _manga_id(manga_title),
                "folder": folder_name,
                "originalName": original_name,
                "pageOrder": meta_page_order(meta),
                "sourcePath": meta.get("sourcePath"),
                "mangaTitle": manga_title,
                **urls,
                "sourceType": source_type,
                "finishedAt": finished_at,
                "reviewStatus": review_status,
                "reviewedAt": meta.get("reviewedAt"),
                "needsReview": review_status == "pending",
            })
        else:
            has_inpainted = find_asset(item_path, "inpainted") is not None
            has_regions = (item_path / "text_regions.json").exists()
            has_bubble_mask = (item_path / "bubble_mask.png").is_file()
            items.append({
                "id": meta.get("id") or folder_name,
                "groupId": _manga_id(manga_title),
                "folder": folder_name,
                "originalName": original_name,
                "pageOrder": meta_page_order(meta),
                "sourcePath": meta.get("sourcePath"),
                "mangaTitle": manga_title,
                **urls,
                "inpaintedUrl": f"/result/{folder_name}/inpainted.jpg" if has_inpainted else None,
                "textRegionsUrl": f"/result/{folder_name}/text_regions.json" if has_regions else None,
                "bubbleMaskUrl": f"/result/{folder_name}/bubble_mask.png" if has_bubble_mask else None,
                "hasTextRegions": has_regions,
                "sourceType": source_type,
                "finishedAt": finished_at,
                "settings": meta.get("settings", {}),
                "reviewStatus": review_status,
                "reviewedAt": meta.get("reviewedAt"),
                "needsReview": review_status == "pending",
            })

    has_page_order = any(item.get("pageOrder") is not None for item in items)
    if has_page_order:
        items.sort(
            key=lambda x: (
                x.get("pageOrder") is None,
                x.get("pageOrder") or 0,
                natural_keys(x["originalName"]),
                x["folder"],
            )
        )
    elif sort == "alpha_desc":
        items.sort(key=lambda x: natural_keys(x["originalName"]), reverse=True)
    elif sort == "date_asc":
        items.sort(key=lambda x: x["finishedAt"])
    elif sort == "date_desc":
        items.sort(key=lambda x: x["finishedAt"], reverse=True)
    else:
        items.sort(key=lambda x: (natural_keys(x["originalName"]), x["folder"]))

    total = len(items)
    if limit is None:
        visible_items = items
        next_offset = None
    else:
        page_size = max(1, min(int(limit), 500))
        page_offset = max(0, int(offset))
        visible_items = items[page_offset : page_offset + page_size]
        next_offset = page_offset + len(visible_items) if page_offset + len(visible_items) < total else None

    return {
        "directories": [item["folder"] for item in visible_items],
        "items": visible_items,
        "total": total,
        "nextOffset": next_offset,
    }

def _scan_manga_groups(
    result_dir: Path,
    limit: Optional[int] = None,
    offset: int = 0,
    manga_id: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "alpha-asc",
    review: Optional[str] = None,
    status: Optional[str] = None,
):
    import datetime
    from pathlib import Path

    result_dir = Path(result_dir)
    valid_dirs = [
        d for d in result_dir.iterdir()
        if d.is_dir() and final_file(d) is not None
    ]

    effective_status = "review" if review == "pending" or status == "review" else (status or "all")
    groups_map = {}
    query = search.strip().casefold() if search and search.strip() else ""

    for item_path in valid_dirs:
        folder_name = item_path.name
        meta = _get_cached_meta(item_path)
        review_status = _review_status(meta, item_path)
        if effective_status == "review" and review_status != "pending":
            continue

        manga_title = (meta.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
        if manga_id and manga_id not in {_manga_id(manga_title), manga_title}:
            continue
        if query and query not in manga_title.casefold():
            continue

        source_type = _source_type(meta)
        original_name = meta.get("originalName")
        if not original_name or original_name == "Unknown":
            original_name = f"{folder_name}.png"
        finished_at = meta.get("finishedAt")
        if not finished_at:
            finished_at = datetime.datetime.fromtimestamp(
                item_path.stat().st_mtime, datetime.timezone.utc
            ).isoformat()
        if manga_title not in groups_map:
            groups_map[manga_title] = {
                "id": _manga_id(manga_title),
                "title": manga_title,
                "count": 0,
                "translatedCount": 0,
                "originalCount": 0,
                "needsReviewCount": 0,
                "coverCandidate": None,
                "latestFinishedAt": finished_at,
            }

        grp = groups_map[manga_title]
        grp["count"] += 1
        if source_type == "original":
            grp["originalCount"] += 1
        else:
            grp["translatedCount"] += 1
        if review_status == "pending":
            grp["needsReviewCount"] += 1
        if finished_at > grp["latestFinishedAt"]:
            grp["latestFinishedAt"] = finished_at

        item_info = {
            "id": meta.get("id") or folder_name,
            "groupId": _manga_id(manga_title),
            "folder": folder_name,
            "originalName": original_name,
            "pageOrder": meta_page_order(meta),
            "sourcePath": meta.get("sourcePath"),
            "mangaTitle": manga_title,
            **_image_urls(folder_name, asset_version(item_path), _input_file(item_path)),
            "hasTextRegions": (item_path / "text_regions.json").exists(),
            "sourceType": source_type,
            "finishedAt": finished_at,
            "settings": meta.get("settings", {}),
            "reviewStatus": review_status,
            "reviewedAt": meta.get("reviewedAt"),
            "needsReview": review_status == "pending",
        }
        if grp["coverCandidate"] is None or (
            (item_info.get("pageOrder") is None, item_info.get("pageOrder") or 0,
             natural_keys(original_name), folder_name)
            < (grp["coverCandidate"].get("pageOrder") is None,
               grp["coverCandidate"].get("pageOrder") or 0,
               natural_keys(grp["coverCandidate"]["originalName"]),
               grp["coverCandidate"]["folder"])
        ):
            grp["coverCandidate"] = item_info

    for group in groups_map.values():
        candidate = group["coverCandidate"]
        if candidate is None:
            continue
        candidate_path = result_dir / candidate["folder"]
        candidate["coverUrl"] = _image_urls(
            candidate["folder"],
            asset_version(candidate_path),
            _input_file(candidate_path),
            include_cover=True,
        )["coverUrl"]

    groups = []
    total_images = 0
    for title, group in groups_map.items():
        saved = load_summary(result_dir, title)
        has_summary = bool(saved and saved.get("summary"))

        if effective_status == "translated" and group["translatedCount"] == 0:
            continue
        if effective_status == "original" and (group["originalCount"] == 0 or group["translatedCount"] > 0):
            continue
        if effective_status == "summarized" and not has_summary:
            continue
        if effective_status == "review" and group["needsReviewCount"] == 0:
            continue

        total_images += group["count"]
        groups.append({
            "id": group["id"],
            "title": title,
            "count": group["count"],
            "needsReviewCount": group["needsReviewCount"],
            "cover": group["coverCandidate"],
            "latestFinishedAt": group["latestFinishedAt"],
            "hasSummary": has_summary,
        })

    others = [group for group in groups if group["title"] != "Ungrouped"]
    ungrouped = [group for group in groups if group["title"] == "Ungrouped"]
    others.sort(key=lambda group: natural_keys(group["title"]), reverse=sort == "alpha-desc")
    if sort in {"date-asc", "date-desc"}:
        def timestamp(group):
            try:
                return datetime.datetime.fromisoformat(
                    str(group["latestFinishedAt"]).replace("Z", "+00:00")
                ).timestamp()
            except (TypeError, ValueError, OSError):
                return 0

        others.sort(key=timestamp, reverse=sort == "date-desc")
    groups = others + ungrouped

    if limit is None:
        visible_groups = groups
        next_offset = None
    else:
        page_size = max(1, min(int(limit), 500))
        page_offset = max(0, int(offset))
        visible_groups = groups[page_offset : page_offset + page_size]
        next_offset = page_offset + len(visible_groups) if page_offset + len(visible_groups) < len(groups) else None

    return {
        "groups": visible_groups,
        "totalGroups": len(groups),
        "totalImages": total_images,
        "nextOffset": next_offset,
    }

@app.get("/results/groups", tags=["api"])
@app.get("/api/results/groups", tags=["api"])
@app.get("/api/manga", tags=["api"])
async def list_result_groups(
    limit: int = Query(12, ge=1, le=500),
    offset: int = Query(0, ge=0),
    manga_id: Optional[str] = Query(None, alias="mangaId"),
    search: Optional[str] = Query(None, max_length=200),
    sort: str = Query("alpha-asc", pattern="^(alpha-asc|alpha-desc|date-asc|date-desc)$"),
    review: Optional[str] = Query(None, pattern="^pending$"),
    status: Optional[str] = Query(None, pattern="^(all|original|translated|summarized|review)$"),
):
    """List a page of manga groups with counts and pagination metadata."""
    store = _postgres()
    if store is not None:
        try:
            return await store.list_groups(limit, offset, manga_id, search, sort, review, status)
        except Exception as error:
            raise HTTPException(503, detail=f"PostgreSQL result store unavailable: {error}") from error

    result_dir = RESULT_ROOT
    if not result_dir.exists():
        return {"groups": [], "totalGroups": 0, "totalImages": 0, "nextOffset": None}

    try:
        return await asyncio.to_thread(_scan_manga_groups, result_dir, limit, offset, manga_id, search, sort, review, status)
    except Exception as e:
        raise HTTPException(500, detail=f"Error listing result groups: {str(e)}")


@app.get("/series", tags=["api", "series"])
@app.get("/api/series", tags=["api", "series"])
async def list_series(
    limit: int = Query(12, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None, max_length=200),
):
    try:
        return await _postgres_required().list_series(limit, offset, search)
    except Exception as error:
        raise _series_http_error(error) from error


@app.get("/series/{series_id}", tags=["api", "series"])
@app.get("/api/series/{series_id}", tags=["api", "series"])
async def get_series(series_id: str):
    try:
        return await _postgres_required().get_series(series_id)
    except Exception as error:
        raise _series_http_error(error) from error


@app.post("/series", tags=["api", "series"])
@app.post("/api/series", tags=["api", "series"])
async def create_series(data: CreateSeriesRequest):
    try:
        return await _postgres_required().create_series(data.title, data.groupIds)
    except Exception as error:
        raise _series_http_error(error) from error


@app.patch("/series/{series_id}", tags=["api", "series"])
@app.patch("/api/series/{series_id}", tags=["api", "series"])
async def update_series(series_id: str, data: UpdateSeriesRequest):
    try:
        return await _postgres_required().update_series_title(series_id, data.title)
    except Exception as error:
        raise _series_http_error(error) from error


@app.put("/series/{series_id}/members", tags=["api", "series"])
@app.put("/api/series/{series_id}/members", tags=["api", "series"])
async def replace_series_members(series_id: str, data: ReplaceSeriesMembersRequest):
    try:
        return await _postgres_required().replace_series_members(series_id, data.groupIds)
    except Exception as error:
        raise _series_http_error(error) from error


@app.post("/series/{series_id}/members/add", tags=["api", "series"])
@app.post("/api/series/{series_id}/members/add", tags=["api", "series"])
async def add_series_members(series_id: str, data: AddSeriesMembersRequest):
    try:
        return await _postgres_required().add_manga_to_series(series_id, data.groupIds)
    except Exception as error:
        raise _series_http_error(error) from error


@app.post("/manga/{manga_id}/series/move", tags=["api", "series"])
@app.post("/api/manga/{manga_id}/series/move", tags=["api", "series"])
async def move_manga_series(manga_id: str, data: MoveMangaSeriesRequest):
    try:
        return await _postgres_required().move_manga_to_series(manga_id, data.targetSeriesId)
    except Exception as error:
        raise _series_http_error(error) from error


@app.delete("/manga/{manga_id}/series", tags=["api", "series"])
@app.delete("/api/manga/{manga_id}/series", tags=["api", "series"])
async def remove_manga_series(manga_id: str):
    try:
        await _postgres_required().remove_manga_from_series(manga_id)
        return {"status": "removed", "mangaId": manga_id}
    except Exception as error:
        raise _series_http_error(error) from error


@app.delete("/series/{series_id}", tags=["api", "series"])
@app.delete("/api/series/{series_id}", tags=["api", "series"])
async def delete_series(series_id: str):
    try:
        await _postgres_required().delete_series(series_id)
    except Exception as error:
        raise _series_http_error(error) from error
    return {"status": "deleted", "id": series_id}


@app.get("/manga/{manga_id}/series", tags=["api", "series"])
@app.get("/api/manga/{manga_id}/series", tags=["api", "series"])
async def get_manga_series(manga_id: str):
    try:
        return {"series": await _postgres_required().get_series_for_group(manga_id)}
    except Exception as error:
        raise _series_http_error(error) from error


@app.get("/reading-progress", tags=["api", "reader"])
@app.get("/api/reading-progress", tags=["api", "reader"])
async def get_reading_progress(
    installationId: str = Query(..., min_length=8, max_length=128),
    groupId: Optional[str] = Query(None, min_length=1),
    mangaTitle: Optional[str] = Query(None, min_length=1, max_length=MAX_MANGA_TITLE_LENGTH),
):
    store = _postgres()
    if store is None:
        raise HTTPException(503, detail="PostgreSQL progress store is unavailable")
    group_value = (groupId or mangaTitle or "").strip()
    if not group_value:
        raise HTTPException(400, detail="groupId is required")
    resolved_group_id = await store.resolve_group_id(group_value)
    if resolved_group_id is None and mangaTitle:
        resolved_group_id = await store.resolve_group_id(mangaTitle.strip())
    group_value = resolved_group_id or group_value
    resolved_title = await store.resolve_group_title(group_value) or (mangaTitle.strip() if mangaTitle else None)
    return await store.get_progress(
        _validate_installation_id(installationId), group_value
    ) or {
        "installationId": installationId,
        "groupId": resolved_group_id or groupId,
        "mangaTitle": resolved_title,
        "pageId": None,
        "page": None,
        "scrollTop": 0,
        "complete": False,
        "updatedAt": None,
    }


@app.put("/reading-progress", tags=["api", "reader"])
@app.put("/api/reading-progress", tags=["api", "reader"])
async def save_reading_progress(data: ReadingProgressRequest):
    store = _postgres()
    if store is None:
        raise HTTPException(503, detail="PostgreSQL progress store is unavailable")
    payload = data.model_dump()
    payload["installationId"] = _validate_installation_id(data.installationId)
    payload["groupId"] = (data.groupId or data.mangaTitle or "").strip()
    if not payload["groupId"]:
        raise HTTPException(400, detail="groupId is required")
    payload["mangaTitle"] = data.mangaTitle.strip() if data.mangaTitle else None
    if data.page is not None and data.page < 1:
        raise HTTPException(400, detail="Page must be positive")
    try:
        return await store.save_progress(payload)
    except GroupNotFound as error:
        raise HTTPException(404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(400, detail=str(error)) from error


def _validate_original_upload(content: bytes, filename: str) -> tuple[bytes, str]:
    from PIL import Image, ImageFile, ImageOps

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    Image.MAX_IMAGE_PIXELS = None

    try:
        with Image.open(io.BytesIO(content)) as image:
            try:
                image = ImageOps.exif_transpose(image) or image
            except Exception:
                pass
            image.load()
            raw_fmt = (image.format or "").upper()
            suffix = SUPPORTED_IMPORT_FORMATS.get(raw_fmt, ".png")
            normalized = io.BytesIO()
            has_alpha = (
                "A" in image.getbands()
                or image.mode in ("RGBA", "LA", "PA")
                or bool(image.info.get("transparency") is not None)
            )
            mode = "RGBA" if has_alpha else "RGB"
            image.convert(mode).save(normalized, format="PNG")
    except HTTPException:
        raise
    except Exception as error:
        logger.warning("Failed to validate image %s: %s", filename, error, exc_info=True)
        raise HTTPException(400, detail=f"Invalid image: {filename}") from error
    return normalized.getvalue(), suffix


def _is_archive_upload(filename: str, content: bytes) -> bool:
    ext = Path(filename).suffix.casefold()
    if ext in {".cbz", ".zip"}:
        return True
    return content.startswith(b"PK\x03\x04") or content.startswith(b"PK\x05\x06")


def _iter_archive_pages(
    source: BinaryIO,
    archive_filename: str,
) -> Iterator[tuple[str, str, bytes, bytes, str]]:
    try:
        # Python 3.10's SpooledTemporaryFile lacks the seekable attribute ZipFile expects.
        zf = zipfile.ZipFile(getattr(source, "_file", source))
    except Exception as error:
        raise HTTPException(400, detail=f"Invalid or corrupted archive: {archive_filename}") from error

    valid_entries: list[zipfile.ZipInfo] = []
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            parts = Path(name).parts
            if any(part.startswith(".") or part == "__MACOSX" for part in parts):
                continue
            ext = Path(name).suffix.casefold()
            if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif", ".avif", ".tga", ".jfif"}:
                valid_entries.append(info)

        if not valid_entries:
            raise HTTPException(400, detail=f"No supported images found in archive: {archive_filename}")

        valid_entries.sort(key=lambda info: natural_keys(info.filename))
        for info in valid_entries:
            if info.file_size > MAX_BATCH_ITEM_BYTES:
                raise HTTPException(413, detail=f"Archive image is too large: {info.filename}")
            try:
                with zf.open(info) as entry:
                    entry_bytes = entry.read(MAX_BATCH_ITEM_BYTES + 1)
            except Exception as error:
                logger.exception("Failed reading archive entry %s from %s", info.filename, archive_filename)
                raise HTTPException(400, detail=f"Failed reading archive entry: {info.filename}") from error
            if len(entry_bytes) > MAX_BATCH_ITEM_BYTES:
                raise HTTPException(413, detail=f"Archive image is too large: {info.filename}")

            raw_basename = Path(info.filename).name
            normalized, suffix = _validate_original_upload(entry_bytes, raw_basename)
            yield raw_basename, f"{archive_filename}/{info.filename}", entry_bytes, normalized, suffix


def _iter_original_upload_pages(
    uploads: list[UploadFile],
    source_paths: list[str] | None = None,
) -> Iterator[tuple[str, str, bytes, bytes, str]]:
    page_count = 0
    total_bytes = 0

    for upload_index, upload in enumerate(uploads):
        filename = (upload.filename or "").strip()
        if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise HTTPException(400, detail="Invalid uploaded filename")

        upload.file.seek(0)
        header = upload.file.read(4)
        upload.file.seek(0)
        source_path = source_paths[upload_index] if source_paths and upload_index < len(source_paths) else filename
        if _is_archive_upload(filename, header):
            pages = _iter_archive_pages(upload.file, source_path)
        else:
            content = upload.file.read(MAX_BATCH_ITEM_BYTES + 1)
            if len(content) > MAX_BATCH_ITEM_BYTES:
                raise HTTPException(413, detail=f"Image is too large: {filename}")
            normalized, suffix = _validate_original_upload(content, filename)
            pages = iter(((filename, source_path, content, normalized, suffix),))

        for page in pages:
            page_count += 1
            if page_count > MAX_BATCH_ITEMS:
                raise HTTPException(413, detail="Manga contains too many pages")
            total_bytes += len(page[2])
            if total_bytes > MAX_MANGA_IMPORT_BYTES:
                raise HTTPException(413, detail="Manga import exceeds 20 GB")
            yield page


    if page_count == 0:
        raise HTTPException(400, detail="At least one image is required")


def _write_original_import(
    title: str,
    pages: Iterable[tuple[str, str, bytes, bytes, str]],
    group_id: Optional[str] = None,
) -> dict:
    from datetime import datetime, timezone
    from PIL import Image

    staging = Path(tempfile.mkdtemp(prefix=".original-import-", dir=RESULT_ROOT))
    moved: list[Path] = []
    records: list[dict[str, Any]] = []
    finished_at = datetime.now(timezone.utc).isoformat()
    try:
        for original_name, source_path, content, normalized, suffix in pages:
            folder_name = f"original-{secrets.token_hex(12)}"
            folder = staging / folder_name
            folder.mkdir()
            with Image.open(io.BytesIO(normalized)) as normalized_image:
                save_jpeg(normalized_image, folder / "input.jpg")
                save_jpeg(normalized_image, folder / "final.jpg")
            metadata = {
                "id": folder_name,
                "originalName": original_name,
                "mangaTitle": title,
                "sourceType": "original",
                "finishedAt": finished_at,
                "sourcePath": source_path,
                "settings": {"translator": "none"},
            }
            if group_id:
                metadata["mangaGroupId"] = group_id
                metadata["groupId"] = group_id
            records.append({
                "folder": folder_name,
                "metadata": metadata,
            })
            (folder / "meta.json").write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )
            if len(records) == 1 or len(records) % 25 == 0:
                logger.info("Original manga import staging: title=%r pages=%d", title, len(records))

        logger.info("Original manga import staged: title=%r pages=%d", title, len(records))
        for folder in staging.iterdir():
            destination = RESULT_ROOT / folder.name
            os.replace(folder, destination)
            moved.append(destination)
        staging.rmdir()
    except Exception:
        for destination in moved:
            shutil.rmtree(destination, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {"records": records}


def _warm_preview_variants(folders: list[str]) -> None:
    failures = 0
    for folder in folders:
        try:
            generate_image_variants(RESULT_ROOT / folder, only="preview")
        except Exception:
            failures += 1
    if failures:
        logger.warning("Preview warmup failed for %d/%d imported pages", failures, len(folders))


@app.post("/results/import", tags=["api", "gallery"])
@app.post("/api/results/import", tags=["api", "gallery"])
async def import_original_manga(request: Request, background_tasks: BackgroundTasks):
    try:
        form = await request.form(
            max_files=MAX_BATCH_ITEMS,
            max_fields=MAX_BATCH_ITEMS,
            max_part_size=MAX_BATCH_ITEM_BYTES,
        )
    except Exception as error:
        logger.exception("Failed to parse multipart body in import_original_manga")
        raise HTTPException(400, detail=f"Failed to parse upload: {error}") from error

    raw_title = form.get("mangaTitle")
    if hasattr(raw_title, "read"):
        raw_title = (await raw_title.read()).decode("utf-8")
    clean_title = str(raw_title or "").strip()
    if not clean_title or clean_title.casefold() == "ungrouped":
        raise HTTPException(400, detail="A manga title is required")
    if len(clean_title) > MAX_MANGA_TITLE_LENGTH:
        raise HTTPException(
            422,
            detail=f"Manga title must be at most {MAX_MANGA_TITLE_LENGTH} characters",
        )

    raw_group_id = form.get("mangaGroupId") or form.get("groupId")
    if hasattr(raw_group_id, "read"):
        raw_group_id = (await raw_group_id.read()).decode("utf-8")
    clean_group_id = str(raw_group_id or "").strip() or None

    raw_is_new_group = form.get("isNewGroup")
    if hasattr(raw_is_new_group, "read"):
        raw_is_new_group = (await raw_is_new_group.read()).decode("utf-8")
    is_new_group = str(raw_is_new_group or "").strip().lower() in ("true", "1") if raw_is_new_group is not None else None

    files: list[UploadFile] = []
    for key, value in form.multi_items():
        if hasattr(value, "file") and hasattr(value, "filename"):
            files.append(value)

    if not files:
        raise HTTPException(400, detail="At least one image is required")
    if len(files) > MAX_BATCH_ITEMS:
        raise HTTPException(413, detail="Manga contains too many pages")

    source_paths: list[str] | None = None
    raw_page_metadata = form.get("pageMetadata")
    if hasattr(raw_page_metadata, "read"):
        raw_page_metadata = (await raw_page_metadata.read()).decode("utf-8")
    if raw_page_metadata:
        try:
            metadata_entries = json.loads(str(raw_page_metadata))
            if isinstance(metadata_entries, list):
                source_paths = [
                    str(entry.get("sourcePath") or entry.get("originalName") or "")
                    if isinstance(entry, dict) else ""
                    for entry in metadata_entries
                ]
        except (TypeError, ValueError):
            raise HTTPException(400, detail="Invalid page metadata")

    store = _postgres()
    resolved_group_id: Optional[str] = None

    if clean_group_id and store is not None:
        resolved_group_id = await store.resolve_group_id(clean_group_id, create=False)
        if resolved_group_id is None:
            resolved_group_id = await store.resolve_group_id(clean_title, create=False)

    if store is not None and resolved_group_id is None:
        resolved_group_id = await store.resolve_group_id(clean_title, create=False)

    if is_new_group is True or (clean_group_id is None and is_new_group is None):
        duplicate = (
            await store.group_exists(clean_title)
            if store is not None
            else bool(group_pages(RESULT_ROOT, clean_title))
        )
        if duplicate:
            raise HTTPException(409, detail="A manga with this title already exists")

    if store is not None and resolved_group_id is None:
        resolved_group_id = await store.resolve_group_id(clean_group_id or clean_title, create=True)

    target_group_id = resolved_group_id or clean_group_id or _manga_id(clean_title)

    logger.info("Original manga import started: title=%r uploads=%d group_id=%r", clean_title, len(files), target_group_id)
    try:
        imported = await asyncio.to_thread(
            _write_original_import,
            clean_title,
            _iter_original_upload_pages(files, source_paths),
            target_group_id,
        )
        if store is not None:
            for record in imported["records"]:
                await store.save_documents(
                    record["folder"], {"meta.json": record["metadata"]}
                )
                # Keep import completion fast; the detail preview is warmed after the response.
                await store.sync_result_folder(record["folder"], generate_variants=False)
            groups_payload = await store.list_groups(limit=1, manga_id=target_group_id)
            if not groups_payload["groups"]:
                groups_payload = await store.list_groups(limit=1, search=clean_title)
            pages_payload = await store.list_results(manga=clean_title, limit=500)
        else:
            imported_folders = {record["folder"] for record in imported["records"]}
            existing_pages = _compact_file_backed_group(RESULT_ROOT, clean_title, imported_folders)
            next_page_order = len(existing_pages) + 1
            for record in imported["records"]:
                record["metadata"]["pageOrder"] = next_page_order
                next_page_order += 1
                _write_file_backed_meta(RESULT_ROOT / record["folder"], record["metadata"])
            groups_payload = await asyncio.to_thread(
                _scan_manga_groups, RESULT_ROOT, 1, 0, None, clean_title, "alpha-asc"
            )
            pages_payload = await asyncio.to_thread(
                _scan_results, RESULT_ROOT, "alpha", clean_title, None, 500, 0, None
            )
            _invalidate_meta_cache()
        if not groups_payload["groups"]:
            raise RuntimeError("Imported manga group was not found after saving")
        background_tasks.add_task(
            _warm_preview_variants,
            [record["folder"] for record in imported["records"]],
        )
        return {
            "group": groups_payload["groups"][0],
            "items": pages_payload["items"],
            "totalImages": groups_payload["totalImages"],
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Original manga import failed")
        raise HTTPException(500, detail="Could not import manga") from error
    finally:
        await asyncio.gather(*(upload.close() for upload in files if hasattr(upload, "close")), return_exceptions=True)


def _summary_target_language(pages: list[dict]) -> str:
    """Use the target saved on the naturally last page, as the UI does."""
    if pages and all(
        page.get("meta", {}).get("sourceType") == "original"
        for page in pages
    ):
        return "ENG"
    valid_languages = {
        "CHS", "CHT", "CSY", "NLD", "ENG", "FRA", "DEU", "HUN", "ITA", "JPN",
        "KOR", "POL", "PTB", "ROM", "RUS", "ESP", "TRK", "UKR", "VIN", "ARA",
        "CNR", "SRP", "HRV", "THA", "IND", "FIL",
    }
    if pages:
        settings = pages[-1].get("meta", {}).get("settings", {})
        if isinstance(settings, dict):
            value = settings.get("targetLanguage") or settings.get("target_lang")
            if isinstance(value, str) and value.strip():
                value = value.strip().upper()
                if value in valid_languages:
                    return value
    return "ENG"


def _safe_setting(value, allowed: set[str], default: str) -> str:
    value = value.value if hasattr(value, "value") else value
    return value if isinstance(value, str) and value in allowed else default


def _ocr_config(page: dict, target_language: str) -> Config:
    from manga_translator.config import Detector, Inpainter, Ocr, Renderer, Translator

    settings = page.get("meta", {}).get("settings", {})
    settings = settings if isinstance(settings, dict) else {}
    detector = _safe_setting(
        settings.get("textDetector") or settings.get("detector"),
        {item.value for item in Detector},
        Detector.default.value,
    )
    ocr = _safe_setting(settings.get("ocr"), {item.value for item in Ocr}, Ocr.ocr48px_ctc.value)
    try:
        detection_size = max(256, min(8192, int(settings.get("detectionResolution", 2560))))
    except (TypeError, ValueError):
        detection_size = 2560
    try:
        box_threshold = float(settings.get("customBoxThreshold", 0.45))
    except (TypeError, ValueError):
        box_threshold = 0.45
    try:
        unclip_ratio = float(settings.get("customUnclipRatio", 2.3))
    except (TypeError, ValueError):
        unclip_ratio = 2.3

    source_type = page.get("meta", {}).get("sourceType")
    config = Config(
        original_name=page["name"],
        manga_title=page.get("meta", {}).get("mangaTitle"),
        detector={
            "detector": detector,
            "detection_size": detection_size,
            "box_threshold": box_threshold,
            "unclip_ratio": unclip_ratio,
        },
        ocr={"ocr": ocr},
        translator={
            "translator": Translator.original.value,
            "target_lang": "ENG" if source_type == "original" else target_language,
            "no_text_lang_skip": True,
        },
        render={"renderer": Renderer.none.value},
        inpainter={"inpainter": Inpainter.original.value},
        colorizer={"colorizer": "none"},
        upscale={"upscale_ratio": None},
    )
    config._web_frontend_optimized = False
    return config


def _json_value(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _repaired_regions(ctx) -> list[dict]:
    regions = []
    for index, region in enumerate(getattr(ctx, "text_regions", []) or []):
        xywh = _json_value(getattr(region, "xywh", None)) or [0, 0, 0, 0]
        x, y, width, height = (list(xywh) + [0, 0, 0, 0])[:4]
        text = str(getattr(region, "text", "") or "")
        regions.append({
            "id": f"bubble_{index}",
            "x": int(x),
            "y": int(y),
            "width": int(width),
            "height": int(height),
            "lines": _json_value(getattr(region, "lines", [])) or [],
            "original_text": text,
            "translation": text,
            "font_size": int(getattr(region, "font_size", 24) or 24),
            "font_family": "Comic Neue",
            "fg_color": [0, 0, 0],
            "bg_color": [255, 255, 255],
            "stroke_width": 2,
            "angle": float(getattr(region, "angle", 0) or 0),
            "direction": "h",
            "alignment": "center",
            "line_spacing": 1,
            "letter_spacing": 1,
            "bold": False,
            "italic": False,
        })
    return regions


async def _run_summary_ocr(
    request: Request,
    page: dict,
    target_language: str,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
    worker=None,
    overwrite: bool = False,
) -> list[dict]:
    page_path = page.get("path")
    input_path = _input_file(page_path) if isinstance(page_path, Path) else None
    if input_path is None or not input_path.is_file():
        input_path = final_file(page_path) if isinstance(page_path, Path) else None
    if input_path is None or not input_path.is_file():
        raise FileNotFoundError("input image is missing")
    image_bytes = await asyncio.to_thread(input_path.read_bytes)
    progress_queue: asyncio.Queue[str | None] | None = asyncio.Queue() if on_progress else None
    consumer: asyncio.Task[None] | None = None
    if progress_queue is not None and on_progress is not None:
        async def consume_progress() -> None:
            while True:
                stage = await progress_queue.get()
                if stage is None:
                    return
                await on_progress(stage)

        consumer = asyncio.create_task(consume_progress())

    def report_progress(stage: str) -> None:
        if progress_queue is not None:
            progress_queue.put_nowait(stage)

    ctx = None
    try:
        if worker is None:
            ctx = await get_ctx(
                request,
                _ocr_config(page, target_language),
                image_bytes,
                report_progress if progress_queue is not None else None,
            )
        else:
            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as opened:
                image = opened.convert("RGB")
            try:
                extract_text = getattr(worker, "extract_text", None)
                if extract_text is None:
                    ctx = await worker.sent(image, _ocr_config(page, target_language))
                else:
                    ctx = await extract_text(image, _ocr_config(page, target_language))
            finally:
                image.close()
                del image
    finally:
        del image_bytes
        if progress_queue is not None and consumer is not None:
            progress_queue.put_nowait(None)
            await consumer
    regions = _repaired_regions(ctx)
    if hasattr(ctx, 'cleanup_all_images'):
        ctx.cleanup_all_images()
    del ctx
    store = _postgres()
    if store is not None and "id" in page:
        if not await store.update_text_regions(page["id"], regions):
            raise FileNotFoundError("page is missing from PostgreSQL")
    path = page.get("path")
    if isinstance(path, Path):
        path.mkdir(parents=True, exist_ok=True)
        regions_path = path / "text_regions.json"
        temporary = regions_path.with_suffix(".tmp")
        await asyncio.to_thread(
            temporary.write_text,
            json.dumps(regions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await asyncio.to_thread(os.replace, temporary, regions_path)
    return regions


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
    if store is not None:
        return (await store.summary_status(group_value)) or {}
    return await asyncio.to_thread(synopsis_status, RESULT_ROOT, title, pages)


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
) -> None:
    if store is not None:
        await store.update_summary_job(
            group_value,
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
            refresh_text=refresh_text,
            regenerate=regenerate,
        )
        return
    await asyncio.to_thread(
        update_summary_job,
        RESULT_ROOT,
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
        refresh_text=refresh_text,
        regenerate=regenerate,
    )


@app.get("/results/group/summary", tags=["api"])
@app.get("/api/results/group/summary", tags=["api"])
async def get_manga_summary(
    groupId: Optional[str] = Query(None, min_length=1),
    title: Optional[str] = Query(None, min_length=1, max_length=MAX_MANGA_TITLE_LENGTH),
):
    group_value = (groupId or title or "").strip()
    if not group_value:
        raise HTTPException(400, detail="groupId is required")
    clean_title = title.strip() if title else "Ungrouped"
    store = _postgres()
    if store is not None:
        resolved_group = await store.resolve_group_id(group_value)
        if resolved_group is None and title:
            resolved_group = await store.resolve_group_id(title.strip())
        group_value = resolved_group or group_value
        clean_title = await store.resolve_group_title(group_value) or clean_title
        pages = await store.group_pages(group_value)
    else:
        pages = await asyncio.to_thread(group_pages, RESULT_ROOT, clean_title)
    if not pages:
        raise HTTPException(404, detail="Manga group not found")
    return await _summary_status_for(store, group_value, clean_title, pages)


@app.get("/results/group/summary/config", tags=["api"])
@app.get("/api/results/group/summary/config", tags=["api"])
async def get_manga_summary_config():
    from manga_translator.translators.keys import GEMINI_MODEL, GROQ_MODEL

    return {
        "provider": "deepseek",
        "model": DEFAULT_SUMMARY_MODEL,
        "models": ["deepseek-flash", "groq", "gemini"],
        "providers": {
            "deepseek": ["deepseek-flash"],
            "groq": [GROQ_MODEL],
            "gemini": [GEMINI_MODEL],
        },
    }


@app.get("/results/group/summary/jobs", tags=["api"])
@app.get("/api/results/group/summary/jobs", tags=["api"])
async def get_manga_summary_jobs():
    store = _postgres()
    if store is not None:
        return await store.list_summary_jobs(20)
    return await asyncio.to_thread(list_summary_jobs, RESULT_ROOT, 20)


async def _summary_job_events():
    last_snapshot = None
    while True:
        # ponytail: two-second snapshot reads keep this simple; switch to store notifications if stream load grows.
        store = _postgres()
        jobs = (
            await store.list_summary_jobs(20)
            if store is not None
            else await asyncio.to_thread(list_summary_jobs, RESULT_ROOT, 20)
        )
        snapshot = json.dumps(jobs, sort_keys=True, separators=(",", ":"))
        if snapshot != last_snapshot:
            last_snapshot = snapshot
            yield f"data: {snapshot}\n\n"
        else:
            yield ": keep-alive\n\n"
        await asyncio.sleep(2)


@app.get("/results/group/summary/jobs/events", tags=["api"])
@app.get("/api/results/group/summary/jobs/events", tags=["api"])
async def manga_summary_job_events():
    return StreamingResponse(
        _summary_job_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/results/group/summary/dismiss", tags=["api"])
@app.post("/api/results/group/summary/dismiss", tags=["api"])
async def dismiss_manga_summary_job(data: SummaryDismissRequest):
    group_value = (data.groupId or data.mangaTitle or "").strip()
    if not group_value:
        raise HTTPException(400, detail="groupId is required")
    _summary_controller.stop(group_value)
    if data.mangaTitle:
        _summary_controller.stop(data.mangaTitle.strip())
    store = _postgres()
    if store is not None:
        await store.dismiss_summary_job(group_value)
    else:
        await asyncio.to_thread(dismiss_summary_job, RESULT_ROOT, data.mangaTitle or group_value)
    if summary_scheduler is not None:
        summary_scheduler.wake()
    return {"status": "dismissed"}


@app.post("/results/group/summary/pause", tags=["api"])
@app.post("/api/results/group/summary/pause", tags=["api"])
async def pause_manga_summary_job(data: SummaryControlRequest):
    group_value = (data.groupId or data.mangaTitle or "").strip()
    if not group_value:
        raise HTTPException(400, detail="groupId is required")
    clean_title = (data.mangaTitle or "Ungrouped").strip() or "Ungrouped"
    store = _postgres()
    if store is not None:
        resolved_group = await store.resolve_group_id(group_value)
        if resolved_group is None and data.mangaTitle:
            resolved_group = await store.resolve_group_id(data.mangaTitle.strip())
        group_value = resolved_group or group_value
        clean_title = await store.resolve_group_title(group_value) or clean_title
        pages = await store.group_pages(group_value)
    else:
        pages = await asyncio.to_thread(group_pages, RESULT_ROOT, clean_title)

    status = await _summary_status_for(store, group_value, clean_title, pages)
    current_status = status.get("jobStatus")
    if current_status not in {"generating", "queued"}:
        if current_status == "paused":
            return status
        raise HTTPException(400, detail=f"Cannot pause summary job with status '{current_status}'")

    _summary_controller.pause(group_value)
    _summary_controller.pause(clean_title)

    await _update_summary_job_for(
        store,
        group_value,
        clean_title,
        "paused",
        None,
        status.get("jobStage"),
        status.get("jobProgress"),
        "Paused",
        status.get("jobCurrentPage"),
        status.get("jobPageCount"),
        status.get("jobPagesWithText"),
        status.get("jobExtractionRequired"),
        status.get("provider"),
        status.get("model"),
    )
    _summary_log("paused", clean_title, group_value)
    if summary_scheduler is not None:
        summary_scheduler.wake()
    return await _summary_status_for(store, group_value, clean_title, pages)


@app.post("/results/group/summary/resume", tags=["api"])
@app.post("/api/results/group/summary/resume", tags=["api"])
async def resume_manga_summary_job(request: Request, data: SummaryControlRequest):
    group_value = (data.groupId or data.mangaTitle or "").strip()
    if not group_value:
        raise HTTPException(400, detail="groupId is required")
    clean_title = (data.mangaTitle or "Ungrouped").strip() or "Ungrouped"
    store = _postgres()
    if store is not None:
        resolved_group = await store.resolve_group_id(group_value)
        if resolved_group is None and data.mangaTitle:
            resolved_group = await store.resolve_group_id(data.mangaTitle.strip())
        group_value = resolved_group or group_value
        clean_title = await store.resolve_group_title(group_value) or clean_title
        pages = await store.group_pages(group_value)
    else:
        pages = await asyncio.to_thread(group_pages, RESULT_ROOT, clean_title)

    status = await _summary_status_for(store, group_value, clean_title, pages)
    current_status = status.get("jobStatus")
    if current_status != "paused":
        if current_status in {"generating", "queued"}:
            return status
        raise HTTPException(400, detail=f"Cannot resume summary job with status '{current_status}'")

    _summary_controller.resume(group_value)
    _summary_controller.resume(clean_title)

    await _update_summary_job_for(
        store,
        group_value,
        clean_title,
        "generating",
        None,
        status.get("jobStage") or "concatenating",
        status.get("jobProgress") or 0,
        "Resuming synopsis generation…",
        status.get("jobCurrentPage"),
        status.get("jobPageCount"),
        status.get("jobPagesWithText"),
        status.get("jobExtractionRequired"),
        status.get("provider"),
        status.get("model"),
    )
    _summary_log("resumed", clean_title, group_value)

    active_task = _summary_controller.get_task(group_value) or _summary_controller.get_task(clean_title)
    if active_task is not None and not active_task.done():
        pass
    elif summary_scheduler is not None:
        await _update_summary_job_for(
            store,
            group_value,
            clean_title,
            "queued",
            None,
            status.get("jobStage") or "concatenating",
            status.get("jobProgress") or 0,
            "Waiting for an available worker",
            status.get("jobCurrentPage"),
            status.get("jobPageCount"),
            status.get("jobPagesWithText"),
            status.get("jobExtractionRequired"),
            status.get("provider"),
            status.get("model"),
            regenerate=True,
        )
        summary_scheduler.wake()
    else:
        req_data = MangaSummaryRequest(
            groupId=group_value if store is not None else None,
            mangaTitle=clean_title,
            summaryModel=status.get("model"),
            regenerate=True,
            refreshText=False,
        )
        pause_event = _summary_controller.get_pause_event(group_value)
        pause_event.set()
        task = asyncio.create_task(
            _run_summary_task(
                request, req_data, store, group_value, clean_title, worker=None, pause_event=pause_event
            ),
            name=f"summary-{group_value}",
        )
        _summary_controller.register_task(group_value, req_data, task)

    return await _summary_status_for(store, group_value, clean_title, pages)


@app.post("/results/group/summary/stop", tags=["api"])
@app.post("/api/results/group/summary/stop", tags=["api"])
async def stop_manga_summary_job(data: SummaryControlRequest):
    group_value = (data.groupId or data.mangaTitle or "").strip()
    if not group_value:
        raise HTTPException(400, detail="groupId is required")
    clean_title = (data.mangaTitle or "Ungrouped").strip() or "Ungrouped"
    store = _postgres()
    if store is not None:
        resolved_group = await store.resolve_group_id(group_value)
        if resolved_group is None and data.mangaTitle:
            resolved_group = await store.resolve_group_id(data.mangaTitle.strip())
        group_value = resolved_group or group_value
        clean_title = await store.resolve_group_title(group_value) or clean_title

    _summary_controller.stop(group_value)
    _summary_controller.stop(clean_title)

    if store is not None:
        await store.dismiss_summary_job(group_value)
    else:
        await asyncio.to_thread(dismiss_summary_job, RESULT_ROOT, clean_title)

    if summary_scheduler is not None:
        summary_scheduler.wake()

    _summary_log("stopped", clean_title, group_value)
    return {"status": "stopped"}


@app.post("/results/group/summary", tags=["api"])
@app.post("/api/results/group/summary", tags=["api"])
async def create_manga_summary(request: Request, data: MangaSummaryRequest):
    group_value = (data.groupId or data.mangaTitle or "").strip()
    if not group_value:
        raise HTTPException(400, detail="groupId is required")
    clean_title = (data.mangaTitle or "Ungrouped").strip() or "Ungrouped"
    async with _summary_submit_lock:
        store = _postgres()
        if store is not None:
            resolved_group = await store.resolve_group_id(group_value)
            if resolved_group is None and data.mangaTitle:
                resolved_group = await store.resolve_group_id(data.mangaTitle.strip())
            group_value = resolved_group or group_value
            clean_title = await store.resolve_group_title(group_value) or clean_title
            pages = await store.group_pages(group_value)
        else:
            pages = await asyncio.to_thread(group_pages, RESULT_ROOT, clean_title)
        if not pages:
            raise HTTPException(404, detail="Manga group not found")

        force_regenerate = data.regenerate or data.refreshText
        status = await _summary_status_for(store, group_value, clean_title, pages)
        if status["jobStatus"] in {"queued", "generating", "paused"}:
            if not force_regenerate:
                _summary_log(
                    "skipped",
                    clean_title,
                    group_value,
                    reason=f"already_{status['jobStatus']}",
                    stage=status.get("jobStage"),
                    progress=status.get("jobProgress"),
                )
                return status
            else:
                _summary_controller.stop(group_value)
                _summary_controller.stop(clean_title)
        if status["summary"] and not status["stale"] and not force_regenerate:
            if status.get("jobStatus") != "ready":
                await _update_summary_job_for(
                    store,
                    group_value,
                    clean_title,
                    "ready",
                    stage="complete",
                    progress=100,
                    message="Summary ready",
                )
                status["jobStatus"] = "ready"
                status["jobStage"] = "complete"
                status["jobProgress"] = 100
                status["jobMessage"] = "Summary ready"
            _summary_log(
                "skipped",
                clean_title,
                group_value,
                reason="already_ready",
                summary_available=True,
            )
            return status

        page_count = len(pages)
        try:
            summary_provider, summary_model, _, _ = resolve_summary_model(data.summaryModel)
        except ValueError as exc:
            _summary_log(
                "rejected",
                clean_title,
                group_value,
                level=logging.WARNING,
                reason="invalid_model",
                error=str(exc),
            )
            raise HTTPException(400, detail=str(exc)) from exc
        has_group_text = any(bool(read_page_text(page)) for page in pages)
        missing_page_count = (
            page_count
            if data.refreshText
            else sum(not is_page_text_extracted(page, has_group_text=has_group_text) for page in pages)
        )
        extraction_required = missing_page_count > 0
        pages_with_text = (
            sum(bool(read_page_text(page)) for page in pages)
            if not extraction_required
            else 0
        )
        await _update_summary_job_for(
            store,
            group_value,
            clean_title,
            "queued",
            None,
            "detecting" if extraction_required else "concatenating",
            0,
            (
                "Waiting to re-read text from all pages"
                if data.refreshText
                else "Waiting for an available worker"
            ),
            0,
            page_count,
            pages_with_text,
            extraction_required,
            summary_provider,
            summary_model,
            refresh_text=data.refreshText,
            regenerate=force_regenerate,
        )
        _summary_log(
            "queued",
            clean_title,
            group_value,
            pages=page_count,
            missing_pages=missing_page_count,
            cached_pages=pages_with_text,
            regenerate=force_regenerate,
            refresh_text=data.refreshText,
            provider=summary_provider,
            model=summary_model,
        )
        if summary_scheduler is not None:
            summary_scheduler.wake()
        else:
            pause_event = _summary_controller.get_pause_event(group_value)
            pause_event.set()
            summary_task = asyncio.create_task(
                _run_summary_task(
                    request, data, store, group_value, clean_title, worker=None, pause_event=pause_event
                ),
                name=f"summary-{group_value}",
            )
            _summary_controller.register_task(group_value, data, summary_task)
        return await _summary_status_for(store, group_value, clean_title, pages)


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


async def _run_summary_task(
    request: Request | None,
    data: MangaSummaryRequest,
    store: PostgresStore | None,
    group_value: str,
    clean_title: str,
    worker=None,
    pause_event: asyncio.Event | None = None,
) -> None:
    current_task = asyncio.current_task()
    try:
        await _generate_manga_summary(request, data, worker, pause_event=pause_event)
    except asyncio.CancelledError:
        _summary_log(
            "cancelled",
            clean_title,
            group_value,
            level=logging.INFO,
            stage="job",
            error="Summary task was cancelled",
        )
        raise
    except HTTPException as exc:
        _summary_log(
            "failed",
            clean_title,
            group_value,
            level=logging.WARNING,
            stage="job",
            status_code=exc.status_code,
            error=str(exc.detail),
        )
        if exc.status_code in {400, 404}:
            await _update_summary_job_for(
                store,
                group_value,
                clean_title,
                "error",
                str(exc.detail),
                "job",
                0,
                str(exc.detail),
            )
        return
    except Exception as exc:
        _summary_log(
            "failed",
            clean_title,
            group_value,
            level=logging.ERROR,
            exc_info=True,
            stage="job",
            error=str(exc),
        )
        await _update_summary_job_for(
            store,
            group_value,
            clean_title,
            "error",
            str(exc),
            "summarizing",
            0,
            str(exc),
        )
    finally:
        _summary_controller.unregister_task(group_value, current_task)
        _summary_controller.unregister_task(clean_title, current_task)
        await _reclaim_summary_memory(worker)


async def _generate_manga_summary(
    request: Request,
    data: MangaSummaryRequest,
    worker=None,
    pause_event: asyncio.Event | None = None,
):
    group_value = (data.groupId or data.mangaTitle or "").strip()
    if not group_value:
        raise HTTPException(400, detail="groupId is required")
    clean_title = (data.mangaTitle or "Ungrouped").strip() or "Ungrouped"
    # Worker availability, not a global lock, controls summary concurrency.
    async with nullcontext():
        store = _postgres()
        if store is not None:
            resolved_group = await store.resolve_group_id(group_value)
            if resolved_group is None and data.mangaTitle:
                resolved_group = await store.resolve_group_id(data.mangaTitle.strip())
            group_value = resolved_group or group_value
            clean_title = await store.resolve_group_title(group_value) or clean_title
            pages = await store.group_pages(group_value)
        else:
            pages = await asyncio.to_thread(group_pages, RESULT_ROOT, clean_title)
        if not pages:
            raise HTTPException(404, detail="Manga group not found")

        force_regenerate = data.regenerate or data.refreshText
        status = await _summary_status_for(store, group_value, clean_title, pages)
        if status["jobStatus"] == "generating" and not force_regenerate:
            existing_task = _summary_controller.get_task(group_value) or _summary_controller.get_task(clean_title)
            current_task = asyncio.current_task()
            if existing_task is not None and existing_task is not current_task and not existing_task.done():
                _summary_log(
                    "skipped",
                    clean_title,
                    group_value,
                    reason="already_generating",
                    stage=status.get("jobStage"),
                    progress=status.get("jobProgress"),
                )
                return status
        if status["summary"] and not status["stale"] and not force_regenerate:
            if status.get("jobStatus") != "ready":
                await _update_summary_job_for(
                    store,
                    group_value,
                    clean_title,
                    "ready",
                    stage="complete",
                    progress=100,
                    message="Summary ready",
                )
                status["jobStatus"] = "ready"
                status["jobStage"] = "complete"
                status["jobProgress"] = 100
                status["jobMessage"] = "Summary ready"
            _summary_log(
                "skipped",
                clean_title,
                group_value,
                reason="already_ready",
                summary_available=True,
            )
            return status

        page_count = len(pages)
        try:
            summary_provider, summary_model, _, _ = resolve_summary_model(data.summaryModel)
        except ValueError as exc:
            _summary_log(
                "rejected",
                clean_title,
                group_value,
                level=logging.WARNING,
                reason="invalid_model",
                error=str(exc),
            )
            raise HTTPException(400, detail=str(exc)) from exc
        has_group_text = any(bool(read_page_text(page)) for page in pages)
        missing_page_count = (
            page_count
            if data.refreshText
            else sum(not is_page_text_extracted(page, has_group_text=has_group_text) for page in pages)
        )
        extraction_required = missing_page_count > 0
        pages_with_text = (
            sum(bool(read_page_text(page)) for page in pages)
            if not extraction_required
            else 0
        )
        initial_stage = "detecting" if extraction_required else "concatenating"
        if data.refreshText:
            initial_message = f"Re-reading text · all {page_count} pages will be replaced"
        elif extraction_required:
            initial_message = (
                f"Detecting text · {missing_page_count} pages need extraction; "
                f"{pages_with_text} cached pages reused"
            )
        else:
            initial_message = f"Reusing text from all {page_count} cached pages"
        await _update_summary_job_for(
            store,
            group_value,
            clean_title,
            "generating",
            None,
            initial_stage,
            0 if extraction_required else 70,
            initial_message,
            0,
            page_count,
            pages_with_text,
            extraction_required,
            summary_provider,
            summary_model,
        )
        target_language = _summary_target_language(pages)
        summary_started_at = time.perf_counter()
        _summary_log(
            "started",
            clean_title,
            group_value,
            pages=page_count,
            missing_pages=missing_page_count,
            cached_pages=pages_with_text,
            regenerate=force_regenerate,
            refresh_text=data.refreshText,
            provider=summary_provider,
            model=summary_model,
            language=target_language,
        )
        failed_pages = []
        ocr_errors = {}

        if extraction_required:
            async def _execute_ocr(active_worker):
                nonlocal pages_with_text
                try:
                    async with _summary_ocr_semaphore:
                        for index, page in enumerate(pages):
                            current_page = index + 1
                            needs_ocr = data.refreshText or not is_page_text_extracted(page, has_group_text=has_group_text)
                            if not needs_ocr:
                                continue

                            if pause_event is not None and not pause_event.is_set():
                                _summary_log(
                                    "paused",
                                    clean_title,
                                    group_value,
                                    page=f"{current_page}/{page_count}",
                                )
                                await pause_event.wait()
                                _summary_log(
                                    "resumed",
                                    clean_title,
                                    group_value,
                                    page=f"{current_page}/{page_count}",
                                )

                            async def report_stage(pipeline_stage: str, *, page_number: int = current_page) -> None:
                                stage_info = {
                                    "detection": ("detecting", 0, "Detecting text"),
                                    "ocr": ("ocr", 1 / 3, "Reading OCR"),
                                    "textline_merge": ("textline_merge", 2 / 3, "Merging text lines"),
                                }.get(pipeline_stage)
                                if stage_info is None:
                                    return
                                stage, offset, label = stage_info
                                progress = round(((page_number - 1 + offset) / max(1, page_count)) * 70)
                                await _update_summary_job_for(
                                    store,
                                    group_value,
                                    clean_title,
                                    "generating",
                                    None,
                                    stage,
                                    progress,
                                    f"{label} · page {page_number} of {page_count}",
                                    page_number,
                                    page_count,
                                    pages_with_text,
                                    extraction_required,
                                )

                            try:
                                ocr_started_at = time.perf_counter()
                                _summary_log(
                                    "ocr_started",
                                    clean_title,
                                    group_value,
                                    page=f"{current_page}/{page_count}",
                                    file=page["name"],
                                )
                                regions = await _run_summary_ocr(
                                    request,
                                    page,
                                    target_language,
                                    report_stage,
                                    active_worker,
                                    overwrite=data.refreshText,
                                )
                                page["textRegions"] = regions
                                if regions:
                                    pages_with_text += 1
                                _summary_log(
                                    "ocr_completed",
                                    clean_title,
                                    group_value,
                                    page=f"{current_page}/{page_count}",
                                    file=page["name"],
                                    regions=len(regions),
                                    elapsed_ms=round((time.perf_counter() - ocr_started_at) * 1000, 1),
                                )
                            except Exception as exc:
                                _summary_log(
                                    "ocr_failed",
                                    clean_title,
                                    group_value,
                                    level=logging.WARNING,
                                    exc_info=True,
                                    page=f"{current_page}/{page_count}",
                                    file=page["name"],
                                    elapsed_ms=round((time.perf_counter() - ocr_started_at) * 1000, 1),
                                    error=str(exc),
                                )
                                failed_pages.append(page["name"])
                                ocr_errors[page["name"]] = str(exc)

                            await _update_summary_job_for(
                                store,
                                group_value,
                                clean_title,
                                "generating",
                                None,
                                "textline_merge",
                                round(current_page / max(1, page_count) * 70),
                                f"Fetched text · page {current_page} of {page_count}",
                                current_page,
                                page_count,
                                pages_with_text,
                                extraction_required,
                            )
                finally:
                    # Reclaim memory on the active worker and clear accelerator cache
                    reclaim = getattr(active_worker, "reclaim_memory", None)
                    if reclaim is not None:
                        try:
                            await reclaim()
                        except Exception:
                            pass
                    empty_device_cache()
                    gc.collect()

            if worker is not None:
                await _execute_ocr(worker)
            else:
                ocr_task = SummaryQueueElement(_execute_ocr)
                _summary_controller.register_ocr_task(group_value, ocr_task)
                task_queue.add_task(ocr_task)
                try:
                    await wait_in_queue(ocr_task, None)
                finally:
                    _summary_controller.unregister_ocr_task(group_value)
        else:
            await _update_summary_job_for(
                store,
                group_value,
                clean_title,
                "generating",
                None,
                "concatenating",
                70,
                f"Reused cached text · all {page_count} pages",
                page_count,
                page_count,
                pages_with_text,
                extraction_required,
            )

        if pause_event is not None and not pause_event.is_set():
            _summary_log(
                "paused",
                clean_title,
                group_value,
                stage="summarizing",
            )
            await pause_event.wait()
            _summary_log(
                "resumed",
                clean_title,
                group_value,
                stage="summarizing",
            )

        await _update_summary_job_for(
            store,
            group_value,
            clean_title,
            "generating",
            None,
            "concatenating",
            72,
            f"Combining text from {pages_with_text} pages",
            page_count,
            page_count,
            pages_with_text,
            extraction_required,
            summary_provider,
            summary_model,
        )
        snapshot = await asyncio.to_thread(source_snapshot, pages)
        for entry in snapshot["missing"]:
            if entry["name"] not in failed_pages:
                failed_pages.append(entry["name"])
                ocr_errors[entry["name"]] = "No text detected"
        _summary_log(
            "text_collected",
            clean_title,
            group_value,
            pages_with_text=sum(bool(entry["texts"]) for entry in snapshot["entries"]),
            pages=page_count,
            missing_pages=len(snapshot["missing"]),
            failed_pages=len(failed_pages),
            transcript_chars=sum(len(text) for text in snapshot["texts"]),
        )
        if not snapshot["texts"]:
            detail = "No original text could be recovered"
            if ocr_errors:
                error_items = [f"{page_name} ({err})" if err != "No text detected" else page_name for page_name, err in ocr_errors.items()]
                if len(error_items) > 8:
                    summary_failed = f"{', '.join(error_items[:8])} (and {len(error_items) - 8} more)"
                else:
                    summary_failed = ", ".join(error_items)
                detail += f"; OCR failed for: {summary_failed}"
            elif failed_pages:
                if len(failed_pages) > 8:
                    summary_failed = f"{', '.join(failed_pages[:8])} (and {len(failed_pages) - 8} more)"
                else:
                    summary_failed = ", ".join(failed_pages)
                detail += f"; OCR failed for: {summary_failed}"
            _summary_log(
                "failed",
                clean_title,
                group_value,
                level=logging.ERROR,
                stage="text_collection",
                duration_ms=round((time.perf_counter() - summary_started_at) * 1000, 1),
                error=detail,
            )
            await _update_summary_job_for(
                store,
                group_value,
                clean_title,
                "error",
                detail,
                "textline_merge" if extraction_required else "concatenating",
                70,
                detail,
                page_count,
                page_count,
                pages_with_text,
                extraction_required,
            )
            raise HTTPException(422, detail=detail)

        transcript = await asyncio.to_thread(transcript_pages, snapshot)
        await _update_summary_job_for(
            store,
            group_value,
            clean_title,
            "generating",
            None,
            "summarizing",
            85,
            f"Generating the manga synopsis with {summary_model}",
            page_count,
            page_count,
            pages_with_text,
            extraction_required,
        )
        provider_started_at = time.perf_counter()
        _summary_log(
            "summarization_started",
            clean_title,
            group_value,
            provider=summary_provider,
            model=summary_model,
            language=target_language,
            transcript_pages=len(transcript),
            transcript_chars=sum(len(text) for text in snapshot["texts"]),
        )
        try:
            summary = await generate_synopsis(
                transcript,
                target_language,
                _deepseek_token_count_factory(),
                data.summaryModel,
            )
            _summary_log(
                "summarization_completed",
                clean_title,
                group_value,
                provider=summary_provider,
                model=summary_model,
                summary_chars=len(summary),
                elapsed_ms=round((time.perf_counter() - provider_started_at) * 1000, 1),
            )
        except HTTPException as exc:
            _summary_log(
                "failed",
                clean_title,
                group_value,
                level=logging.WARNING,
                stage="summarizing",
                provider=summary_provider,
                model=summary_model,
                duration_ms=round((time.perf_counter() - summary_started_at) * 1000, 1),
                error=str(exc.detail),
            )
            await _update_summary_job_for(
                store,
                group_value,
                clean_title,
                "error",
                str(exc.detail),
                "summarizing",
                85,
                str(exc.detail),
                page_count,
                page_count,
                pages_with_text,
                extraction_required,
            )
            raise
        except Exception as exc:
            error_detail = summary_error_details(exc)
            _summary_log(
                "failed",
                clean_title,
                group_value,
                level=logging.ERROR,
                exc_info=True,
                stage="summarizing",
                provider=summary_provider,
                model=summary_model,
                duration_ms=round((time.perf_counter() - summary_started_at) * 1000, 1),
                error_type=type(exc).__name__,
                error=error_detail,
            )
            await _update_summary_job_for(
                store,
                group_value,
                clean_title,
                "error",
                str(exc),
                "summarizing",
                85,
                str(exc),
                page_count,
                page_count,
                pages_with_text,
                extraction_required,
            )
            raise HTTPException(502, detail=f"{summary_provider.title()} synopsis generation failed: {exc}") from exc

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record = {
            "groupId": group_value if store is not None else None,
            "mangaTitle": clean_title,
            "summary": summary,
            "language": target_language,
            "generatedAt": now,
            "sourceFingerprint": snapshot["fingerprint"],
            "pageCount": len(pages),
            "textPageCount": sum(bool(entry["texts"]) for entry in snapshot["entries"]),
            "skippedPages": failed_pages,
            "ocrErrors": ocr_errors,
            "provider": summary_provider,
            "model": summary_model,
            "jobStatus": "ready",
            "jobError": None,
            "jobUpdatedAt": now,
            "jobStage": "complete",
            "jobProgress": 100,
            "jobMessage": "Summary ready",
            "jobCurrentPage": page_count,
            "jobPageCount": page_count,
            "jobPagesWithText": pages_with_text,
            "jobExtractionRequired": extraction_required,
            "jobDismissed": False,
        }
        if store is not None:
            await store.save_summary_payload(group_value, record)
            await asyncio.to_thread(save_summary, RESULT_ROOT, clean_title, record)
        else:
            await asyncio.to_thread(save_summary, RESULT_ROOT, clean_title, record)
        _summary_log(
            "completed",
            clean_title,
            group_value,
            provider=summary_provider,
            model=summary_model,
            pages=page_count,
            pages_with_text=pages_with_text,
            skipped_pages=len(failed_pages),
            summary_chars=len(summary),
            duration_ms=round((time.perf_counter() - summary_started_at) * 1000, 1),
        )
        empty_device_cache()
        gc.collect()
        return await _summary_status_for(store, group_value, clean_title, pages)

@app.get("/results/list", tags=["api"])
@app.get("/api/results/list", tags=["api"])
async def list_results(
    sort: str = "alpha",
    manga: Optional[str] = Query(None, max_length=MAX_MANGA_TITLE_LENGTH),
    detail: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    groupId: Optional[str] = None,
    review: Optional[str] = Query(None, pattern="^pending$"),
):
    """List result directories with metadata, optionally filtered by manga title and slim reader detail mode"""
    store = _postgres()
    group_value = groupId or manga
    if store is not None:
        try:
            return await store.list_results(sort, group_value, detail, limit, offset, review)
        except Exception as error:
            raise HTTPException(503, detail=f"PostgreSQL result store unavailable: {error}") from error

    result_dir = RESULT_ROOT
    if not result_dir.exists():
        return {"directories": [], "items": [], "total": 0, "nextOffset": None}
    
    try:
        return await asyncio.to_thread(_scan_results, result_dir, sort, group_value, detail, limit, offset, review)
    except Exception as e:
        raise HTTPException(500, detail=f"Error listing results: {str(e)}")


@app.get("/api/manga/{manga_id}/pages", tags=["api"])
async def list_manga_pages(
    manga_id: str,
    sort: str = "alpha",
    detail: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    review: Optional[str] = Query(None, pattern="^pending$"),
):
    """Canonical page-list adapter; legacy result routes remain supported."""
    return await list_results(sort=sort, groupId=manga_id, detail=detail, limit=limit, offset=offset, review=review)


@app.put("/manga/{manga_id}/pages/order", tags=["api"])
@app.put("/api/manga/{manga_id}/pages/order", tags=["api"])
async def reorder_manga_pages(manga_id: str, data: ReorderPagesRequest):
    try:
        store = _postgres()
        if store is not None:
            pages = await store.reorder_pages(manga_id, data.pageIds)
        else:
            pages = await asyncio.to_thread(
                _reorder_file_backed_pages, RESULT_ROOT, manga_id, data.pageIds
            )
    except GroupNotFound as error:
        raise HTTPException(404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(400, detail=str(error)) from error
    return {"groupId": manga_id, "pages": pages}

@app.post("/results/update-meta", tags=["api"])
@app.post("/api/results/update-meta", tags=["api"])
@app.patch("/api/manga", tags=["api"])
async def update_meta(data: UpdateMetaRequest):
    """Update metadata (e.g. mangaTitle) for specified result folders or by old mangaTitle"""
    clean_title = (data.mangaTitle or "").strip() or "Ungrouped"
    store = _postgres()
    if store is not None:
        try:
            if data.groupId:
                old_title, updated_count = await store.rename_group(data.groupId, clean_title)
            else:
                old_title = data.oldMangaTitle.strip() if data.oldMangaTitle else None
                updated_count = await store.update_meta(data.pageIds or data.folders, old_title, clean_title)
        except GroupNotFound as error:
            raise HTTPException(404, detail=str(error)) from error
        except GroupConflict as error:
            raise HTTPException(409, detail=str(error)) from error
    else:
        old_title = data.oldMangaTitle.strip() if data.oldMangaTitle else None
        try:
            old_title, updated_count = await asyncio.to_thread(
                _update_file_backed_meta,
                RESULT_ROOT,
                data.folders,
                old_title,
                clean_title,
                data.groupId,
            )
        except GroupNotFound as error:
            raise HTTPException(404, detail=str(error)) from error
    if store is None and old_title and old_title != clean_title and updated_count:
        await asyncio.to_thread(rename_summary, RESULT_ROOT, old_title, clean_title)
    return {"updated": updated_count, "mangaTitle": clean_title}

@app.delete("/results/group", tags=["api"])
@app.delete("/api/results/group", tags=["api"])
@app.delete("/api/manga/{title}", tags=["api"])
async def delete_manga_group_endpoint(title: str):
    """Delete all results belonging to a specific manga title"""
    store = _postgres()
    if store is not None:
        clean_title = (title or "").strip() or "Ungrouped"
        clean_title = await store.resolve_group_title(clean_title) or clean_title
        folders = await store.delete_group(clean_title)
        for folder in folders:
            await asyncio.to_thread(shutil.rmtree, RESULT_ROOT / folder, True)
            _invalidate_meta_cache(folder)
        return {
            "message": f"Deleted {len(folders)} pages for manga '{clean_title}'",
            "deleted": len(folders),
        }

    result_dir = RESULT_ROOT
    if not result_dir.exists():
        return {"message": "No results directory found", "deleted": 0}

    clean_title = (title or "").strip() or "Ungrouped"
    deleted_count = 0
    try:
        for item_path in result_dir.iterdir():
            if item_path.is_dir() and final_file(item_path) is not None:
                meta_file = item_path / "meta.json"
                item_manga = "Ungrouped"
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        item_manga = (meta.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
                    except Exception:
                        pass
                if item_manga == clean_title:
                    shutil.rmtree(item_path)
                    _invalidate_meta_cache(item_path.name)
                    deleted_count += 1
        await asyncio.to_thread(remove_summary, RESULT_ROOT, clean_title)
        return {"message": f"Deleted {deleted_count} pages for manga '{clean_title}'", "deleted": deleted_count}
    except Exception as e:
        raise HTTPException(500, detail=f"Error deleting manga group: {str(e)}")


def _build_cbz_archive(tmp_path: str, pages: list, safe_title: str):
    """Worker function to build CBZ archive on disk synchronously in a worker thread"""
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
        comic_info = f"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <Title>{safe_title}</Title>
  <PageCount>{len(pages)}</PageCount>
</ComicInfo>"""
        zf.writestr("ComicInfo.xml", comic_info)

        for idx, page in enumerate(pages):
            orig_name = page["originalName"]
            file_path = page["path"]
            ext = os.path.splitext(orig_name)[1] or ".png"
            base = os.path.splitext(orig_name)[0]
            clean_base = re.sub(r'[\\/*?:"<>|]', '_', base)
            arcname = f"{idx+1:03d}_{clean_base}{ext}"
            zf.write(file_path, arcname=arcname)

async def create_cbz_stream(pages: list, manga_title: str):
    """Generates a zip archive (CBZ) containing pages sorted naturally with ComicInfo.xml using FileResponse"""
    pages.sort(
        key=lambda p: (
            p.get("pageOrder") is None,
            p.get("pageOrder") or 0,
            natural_keys(p["originalName"]),
            p["path"],
        )
    )
    safe_title = (manga_title or "Manga").strip() or "Manga"

    tmp_file = tempfile.NamedTemporaryFile(suffix=".cbz", delete=False)
    tmp_path = tmp_file.name
    tmp_file.close()

    try:
        await asyncio.to_thread(_build_cbz_archive, tmp_path, pages, safe_title)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise

    safe_filename = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5\u3040-\u30ff\uac00-\ud7af\.\-]', '_', safe_title)
    if not safe_filename:
        safe_filename = "manga"
    cbz_filename = f"{safe_filename}.cbz"

    def cleanup(path: str):
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass

    return FileResponse(
        tmp_path,
        media_type="application/vnd.comicbook+zip",
        filename=cbz_filename,
        background=BackgroundTask(cleanup, tmp_path),
        headers={
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
    )

@app.post("/results/export/cbz", tags=["api"])
@app.post("/api/results/export/cbz", tags=["api"])
async def export_cbz_post(data: ExportCbzRequest):
    """Export translated manga pages as a .cbz comic archive (POST)"""
    store = _postgres()
    if store is not None:
        group_value = (data.groupId or data.mangaTitle or "").strip()
        if data.groupId and data.mangaTitle and await store.resolve_group_id(group_value) is None:
            group_value = data.mangaTitle.strip()
        manga_title = await store.resolve_group_title(group_value) or group_value or "Manga"
        pages = await store.export_pages(group_value, data.folders)
        if not pages:
            raise HTTPException(404, detail="No manga pages found to export")
        return await create_cbz_stream(pages, manga_title)

    result_dir = RESULT_ROOT
    if not result_dir.exists():
        raise HTTPException(404, detail="Result directory not found")

    manga_title = (data.mangaTitle or "Manga").strip()
    pages = []
    target_folders = set(data.folders) if data.folders else None

    for item_path in result_dir.iterdir():
        if item_path.is_dir():
            if final_file(item_path) is not None:
                folder_name = item_path.name
                if target_folders is not None and folder_name not in target_folders:
                    continue

                meta_file = item_path / "meta.json"
                meta = {}
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    except Exception:
                        pass

                item_manga = meta.get("mangaTitle") or "Ungrouped"
                if target_folders is None and item_manga != manga_title:
                    continue

                orig_name = meta.get("originalName")
                if not orig_name or orig_name == "Unknown":
                    orig_name = f"{folder_name}.png"
                page_path = final_file(item_path)
                if _source_type(meta) == "original":
                    page_path = _input_file(item_path) or page_path
                pages.append({
                    "originalName": orig_name,
                    "pageOrder": meta_page_order(meta),
                    "path": str(page_path)
                })

    if not pages:
        raise HTTPException(404, detail="No manga pages found to export")

    return await create_cbz_stream(pages, manga_title)

@app.get("/results/export/cbz", tags=["api"])
@app.get("/api/results/export/cbz", tags=["api"])
async def export_cbz_get(
    manga: Optional[str] = Query(None, max_length=MAX_MANGA_TITLE_LENGTH),
    groupId: Optional[str] = None,
    folders: Optional[str] = None,
):
    """Export translated manga pages as a .cbz comic archive (GET)"""
    folder_list = [f.strip() for f in folders.split(",") if f.strip()] if isinstance(folders, str) and folders.strip() else None
    manga_title = manga if isinstance(manga, str) and manga.strip() else "Manga"
    return await export_cbz_post(
        ExportCbzRequest(groupId=groupId, mangaTitle=manga_title, folders=folder_list)
    )

@app.delete("/results/clear", tags=["api"])
@app.delete("/api/results/clear", tags=["api"])
async def clear_results():
    """Delete all result directories"""
    store = _postgres()
    if store is not None:
        deleted_count = await store.clear_results()
        await asyncio.to_thread(shutil.rmtree, RESULT_ROOT / ".summaries", True)
        _invalidate_meta_cache()
        return {"message": f"Deleted {deleted_count} result directories"}

    result_dir = RESULT_ROOT
    if not result_dir.exists():
        return {"message": "No results directory found"}
    
    try:
        deleted_count = 0
        for item_path in result_dir.iterdir():
            if item_path.is_dir():
                if final_file(item_path) is not None:
                    shutil.rmtree(item_path)
                    deleted_count += 1

        await asyncio.to_thread(shutil.rmtree, result_dir / ".summaries", ignore_errors=True)
        
        _invalidate_meta_cache()
        return {"message": f"Deleted {deleted_count} result directories"}
    except Exception as e:
        raise HTTPException(500, detail=f"Error clearing results: {str(e)}")

@app.get("/results/{folder_name}", tags=["api"])
@app.get("/api/results/{folder_name}", tags=["api"])
@app.get("/api/pages/{folder_name}", tags=["api"])
async def get_result_detail(folder_name: str):
    """Get full metadata for a specific result folder to restore viewer/editor deep links"""
    store = _postgres()
    if store is not None:
        item = await store.page_detail(folder_name)
        if item is None:
            raise HTTPException(404, detail="Result directory not found")
        return item

    result_dir = RESULT_ROOT.resolve()
    folder_path = result_dir / folder_name
    if not folder_path.exists() or not folder_path.is_dir():
        raise HTTPException(404, detail="Result directory not found")

    final_path = final_file(folder_path)
    if final_path is None:
        raise HTTPException(404, detail="Result file not found")

    meta = _get_cached_meta(folder_path)
    original_name = meta.get("originalName")
    if not original_name or original_name == "Unknown":
        original_name = f"{folder_name}.png"
    manga_title = (meta.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
    finished_at = meta.get("finishedAt")
    if not finished_at:
        finished_at = datetime.datetime.fromtimestamp(
            folder_path.stat().st_mtime, datetime.timezone.utc
        ).isoformat()

    input_file = _input_file(folder_path)
    source_type = _source_type(meta)
    await asyncio.to_thread(generate_image_variants, folder_path)
    urls = _image_urls(folder_name, asset_version(folder_path), input_file)
    has_inpainted = find_asset(folder_path, "inpainted") is not None
    has_regions = (folder_path / "text_regions.json").exists()
    has_bubble_mask = (folder_path / "bubble_mask.png").is_file()
    review_status = _review_status(meta, folder_path)

    return {
        "id": meta.get("id") or folder_name,
        "groupId": _manga_id(manga_title),
        "folder": folder_name,
        "originalName": original_name,
        "pageOrder": meta_page_order(meta),
        "sourcePath": meta.get("sourcePath"),
        "mangaTitle": manga_title,
        **urls,
        "inpaintedUrl": f"/result/{folder_name}/inpainted.jpg" if has_inpainted else None,
        "textRegionsUrl": f"/result/{folder_name}/text_regions.json" if has_regions else None,
        "bubbleMaskUrl": f"/result/{folder_name}/bubble_mask.png" if has_bubble_mask else None,
        "hasTextRegions": has_regions,
        "sourceType": source_type,
        "finishedAt": finished_at,
        "settings": meta.get("settings", {}),
        "reviewStatus": review_status,
        "reviewedAt": meta.get("reviewedAt"),
        "needsReview": review_status == "pending",
    }

@app.delete("/results/{folder_name}", tags=["api"])
@app.delete("/api/results/{folder_name}", tags=["api"])
@app.delete("/api/pages/{folder_name}", tags=["api"])
async def delete_result(folder_name: str):
    """Delete a specific result directory"""
    store = _postgres()
    if store is not None:
        resolved_folder = await store.resolve_folder(folder_name)
        if resolved_folder is None:
            raise HTTPException(404, detail="Result file not found")
        folder_name = resolved_folder
        folder_path = RESULT_ROOT / folder_name
        if not folder_path.is_dir() or final_file(folder_path) is None:
            raise HTTPException(404, detail="Result file not found")
        await store.delete_result(folder_name)
        await asyncio.to_thread(shutil.rmtree, folder_path, True)
        _invalidate_meta_cache(folder_name)
        return {"message": f"Deleted result directory: {folder_name}"}

    result_dir = RESULT_ROOT.resolve()
    folder_path = result_dir / folder_name
    
    if not folder_path.exists():
        raise HTTPException(404, detail="Result directory not found")
    
    try:
        if final_file(folder_path) is None:
            raise HTTPException(404, detail="Result file not found")
        metadata = dict(_get_cached_meta(folder_path))
        manga_title = (metadata.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
        shutil.rmtree(folder_path)
        _invalidate_meta_cache(folder_name)
        _compact_file_backed_group(result_dir, manga_title)
        return {"message": f"Deleted result directory: {folder_name}"}
    except Exception as e:
        raise HTTPException(500, detail=f"Error deleting result: {str(e)}")

# Keep API routes above the static mount so POST /result/.../save_edits is reachable.
if RESULT_ROOT.exists():
    app.mount("/result", StaticFiles(directory=str(RESULT_ROOT)), name="result")

#todo: restart if crash
#todo: cache results
#todo: cleanup cache

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
