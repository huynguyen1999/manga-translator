"""Free-text candidate search and result materialization."""

from __future__ import annotations

import logging
import math
import os
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ...config import Config
from .. import _points_for_rect, fg_bg_compare, stroke
from .free_text_search import (
    _clamp_free_text_translation,
    _free_text_footprint_qa,
    _free_text_ink_overflow_from_raster,
    _free_text_offset_refine,
    _free_text_offset_search,
    _free_text_search_result,
    _free_text_shift_candidate,
)
from .free_text_typography import (
    _free_text_typography_candidates,
)
from .joint_layout import _candidate_bbox
from .models import (
    CandidateRaster,
    FreeTextDamageTarget,
    FreeTextZone,
    LayoutCandidate,
    OriginalLayoutProfile,
    PageObstacleMap,
    PlacementMode,
    SearchResult,
)
from .profiling import _render_text, get_solver_profile
from .raster import (
    _candidate_global_glyph_mask,
    _candidate_raster_at_offset,
    rasterize_candidate,
)
from .source_profile import build_original_layout_profile

logger = logging.getLogger("layout.solver")


def _source_height_candidates(candidates, profile, target, panel):
    minimum = max(1, int(math.ceil(profile.block_height)))
    maximum = int(profile.block_height * 1.5)
    if panel is not None:
        _, top, _, bottom = panel.bounds
        maximum = min(maximum, bottom - top - max(0, int(panel.margin)) * 2)
    if maximum <= 0: return []
    minimum = min(minimum, maximum)
    fitted = []
    for candidate in candidates:
        top = min(line.y for line in candidate.lines)
        bottom = max(line.y + line.height for line in candidate.lines)
        content_height = bottom - top
        if content_height > maximum:
            continue
        height = max(minimum, content_height)
        offset = -top
        for line in candidate.lines:
            line.y += offset
            line.slot.y_start += offset
            line.slot.y_end += offset
        candidate.layout_bounds = (
            min(line.x for line in candidate.lines), 0,
            max(line.x + line.width for line in candidate.lines), height,
        )
        fitted.append(candidate)
    return fitted


def _materialize_free_text_search_result(
    result: SearchResult,
    original_profile: OriginalLayoutProfile,
    target: Optional[FreeTextDamageTarget],
    damage_centroid: Tuple[float, float],
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
    status: str = "free_text",
    overflow: Optional[float] = None,
) -> LayoutCandidate:
    stats = get_solver_profile()
    candidate = _free_text_shift_candidate(result.typography_candidate, result.dx, result.dy)
    candidate_bbox = _candidate_bbox(candidate)
    ink_overflow = overflow
    if ink_overflow is None:
        ink_overflow = _free_text_ink_overflow_from_raster(
            result.raster,
            tuple(value + delta for value, delta in zip(result.raster.crop_box, (result.dx, result.dy, result.dx, result.dy))),
            obstacles,
            other_text,
        )
    candidate.penalty = result.score
    candidate.qa.update({
        "placement_mode": PlacementMode.FREE_TEXT.value,
        "source_bbox": original_profile.bbox,
        "source_font_size": original_profile.font_size,
        "source_line_count": original_profile.line_count,
        "damage_bbox": target.bbox if target is not None else original_profile.bbox,
        "damage_centroid": [damage_centroid[0], damage_centroid[1]],
        "ink_centroid": [result.actual_centroid[0], result.actual_centroid[1]],
        "center_error_px": math.hypot(result.relative_dx, result.relative_dy),
        "placement_dx": result.relative_dx,
        "placement_dy": result.relative_dy,
        "coverage_ink": result.coverage["c_ink"],
        "coverage_visual": result.coverage["c_visual"],
        "coverage_block": result.coverage["c_block"],
        "placement_anchor_coverage": result.coverage["c_block"],
        "damage_coverage": result.coverage["c_damage"],
        "core_damage_coverage": result.coverage["c_core"],
        "uncovered_damage": result.coverage["u_damage"],
        "cleanup_mask_coverage": result.coverage["cleanup_mask_coverage"],
        **_free_text_footprint_qa(original_profile, target, result.ink_bbox),
        "ink_overflow": ink_overflow,
        "layout_width": candidate_bbox[2] - candidate_bbox[0],
        "layout_height": candidate_bbox[3] - candidate_bbox[1],
        "expansion_ratio": (result.ink_bbox[2] - result.ink_bbox[0]) * (result.ink_bbox[3] - result.ink_bbox[1]) / max(1.0, original_profile.block_width * original_profile.block_height),
        "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text", "panel_bounds"],
        "free_text_score": result.score,
    })
    candidate.status = status
    stats.full_qa_candidates += 1
    return candidate


