"""Pipeline result document serialization and restoration."""

from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np

from ..utils import Quadrilateral, TextBlock


def _json_default(value: Any):
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_default(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_default(item) for key, item in value.items()}
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def serialize_regions(regions) -> list[dict[str, Any]]:
    """Keep useful OCR/translation/layout facts without serializing model objects."""
    from ..rendering.bubble_layout import encode_safe_shape
    result = []
    for index, region in enumerate(regions or []):
        item: dict[str, Any] = {"index": index}
        for key in (
            "region_id", "source_region_ids", "source_regions", "group_id", "group_members", "bubble_id",
            "text", "text_raw", "source_text_snapshot", "source_geometry", "translation", "confidence", "font_size", "source_font_size",
            "calibrated_font_size", "angle", "direction", "alignment", "target_lang", "source_lang",
            "bubble_bounds", "layout_bounds", "layout_segments", "review_required", "review_reason",
            "placement_mode", "line_spacing", "letter_spacing", "font_family", "bold", "italic",
            "provenance", "source_style", "source_line_styles", "retention", "retention_reason",
            "translation_policy",
        ):
            value = getattr(region, key, None)
            if value is None and key == "confidence":
                value = getattr(region, "prob", None)
            if value is not None:
                item[key] = _json_default(value)
        fg_col, bg_col = region.get_font_colors() if hasattr(region, "get_font_colors") else (getattr(region, "fg_color", None), getattr(region, "bg_color", None))
        if fg_col is not None:
            item["fg_color"] = _json_default(fg_col)
        if bg_col is not None:
            item["bg_color"] = _json_default(bg_col)
        interior = getattr(region, "_bubble_interior", None)
        if interior is not None and np.any(interior):
            item["bubble_safe_shape"] = encode_safe_shape(interior)
        elif getattr(region, "bubble_safe_shape", None):
            item["bubble_safe_shape"] = region.bubble_safe_shape
        for key in ("xywh", "pts", "lines"):
            value = getattr(region, key, None)
            if value is not None:
                item[key] = _json_default(value)
        result.append(item)
    return result


def serialize_editor_regions(regions) -> list[dict[str, Any]]:
    """Minimal editor payload for resumed runs that skip the normal save path."""
    from ..rendering.bubble_layout import encode_safe_shape, encode_rendered_box
    result = []
    for index, region in enumerate(regions or []):
        xywh = _json_default(getattr(region, "xywh", [0, 0, 0, 0]))
        result.append({
            "id": getattr(region, "group_id", f"bubble_{index}"),
            "region_id": getattr(region, "region_id", ""),
            "x": xywh[0],
            "y": xywh[1],
            "width": xywh[2],
            "height": xywh[3],
            "lines": _json_default(getattr(region, "lines", [])),
            "original_text": getattr(region, "text", ""),
            "translation": getattr(region, "translation", ""),
            "confidence": _json_default(getattr(region, "confidence", getattr(region, "prob", None))),
            "font_size": getattr(region, "font_size", 24),
            "source_font_size": getattr(region, "source_font_size", None) or getattr(region, "font_size", 24),
            "font_family": getattr(region, "font_family", ""),
            "fg_color": _json_default(getattr(region, "fg_colors", (0, 0, 0))),
            "bg_color": _json_default(getattr(region, "bg_colors", (0, 0, 0))),
            "alignment": getattr(region, "alignment", getattr(region, "_alignment", "auto")),
            "line_spacing": getattr(region, "line_spacing", 1.0),
            "letter_spacing": getattr(region, "letter_spacing", 1.0),
            "bold": bool(getattr(region, "bold", False)),
            "italic": bool(getattr(region, "italic", False)),
            "target_lang": getattr(region, "target_lang", ""),
            "direction": getattr(region, "direction", "h"),
            "layout_segments": [
                {**_json_default(segment), "rendered_png": encode_rendered_box(box["box"])}
                for segment, box in zip(getattr(region, "layout_segments", []),
                                        getattr(region, "_bubble_segments", []))
            ],
            "bubble_safe_shape": encode_safe_shape(getattr(region, "_bubble_interior", None)),
            "review_required": bool(getattr(region, "review_required", False)),
            "review_reason": getattr(region, "review_reason", None),
            "provenance": getattr(region, "provenance", None),
            "translation_remap": getattr(region, "translation_remap", None),
            "translation_source": getattr(region, "translation_source", None),
        })
    return result


def deserialize_textlines(data: list[dict[str, Any]]) -> list[Quadrilateral]:
    textlines = []
    for item in data or []:
        pts = item.get("pts")
        if pts is None and "lines" in item:
            pts = item["lines"]
        if pts is not None:
            pts_arr = np.array(pts, dtype=np.int32)
            if len(pts_arr.shape) == 3:
                pts_arr = pts_arr[0]
            txt = str(item.get("text") or item.get("text_raw") or "")
            prob = float(item.get("confidence") or item.get("prob") or 1.0)
            line = Quadrilateral(pts_arr, txt, prob)
            if item.get("source_style") is not None:
                line.source_style = item["source_style"]
            textlines.append(line)
    return textlines


def deserialize_textblocks(data: list[dict[str, Any]]) -> list[TextBlock]:
    blocks = []
    for item in data or []:
        lines = item.get("lines")
        if lines is None and "pts" in item:
            lines = [item["pts"]]
        if lines is None:
            lines = []
        texts = [str(item.get("text") or item.get("text_raw") or item.get("original_text") or "")]
        tb = TextBlock(
            lines=lines,
            texts=texts,
            translation=str(item.get("translation") or ""),
            font_size=float(item.get("font_size", -1) or -1),
            angle=float(item.get("angle", 0) or 0),
            fg_color=tuple(item.get("fg_color") or (0, 0, 0)),
            bg_color=tuple(item.get("bg_color") or (0, 0, 0)),
            line_spacing=float(item.get("line_spacing", 1.0) or 1.0),
            letter_spacing=float(item.get("letter_spacing", 1.0) or 1.0),
            font_family=str(item.get("font_family") or ""),
            bold=bool(item.get("bold", False)),
            italic=bool(item.get("italic", False)),
            direction=str(item.get("direction") or "auto"),
            alignment=str(item.get("alignment") or "auto"),
            target_lang=str(item.get("target_lang") or ""),
            prob=float(item.get("confidence") or item.get("prob") or 1.0),
            source_text_snapshot=item.get("source_text_snapshot"),
            source_geometry=item.get("source_geometry"),
        )
        tb.region_id = str(item.get("region_id") or "")
        for key in ("source_style", "source_line_styles"):
            if key in item:
                setattr(tb, key, item[key])
        for key in (
            "group_id", "group_members", "source_region_ids", "source_regions", "source_text_snapshot", "source_geometry", "bubble_id",
            "source_font_size", "calibrated_font_size", "placement_mode",
            "bubble_bounds", "layout_bounds", "layout_segments",
            "bubble_safe_shape", "review_required", "review_reason",
            "retention", "retention_reason", "translation_policy",
        ):
            if key in item and item[key] is not None:
                setattr(tb, key, item[key])
        if not getattr(tb, "group_id", None):
            tb.group_id = str(item.get("id") or tb.region_id or "")
        blocks.append(tb)
    return blocks
