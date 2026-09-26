"""Production shape-aware bubble and free-text layout solver.

This module is the production home for the layout algorithm exercised by the
pipeline step runner. The runner imports these symbols for diagnostics and
fast rendering; production rendering calls the same entry point directly.
"""

from __future__ import annotations

import itertools
import logging
import math
import os
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

from .. import (
    _points_for_rect,
    fg_bg_compare,
    get_default_eng_font,
    stroke,
    text_render,
)
from ..bubble_layout import (
    _estimate_adaptive_font_size,
    decode_safe_shape,
    encode_safe_shape,
    prepare_bubbles,
    render_positioned_lines,
)
from ...geometry.bubbles import PageGeometry, prepare_page_geometry
from ...utils import is_preserved_region
from .geometry import (
    BubbleGeometry,
    build_lobe_graph,
    compute_placement_target,
    compute_zone_shape_profile,
    _mask_moments,
)
from .models import (
    BandSlot,
    RegionFontPolicy,
    BubbleLayoutGroup,
    CandidateRaster,
    FreeTextDamageTarget,
    FreeTextZone,
    LayoutCandidate,
    LobeGraph,
    OriginalLayoutProfile,
    PageObstacleMap,
    PanelConstraint,
    PlacementTarget,
    PlacementMode,
    PlacedLine,
    ScanInterval,
    SearchResult,
    ZoneShapeProfile,
)
from .ownership import build_free_text_ownership_zones
from .obstacles import build_page_obstacle_map, classify_placement_modes
from .regions import prepare_regions as _ensure_region_identities
from .raster import (
    _candidate_cropped_visual_masks,
    _candidate_global_glyph_mask,
    _candidate_raster_at_offset,
    _candidate_visual_masks,
    _cropped_masks_overlap,
    _render_line_alpha,
    rasterize_candidate,
)


from .line_breaking import (
    RowGeometry,
    _FUNCTION_WORDS,
    _PUNCT_STRIP,
    _WEIGHT_TRANS_BRANCH,
    _WEIGHT_TRANS_OVERLAP,
    _WEIGHT_TRANS_XJUMP,
    _WEIGHT_VERTICAL_GAP,
    _build_row_slot_table,
    _cached_placement_target,
    _cached_row_slot_table,
    _cached_zone_shape_profile,
    _dp_word_break_rows,
    _line_break_cost,
    _max_usable_row_width,
    _phrase_break_penalty,
    _precompute_widths,
    _row_can_fit_word,
    _transition_cost,
    _try_placement_rows,
    _word_core,
    _y_origin_sequence,
)
from .paragraph_flow import (
    GAP_DISCONNECTED_SAFE_REGION,
    GAP_LOCAL_GEOMETRY,
    GAP_LOBE_NECK,
    GAP_NORMAL,
    GAP_SOURCE_BREAK,
    GAP_UNEXPLAINED,
    _classify_adjacent_gaps,
    _compact_vertical_rhythm,
    _is_geometric_obstruction,
)
from .font_policy import (
    PAGE_FONT_FLOOR_RATIO,
    PREFERRED_FONT_MAX_DROP_PX,
    build_region_font_policy,
    compression_severity as _compression_severity,
    page_font_penalty as _page_font_penalty,
)

from .profiling import (
    SolverProfileStats,
    _SOLVER_PROFILE,
    _record_content_trace,
    _render_text,
    get_solver_profile,
    reset_solver_profile,
)

from .solve_core import _line_height, solve_layout


logger = logging.getLogger("layout.solver")

_JOINT_CANDIDATE_COUNT = 5

# Staged Font Policy & Hyphen Rescue Constants
MAX_PAGE_UPLIFT = 3
HYPHEN_RESCUE_TRIGGER_RATIO = 0.90
HYPHEN_MIN_GAIN_RATIO = 1.08
HYPHEN_MIN_GAIN_PX = 2
MAX_AUTOMATIC_HYPHENS_PER_REGION = 1



from .centering import _center_layout_block, _optimize_x, _x_cost


from .text_normalization import (
    _COMPOUND_PREFIXES,
    _DICTIONARY_CACHE,
    _DICTIONARY_PATHS,
    _dict_contains,
    _load_dictionary,
    normalize_words,
    split_layout_words,
)

