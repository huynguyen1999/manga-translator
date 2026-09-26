"""Source-anchored ownership zones for non-bubble text placement."""

from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from manga_translator.geometry.panels import infer_panel_constraints
from .geometry import _mask_moments
from .models import FreeTextDamageTarget, FreeTextZone, PageObstacleMap, PanelConstraint, PlacementMode
from .obstacles import _region_source_mask
from .source_profile import _effective_source_font_size


def _extract_region_damage_masks(
    regions: List[Any],
    shape: Tuple[int, int],
    inpaint_mask: Optional[np.ndarray] = None,
) -> Dict[int, np.ndarray]:
    """Assign captured inpaint/text-removal mask pixels to individual text regions."""
    h, w = shape[:2]
    free_regions = [
        r for r in regions or []
        if getattr(r, "placement_mode", None) is PlacementMode.FREE_TEXT
    ]
    if not free_regions:
        return {}

    source_masks = [_region_source_mask(r, (h, w)) > 0 for r in free_regions]
    if inpaint_mask is not None and np.any(inpaint_mask):
        raw_mask = (inpaint_mask > 0).astype(np.uint8)
        if raw_mask.shape[:2] != (h, w):
            raw_mask = cv2.resize(raw_mask, (w, h), interpolation=cv2.INTER_NEAREST)
    else:
        raw_mask = np.zeros((h, w), dtype=np.uint8)

    # Scope the shared mask around each source region before assigning ownership.
    # Otherwise one free-text region would claim every unrelated erased pixel on the page.
    scopes = []
    distances = []
    for region, source in zip(free_regions, source_masks):
        profile = getattr(region, "_source_profile", None)
        reported_font = profile.font_size if profile is not None else getattr(region, "source_font_size", None) or getattr(region, "font_size", 12)
        font_s = max(8.0, float(_effective_source_font_size(region, reported_font)))
        radius = max(3, int(round(font_s * 1.5)))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        scopes.append(cv2.dilate(source.astype(np.uint8), kernel) > 0)
        distances.append(cv2.distanceTransform((~source).astype(np.uint8), cv2.DIST_L2, 5))
    scope_union = np.any(np.stack(scopes, axis=0), axis=0)
    owner = np.argmin(np.stack(distances, axis=0), axis=0)
    active_damage = (raw_mask > 0) & scope_union

    region_damages: Dict[int, np.ndarray] = {}
    for idx, r in enumerate(free_regions):
        claimed = (owner == idx) & active_damage & scopes[idx]
        # Keep the exact pixels sent to inpainting. Source geometry is only a fallback
        # for legacy captures that did not persist a mask.
        claimed |= source_masks[idx] if not np.any(raw_mask) else False
        region_damages[id(r)] = claimed.astype(np.uint8)

    return region_damages


