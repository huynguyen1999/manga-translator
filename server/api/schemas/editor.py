from typing import Literal

from pydantic import BaseModel, Field


class SaveEditsRequest(BaseModel):
    text_regions: list[dict]
    final_image_base64: str | None = None


class LayoutSegmentRequest(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0, le=10000)
    height: int = Field(gt=0, le=10000)


class LayoutPreviewRequest(BaseModel):
    translation: str = Field(max_length=20000)
    group_id: str | None = Field(default=None, max_length=128)
    segments: list[LayoutSegmentRequest] = Field(min_length=1, max_length=8)
    font_size: int = Field(default=24, ge=8, le=512)
    minimum_font_size: int = Field(default=8, ge=8, le=512)
    alignment: Literal["left", "center", "right"] = "center"
    line_spacing: float = Field(default=1.0, ge=0, le=5)
    target_lang: str = Field(default="ENG", max_length=32)


class ReviewStatusRequest(BaseModel):
    status: Literal["approved", "pending"]
