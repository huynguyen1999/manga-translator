"""Conservative second-stage grouping for neighboring free-text regions."""

from __future__ import annotations

import copy
from functools import cached_property

import cv2
import numpy as np

from ..geometry.panels import infer_panel_constraints
from .grouping import _source_region_snapshot

_MIN_PANEL_CONFIDENCE = 0.68
_MAX_FONT_RATIO = 1.25
_MAX_ANGLE_DELTA = 8.0
_MAX_GAP_FONT_UNITS = 1.25
_MIN_ALIGNMENT_OVERLAP = 0.65


def _direction(region) -> str:
    try:
        direction = str(region.direction or "")
    except (AttributeError, TypeError, ValueError):
        direction = ""
    return direction if direction[:1] in ("h", "v") else ""


def _reading_key(region, orientation: str):
    x1, y1, x2, y2 = [float(value) for value in region.xyxy]
    direction = str(getattr(region, "direction", "") or "")
    rtl = direction == "v" or direction.endswith("r")
    if orientation == "v":
        return (-x1 if rtl else x1, (y1 + y2) / 2)
    return ((y1 + y2) / 2, -(x1 + x2) / 2 if rtl else (x1 + x2) / 2)


def _is_bubble_free(region) -> bool:
    return not any(
        getattr(region, name, None) is not None
        for name in ("bubble_id", "_bubble_mask", "_bubble_interior", "bubble_safe_shape")
    ) and getattr(region, "translation_policy", None) != "preserve"


def _can_coalesce(first, second, panels) -> bool:
    if not _is_bubble_free(first) or not _is_bubble_free(second):
        return False
    if not str(getattr(first, "text", "") or "").strip() or not str(getattr(second, "text", "") or "").strip():
        return False
    direction = _direction(first)
    if not direction or direction != _direction(second):
        return False

    first_panel, second_panel = panels.get(id(first)), panels.get(id(second))
    if (
        first_panel is None or second_panel is None
        or first_panel.source != "cv" or second_panel.source != "cv"
        or first_panel.confidence < _MIN_PANEL_CONFIDENCE
        or second_panel.confidence < _MIN_PANEL_CONFIDENCE
        or first_panel.panel_id == "page"
        or first_panel.panel_id != second_panel.panel_id
    ):
        return False

    font_a = float(getattr(first, "font_size", 0) or 0)
    font_b = float(getattr(second, "font_size", 0) or 0)
    if min(font_a, font_b) <= 0 or max(font_a, font_b) / min(font_a, font_b) > _MAX_FONT_RATIO:
        return False
    angle_a = float(getattr(first, "angle", 0) or 0)
    angle_b = float(getattr(second, "angle", 0) or 0)
    angle_delta = abs((angle_a - angle_b + 90.0) % 180.0 - 90.0)
    if angle_delta > _MAX_ANGLE_DELTA:
        return False

    x1, y1, x2, y2 = [float(value) for value in first.xyxy]
    x3, y3, x4, y4 = [float(value) for value in second.xyxy]
    font = min(font_a, font_b)
    if direction.startswith("h"):
        gap = max(0.0, y3 - y2, y1 - y4)
        overlap = max(0.0, min(x2, x4) - max(x1, x3))
        shorter_span = max(1.0, min(x2 - x1, x4 - x3))
        aligned = abs(x1 - x3) <= font and overlap / shorter_span >= _MIN_ALIGNMENT_OVERLAP
    else:
        rtl = direction == "v" or direction.endswith("r")
        gap = max(0.0, x1 - x4 if rtl else x3 - x2)
        overlap = max(0.0, min(y2, y4) - max(y1, y3))
        shorter_span = max(1.0, min(y2 - y1, y4 - y3))
        aligned = abs(y1 - y3) <= font and overlap / shorter_span >= _MIN_ALIGNMENT_OVERLAP
    return aligned and gap <= font * _MAX_GAP_FONT_UNITS


