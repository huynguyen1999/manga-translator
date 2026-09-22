"""Thin HTTP boundary for the optional Search Lab service."""
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("manga-translator.search")


class EmbedRequest(BaseModel):
    groupIds: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("groupIds")
    @classmethod
    def valid_ids(cls, values):
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError("Invalid manga ID")
        return list(dict.fromkeys(values))


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=5000)
    mode: Literal["summary", "image", "combined"] = "combined"
    groupIds: list[str] | None = Field(default=None, max_length=1000)
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value):
        if not value.strip():
            raise ValueError("Enter a search description")
        return value.strip()

    @field_validator("groupIds")
    @classmethod
    def selected_scope(cls, values):
        if values is not None:
            if not values:
                raise ValueError("Select at least one manga or search all indexed manga")
            return EmbedRequest.valid_ids(values)
        return None


async def call(operation):
    try:
        return await operation
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        if type(error).__name__ == "UniqueViolationError":
            raise HTTPException(409, "An embedding job is already running") from error
        logger.exception("Search Lab request failed")
        raise HTTPException(503, f"Search Lab is unavailable: {error}. Check Qdrant, model downloads, and database migration.") from error


def search_router(get_service):
    router = APIRouter(prefix="/search", tags=["search"])

    def service():
        instance = get_service()
        if instance is None:
            raise HTTPException(503, "Search Lab requires PostgreSQL and the search migration. Start the backend with DATABASE_URL configured.")
        return instance

    @router.get("/status")
    async def status():
        return await call(service().status())

    @router.get("/manga")
    async def manga(
        search: str = Query("", max_length=200),
        status: Literal["all", "summarized", "not-summarized"] = Query("all"),
        summarized: bool | None = Query(None),
        offset: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=50),
    ):
        filter_status = status
        if summarized is True:
            filter_status = "summarized"
        elif summarized is False:
            filter_status = "not-summarized"
        return await call(service().db.manga(search, offset, limit, status=filter_status))

    @router.post("/jobs", status_code=202)
    async def embed(body: EmbedRequest):
        return await call(service().submit(body.groupIds))

    @router.get("/jobs")
    async def jobs():
        return await call(service().db.jobs())

    @router.post("/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        await call(service().cancel(job_id))
        return {"status": "cancelling"}

    @router.post("/jobs/{job_id}/resume", status_code=202)
    async def resume(job_id: str):
        return await call(service().submit(resume_id=job_id))

    @router.post("/query")
    async def query(body: SearchRequest):
        return await call(service().query(body.query, body.mode, body.groupIds, body.limit))

    return router