from .free_text_search import (
    _clamp_free_text_translation,
    _free_text_footprint_qa,
    _free_text_hard_valid,
    _free_text_ink_overflow,
    _free_text_ink_overflow_from_raster,
    _free_text_ink_overflow_in_crop,
    _free_text_offset_refine,
    _free_text_offset_search,
    _free_text_search_result,
    _free_text_shift_candidate,
    _measure_damage_coverage_crop,
)
from .free_text_typography import (
    _FREE_TEXT_MAX_LINE_SPACING,
    _free_text_candidate_ink_metrics,
    _free_text_font_metrics,
    _free_text_line_height,
    _free_text_line_spacing,
    _free_text_typography_score,
    _free_text_typography_candidates,
    _free_text_words,
    _free_text_wrap_candidate,
    _mask_metrics,
    _panel_constraint_diagnostics,
)


from .free_text_solver import (
    _free_text_fast_gate,
    _layout_env_disabled,
    _layout_env_enabled,
    _log_free_text_shadow_comparison,
    _materialize_free_text_search_result,
    _solve_free_text_region,
)


def _select_free_text_joint_candidates(
    regions: List[Any],
    plans: Dict[int, List[LayoutCandidate]],
    image_shape: Tuple[int, int],
    free_profiles: Optional[Dict[int, OriginalLayoutProfile]] = None,
) -> Dict[int, LayoutCandidate]:
    """Select optimal joint combination for all free-text regions without touching bubbles."""
    if not regions:
        return {}

    # Check pairwise candidates for collision and choose combination with lowest total penalty
    free_plans = [
        _RegionLayoutPlan(
            region=r,
            text=_render_text(r),
            source_profile=(free_profiles.get(id(r)) if free_profiles else getattr(r, "_source_profile", None)),
            candidates=plans.get(id(r), []),
            fg=getattr(r, "fg_color", (0, 0, 0)),
            bg=getattr(r, "bg_color", (255, 255, 255)),
            line_spacing=0.1,
            language=getattr(r, "target_lang", "ENG") or "ENG",
        )
        for r in regions if plans.get(id(r))
    ]
    if not free_plans:
        return {}

    selected: Dict[int, LayoutCandidate] = {}
    data = {
        id(candidate): _candidate_data(candidate, image_shape)[:2]
        for plan in free_plans for candidate in plan.candidates
    }
    conflicts = [set() for _ in free_plans]
    for first in range(len(free_plans)):
        for second in range(first + 1, len(free_plans)):
            overlap = any(
                _cropped_masks_overlap(*data[id(a)], *data[id(b)])
                for a in free_plans[first].candidates
                for b in free_plans[second].candidates
            )
            if overlap:
                conflicts[first].add(second)
                conflicts[second].add(first)

    components = []
    unseen = set(range(len(free_plans)))
    while unseen:
        pending = [unseen.pop()]
        component = []
        while pending:
            index = pending.pop()
            component.append(index)
            connected = conflicts[index] & unseen
            unseen -= connected
            pending.extend(connected)
        components.append(component)

    for component in components:
        component_plans = [free_plans[index] for index in component]
        chosen = _choose_joint_layout(component_plans, image_shape)
        if chosen is not None:
            selected.update({id(plan.region): candidate for plan, candidate in zip(component_plans, chosen)})
            continue

        # ponytail: cap dense-cluster search at 4096 states; raise only if real pages exceed that.
        ordered = sorted(component, key=lambda i: (len(free_plans[i].candidates), -len(conflicts[i])))
        best = []
        best_cost = float("inf")
        visited = 0

        def search(position, chosen_pairs, cost):
            nonlocal best, best_cost, visited
            if visited >= _MAX_JOINT_LAYOUT_COMBINATIONS:
                return
            visited += 1
            if len(chosen_pairs) + len(ordered) - position < len(best):
                return
            if position == len(ordered):
                if len(chosen_pairs) > len(best) or (len(chosen_pairs) == len(best) and cost < best_cost):
                    best, best_cost = chosen_pairs[:], cost
                return
            index = ordered[position]
            for candidate in sorted(free_plans[index].candidates, key=lambda item: item.penalty):
                crop_box, glyph_mask = data[id(candidate)]
                if any(
                    _cropped_masks_overlap(crop_box, glyph_mask, *data[id(other)])
                    for _, other in chosen_pairs
                ):
                    continue
                chosen_pairs.append((index, candidate))
                search(position + 1, chosen_pairs, cost + candidate.penalty)
                chosen_pairs.pop()
            search(position + 1, chosen_pairs, cost)

        search(0, [], 0.0)
        selected.update({id(free_plans[index].region): candidate for index, candidate in best})
    return selected


