"""Ordered candidate generation for free-text typography."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

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
) -> List[SearchResult]:
    retained = []
    for stage in stages:
        results = [result for result in cached.values() if any(result.typography_candidate is candidate for candidate in stage)]
        for candidate in stage:
            prepared = prepare(candidate)
            if prepared is None:
                continue
            raster, base_box, ink, visual, block, base_centroid, ink_bbox, dx, dy, radius = prepared
            offsets = offset_search(radius)
            best, best_offset, tested = float("inf"), None, set()
            for rel_dx, rel_dy in offsets:
                tested.add((rel_dx, rel_dy))
                key = (id(candidate), rel_dx, rel_dy)
                result = cached.pop(key, None) or search_offset(
                    candidate, raster, base_box, ink, visual, block, base_centroid, ink_bbox, dx, dy,
                    rel_dx, rel_dy, profile, centroid, target_width, target_height, zone, obstacles, other_text,
                )
                if result is not None:
                    results.append(result)
                    if result.score < best:
                        best, best_offset = result.score, (rel_dx, rel_dy)
            if best_offset is not None:
                for rel_dx, rel_dy in offset_refine(*best_offset, step=2):
                    if (rel_dx, rel_dy) in tested:
                        continue
                    result = search_offset(
                        candidate, raster, base_box, ink, visual, block, base_centroid, ink_bbox, dx, dy,
                        rel_dx, rel_dy, profile, centroid, target_width, target_height, zone, obstacles, other_text,
                    )
                    if result is not None:
                        results.append(result)
        if results and validate_results is not None:
            results = validate_results(results)
        if results:
            if not collect_stages:
                return results
            retained.extend(results[:2])
            if len(retained) >= 16:
                break
    return retained


def _free_text_hyphenation_stage(
    words: List[str], font_size: int, width_limit: float, language: str, allow_emergency=True,
) -> Optional[Tuple[List[str], str]]:
    from .readable_text import split_oversized_words
    split_words, changes = split_oversized_words(words, font_size, width_limit, language, allow_emergency)
    return (split_words, changes) if changes else None


def _free_text_typography_candidates(
    text: str,
    profile: OriginalLayoutProfile,
    config: Config,
    image_shape: Tuple[int, int],
    target: Optional[FreeTextDamageTarget] = None,
    target_height: Optional[int] = None,
    panel_constraint: Optional[PanelConstraint] = None,
    language: str = "en_US",
    preserve: bool = False,
) -> List[LayoutCandidate]:
    """Generate frozen paragraph candidates; ``target_height`` is legacy-only."""
    # Keep the old keyword source-compatible, but never turn erased height into leading.
    del target_height
    words = _free_text_words(text)
    if not words:
        return []

    render_cfg = config.render
    from .readable_text import readable_font_minimum
    minimum = readable_font_minimum(render_cfg, image_shape)
    source_font = max(minimum, int(round(profile.font_size)))
    preferred = max(minimum, int(render_cfg.font_size or source_font))
    font_stages = []
    width_limit = min(
        float(target.width if target is not None else profile.block_width),
        float(profile.block_width),
    )
    if panel_constraint is not None:
        left, _, right, _ = panel_constraint.bounds
        width_limit = min(
            width_limit,
            float(right - left - max(0, int(panel_constraint.margin)) * 2),
        )
    emergency_stages = []
    for size in range(preferred, minimum - 1, -1):
        tier = "source" if size == preferred else f"shrink_{size}"
        font_stages.append((size, tier, words, 0, []))
        if not preserve and not getattr(render_cfg, "no_hyphenation", False):
            rescue = _free_text_hyphenation_stage(words, size, max(1, width_limit), language, False)
            if rescue:
                split_words, changes = rescue
                font_stages.append((size, f"{tier}_wrap", split_words, sum(len(c["fragments"]) - 1 for c in changes), changes))
            emergency = _free_text_hyphenation_stage(words, size, max(1, width_limit), language)
            if emergency and any("emergency" in c["strategies"] for c in emergency[1]):
                emergency_stages.append((size, f"{tier}_emergency", emergency[0], sum(len(c["fragments"]) - 1 for c in emergency[1]), emergency[1]))
    font_stages.extend(emergency_stages)
    candidates: List[LayoutCandidate] = []
    for font_size, tier, stage_words, hyphen_count, hyphen_words in font_stages:
        stage_candidates: List[LayoutCandidate] = []
        widths, space_width = _precompute_widths(stage_words, font_size)
        total_width = sum(widths) + max(0, len(stage_words) - 1) * space_width
        line_spacing = _free_text_line_spacing(render_cfg.line_spacing)
        line_height = _free_text_line_height(font_size, line_spacing)
        target_width = float(target.width if target is not None else profile.block_width)
        max_height = max(1.0, profile.block_height * 1.5, profile.block_height + 128 * max(image_shape[:2]) / 2048)
        target_height_value = min(float(target.height if target is not None else profile.block_height) * 1.1, max_height)
        max_width = target_width
        panel_width = None
        panel_meta = None
        if panel_constraint is not None:
            left, top, right, bottom = panel_constraint.bounds
            margin = max(0, int(panel_constraint.margin))
            panel_width = float(right - left - margin * 2)
            max_width = min(max_width, panel_width)
            max_height = min(max_height, bottom - top - margin * 2)
            if max_width <= 0 or max_height <= 0:
                continue
            target_width = min(target_width, float(max_width))
            target_height_value = min(target_height_value, float(max_height))
            panel_meta = _panel_constraint_diagnostics(panel_constraint)
        preferred_width = min(target_width, float(profile.block_width))
        max_width = panel_width if panel_width is not None else min(
            float(image_shape[1]), max(preferred_width, float(max(widths, default=0)))
        )
        target_ar = max(0.05, preferred_width / max(1.0, target_height_value))
        if HARD_LINE_BREAK in stage_words:
            candidate = explicit_break_candidate(
                stage_words, font_size, width_limit, max_height, line_height, space_width,
                line_spacing, preferred_width, target_height_value, target_ar,
                profile, target, panel_meta,
            )
            if candidate is not None:
                candidate.qa.update({
                    "selected_tier": tier,
                    "font_ratio": font_size / max(1.0, float(source_font)),
                    "introduced_hyphen_count": hyphen_count,
                    "introduced_hyphen_words": [change["word"] for change in hyphen_words],
                    "wrapping_splits": hyphen_words, "absolute_minimum": minimum,
                    "emergency_word_split": any("emergency" in change["strategies"] for change in hyphen_words),
                })
                stage_candidates.append(candidate)
            candidates.extend(stage_candidates)
            continue
        preferred_lines = int(round(math.sqrt(total_width / max(1.0, target_ar * line_height))))
        preferred_lines = max(1, min(len(stage_words), preferred_lines))
        line_counts = sorted({
            max(1, min(len(stage_words), count))
            for count in range(min(preferred_lines, profile.line_count) - 2, max(preferred_lines, profile.line_count) + 3)
        })
        max_lines = max(1, int((max_height - font_size) // max(1, line_height)) + 1)
        line_counts = [count for count in line_counts if count <= max_lines]
        for line_count in line_counts:
            wrap_width = max(
                float(font_size * 2),
                total_width / line_count,
                target_ar * line_count * line_height,
            )
            if max_width is not None:
                wrap_width = min(wrap_width, float(max_width))
            candidate = _free_text_wrap_candidate(
                stage_words, widths, space_width, font_size,
                line_spacing, line_count, wrap_width,
                max_line_width=float(max_width) if max_width is not None else None,
            )
            if candidate is None:
                continue
            candidate_width = max(line.x + line.width for line in candidate.lines) - min(line.x for line in candidate.lines)
            candidate_height = max(line.y + line.height for line in candidate.lines) - min(line.y for line in candidate.lines)
            if max_width is not None and candidate_width > max_width:
                continue
            if max_height is not None and candidate_height > max_height:
                continue
            candidate.penalty = _free_text_typography_score(
                candidate, profile, target,
                target_width_override=preferred_width,
                target_height_override=target_height_value,
            )
            ink = _free_text_candidate_ink_metrics(candidate)
            candidate.qa = {
                "font_size": candidate.font_size,
                "line_spacing": candidate.line_spacing,
                "lines": [line.text for line in candidate.lines],
                "line_widths": [line.width for line in candidate.lines],
                "typography_score": candidate.penalty,
                "ink_bbox": list(ink["ink_bbox"]), "ink_width": ink["ink_width"],
                "ink_height": ink["ink_height"], "ink_area": ink["ink_area"],
                "ink_aspect_ratio": ink["ink_width"] / max(1.0, ink["ink_height"]), "ink_line_height": ink["ink_line_height"],
                "font_metric_height": ink["font_metric_height"], "baseline_advance": ink["baseline_advance"],
                "baseline_advance_min": ink["baseline_advance_min"], "baseline_advance_max": ink["baseline_advance_max"],
                "visible_gap": ink["visible_gap"], "baseline_consistent": ink["baseline_advance_min"] == ink["baseline_advance_max"],
                "line_gap_sane": ink["visible_gap"] <= max(4, ink["font_metric_height"] * 0.5), "target_width": preferred_width,
                "target_height": target_height_value, "target_aspect_ratio": target_ar,
                "selected_tier": tier,
                "font_ratio": font_size / max(1.0, float(source_font)),
                "introduced_hyphen_count": hyphen_count,
                "introduced_hyphen_words": [change["word"] for change in hyphen_words],
                    "wrapping_splits": hyphen_words, "absolute_minimum": minimum,
                    "emergency_word_split": any("emergency" in change["strategies"] for change in hyphen_words),
            }
            if panel_meta is not None:
                candidate.qa["panel_constraint"] = panel_meta
            stage_candidates.append(candidate)
        stage_candidates.sort(key=lambda candidate: candidate.penalty)
        candidates.extend(stage_candidates)

    unique, seen = [], set()
    for candidate in candidates:
        key = (
            candidate.font_size,
            candidate.qa.get("selected_tier"),
            tuple(line.text for line in candidate.lines),
        )
        if key not in seen:
            unique.append(candidate)
            seen.add(key)

    unsplit_scores = {}
    for candidate in unique:
        if not candidate.qa.get("introduced_hyphen_count"):
            unsplit_scores[candidate.font_size] = min(
                candidate.penalty,
                unsplit_scores.get(candidate.font_size, float("inf")),
            )
    severe_split_words = {}
    for size in {candidate.font_size for candidate in unique if candidate.qa.get("introduced_hyphen_count")}:
        widths, _ = _precompute_widths(words, size)
        overwide = {word for word, width in zip(words, widths) if width >= width_limit * 1.5}
        if overwide and any(
            overwide <= set(candidate.qa["introduced_hyphen_words"])
            for candidate in unique if candidate.font_size == size and candidate.qa.get("introduced_hyphen_count")
        ):
            severe_split_words[size] = overwide
    return [
        candidate for candidate in unique
        if (candidate.font_size in severe_split_words
            and severe_split_words[candidate.font_size] <= set(candidate.qa["introduced_hyphen_words"]))
        or (candidate.font_size not in severe_split_words and (
            not candidate.qa.get("introduced_hyphen_count")
            or candidate.font_size not in unsplit_scores
            or candidate.penalty <= unsplit_scores[candidate.font_size] * (1.0 - _SPLIT_SHAPE_GAIN)
        ))
    ]
