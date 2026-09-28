from typing import Any

from pydantic import BaseModel, Field, field_validator


class CreatePresetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    settings: dict[str, Any] = Field(default_factory=dict)
    isDefault: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        if v is None:
            return ""
        return v


class UpdatePresetRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    settings: dict[str, Any] | None = None
    isDefault: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v


class PresetResponse(BaseModel):
    id: str
    name: str
    description: str
    isDefault: bool
    settings: dict[str, Any]
    createdAt: str
    updatedAt: str


class PresetListResponse(BaseModel):
    presets: list[PresetResponse]