def _free_text_fast_conflict_regions(
    regions: List[Any],
    plans: Dict[int, List[LayoutCandidate]],
    image_shape: Tuple[int, int],
) -> set[int]:
    """Return fast-only placements that collide and need exhaustive alternatives."""
    fast_regions = [
        region for region in regions
        if len(plans.get(id(region), [])) == 1
        and plans[id(region)][0].status in {"free_text_ideal", "free_text_local"}
    ]
    if not fast_regions:
        return set()

    candidate_data = {}

    def masks_for(candidate):
        data = candidate_data.get(id(candidate))
        if data is None:
            crop_box, glyph_mask, _, _ = _candidate_data(candidate, image_shape)
            data = (crop_box, glyph_mask)
            candidate_data[id(candidate)] = data
        return data

    conflicts: set[int] = set()
    for first_region in fast_regions:
        first_id = id(first_region)
        first_candidate = plans[first_id][0]
        first_box, first_mask = masks_for(first_candidate)
        for second_region in regions:
            second_id = id(second_region)
            if first_id == second_id:
                continue
            second_candidates = plans.get(second_id, [])
            if not second_candidates:
                continue
            all_collide = True
            for second_candidate in second_candidates:
                second_box, second_mask = masks_for(second_candidate)
                if not _cropped_masks_overlap(first_box, first_mask, second_box, second_mask):
                    all_collide = False
                    break
            if not all_collide:
                continue
            conflicts.add(first_id)
            if len(second_candidates) == 1 and second_candidates[0].status in {"free_text_ideal", "free_text_local"}:
                conflicts.add(second_id)
    return conflicts


def _apply_free_text_candidate(
    region: Any,
    candidate: LayoutCandidate,
    profile: OriginalLayoutProfile,
    config: Config,
    image_shape: Tuple[int, int],
    layout_debug: bool = False,
) -> bool:
    text = _render_text(region)
    layout_rect = list(candidate.layout_bounds or (
        min(line.x for line in candidate.lines),
        min(line.y for line in candidate.lines),
        max(line.x + line.width for line in candidate.lines),
        max(line.y + line.height for line in candidate.lines),
    ))
    fg, bg = fg_bg_compare(*region.get_font_colors())
    line_dicts = [
        {"text": line.text, "x": line.x, "y": line.y, "width": line.width, "height": line.height}
        for line in candidate.lines
    ]
    box = render_positioned_lines(
        line_dicts, layout_rect, candidate.font_size, fg, bg,
        config.render.line_spacing or 0.0,
        getattr(region, "target_lang", "en_US") or "en_US",
        getattr(region, "direction", "hr") == "hr",
        stroke_width=stroke.get_text_stroke_width(
            candidate.font_size, bg, getattr(region, "bg_colors", None)
        ),
    )
    if box is None or not np.any(box[:, :, 3]):
        return False
    region.font_size = candidate.font_size
    region.layout_bounds = layout_rect
    region._bubble_box = box
    region._bubble_points = _points_for_rect(region, layout_rect, image_shape[1], image_shape[0])
    region.layout_segments = [{
        "x": layout_rect[0],
        "y": layout_rect[1],
        "width": layout_rect[2] - layout_rect[0],
        "height": layout_rect[3] - layout_rect[1],
        "text": text,
        "font_size": candidate.font_size,
        "lines": line_dicts,
    }]
    region._layout_input_text = text
    if layout_debug:
        region._free_text_glyph_mask = _candidate_global_glyph_mask(candidate, image_shape)
        region._free_text_visual_mask = _candidate_visual_masks(candidate, image_shape, 1)[1]
    else:
        for attr in ("_free_text_glyph_mask", "_free_text_visual_mask"):
            if hasattr(region, attr):
                delattr(region, attr)
    region._free_text_solver_applied = True
    region._solver_applied = True
    region._solver_path = "free_text"
    region._solver_p5 = candidate.glyph_clearance_p5
    region._solver_score = candidate.penalty
    region._solver_status = candidate.status
    region._solver_qa = candidate.qa
    region._render_suppressed = False
    zone = getattr(region, "_free_text_zone", None)
    if zone is not None and np.any(zone.coverable_damage_mask):
        cleanup = cv2.dilate(
            (zone.coverable_damage_mask > 0).astype(np.uint8),
            np.ones((3, 3), np.uint8),
            iterations=1,
        )
        cleanup &= (zone.ownership_mask > 0).astype(np.uint8)
        region.free_text_cleanup_mask = encode_safe_shape(cleanup)
    region._draw_operations = [
        {
            "region_id": str(getattr(region, "region_id", "")),
            "text": line.text,
            "x": line.x,
            "y": line.y,
            "width": line.width,
            "height": line.height,
            "font_size": candidate.font_size,
            "bbox": [line.x, line.y, line.x + line.width, line.y + line.height],
            "renderer": "free_text_positioned_lines",
        }
        for line in candidate.lines
    ]
    return True



