"""HTTP contract for durably queued original manga imports."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from server.manga_import_jobs import (
    MangaImportJobConflict,
    MangaImportJobNotFound,
)


def public_job(job: dict[str, Any], *, include_items: bool = False) -> dict[str, Any]:
    total = job.get("totalPages")
    processed = int(job.get("processedPages", 0))
    keys = (
        "id", "title", "status", "createdAt", "acceptedAt", "updatedAt", "startedAt", "completedAt",
        "processedPages", "totalPages", "progress", "error", "group", "totalImages", "attempt", "phase", "groupId",
    ) + (("items",) if include_items else ())
    return {key: job[key] for key in keys if key in job} | {
        "progress": job.get("progress", round(100 * processed / total) if total else 0),
        "fileCount": len(job.get("files", [])),
    }


def create_manga_import_job_router(
    get_queue: Callable[[], Any],
    get_max_items: Callable[[], int],
    get_max_item_bytes: Callable[[], int],
    get_max_import_bytes: Callable[[], int],
    get_max_title_length: Callable[[], int],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.post("/api/manga-import-jobs", status_code=202, tags=["api", "gallery"])
    async def create_manga_import_job(request: Request):
        max_items = get_max_items()
        try:
            form = await request.form(
                max_files=max_items,
                max_fields=max_items,
                max_part_size=get_max_item_bytes(),
            )
        except Exception as error:
            raise HTTPException(400, detail=f"Failed to parse upload: {error}") from error

        files = [
            value for _, value in form.multi_items()
            if hasattr(value, "file") and hasattr(value, "filename")
        ]
        try:
            raw_title = form.get("mangaTitle")
            if hasattr(raw_title, "read"):
                raw_title = (await raw_title.read()).decode("utf-8")
            title = str(raw_title or "").strip()
            if not title or title.casefold() == "ungrouped":
                raise HTTPException(400, detail="A manga title is required")
            max_title_length = get_max_title_length()
            if len(title) > max_title_length:
                raise HTTPException(
                    422,
                    detail=f"Manga title must be at most {max_title_length} characters",
                )

            raw_client_id = form.get("clientUploadId")
            if hasattr(raw_client_id, "read"):
                raw_client_id = (await raw_client_id.read()).decode("utf-8")
            client_upload_id = str(raw_client_id or "").strip() or None
            if client_upload_id and (len(client_upload_id) > 128 or "\x00" in client_upload_id):
                raise HTTPException(400, detail="Invalid client upload ID")

            queue = get_queue()
            if queue is None:
                raise HTTPException(503, detail="Manga import queue is not available")
            if client_upload_id:
                existing = await queue.store.get_by_client_upload_id(client_upload_id)
                if existing is not None:
                    return {"job": public_job(existing)}
            if not files:
                raise HTTPException(400, detail="At least one image is required")
            if len(files) > max_items:
                raise HTTPException(413, detail="Manga contains too many pages")

            total_bytes = 0
            file_metadata = []
            for upload in files:
                filename = str(upload.filename or "").strip()
                upload.file.seek(0, 2)
                size = upload.file.tell()
                upload.file.seek(0)
                if size > get_max_item_bytes():
                    raise HTTPException(413, detail=f"Image is too large: {filename}")
                total_bytes += size
                if total_bytes > get_max_import_bytes():
                    raise HTTPException(413, detail="Manga import exceeds 20 GB")
                file_metadata.append({"sourcePath": filename})

            raw_group_id = form.get("mangaGroupId") or form.get("groupId")
            if hasattr(raw_group_id, "read"):
                raw_group_id = (await raw_group_id.read()).decode("utf-8")
            group_id = str(raw_group_id or "").strip() or None

            raw_is_new_group = form.get("isNewGroup")
            if hasattr(raw_is_new_group, "read"):
                raw_is_new_group = (await raw_is_new_group.read()).decode("utf-8")
            is_new_group = (
                str(raw_is_new_group or "").strip().lower() in ("true", "1")
                if raw_is_new_group is not None else None
            )

            raw_page_metadata = form.get("pageMetadata")
            if hasattr(raw_page_metadata, "read"):
                raw_page_metadata = (await raw_page_metadata.read()).decode("utf-8")
            if raw_page_metadata:
                try:
                    entries = json.loads(str(raw_page_metadata))
                    if isinstance(entries, list):
                        file_metadata = [
                            {"sourcePath": str(entry.get("sourcePath") or entry.get("originalName") or "")
                             if isinstance(entry, dict) else ""}
                            for entry in entries
                        ]
                except (TypeError, ValueError) as error:
                    raise HTTPException(400, detail="Invalid page metadata") from error
                file_metadata.extend({"sourcePath": str(upload.filename or "")} for upload in files[len(file_metadata):])

            try:
                job = await queue.accept(
                    {
                        "title": title,
                        "mangaGroupId": group_id,
                        "isNewGroup": is_new_group,
                        "pageMetadata": file_metadata,
                        "files": file_metadata,
                        "fileCount": len(files),
                        "totalBytes": total_bytes,
                        "clientUploadId": client_upload_id,
                    },
                    files,
                )
            except MangaImportJobConflict:
                existing = (
                    await queue.store.get_by_client_upload_id(client_upload_id)
                    if client_upload_id else None
                )
                if existing is None:
                    raise
                job = existing
            return {"job": public_job(job)}
        finally:
            await _close_uploads(files)

    @router.get("/api/manga-import-jobs", tags=["api", "gallery"])
    async def list_manga_import_jobs():
        queue = get_queue()
        if queue is None:
            raise HTTPException(503, detail="Manga import queue is not available")
        jobs = await queue.store.list()
        return {"jobs": [public_job(job) for job in jobs]}

    @router.get("/api/manga-import-jobs/{job_id}", tags=["api", "gallery"])
    async def get_manga_import_job(job_id: str):
        queue = get_queue()
        if queue is None:
            raise HTTPException(503, detail="Manga import queue is not available")
        try:
            return {"job": public_job(await queue.store.get(job_id), include_items=True)}
        except MangaImportJobNotFound as error:
            raise HTTPException(404, detail="Manga import job not found") from error

    @router.post("/api/manga-import-jobs/{job_id}/retry", tags=["api", "gallery"])
    async def retry_manga_import_job(job_id: str):
        queue = get_queue()
        if queue is None:
            raise HTTPException(503, detail="Manga import queue is not available")
        try:
            return {"job": public_job(await queue.retry(job_id))}
        except MangaImportJobNotFound as error:
            raise HTTPException(404, detail="Manga import job not found") from error
        except MangaImportJobConflict as error:
            raise HTTPException(409, detail=str(error)) from error

    @router.delete("/api/manga-import-jobs/{job_id}", tags=["api", "gallery"])
    async def dismiss_manga_import_job(job_id: str):
        queue = get_queue()
        if queue is None:
            raise HTTPException(503, detail="Manga import queue is not available")
        try:
            await queue.dismiss(job_id)
            return {"status": "dismissed"}
        except MangaImportJobNotFound as error:
            raise HTTPException(404, detail="Manga import job not found") from error
        except MangaImportJobConflict as error:
            raise HTTPException(409, detail=str(error)) from error

    return router, (
        create_manga_import_job,
        list_manga_import_jobs,
        get_manga_import_job,
        retry_manga_import_job,
        dismiss_manga_import_job,
    )


async def _close_uploads(files: list[Any]) -> None:
    import asyncio

    await asyncio.gather(
        *(upload.close() for upload in files if hasattr(upload, "close")),
        return_exceptions=True,
    )