def _merge_regions(members, base_region=None):
    base_region = base_region or members[0][1]
    first = copy.copy(base_region)
    for name, descriptor in vars(type(first)).items():
        if isinstance(descriptor, cached_property):
            first.__dict__.pop(name, None)

    ordered = [region for _, region in members]
    first.lines = np.concatenate([np.asarray(region.lines) for region in ordered])
    first.texts = [text for region in ordered for text in (getattr(region, "texts", None) or [region.text])]
    first.text = "\n".join(str(region.text or "") for region in ordered)
    first.text_raw = "\n".join(str(getattr(region, "text_raw", region.text) or "") for region in ordered)
    source_ids = []
    source_regions = []
    seen_ids = set()
    seen_sources = set()
    for index, region in members:
        region_id = str(getattr(region, "region_id", None) or f"region_{index}")
        ids = getattr(region, "source_region_ids", None) or [region_id]
        for source_id in ids:
            source_id = str(source_id)
            if source_id not in seen_ids:
                source_ids.append(source_id)
                seen_ids.add(source_id)
        sources = getattr(region, "source_regions", None) or [_source_region_snapshot(region, index)]
        for source in sources:
            source = copy.deepcopy(source)
            source_id = str(source.get("id", f"source_{index}"))
            if source_id not in seen_sources:
                source_regions.append(source)
                seen_sources.add(source_id)

    first.source_region_ids = source_ids
    first.source_regions = source_regions
    first.group_members = list(source_ids)
    owner_id = str(getattr(base_region, "region_id", None) or source_ids[0])
    first.group_id = f"paragraph_{owner_id}"
    first.region_id = first.group_id
    first.source_text_snapshot = first.text
    first.source_geometry = {
        "polygons": first.lines.tolist(),
        "bbox": np.asarray(first.xyxy).tolist(),
        "centroid": np.asarray(first.center).astype(float).tolist(),
    }
    first._bounding_rect = None
    return min(index for index, _ in members), {index for index, _ in members}, first


def _polygon_footprint(region):
    polygons = [np.asarray(line, dtype=np.int32) for line in region.lines]
    x, y, width, height = cv2.boundingRect(np.concatenate(polygons))
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [polygon - (x, y) for polygon in polygons], 1)
    return x, y, mask, int(np.count_nonzero(mask))


def _polygon_coverage(container, candidate) -> float:
    ax, ay, a_mask, _ = container
    bx, by, b_mask, b_area = candidate
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + a_mask.shape[1], bx + b_mask.shape[1]), min(ay + a_mask.shape[0], by + b_mask.shape[0])
    if x1 >= x2 or y1 >= y2 or not b_area:
        return 0.0
    overlap = np.count_nonzero(
        a_mask[y1 - ay:y2 - ay, x1 - ax:x2 - ax]
        & b_mask[y1 - by:y2 - by, x1 - bx:x2 - bx]
    )
    return overlap / b_area


def _coalesce_nested_free_text_regions(regions):
    footprints = {
        index: _polygon_footprint(region)
        for index, region in enumerate(regions)
        if _is_bubble_free(region) and str(getattr(region, "text", "") or "").strip()
    }
    retained = []
    suppressed = {}
    for index in sorted(footprints, key=lambda i: (-footprints[i][3], i)):
        owner = next(
            (candidate for candidate in retained
             if _polygon_coverage(footprints[candidate], footprints[index]) >= 0.9),
            None,
        )
        if owner is None:
            retained.append(index)
        else:
            suppressed[index] = owner

    result = []
    for index, region in enumerate(regions):
        if index in suppressed:
            continue
        losers = [i for i, owner in suppressed.items() if owner == index]
        if not losers:
            result.append(region)
            continue
        winner = copy.copy(region)
        winner.source_region_ids = list(dict.fromkeys(
            [str(value) for member in [region, *(regions[i] for i in sorted(losers))]
             for value in (getattr(member, "source_region_ids", None) or [member.region_id])]
        ))
        winner.source_regions = [_source_region_snapshot(region, index)]
        winner.group_members = list(winner.source_region_ids)
        result.append(winner)
    return result


def coalesce_free_text_regions(regions, image):
    """Merge nested duplicate free-text detections and adjacent aligned lines within a panel."""
    regions = list(regions or [])
    if len(regions) < 2:
        return regions
    regions = _coalesce_nested_free_text_regions(regions)
    if len(regions) < 2 or image is None:
        return regions

    panels = infer_panel_constraints(image, regions)
    merged = []
    for orientation in ("h", "v"):
        ordered = sorted(enumerate(regions), key=lambda item: _reading_key(item[1], orientation))
        run = []
        for current in ordered:
            same_flow = _direction(current[1]).startswith(orientation)
            if (
                run
                and same_flow
                and _direction(run[-1][1]).startswith(orientation)
                and _can_coalesce(run[-1][1], current[1], panels)
            ):
                run.append(current)
            else:
                if len(run) > 1:
                    merged.append(_merge_regions(run))
                run = [current]
        if len(run) > 1:
            merged.append(_merge_regions(run))

    if not merged:
        return regions
    groups = {first: (indexes, region) for first, indexes, region in merged}
    skipped = {index for _, indexes, _ in merged for index in indexes if index not in groups}
    return [
        groups[index][1] if index in groups else region
        for index, region in enumerate(regions)
        if index not in skipped
    ]