from .source_profile import (
    _effective_source_font_size,
    _source_geometry_font_size,
    build_original_layout_profile,
)

# ---------------------------------------------------------------------------
# Phase 7/8/9 — composite soft objective
# ---------------------------------------------------------------------------



@dataclass
class _RegionLayoutPlan:
    region: Any
    text: str = ""
    source_profile: Optional[OriginalLayoutProfile] = None
    candidates: List[LayoutCandidate] = field(default_factory=list)
    fg: Tuple[int, int, int] = (0, 0, 0)
    bg: Optional[Tuple[int, int, int]] = (255, 255, 255)
    line_spacing: float = 0.1
    language: str = "ENG"
    zone_mask: Optional[np.ndarray] = None


from .bubble_zones import (
    _shared_bubble_groups,
    partition_bubble_zones,
)
from .scoring import (
    _OCC_IDEAL,
    _WEIGHT_ASPECT,
    _WEIGHT_BLOCK_GAP,
    _WEIGHT_CENTER_VAR,
    _WEIGHT_CENTROID,
    _WEIGHT_FILL_VAR,
    _WEIGHT_FONT,
    _WEIGHT_HORIZ_BALANCE,
    _WEIGHT_HYPHEN,
    _WEIGHT_JITTER,
    _WEIGHT_OCC_HIGH,
    _WEIGHT_OCC_LOW,
    _WEIGHT_ORPHAN,
    _WEIGHT_RAGGED,
    _WEIGHT_SHAPE,
    _WEIGHT_SILHOUETTE,
    _WEIGHT_SRC_BBOX,
    _WEIGHT_SRC_CENTROID,
    _WEIGHT_SRC_FONT,
    _WEIGHT_SRC_LINES,
    _WEIGHT_SRC_PATTERN,
    _WEIGHT_VERT_BALANCE,
    _WEIGHT_VFILL_HIGH,
    _WEIGHT_VFILL_LOW,
    _WEIGHT_ZONE_OVERFLOW,
    _composite_penalty,
    _font_penalty,
    _glyph_mask_for_lines,
)

from .validation import _bbox_validate, _validate_glyph_pixels
from .joint_layout import (
    _MAX_JOINT_LAYOUT_COMBINATIONS,
    _candidate_bbox,
    _candidate_data,
    _choose_joint_layout,
    _ink_centroid,
    _rect_gap,
)


def _page_dialogue_font_baseline(groups: List[BubbleLayoutGroup], minimum: int) -> Optional[int]:
    estimates = []
    for group in groups:
        texts = []
        for region in group.regions:
            text = _render_text(region).strip()
            if text and not is_preserved_region(region):
                texts.append(text)
        text = "\n".join(texts)
        if text and group.interior is not None and np.any(group.interior):
            estimate = _estimate_adaptive_font_size(group.interior, text, minimum)
            if estimate > minimum:
                estimates.append(estimate)
    return int(round(np.percentile(estimates, 70))) if estimates else None


