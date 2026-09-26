import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from server.api.schemas.batches import (
    RetryBatchItemRequest,
    UpdateBatchItemRequest,
    UpdateBatchRequest,
)
from server.batch_store import BatchConflict, BatchNotFound, InvalidBatch


def batch_http_error(error: Exception) -> HTTPException:
    if isinstance(error, BatchNotFound):
        return HTTPException(404, detail="Batch not found")
    if isinstance(error, BatchConflict):
        return HTTPException(409, detail=str(error))
    if isinstance(error, InvalidBatch):
        return HTTPException(400, detail=str(error))
    return HTTPException(500, detail=str(error))


def create_batch_router(
    get_store: Callable[[], Any],
    get_scheduler: Callable[[], Any],
    *,
    max_items: int,
    max_item_bytes: int,
    max_upload_bytes: int,
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.get("/batches", tags=["api", "batches"])
    @router.get("/api/batches", tags=["api", "batches"])
    async def list_batches():
        try:
            return await get_store().list_batch_summaries()
        except Exception as error:
            raise batch_http_error(error) from error

    async def _batch_events():
        last_snapshot = None
        previous_batches = {}
        has_previous_snapshot = False
        while True:
            store = get_store()
            batches = await store.list_batch_summaries()
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
                            details = await get_store().get_batch(batch["id"])
                        except BatchNotFound:
                            continue
                        yield f"event: batch_details\ndata: {json.dumps(details)}\n\n"
                previous_batches = current_batches
                has_previous_snapshot = True
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    @router.get("/batches/events", tags=["api", "batches"])
    @router.get("/api/batches/events", tags=["api", "batches"])
    async def batch_events():
        return StreamingResponse(
            _batch_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/batches/{batch_id}", tags=["api", "batches"])
    @router.get("/api/batches/{batch_id}", tags=["api", "batches"])
    async def get_batch(batch_id: str):
        try:
            return await get_store().get_batch(batch_id)
        except Exception as error:
            raise batch_http_error(error) from error

    @router.put("/batches/{batch_id}", tags=["api", "batches"])
    @router.put("/api/batches/{batch_id}", tags=["api", "batches"])
    async def put_batch(batch_id: str, request: Request):
        """Accept a complete batch and expose it only after all inputs are written."""
        manifest: dict
        files: dict[str, tuple[str, bytes]] = {}
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/"):
            form = await request.form(
                max_files=max_items,
                max_fields=max_items,
                max_part_size=max_item_bytes,
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
            if len(expected_ids) > max_items:
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
                    if item_size > max_item_bytes:
                        raise HTTPException(413, detail="Batch item is too large")
                    total_upload_bytes += item_size
                    if total_upload_bytes > max_upload_bytes:
                        raise HTTPException(413, detail="Batch upload is too large")
                    files[item_id] = (upload.filename or item_id, upload)
                else:
                    content = await upload.read(max_item_bytes + 1)
                    if len(content) > max_item_bytes:
                        raise HTTPException(413, detail="Batch item is too large")
                    total_upload_bytes += len(content)
                    if total_upload_bytes > max_upload_bytes:
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
            result = await get_store().put_batch(batch_id, manifest, files)
            get_scheduler().wake()
            return result
        except Exception as error:
            raise batch_http_error(error) from error
        finally:
            if content_type.startswith("multipart/"):
                await asyncio.gather(
                    *(upload.close() for _, upload in uploads if hasattr(upload, "close")),
                    return_exceptions=True,
                )

    @router.get("/batches/{batch_id}/items/{item_id}/input", tags=["api", "batches", "file"])
    @router.get("/api/batches/{batch_id}/items/{item_id}/input", tags=["api", "batches", "file"])
    async def get_batch_input(batch_id: str, item_id: str):
        try:
            path = await get_store().input_path(batch_id, item_id)
        except Exception as error:
            raise batch_http_error(error) from error
        media_type = "image/png"
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            media_type = "image/jpeg"
        elif path.suffix.lower() == ".webp":
            media_type = "image/webp"
        return FileResponse(path, media_type=media_type, headers={"Content-Disposition": f"inline; filename={path.name}"})

    @router.post("/batches/{batch_id}/pause", tags=["api", "batches"])
    @router.post("/api/batches/{batch_id}/pause", tags=["api", "batches"])
    async def pause_batch(batch_id: str):
        try:
            return await get_scheduler().pause(batch_id)
        except Exception as error:
            raise batch_http_error(error) from error

    @router.post("/batches/{batch_id}/resume", tags=["api", "batches"])
    @router.post("/api/batches/{batch_id}/resume", tags=["api", "batches"])
    async def resume_batch(batch_id: str):
        try:
            return await get_scheduler().resume(batch_id)
        except Exception as error:
            raise batch_http_error(error) from error

    @router.post("/batches/{batch_id}/dismiss", tags=["api", "batches"])
    @router.post("/api/batches/{batch_id}/dismiss", tags=["api", "batches"])
    async def dismiss_batch(batch_id: str):
        try:
            return await get_scheduler().dismiss(batch_id)
        except Exception as error:
            raise batch_http_error(error) from error

    @router.delete("/batches/{batch_id}", tags=["api", "batches"])
    @router.delete("/api/batches/{batch_id}", tags=["api", "batches"])
    async def delete_batch(batch_id: str):
        try:
            await get_scheduler().remove_batch(batch_id)
        except Exception as error:
            raise batch_http_error(error) from error
        return {"status": "deleted", "id": batch_id}

    @router.patch("/batches/{batch_id}", tags=["api", "batches"])
    @router.patch("/api/batches/{batch_id}", tags=["api", "batches"])
    async def update_batch(batch_id: str, data: UpdateBatchRequest):
        try:
            if data.translator is not None:
                from manga_translator.config import Translator

                if data.translator not in {value.value for value in Translator}:
                    raise InvalidBatch("Unknown translator")
                result = await get_scheduler().update_translator(batch_id, data.translator)
            elif data.mangaTitle is not None:
                result = await get_scheduler().update_title(batch_id, data.mangaTitle)
            elif data.priority is not None:
                result = await get_scheduler().update_priority(batch_id, data.priority)
            elif data.keep_failed_pages_for_editing is not None:
                result = await get_scheduler().update_manual_review(
                    batch_id, data.keep_failed_pages_for_editing
                )
            else:
                raise InvalidBatch("Batch patch is empty")
            return result
        except Exception as error:
            raise batch_http_error(error) from error

    @router.post("/batches/{batch_id}/items/{item_id}/retry", tags=["api", "batches"])
    @router.post("/api/batches/{batch_id}/items/{item_id}/retry", tags=["api", "batches"])
    async def retry_batch_item(
        batch_id: str,
        item_id: str,
        data: RetryBatchItemRequest | None = None,
    ):
        try:
            return await get_scheduler().retry_item(
                batch_id,
                item_id,
                data.keep_failed_pages_for_editing if data else None,
                data.from_stage if data else None,
            )
        except Exception as error:
            raise batch_http_error(error) from error

    @router.patch("/batches/{batch_id}/items/{item_id}", tags=["api", "batches"])
    @router.patch("/api/batches/{batch_id}/items/{item_id}", tags=["api", "batches"])
    async def update_batch_item(batch_id: str, item_id: str, data: UpdateBatchItemRequest):
        try:
            return await get_scheduler().update_item(batch_id, item_id, data.excludeColor)
        except Exception as error:
            raise batch_http_error(error) from error

    @router.delete("/batches/{batch_id}/items/{item_id}", tags=["api", "batches"])
    @router.delete("/api/batches/{batch_id}/items/{item_id}", tags=["api", "batches"])
    async def delete_batch_item(batch_id: str, item_id: str):
        try:
            return await get_scheduler().remove_item(batch_id, item_id)
        except Exception as error:
            raise batch_http_error(error) from error

    return router, (
        list_batches,
        _batch_events,
        batch_events,
        get_batch,
        put_batch,
        get_batch_input,
        pause_batch,
        resume_batch,
        dismiss_batch,
        delete_batch,
        update_batch,
        retry_batch_item,
        update_batch_item,
        delete_batch_item,
    )
