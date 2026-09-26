from collections.abc import Callable
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from manga_translator.config import MAX_MANGA_TITLE_LENGTH
from server.api.schemas.manga import ReadingProgressRequest
from server.postgres_store import GroupNotFound


def create_reading_progress_router(
    get_store: Callable[[], Any],
    validate_installation_id: Callable[[str], str],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.get("/reading-progress", tags=["api", "reader"])
    @router.get("/api/reading-progress", tags=["api", "reader"])
    async def get_reading_progress(
        installationId: str = Query(..., min_length=8, max_length=128),
        groupId: Optional[str] = Query(None, min_length=1),
        mangaTitle: Optional[str] = Query(None, min_length=1, max_length=MAX_MANGA_TITLE_LENGTH),
    ):
        store = get_store()
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
            validate_installation_id(installationId), group_value
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

    @router.put("/reading-progress", tags=["api", "reader"])
    @router.put("/api/reading-progress", tags=["api", "reader"])
    async def save_reading_progress(data: ReadingProgressRequest):
        store = get_store()
        if store is None:
            raise HTTPException(503, detail="PostgreSQL progress store is unavailable")
        payload = data.model_dump()
        payload["installationId"] = validate_installation_id(data.installationId)
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

    return router, (get_reading_progress, save_reading_progress)
