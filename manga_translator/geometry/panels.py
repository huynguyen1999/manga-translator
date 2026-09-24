"""Conservative axis-aligned panel constraints inferred from manga frame lines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

import cv2
import numpy as np

from manga_translator.rendering.layout.models import PanelConstraint


@dataclass(frozen=True)
class _Boundary:
    axis: str
    span_start: int
    span_end: int
    coord_start: int
    coord_end: int
    confidence: float


def _source_mask(region: Any, shape: Tuple[int, int]) -> np.ndarray:
    height, width = shape
    mask = np.zeros(shape, dtype=np.uint8)
    lines = np.asarray(getattr(region, "lines", []))
    if lines.size:
        if lines.ndim == 2:
            lines = lines[None, ...]
        polygons = [np.asarray(line, dtype=np.int32).reshape(-1, 2) for line in lines]
        cv2.fillPoly(mask, [polygon for polygon in polygons if len(polygon) >= 3], 1)
    if not np.any(mask):
        bounds = getattr(region, "xyxy", None)
        if bounds is None:
            bounds = getattr(region, "layout_bounds", None)
        if bounds is not None and len(bounds) == 4:
            x1, y1, x2, y2 = [int(round(value)) for value in bounds]
            mask[max(0, y1):min(height, y2), max(0, x1):min(width, x2)] = 1
    return mask


def _detect_boundaries(image: np.ndarray, regions: Iterable[Any]) -> List[_Boundary]:
    height, width = image.shape[:2]
    if image.ndim == 2:
        gray = image
    elif image.shape[2] == 1:
        gray = image[:, :, 0]
    else:
        gray = cv2.cvtColor(image[..., :3], cv2.COLOR_RGB2GRAY)
    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    threshold = int(np.clip(otsu, 65, 105))
    dark = (gray <= threshold).astype(np.uint8)

    excluded = np.zeros((height, width), dtype=np.uint8)
    for region in regions:
        excluded |= _source_mask(region, (height, width))
        for name in ("_bubble_mask", "_bubble_interior"):
            bubble = getattr(region, name, None)
            if bubble is None:
                continue
            bubble = np.asarray(bubble)
            if bubble.shape[:2] != (height, width):
                bubble = cv2.resize(
                    bubble.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST
                )
            excluded |= (bubble > 0).astype(np.uint8)
    if np.any(excluded):
        excluded = cv2.dilate(excluded, np.ones((5, 5), np.uint8))
        dark[excluded > 0] = 0

    boundaries: List[_Boundary] = []
    for axis, dimension, kernel_size in (
        ("h", width, max(18, int(round(width * 0.035)))),
        ("v", height, max(18, int(round(height * 0.035)))),
    ):
        kernel_shape = (1, kernel_size) if axis == "h" else (kernel_size, 1)
        opened = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones(kernel_shape, np.uint8))
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(opened, 8)
        for label in range(1, count):
            x, y, box_width, box_height, _area = stats[label]
            length, thickness = (box_width, box_height) if axis == "h" else (box_height, box_width)
            if length < max(20, int(round(dimension * 0.035))):
                continue
            if thickness > max(12, int(round(min(height, width) * 0.016))):
                continue

            if axis == "h":
                span_start, span_end = int(x), int(x + box_width)
                coord_start, coord_end = int(y), int(y + box_height)
                support = dark[coord_start:coord_end, span_start:span_end]
                continuity = float(np.mean(np.any(support > 0, axis=0))) if support.size else 0.0
            else:
                span_start, span_end = int(y), int(y + box_height)
                coord_start, coord_end = int(x), int(x + box_width)
                support = dark[span_start:span_end, coord_start:coord_end]
                continuity = float(np.mean(np.any(support > 0, axis=1))) if support.size else 0.0
            if continuity < 0.72:
                continue
            length_score = min(1.0, length / max(1.0, dimension * 0.18))
            confidence = 0.55 * continuity + 0.45 * length_score
            if confidence >= 0.68:
                boundaries.append(_Boundary(axis, span_start, span_end, coord_start, coord_end, confidence))
    return boundaries


def _constraint_for_region(
    region: Any,
    shape: Tuple[int, int],
    boundaries: List[_Boundary],
) -> PanelConstraint:
    height, width = shape
    source = _source_mask(region, shape)
    ys, xs = np.nonzero(source)
    if not len(xs):
        return PanelConstraint("page", (0, 0, width, height))

    sx1, sy1, sx2, sy2 = int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)
    cx, cy = float(xs.mean()), float(ys.mean())
    found: List[_Boundary] = []

    def nearest(axis: str, cross_coordinate: float, source_low: int, source_high: int, side: str, dimension: int, source_extent: int):
        minimum_length = max(24, int(round(dimension * 0.045)), int(round(min(source_extent * 1.2, dimension * 0.18))))
        candidates = []
        for boundary in boundaries:
            if boundary.axis != axis or boundary.span_end - boundary.span_start < minimum_length:
                continue
            if not (boundary.span_start <= cross_coordinate <= boundary.span_end):
                continue
            if side == "before" and boundary.coord_end <= source_low:
                distance = source_low - boundary.coord_end
                candidates.append((distance, boundary))
            elif side == "after" and boundary.coord_start >= source_high:
                distance = boundary.coord_start - source_high
                candidates.append((distance, boundary))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    left = nearest("v", cy, sx1, sx2, "before", height, sy2 - sy1)
    right = nearest("v", cy, sx1, sx2, "after", height, sy2 - sy1)
    top = nearest("h", cx, sy1, sy2, "before", width, sx2 - sx1)
    bottom = nearest("h", cx, sy1, sy2, "after", width, sx2 - sx1)
    found = [boundary for boundary in (left, right, top, bottom) if boundary is not None]

    if left is None and right is None and top is None and bottom is None:
        return PanelConstraint("page", (0, 0, width, height))

    bounds = (
        left.coord_end if left is not None else 0,
        top.coord_end if top is not None else 0,
        right.coord_start if right is not None else width,
        bottom.coord_start if bottom is not None else height,
    )
    if bounds[0] > sx1 or bounds[1] > sy1 or bounds[2] < sx2 or bounds[3] < sy2:
        return PanelConstraint("page", (0, 0, width, height))

    font_size = max(1, int(getattr(region, "font_size", 12) or 12))
    margin = max(2, min(12, int(round(font_size * 0.2))))
    confidence = float(sum(item.confidence for item in found) / len(found))
    panel_id = "cv:" + ":".join(str(value) for value in bounds)
    return PanelConstraint(panel_id, bounds, confidence=confidence, source="cv", margin=margin)


def infer_panel_constraints(
    image: np.ndarray,
    regions: Iterable[Any],
    other_regions: Iterable[Any] | None = None,
) -> Dict[int, PanelConstraint]:
    """Infer one conservative frame envelope per source region, using source geometry only."""
    regions = list(regions or [])
    if image is None or image.ndim < 2:
        return {}
    excluded_regions = list(other_regions or [])
    if not excluded_regions:
        excluded_regions = regions
    else:
        excluded_ids = {id(region) for region in excluded_regions}
        excluded_regions.extend(region for region in regions if id(region) not in excluded_ids)
    boundaries = _detect_boundaries(image, excluded_regions)
    shape = image.shape[:2]
    return {id(region): _constraint_for_region(region, shape, boundaries) for region in regions}


def infer_panel_constraint(
    image: np.ndarray,
    region: Any,
    other_regions: Iterable[Any] | None = None,
) -> PanelConstraint:
    """Infer a panel envelope for one region without consulting translated geometry."""
    return infer_panel_constraints(image, [region], other_regions).get(
        id(region), PanelConstraint("page", (0, 0, image.shape[1], image.shape[0]))
    )
