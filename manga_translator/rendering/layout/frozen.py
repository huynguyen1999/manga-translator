"""Versioned, checkpoint-safe page layout serialization."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from ...pipeline.stages import PipelineStage, fingerprint, settings_for_stage
from ..bubble_layout import encode_rendered_box, encode_safe_shape
from .models import FrozenLayout, FrozenLayoutSegment, FrozenLine, FrozenRegionLayout
from .regions import prepare_regions


def _jsonable(value):
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _font_identity(font_path: str | None) -> str:
    if not font_path:
        return fingerprint({"path": ""})
    path = Path(font_path).expanduser()
    try:
        digest = hashlib.sha256()
        with path.open("rb") as font_file:
            for chunk in iter(lambda: font_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return fingerprint({"path": str(path.resolve()), "sha256": digest.hexdigest()})
    except OSError:
        return fingerprint({"path": str(path)})


def layout_input_fingerprints(
    regions,
    config,
    font_path,
    bubble_detections=None,
    image_shape=None,
    inpaint_mask=None,
    mask_fingerprint_override=None,
):
    regions = prepare_regions(regions or [])
    render_config = getattr(config, "render", None)
    transform = getattr(render_config, "transform_text_case", None)
    translations = []
    associations = []
    source_geometry = []
    styles = []
    for index, region in enumerate(regions or []):
        region_id = str(getattr(region, "region_id", None) or f"region_{index}")
        source_font_size = getattr(region, "source_font_size", None)
        if source_font_size is None:
            source_font_size = getattr(region, "font_size", None)
        text = str(getattr(region, "translation", "") or "")
        translations.append({"region_id": region_id, "text": transform(text) if transform else text})
        associations.append({"region_id": region_id, "bubble_id": getattr(region, "bubble_id", None)})
        source_geometry.append({
            "region_id": region_id,
            "lines": _jsonable(getattr(region, "lines", [])),
            "source_region_ids": _jsonable(getattr(region, "source_region_ids", [])),
            "source_regions": _jsonable(getattr(region, "source_regions", [])),
            "source_font_size": source_font_size,
        })
        fg, bg = region.get_font_colors() if hasattr(region, "get_font_colors") else (None, None)
        styles.append({
            "region_id": region_id,
            "font_family": getattr(region, "font_family", None),
            "bold": getattr(region, "bold", None),
            "italic": getattr(region, "italic", None),
            "alignment": _jsonable(getattr(region, "alignment", None)),
            "direction": _jsonable(getattr(region, "direction", None)),
            "fg_color": _jsonable(fg),
            "bg_color": _jsonable(bg),
        })

    config_data = config.model_dump() if hasattr(config, "model_dump") else (
        config.dict() if hasattr(config, "dict") else config
    )
    render_settings = settings_for_stage(
        config_data if isinstance(config_data, dict) else {}, PipelineStage.LAYOUT
    )
    bubble_config = getattr(config, "bubble_detection", None)
    inputs = {
        "translations": fingerprint(translations),
        "source_geometry": fingerprint(source_geometry),
        "styles": fingerprint(styles),
        "bubble_geometry": fingerprint({
            "detections": bubble_detections or [],
            "associations": associations,
            "padding": getattr(bubble_config, "padding", None),
        }),
        "font": _font_identity(font_path),
        "settings": fingerprint(render_settings),
        "image_shape": fingerprint(list(image_shape or [])),
        "mask": (
            fingerprint({
                "shape": list(inpaint_mask.shape),
                "sha256": hashlib.sha256(np.ascontiguousarray(inpaint_mask).tobytes()).hexdigest(),
            }) if inpaint_mask is not None else (mask_fingerprint_override or fingerprint(None))
        ),
    }
    inputs["fingerprint"] = fingerprint(inputs)
    return inputs


def serialize_frozen_layout(ctx, config, font_path, bubble_detections=None) -> dict[str, Any]:
    from .. import fg_bg_compare

    regions = []
    for region in getattr(ctx, "text_regions", []) or []:
        segments = []
        all_lines = []
        for segment in getattr(region, "layout_segments", []) or []:
            lines = [
                FrozenLine(
                    text=str(line.get("text", "")),
                    x=int(line.get("x", 0)),
                    y=int(line.get("y", 0)),
                    width=int(line.get("width", 0)),
                    height=int(line.get("height", 0)),
                )
                for line in segment.get("lines", []) or []
            ]
            all_lines.extend(lines)
            bounds = segment.get("bounds")
            if bounds is None:
                x, y = int(segment.get("x", 0)), int(segment.get("y", 0))
                bounds = [x, y, x + int(segment.get("width", 0)), y + int(segment.get("height", 0))]
            boxes = getattr(region, "_bubble_segments", []) or []
            box = segment.get("box")
            if box is None and len(boxes) > len(segments):
                box = boxes[len(segments)].get("box")
            if box is None and not segments:
                box = getattr(region, "_bubble_box", None)
            # ponytail: keep rare unresolved fallback crops inline; split them into disk artifacts if page documents grow.
            rendered_png = encode_rendered_box(box) if box is not None and not lines else None
            if not lines and not rendered_png:
                continue
            segments.append(FrozenLayoutSegment(
                x=int(bounds[0]),
                y=int(bounds[1]),
                width=int(bounds[2] - bounds[0]),
                height=int(bounds[3] - bounds[1]),
                text=str(segment.get("text", "")),
                font_size=int(segment.get("font_size", getattr(region, "font_size", 0)) or 0),
                lines=lines,
                rendered_png=rendered_png,
            ))

        if not segments and getattr(region, "_bubble_box", None) is not None:
            bounds = list(getattr(region, "layout_bounds", []) or [])
            box = region._bubble_box
            if len(bounds) == 4:
                segments.append(FrozenLayoutSegment(
                    x=int(bounds[0]), y=int(bounds[1]),
                    width=int(bounds[2] - bounds[0]), height=int(bounds[3] - bounds[1]),
                    text=str(getattr(region, "translation", "") or ""),
                    font_size=int(getattr(region, "font_size", 0) or 0),
                    rendered_png=encode_rendered_box(box),
                ))

        bounds = list(getattr(region, "layout_bounds", []) or [])
        if len(bounds) != 4 and all_lines:
            bounds = [
                min(line.x for line in all_lines), min(line.y for line in all_lines),
                max(line.x + line.width for line in all_lines),
                max(line.y + line.height for line in all_lines),
            ]
        fg, bg = region.get_font_colors() if hasattr(region, "get_font_colors") else ((0, 0, 0), (255, 255, 255))
        fg, bg = fg_bg_compare(fg, bg)
        source_font_size = getattr(region, "source_font_size", None)
        if source_font_size is None:
            source_font_size = getattr(region, "font_size", 0)
        interior = getattr(region, "_bubble_interior", None)
        placement_mode = getattr(region, "placement_mode", None)
        placement_mode = getattr(placement_mode, "value", placement_mode)
        state = "FROZEN" if segments else "UNSOLVED"
        regions.append(FrozenRegionLayout(
            region_id=str(getattr(region, "region_id", "") or ""),
            bubble_id=str(getattr(region, "bubble_id", "") or "") or None,
            state=state,
            placement_mode=str(placement_mode) if placement_mode is not None else None,
            font=str(font_path) if font_path else None,
            font_size=int(getattr(region, "font_size", 0) or 0),
            source_font_size=int(source_font_size or 0),
            line_spacing=float(getattr(config.render, "line_spacing", 0.0) or 0.0),
            layout_bounds=[int(value) for value in bounds],
            segments=segments,
            alignment=str(getattr(region, "alignment", "") or ""),
            direction=str(getattr(region, "direction", "") or ""),
            language=str(getattr(region, "target_lang", "") or ""),
            fg_color=[int(value) for value in fg],
            bg_color=[int(value) for value in bg] if bg is not None else None,
            font_family=str(getattr(region, "font_family", "") or ""),
            bold=bool(getattr(region, "bold", False)),
            italic=bool(getattr(region, "italic", False)),
            solver_path=getattr(region, "_solver_path", None),
            solver_status=getattr(region, "_solver_status", None),
            hyphenation=dict(getattr(region, "_hyphenation_diagnostics", {}) or {}),
            render_suppressed=bool(getattr(region, "_render_suppressed", False)),
            bubble_safe_shape=encode_safe_shape(interior) if interior is not None else getattr(region, "bubble_safe_shape", None),
        ))

    layout_mask = getattr(ctx, "inpaint_mask", None)
    if layout_mask is None:
        layout_mask = getattr(ctx, "mask", None)
    inputs = layout_input_fingerprints(
        getattr(ctx, "text_regions", []) or [], config, font_path, bubble_detections,
        getattr(getattr(ctx, "img_rgb", None), "shape", None),
        layout_mask,
    )
    return asdict(FrozenLayout(2, inputs["fingerprint"], inputs, regions))


def hydrate_layout(ctx, persisted_layout, expected_fingerprint: str | None = None) -> None:
    """Restore frozen line placements by region identity, rejecting stale inputs."""
    if not isinstance(persisted_layout, dict) or persisted_layout.get("version") != 2:
        _hydrate_legacy_layout(ctx, persisted_layout)
        return
    if expected_fingerprint and persisted_layout.get("input_fingerprint") != expected_fingerprint:
        raise ValueError("Frozen layout is stale; rerun the layout stage before rendering")

    from .models import PlacementMode

    by_id = {
        str(getattr(region, "region_id", "") or ""): region
        for region in getattr(ctx, "text_regions", []) or []
    }
    for item in persisted_layout.get("regions", []):
        region = by_id.get(str(item.get("region_id", "")))
        if region is None:
            continue
        if item.get("source_font_size") is not None:
            region.source_font_size = int(item["source_font_size"] or 0)
        elif getattr(region, "source_font_size", None) is None:
            # Older v2 documents omit it; preserve the translation checkpoint size before replacing font_size.
            region.source_font_size = int(getattr(region, "font_size", 0) or 0)
        region.layout_bounds = list(item.get("layout_bounds") or [])
        region.layout_segments = item.get("segments") or []
        region.font_size = int(item.get("font_size", getattr(region, "font_size", 0)) or 0)
        region.line_spacing = float(item.get("line_spacing", 0.0) or 0.0)
        region.bubble_id = item.get("bubble_id")
        region._layout_frozen = item.get("state") == "FROZEN" and bool(region.layout_segments)
        region._solver_applied = region._layout_frozen
        region._free_text_solver_applied = region._layout_frozen and item.get("placement_mode") == "FREE_TEXT"
        region.placement_mode = PlacementMode(item["placement_mode"]) if item.get("placement_mode") in {mode.value for mode in PlacementMode} else item.get("placement_mode")
        region._solver_path = item.get("solver_path")
        region._solver_status = item.get("solver_status")
        region._hyphenation_diagnostics = item.get("hyphenation") or {}
        region._render_suppressed = bool(item.get("render_suppressed", False))
        if item.get("fg_color") is not None:
            region.fg_colors = item["fg_color"]
        if item.get("bg_color") is not None:
            region.bg_colors = item["bg_color"]
        region.bubble_safe_shape = item.get("bubble_safe_shape")


def _hydrate_legacy_layout(ctx, persisted_layout) -> None:
    if isinstance(persisted_layout, dict):
        return
    if not isinstance(persisted_layout, list):
        return
    from .models import PlacementMode

    by_id = {
        str(getattr(region, "region_id", "") or ""): region
        for region in getattr(ctx, "text_regions", []) or []
    }
    for index, item in enumerate(persisted_layout):
        if not isinstance(item, dict):
            continue
        region = by_id.get(str(item.get("region_id", "")))
        if region is None and index < len(getattr(ctx, "text_regions", []) or []):
            region = ctx.text_regions[index]
        if region is None:
            continue
        segments = item.get("layout_segments") or []
        region.layout_segments = segments
        region.layout_bounds = item.get("layout_bounds") or getattr(region, "layout_bounds", None)
        mode = item.get("placement_mode")
        region.placement_mode = PlacementMode(mode) if mode in {value.value for value in PlacementMode} else mode
        region._layout_frozen = any(segment.get("lines") for segment in segments)
        region._solver_applied = region._layout_frozen
        region._free_text_solver_applied = region._layout_frozen and mode == "FREE_TEXT"
