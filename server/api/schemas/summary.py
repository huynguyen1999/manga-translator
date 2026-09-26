from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from manga_translator.config import MAX_MANGA_TITLE_LENGTH


class MangaSummaryRequest(BaseModel):
    groupId: Optional[str] = None
    mangaTitle: Optional[str] = Field(None, max_length=MAX_MANGA_TITLE_LENGTH)
    summaryModel: Optional[str] = Field(None, max_length=100)
    regenerate: bool = False
    refreshText: bool = False

    @field_validator("mangaTitle", mode="before")
    @classmethod
    def _strip_manga_title(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    @field_validator("summaryModel", mode="before")
    @classmethod
    def _strip_summary_model(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


class SummaryControlRequest(BaseModel):
    groupId: Optional[str] = None
    mangaTitle: Optional[str] = Field(None, max_length=MAX_MANGA_TITLE_LENGTH)

    @field_validator("mangaTitle", mode="before")
    @classmethod
    def _strip_title(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


SummaryDismissRequest = SummaryControlRequest
