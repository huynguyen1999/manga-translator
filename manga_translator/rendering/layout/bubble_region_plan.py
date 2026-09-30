"""Bubble-region typography plans and rescue policy."""

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from ...config import Config
from ...utils import is_preserved_region
from .models import LayoutCandidate, PlacementMode, LobeGraph
from .search_budget import get_search_budget, small_text_rescue_minimum

def _build_region_layout_plan(
    region: Any,
    interior: np.ndarray,
    config: Config,
    image_shape: Tuple[int, int],
    solver_margin: float,
    solver_max_y_trials: int,
    preferred_mask: Optional[np.ndarray],
    top_k: int,
    zone_geometry_mask: Optional[np.ndarray] = None,
    lobe_graph: Optional[LobeGraph] = None,
    page_font_baseline: Optional[int] = None,
):
    from .solver import (
        _render_text, _estimate_adaptive_font_size, build_region_font_policy,
        fg_bg_compare, stroke, BubbleGeometry, build_original_layout_profile,
        split_layout_words, solve_layout, _compression_severity, _max_usable_row_width,
        _cached_row_slot_table, _RegionLayoutPlan,
    )
    text = _render_text(region)
    if not text.strip() or interior is None or not np.any(interior):
        return None

    budget = get_search_budget()
    if budget is not None and budget.initial:
        top_k = 2
    render_cfg = config.render
    from .readable_text import readable_font_minimum
    minimum = readable_font_minimum(render_cfg, image_shape)

    adaptive_target = _estimate_adaptive_font_size(interior, text, minimum)
    effective_baseline = None if is_preserved_region(region) else page_font_baseline

    font_policy = build_region_font_policy(
        region=region,
        adaptive_target=adaptive_target,
        page_baseline=effective_baseline,
        minimum=minimum,
        render_config=render_cfg,
    )

    region.calibrated_font_size = font_policy.preferred_size
    target = font_policy.preferred_size
    render_mode = getattr(region, "placement_mode", None)
    is_bubble = render_mode is PlacementMode.BUBBLE or render_mode == PlacementMode.BUBBLE.value

    fg, bg = fg_bg_compare(*region.get_font_colors())
    stroke_width = stroke.get_text_stroke_width(
        target, bg, getattr(region, "bg_colors", None)
    )

    active_mask = zone_geometry_mask if zone_geometry_mask is not None and np.any(zone_geometry_mask) else interior
    cache = getattr(budget, "bubble_preparations", {}) if budget else {}
    key = (id(region), active_mask.shape, hash(active_mask.tobytes()))
    if key not in cache:
        cache[key] = (BubbleGeometry(active_mask), build_original_layout_profile(region, interior))
    geom, source_profile = cache[key]
    if budget is not None:
        budget.bubble_preparations = cache
    zone_local = None
    if preferred_mask is not None:
        y1, y2 = geom.y_offset, geom.y_offset + geom.shape[0]
        x1, x2 = geom.x_offset, geom.x_offset + geom.shape[1]
        zone_local = preferred_mask[y1:y2, x1:x2]

    # ---------------------------------------------------------
    # Stage A — Normal word wrapping stays within the preferred font range.
    # ---------------------------------------------------------
    stage_a_result = solve_layout(
        geom=geom,
        words=split_layout_words(text),
        font_size_max=font_policy.preferred_size,
        font_size_min=font_policy.consistency_floor,
        language=getattr(region, "target_lang", "en_US") or "en_US",
        hyphenate=False,
        line_spacing=render_cfg.line_spacing or 0.0,
        stroke_width=stroke_width,
        margin=solver_margin,
        y_origin_step=max(2, target // 8),
        max_y_origin_trials=solver_max_y_trials,
        source_profile=source_profile,
        preferred_mask=zone_local,
        top_k=top_k,
        is_single_region=(preferred_mask is None or not np.any(preferred_mask)),
        lobe_graph=lobe_graph,
    )
    candidates_a = stage_a_result if isinstance(stage_a_result, list) else ([stage_a_result] if stage_a_result is not None else [])
    normal = candidates_a[0] if candidates_a else None

    emergency_floor = font_policy.absolute_minimum
    emergency_candidates: List[LayoutCandidate] = []
    if (normal is None or normal.qa.get("center_error_px", 0) > normal.font_size / 2) and is_bubble and font_policy.consistency_floor - 1 >= emergency_floor:
        emergency_result = solve_layout(
            geom=geom,
            words=split_layout_words(text),
            font_size_max=font_policy.consistency_floor - 1,
            font_size_min=emergency_floor,
            language=getattr(region, "target_lang", "en_US") or "en_US",
            hyphenate=False,
            line_spacing=render_cfg.line_spacing or 0.0,
            stroke_width=stroke_width,
            margin=solver_margin,
            y_origin_step=max(2, target // 8),
            max_y_origin_trials=solver_max_y_trials,
            source_profile=source_profile,
            preferred_mask=zone_local,
            top_k=top_k,
            is_single_region=(preferred_mask is None or not np.any(preferred_mask)),
            lobe_graph=lobe_graph,
        )
        emergency_candidates = emergency_result if isinstance(emergency_result, list) else (
            [emergency_result] if emergency_result is not None else []
        )

    # Normal search outcome
    normal_font_size = normal.font_size if normal else None
    normal_ratio = (normal.font_size / float(max(1, font_policy.region_target))) if normal else None

    diagnostics: Dict[str, Any] = {
        "explicit_font_size": bool(getattr(render_cfg, "font_size", None) and render_cfg.font_size > 0),
        "region_target": font_policy.region_target,
        "region_geometric_target": font_policy.region_target,
        "calibrated_target": font_policy.preferred_size,
        "page_font_baseline": font_policy.page_baseline,
        "page_baseline": font_policy.page_baseline,
        "preferred_size": font_policy.preferred_size,
        "consistency_floor": font_policy.consistency_floor,
        "mild_compression_floor": font_policy.mild_compression_floor,
        "absolute_minimum": font_policy.absolute_minimum,
        "normal_font_size": normal_font_size,
        "normal_ratio": round(normal_ratio, 4) if normal_ratio is not None else None,
        "font_ratio": round(normal_ratio, 4) if normal_ratio is not None else None,
        "font_ratio_before_rescue": round(normal_ratio, 4) if normal_ratio is not None else None,
        "compression_from_page_ratio": round(
            normal.font_size / float(max(1, font_policy.page_baseline)), 4
        ) if font_policy.page_baseline and normal else None,
        "compression_severity": _compression_severity(normal_ratio) if normal_ratio is not None else "infeasible",
        "hyphenation_rescue_attempted": False,
        "hyphenation_attempted": False,
        "hyphenation_selected": False,
        "hyphenation_rescue_selected": False,
        "hyphenation_reason": "disabled_by_config" if render_cfg.no_hyphenation else ("satisfactory_font_ratio" if normal is not None else "stage_a_failed"),
        "longest_word": None,
        "longest_word_width": 0,
        "max_usable_row_width": 0,
        "word_pressure_ratio": 0.0,
        "width_pressure_ratio": 0.0,
        "long_word_bottleneck": False,
        "bottleneck_word": None,
        "rescue_candidate_font_size": None,
        "rescue_font_size": None,
        "final_font_size": normal.font_size if normal else None,
        "introduced_hyphen_count": 0,
        "introduced_hyphen_words": [],
        "font_policy_status": "preferred" if normal is not None else ("emergency_review" if emergency_candidates else "infeasible"),
        "emergency_compression": bool(emergency_candidates),
    }

    # Try all oversized tokens, including compounds, before accepting a tiny layout.
    from .readable_text import split_oversized_words, centered_candidate_key
    candidates = list(candidates_a) + list(emergency_candidates)
    for candidate in candidates:
        candidate.qa.update({key: value for key, value in diagnostics.items() if key not in candidate.qa})
    if normal is None and is_bubble and not is_preserved_region(region) and not render_cfg.no_hyphenation:
        for allow_emergency in (False, True):
            if allow_emergency and candidates:
                break
            for size in range(font_policy.preferred_size, minimum - 1, -1):
                if budget is not None and budget.expired():
                    break
                max_width = _max_usable_row_width(geom, size, stroke_width, solver_margin, min_width=max(size, 8))
                row_widths = [
                    max((slot.width for slot in slots), default=0)
                    for slots in _cached_row_slot_table(geom, size, stroke_width, solver_margin, max(size, 8)).values()
                ]
                row_widths = [w for w in row_widths if w > 0]
                central_width = int(np.percentile(row_widths, 50)) if row_widths else max_width
                trial_widths = [max_width] + ([central_width] if 0 < central_width < max_width else [])
                size_rescued = []
                for width in trial_widths:
                    if budget is not None and budget.expired():
                        break
                    words, splits = split_oversized_words(split_layout_words(text), size, width, getattr(region, "target_lang", "ENG"), allow_emergency=allow_emergency)
                    if not splits:
                        continue
                    diagnostics["hyphenation_rescue_attempted"] = True
                    result = solve_layout(
                        geom, words, size, size, language=getattr(region, "target_lang", "ENG"),
                        hyphenate=False, line_spacing=render_cfg.line_spacing or 0.0,
                        stroke_width=stroke_width, margin=solver_margin,
                        max_y_origin_trials=solver_max_y_trials, source_profile=source_profile,
                        preferred_mask=zone_local, top_k=top_k, lobe_graph=lobe_graph,
                        is_single_region=(preferred_mask is None or not np.any(preferred_mask)),
                    )
                    rescued = result if isinstance(result, list) else ([result] if result else [])
                    for candidate in rescued:
                        candidate.qa.update({key: value for key, value in diagnostics.items() if key not in candidate.qa})
                        candidate.qa.update({"wrapping_splits": splits, "hyphenation_selected": True,
                            "hyphenation_rescue_selected": True, "font_policy_status": "hyphen_rescue",
                            "emergency_word_split": any("emergency" in split["strategies"] for split in splits),
                            "introduced_hyphen_count": sum(strategy != "existing_break" for split in splits for strategy in split["strategies"]),
                            "introduced_hyphen_words": [split["word"] for split in splits],
                            "hyphenation_reason": "oversized_token", "emergency_compression": False})
                    size_rescued.extend(rescued)
                    if any(c.qa.get("center_error_px", float("inf")) <= c.font_size / 2 for c in size_rescued):
                        break
                candidates.extend(size_rescued)
                if any(c.qa.get("center_error_px", float("inf")) <= c.font_size / 2 for c in size_rescued):
                    break
    rescue_minimum = small_text_rescue_minimum(region, render_cfg, image_shape)
    if (not candidates and budget is not None and budget.phase == "rescue"
            and not budget.expired() and rescue_minimum is not None and rescue_minimum < minimum):
        result = solve_layout(
            geom, split_layout_words(text), minimum - 1, rescue_minimum,
            language=getattr(region, "target_lang", "ENG"), hyphenate=False,
            line_spacing=render_cfg.line_spacing or 0.0, stroke_width=stroke_width,
            margin=solver_margin, max_y_origin_trials=solver_max_y_trials,
            source_profile=source_profile, preferred_mask=zone_local, top_k=top_k,
            lobe_graph=lobe_graph, is_single_region=(zone_local is None),
        )
        candidates = result if isinstance(result, list) else ([result] if result else [])
        for candidate in candidates:
            candidate.qa.update(diagnostics)
            candidate.qa.update(small_text_rescue=True, rescue_minimum=rescue_minimum)
        budget.rescue_usage += bool(candidates)
    candidates.sort(key=centered_candidate_key)
    for candidate in candidates:
        candidate.qa["final_font_size"] = candidate.font_size
        candidate.qa["absolute_minimum"] = minimum
        candidate.qa["font_ratio_after_rescue"] = candidate.font_size / max(1, target)
    if candidates:
        diagnostics.update(candidates[0].qa)
        if candidates[0].font_size < target * 0.85:
            region.review_required, region.review_reason = True, "text_requires_emergency_compression"
        if candidates[0].qa.get("center_error_px", 0) > candidates[0].font_size / 2:
            region.review_required, region.review_reason = True, "closest_valid_bubble_placement"
        if candidates[0].qa.get("emergency_word_split"):
            region.review_required = True
            region.review_reason = "emergency_word_split"

    if budget is not None and budget.initial:
        candidates = candidates[:2]
    # Store policy diagnostics on region
    region._font_policy_diagnostics = dict(diagnostics)
    if not candidates:
        region.review_required = True
        region.review_reason = getattr(region, "review_reason", None) or "text_does_not_fit"
        region.font_size = font_policy.preferred_size
        region._render_suppressed = True
        region._solver_status = "font_policy_infeasible"
        for attr in ("layout_segments", "layout_bounds", "_bubble_box", "_bubble_points"):
            if hasattr(region, attr):
                setattr(region, attr, [] if attr == "layout_segments" else None)

    return _RegionLayoutPlan(
        region=region,
        text=text,
        source_profile=source_profile,
        candidates=candidates,
        fg=fg,
        bg=bg,
        line_spacing=render_cfg.line_spacing or 0.0,
        language=getattr(region, "target_lang", "en_US") or "en_US",
        zone_mask=zone_geometry_mask,
    )

