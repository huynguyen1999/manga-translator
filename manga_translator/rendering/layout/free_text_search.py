"""Constraint checks and placement search helpers for free-text layouts."""

from __future__ import annotations

import math
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .free_text_typography import (
    _free_text_candidate_ink_metrics,
    _free_text_line_height,
    _free_text_line_spacing,
    _free_text_typography_score,
    _free_text_words,
    _free_text_wrap_candidate,
)
from .joint_layout import _candidate_bbox
from .models import (
    BandSlot,
    CandidateRaster,
    FreeTextDamageTarget,
    FreeTextZone,
    LayoutCandidate,
    OriginalLayoutProfile,
    PageObstacleMap,
    PanelConstraint,
    PlacedLine,
    SearchResult,
)
from .profiling import get_solver_profile
from .raster import _candidate_cropped_visual_masks, _candidate_raster_at_offset


def _measure_damage_coverage_crop(
    crop_box: Tuple[int, int, int, int],
    vis_crop: np.ndarray,
    ink_crop: np.ndarray,
    block_crop: np.ndarray,
    zone: FreeTextZone,
) -> Dict[str, float]:
    total_w = zone.total_coverable_weight
    if total_w <= 0.0:
        return {
            "c_ink": 1.0,
            "c_visual": 1.0,
            "c_block": 1.0,
            "c_damage": 1.0,
            "c_core": 1.0,
            "u_damage": 0.0,
            "cleanup_mask_coverage": 1.0,
        }

    cx1, cy1, cx2, cy2 = crop_box
    cov_crop = (zone.coverable_damage_mask[cy1:cy2, cx1:cx2] > 0)
    w_crop = zone.coverage_weight_map[cy1:cy2, cx1:cx2]

    cov_ink = float(np.sum(w_crop[cov_crop & ink_crop])) / total_w
    cov_visual = float(np.sum(w_crop[cov_crop & vis_crop])) / total_w
    cov_block = float(np.sum(w_crop[cov_crop & block_crop])) / total_w

    # Cleanup owns source-pixel removal; glyph coverage is placement-only telemetry.
    c_damage = 1.0

    core_total = zone.total_core_coverable
    if core_total > 0:
        core_crop = (zone.core_damage_mask[cy1:cy2, cx1:cx2] > 0) & cov_crop
        c_core = float(np.sum(ink_crop & core_crop)) / core_total
    else:
        c_core = cov_visual

    return {
        "c_ink": cov_ink,
        "c_visual": cov_visual,
        "c_block": cov_block,
        "c_damage": c_damage,
        "c_core": c_core,
        "u_damage": 0.0,
        "cleanup_mask_coverage": c_damage,
    }



from .free_text_typography import (
    _FREE_TEXT_MAX_LINE_SPACING,
    _free_text_candidate_ink_metrics,
    _free_text_font_metrics,
    _free_text_line_height,
    _free_text_line_spacing,
    _free_text_typography_score,
    _free_text_words,
    _free_text_wrap_candidate,
    _mask_metrics,
)


def _free_text_shift_candidate(candidate: LayoutCandidate, dx: int, dy: int) -> LayoutCandidate:
    return LayoutCandidate(
        font_size=candidate.font_size,
        y_origin=candidate.y_origin + dy,
        line_spacing=candidate.line_spacing,
        lines=[
            PlacedLine(
                text=line.text,
                y=line.y + dy,
                x=line.x + dx,
                width=line.width,
                height=line.height,
                slot=BandSlot(
                    left=line.slot.left + dx,
                    right=line.slot.right + dx,
                    y_start=line.slot.y_start + dy,
                    y_end=line.slot.y_end + dy,
                ),
            )
            for line in candidate.lines
        ],
        penalty=candidate.penalty,
        glyph_clearance_p5=candidate.glyph_clearance_p5,
        status="free_text",
        valid=True,
        layout_bounds=tuple(value + delta for value, delta in zip(candidate.layout_bounds, (dx, dy, dx, dy))) if candidate.layout_bounds is not None else None,
        qa=dict(candidate.qa),
    )


def _free_text_offset_search(max_radius: int) -> List[Tuple[int, int]]:
    """Yield offset sequence for rapid basin discovery."""
    offsets: List[Tuple[int, int]] = []
    for radius in (0, 2, 4, 8, 12, 16, 24, 32, 48, 64):
        if radius > max_radius:
            continue
        points = [(0, 0)] if radius == 0 else [
            (radius, 0), (-radius, 0), (0, radius), (0, -radius),
            (radius, radius), (radius, -radius), (-radius, radius), (-radius, -radius),
        ]
        for point in points:
            if point not in offsets:
                offsets.append(point)
    return offsets


def _free_text_offset_refine(center_dx: int, center_dy: int, step: int = 2) -> List[Tuple[int, int]]:
    """Local fine refinement around a promising coarse basin."""
    offsets: List[Tuple[int, int]] = []
    for dx in range(center_dx - step * 2, center_dx + step * 2 + 1, step):
        for dy in range(center_dy - step * 2, center_dy + step * 2 + 1, step):
            offsets.append((dx, dy))
    return offsets