def _log_free_text_shadow_comparison(
    fast: Optional[LayoutCandidate],
    exhaustive: Optional[LayoutCandidate],
    image_shape: Tuple[int, int],
) -> None:
    if exhaustive is None:
        logger.info("layout shadow fast_accepted=%s exhaustive=none", fast is not None)
        return
    if fast is None:
        logger.info("layout shadow fast_accepted=false exhaustive_score=%.4f", exhaustive.penalty)
        return

    fast_center = fast.qa.get("ink_centroid", (0.0, 0.0))
    full_center = exhaustive.qa.get("ink_centroid", (0.0, 0.0))
    fast_mask = _candidate_global_glyph_mask(fast, image_shape)
    full_mask = _candidate_global_glyph_mask(exhaustive, image_shape)
    union = int(np.count_nonzero(fast_mask | full_mask))
    iou = float(np.count_nonzero(fast_mask & full_mask)) / union if union else 1.0
    fast_bbox = fast.qa.get("ink_bbox", (0, 0, 0, 0))
    full_bbox = exhaustive.qa.get("ink_bbox", (0, 0, 0, 0))
    fast_area = max(0, fast_bbox[2] - fast_bbox[0]) * max(0, fast_bbox[3] - fast_bbox[1])
    full_area = max(0, full_bbox[2] - full_bbox[0]) * max(0, full_bbox[3] - full_bbox[1])
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


def _free_text_fast_gate(
    result: SearchResult,
    image_shape: Tuple[int, int],
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
) -> Tuple[bool, float]:
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
    )
    return overflow == 0.0, overflow


def _layout_env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _layout_env_disabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"0", "false", "no", "off"}


