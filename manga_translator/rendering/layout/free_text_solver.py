"""Free-text candidate search and result materialization."""

from __future__ import annotations

import math
import os
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ...config import Config
from .free_text_search import (
    _clamp_free_text_translation,
    _free_text_footprint_qa,
    _free_text_ink_overflow_from_raster,
    _free_text_offset_refine,
    _free_text_offset_search,
    _free_text_search_result,
    _free_text_shift_candidate,
)
from .free_text_stages import _search_first_viable_stage
from .free_text_context import FreeTextSolveContext, _source_height_candidates
from .joint_layout import _candidate_bbox
from .models import (
    FreeTextDamageTarget,
    FreeTextZone,
    LayoutCandidate,
    OriginalLayoutProfile,
    PageObstacleMap,
    PlacementMode,
    SearchResult,
)
from .profiling import _SOLVER_PROFILE, SolverProfileStats, _render_text, get_solver_profile
from .raster import (
    _candidate_raster_at_offset,
    rasterize_candidate,
)
from .candidate_footprint import (
    free_text_fast_gate as _free_text_fast_gate,
    log_free_text_shadow_comparison as _log_free_text_shadow_comparison,
)

def _materialize_free_text_search_result(
    result: SearchResult,
    original_profile: OriginalLayoutProfile,
    target: Optional[FreeTextDamageTarget],
    damage_centroid: Tuple[float, float],
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
    status: str = "free_text",
    overflow: Optional[float] = None,
    placement_domain_mask: Optional[np.ndarray] = None,
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
            placement_domain_mask=placement_domain_mask,
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

def _validate_fast_result(
    result, region, profile, target, damage_centroid, obstacles, other_text,
    status, overflow, domain_mask, config, image_shape, zone, isolate_rejections=False,
):
    candidate = _materialize_free_text_search_result(
        result, profile, target, damage_centroid, obstacles, other_text,
        status=status, overflow=overflow, placement_domain_mask=domain_mask,
    )
    domain_qa = getattr(region, "_free_text_domain_qa", None)
    if domain_qa:
        candidate.qa.update(domain_qa)
    rejections = dict(getattr(zone, "rejections", {}))
    from .candidate_footprint import filter_renderable_candidates
    accepted = filter_renderable_candidates(region, [candidate], config, image_shape, zone, obstacles, other_text)
    if isolate_rejections or not accepted:
        zone.rejections = rejections
    return accepted[0] if accepted else None


def _free_text_candidate_alternative(candidate: LayoutCandidate) -> Dict[str, Any]:
    return {
        "font_size": candidate.font_size, "lines": len(candidate.lines),
        "text": [line.text for line in candidate.lines],
        "damage_coverage": candidate.qa.get("damage_coverage", 0.0),
        "core_coverage": candidate.qa.get("core_damage_coverage", 0.0),
        "expansion_ratio": candidate.qa.get("expansion_ratio", 1.0),
        "center_error_px": candidate.qa.get("center_error_px", 0.0),
        "center_drift": candidate.qa.get("center_error_px", 0.0),
        "penalty": candidate.penalty,
    }


def _layout_env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

def _layout_env_disabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"0", "false", "no", "off"}


