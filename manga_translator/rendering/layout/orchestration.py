"""Page-level layout orchestration for bubble and free-text regions."""
from __future__ import annotations
from time import perf_counter
from .search_budget import get_search_budget
from contextlib import nullcontext
from .page_search import choose_bubble_group
from typing import Any, Dict, List, Optional
import numpy as np
from ...geometry.bubbles import PageGeometry
from .contrast import apply_free_text_contrast
from .models import LayoutCandidate, OriginalLayoutProfile, PlacementMode
from .failure_policy import free_text_failure_reason
from .region_profiling import profiled_contrast, profiled_region_call
def apply_shape_aware_bubble_layout(
    ctx: Context,
    config: Config,
    font_path: Optional[str] = None,
    solver_margin: float = 2.0,
    solver_max_y_trials: int = 12,
    legacy_only: bool = False,
    infer_bubbles: bool = False,
    timing: Optional[Dict[str, float]] = None,
    layout_debug: bool = False,
    page_geometry: Optional[PageGeometry] = None,
) -> None:
    """Execute shape-aware 2D free-space text fitting and layout on ctx.text_regions."""
    from . import solver as _solver
    budget = get_search_budget()
    collect_region_profile = bool(getattr(ctx, "_collect_layout_profile", False))
    layout_timing = {
        "mask_prep_ms": 0.0,
        "mode_classification_ms": 0.0,
        "bubble_solver_ms": 0.0,
        "free_text_solver_ms": 0.0,
        "fallback_ms": 0.0,
    }
    if timing is not None:
        timing.update(layout_timing)
    active_font = font_path or getattr(config.render, "font_path", None) or _solver.get_default_eng_font()
    _solver.text_render.set_font(active_font)
    regions = ctx.text_regions or []
    img = getattr(ctx, "img_rgb", None)
    if img is None:
        return
    _solver._ensure_region_identities(regions)
    transform_text_case = getattr(config.render, "transform_text_case", None)
    for region in regions:
        region.review_required = bool(getattr(region, "review_required", False))
        if (
            transform_text_case
            and getattr(region, "translation", None)
            and isinstance(region.translation, str)
        ):
            region.translation = transform_text_case(region.translation)
    for region in regions:
        if getattr(region, "_bubble_interior", None) is not None:
            continue
        safe_shape = getattr(region, "bubble_safe_shape", None)
        interior = _solver.decode_safe_shape(safe_shape, img.shape[0], img.shape[1])
        if interior is not None and _solver.np.any(interior):
            region._bubble_interior = interior
    if infer_bubbles and not any(
        getattr(region, "_bubble_mask", None) is not None
        or getattr(region, "_bubble_interior", None) is not None
        for region in regions
    ):
        seed_regions = [
            region for region in regions
            if getattr(region, "translation", None)
            and str(region.translation).strip()
        ]
        if seed_regions:
            group_regions = bool(
                getattr(getattr(config, "bubble_detection", None), "group_regions", False)
            )
            prepared = _solver.prepare_bubbles(
                image=img,
                regions=seed_regions,
                font_path=active_font,
                render_config=config.render,
                group=group_regions,
            )
            by_id = {
                str(getattr(region, "region_id", "")): region
                for region in prepared
                if getattr(region, "region_id", None)
            }
            ctx.text_regions = [
                by_id.get(str(getattr(region, "region_id", "")), region)
                for region in regions
            ]
            regions = ctx.text_regions
    phase_start = _solver.perf_counter()
    bubble_padding = int(getattr(getattr(config, "bubble_detection", None), "padding", 9))
    if page_geometry is None or not page_geometry.matches(img, regions, bubble_padding):
        page_geometry, _ = _solver.prepare_page_geometry(
            img, regions, bubble_padding, return_cleanup=False,
        )
    ctx.page_geometry = page_geometry
    layout_timing["mask_prep_ms"] = (_solver.perf_counter() - phase_start) * 1000.0
    phase_start = _solver.perf_counter()
    _solver.classify_placement_modes(regions)
    layout_timing["mode_classification_ms"] = (_solver.perf_counter() - phase_start) * 1000.0
    render_cfg = config.render
    unplaced_regions: List[Any] = []
    bubble_regions = [
        region for region in regions
        if getattr(region, "placement_mode", None) is PlacementMode.BUBBLE
    ]
    free_regions = [
        region for region in regions
        if getattr(region, "placement_mode", None) is PlacementMode.FREE_TEXT
    ]
    obstacles = None
    inpaint_mask = getattr(ctx, "inpaint_mask", None) if getattr(ctx, "inpaint_mask", None) is not None else (getattr(ctx, "text_mask", None) if getattr(ctx, "text_mask", None) is not None else (getattr(ctx, "mask_raw", None) if getattr(ctx, "mask_raw", None) is not None else getattr(ctx, "mask", None)))
    bubble_groups = _solver._shared_bubble_groups(bubble_regions)
    from .readable_text import readable_font_minimum
    minimum = readable_font_minimum(render_cfg, img.shape[:2])
    page_font_baseline = (
        _solver._page_dialogue_font_baseline(bubble_groups, minimum)
        if render_cfg.font_size is None or render_cfg.font_size <= 0 else None
    )
    if page_font_baseline is not None:
        page_font_baseline = max(
            minimum,
            page_font_baseline + (getattr(render_cfg, "font_size_offset", 0) or 0),
        )
    if legacy_only:
        unplaced_regions.extend(regions)
    phase_start = _solver.perf_counter()
    for group in bubble_groups if not legacy_only else []:
        active = [
            region for region in group.regions
            if not legacy_only
            and getattr(region, "_bubble_interior", None) is not None
            and _solver.np.any(getattr(region, "_bubble_interior", None))
            and _solver._render_text(region).strip()
        ]
        if not active:
            unplaced_regions.extend(group.regions)
            continue
        interior = getattr(active[0], "_bubble_interior", None)
        lobe_graph = _solver.build_lobe_graph(group.bubble_mask) if _solver.np.any(group.bubble_mask) else None
        group.lobe_graph = lobe_graph
        def _build_group_plans(members, retained=None):
            zones = _solver.partition_bubble_zones(members, interior, lobe_graph=lobe_graph, apply_boundary_gap=(len(members) > 1))
            group.zones = zones
            return [
                plan for index, region in enumerate(members)
                if (plan := (retained or {}).get(id(region)) or profiled_region_call(
                    _solver._build_region_layout_plan, region, "bubble_solver", collect_region_profile,
                    region=region, interior=interior, config=config, image_shape=img.shape[:2],
                    solver_margin=solver_margin, solver_max_y_trials=solver_max_y_trials,
                    preferred_mask=zones[index] if len(members) > 1 and index < len(zones) else None,
                    top_k=_solver._JOINT_CANDIDATE_COUNT if len(members) > 1 else 1,
                    zone_geometry_mask=zones[index] if len(members) > 1 and index < len(zones) else None,
                    page_font_baseline=page_font_baseline,
                )) is not None
            ]
        plans = _build_group_plans(active)
        if budget is not None and not budget.expired():
            unresolved = [r for r in active if not any(p.region is r and p.candidates for p in plans)]
            if unresolved:
                with budget.searching("expanded"):
                    expanded = _build_group_plans(active, {id(p.region): p for p in plans if p.candidates})
                plans = expanded
        feasible = [plan.region for plan in plans if plan.candidates]
        if 0 < len(feasible) < len(active):
            active = feasible
            plans = [plan for plan in plans if plan.candidates]
        chosen = choose_bubble_group(plans, len(active), img.shape[:2])
        if chosen is None and budget is not None and not budget.expired():
            with budget.searching("expanded"):
                expanded = _build_group_plans(active)
            from .page_search import merge_bubble_plans
            plans = merge_bubble_plans(plans, expanded)
            chosen = choose_bubble_group(plans, len(active), img.shape[:2])
        if chosen is None and budget is not None and not budget.expired():
            with budget.searching("rescue"):
                rescued = _build_group_plans(active)
            from .page_search import merge_bubble_plans
            plans = merge_bubble_plans(plans, rescued)
            chosen = choose_bubble_group(plans, len(active), img.shape[:2])
        if chosen is None and budget is not None:
            from .page_search import compatible_bubble_subset
            plans, chosen = compatible_bubble_subset(plans, img.shape[:2])
            active = [plan.region for plan in plans]
        if chosen is not None and len(chosen) == len(plans) == len(active):
            for plan, candidate in zip(plans, chosen):
                if not _solver._apply_layout_candidate(
                    plan, candidate, img.shape[:2], layout_debug=layout_debug
                ):
                    unplaced_regions.append(plan.region)
            active_ids = {id(region) for region in active}
            unplaced_regions.extend(
                region for region in group.regions
                if id(region) not in active_ids and not getattr(region, "_render_suppressed", False)
            )
            continue
        active_ids = {id(region) for region in active}
        for region in group.regions:
            if id(region) in active_ids and not getattr(region, "_render_suppressed", False):
                region._solver_status = "requires_compression"
                unplaced_regions.append(region)
    layout_timing["bubble_solver_ms"] = (_solver.perf_counter() - phase_start) * 1000.0
    if not legacy_only and free_regions:
        phase_start = _solver.perf_counter()
        bubble_halo = max(2, int(round(max((getattr(r, "source_font_size", 0) or getattr(r, "font_size", 0) or 12 for r in free_regions), default=12) * 0.20)))
        obstacles = _solver.build_page_obstacle_map(regions, img.shape[:2], bubble_halo=bubble_halo)
        free_zones = _solver.build_free_text_ownership_zones(free_regions, obstacles, inpaint_mask=inpaint_mask, image=img, other_regions=regions, panel_detections=getattr(ctx, "panel_detections", None))
        free_plans: Dict[int, List[LayoutCandidate]] = {}
        free_profiles: Dict[int, OriginalLayoutProfile] = {}
        def solve_free_text_region(region, zone, phase, **options):
            return profiled_region_call(
                _solver._solve_free_text_region, region, phase, collect_region_profile,
                region=region, zone=zone, obstacles=obstacles, config=config,
                image_shape=img.shape[:2], solver_margin=solver_margin,
                solver_max_y_trials=solver_max_y_trials, **options,
            )
        for region in free_regions:
            ft_zone = free_zones.get(id(region))
            if ft_zone is None:
                continue
            result = solve_free_text_region(
                region, ft_zone, "free_text_solver",
                allow_early_accept=True,
                shadow_compare=bool(getattr(ctx, "_layout_shadow_compare", False)),
            )
            if result is None and budget is not None and not budget.expired():
                for search_phase in ("expanded", "rescue"):
                    with budget.searching(search_phase):
                        result = solve_free_text_region(region, ft_zone, "free_text_" + search_phase,
                            allow_early_accept=False, force_exhaustive=True)
                    if result is not None or budget.expired():
                        break
            if result is None:
                region._solver_path = "free_text"
                region._solver_status = "no_valid_layout"
                region._solver_qa = {
                    "placement_mode": PlacementMode.FREE_TEXT.value, **getattr(region, "_free_text_attempt_qa", {}),
                    "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text", "panel_bounds"],
                    "panel_constraint": _solver._panel_constraint_diagnostics(ft_zone.panel_constraint),
                }
                region._render_suppressed = region.review_required = True
                region.review_reason = free_text_failure_reason(region)
                continue
            candidate, profile, _qa = result
            free_plans[id(region)] = getattr(region, "_free_text_candidate_pool", [candidate])
            free_profiles[id(region)] = profile
        if len(free_regions) > 1:
            conflict_ids = _solver._free_text_fast_conflict_regions(free_regions, free_plans, img.shape[:2])
            for region in free_regions:
                if id(region) not in conflict_ids:
                    continue
                ft_zone = free_zones.get(id(region))
                if ft_zone is None:
                    continue
                with budget.searching("expanded") if budget is not None else nullcontext():
                    result = solve_free_text_region(
                    region, ft_zone, "free_text_conflict_rerun",
                    allow_early_accept=False,
                    shadow_compare=False,
                    force_exhaustive=True,
                )
                if result is None:
                    continue
                candidate, profile, _qa = result
                free_plans[id(region)] = getattr(region, "_free_text_candidate_pool", [candidate])
                free_profiles[id(region)] = profile
        chosen_free = _solver._select_free_text_joint_candidates(
            free_regions, free_plans, img.shape[:2], free_profiles=free_profiles
        )
        for region in free_regions:
            candidate = chosen_free.get(id(region))
            profile = free_profiles.get(id(region))
            if candidate is None or profile is None:
                if id(region) in free_plans:
                    region._solver_path = "free_text"
                    region._solver_status = "no_joint_layout"
                    region._render_suppressed = region.review_required = True
                    region.review_reason = "no_joint_layout: rendered text collision"
                    region._solver_qa = {
                        "placement_mode": PlacementMode.FREE_TEXT.value, **getattr(region, "_free_text_attempt_qa", {}),
                        "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text", "panel_bounds"],
                        "panel_constraint": _solver._panel_constraint_diagnostics(getattr(region, "_panel_constraint", None)),
                    }
                continue
            if not _solver._apply_free_text_candidate(
                region, candidate, profile, config, img.shape[:2], layout_debug=layout_debug
            ):
                region._solver_path = "free_text"
                region._solver_status = "rasterization_failed"
                region._render_suppressed = region.review_required = True
                region.review_reason = region.review_reason or "rasterization_failed"
                region._solver_qa = {
                    "placement_mode": PlacementMode.FREE_TEXT.value, **getattr(region, "_free_text_attempt_qa", {}),
                    "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text", "panel_bounds"],
                    "panel_constraint": _solver._panel_constraint_diagnostics(getattr(region, "_panel_constraint", None)),
                }
        for region in free_regions:
            _solver.logger.info(
                f"FREE_TEXT SOLVER RESULT id={getattr(region, 'region_id', id(region))} "
                f"placement_mode={getattr(region, 'placement_mode', None)} "
                f"solver_applied={getattr(region, '_free_text_solver_applied', False)} "
                f"layout_input_text={getattr(region, '_layout_input_text', None)!r} "
                f"solver_path={getattr(region, '_solver_path', None)} "
                f"solver_status={getattr(region, '_solver_status', None)} "
                f"has_bubble_box={getattr(region, '_bubble_box', None) is not None} "
                f"has_bubble_points={getattr(region, '_bubble_points', None) is not None} "
                f"has_zone={getattr(region, '_free_text_zone', None) is not None}"
            )
        if layout_debug:
            ctx._free_text_obstacle_map = obstacles
            ctx._free_text_zones = free_zones
            from .debug import create_free_text_layout_debug
            ctx._free_text_layout_debug = create_free_text_layout_debug(
                img, free_regions, obstacles, free_zones
            )
        else:
            ctx._free_text_layout_debug = None
        layout_timing["free_text_solver_ms"] = (_solver.perf_counter() - phase_start) * 1000.0
    ctx._bubble_layout_groups = bubble_groups
    group_regions = bool(getattr(getattr(config, "bubble_detection", None), "group_regions", False))
    if unplaced_regions and not legacy_only and budget is not None:
        for region in unplaced_regions:
            region._render_suppressed = region.review_required = True
            region.review_reason = "layout_search_deadline" if budget.expired() else "text_does_not_fit"
        unplaced_regions = []
    if unplaced_regions and not legacy_only:
        phase_start = _solver.perf_counter()
        prepared = _solver.prepare_bubbles(
            image=img,
            regions=unplaced_regions,
            font_path=active_font,
            render_config=render_cfg,
            group=group_regions,
            page_target_font=page_font_baseline,
        )
        by_id = {
            str(getattr(r, "region_id", "")): r
            for r in prepared
            if getattr(r, "region_id", None)
        }
        for idx, r in enumerate(unplaced_regions):
            prep = by_id.get(str(getattr(r, "region_id", ""))) or (prepared[idx] if idx < len(prepared) else None)
            if prep is not None:
                r.review_required = getattr(prep, "review_required", False)
                r.review_reason = getattr(prep, "review_reason", None)
                r._render_suppressed = getattr(prep, "_render_suppressed", False)
                r._bubble_box = getattr(prep, "_bubble_box", None)
                r._bubble_points = getattr(prep, "_bubble_points", None)
                r.font_size = getattr(prep, "font_size", r.font_size)
                r.layout_bounds = getattr(prep, "layout_bounds", getattr(r, "layout_bounds", None))
        layout_timing["fallback_ms"] += (_solver.perf_counter() - phase_start) * 1000.0
    elif unplaced_regions and legacy_only:
        phase_start = _solver.perf_counter()
        prepared = _solver.prepare_bubbles(
            image=img,
            regions=regions,
            font_path=active_font,
            render_config=render_cfg,
            group=group_regions,
        )
        by_id = {
            str(getattr(r, "region_id", "")): r
            for r in prepared
            if getattr(r, "region_id", None)
        }
        for idx, r in enumerate(regions):
            prep = by_id.get(str(getattr(r, "region_id", ""))) or (prepared[idx] if idx < len(prepared) else None)
            if prep is not None:
                r.review_required = getattr(prep, "review_required", False)
                r.review_reason = getattr(prep, "review_reason", None)
                r._render_suppressed = getattr(prep, "_render_suppressed", False)
                r._bubble_box = getattr(prep, "_bubble_box", None)
                r._bubble_points = getattr(prep, "_bubble_points", None)
                r.font_size = getattr(prep, "font_size", r.font_size)
                r.layout_bounds = getattr(prep, "layout_bounds", getattr(r, "layout_bounds", None))
        layout_timing["fallback_ms"] += (_solver.perf_counter() - phase_start) * 1000.0
    if obstacles is None:
        obstacles = _solver.build_page_obstacle_map(regions, img.shape[:2])
    profiled_contrast(apply_free_text_contrast, ctx, regions, obstacles, inpaint_mask, layout_timing, collect_region_profile)
    ctx._bubble_detection_done = True
    ctx._bubble_layout_ready = True
    _solver._record_content_trace(regions, "layout")
    if timing is not None:
        timing.update(layout_timing)
