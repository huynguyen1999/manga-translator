"""Page-level masks shared by bubble and free-text placement."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

from .models import FreeTextDamageTarget, FreeTextZone, PageObstacleMap, PlacementMode


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
    regions: List[Any], obstacles: PageObstacleMap, inpaint_mask: np.ndarray | None = None
) -> Dict[int, FreeTextZone]:
    """Create disjoint source-anchored free-text zones without solving typography."""
    free_regions = [
        region for region in regions or []
        if getattr(region, "placement_mode", None) is PlacementMode.FREE_TEXT
    ]
    if not free_regions:
        return {}
    shape = obstacles.panel_mask.shape[:2]
    seeds = [_region_source_mask(region, shape) > 0 for region in free_regions]
    distances = [
        cv2.distanceTransform((~seed).astype(np.uint8), cv2.DIST_L2, 5) for seed in seeds
    ]
    owner = np.argmin(np.stack(distances, axis=0), axis=0)
    forbidden = (obstacles.protected_bubble_mask > 0) | (obstacles.panel_mask == 0)
    raw_damage = (np.asarray(inpaint_mask) > 0) if inpaint_mask is not None else np.zeros(shape, bool)
    zones: Dict[int, FreeTextZone] = {}
    for index, region in enumerate(free_regions):
        source = seeds[index]
        ownership = (owner == index) & ~forbidden
        damage = raw_damage & ownership
        if not np.any(damage):
            damage = source & ownership
        other_text = (obstacles.text_mask > 0) & ~source
        obstacle_mask = forbidden | other_text | ~ownership
        coverable = damage & ~forbidden & ~other_text
        distance = cv2.distanceTransform(damage.astype(np.uint8), cv2.DIST_L2, 5)
        max_distance = float(distance.max())
        weights = np.where(damage, 1.0 + 1.5 * distance / max_distance, 0.0) if max_distance else damage.astype(np.float32)
        core = damage & ((distance / max_distance) >= 0.5 if max_distance else True)
        ys, xs = np.nonzero(source)
        source_bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else (0, 0, shape[1], shape[0])
        ys, xs = np.nonzero(damage)
        damage_bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else source_bbox
        target = source | damage
        tys, txs = np.nonzero(target)
        target_bbox = (int(txs.min()), int(tys.min()), int(txs.max()) + 1, int(tys.max()) + 1) if len(txs) else source_bbox
        source_centroid = tuple(float(value) for value in (np.mean(np.nonzero(source)[1]), np.mean(np.nonzero(source)[0]))) if np.any(source) else (0.0, 0.0)
        damage_centroid = tuple(float(value) for value in (np.mean(np.nonzero(damage)[1]), np.mean(np.nonzero(damage)[0]))) if np.any(damage) else source_centroid
        target_centroid = tuple(float(value) for value in (np.mean(txs), np.mean(tys))) if len(txs) else source_centroid
        damage_target = FreeTextDamageTarget(
            mask=target.astype(np.uint8), centroid_x=target_centroid[0], centroid_y=target_centroid[1],
            bbox=target_bbox, area=int(np.count_nonzero(target)), width=target_bbox[2] - target_bbox[0],
            height=target_bbox[3] - target_bbox[1], source_centroid=source_centroid,
            source_bbox=source_bbox, inpaint_bbox=damage_bbox, inpaint_centroid=damage_centroid,
        )
        zone = FreeTextZone(
            source_bbox=source_bbox, ownership_mask=ownership.astype(np.uint8),
            obstacle_mask=obstacle_mask.astype(np.uint8), coverage_target_mask=damage.astype(np.uint8),
            coverable_damage_mask=coverable.astype(np.uint8), coverage_weight_map=weights.astype(np.float32),
            core_damage_mask=core.astype(np.uint8), total_coverable_weight=max(1.0, float(weights[coverable].sum())),
            total_core_coverable=int(np.count_nonzero(core & coverable)), damage_target=damage_target,
        )
        region._free_text_zone = zone
        zones[id(region)] = zone
    return zones
