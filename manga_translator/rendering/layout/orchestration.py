"""Page-level layout orchestration for bubble and free-text regions."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Dict, List, Optional

import numpy as np

from ...geometry.bubbles import PageGeometry
from .models import LayoutCandidate, OriginalLayoutProfile, PlacementMode


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

    # Saved editor/rerender payloads carry the safe bubble interior instead
    # of the detector mask. Rehydrate it before classifying placement.
    for region in regions:
        if getattr(region, "_bubble_interior", None) is not None:
            continue
        safe_shape = getattr(region, "bubble_safe_shape", None)
        interior = _solver.decode_safe_shape(safe_shape, img.shape[0], img.shape[1])
        if interior is not None and _solver.np.any(interior):
            region._bubble_interior = interior

    # When detection is disabled, reuse the existing enclosed-bubble
    # inference as the geometry seed; the shape-aware solver owns layout.
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

    # Reuse the geometry produced during mask construction when available.
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
    bubble_groups = _solver._shared_bubble_groups(bubble_regions)
    minimum = render_cfg.font_size_minimum
    if minimum == -1:
        minimum = round(sum(img.shape[:2]) / 200)
    minimum = max(1, minimum)
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

        # Partition the bubble into geometry-aware placement zones
        zones = _solver.partition_bubble_zones(active, interior, lobe_graph=lobe_graph, apply_boundary_gap=(len(active) > 1))
        group.zones = zones

        plans = []
        for index, region in enumerate(active):
            zone_mask = zones[index] if index < len(zones) else interior
            plan = _solver._build_region_layout_plan(
                region=region,
                interior=interior,
                config=config,
                image_shape=img.shape[:2],
                solver_margin=solver_margin,
                solver_max_y_trials=solver_max_y_trials,
                preferred_mask=zone_mask if len(active) > 1 else None,
                top_k=_solver._JOINT_CANDIDATE_COUNT if len(active) > 1 else 1,
                zone_geometry_mask=zone_mask if len(active) > 1 else None,
                page_font_baseline=page_font_baseline,
            )
            if plan is not None:
                plans.append(plan)

        chosen = _solver._choose_joint_layout(plans, img.shape[:2]) if len(active) > 1 else None
        if len(active) == 1 and plans and plans[0].candidates:
            chosen = (plans[0].candidates[0],)

        if chosen is not None and len(chosen) == len(plans) == len(active):
            for plan, candidate in zip(plans, chosen):
                if not _solver._apply_layout_candidate(
                    plan, candidate, img.shape[:2], layout_debug=layout_debug
                ):
                    unplaced_regions.append(plan.region)
            active_ids = {id(region) for region in active}
            unplaced_regions.extend(region for region in group.regions if id(region) not in active_ids)
            continue

        active_ids = {id(region) for region in active}
        for region in group.regions:
            if id(region) in active_ids and not getattr(region, "_render_suppressed", False):
                region._solver_status = "requires_compression"
                unplaced_regions.append(region)
    layout_timing["bubble_solver_ms"] = (_solver.perf_counter() - phase_start) * 1000.0
    if not legacy_only and free_regions:
        phase_start = _solver.perf_counter()
        bubble_halo = max(2, int(round((render_cfg.font_size or 12) * 0.20)))
        obstacles = _solver.build_page_obstacle_map(regions, img.shape[:2], bubble_halo=bubble_halo)
        inpaint_mask = getattr(ctx, "inpaint_mask", None)
        if inpaint_mask is None:
            inpaint_mask = getattr(ctx, "text_mask", None)
        if inpaint_mask is None:
            inpaint_mask = getattr(ctx, "mask_raw", None)
        if inpaint_mask is None:
            inpaint_mask = getattr(ctx, "mask", None)
        free_zones = _solver.build_free_text_ownership_zones(
            free_regions, obstacles, inpaint_mask=inpaint_mask, image=img, other_regions=regions
        )
        free_plans: Dict[int, List[LayoutCandidate]] = {}
        free_profiles: Dict[int, OriginalLayoutProfile] = {}
        for region in free_regions:
            ft_zone = free_zones.get(id(region))
            if ft_zone is None:
                continue
            result = _solver._solve_free_text_region(
                region=region,
                zone=ft_zone,
                obstacles=obstacles,
                config=config,
                image_shape=img.shape[:2],
                solver_margin=solver_margin,
                solver_max_y_trials=solver_max_y_trials,
                allow_early_accept=True,
                shadow_compare=bool(getattr(ctx, "_layout_shadow_compare", False)),
            )
            if result is None:
                region._solver_path = "free_text"
                region._solver_status = "no_valid_layout"
                region._solver_qa = {
                    "placement_mode": PlacementMode.FREE_TEXT.value,
                    "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text", "panel_bounds"],
                    "panel_constraint": _solver._panel_constraint_diagnostics(ft_zone.panel_constraint),
                }
                region._render_suppressed = region.review_required = True
                region.review_reason = region.review_reason or "no_valid_layout"
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
                result = _solver._solve_free_text_region(
                    region=region,
                    zone=ft_zone,
                    obstacles=obstacles,
                    config=config,
                    image_shape=img.shape[:2],
                    solver_margin=solver_margin,
                    solver_max_y_trials=solver_max_y_trials,
                    allow_early_accept=False,
                    shadow_compare=False,
                    force_exhaustive=True,
                )
                if result is None:
                    free_plans.pop(id(region), None)
                    free_profiles.pop(id(region), None)
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
                    region.review_reason = region.review_reason or "no_joint_layout"
                    region._solver_qa = {
                        "placement_mode": PlacementMode.FREE_TEXT.value,
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
                    "placement_mode": PlacementMode.FREE_TEXT.value,
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

    # Store layout groups on ctx for diagnostic overlay generation
    ctx._bubble_layout_groups = bubble_groups

    # If any regions were not placed by the shape-aware solver, run fallback
    group_regions = bool(
        getattr(getattr(config, "bubble_detection", None), "group_regions", False)
    )
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

    ctx._bubble_detection_done = True
    ctx._bubble_layout_ready = True
    _solver._record_content_trace(regions, "layout")
    if timing is not None:
        timing.update(layout_timing)