def _long_word_pressure(
    geom: BubbleGeometry,
    words: List[str],
    font_size: int,
    stroke_width: int,
    margin: float,
) -> Tuple[Dict[str, Any], Optional[int]]:
    widths, _ = _precompute_widths(words, font_size)
    max_row_width = _max_usable_row_width(
        geom, font_size, stroke_width, margin, min_width=max(font_size, 8)
    )
    longest_index = max(range(len(words)), key=lambda i: widths[i], default=None)
    longest_width = widths[longest_index] if longest_index is not None else 0
    bottlenecks = [
        i for i, (word, width) in enumerate(zip(words, widths))
        if len(word) >= 7 and width > max_row_width
    ]
    split_candidates = [
        i for i in bottlenecks
        if re.fullmatch(r"[A-Za-z]{7,}[.,!?;:…'\"”’)]*", words[i])
        and "-" not in words[i] and "/" not in words[i] and not any(ch.isdigit() for ch in words[i])
    ]
    bottleneck_index = max(split_candidates, key=lambda i: widths[i], default=None)
    pressured_width = max((widths[i] for i in bottlenecks), default=0)
    width_pressure_ratio = round(pressured_width / max_row_width, 4) if max_row_width else 0.0
    dominant_bottleneck_index = bottleneck_index
    single_word_dominant = (len(bottlenecks) == 1 and bottleneck_index is not None)

    total_text_width = sum(widths)
    paragraph_density_pressure = round(total_text_width / float(max(1, max_row_width * 3)), 4) if max_row_width else 0.0

    return ({
        "longest_word": words[longest_index] if longest_index is not None else "",
        "longest_word_width": longest_width,
        "max_usable_row_width": max_row_width,
        "word_pressure_ratio": width_pressure_ratio,
        "width_pressure_ratio": width_pressure_ratio,
        "bottleneck_indices": bottlenecks,
        "dominant_bottleneck_index": dominant_bottleneck_index,
        "single_word_dominant": single_word_dominant,
        "paragraph_density_pressure": paragraph_density_pressure,
        "long_word_bottleneck": bool(bottlenecks),
        "bottleneck_word": words[bottleneck_index] if bottleneck_index is not None else (
            words[max(bottlenecks, key=lambda i: widths[i])] if bottlenecks else None
        ),
    }, bottleneck_index)


@dataclass
class HyphenVariant:
    words: List[str]
    word: str
    left: str
    right: str
    breakpoint: int
    split_str: str


def _hyphenation_variants(
    words: List[str],
    word_index: int,
    language: str,
    font_size: int,
    max_variants: int = 3,
) -> List[HyphenVariant]:
    match = re.fullmatch(r"([A-Za-z]{7,})([.,!?;:…'\"”’)]*)", words[word_index])
    if not match:
        return []
    raw_word = words[word_index]
    if "-" in raw_word or "/" in raw_word or any(ch.isdigit() for ch in raw_word):
        return []
    word, punctuation = match.groups()
    split_points = []
    hyphenator = text_render.select_hyphenator(language)
    if hyphenator is not None:
        try:
            syllables = hyphenator.syllables(word.lower())
        except Exception:
            syllables = []
        if len(syllables) >= 2 and "".join(syllables).lower() == word.lower():
            offset = 0
            for syllable in syllables[:-1]:
                offset += len(syllable)
                if offset >= 2 and len(word) - offset >= 2:
                    split_points.append(offset)
    if not split_points:
        # Unknown names still deserve one bounded rescue attempt near visual midpoint.
        split_points = sorted(
            range(2, len(word) - 1),
            key=lambda split: abs(
                _precompute_widths([word[:split]], font_size)[0][0]
                - _precompute_widths([word[split:]], font_size)[0][0]
            ),
        )[:max_variants]
    if not split_points:
        return []

    variants: List[Tuple[int, int, int, str, str]] = []
    for split in split_points:
        left, right = word[:split] + "-", word[split:] + punctuation
        widths, _ = _precompute_widths([left, right], font_size)
        variants.append((max(widths), abs(widths[0] - widths[1]), split, left, right))

    variants.sort(key=lambda item: (item[0], item[1]))
    selected = variants[:max_variants]

    res = []
    for max_w, diff, split, left, right in selected:
        cand_words = words[:word_index] + [left, right] + words[word_index + 1:]
        res.append(HyphenVariant(
            words=cand_words,
            word=word,
            left=left,
            right=right,
            breakpoint=split,
            split_str=f"{word[:split]}-/{word[split:]}",
        ))
    return res


