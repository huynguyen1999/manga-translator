from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator

from manga_translator.config import MAX_MANGA_TITLE_LENGTH
from server.constants import MAX_BATCH_ITEMS


class UpdateMetaRequest(BaseModel):
    folders: Optional[List[str]] = None
    pageIds: Optional[List[str]] = None
    groupId: Optional[str] = None
    oldMangaTitle: Optional[str] = None
    mangaTitle: str = Field(..., max_length=MAX_MANGA_TITLE_LENGTH)

    @field_validator("mangaTitle", "oldMangaTitle", mode="before")
    @classmethod
    def _strip_titles(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v


class ReorderPagesRequest(BaseModel):
    pageIds: List[str]


class DeletePagesRequest(BaseModel):
    folders: List[str] = Field(..., min_length=1, max_length=MAX_BATCH_ITEMS)


class ExportCbzRequest(BaseModel):
    groupId: Optional[str] = None
    mangaTitle: Optional[str] = Field("manga", max_length=MAX_MANGA_TITLE_LENGTH)
    folders: Optional[List[str]] = None
    original: bool = False

    @field_validator("mangaTitle", mode="before")
    @classmethod
    def _strip_manga_title(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v if v else "manga"
        return v


class ReadingProgressRequest(BaseModel):
    installationId: str
    groupId: Optional[str] = None
    mangaTitle: Optional[str] = Field(None, max_length=MAX_MANGA_TITLE_LENGTH)
    pageId: Optional[str] = None
    page: Optional[int] = None
    scrollTop: int = 0
    complete: bool = False
    updatedAt: Optional[str] = None

    @field_validator("mangaTitle", mode="before")
    @classmethod
    def _strip_title(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v
