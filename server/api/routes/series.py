from collections.abc import Callable
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from server.api.schemas.series import (
    AddSeriesMembersRequest,
    CreateSeriesRequest,
    MoveMangaSeriesRequest,
    ReplaceSeriesMembersRequest,
    UpdateSeriesRequest,
)
from server.postgres_store import InvalidSeries, SeriesConflict, SeriesNotFound


def series_http_error(error: Exception) -> HTTPException:
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, SeriesNotFound):
        return HTTPException(404, detail="Series not found")
    if isinstance(error, SeriesConflict):
        return HTTPException(409, detail=str(error))
    if isinstance(error, InvalidSeries):
        return HTTPException(400, detail=str(error))
    return HTTPException(500, detail=str(error))


def create_series_router(
    postgres_required: Callable[[], Any],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.get("/series", tags=["api", "series"])
    @router.get("/api/series", tags=["api", "series"])
    async def list_series(
        limit: int = Query(12, ge=1, le=500),
        offset: int = Query(0, ge=0),
        search: Optional[str] = Query(None, max_length=200),
    ):
        try:
            return await postgres_required().list_series(limit, offset, search)
        except Exception as error:
            raise series_http_error(error) from error

    @router.get("/series/{series_id}", tags=["api", "series"])
    @router.get("/api/series/{series_id}", tags=["api", "series"])
    async def get_series(series_id: str):
        try:
            return await postgres_required().get_series(series_id)
        except Exception as error:
            raise series_http_error(error) from error

    @router.post("/series", tags=["api", "series"])
    @router.post("/api/series", tags=["api", "series"])
    async def create_series(data: CreateSeriesRequest):
        try:
            return await postgres_required().create_series(data.title, data.groupIds)
        except Exception as error:
            raise series_http_error(error) from error

    @router.patch("/series/{series_id}", tags=["api", "series"])
    @router.patch("/api/series/{series_id}", tags=["api", "series"])
    async def update_series(series_id: str, data: UpdateSeriesRequest):
        try:
            return await postgres_required().update_series_title(series_id, data.title)
        except Exception as error:
            raise series_http_error(error) from error

    @router.put("/series/{series_id}/members", tags=["api", "series"])
    @router.put("/api/series/{series_id}/members", tags=["api", "series"])
    async def replace_series_members(series_id: str, data: ReplaceSeriesMembersRequest):
        try:
            return await postgres_required().replace_series_members(series_id, data.groupIds)
        except Exception as error:
            raise series_http_error(error) from error

    @router.post("/series/{series_id}/members/add", tags=["api", "series"])
    @router.post("/api/series/{series_id}/members/add", tags=["api", "series"])
    async def add_series_members(series_id: str, data: AddSeriesMembersRequest):
        try:
            return await postgres_required().add_manga_to_series(series_id, data.groupIds)
        except Exception as error:
            raise series_http_error(error) from error

    @router.post("/manga/{manga_id}/series/move", tags=["api", "series"])
    @router.post("/api/manga/{manga_id}/series/move", tags=["api", "series"])
    async def move_manga_series(manga_id: str, data: MoveMangaSeriesRequest):
        try:
            return await postgres_required().move_manga_to_series(manga_id, data.targetSeriesId)
        except Exception as error:
            raise series_http_error(error) from error

    @router.delete("/manga/{manga_id}/series", tags=["api", "series"])
    @router.delete("/api/manga/{manga_id}/series", tags=["api", "series"])
    async def remove_manga_series(manga_id: str):
        try:
            await postgres_required().remove_manga_from_series(manga_id)
            return {"status": "removed", "mangaId": manga_id}
        except Exception as error:
            raise series_http_error(error) from error

    @router.delete("/series/{series_id}", tags=["api", "series"])
    @router.delete("/api/series/{series_id}", tags=["api", "series"])
    async def delete_series(series_id: str):
        try:
            await postgres_required().delete_series(series_id)
        except Exception as error:
            raise series_http_error(error) from error
        return {"status": "deleted", "id": series_id}

    @router.get("/manga/{manga_id}/series", tags=["api", "series"])
    @router.get("/api/manga/{manga_id}/series", tags=["api", "series"])
    async def get_manga_series(manga_id: str):
        try:
            return {"series": await postgres_required().get_series_for_group(manga_id)}
        except Exception as error:
            raise series_http_error(error) from error

    return router, (
        list_series,
        get_series,
        create_series,
        update_series,
        replace_series_members,
        add_series_members,
        move_manga_series,
        remove_manga_series,
        delete_series,
        get_manga_series,
    )
