from typing import Any

from pydantic import BaseModel, Field

from manga_translator.config import MAX_MANGA_TITLE_LENGTH
from server.constants import MAX_BATCH_ITEMS


class PipelineRerunRequest(BaseModel):
    pageIds: list[str] = Field(default_factory=list, max_length=MAX_BATCH_ITEMS)
    groupId: str | None = Field(default=None, max_length=MAX_MANGA_TITLE_LENGTH)
    mode: str = Field(default="typesetting")
    settingsOverrides: dict[str, Any] = Field(default_factory=dict)


class PipelineCaseRerunRequest(BaseModel):
    pageId: str
    mode: str = Field(default="typesetting")
    settingsOverrides: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)


class RerenderRequest(BaseModel):
    pageIds: list[str] = Field(default_factory=list, max_length=MAX_BATCH_ITEMS)
    groupId: str | None = Field(default=None, max_length=MAX_MANGA_TITLE_LENGTH)
    settingsOverrides: dict[str, Any] = Field(default_factory=dict)