def build_free_text_ownership_zones(
    regions: List[Any],
    obstacles: PageObstacleMap,
    inpaint_mask: Optional[np.ndarray] = None,
    image: Optional[np.ndarray] = None,
    other_regions: Optional[List[Any]] = None,
) -> Dict[int, FreeTextZone]:
    """Assign all free-text seeds simultaneously to disjoint FreeTextZones."""
    free_regions = [
        region for region in regions or []
        if getattr(region, "placement_mode", None) is PlacementMode.FREE_TEXT
    ]
    if not free_regions:
        return {}

    shape = obstacles.panel_mask.shape[:2]
    h, w = shape
    panel_constraints = (
        infer_panel_constraints(image, free_regions, other_regions=other_regions or regions)
        if image is not None
        else {id(region): PanelConstraint("page", (0, 0, w, h)) for region in free_regions}
    )
    forbidden_global = (
        (obstacles.protected_bubble_mask > 0)
        | (obstacles.panel_mask == 0)
    )
    available = (obstacles.panel_mask > 0) & ~forbidden_global

    seeds = [_region_source_mask(region, shape) > 0 for region in free_regions]
    distances = [
        cv2.distanceTransform((~seed).astype(np.uint8), cv2.DIST_L2, 5)
        for seed in seeds
    ]
    owner = np.argmin(np.stack(distances, axis=0), axis=0)
    damage_dict = _extract_region_damage_masks(free_regions, shape, inpaint_mask=inpaint_mask)

    zones: Dict[int, FreeTextZone] = {}
    for index, region in enumerate(free_regions):
        rid = id(region)
        ownership = (owner == index) & available
        raw_damage = damage_dict.get(rid, seeds[index].astype(np.uint8)) > 0
        damage_mask = raw_damage & (owner == index)  # Don't let regions fight over damage
        if not np.any(damage_mask):
            damage_mask = seeds[index].copy()

        # Build distance transform & weight map over damage
        damage_dt = cv2.distanceTransform(damage_mask.astype(np.uint8), cv2.DIST_L2, 5)
        max_dt = float(damage_dt.max())
        if max_dt > 0.0:
            norm_dt = damage_dt / max_dt
            weight_map = np.where(damage_mask, 1.0 + 1.5 * norm_dt, 0.0)
            core_mask = damage_mask & (norm_dt >= 0.50)
        else:
            weight_map = np.where(damage_mask, 1.0, 0.0)
            core_mask = damage_mask.copy()

        # Other text obstacles (all other sources)
        other_text_mask = obstacles.text_mask.astype(bool) & ~seeds[index]
        obstacle_mask = forbidden_global | other_text_mask | ~ownership
        coverable_damage = damage_mask & ownership & ~forbidden_global & ~other_text_mask

        # Precompute total weights for instant local cropped coverage checks
        core_m = core_mask > 0
        core_coverable = core_m & coverable_damage
        core_total = int(np.sum(core_coverable))
        total_w = float(np.sum(weight_map[coverable_damage]))
        if total_w <= 0.0:
            total_w = float(np.sum(coverable_damage))
        if total_w <= 0.0:
            total_w = 1.0

        # Derive source bounding box
        ys, xs = np.nonzero(seeds[index])
        if len(xs):
            s_bbox = (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1))
        else:
            s_bbox = (0, 0, w, h)

        damage_centroid, _ = _mask_moments(damage_mask)
        source_centroid, _ = _mask_moments(seeds[index])
        dys, dxs = np.nonzero(damage_mask)
        if len(dxs):
            d_bbox = (int(dxs.min()), int(dys.min()), int(dxs.max() + 1), int(dys.max() + 1))
        else:
            d_bbox = s_bbox
        target_mask = (damage_mask > 0) | seeds[index]
        target_centroid, _ = _mask_moments(target_mask)
        tys, txs = np.nonzero(target_mask)
        target_bbox = (
            (int(txs.min()), int(tys.min()), int(txs.max() + 1), int(tys.max() + 1))
            if len(txs) else s_bbox
        )
        damage_target = FreeTextDamageTarget(
            mask=target_mask.astype(np.uint8),
            centroid_x=float(target_centroid[0]),
            centroid_y=float(target_centroid[1]),
            bbox=target_bbox,
            area=int(np.count_nonzero(target_mask)),
            width=max(0, target_bbox[2] - target_bbox[0]),
            height=max(0, target_bbox[3] - target_bbox[1]),
            source_centroid=(float(source_centroid[0]), float(source_centroid[1])),
            source_bbox=s_bbox,
            inpaint_bbox=d_bbox,
            inpaint_centroid=(float(damage_centroid[0]), float(damage_centroid[1])),
        )

        ft_zone = FreeTextZone(
            source_bbox=s_bbox,
            ownership_mask=ownership.astype(np.uint8),
            obstacle_mask=obstacle_mask.astype(np.uint8),
            coverage_target_mask=damage_mask.astype(np.uint8),
            coverable_damage_mask=coverable_damage.astype(np.uint8),
            coverage_weight_map=weight_map.astype(np.float32),
            core_damage_mask=core_mask.astype(np.uint8),
            total_coverable_weight=total_w,
            total_core_coverable=core_total,
            damage_target=damage_target,
            panel_constraint=panel_constraints.get(rid),
        )
        zones[rid] = ft_zone
        region._panel_constraint = ft_zone.panel_constraint
        region._free_text_source_mask = seeds[index].astype(np.uint8)
        region._free_text_inpaint_mask = damage_mask.astype(np.uint8)
        region._free_text_ownership_mask = ownership.astype(np.uint8)
        region._free_text_zone = ft_zone

    return zones