def _clamp_free_text_translation(
    block_bbox: Tuple[int, int, int, int],
    dx: int,
    dy: int,
    panel: Optional[PanelConstraint],
) -> Optional[Tuple[int, int]]:
    """Keep the full paragraph box inside its panel while preserving its desired center when possible."""
    if panel is None:
        return dx, dy
    left, top, right, bottom = panel.bounds
    margin = max(0, int(panel.margin))
    left, top, right, bottom = left + margin, top + margin, right - margin, bottom - margin
    bx1, by1, bx2, by2 = block_bbox
    if bx2 - bx1 > right - left or by2 - by1 > bottom - top:
        return None
    min_dx, max_dx = left - bx1, right - bx2
    min_dy, max_dy = top - by1, bottom - by2
    return min(max(dx, min_dx), max_dx), min(max(dy, min_dy), max_dy)


def _free_text_hard_valid(
    crop_box: Tuple[int, int, int, int],
    visual_crop: np.ndarray,
    zone: FreeTextZone,
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
    block_bbox: Optional[Tuple[int, int, int, int]] = None,
) -> bool:
    """Reject page, bubble, and foreign-text collisions for a free-text block.

    ``ownership_mask`` partitions damage between regions; it is deliberately not
    a placement boundary because translated text can be wider than its source.
    """
    x1, y1, x2, y2 = crop_box
    h_obs, w_obs = obstacles.panel_mask.shape[:2]
    if x1 < 0 or y1 < 0 or x2 > w_obs or y2 > h_obs:
        return False
    if not visual_crop.any():
        return False

    panel = zone.panel_constraint
    if panel is not None:
        left, top, right, bottom = panel.bounds
        margin = max(0, int(panel.margin))
        safe_bounds = (left + margin, top + margin, right - margin, bottom - margin)
        if block_bbox is not None:
            bx1, by1, bx2, by2 = block_bbox
            if bx1 < safe_bounds[0] or by1 < safe_bounds[1] or bx2 > safe_bounds[2] or by2 > safe_bounds[3]:
                return False
        ys, xs = np.nonzero(visual_crop)
        if len(xs):
            vx1, vy1 = x1 + int(xs.min()), y1 + int(ys.min())
            vx2, vy2 = x1 + int(xs.max()) + 1, y1 + int(ys.max()) + 1
            if vx1 < safe_bounds[0] or vy1 < safe_bounds[1] or vx2 > safe_bounds[2] or vy2 > safe_bounds[3]:
                return False
        if panel.mask is not None and panel.mask.shape == obstacles.panel_mask.shape:
            panel_crop = panel.mask[y1:y2, x1:x2] > 0
            if np.any(visual_crop & ~panel_crop):
                return False

    # Quick check: if the entire crop_box has no obstacles, it's valid immediately
    bubble_sub = obstacles.protected_bubble_mask[y1:y2, x1:x2]
    other_sub = other_text[y1:y2, x1:x2]
    panel_sub = obstacles.panel_mask[y1:y2, x1:x2]

    # If obstacles exist in sub-window, test against visual crop
    if np.any(bubble_sub):
        if np.any(visual_crop & (bubble_sub > 0)):
            return False
    if np.any(other_sub):
        if np.any(visual_crop & other_sub):
            return False
    if not np.all(panel_sub > 0):
        if np.any(visual_crop & ~(panel_sub > 0)):
            return False

    return True


def _free_text_footprint_qa(
    profile: OriginalLayoutProfile,
    target: Optional[FreeTextDamageTarget],
    ink_bbox: Tuple[int, int, int, int],
) -> Dict[str, Any]:
    """Describe the selected glyph footprint against its source/inpaint target."""
    x1, y1, x2, y2 = ink_bbox
    ink_width = max(0, x2 - x1)
    ink_height = max(0, y2 - y1)
    target_bbox = target.bbox if target is not None else profile.bbox
    target_width = max(1, target_bbox[2] - target_bbox[0])
    target_height = max(1, target_bbox[3] - target_bbox[1])
    return {
        "source_bbox": list(profile.bbox),
        "inpaint_bbox": list(target.inpaint_bbox) if target is not None else list(profile.bbox),
        "target_bbox": list(target_bbox),
        "target_centroid": [target.centroid_x, target.centroid_y] if target is not None else [profile.centroid[0], profile.centroid[1]],
        "inpaint_centroid": list(target.inpaint_centroid) if target is not None else [profile.centroid[0], profile.centroid[1]],
        "target_width": target_width,
        "target_height": target_height,
        "target_area": int(target.area) if target is not None else int(profile.block_width * profile.block_height),
        "target_aspect_ratio": target_width / max(1.0, float(target_height)),
        "ink_bbox": [x1, y1, x2, y2],
        "ink_width": ink_width,
        "ink_height": ink_height,
        "ink_aspect_ratio": ink_width / max(1.0, float(ink_height)),
        "footprint_width": ink_width,
        "footprint_height": ink_height,
        "footprint_area_ratio": (ink_width * ink_height) / max(1.0, target_width * target_height),
    }


