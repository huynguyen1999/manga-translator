import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from manga_translator.config import MAX_MANGA_TITLE_LENGTH


def create_manga_group_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    scan_manga_groups: Callable[..., dict[str, Any]],
) -> tuple[APIRouter, Callable[..., Any]]:
    router = APIRouter()

    @router.get("/results/groups", tags=["api"])
    @router.get("/api/results/groups", tags=["api"])
    @router.get("/api/manga", tags=["api"])
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
        store = get_store()
        if store is not None:
            try:
                return await store.list_groups(limit, offset, manga_id, search, sort, review, status)
            except Exception as error:
                raise HTTPException(503, detail=f"PostgreSQL result store unavailable: {error}") from error

        result_dir = get_result_root()
        if not result_dir.exists():
            return {"groups": [], "totalGroups": 0, "totalImages": 0, "nextOffset": None}

        try:
            return await asyncio.to_thread(
                scan_manga_groups, result_dir, limit, offset, manga_id, search, sort, review, status
            )
        except Exception as error:
            raise HTTPException(500, detail=f"Error listing result groups: {str(error)}")

    return router, list_result_groups


def create_manga_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    scan_results: Callable[..., dict[str, Any]],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.get("/results/list", tags=["api"])
    @router.get("/api/results/list", tags=["api"])
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
        store = get_store()
        group_value = groupId or manga
        if store is not None:
            try:
                return await store.list_results(sort, group_value, detail, limit, offset, review)
            except Exception as error:
                raise HTTPException(503, detail=f"PostgreSQL result store unavailable: {error}") from error

        result_dir = get_result_root()
        if not result_dir.exists():
            return {"directories": [], "items": [], "total": 0, "nextOffset": None}

        try:
            return await asyncio.to_thread(
                scan_results, result_dir, sort, group_value, detail, limit, offset, review
            )
        except Exception as error:
            raise HTTPException(500, detail=f"Error listing results: {str(error)}")

    @router.get("/api/manga/{manga_id}/pages", tags=["api"])
    async def list_manga_pages(
        manga_id: str,
        sort: str = "alpha",
        detail: Optional[str] = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        review: Optional[str] = Query(None, pattern="^pending$"),
    ):
        """Canonical page-list adapter; legacy result routes remain supported."""
        return await list_results(
            sort=sort,
            groupId=manga_id,
            detail=detail,
            limit=limit,
            offset=offset,
            review=review,
        )

    return router, (list_results, list_manga_pages)
