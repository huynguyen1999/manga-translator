"""Ordered candidate generation for free-text typography."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np

from ...config import Config
from .hard_line_breaks import HARD_LINE_BREAK
from .hard_line_break_layout import explicit_break_candidate
from .models import (
    FreeTextDamageTarget,
    LayoutCandidate,
    OriginalLayoutProfile,
    PageObstacleMap,
    PanelConstraint,
    SearchResult,
)
from .profiling import get_solver_profile
from .free_text_typography import (
    _free_text_candidate_ink_metrics,
    _free_text_line_height,
    _free_text_line_spacing,
    _free_text_typography_score,
    _free_text_words,
    _free_text_wrap_candidate,
    _panel_constraint_diagnostics,
)
from .line_breaking import _precompute_widths

_SPLIT_SHAPE_GAIN = 0.15
_FREE_TEXT_LOCAL_OFFSETS = (
    (0, 0), (4, 0), (-4, 0), (0, 4), (0, -4),
    (4, 4), (4, -4), (-4, 4), (-4, -4),
    (8, 0), (-8, 0), (0, 8), (0, -8),
)


def _free_text_offset_bbox_rejection(
    crop_box, visual_bbox, dx, dy, rel_dx, rel_dy, image_shape, domain_bbox, center_only,
):
    """Reject placements that cannot pass page or placement-domain bounds."""
    x1, y1, x2, y2 = crop_box
    tx, ty = dx + rel_dx, dy + rel_dy
    height, width = image_shape
    if x1 + tx < 0 or y1 + ty < 0 or x2 + tx > width or y2 + ty > height:
        return "page_bounds"
    if domain_bbox is not None and not center_only:
        left, top, right, bottom = domain_bbox
        vx1, vy1, vx2, vy2 = visual_bbox
        if vx1 + tx < left or vy1 + ty < top or vx2 + tx > right or vy2 + ty > bottom:
            return "local_domain"
    return None


def _group_typography_stages(candidates: List[LayoutCandidate]) -> List[List[LayoutCandidate]]:
    stages: List[List[LayoutCandidate]] = []
    last_key = None
    for candidate in candidates:
        key = (candidate.font_size, bool(candidate.qa.get("emergency_word_split")))
        if key != last_key:
            stages.append([])
            last_key = key
        stages[-1].append(candidate)
    return stages


def _search_first_viable_stage(
    stages: List[List[LayoutCandidate]], prepare: Any, cached: Dict[Tuple[int, int, int], SearchResult],
    profile: OriginalLayoutProfile, centroid: Tuple[float, float], target_width: float,
    target_height: float, zone: Any, obstacles: PageObstacleMap, other_text: Any,
    offset_search: Any, offset_refine: Any, search_offset: Any, validate_results=None, collect_stages=False,
    domain_bbox: Optional[Tuple[int, int, int, int]] = None,
    budget: Any = None, _local_pass: bool = True,
) -> List[SearchResult]:
    initial = budget is not None and budget.initial and _local_pass
    if initial:
        get_solver_profile().free_text_local_search_runs += 1
    retained = []
    for stage in stages:
        if budget is not None and budget.expired():
            break
        results = [result for result in cached.values() if any(result.typography_candidate is candidate for candidate in stage)]
        for candidate in stage:
            if budget is not None and budget.expired():
                break
            prepared = prepare(candidate)
            if prepared is None:
                continue
            raster, base_box, ink, visual, block, base_centroid, ink_bbox, dx, dy, radius = prepared
            if obstacles is not None:
                visual_y, visual_x = np.nonzero(visual)
                visual_bbox = (
                    base_box[0] + int(visual_x.min()),
                    base_box[1] + int(visual_y.min()),
                    base_box[0] + int(visual_x.max()) + 1,
                    base_box[1] + int(visual_y.max()) + 1,
                ) if len(visual_x) else (0, 0, 0, 0)
            else:
                visual_bbox = None
            offsets = []
            for rel_dx, rel_dy in (_FREE_TEXT_LOCAL_OFFSETS if initial else offset_search(radius)):
                if budget is not None and budget.expired():
                    break
                reason = None if obstacles is None else _free_text_offset_bbox_rejection(
                    base_box, visual_bbox, dx, dy, rel_dx, rel_dy,
                    obstacles.panel_mask.shape[:2], domain_bbox,
                    bool(getattr(zone, "domain_center_only", False)),
                )
                if reason is None:
                    offsets.append((rel_dx, rel_dy))
                else:
                    get_solver_profile().free_text_offsets_tested += 1
                    rejections = getattr(zone, "rejections", {})
                    rejections[reason] = rejections.get(reason, 0) + 1
                    zone.rejections = rejections
            best, best_offset, tested = float("inf"), None, set()
            for rel_dx, rel_dy in offsets:
                if budget is not None and budget.expired():
                    break
                tested.add((rel_dx, rel_dy))
                key = (id(candidate), rel_dx, rel_dy)
                if initial:
                    get_solver_profile().free_text_local_search_attempts += 1
                result = cached.pop(key, None) or search_offset(
                    candidate, raster, base_box, ink, visual, block, base_centroid, ink_bbox, dx, dy,
                    rel_dx, rel_dy, profile, centroid, target_width, target_height, zone, obstacles, other_text,
                )
                if result is not None:
                    candidate._local_search = initial
                    results.append(result)
                    if result.score < best:
                        best, best_offset = result.score, (rel_dx, rel_dy)
            if best_offset is not None and not initial:
                for rel_dx, rel_dy in offset_refine(*best_offset, step=2):
                    if budget is not None and budget.expired():
                        break
                    if (rel_dx, rel_dy) in tested:
                        continue
                    if obstacles is not None and _free_text_offset_bbox_rejection(
                        base_box, visual_bbox, dx, dy, rel_dx, rel_dy,
                        obstacles.panel_mask.shape[:2], domain_bbox,
                        bool(getattr(zone, "domain_center_only", False)),
                    ) is not None:
                        get_solver_profile().free_text_offsets_tested += 1
                        continue
                    result = search_offset(
                        candidate, raster, base_box, ink, visual, block, base_centroid, ink_bbox, dx, dy,
                        rel_dx, rel_dy, profile, centroid, target_width, target_height, zone, obstacles, other_text,
                    )
                    if result is not None:
                        results.append(result)
        if results and validate_results is not None:
            results = validate_results(results)
            if initial:
                get_solver_profile().free_text_local_search_successes += len(results)
        if not results and initial and not budget.expired():
            get_solver_profile().free_text_full_search_runs += 1
            results = _search_first_viable_stage(
                [stage], prepare, cached, profile, centroid, target_width, target_height,
                zone, obstacles, other_text, offset_search, offset_refine, search_offset,
                validate_results, False, domain_bbox, budget, _local_pass=False,
            )
        if results:
            if budget is not None and not budget.may_continue(validated=True, conflicting=collect_stages):
                return retained + (results[:2] if initial else results)
            if not collect_stages:
                return results[:2] if initial else results
            retained.extend(results[:2])
            if collect_stages and len(retained) >= 16:
                break
    return retained


def _free_text_hyphenation_stage(
    words: List[str], font_size: int, width_limit: float, language: str, allow_emergency=True,
) -> Optional[Tuple[List[str], str]]:
    from .readable_text import split_oversized_words
    split_words, changes = split_oversized_words(words, font_size, width_limit, language, allow_emergency)
    return (split_words, changes) if changes else None


def _free_text_font_stage_candidates(
    stage_words: List[str], font_size: int, tier: str, hyphen_count: int,
    hyphen_words: List[Dict[str, Any]], words: List[str], source_font: int, minimum: int,
    width_limit: float, profile: OriginalLayoutProfile, config: Config,
    image_shape: Tuple[int, int], target: Optional[FreeTextDamageTarget],
    panel_constraint: Optional[PanelConstraint], budget: Any = None,
) -> List[LayoutCandidate]:
    """Generate one font/hyphenation variant without changing its scoring."""
    render_cfg = config.render
    widths, space_width = _precompute_widths(stage_words, font_size)
    total_width = sum(widths) + max(0, len(stage_words) - 1) * space_width
    line_spacing = _free_text_line_spacing(render_cfg.line_spacing)
    line_height = _free_text_line_height(font_size, line_spacing)
    target_width = float(target.width if target is not None else profile.block_width)
    max_height = max(1.0, profile.block_height * 1.5, profile.block_height + 128 * max(image_shape[:2]) / 2048)
    target_height_value = min(float(target.height if target is not None else profile.block_height) * 1.1, max_height)
    max_width, panel_width, panel_meta = target_width, None, None
    if panel_constraint is not None:
        left, top, right, bottom = panel_constraint.bounds
        margin = max(0, int(panel_constraint.margin))
        panel_width = float(right - left - margin * 2)
        max_width = min(max_width, panel_width)
        max_height = min(max_height, bottom - top - margin * 2)
        if max_width <= 0 or max_height <= 0: return []
        target_width = min(target_width, float(max_width))
        target_height_value = min(target_height_value, float(max_height))
        panel_meta = _panel_constraint_diagnostics(panel_constraint)
    preferred_width = min(target_width, float(profile.block_width))
    max_width = panel_width if panel_width is not None else min(
        float(image_shape[1]), max(preferred_width, float(max(widths, default=0)))
    )
    target_ar = max(0.05, preferred_width / max(1.0, target_height_value))
    stage_candidates: List[LayoutCandidate] = []
    if HARD_LINE_BREAK in stage_words:
        candidate = explicit_break_candidate(stage_words, font_size, width_limit, max_height, line_height,
            space_width, line_spacing, preferred_width, target_height_value, target_ar, profile, target, panel_meta,
            allow_oversized_single_word=any("emergency" in change["strategies"] for change in hyphen_words))
        if candidate is not None:
            candidate.qa.update({
                "selected_tier": tier, "font_ratio": font_size / max(1.0, float(source_font)),
                "introduced_hyphen_count": hyphen_count,
                "introduced_hyphen_words": [change["word"] for change in hyphen_words],
                "wrapping_splits": hyphen_words, "absolute_minimum": minimum,
                "emergency_word_split": any("emergency" in change["strategies"] for change in hyphen_words),
            })
            stage_candidates.append(candidate)
        return stage_candidates

    preferred_lines = int(round(math.sqrt(total_width / max(1.0, target_ar * line_height))))
    preferred_lines = max(1, min(len(stage_words), preferred_lines))
    line_counts = sorted({max(1, min(len(stage_words), count)) for count in range(
        min(preferred_lines, profile.line_count) - 2, max(preferred_lines, profile.line_count) + 3,
    )})
    max_lines = max(1, int((max_height - font_size) // max(1, line_height)) + 1)
    line_counts = [count for count in line_counts if count <= max_lines]
    for line_count in line_counts:
        if budget is not None and budget.expired():
            break
        wrap_width = max(float(font_size * 2), total_width / line_count, target_ar * line_count * line_height)
        if max_width is not None:
            wrap_width = min(wrap_width, float(max_width))
        candidate = _free_text_wrap_candidate(stage_words, widths, space_width, font_size,
            line_spacing, line_count, wrap_width,
            max_line_width=float(max_width) if max_width is not None else None,
            allow_oversized_single_word=any("emergency" in change["strategies"] for change in hyphen_words))
        if candidate is None:
            continue
        candidate_width = max(line.x + line.width for line in candidate.lines) - min(line.x for line in candidate.lines)
        candidate_height = max(line.y + line.height for line in candidate.lines) - min(line.y for line in candidate.lines)
        if max_width is not None and candidate_width > max_width and not (
            any("emergency" in change["strategies"] for change in hyphen_words)
            and all(len(line.text.split()) == 1 for line in candidate.lines)
        ):
            continue
        if max_height is not None and candidate_height > max_height:
            continue
        ink = _free_text_candidate_ink_metrics(candidate)
        candidate.penalty = _free_text_typography_score(candidate, profile, target,
            target_width_override=preferred_width, target_height_override=target_height_value, ink_metrics=ink)
        candidate.qa = {
            "font_size": candidate.font_size, "line_spacing": candidate.line_spacing,
            "lines": [line.text for line in candidate.lines], "line_widths": [line.width for line in candidate.lines],
            "typography_score": candidate.penalty, "ink_bbox": list(ink["ink_bbox"]), "ink_width": ink["ink_width"],
            "ink_height": ink["ink_height"], "ink_area": ink["ink_area"],
            "ink_aspect_ratio": ink["ink_width"] / max(1.0, ink["ink_height"]), "ink_line_height": ink["ink_line_height"],
            "font_metric_height": ink["font_metric_height"], "baseline_advance": ink["baseline_advance"],
            "baseline_advance_min": ink["baseline_advance_min"], "baseline_advance_max": ink["baseline_advance_max"],
            "visible_gap": ink["visible_gap"], "baseline_consistent": ink["baseline_advance_min"] == ink["baseline_advance_max"],
            "line_gap_sane": ink["visible_gap"] <= max(4, ink["font_metric_height"] * 0.5),
            "target_width": preferred_width, "target_height": target_height_value, "target_aspect_ratio": target_ar,
            "selected_tier": tier, "font_ratio": font_size / max(1.0, float(source_font)),
            "introduced_hyphen_count": hyphen_count,
            "introduced_hyphen_words": [change["word"] for change in hyphen_words],
            "wrapping_splits": hyphen_words, "absolute_minimum": minimum,
            "emergency_word_split": any("emergency" in change["strategies"] for change in hyphen_words),
        }
        if panel_meta is not None:
            candidate.qa["panel_constraint"] = panel_meta
        stage_candidates.append(candidate)
    stage_candidates.sort(key=lambda candidate: candidate.penalty)
    return stage_candidates

from .free_text_typography_stages import _free_text_typography_candidates
