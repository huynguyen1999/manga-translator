from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from server.api.schemas.manga import ReorderPagesRequest, UpdateMetaRequest
from server.postgres_store import GroupConflict, GroupNotFound
from server.services.manga_mutation import MangaMutationService
from server.services.manga_review_mutation import MangaReviewMutationService


def create_manga_management_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    reorder_file_backed_pages: Callable[..., Any],
    update_file_backed_meta: Callable[..., Any],
    result_file: Callable[[Path], Optional[Path]],
    invalidate_meta_cache: Callable[[Optional[str]], None],
    rename_summary: Callable[..., Any],
    remove_summary: Callable[..., Any],
    sync_batch_review: Optional[Callable[[str, bool], Any]] = None,
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()
    service = MangaMutationService(
        get_store, get_result_root, reorder_file_backed_pages, update_file_backed_meta,
        rename_summary, result_file, invalidate_meta_cache, remove_summary,
    )
    review_service = MangaReviewMutationService(
        get_store, get_result_root, result_file, invalidate_meta_cache, sync_batch_review,
    )

    @router.put("/manga/{manga_id}/pages/order", tags=["api"])
    @router.put("/api/manga/{manga_id}/pages/order", tags=["api"])
    async def reorder_manga_pages(manga_id: str, data: ReorderPagesRequest):
        try:
            return {"groupId": manga_id, "pages": await service.reorder_pages(manga_id, data.pageIds)}
        except GroupNotFound as error:
            raise HTTPException(404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(400, detail=str(error)) from error

    @router.post("/results/update-meta", tags=["api"])
    @router.post("/api/results/update-meta", tags=["api"])
    @router.patch("/api/manga", tags=["api"])
    async def update_meta(data: UpdateMetaRequest):
        try:
            clean_title, old_title, updated_count = await service.update_metadata(
                manga_title=data.mangaTitle, old_manga_title=data.oldMangaTitle,
                page_ids=data.pageIds, folders=data.folders, group_id=data.groupId,
            )
            return {"updated": updated_count, "mangaTitle": clean_title}
        except GroupNotFound as error:
            raise HTTPException(404, detail=str(error)) from error
        except GroupConflict as error:
            raise HTTPException(409, detail=str(error)) from error

    @router.delete("/results/group", tags=["api"])
    @router.delete("/api/results/group", tags=["api"])
    @router.delete("/api/manga/{title}", tags=["api"])
    async def delete_manga_group_endpoint(title: str):
        store = get_store()
        try:
            clean_title, deleted_count = await service.delete_group(title, store)
            return {"message": f"Deleted {deleted_count or 0} pages for manga '{clean_title}'", "deleted": deleted_count or 0}
        except Exception as error:
            raise HTTPException(500, detail=f"Error deleting manga group: {str(error)}")

    @router.post("/manga/{manga_id}/review/approve-all", tags=["api"])
    @router.post("/api/manga/{manga_id}/review/approve-all", tags=["api"])
    @router.post("/api/manga/{manga_id}/review/approve", tags=["api"])
    async def approve_all_manga_review(manga_id: str):
        try:
            clean_title, approved_count = await review_service.approve_manga_review(manga_id)
            return {"groupId": manga_id, "mangaTitle": clean_title, "approvedCount": approved_count, "status": "approved"}
        except GroupNotFound as error:
            raise HTTPException(404, detail=str(error)) from error

    return router, (reorder_manga_pages, update_meta, delete_manga_group_endpoint, approve_all_manga_review)
