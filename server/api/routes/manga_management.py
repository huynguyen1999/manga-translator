from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from server.api.schemas.manga import ReorderPagesRequest, UpdateMetaRequest
from server.postgres_store import GroupConflict, GroupNotFound
from server.services.manga_mutation import MangaMutationService


def create_manga_management_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    reorder_file_backed_pages: Callable[..., Any],
    update_file_backed_meta: Callable[..., Any],
    result_file: Callable[[Path], Optional[Path]],
    invalidate_meta_cache: Callable[[Optional[str]], None],
    rename_summary: Callable[..., Any],
    remove_summary: Callable[..., Any],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()
    service = MangaMutationService(
        get_store,
        get_result_root,
        reorder_file_backed_pages,
        update_file_backed_meta,
        rename_summary,
        result_file,
        invalidate_meta_cache,
        remove_summary,
    )

    @router.put("/manga/{manga_id}/pages/order", tags=["api"])
    @router.put("/api/manga/{manga_id}/pages/order", tags=["api"])
    async def reorder_manga_pages(manga_id: str, data: ReorderPagesRequest):
        try:
            pages = await service.reorder_pages(manga_id, data.pageIds)
        except GroupNotFound as error:
            raise HTTPException(404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(400, detail=str(error)) from error
        return {"groupId": manga_id, "pages": pages}

    @router.post("/results/update-meta", tags=["api"])
    @router.post("/api/results/update-meta", tags=["api"])
    @router.patch("/api/manga", tags=["api"])
    async def update_meta(data: UpdateMetaRequest):
        """Update metadata (e.g. mangaTitle) for specified result folders or by old mangaTitle"""
        try:
            clean_title, old_title, updated_count = await service.update_metadata(
                manga_title=data.mangaTitle,
                old_manga_title=data.oldMangaTitle,
                page_ids=data.pageIds,
                folders=data.folders,
                group_id=data.groupId,
            )
        except GroupNotFound as error:
            raise HTTPException(404, detail=str(error)) from error
        except GroupConflict as error:
            raise HTTPException(409, detail=str(error)) from error
        return {"updated": updated_count, "mangaTitle": clean_title}

    @router.delete("/results/group", tags=["api"])
    @router.delete("/api/results/group", tags=["api"])
    @router.delete("/api/manga/{title}", tags=["api"])
    async def delete_manga_group_endpoint(title: str):
        """Delete all results belonging to a specific manga title"""
        store = get_store()
        if store is not None:
            clean_title, deleted_count = await service.delete_group(title, store)
            return {"message": f"Deleted {deleted_count} pages for manga '{clean_title}'", "deleted": deleted_count}

        try:
            clean_title, deleted_count = await service.delete_group(title, None)
            if deleted_count is None:
                return {"message": "No results directory found", "deleted": 0}
            return {"message": f"Deleted {deleted_count} pages for manga '{clean_title}'", "deleted": deleted_count}
        except Exception as error:
            raise HTTPException(500, detail=f"Error deleting manga group: {str(error)}")

    return router, (reorder_manga_pages, update_meta, delete_manga_group_endpoint)