def _free_text_ink_overflow(
    candidate: LayoutCandidate,
    image_shape: Tuple[int, int],
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
) -> float:
    """Measure glyph pixels outside the legal page/obstacle area."""
    stats = get_solver_profile()
    stats.overflow_checks += 1
    stats.overflow_rasterizations += 1
    crop_box, glyph, _, _ = _candidate_cropped_visual_masks(candidate, 0, image_shape)
    return _free_text_ink_overflow_in_crop(crop_box, glyph, obstacles, other_text)


def _free_text_ink_overflow_from_raster(
    raster: CandidateRaster,
    destination_crop_box: Tuple[int, int, int, int],
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
) -> float:
    """Measure overflow by translating a cached raster, without rerasterizing."""
    profile = get_solver_profile()
    profile.overflow_checks += 1
    dx = destination_crop_box[0] - raster.crop_box[0]
    dy = destination_crop_box[1] - raster.crop_box[1]
    crop_box, glyph, _, _ = _candidate_raster_at_offset(
        raster, dx, dy, obstacles.panel_mask.shape[:2]
    )
    return _free_text_ink_overflow_in_crop(crop_box, glyph, obstacles, other_text)


def _free_text_ink_overflow_in_crop(
    crop_box: Tuple[int, int, int, int],
    glyph: np.ndarray,
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
) -> float:
    total = int(np.count_nonzero(glyph))
    if not total:
        return 1.0
    x1, y1, x2, y2 = crop_box
    allowed = (
        (obstacles.panel_mask[y1:y2, x1:x2] > 0)
        & ~(obstacles.protected_bubble_mask[y1:y2, x1:x2] > 0)
        & ~other_text[y1:y2, x1:x2]
    )
    return float(np.count_nonzero(glyph & ~allowed)) / total


def _free_text_search_result(
    typography_candidate: LayoutCandidate,
    raster: CandidateRaster,
    base_box: Tuple[int, int, int, int],
    ink_crop: np.ndarray,
    visual_crop: np.ndarray,
    block_crop: np.ndarray,
    base_centroid: Tuple[float, float],
    base_ink_bbox: Tuple[int, int, int, int],
    ideal_dx: int,
    ideal_dy: int,
    relative_dx: int,
    relative_dy: int,
    original_profile: OriginalLayoutProfile,
    damage_centroid: Tuple[float, float],
    target_width: float,
    target_height: float,
    zone: FreeTextZone,
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
) -> Optional[SearchResult]:
    stats = get_solver_profile()
    stats.free_text_offsets_tested += 1
    stats.candidate_raster_cache_hits += 1
    stats.free_text_candidates_geometry_evaluated += 1
    dx, dy = ideal_dx + relative_dx, ideal_dy + relative_dy
    x1, y1, x2, y2 = base_box
    crop_box = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
    bx1, by1, bx2, by2 = _candidate_bbox(typography_candidate)
    block_bbox = (bx1 + dx, by1 + dy, bx2 + dx, by2 + dy)
    started = perf_counter()
    valid = _free_text_hard_valid(crop_box, visual_crop, zone, obstacles, other_text, block_bbox)
    stats.ft_offset_search_ms += (perf_counter() - started) * 1000.0
    if not valid:
        return None
    stats.free_text_hard_valid_hits += 1

    started = perf_counter()
    coverage = _measure_damage_coverage_crop(crop_box, visual_crop, ink_crop, block_crop, zone)
    stats.ft_coverage_ms += (perf_counter() - started) * 1000.0
    if coverage["c_block"] < 0.10:
        return None
    actual_centroid = (base_centroid[0] + dx, base_centroid[1] + dy)
    center_dx = actual_centroid[0] - damage_centroid[0]
    center_dy = actual_centroid[1] - damage_centroid[1]
    center_penalty = (
        (relative_dx / max(1.0, float(max(target_width, original_profile.block_width)))) ** 2
        + (relative_dy / max(1.0, float(max(target_height, original_profile.block_height)))) ** 2
    )
    source_drift = abs(actual_centroid[0] - original_profile.centroid[0]) / max(1.0, original_profile.block_width)
    score = (
        typography_candidate.penalty
        + center_penalty * 100.0
        + source_drift * 3.0
        - coverage["c_block"] * 18.0
        - coverage["c_visual"] * 4.0
    )
    ink_bbox = tuple(value + delta for value, delta in zip(base_ink_bbox, (dx, dy, dx, dy)))
    return SearchResult(
        typography_candidate, raster, dx, dy, relative_dx, relative_dy,
        score, coverage, actual_centroid, center_dx, center_dy, ink_bbox,
    )
