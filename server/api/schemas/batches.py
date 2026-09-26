from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from manga_translator.config import MAX_MANGA_TITLE_LENGTH


class UpdateBatchRequest(BaseModel):
    translator: str | None = None
    mangaTitle: str | None = Field(None, max_length=MAX_MANGA_TITLE_LENGTH)
    priority: bool | None = None
    keep_failed_pages_for_editing: bool | None = None

    @field_validator("mangaTitle", mode="before")
    @classmethod
    def _strip_manga_title(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v


class RetryBatchItemRequest(BaseModel):
    keep_failed_pages_for_editing: bool | None = None
    from_stage: str | None = Field(None, alias="fromStage")


class UpdateBatchItemRequest(BaseModel):
    excludeColor: bool
