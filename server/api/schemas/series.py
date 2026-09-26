from typing import Any

from pydantic import BaseModel, Field, field_validator

from manga_translator.config import MAX_MANGA_TITLE_LENGTH


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