def _hyphenation_variant(
    words: List[str],
    word_index: int,
    language: str,
    font_size: int,
) -> Optional[List[str]]:
    variants = _hyphenation_variants(words, word_index, language, font_size, max_variants=1)
    return variants[0].words if variants else None


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
) -> Optional[_RegionLayoutPlan]:
    text = _render_text(region)
    if not text.strip() or interior is None or not np.any(interior):
        return None

    render_cfg = config.render
    minimum = render_cfg.font_size_minimum
    if minimum == -1:
        minimum = round(sum(image_shape) / 200)
    minimum = max(1, minimum)

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
    geom = BubbleGeometry(active_mask)
    source_profile = build_original_layout_profile(region, interior)
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

    # ponytail: cap review-only fallback at half target; use measured readability data before lowering it.
    emergency_floor = max(font_policy.absolute_minimum, (font_policy.preferred_size + 1) // 2)
    emergency_candidates: List[LayoutCandidate] = []
    if normal is None and is_bubble and font_policy.consistency_floor - 1 >= emergency_floor:
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
        "explicit_font_size": getattr(render_cfg, "font_size", None) is not None,
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
        "hyphenation_reason": "satisfactory_font_ratio" if normal is not None else "stage_a_failed",
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

    # Evaluate if hyphenation rescue is needed or permitted
    needs_rescue = (normal is None or normal.font_size < font_policy.hyphenation_trigger_size)
    rescue = None
    rescue_word = None
    rescue_variant_obj = None

    if needs_rescue:
        if getattr(render_cfg, "no_hyphenation", False):
            diagnostics["hyphenation_reason"] = "disabled_by_config"
        elif not is_bubble:
            diagnostics["hyphenation_reason"] = "not_bubble_placement"
        elif is_preserved_region(region):
            diagnostics["hyphenation_reason"] = "preserved_region"
        else:
            normal_words = normalize_words(split_layout_words(text))
            pressure, bottleneck_index = _long_word_pressure(
                geom, normal_words, font_policy.preferred_size, stroke_width, solver_margin
            )
            diagnostics.update(pressure)
            diagnostics["hyphenation_reason"] = "no_long_word_bottleneck"
            if bottleneck_index is not None:
                variants = _hyphenation_variants(
                    normal_words, bottleneck_index,
                    getattr(region, "target_lang", "en_US") or "en_US", font_policy.preferred_size,
                    max_variants=3,
                )
                single_var = _hyphenation_variant(
                    normal_words, bottleneck_index,
                    getattr(region, "target_lang", "en_US") or "en_US", font_policy.preferred_size,
                )
                if not single_var:
                    variants = []
                diagnostics["hyphenation_reason"] = "no_dictionary_breakpoint"
                if variants:
                    diagnostics["hyphenation_rescue_attempted"] = True
                    diagnostics["hyphenation_attempted"] = True
                    diagnostics["hyphenation_reason"] = "long_word_width_bottleneck"
                    rescue_word = normal_words[bottleneck_index]

                    best_rescue_cand = None
                    best_rescue_variant = None

                    for v_obj in variants:
                        variant = v_obj.words
                        variant_widths, _ = _precompute_widths(variant, font_policy.preferred_size)
                        original_width = _precompute_widths([rescue_word], font_policy.preferred_size)[0][0]
                        materially_narrower = max(variant_widths[bottleneck_index:bottleneck_index + 2], default=original_width) < original_width

                        if materially_narrower:
                            v_res = solve_layout(
                                geom=geom,
                                words=variant,
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
                                top_k=1,
                                is_single_region=(preferred_mask is None or not np.any(preferred_mask)),
                                lobe_graph=lobe_graph,
                                forced_break_after=bottleneck_index,
                            )
                            cand = v_res[0] if isinstance(v_res, list) and v_res else (
                                v_res if isinstance(v_res, LayoutCandidate) else None
                            )
                            if cand is not None:
                                if best_rescue_cand is None or cand.font_size > best_rescue_cand.font_size or (
                                    cand.font_size == best_rescue_cand.font_size and len(cand.lines) < len(best_rescue_cand.lines)
                                ) or (
                                    cand.font_size == best_rescue_cand.font_size and len(cand.lines) == len(best_rescue_cand.lines) and cand.penalty < best_rescue_cand.penalty
                                ):
                                    best_rescue_cand = cand
                                    best_rescue_variant = v_obj

                    rescue = best_rescue_cand
                    rescue_variant_obj = best_rescue_variant

                    if rescue is not None:
                        diagnostics["rescue_candidate_font_size"] = rescue.font_size
                        diagnostics["rescue_font_size"] = rescue.font_size
                        rescue_gain = (
                            rescue.font_size / float(max(1, normal.font_size))
                            if normal else None
                        )
                        diagnostics["font_gain_ratio"] = round(rescue_gain, 4) if rescue_gain is not None else None
                        gain_px = (rescue.font_size - normal.font_size) if normal else rescue.font_size
                        
                        meaningful = (
                            normal is None
                            or (gain_px >= HYPHEN_MIN_GAIN_PX and (rescue_gain >= HYPHEN_MIN_GAIN_RATIO or rescue.font_size >= font_policy.consistency_floor))
                        )
                        if meaningful:
                            diagnostics["hyphenation_reason"] = "long_word_width_bottleneck"
                            diagnostics["hyphenation_selected"] = True
                            diagnostics["hyphenation_rescue_selected"] = True
                            diagnostics["final_font_size"] = rescue.font_size
                            diagnostics["font_policy_status"] = "hyphen_rescue"
                        else:
                            diagnostics["hyphenation_reason"] = "insufficient_font_gain"
                    else:
                        diagnostics["hyphenation_reason"] = "no_valid_rescue_layout"
            elif diagnostics["long_word_bottleneck"]:
                diagnostics["hyphenation_reason"] = "no_hyphenatable_bottleneck"

    candidates: List[LayoutCandidate] = []
    if rescue is not None and diagnostics["hyphenation_rescue_selected"]:
        rescue.qa.update(diagnostics)
        rescue.qa["hyphenation_rescue_selected"] = True
        rescue.qa["hyphenation_selected"] = True
        rescue.qa["final_font_size"] = rescue.font_size
        rescue.qa["font_policy_status"] = "hyphen_rescue"
        rescue.qa["font_ratio_after_rescue"] = round(
            rescue.font_size / float(max(1, target)), 4
        )
        rescue.qa["introduced_hyphen_count"] = 1
        rescue.qa["introduced_hyphen_words"] = [rescue_word]
        if normal is not None:
            rescue.penalty = min(rescue.penalty, normal.penalty - 1e-3)
        candidates.append(rescue)
    elif normal is not None:
        candidates = list(candidates_a)
        if normal.font_size >= font_policy.consistency_floor:
            diagnostics["font_policy_status"] = "preferred"
        elif normal.font_size >= font_policy.mild_compression_floor:
            diagnostics["font_policy_status"] = "mild_compression"
        else:
            diagnostics["font_policy_status"] = "emergency_compression"
        diagnostics["final_font_size"] = normal.font_size
    elif emergency_candidates:
        candidates = list(emergency_candidates)
        diagnostics["font_policy_status"] = "emergency_review"
        diagnostics["final_font_size"] = emergency_candidates[0].font_size
        region.review_required = True
        region.review_reason = "text_requires_emergency_compression"

    for candidate in candidates:
        if candidate is rescue and candidate.qa.get("hyphenation_rescue_selected"):
            continue
        candidate.qa.update(diagnostics)
        candidate.qa["hyphenation_rescue_selected"] = False
        candidate.qa["hyphenation_selected"] = False
        candidate.qa["final_font_size"] = candidate.font_size
        candidate.qa["font_ratio_after_rescue"] = round(
            candidate.font_size / float(max(1, target)), 4
        )
        candidate.qa["introduced_hyphen_count"] = 0
        candidate.qa["introduced_hyphen_words"] = []

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


def _apply_layout_candidate(
    plan: _RegionLayoutPlan,
    candidate: LayoutCandidate,
    image_shape: Tuple[int, int],
    layout_debug: bool = False,
) -> bool:
    region = plan.region
    all_x1 = min(line.x for line in candidate.lines)
    all_y1 = min(line.y for line in candidate.lines)
    all_x2 = max(line.x + line.width for line in candidate.lines)
    all_y2 = max(line.y + line.height for line in candidate.lines)
    layout_rect = [all_x1, all_y1, all_x2, all_y2]
    line_dicts = [
        {"text": line.text, "x": line.x, "y": line.y, "width": line.width, "height": line.height}
        for line in candidate.lines
    ]
    layout_stroke_width = stroke.get_text_stroke_width(
        candidate.font_size, plan.bg, getattr(region, "bg_colors", None)
    )
    candidate.qa["layout_stroke_width"] = layout_stroke_width
    box = render_positioned_lines(
        line_dicts, layout_rect, candidate.font_size, plan.fg, plan.bg,
        plan.line_spacing, plan.language, region.direction == "hr",
        stroke_width=layout_stroke_width,
    )
    if box is None or not np.any(box[:, :, 3]):
        return False

    region.font_size = candidate.font_size
    region.line_spacing = plan.line_spacing
    region._render_suppressed = False
    region.layout_bounds = layout_rect
    region._bubble_box = box
    region._bubble_cleanup = getattr(region, "_bubble_interior", None) * 255
    region._bubble_points = _points_for_rect(region, layout_rect, image_shape[1], image_shape[0])
    region.layout_segments = [{
        "x": layout_rect[0],
        "y": layout_rect[1],
        "width": layout_rect[2] - layout_rect[0],
        "height": layout_rect[3] - layout_rect[1],
        "text": plan.text,
        "font_size": candidate.font_size,
        "lines": line_dicts,
    }]
    region._layout_input_text = plan.text
    region._solver_applied = True
    region._solver_p5 = candidate.glyph_clearance_p5
    region._solver_score = candidate.penalty
    region._solver_status = candidate.status
    region._solver_qa = candidate.qa
    region._hyphenation_diagnostics = {
        key: candidate.qa[key]
        for key in (
            "calibrated_target", "normal_font_size", "font_ratio",
            "font_ratio_before_rescue", "font_ratio_after_rescue",
            "compression_severity", "longest_word", "longest_word_width",
            "max_usable_row_width", "word_pressure_ratio", "long_word_bottleneck",
            "bottleneck_word", "hyphenation_rescue_attempted", "hyphenation_reason",
            "rescue_candidate_font_size", "font_gain_ratio", "final_font_size",
            "hyphenation_rescue_selected", "introduced_hyphen_count",
            "introduced_hyphen_words",
        ) if key in candidate.qa
    }
    region._font_policy_diagnostics = dict(getattr(region, "_font_policy_diagnostics", {}) or candidate.qa)
    if layout_debug and "calibrated_target" in candidate.qa:
        qa = candidate.qa
        logger.info(
            "Bubble %s target=%spx normal=%spx bottleneck=%r rescue_attempted=%s "
            "rescue=%spx gain=%s selected=%s hyphens=%s reason=%s lines=%s",
            getattr(region, "region_id", ""), qa["calibrated_target"],
            qa["normal_font_size"], qa.get("bottleneck_word"),
            qa["hyphenation_rescue_attempted"], qa.get("rescue_candidate_font_size"),
            qa.get("font_gain_ratio"), qa["hyphenation_rescue_selected"],
            qa["introduced_hyphen_count"], qa["hyphenation_reason"],
            " / ".join(line.text for line in candidate.lines),
        )
    return True


from .debug import create_placement_zones_visualization


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
    from .orchestration import apply_shape_aware_bubble_layout as apply_page_layout

    return apply_page_layout(
        ctx,
        config,
        font_path=font_path,
        solver_margin=solver_margin,
        solver_max_y_trials=solver_max_y_trials,
        legacy_only=legacy_only,
        infer_bubbles=infer_bubbles,
        timing=timing,
        layout_debug=layout_debug,
        page_geometry=page_geometry,
    )
