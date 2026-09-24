"""Page-level masks shared by bubble and free-text placement."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

from .models import FreeTextZone, PageObstacleMap, PlacementMode


def _region_source_mask(region: Any, shape: Tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    raw_lines = np.asarray(getattr(region, "lines", []))
    if raw_lines.size:
        if raw_lines.ndim == 2:
            raw_lines = raw_lines[None, ...]
        polygons = [np.asarray(line, dtype=np.int32).reshape(-1, 2) for line in raw_lines]
        cv2.fillPoly(mask, [polygon for polygon in polygons if len(polygon) >= 3], 1)
    if not np.any(mask):
        bounds = getattr(region, "xyxy", None)
        if bounds is None:
            bounds = getattr(region, "layout_bounds", None)
        if bounds is not None and len(bounds) == 4:
            x1, y1, x2, y2 = [int(round(value)) for value in bounds]
            mask[max(0, y1):min(shape[0], y2), max(0, x1):min(shape[1], x2)] = 1
    return mask


def classify_placement_modes(regions: List[Any]) -> List[Any]:
    """Freeze BUBBLE/FREE_TEXT after bubble association."""
    for index, region in enumerate(regions or []):
        assigned = getattr(region, "_bubble_interior", None)
        if assigned is None or not np.any(assigned):
            assigned = getattr(region, "_bubble_mask", None)
        mode = PlacementMode.BUBBLE if assigned is not None and np.any(assigned) else PlacementMode.FREE_TEXT
        region.placement_mode = mode
        region._placement_mode = mode.value
        region._placement_debug_label = f"region {index} → {mode.value}"
    return regions


def build_page_obstacle_map(
    regions: List[Any], image_shape: Tuple[int, int], bubble_halo: int = 4
) -> PageObstacleMap:
    h, w = image_shape[:2]
    bubble_mask = np.zeros((h, w), dtype=np.uint8)
    text_mask = np.zeros((h, w), dtype=np.uint8)
    for region in regions or []:
        text_mask = cv2.bitwise_or(text_mask, _region_source_mask(region, (h, w)))
        if getattr(region, "placement_mode", None) is not PlacementMode.BUBBLE:
            continue
        assigned = getattr(region, "_bubble_mask", None)
        if assigned is None:
            assigned = getattr(region, "_bubble_interior", None)
        if assigned is None or not np.any(assigned):
            continue
        assigned = np.asarray(assigned)
        if assigned.shape != (h, w):
            assigned = cv2.resize(assigned.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
        bubble_mask = cv2.bitwise_or(bubble_mask, (assigned > 0).astype(np.uint8))
    if bubble_halo > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (bubble_halo * 2 + 1, bubble_halo * 2 + 1)
        )
        protected = cv2.dilate(bubble_mask, kernel)
    else:
        protected = bubble_mask.copy()
    return PageObstacleMap(
        bubble_mask=bubble_mask,
        protected_bubble_mask=protected,
        text_mask=text_mask,
        panel_mask=np.ones((h, w), dtype=np.uint8),
    )


def build_free_text_ownership_zones(
    regions: List[Any],
    obstacles: PageObstacleMap,
    inpaint_mask: np.ndarray | None = None,
    image: np.ndarray | None = None,
    other_regions: List[Any] | None = None,
) -> Dict[int, FreeTextZone]:
    """Create source-anchored free-text zones using the shared ownership solver."""
    from .ownership import build_free_text_ownership_zones as build_zones

    return build_zones(
        regions, obstacles, inpaint_mask=inpaint_mask, image=image, other_regions=other_regions
    )
