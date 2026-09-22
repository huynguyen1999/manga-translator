import asyncio
import builtins
import io
import json
import pickle
import re
from base64 import b64decode
from pathlib import Path
from typing import Awaitable, Callable, Union

import requests
from PIL import Image
from fastapi import Request, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse

from manga_translator import Config
from server.myqueue import task_queue, wait_in_queue, QueueElement, BatchQueueElement
from server.streaming import notify, stream
from server.constants import SERVER_RESULT_ROOT
from server.image_variants import final_file

RESULT_DIR = SERVER_RESULT_ROOT
# ponytail: process-local single-flight; use shared job state if the API runs multiple server processes.
_active_web_requests: dict[str, asyncio.Future[tuple[bool, str]]] = {}
_result_indexer: Callable[[str], Awaitable[object]] | None = None
_request_lookup: Callable[[str], Awaitable[str | None]] | None = None


def set_result_indexer(indexer: Callable[[str], Awaitable[object]] | None) -> None:
    global _result_indexer
    _result_indexer = indexer


def set_request_lookup(lookup: Callable[[str], Awaitable[str | None]] | None) -> None:
    global _request_lookup
    _request_lookup = lookup


async def _find_completed_web_request(request_id: str) -> str | None:
    if _request_lookup is not None:
        return await _request_lookup(request_id)
    return _completed_web_request(request_id)


def _frame(code: int, data: bytes = b"") -> bytes:
    return code.to_bytes(1, "big") + len(data).to_bytes(4, "big") + data


def _completed_web_request(request_id: str) -> str | None:
    if not RESULT_DIR.is_dir():
        return None
    for meta_path in RESULT_DIR.glob("*/meta.json"):
        try:
            with meta_path.open(encoding="utf-8") as meta_file:
                meta = json.load(meta_file)
            if meta.get("requestId") == request_id and final_file(meta_path.parent) is not None:
                return meta_path.parent.name
        except (OSError, UnicodeError, ValueError):
            continue
    return None


def _replay_web_result(folder: str):
    async def replay():
        yield _frame(1, f"final_ready:{folder}".encode("utf-8"))
        yield _frame(0)

    return StreamingResponse(replay(), media_type="application/octet-stream")


async def _replay_web_request(result: asyncio.Future[tuple[bool, str]]):
    try:
        success, value = await result
    except Exception as error:
        yield _frame(2, str(error).encode("utf-8"))
        return
    if success:
        yield _frame(1, f"final_ready:{value}".encode("utf-8"))
        yield _frame(0)
    else:
        yield _frame(2, value.encode("utf-8"))

class TranslateRequest(BaseModel):
    """This request can be a multipart or a json request"""
    image: bytes|str
    """can be a url, base64 encoded image or a multipart image"""
    config: Config = Config()
    """in case it is a multipart this needs to be a string(json.stringify)"""

class BatchTranslateRequest(BaseModel):
    """Batch translation request"""
    images: list[bytes|str]
    """List of images, can be URLs, base64 encoded strings, or binary data"""
    config: Config = Config()
    """Translation configuration"""
    batch_size: int = Field(default=20, ge=1, le=100)
    """Maximum images per translation request."""

async def to_pil_image(image: Union[str, bytes]) -> Image.Image:
    try:
        if isinstance(image, builtins.bytes):
            image = Image.open(io.BytesIO(image))
            return image
        else:
            if re.match(r'^data:image/.+;base64,', image):
                value = image.split(',', 1)[1]
                image_data = b64decode(value)
                image = Image.open(io.BytesIO(image_data))
                return image
            else:
                response = requests.get(image, timeout=(5, 30))
                image = Image.open(io.BytesIO(response.content))
                return image
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


async def get_ctx(
    req: Request,
    config: Config,
    image: str | bytes,
    on_progress: Callable[[str], None] | None = None,
):
    image = await to_pil_image(image)

    task = QueueElement(req, image, config, 0)
    task_queue.add_task(task)

    if on_progress is None:
        return await wait_in_queue(task, None)

    result: object | None = None
    error: str | None = None

    def notify_internal(code: int, data: bytes | None) -> None:
        nonlocal result, error
        if code == 1 and data:
            on_progress(data.decode("utf-8", errors="replace"))
        elif code == 0 and data:
            result = pickle.loads(data)
        elif code == 2:
            error = (data or b"Summary text extraction failed").decode("utf-8", errors="replace")

    await wait_in_queue(task, notify_internal)
    if error:
        raise RuntimeError(error)
    if result is None:
        raise RuntimeError("Summary text extraction ended without a result")
    return result

async def while_streaming(req: Request, transform, config: Config, image: bytes | str):
    request_id = getattr(config, "request_id", None) if getattr(config, "_web_frontend_optimized", False) else None
    if request_id:
        completed_folder = await _find_completed_web_request(request_id)
        if completed_folder:
            return _replay_web_result(completed_folder)

        existing = _active_web_requests.get(request_id)
        if existing:
            return StreamingResponse(_replay_web_request(existing), media_type="application/octet-stream")

        request_result = asyncio.get_running_loop().create_future()
        _active_web_requests[request_id] = request_result

    try:
        image = await to_pil_image(image)
    except Exception as error:
        if request_id:
            request_result.set_result((False, str(error)))
            _active_web_requests.pop(request_id, None)
        raise

    task = QueueElement(req, image, config, 0)
    task_queue.add_task(task)

    messages = asyncio.Queue()

    def notify_internal(code: int, data: bytes) -> None:
        notify(code, data, transform, messages)
        if request_id:
            if code == 1 and data.startswith(b"final_ready:") and not request_result.done():
                folder = data[12:].decode("utf-8")
                if _result_indexer is not None:
                    asyncio.create_task(_result_indexer(folder))
                request_result.set_result((True, folder))
            elif code == 2 and not request_result.done():
                request_result.set_result((False, data.decode("utf-8")))
    worker_task = asyncio.create_task(wait_in_queue(task, notify_internal))
    if request_id:
        def cleanup(_task):
            if _active_web_requests.get(request_id) is request_result:
                if not request_result.done():
                    request_result.set_result((False, "Translation ended before producing a result"))
                del _active_web_requests[request_id]

        worker_task.add_done_callback(cleanup)

    async def stream_with_cleanup():
        try:
            async for chunk in stream(messages):
                yield chunk
        finally:
            if not worker_task.done():
                worker_task.cancel()

    return StreamingResponse(stream_with_cleanup(), media_type="application/octet-stream")

async def get_batch_ctx(req: Request, config: Config, images: list[str|bytes], batch_size: int = 20):
    """Process batch translation request"""
    # Convert images to PIL Image objects
    pil_images = []
    for img in images:
        pil_img = await to_pil_image(img)
        pil_images.append(pil_img)
    
    # Create batch task
    batch_task = BatchQueueElement(req, pil_images, config, batch_size)
    task_queue.add_task(batch_task)
    
    return await wait_in_queue(batch_task, None)
