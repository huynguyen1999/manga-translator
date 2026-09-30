"""Use compositor pixels when accepting and combining free-text candidates."""

import logging
import math
from typing import Optional, Tuple

import numpy as np

from .joint_layout import _candidate_bbox
from .models import LayoutCandidate, PageObstacleMap, SearchResult
from .raster import _candidate_global_glyph_mask
from .compositor_cache import get_candidate_render_box, footprint_cache_key

logger = logging.getLogger("layout.solver")


def filter_renderable_candidates(region, candidates, config, image_shape, zone, obstacles, other_text):
    from ..placement_geometry import _points_for_rect
    from .render_output_validation import _warped_alpha_crop
    from .free_text_search import _free_text_hard_valid
    from .profiling import get_solver_profile

    profile, accepted = get_solver_profile(), []
    for candidate in candidates:
        bounds = tuple(map(int, _candidate_bbox(candidate)))
        box = get_candidate_render_box(region, candidate, config)
        if box is None:
            zone.rejections["rasterization"] = zone.rejections.get("rasterization", 0) + 1
            continue
        points = _points_for_rect(region, bounds, image_shape[1], image_shape[0])
        footprint_key = footprint_cache_key(candidate._render_box_key, bounds, points, image_shape)
        footprint, outside = profile.compositor_crops.get(footprint_key), False
        if footprint is None:
            footprint, outside = _warped_alpha_crop(box, points, image_shape)
            if footprint is not None and not outside:
                profile.compositor_crops[footprint_key] = footprint
        if outside or footprint is None:
            zone.rejections["page_bounds"] = zone.rejections.get("page_bounds", 0) + 1
            continue
        crop, visual = footprint
        if not _free_text_hard_valid(crop, visual, zone, obstacles, other_text, bounds):
            continue
        candidate._render_footprint = footprint
        candidate.qa.update(layout_stroke_width=candidate._render_stroke_width, render_footprint_validated=True)
        accepted.append(candidate)
    return accepted


def stage_render_validator(region, config, image_shape, zone, obstacles, other_text):
    from .free_text_search import _free_text_shift_candidate
    from .search_budget import get_search_budget

    def validate(results):
        accepted = []
        budget = get_search_budget()
        max_validated = 2 if budget is not None and budget.initial else 8
        for result in sorted(results, key=lambda item: item.score):
            candidate = _free_text_shift_candidate(result.typography_candidate, result.dx, result.dy)
            if filter_renderable_candidates(region, [candidate], config, image_shape, zone, obstacles, other_text):
                accepted.append(result)
            if len(accepted) >= max_validated:
                break
        return accepted
    return validate


def log_free_text_shadow_comparison(
    fast: LayoutCandidate | None,
    exhaustive: LayoutCandidate | None,
    image_shape: tuple[int, int],
) -> dict:
    if exhaustive is None:
        logger.info("layout shadow fast_accepted=%s exhaustive=none", fast is not None)
        return {"fast_accepted": fast is not None, "exhaustive_available": False}
    if fast is None:
        logger.info("layout shadow fast_accepted=false exhaustive_score=%.4f", exhaustive.penalty)
        return {
            "fast_accepted": False, "exhaustive_available": True,
            "exhaustive_score": exhaustive.penalty,
        }

    fast_center = fast.qa.get("ink_centroid", (0.0, 0.0))
    full_center = exhaustive.qa.get("ink_centroid", (0.0, 0.0))
    def rendered_mask(candidate):
        footprint = getattr(candidate, "_render_footprint", None)
        if footprint is None:
            return _candidate_global_glyph_mask(candidate, image_shape)
        crop_box, visual = footprint
        mask = np.zeros(image_shape, dtype=bool)
        x1, y1, x2, y2 = crop_box
        mask[y1:y2, x1:x2] = visual > 0
        return mask

    fast_mask = rendered_mask(fast)
    full_mask = rendered_mask(exhaustive)
    union = int(np.count_nonzero(fast_mask | full_mask))
    iou = float(np.count_nonzero(fast_mask & full_mask)) / union if union else 1.0
    fast_area = int(np.count_nonzero(fast_mask))
    full_area = int(np.count_nonzero(full_mask))
    comparison = {
        "fast_accepted": True,
        "exhaustive_available": True,
        "exhaustive_score": exhaustive.penalty,
        "font_delta": fast.font_size - exhaustive.font_size,
        "line_count_delta": len(fast.lines) - len(exhaustive.lines),
        "centroid_delta_px": math.hypot(
            float(fast_center[0]) - float(full_center[0]),
            float(fast_center[1]) - float(full_center[1]),
        ),
        "damage_coverage_delta": float(fast.qa.get("damage_coverage", 0.0)) - float(exhaustive.qa.get("damage_coverage", 0.0)),
        "core_coverage_delta": float(fast.qa.get("core_damage_coverage", 0.0)) - float(exhaustive.qa.get("core_damage_coverage", 0.0)),
        "footprint_area_delta": fast_area - full_area,
        "overflow_delta": float(fast.qa.get("ink_overflow", 0.0)) - float(exhaustive.qa.get("ink_overflow", 0.0)),
        "rendered_mask_iou": iou,
    }
    logger.info(
        "layout shadow fast_accepted=true exhaustive_score=%.4f font_delta=%+d line_count_delta=%+d "
        "centroid_delta_px=%.2f damage_coverage_delta=%+.4f core_coverage_delta=%+.4f "
        "footprint_area_delta=%+d overflow_delta=%+.4f rendered_mask_iou=%.4f",
        exhaustive.penalty,
        fast.font_size - exhaustive.font_size,
        len(fast.lines) - len(exhaustive.lines),
        math.hypot(float(fast_center[0]) - float(full_center[0]), float(fast_center[1]) - float(full_center[1])),
        float(fast.qa.get("damage_coverage", 0.0)) - float(exhaustive.qa.get("damage_coverage", 0.0)),
        float(fast.qa.get("core_damage_coverage", 0.0)) - float(exhaustive.qa.get("core_damage_coverage", 0.0)),
        fast_area - full_area,
        float(fast.qa.get("ink_overflow", 0.0)) - float(exhaustive.qa.get("ink_overflow", 0.0)),
        iou,
    )
    return comparison


def free_text_fast_gate(
    result: SearchResult,
    image_shape: Tuple[int, int],
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
    placement_domain_mask: Optional[np.ndarray] = None,
) -> Tuple[bool, float]:
    from .free_text_search import _free_text_ink_overflow_from_raster

    candidate_bbox = _candidate_bbox(result.typography_candidate)
    candidate_bbox = tuple(value + delta for value, delta in zip(candidate_bbox, (result.dx, result.dy, result.dx, result.dy)))
    h, w = image_shape[:2]
    if not (0 <= candidate_bbox[0] <= candidate_bbox[2] <= w and 0 <= candidate_bbox[1] <= candidate_bbox[3] <= h):
        return False, 1.0
    if math.hypot(result.center_dx, result.center_dy) > max(2.0, 0.10 * result.typography_candidate.font_size):
        return False, 1.0
    if result.coverage["c_core"] < 0.90:
        return False, 1.0
    overflow = _free_text_ink_overflow_from_raster(
        result.raster,
        tuple(value + delta for value, delta in zip(result.raster.crop_box, (result.dx, result.dy, result.dx, result.dy))),
        obstacles,
        other_text,
        placement_domain_mask=placement_domain_mask,
    )
    return overflow == 0.0, overflow