def _solve_free_text_region(
    region: Any,
    zone: FreeTextZone,
    obstacles: PageObstacleMap,
    config: Config,
    image_shape: Tuple[int, int],
    solver_margin: float,
    solver_max_y_trials: int,
    allow_early_accept: bool = False,
    shadow_compare: bool = False,
    force_exhaustive: bool = False,
) -> Optional[Tuple[LayoutCandidate, OriginalLayoutProfile, Dict[str, Any]]]:
    """Solve FREE_TEXT as frozen typography followed by rigid placement.

    The inpainting mask determines where the paragraph belongs, never how its
    words wrap. Bubble geometry is intentionally not called from this path.
    """
    text = _render_text(region).strip()
    source_mask = getattr(region, "_free_text_source_mask", None)
    if not text or source_mask is None or not np.any(source_mask):
        return None
    if not np.any(zone.ownership_mask):
        return None

    profile = build_original_layout_profile(region, zone.ownership_mask)
    if profile is None:
        return None
    if config.render.font_size is not None:
        region.calibrated_font_size = int(config.render.font_size)
        region._font_policy_diagnostics = {"explicit_font_size": True}

    target = zone.damage_target
    prof = get_solver_profile()
    prof.free_text_regions += 1
    t_topo0 = perf_counter()
    typography = _source_height_candidates(
        _free_text_typography_candidates(
            text, profile, config, image_shape, target=target,
            panel_constraint=zone.panel_constraint,
        ),
        profile, target, zone.panel_constraint,
    )
    prof.ft_typography_ms += (perf_counter() - t_topo0) * 1000.0
    if not typography:
        return None
    prof.free_text_typography_candidates += len(typography)

    if target is None or target.area == 0:
        damage_centroid = profile.centroid
    else:
        damage_centroid = (target.centroid_x, target.centroid_y)
    target_width = target.width if target is not None else profile.block_width
    target_height = target.height if target is not None else profile.block_height

    stroke_width = stroke.get_text_stroke_width(max(c.font_size for c in typography), fg_bg_compare(*region.get_font_colors())[1], region.bg_colors)
    source = source_mask.astype(bool)
    other_text = obstacles.text_mask.astype(bool) & ~source
    candidate_rasters: Dict[int, CandidateRaster] = {}
    prepared_candidates: Dict[int, Tuple[Any, ...]] = {}
    search_results: List[SearchResult] = []
    evaluated: List[LayoutCandidate] = []
    typography.sort(key=lambda c: (abs(c.font_size - profile.font_size), c.penalty))
    eval_candidates = typography
    fast_results_by_offset: Dict[Tuple[int, int, int], SearchResult] = {}

    def prepare_candidate(typography_candidate: LayoutCandidate) -> Optional[Tuple[Any, ...]]:
        cid = id(typography_candidate)
        prepared = prepared_candidates.get(cid)
        if prepared is not None:
            prof.candidate_raster_cache_hits += 1
            return prepared
        t_crop0 = perf_counter()
        raster = candidate_rasters.get(cid)
        if raster is None:
            raster = rasterize_candidate(typography_candidate, stroke_width)
            candidate_rasters[cid] = raster
            prof.free_text_crops_rendered += 1
            prof.candidate_rasters_created += 1
        base_box, ink_crop, visual_crop, block_crop = _candidate_raster_at_offset(raster, 0, 0, image_shape)
        prof.ft_crops_rasterize_ms += (perf_counter() - t_crop0) * 1000.0
        bx1, by1, bx2, by2 = base_box
        if bx2 <= bx1 or by2 <= by1 or not np.any(ink_crop):
            return None
        ink_y, ink_x = np.nonzero(ink_crop)
        base_centroid = (bx1 + float(ink_x.mean()), by1 + float(ink_y.mean()))
        base_ink_bbox = (
            bx1 + int(ink_x.min()), by1 + int(ink_y.min()),
            bx1 + int(ink_x.max()) + 1, by1 + int(ink_y.max()) + 1,
        )
        ideal_dx = int(round(profile.centroid[0] - base_centroid[0]))
        ideal_dy = profile.bbox[1] - base_ink_bbox[1]
        clamped = _clamp_free_text_translation(
            _candidate_bbox(typography_candidate), ideal_dx, ideal_dy, zone.panel_constraint
        )
        if clamped is None:
            return None
        ideal_dx, ideal_dy = clamped
        max_radius = max(16, min(64, int(max(target_width, target_height, profile.block_width, profile.block_height))))
        prepared = (
            raster, base_box, ink_crop, visual_crop, block_crop, base_centroid,
            base_ink_bbox, ideal_dx, ideal_dy, max_radius,
        )
        prepared_candidates[cid] = prepared
        return prepared

    fast_requested = shadow_compare or (
        allow_early_accept and not force_exhaustive and not _layout_env_disabled("LAYOUT_FAST_FREE_TEXT")
    )
    try_local_stage = shadow_compare or (
        not force_exhaustive and _layout_env_enabled("LAYOUT_LAZY_CANDIDATES")
    )
    fast_result: Optional[SearchResult] = None
    fast_overflow: Optional[float] = None
    fast_status = "free_text_ideal"
    if (fast_requested or try_local_stage) and eval_candidates:
        first = eval_candidates[0]
        prepared = prepare_candidate(first) if fast_requested else None
        if prepared is not None:
            raster, base_box, ink_crop, visual_crop, block_crop, base_centroid, base_ink_bbox, ideal_dx, ideal_dy, _ = prepared
            prof.free_text_ideal_attempts += 1
            result = _free_text_search_result(
                first, raster, base_box, ink_crop, visual_crop, block_crop,
                base_centroid, base_ink_bbox, ideal_dx, ideal_dy, 0, 0,
                profile, damage_centroid, target_width, target_height, zone,
                obstacles, other_text,
            )
            if result is not None:
                fast_results_by_offset[(id(first), 0, 0)] = result
                accepted, fast_overflow = _free_text_fast_gate(result, image_shape, obstacles, other_text)
                if accepted:
                    fast_result = result
                    prof.free_text_ideal_successes += 1

        if fast_result is None and try_local_stage:
            prof.free_text_local_search_runs += 1
            local_offsets = (
                (0, 0), (4, 0), (-4, 0), (0, 4), (0, -4),
                (4, 4), (4, -4), (-4, 4), (-4, -4),
                (8, 0), (-8, 0), (0, 8), (0, -8),
            )
            for candidate_index, typography_candidate in enumerate(eval_candidates[:3]):
                prepared = prepare_candidate(typography_candidate)
                if prepared is None:
                    continue
                raster, base_box, ink_crop, visual_crop, block_crop, base_centroid, base_ink_bbox, ideal_dx, ideal_dy, _ = prepared
                for rel_dx, rel_dy in local_offsets:
                    if candidate_index == 0 and (rel_dx, rel_dy) == (0, 0):
                        continue
                    prof.free_text_local_search_attempts += 1
                    result = _free_text_search_result(
                        typography_candidate, raster, base_box, ink_crop, visual_crop, block_crop,
                        base_centroid, base_ink_bbox, ideal_dx, ideal_dy, rel_dx, rel_dy,
                        profile, damage_centroid, target_width, target_height, zone,
                        obstacles, other_text,
                    )
                    if result is None:
                        continue
                    fast_results_by_offset[(id(typography_candidate), rel_dx, rel_dy)] = result
                    accepted, overflow = _free_text_fast_gate(result, image_shape, obstacles, other_text)
                    if not accepted:
                        continue
                    fast_result, fast_overflow, fast_status = result, overflow, "free_text_local"
                    prof.free_text_local_search_successes += 1
                    break
                if fast_result is not None:
                    break

        if fast_result is not None and (fast_requested or try_local_stage) and not shadow_compare:
            candidate = _materialize_free_text_search_result(
                fast_result, profile, target, damage_centroid, obstacles, other_text,
                status=fast_status, overflow=fast_overflow,
            )
            region._free_text_candidate_pool = [candidate]
            return candidate, profile, candidate.qa
        if fast_result is None and (fast_requested or try_local_stage):
            prof.free_text_full_search_fallbacks += 1

    prof.free_text_full_search_runs += 1
    for typography_candidate in eval_candidates:
        if not force_exhaustive and search_results and typography_candidate.font_size != search_results[0].typography_candidate.font_size:
            break
        prepared = prepare_candidate(typography_candidate)
        if prepared is None:
            continue
        raster, base_box, ink_crop, visual_crop, block_crop, base_centroid, base_ink_bbox, ideal_dx, ideal_dy, max_radius = prepared
        coarse_offsets = _free_text_offset_search(max_radius)
        best_coarse_offset: Optional[Tuple[int, int]] = None
        best_coarse_score = float("inf")
        tested_offsets = set()
        for rel_dx, rel_dy in coarse_offsets:
            tested_offsets.add((rel_dx, rel_dy))
            cache_key = (id(typography_candidate), rel_dx, rel_dy)
            result = fast_results_by_offset.pop(cache_key, None)
            if result is None:
                result = _free_text_search_result(
                    typography_candidate, raster, base_box, ink_crop, visual_crop, block_crop,
                    base_centroid, base_ink_bbox, ideal_dx, ideal_dy, rel_dx, rel_dy,
                    profile, damage_centroid, target_width, target_height, zone,
                    obstacles, other_text,
                )
            if result is None:
                continue
            search_results.append(result)
            if result.score < best_coarse_score:
                best_coarse_score = result.score
                best_coarse_offset = (rel_dx, rel_dy)

        # Stage B: Fine refinement around best coarse offset
        if best_coarse_offset is not None:
            fine_offsets = _free_text_offset_refine(best_coarse_offset[0], best_coarse_offset[1], step=2)
            for rel_dx, rel_dy in fine_offsets:
                if (rel_dx, rel_dy) in tested_offsets:
                    continue
                tested_offsets.add((rel_dx, rel_dy))
                result = _free_text_search_result(
                    typography_candidate, raster, base_box, ink_crop, visual_crop, block_crop,
                    base_centroid, base_ink_bbox, ideal_dx, ideal_dy, rel_dx, rel_dy,
                    profile, damage_centroid, target_width, target_height, zone,
                    obstacles, other_text,
                )
                if result is None:
                    continue
                search_results.append(result)

    search_results.sort(key=lambda result: result.score)
    selected_results = []
    seen_layouts = set()
    seen_fonts = set()
    for result in search_results:
        font_size = result.typography_candidate.font_size
        if font_size in seen_fonts:
            continue
        layout = (result.typography_candidate.font_size, tuple(line.text for line in result.typography_candidate.lines))
        if layout in seen_layouts:
            continue
        seen_fonts.add(font_size)
        seen_layouts.add(layout)
        selected_results.append(result)
        if len(selected_results) == 8:
            break
    if len(selected_results) < 8:
        seen_positions = {(id(item.typography_candidate), item.dx, item.dy) for item in selected_results}
        for result in search_results:
            key = (id(result.typography_candidate), result.dx, result.dy)
            if key in seen_positions:
                continue
            seen_positions.add(key)
            selected_results.append(result)
            if len(selected_results) == 8:
                break
    evaluated.extend(
        _materialize_free_text_search_result(
            result, profile, target, damage_centroid, obstacles, other_text
        )
        for result in selected_results
    )

    if not evaluated:
        if shadow_compare:
            fast_candidate = _materialize_free_text_search_result(
                fast_result, profile, target, damage_centroid, obstacles, other_text,
                status=fast_status, overflow=fast_overflow,
            ) if fast_result is not None else None
            _log_free_text_shadow_comparison(fast_candidate, None, image_shape)
        return None

    evaluated.sort(key=lambda candidate: candidate.penalty)
    if shadow_compare:
        fast_candidate = _materialize_free_text_search_result(
            fast_result, profile, target, damage_centroid, obstacles, other_text,
            status=fast_status, overflow=fast_overflow,
        ) if fast_result is not None else None
        _log_free_text_shadow_comparison(fast_candidate, evaluated[0], image_shape)
    unique: List[LayoutCandidate] = []
    seen = set()
    for candidate in evaluated:
        key = (candidate.font_size, tuple((line.text, line.x, line.y) for line in candidate.lines))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= 8:
            break

    alternatives = [
        {
            "font_size": candidate.font_size,
            "lines": len(candidate.lines),
            "text": [line.text for line in candidate.lines],
            "damage_coverage": candidate.qa.get("damage_coverage", 0.0),
            "core_coverage": candidate.qa.get("core_damage_coverage", 0.0),
            "expansion_ratio": candidate.qa.get("expansion_ratio", 1.0),
            "center_error_px": candidate.qa.get("center_error_px", 0.0),
            "center_drift": candidate.qa.get("center_error_px", 0.0),
            "penalty": candidate.penalty,
        }
        for candidate in unique
    ]
    for candidate in unique:
        candidate.qa["candidate_alternatives"] = alternatives
    region._free_text_candidate_pool = unique[:16]
    best = unique[0]
    return best, profile, best.qa