def _solve_free_text_region_once(
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
    preparation: Optional[FreeTextSolveContext] = None,
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
        region._free_text_attempt_qa = {"failure_stage": "empty_ownership", "reason": "source_overlaps_other_regions"}
        return None

    preparation = preparation if preparation is not None else FreeTextSolveContext()
    profile = preparation.prepare(
        region, zone, obstacles, config, image_shape, text, source_mask,
        full_font_search=force_exhaustive,
    )
    if profile is None:
        return None
    prof = get_solver_profile()
    target = preparation.target
    typography = preparation.typography
    region._free_text_attempt_qa = {
        "typography_candidates": len(typography),
        "attempted_font_sizes": sorted({c.font_size for c in typography}, reverse=True),
    }
    if not typography:
        region._free_text_attempt_qa["failure_stage"] = "typography"
        return None
    damage_centroid = preparation.damage_centroid
    target_width = preparation.target_width
    target_height = preparation.target_height
    stroke_width = preparation.stroke_width
    source = preparation.source
    other_text = preparation.other_text
    stage_groups = preparation.stage_groups
    candidate_rasters = preparation.candidate_rasters
    prepared_candidates = preparation.prepared_candidates
    search_results: List[SearchResult] = []
    evaluated: List[LayoutCandidate] = []
    eval_candidates = typography
    first_stage_candidates = stage_groups[0]
    domain_for_overflow = None if getattr(zone, "domain_center_only", False) else zone.placement_domain_mask
    fast_results_by_offset: Dict[Tuple[int, int, int], SearchResult] = {}

    def prepare_candidate(typography_candidate: LayoutCandidate) -> Optional[Tuple[Any, ...]]:
        cid = id(typography_candidate)
        prepared = prepared_candidates.get(cid)
        if prepared is not None:
            prof.candidate_raster_cache_hits += 1
            return (*prepared, int(getattr(region, "_free_text_domain_qa", {}).get("geodesic_limit_px", 64)))
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
        prepared = (
            raster, base_box, ink_crop, visual_crop, block_crop, base_centroid,
            base_ink_bbox, ideal_dx, ideal_dy,
        )
        prepared_candidates[cid] = prepared
        return (*prepared, int(getattr(region, "_free_text_domain_qa", {}).get("geodesic_limit_px", 64)))

    budget = preparation.search_budget
    budget_initial = budget is not None and budget.initial
    previous_candidates = list(getattr(region, "_free_text_candidate_pool", [])) if budget is not None and not budget_initial else []
    fast_requested = not budget_initial and (shadow_compare or (allow_early_accept and not force_exhaustive and not _layout_env_disabled("LAYOUT_FAST_FREE_TEXT")))
    try_local_stage = not budget_initial and allow_early_accept and not shadow_compare and not force_exhaustive and not _layout_env_disabled("LAYOUT_LAZY_CANDIDATES")
    fast_result: Optional[SearchResult] = None
    fast_overflow: Optional[float] = None
    fast_status = "free_text_ideal"
    local_results: List[Tuple[SearchResult, float]] = []
    fast_path_attempted = fast_requested or try_local_stage
    shadow_candidate = None
    shadow_profile = SolverProfileStats() if shadow_compare else None
    shadow_started = perf_counter() if shadow_compare else 0.0
    shadow_rejections = dict(getattr(zone, "rejections", {})) if shadow_compare else None
    shadow_caches = [cache.copy() for cache in (candidate_rasters, prepared_candidates)] if shadow_compare else None
    shadow_token = _SOLVER_PROFILE.set(shadow_profile) if shadow_compare else None
    if shadow_compare:
        prof = shadow_profile
    try:
        if (fast_requested or try_local_stage) and eval_candidates:
            first = first_stage_candidates[0]
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
                    accepted, fast_overflow = _free_text_fast_gate(
                        result, image_shape, obstacles, other_text, domain_for_overflow,
                    )
                    if accepted:
                        fast_result = result

            if fast_result is not None and allow_early_accept and not shadow_compare:
                fast_candidate = _validate_fast_result(
                    fast_result, region, profile, target, damage_centroid, obstacles, other_text,
                    fast_status, fast_overflow, domain_for_overflow, config, image_shape, zone,
                )
                if fast_candidate is not None:
                    prof.free_text_ideal_successes += 1
                    fast_candidate.qa["candidate_alternatives"] = [_free_text_candidate_alternative(fast_candidate)]
                    region._free_text_candidate_pool = [fast_candidate]
                    return fast_candidate, profile, fast_candidate.qa
                fast_result = None
                fast_results_by_offset.clear()

            if fast_result is None and try_local_stage:
                prof.free_text_local_search_runs += 1
                local_offsets = (
                    (0, 0), (4, 0), (-4, 0), (0, 4), (0, -4),
                    (4, 4), (4, -4), (-4, 4), (-4, -4),
                    (8, 0), (-8, 0), (0, 8), (0, -8),
                )
                for candidate_index, typography_candidate in enumerate(first_stage_candidates[:3]):
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
                        accepted, overflow = _free_text_fast_gate(
                            result, image_shape, obstacles, other_text, domain_for_overflow,
                        )
                        if not accepted:
                            continue
                        local_results.append((result, overflow))

                validated_local = []
                seen_local = set()
                seen_local_positions = set()
                for result, overflow in sorted(local_results, key=lambda item: item[0].score):
                    position = (id(result.typography_candidate), result.dx, result.dy)
                    if position in seen_local_positions:
                        continue
                    seen_local_positions.add(position)
                    candidate = _validate_fast_result(
                        result, region, profile, target, damage_centroid, obstacles, other_text,
                        "free_text_local", overflow, domain_for_overflow, config, image_shape, zone,
                    )
                    if candidate is None:
                        continue
                    prof.free_text_local_search_successes += 1
                    layout = (candidate.font_size, tuple((line.text, line.x, line.y) for line in candidate.lines))
                    if layout in seen_local:
                        continue
                    seen_local.add(layout)
                    validated_local.append(candidate)
                    if len(validated_local) == 8:
                        break
                if validated_local:
                    alternatives = [_free_text_candidate_alternative(candidate) for candidate in validated_local]
                    for candidate in validated_local:
                        candidate.qa["candidate_alternatives"] = alternatives
                    region._free_text_candidate_pool = validated_local
                    return validated_local[0], profile, validated_local[0].qa

        if shadow_compare and fast_result is not None:
            shadow_candidate = _validate_fast_result(
                fast_result, region, profile, target, damage_centroid, obstacles, other_text,
                fast_status, fast_overflow, domain_for_overflow, config, image_shape, zone,
                isolate_rejections=True,
            )
            if shadow_candidate is not None:
                prof.free_text_ideal_successes += fast_status == "free_text_ideal"
        if shadow_compare and fast_path_attempted and shadow_candidate is None:
            prof.free_text_full_search_fallbacks += 1
    finally:
        if shadow_compare:
            zone.rejections = shadow_rejections
            _SOLVER_PROFILE.reset(shadow_token)
            prof = get_solver_profile()
            for cache, saved in zip((candidate_rasters, prepared_candidates), shadow_caches):
                cache.clear()
                cache.update(saved)
            fast_results_by_offset.clear()
            metrics = shadow_profile.to_dict()
            region._free_text_shadow_metrics = {
                "elapsed_ms": (perf_counter() - shadow_started) * 1000.0,
                "workload": metrics["workload"], "timings_ms": metrics["timings_ms"],
            }

    if fast_path_attempted and not shadow_compare:
        prof.free_text_full_search_fallbacks += 1

    if not budget_initial: prof.free_text_full_search_runs += 1
    from .candidate_footprint import stage_render_validator
    render_validator = stage_render_validator(region, config, image_shape, zone, obstacles, other_text)

    def search_stages(stages, collect_stages=False, offset_search=None, offset_refine=None):
        domain = getattr(region, "_free_text_domain_qa", {}).get("bbox")
        domain_bbox = tuple(domain) if isinstance(domain, (list, tuple)) and len(domain) == 4 else None
        return _search_first_viable_stage(
            stages, prepare_candidate, fast_results_by_offset, profile, damage_centroid,
            target_width, target_height, zone, obstacles, other_text,
            offset_search or _free_text_offset_search,
            offset_refine or _free_text_offset_refine, _free_text_search_result,
            render_validator, collect_stages, domain_bbox, budget,
        )

    search_results = preparation.search_with_fallback(region, zone, config, image_shape, profile, search_stages, force_exhaustive)

    search_results.sort(key=lambda result: result.score)
    selected_results = []
    candidate_limit, seen_layouts, seen_fonts = (2 if budget_initial else 8), set(), set()
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
        if len(selected_results) == candidate_limit:
            break
    if len(selected_results) < candidate_limit:
        seen_positions = {(id(item.typography_candidate), item.dx, item.dy) for item in selected_results}
        for result in search_results:
            key = (id(result.typography_candidate), result.dx, result.dy)
            if key in seen_positions:
                continue
            seen_positions.add(key)
            selected_results.append(result)
            if len(selected_results) == candidate_limit:
                break
    evaluated.extend(
        _materialize_free_text_search_result(
            result, profile, target, damage_centroid, obstacles, other_text,
            status="free_text_local" if budget_initial and getattr(result.typography_candidate, "_local_search", False) else "free_text",
            placement_domain_mask=domain_for_overflow,
        )
        for result in selected_results
    )
    domain_qa = getattr(region, "_free_text_domain_qa", None)
    if domain_qa:
        for candidate in evaluated:
            candidate.qa.update(domain_qa)

    from .candidate_footprint import filter_renderable_candidates
    evaluated = filter_renderable_candidates(region, evaluated, config, image_shape, zone, obstacles, other_text)
    if previous_candidates: previous_candidates = filter_renderable_candidates(region, previous_candidates, config, image_shape, zone, obstacles, other_text)
    if not evaluated and not previous_candidates:
        if shadow_compare:
            region._free_text_shadow_metrics["comparison"] = _log_free_text_shadow_comparison(
                shadow_candidate, None, image_shape,
            )
        return None

    evaluated.extend(previous_candidates)
    evaluated.sort(key=lambda candidate: candidate.penalty)
    if shadow_compare:
        region._free_text_shadow_metrics["comparison"] = _log_free_text_shadow_comparison(
            shadow_candidate, evaluated[0], image_shape,
        )
    unique: List[LayoutCandidate] = []
    seen = set()
    for candidate in evaluated:
        key = (candidate.font_size, tuple((line.text, line.x, line.y) for line in candidate.lines))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= candidate_limit:
            break

    alternatives = [_free_text_candidate_alternative(candidate) for candidate in unique]
    for candidate in unique:
        candidate.qa["candidate_alternatives"] = alternatives
    region._free_text_candidate_pool = unique[:candidate_limit]
    best = unique[0]
    return best, profile, best.qa

def _solve_free_text_region(region, zone, obstacles, config, image_shape, solver_margin,
                            solver_max_y_trials, **options):
    from .free_text_constraints import _build_free_text_placement_domain
    from .search_budget import get_search_budget
    attempts = []
    budget = get_search_budget()
    preparation = getattr(region, "_free_text_solve_context", None)
    reused_preparation = (
        budget is not None and isinstance(preparation, FreeTextSolveContext)
        and preparation.search_budget is budget and preparation._zone is zone
    )
    if not reused_preparation:
        preparation = FreeTextSolveContext()
        region._free_text_candidate_pool = []
    region._free_text_solve_context = preparation
    for distance, center_only in ((64, False), (128, False), (128, True)):
        if budget is not None and budget.expired():
            break
        if center_only and not any(attempt["rejections"].get("local_domain") for attempt in attempts):
            break
        zone.domain_center_only = center_only
        limit = max(1, round(distance * max(image_shape[:2]) / 2048))
        zone.placement_domain_mask, region._free_text_domain_qa = _build_free_text_placement_domain(
            region, region._free_text_source_mask, zone.coverable_damage_mask,
            obstacles, zone.panel_constraint, distance=limit)
        zone.rejections = {}
        region._free_text_attempt_qa = {}
        result = _solve_free_text_region_once(region, zone, obstacles, config, image_shape,
                                             solver_margin, solver_max_y_trials,
                                             preparation=preparation, **options)
        attempts.append({**region._free_text_domain_qa, **region._free_text_attempt_qa,
                         "rejections": dict(zone.rejections), "overlapping_source_ids": getattr(region, "_free_text_source_conflicts", [])})
        region._free_text_attempt_qa = {"placement_attempts": attempts}
        if result is not None:
            for candidate in region._free_text_candidate_pool:
                candidate.qa.update(region._free_text_attempt_qa)
            return result
    if reused_preparation and budget is not None and region._free_text_candidate_pool:
        candidate = region._free_text_candidate_pool[0]
        candidate.qa.update(region._free_text_attempt_qa)
        return candidate, preparation.profile, candidate.qa
    return None
