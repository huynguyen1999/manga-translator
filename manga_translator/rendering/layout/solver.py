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
    _RENDER_LOCK,
    _points_for_rect,
    fg_bg_compare,
    get_default_eng_font,
    text_render,
)
from ..bubble_layout import (
    _estimate_adaptive_font_size,
    decode_safe_shape,
    prepare_bubbles,
    render_positioned_lines,
)
from ...geometry.bubbles import PageGeometry, prepare_page_geometry
from .geometry import (
    BubbleGeometry,
    build_lobe_graph,
    compute_placement_target,
    compute_zone_shape_profile,
    _mask_moments,
    _runs_from_row,
)
from .models import (
    BandSlot,
    BubbleLayoutGroup,
    FreeTextDamageTarget,
    FreeTextZone,
    LayoutCandidate,
    LobeGraph,
    OriginalLayoutProfile,
    PageObstacleMap,
    PlacementTarget,
    PlacementMode,
    PlacedLine,
    ScanInterval,
    ZoneShapeProfile,
)
from .ownership import build_free_text_ownership_zones
from .obstacles import build_page_obstacle_map, classify_placement_modes
from .regions import prepare_regions as _ensure_region_identities
from .raster import (
    _candidate_cropped_visual_masks,
    _candidate_global_glyph_mask,
    _candidate_visual_masks,
    _cropped_masks_overlap,
    _render_line_alpha,
)


def _render_text(region: Any) -> str:
    if hasattr(region, "get_translation_for_rendering"):
        return str(region.get_translation_for_rendering() or "")
    return str(getattr(region, "translation", "") or getattr(region, "text", "") or "")


def _record_content_trace(regions: Optional[List[Any]], stage: str) -> None:
    _ensure_region_identities(regions)
    for region in regions or []:
        trace = getattr(region, "_content_trace", None)
        if trace is None:
            trace = []
            region._content_trace = trace
        mode = getattr(region, "placement_mode", None)
        mode_val = getattr(mode, "value", str(mode)) if mode is not None else None
        trace.append({
            "stage": stage,
            "region_id": str(region.region_id),
            "source_text": str(getattr(region, "text", "") or ""),
            "source_lines": [str(item) for item in (getattr(region, "texts", None) or [])],
            "translated_text": str(getattr(region, "translation", "") or ""),
            "render_text": _render_text(region),
            "layout_text": getattr(region, "_layout_input_text", None),
            "placement_mode": mode_val,
            "free_text_solver_applied": bool(getattr(region, "_free_text_solver_applied", False)),
            "solver_path": getattr(region, "_solver_path", None),
            "solver_status": getattr(region, "_solver_status", None),
            "has_bubble_box": getattr(region, "_bubble_box", None) is not None,
            "has_bubble_points": getattr(region, "_bubble_points", None) is not None,
            "has_free_text_zone": getattr(region, "_free_text_zone", None) is not None,
        })


logger = logging.getLogger("layout.solver")

class SolverProfileStats:
    """Fine-grained Level-2 timing and workload counter stats for layout solvers."""
    # Workload counters
    fonts_tested: int = 0
    spacing_tested: int = 0
    y_origins_tested: int = 0
    dp_invocations: int = 0
    dp_states_created: int = 0
    dp_states_pruned: int = 0
    dp_states_deduplicated: int = 0
    raw_wrappings: int = 0
    pre_score_survivors: int = 0
    refined_candidates: int = 0
    glyph_validations: int = 0
    safe_cache_hits: int = 0
    safe_cache_misses: int = 0
    band_cache_hits: int = 0
    band_cache_misses: int = 0
    # Free-text specific workload
    free_text_crops_rendered: int = 0
    free_text_offsets_tested: int = 0
    free_text_hard_valid_hits: int = 0

    # Level-2 timing accumulators (in ms)
    safe_mask_prep_ms: float = 0.0
    width_precompute_ms: float = 0.0
    row_slot_table_ms: float = 0.0
    placement_target_ms: float = 0.0
    zone_profile_ms: float = 0.0
    y_origin_seq_ms: float = 0.0
    dp_search_ms: float = 0.0
    compaction_ms: float = 0.0
    gap_classification_ms: float = 0.0
    centering_ms: float = 0.0
    x_optimization_ms: float = 0.0
    composite_penalty_ms: float = 0.0
    bbox_validation_ms: float = 0.0
    glyph_validation_ms: float = 0.0
    # Free-text specific timings
    ft_typography_ms: float = 0.0
    ft_crops_rasterize_ms: float = 0.0
    ft_offset_search_ms: float = 0.0
    ft_coverage_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workload": {
                "fonts_tested": self.fonts_tested,
                "spacing_tested": self.spacing_tested,
                "y_origins_tested": self.y_origins_tested,
                "dp_invocations": self.dp_invocations,
                "dp_states_created": self.dp_states_created,
                "dp_states_pruned": self.dp_states_pruned,
                "dp_states_deduplicated": self.dp_states_deduplicated,
                "raw_wrappings": self.raw_wrappings,
                "pre_score_survivors": self.pre_score_survivors,
                "refined_candidates": self.refined_candidates,
                "glyph_validations": self.glyph_validations,
                "safe_cache_hits": self.safe_cache_hits,
                "safe_cache_misses": self.safe_cache_misses,
                "band_cache_hits": self.band_cache_hits,
                "band_cache_misses": self.band_cache_misses,
                "free_text_crops_rendered": self.free_text_crops_rendered,
                "free_text_offsets_tested": self.free_text_offsets_tested,
                "free_text_hard_valid_hits": self.free_text_hard_valid_hits,
            },
            "timings_ms": {
                "safe_mask_prep": self.safe_mask_prep_ms,
                "width_precompute": self.width_precompute_ms,
                "row_slot_table": self.row_slot_table_ms,
                "placement_target": self.placement_target_ms,
                "zone_profile": self.zone_profile_ms,
                "y_origin_seq": self.y_origin_seq_ms,
                "dp_search": self.dp_search_ms,
                "compaction": self.compaction_ms,
                "gap_classification": self.gap_classification_ms,
                "centering": self.centering_ms,
                "x_optimization": self.x_optimization_ms,
                "composite_penalty": self.composite_penalty_ms,
                "bbox_validation": self.bbox_validation_ms,
                "glyph_validation": self.glyph_validation_ms,
                "ft_typography": self.ft_typography_ms,
                "ft_crops_rasterize": self.ft_crops_rasterize_ms,
                "ft_offset_search": self.ft_offset_search_ms,
                "ft_coverage": self.ft_coverage_ms,
            }
        }


_GLOBAL_SOLVER_PROFILE: Optional[SolverProfileStats] = None


def get_solver_profile() -> SolverProfileStats:
    global _GLOBAL_SOLVER_PROFILE
    if _GLOBAL_SOLVER_PROFILE is None:
        _GLOBAL_SOLVER_PROFILE = SolverProfileStats()
    return _GLOBAL_SOLVER_PROFILE


def reset_solver_profile() -> SolverProfileStats:
    global _GLOBAL_SOLVER_PROFILE
    _GLOBAL_SOLVER_PROFILE = SolverProfileStats()
    return _GLOBAL_SOLVER_PROFILE

# Composite-objective weights (Phase 7). Hard validity stays in the glyph
# validators; everything here is soft preference.
_WEIGHT_FONT = 100.0       # deviation from the target font size
_WEIGHT_CENTROID = 2.0     # ink-centroid drift from the placement target
_WEIGHT_SHAPE = 0.05       # second derivative of the line-width silhouette
_WEIGHT_FILL_VAR = 2.0     # variance of per-line slot fill
_WEIGHT_JITTER = 0.05      # per-line drift from slot center
_WEIGHT_RAGGED = 0.2       # adjacent line-width disparity
_WEIGHT_ORPHAN = 10.0      # short isolated word lines
_WEIGHT_HYPHEN = 8.0       # non-final lines ending with a hyphen
_WEIGHT_OCC_LOW = 50.0     # text block too small for the bubble
_WEIGHT_OCC_HIGH = 50.0    # text block cramped inside the bubble
_OCC_IDEAL = (0.20, 0.75)  # desirable occupancy band
_WEIGHT_SRC_CENTROID = 3.0
_WEIGHT_SRC_LINES = 2.0
_WEIGHT_SRC_FONT = 20.0
_WEIGHT_SRC_BBOX = 1.0
_WEIGHT_SRC_PATTERN = 4.0   # deviation from source relative line-center pattern
_WEIGHT_ZONE_OVERFLOW = 18.0
_WEIGHT_BLOCK_GAP = 15.0    # vertical empty span inside block
_WEIGHT_CENTER_VAR = 5.0    # unpenalized lateral dispersion of line centers
_WEIGHT_VERT_BALANCE = 25.0 # vertical whitespace balance |A_above - A_below| / A_zone
_WEIGHT_HORIZ_BALANCE = 12.0 # horizontal whitespace balance |A_left - A_right| / A_zone
_WEIGHT_VFILL_LOW = 30.0    # vertical fill under-utilization (< 0.55)
_WEIGHT_VFILL_HIGH = 35.0   # vertical fill over-cramped (> 0.80)
_WEIGHT_ASPECT = 18.0       # log aspect ratio matching penalty
_WEIGHT_SILHOUETTE = 8.0    # width profile / silhouette match
_JOINT_CANDIDATE_COUNT = 5
_MAX_JOINT_LAYOUT_COMBINATIONS = 4096

# DP transition & continuity weights
_WEIGHT_TRANS_XJUMP = 8.0       # quadratic penalty for normalized center jump
_WEIGHT_TRANS_OVERLAP = 12.0    # penalty for poor horizontal slot overlap
_WEIGHT_TRANS_BRANCH = 25.0     # penalty for zero overlap / branch jump
_WEIGHT_VERTICAL_GAP = 24.0           # nonlinear paragraph spring penalty


# Gap classification constants
GAP_NORMAL = "NORMAL"
GAP_LOCAL_GEOMETRY = "LOCAL_GEOMETRY_ADJUSTMENT"
GAP_LOBE_NECK = "LOBE_NECK"
GAP_DISCONNECTED_SAFE_REGION = "DISCONNECTED_SAFE_REGION"
GAP_SOURCE_BREAK = "SOURCE_PARAGRAPH_BREAK"
GAP_UNEXPLAINED = "UNEXPLAINED"


def _is_geometric_obstruction(
    y_from: int,
    y_to: int,
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    min_w: int = 8,
    lobe_graph: Optional["LobeGraph"] = None,
) -> Tuple[bool, str]:
    """Check if there is a concrete geometric obstruction in the vertical interval [y_from, y_to]."""
    if y_from >= y_to:
        return False, GAP_NORMAL

    # 1. Check LobeGraph neck crossing
    if lobe_graph is not None and getattr(lobe_graph, "necks", None):
        for neck in lobe_graph.necks:
            neck_pt = neck.get("center") or neck.get("neck_point")
            if neck_pt is not None:
                # neck_pt is [x, y] or (x, y)
                ny = neck_pt[1] - getattr(geom, "y_offset", 0)
                if y_from <= ny <= y_to:
                    return True, GAP_LOBE_NECK

    # 2. Check safe mask cross section along the vertical interval
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    h_mask, _ = safe.shape
    y_start = max(0, min(h_mask, y_from))
    y_end = max(0, min(h_mask, y_to))
    if y_start >= y_end:
        return False, GAP_NORMAL

    sub_safe = safe[y_start:y_end, :]
    row_widths = np.sum(sub_safe, axis=1)

    if np.any(row_widths == 0):
        return True, GAP_DISCONNECTED_SAFE_REGION

    if np.any(row_widths < min_w):
        return True, GAP_DISCONNECTED_SAFE_REGION

    return False, GAP_UNEXPLAINED


def _classify_adjacent_gaps(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
    line_h: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    lobe_graph: Optional["LobeGraph"] = None,
    source_profile: Optional["OriginalLayoutProfile"] = None,
) -> List[Dict[str, Any]]:
    """Classify every adjacent line gap with an explicit reason and gap ratio."""
    if len(lines) <= 1:
        return []

    H = max(1.0, float(line_h))
    gap_reports: List[Dict[str, Any]] = []

    for i in range(len(lines) - 1):
        l1 = lines[i]
        l2 = lines[i + 1]
        delta_y = l2.y - l1.y
        gap_ratio = delta_y / H
        min_required_w = max(8, min(l1.width, l2.width) // 2)

        if gap_ratio <= 1.25:
            reason = GAP_NORMAL
        elif gap_ratio <= 1.45:
            is_obstructed, obs_reason = _is_geometric_obstruction(
                l1.y + l1.height, l2.y, geom, font_size, stroke_width, margin, min_required_w, lobe_graph
            )
            if is_obstructed:
                reason = obs_reason
            else:
                reason = GAP_LOCAL_GEOMETRY
        else:
            is_obstructed, obs_reason = _is_geometric_obstruction(
                l1.y + l1.height, l2.y, geom, font_size, stroke_width, margin, min_required_w, lobe_graph
            )
            if is_obstructed:
                reason = obs_reason
            else:
                reason = GAP_UNEXPLAINED

        gap_reports.append({
            "line_from": l1.text,
            "line_to": l2.text,
            "gap_px": delta_y,
            "gap_h": round(gap_ratio, 2),
            "reason": reason,
            "valid": (reason != GAP_UNEXPLAINED or gap_ratio <= 1.40),
        })

    return gap_reports


def _compact_vertical_rhythm(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
    line_h: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    lobe_graph: Optional["LobeGraph"] = None,
    row_slot_table: Optional[Dict[int, List[BandSlot]]] = None,
) -> List[PlacedLine]:
    """Bidirectional vertical compaction & spring-chain rhythm optimization.

    Enforces continuous paragraph rhythm (y_i^* = y_0 + i*H) at sub-row / pixel resolution,
    pulling lines toward their ideal positions whenever permitted by safe mask geometry.
    """
    if len(lines) <= 1:
        return lines

    n = len(lines)
    H = max(font_size, line_h)
    _, y1_safe, _, y2_safe = geom.safe_bounding_box(font_size, stroke_width, margin)
    if y2_safe <= y1_safe:
        return lines

    cur_lines = list(lines)

    def _best_slot_at_y(target_y: int, line_obj: PlacedLine) -> Optional[BandSlot]:
        if target_y < y1_safe or target_y + font_size > y2_safe:
            return None
        if row_slot_table is not None and target_y in row_slot_table:
            slots = [s for s in row_slot_table[target_y] if s.width >= line_obj.width]
        else:
            slots = geom.band_intervals(
                target_y, target_y + font_size, font_size, stroke_width, margin, min_width=line_obj.width
            )
        if not slots:
            return None
        # Pick slot closest in center to previous slot / center
        return min(slots, key=lambda s: abs(s.center - line_obj.slot.center))

    # Pass 1: Upward compaction ("gravity" pulling lines upward toward y_{i-1} + H)
    for i in range(1, n):
        prev_y = cur_lines[i - 1].y
        expected_y = prev_y + H
        cur_y = cur_lines[i].y
        if cur_y > expected_y:
            for cand_y in range(expected_y, cur_y + 1):
                slot = _best_slot_at_y(cand_y, cur_lines[i])
                if slot is not None:
                    ideal_x = int(round(slot.center - cur_lines[i].width / 2.0))
                    new_x = max(slot.left, min(slot.right - cur_lines[i].width, ideal_x))
                    cur_lines[i] = PlacedLine(
                        text=cur_lines[i].text,
                        y=cand_y,
                        x=new_x,
                        width=cur_lines[i].width,
                        height=font_size,
                        slot=slot,
                    )
                    break

    # Pass 2: Downward compaction (pulling upper lines downward towards y_{i+1} - H if displaced)
    for i in range(n - 2, -1, -1):
        next_y = cur_lines[i + 1].y
        expected_y = next_y - H
        cur_y = cur_lines[i].y
        if cur_y < expected_y:
            min_bound = (cur_lines[i - 1].y + font_size) if i > 0 else y1_safe
            for cand_y in range(expected_y, cur_y - 1, -1):
                if cand_y < min_bound:
                    break
                slot = _best_slot_at_y(cand_y, cur_lines[i])
                if slot is not None:
                    ideal_x = int(round(slot.center - cur_lines[i].width / 2.0))
                    new_x = max(slot.left, min(slot.right - cur_lines[i].width, ideal_x))
                    cur_lines[i] = PlacedLine(
                        text=cur_lines[i].text,
                        y=cand_y,
                        x=new_x,
                        width=cur_lines[i].width,
                        height=font_size,
                        slot=slot,
                    )
                    break

    # Pass 3: Spring chain energy relaxation E = sum (y_{k+1} - y_k - H)^2
    for _ in range(2):
        for i in range(n):
            cur_y = cur_lines[i].y
            min_y = (cur_lines[i - 1].y + font_size) if i > 0 else y1_safe
            max_y = (cur_lines[i + 1].y - font_size) if i < n - 1 else (y2_safe - font_size)
            if min_y > max_y:
                continue

            search_min = max(min_y, cur_y - H)
            search_max = min(max_y, cur_y + H)

            best_cand_y = cur_y
            best_cand_slot = cur_lines[i].slot
            best_e = float("inf")

            for cand_y in range(search_min, search_max + 1):
                slot = _best_slot_at_y(cand_y, cur_lines[i])
                if slot is None:
                    continue
                # Compute local spring energy
                e = 0.0
                if i > 0:
                    e += (cand_y - cur_lines[i - 1].y - H) ** 2
                if i < n - 1:
                    e += (cur_lines[i + 1].y - cand_y - H) ** 2

                if e < best_e:
                    best_e = e
                    best_cand_y = cand_y
                    best_cand_slot = slot

            if best_cand_slot is not None and best_cand_y != cur_y:
                ideal_x = int(round(best_cand_slot.center - cur_lines[i].width / 2.0))
                new_x = max(best_cand_slot.left, min(best_cand_slot.right - cur_lines[i].width, ideal_x))
                cur_lines[i] = PlacedLine(
                    text=cur_lines[i].text,
                    y=best_cand_y,
                    x=new_x,
                    width=cur_lines[i].width,
                    height=font_size,
                    slot=best_cand_slot,
                )

    return cur_lines


def _font_penalty(font_size: int, font_target: int) -> float:
    if font_target <= 0:
        return 0.0
    return ((font_size - font_target) / float(font_target)) ** 2 * _WEIGHT_FONT


def solve_layout(
    geom: BubbleGeometry,
    words: List[str],
    font_size_max: int,
    font_size_min: int,
    language: str = "en_US",
    hyphenate: bool = True,
    line_spacing: float = 0.0,
    line_spacing_options: Optional[List[float]] = None,
    y_origin_step: int = 4,
    stroke_width: int = 0,
    margin: float = 2.0,
    max_y_origin_trials: int = 12,
    source_profile: Optional["OriginalLayoutProfile"] = None,
    preferred_mask: Optional[np.ndarray] = None,
    top_k: int = 1,
    is_single_region: bool = True,
    lobe_graph: Optional["LobeGraph"] = None,
) -> Union[Optional[LayoutCandidate], List[LayoutCandidate]]:
    """Generate and rank layout candidates; return one or the best ``top_k``.

    The mask determines *where text may exist* (hard validation); language,
    typography, and the original page's layout profile shape the soft score.
    ``hyphenate`` is accepted for API compatibility — normalized words are
    treated as atomic and hyphenation is never introduced by the DP.
    """
    if not words:
        return None

    # Phase 2: repair OCR hyphen splits so ordinary words are atomic.
    norm_words = normalize_words(words)

    if line_spacing_options is None:
        line_spacing_options = [line_spacing]

    font_target = font_size_max
    candidates: List[LayoutCandidate] = []
    prof = get_solver_profile()

    # Determine font size sequence: coarse stepping for wide ranges, then fine refinement
    font_range = list(range(font_size_max, font_size_min - 1, -1))
    if len(font_range) > 10:
        # Test coarse font sizes first (step 2 or 3)
        step = 3 if len(font_range) > 16 else 2
        coarse_fonts = list(range(font_size_max, font_size_min - 1, -step))
        if font_size_min not in coarse_fonts:
            coarse_fonts.append(font_size_min)
        # All font sizes to test will include coarse fonts, and once a valid candidate is found,
        # we will add fine neighbors
        candidate_font_queue = list(coarse_fonts)
        fine_refined_fonts = set()
    else:
        candidate_font_queue = list(font_range)
        fine_refined_fonts = set(font_range)

    queue_idx = 0
    while queue_idx < len(candidate_font_queue):
        S = candidate_font_queue[queue_idx]
        queue_idx += 1
        prof.fonts_tested += 1

        # A smaller font may win on composition, but the font penalty grows
        # monotonically below the target: prune once the font term alone can
        # no longer beat the incumbent.
        cutoff = sorted((candidate.penalty for candidate in candidates if candidate.valid))[:max(1, top_k)]
        if len(cutoff) >= top_k and _font_penalty(S, font_target) >= cutoff[-1]:
            continue

        if not geom.has_safe_pixels(S, stroke_width, margin):
            continue

        x1, y1, x2, y2 = geom.safe_bounding_box(S, stroke_width, margin)
        if x2 <= x1 or y2 <= y1:
            continue

        t_w0 = perf_counter()
        word_widths, space_w = _precompute_widths(norm_words, S)
        prof.width_precompute_ms += (perf_counter() - t_w0) * 1000.0
        if not word_widths:
            continue

        # Feasibility check: max single word width must fit in safe width
        if max(word_widths) > (x2 - x1):
            continue

        # Precompute reusable row-slot table for font size S
        min_slot_w = max(S, 8)
        t_rst0 = perf_counter()
        row_slot_table = _build_row_slot_table(
            geom, S, stroke_width, margin, min_width=min_slot_w
        )
        prof.row_slot_table_ms += (perf_counter() - t_rst0) * 1000.0
        if not row_slot_table:
            continue

        t_pt0 = perf_counter()
        target_geom = compute_placement_target(
            geom, S, stroke_width, margin,
            source_profile=source_profile,
            preferred_mask=preferred_mask,
            is_single_region=is_single_region,
        )
        prof.placement_target_ms += (perf_counter() - t_pt0) * 1000.0

        for ls in line_spacing_options:
            prof.spacing_tested += 1
            line_h = _line_height(S, ls)
            t_zp0 = perf_counter()
            zone_profile = compute_zone_shape_profile(
                geom, S, stroke_width, margin,
                preferred_mask=preferred_mask,
                line_h=line_h,
                words=norm_words,
            )
            prof.zone_profile_ms += (perf_counter() - t_zp0) * 1000.0

            t_ys0 = perf_counter()
            y_origins = _y_origin_sequence(y1, y2, S, y_origin_step)
            prof.y_origin_seq_ms += (perf_counter() - t_ys0) * 1000.0

            # Phase 2: Collect candidate wrappings across Y trials using coarse-to-fine exploration
            raw_wrappings: List[Tuple[int, List[PlacedLine]]] = []
            trials_after_first = 0

            # If many Y origins, explore a coarse subset first, then refine around promising basins
            if len(y_origins) > 6:
                coarse_y_step = max(2, len(y_origins) // 6)
                coarse_y = y_origins[::coarse_y_step]
                if y_origins[-1] not in coarse_y:
                    coarse_y.append(y_origins[-1])
            else:
                coarse_y = y_origins

            promising_y = list(coarse_y)
            tested_y = set()

            for y_orig in promising_y:
                if y_orig in tested_y:
                    continue
                tested_y.add(y_orig)
                prof.y_origins_tested += 1
                t_dp0 = perf_counter()
                cand_wrappings = _try_placement_rows(
                    geom, norm_words, word_widths, space_w,
                    S, y_orig, ls, line_h, stroke_width, margin,
                    zone_profile=zone_profile,
                    max_per_bucket=2,
                    row_slot_table=row_slot_table,
                )
                prof.dp_search_ms += (perf_counter() - t_dp0) * 1000.0
                if not cand_wrappings:
                    continue

                for placed in cand_wrappings:
                    raw_wrappings.append((y_orig, placed))
                    prof.raw_wrappings += 1

                trials_after_first += 1
                if trials_after_first >= max_y_origin_trials:
                    break

            # Fine Y refinement around best coarse wrappings
            if raw_wrappings and len(y_origins) > len(promising_y) and trials_after_first < max_y_origin_trials:
                best_y_origs = [item[0] for item in raw_wrappings[:2]]
                for best_y in best_y_origs:
                    for neighbor_y in (best_y - y_origin_step, best_y + y_origin_step):
                        if neighbor_y in y_origins and neighbor_y not in tested_y:
                            tested_y.add(neighbor_y)
                            prof.y_origins_tested += 1
                            t_dp0 = perf_counter()
                            cand_wrappings = _try_placement_rows(
                                geom, norm_words, word_widths, space_w,
                                S, neighbor_y, ls, line_h, stroke_width, margin,
                                zone_profile=zone_profile,
                                max_per_bucket=2,
                                row_slot_table=row_slot_table,
                            )
                            prof.dp_search_ms += (perf_counter() - t_dp0) * 1000.0
                            if cand_wrappings:
                                for placed in cand_wrappings:
                                    raw_wrappings.append((neighbor_y, placed))
                                    prof.raw_wrappings += 1
                            trials_after_first += 1
                            if trials_after_first >= max_y_origin_trials:
                                break

            if not raw_wrappings:
                continue

            # If font S produced valid wrappings and fine neighbors haven't been queued yet, add S-1, S+1
            if S not in fine_refined_fonts:
                fine_refined_fonts.add(S)
                for neighbor_S in (S + 1, S - 1, S - 2):
                    if font_size_min <= neighbor_S <= font_size_max and neighbor_S not in fine_refined_fonts:
                        fine_refined_fonts.add(neighbor_S)
                        candidate_font_queue.append(neighbor_S)

            # Phase 2: Heuristic pre-score raw wrappings, grouping by line count to preserve diversity
            by_line_cnt: Dict[int, List[Tuple[float, int, List[PlacedLine]]]] = {}
            for y_orig, placed in raw_wrappings:
                lc = len(placed)
                p_approx = _font_penalty(S, font_target)
                if source_profile is not None:
                    p_approx += abs(lc - source_profile.line_count) * _WEIGHT_SRC_LINES
                y_top = min(line.y for line in placed)
                y_bot = max(line.y + line.height for line in placed)
                span = y_bot - y_top
                p_approx += (span / max(1.0, float(zone_profile.height))) * 5.0
                if lc not in by_line_cnt:
                    by_line_cnt[lc] = []
                by_line_cnt[lc].append((p_approx, y_orig, placed))

            elite_raw: List[Tuple[float, int, List[PlacedLine]]] = []
            for lc in sorted(by_line_cnt.keys()):
                group = sorted(by_line_cnt[lc], key=lambda x: x[0])
                elite_raw.extend(group[:2])  # top 2 per line count bucket

            elite_raw.sort(key=lambda x: x[0])
            del elite_raw[max(8, top_k * 4):]
            prof.pre_score_survivors += len(elite_raw)

            for _, y_orig, placed in elite_raw:
                # 1. Vertical Compaction Pass: Continuous Y refinement & Spring chain
                t_comp0 = perf_counter()
                placed_compacted = _compact_vertical_rhythm(
                    placed, geom, S, line_h, stroke_width, margin,
                    lobe_graph=lobe_graph, row_slot_table=row_slot_table,
                )
                prof.compaction_ms += (perf_counter() - t_comp0) * 1000.0
                if not placed_compacted:
                    placed_compacted = placed

                # 2. Gap classification and validation
                t_gap0 = perf_counter()
                gap_reports = _classify_adjacent_gaps(
                    placed_compacted, geom, S, line_h, stroke_width, margin,
                    lobe_graph=lobe_graph, source_profile=source_profile
                )
                prof.gap_classification_ms += (perf_counter() - t_gap0) * 1000.0
                has_unexplained_large_gap = any(not g["valid"] for g in gap_reports)

                # 3. Whole-block recentering to target capacity center with local search
                t_cent0 = perf_counter()
                placed_centered = _center_layout_block(
                    placed_compacted, geom, target_geom, S, stroke_width, margin
                )
                prof.centering_ms += (perf_counter() - t_cent0) * 1000.0
                if placed_centered is None:
                    continue

                # 4. Internal line refinement (X optimization)
                zone_center_x = target_geom.center_x
                source_center_x = (
                    source_profile.centroid[0] - geom.x_offset
                    if source_profile is not None and not is_single_region else None
                )
                t_xopt0 = perf_counter()
                placed_opt = _optimize_x(
                    placed_centered,
                    source_center_x=source_center_x,
                    zone_center_x=zone_center_x,
                )
                prof.x_optimization_ms += (perf_counter() - t_xopt0) * 1000.0

                t_bbox0 = perf_counter()
                fast_valid, approx_p5 = _bbox_validate(placed_opt, geom, S, stroke_width, margin)
                prof.bbox_validation_ms += (perf_counter() - t_bbox0) * 1000.0
                if not fast_valid:
                    continue

                t_pen0 = perf_counter()
                penalty, qa = _composite_penalty(
                    placed_opt, geom, S, font_target, source_profile, stroke_width, margin,
                    preferred_mask=preferred_mask,
                    target_geom=target_geom,
                    zone_profile=zone_profile,
                )
                prof.composite_penalty_ms += (perf_counter() - t_pen0) * 1000.0
                qa["gap_details"] = gap_reports
                qa["gap_reasons"] = [g["reason"] for g in gap_reports]
                qa["max_gap_h"] = max((g["gap_h"] for g in gap_reports), default=1.0)
                qa["has_unexplained_gap"] = has_unexplained_large_gap

                is_cand_valid = not has_unexplained_large_gap
                cand_status = "ok" if is_cand_valid else "unexplained_gap"

                candidate = LayoutCandidate(
                    font_size=S,
                    y_origin=y_orig,
                    line_spacing=ls,
                    lines=placed_opt,
                    penalty=penalty + (500.0 if has_unexplained_large_gap else 0.0),
                    glyph_clearance_p5=approx_p5,
                    valid=is_cand_valid,
                    status=cand_status,
                    qa=qa,
                )

                candidates.append(candidate)
                prof.refined_candidates += 1

            candidates.sort(key=lambda item: (not item.valid, item.penalty))
            del candidates[max(top_k * 4, 32):]

        # Phase 7: Clear ephemeral caches on BubbleGeometry periodically
        geom.clear_ephemeral_caches()

    # Attach candidate alternatives summary to candidates' QA for diagnostics
    alts_by_lines: Dict[int, LayoutCandidate] = {}
    for cand in candidates:
        lc = len(cand.lines)
        if lc not in alts_by_lines:
            alts_by_lines[lc] = cand

    eval_summary = []
    for lc in sorted(alts_by_lines.keys()):
        c = alts_by_lines[lc]
        eval_summary.append({
            "lines": lc,
            "font_size": c.font_size,
            "vertical_utilization": c.qa.get("vertical_utilization", 0.0),
            "aspect_mismatch": c.qa.get("aspect_mismatch", 0.0),
            "p_aspect": c.qa.get("p_aspect", 0.0),
            "p_vertical_fill": c.qa.get("p_vertical_fill", 0.0),
            "p_composition": c.qa.get("p_composition", 0.0),
            "penalty": c.penalty,
        })
    for cand in candidates:
        cand.qa["candidate_alternatives"] = eval_summary
        if "zone_profile" in locals() and zone_profile is not None:
            cand.qa["zone_size"] = (zone_profile.width, zone_profile.height)
            cand.qa["zone_aspect"] = zone_profile.aspect_ratio
            cand.qa["zone_line_capacity"] = zone_profile.vertical_capacity

    valid_candidates: List[LayoutCandidate] = []
    for candidate in candidates:
        if not candidate.valid:
            continue
        t_val0 = perf_counter()
        prof.glyph_validations += 1
        valid, p5 = _validate_glyph_pixels(
            candidate.lines, geom, candidate.font_size, stroke_width, margin
        )
        prof.glyph_validation_ms += (perf_counter() - t_val0) * 1000.0
        candidate.glyph_clearance_p5 = p5
        if not valid:
            candidate.valid = False
            candidate.status = "glyph_overflow"
            continue

        if geom.x_offset or geom.y_offset:
            for ln in candidate.lines:
                ln.x += geom.x_offset
                ln.y += geom.y_offset
                ln.slot = BandSlot(
                    left=ln.slot.left + geom.x_offset,
                    right=ln.slot.right + geom.x_offset,
                    y_start=ln.slot.y_start + geom.y_offset,
                    y_end=ln.slot.y_end + geom.y_offset,
                )
        valid_candidates.append(candidate)

    if not valid_candidates and candidates:
        # Fallback to best candidate if none passed gap check, validating glyph pixels
        for candidate in candidates:
            t_val0 = perf_counter()
            prof.glyph_validations += 1
            valid, p5 = _validate_glyph_pixels(
                candidate.lines, geom, candidate.font_size, stroke_width, margin
            )
            prof.glyph_validation_ms += (perf_counter() - t_val0) * 1000.0
            if valid:
                candidate.valid = True
                candidate.glyph_clearance_p5 = p5
                if geom.x_offset or geom.y_offset:
                    for ln in candidate.lines:
                        ln.x += geom.x_offset
                        ln.y += geom.y_offset
                        ln.slot = BandSlot(
                            left=ln.slot.left + geom.x_offset,
                            right=ln.slot.right + geom.x_offset,
                            y_start=ln.slot.y_start + geom.y_offset,
                            y_end=ln.slot.y_end + geom.y_offset,
                        )
                valid_candidates.append(candidate)
                break

    if top_k > 1:
        return valid_candidates[:top_k]
    return valid_candidates[0] if valid_candidates else None


def _precompute_widths(words: List[str], font_size: int) -> Tuple[List[int], int]:
    try:
        from manga_translator.rendering import text_render
        word_widths = [int(text_render.get_string_width(font_size, w)) for w in words]
        space_w = int(text_render.get_string_width(font_size, " "))
        return word_widths, space_w
    except Exception:
        word_widths = [int(len(w) * font_size * 0.6) for w in words]
        space_w = int(font_size * 0.3)
        return word_widths, space_w


def _y_origin_sequence(y1: int, y2: int, font_size: int, step: int) -> List[int]:
    """Sample plausible starting top-Y coordinates across the safe vertical range."""
    step = max(1, step)
    max_top = y2 - font_size
    if max_top < y1:
        return [y1]

    positions = []
    y = y1
    while y <= max_top:
        positions.append(y)
        y += step
    if not positions or positions[-1] != max_top:
        positions.append(max_top)

    seen = set()
    result = []
    for p in positions:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


@dataclass
class RowGeometry:
    """One textual line-height row: a Y position plus the disjoint safe
    intervals available at that height (multi-lobe bubbles have several)."""
    y: int
    height: int
    intervals: List[BandSlot]


def _build_row_slot_table(
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    min_width: int = 8,
) -> Dict[int, List[BandSlot]]:
    """Precompute valid BandSlot horizontal intervals for every vertical y coordinate."""
    _, y1, _, y2 = geom.safe_bounding_box(font_size, stroke_width, margin)
    if y2 <= y1:
        return {}
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    h_mask, w_mask = safe.shape
    table: Dict[int, List[BandSlot]] = {}
    for y in range(max(0, y1), min(h_mask - font_size + 1, y2 - font_size + 1)):
        band_row = np.all(safe[y : y + font_size], axis=0)
        intervals = _runs_from_row(band_row)
        slots = [
            BandSlot(left=iv.left, right=iv.right, y_start=y, y_end=y + font_size)
            for iv in intervals
            if iv.right - iv.left >= min_width
        ]
        slots.sort(key=lambda s: s.left)
        table[y] = slots
    return table


def _try_placement_rows(
    geom: BubbleGeometry,
    words: List[str],
    word_widths: List[int],
    space_w: int,
    font_size: int,
    y_origin: int,
    line_spacing: float,
    line_h: int,
    stroke_width: int,
    margin: float,
    zone_profile: Optional[ZoneShapeProfile] = None,
    max_per_bucket: int = 2,
    row_slot_table: Optional[Dict[int, List[BandSlot]]] = None,
) -> List[List[PlacedLine]]:
    _, y1, _, y2 = geom.safe_bounding_box(font_size, stroke_width, margin)
    if y2 <= y1:
        return []
    rows: List[RowGeometry] = []
    min_w = max(font_size, 8)

    y = y_origin
    while y + font_size <= y2:
        if row_slot_table is not None and y in row_slot_table:
            band = [s for s in row_slot_table[y] if s.width >= min_w]
        else:
            band = geom.band_intervals(
                y, y + font_size, font_size, stroke_width, margin,
                min_width=min_w,
            )
        rows.append(RowGeometry(y=y, height=font_size, intervals=band or []))
        y += line_h

    if not rows:
        return []

    return _dp_word_break_rows(
        words, word_widths, space_w, rows, font_size,
        max_per_bucket=max_per_bucket, zone_profile=zone_profile,
        normal_gap=max(0, line_h - font_size),
    )


# Linguistic line-break guidance (Phase 5). Function words strongly prefer
# not starting a new line; a line that *is* a single function word is worse.
_FUNCTION_WORDS = {
    # articles
    "A", "AN", "THE",
    # prepositions
    "OF", "TO", "IN", "ON", "AT", "FOR", "WITH", "FROM", "BY", "AS", "INTO",
    "OVER", "AFTER", "BEFORE", "BETWEEN", "THROUGH", "UNDER", "OFF", "UP",
    "DOWN", "OUT", "ABOUT", "AROUND", "ACROSS",
    # auxiliaries / modals
    "IS", "ARE", "WAS", "WERE", "BE", "BEEN", "BEING", "AM",
    "HAVE", "HAS", "HAD", "HAVING", "DO", "DOES", "DID", "DONE",
    "WILL", "WOULD", "CAN", "COULD", "SHALL", "SHOULD", "MAY", "MIGHT",
    "MUST", "NEED", "OUGHT",
    # pronouns / possessives
    "I", "YOU", "HE", "SHE", "IT", "WE", "THEY", "ME", "HIM", "HER", "US",
    "THEM", "MY", "YOUR", "HIS", "ITS", "OUR", "THEIR", "MINE", "YOURS",
    "HERS", "OURS", "THEIRS", "THIS", "THAT", "THESE", "THOSE", "WHO",
    "WHOM", "WHOSE", "WHICH", "WHAT",
    # conjunctions
    "AND", "BUT", "OR", "NOR", "SO", "YET", "THOUGH", "ALTHOUGH", "BECAUSE",
    "WHILE", "UNTIL", "SINCE", "IF", "THAN", "THEN",
}

_PUNCT_STRIP = ".,!?;:…\"')-]["


def _word_core(word: str) -> str:
    return word.strip(_PUNCT_STRIP).upper()


def _phrase_break_penalty(prev_word: str, next_word: str) -> float:
    """Penalty for breaking the line between ``prev_word`` and ``next_word``.

    Keeps strongly connected pairs (LET ME / MAY HAVE / LOOK AT / ARE YOU)
    together when geometry allows, without any LLM in the loop.
    """
    penalty = 0.0
    if _word_core(next_word) in _FUNCTION_WORDS:
        penalty += 15.0
    if _word_core(prev_word) in _FUNCTION_WORDS:
        penalty += 4.0
    return penalty


def _line_break_cost(
    words: List[str],
    wi: int,
    end: int,
    slot_w: int,
    run_w: int,
    zone_profile: Optional[ZoneShapeProfile] = None,
    row_y: Optional[int] = None,
) -> float:
    """Soft cost of placing words[wi:end] on one row interval.

    Geometry only asks "is this line allowed here?" — slot-fill pressure is
    deliberately weak so the DP follows language, not bubble contours.
    """
    fill = run_w / max(1, slot_w)
    cost = (1.0 - fill) ** 2 * 2.0

    n_words = end - wi
    is_final = end >= len(words)

    # Check if this row is in a narrow section of the bubble
    is_narrow_row = False
    if zone_profile is not None and row_y is not None and zone_profile.bbox and zone_profile.width_by_y:
        y_base = zone_profile.bbox[1]
        rel_y = max(0, min(len(zone_profile.width_by_y) - 1, int(row_y - y_base)))
        if rel_y < len(zone_profile.width_by_y):
            avail_w = zone_profile.width_by_y[rel_y]
            if avail_w <= zone_profile.width * 0.65 or slot_w <= zone_profile.width * 0.65:
                is_narrow_row = True

    # Punctuation-only line penalty (e.g. "?!", "...", "!")
    is_punct_only = all(not w.strip(_PUNCT_STRIP) for w in words[wi:end])
    if is_punct_only:
        cost += 50.0

    # Orphans: a short isolated word stranded on its own line.
    if n_words == 1 and not is_final:
        word_core = _word_core(words[wi])
        if len(word_core) <= 3:
            # If the single word line occurs in a narrow row and fits nicely, allow it with minimal penalty
            if is_narrow_row and fill >= 0.35:
                cost += 3.0
            else:
                cost += 25.0
        elif len(words[wi]) <= 2:
            if is_narrow_row and fill >= 0.35:
                cost += 2.0
            else:
                cost += 15.0

    # A line consisting of a single function word is typographically poor unless justified by narrow geometry.
    if n_words == 1 and not is_final and _word_core(words[wi]) in _FUNCTION_WORDS:
        if is_narrow_row and fill >= 0.35:
            cost += 4.0
        else:
            cost += 20.0

    # Linguistic break quality between this line and the next.
    if not is_final:
        cost += _phrase_break_penalty(words[end - 1], words[end])

    return cost


def _transition_cost(
    prev_slot: Optional[BandSlot],
    prev_center_x: Optional[float],
    curr_slot: BandSlot,
    curr_center_x: float,
    font_size: int,
) -> float:
    """Calculate transition penalty between consecutive placed lines."""
    if prev_slot is None or prev_center_x is None:
        return 0.0

    # 1. Normalized center displacement d_x = |C_i - C_{i-1}| / S
    dx = abs(curr_center_x - prev_center_x)
    dx_norm = dx / max(1.0, float(font_size))
    # Quadratic penalty for normalized displacement
    p_xjump = (dx_norm ** 2) * _WEIGHT_TRANS_XJUMP

    # 2. Interval overlap ratio r = |I_prev ∩ I_curr| / min(|I_prev|, |I_curr|)
    overlap_left = max(prev_slot.left, curr_slot.left)
    overlap_right = min(prev_slot.right, curr_slot.right)
    overlap_w = max(0, overlap_right - overlap_left)
    min_w = max(1, min(prev_slot.width, curr_slot.width))
    overlap_ratio = overlap_w / float(min_w)

    p_overlap = ((1.0 - overlap_ratio) ** 2) * _WEIGHT_TRANS_OVERLAP

    # 3. Branch switch penalty: zero overlap and non-trivial center jump
    p_branch = 0.0
    if overlap_ratio == 0.0 and dx_norm > 0.5:
        p_branch = _WEIGHT_TRANS_BRANCH

    return p_xjump + p_overlap + p_branch


def _row_can_fit_word(row: RowGeometry, word_w: int) -> bool:
    """Check if any interval in the row is wide enough to hold at least word_w."""
    for slot in row.intervals:
        if slot.width >= word_w:
            return True
    return False


def _dp_word_break_rows(
    words: List[str],
    word_widths: List[int],
    space_w: int,
    rows: List[RowGeometry],
    font_size: int,
    max_per_bucket: int = 2,
    zone_profile: Optional[ZoneShapeProfile] = None,
    normal_gap: int = 0,
) -> List[List[PlacedLine]]:
    """Break words into a continuous paragraph stack across the safe rows.

    Usable rows are not optional once text has started. Empty or too-narrow
    rows are traversed as geometry-forced gaps, while actual line spacing is
    charged as a spring-like deformation from the normal line step.
    """
    nw = len(words)
    nr = len(rows)
    if nw == 0 or nr == 0:
        return []

    # Trailing whitespace needs no state: once all words are placed, the
    # paragraph ends and remaining rows are free.
    # Result per state: Dict[int, List[Tuple[float, List[PlacedLine]]]] (line_count -> top candidates)
    memo: Dict[Tuple[Any, ...], Dict[int, List[Tuple[float, List[PlacedLine]]]]] = {}

    BEFORE_TEXT = 0
    IN_TEXT = 1

    def _add_candidates(
        dest: Dict[int, List[Tuple[float, List[PlacedLine]]]],
        cand_lines_cnt: int,
        cand_cost: float,
        cand_lines: List[PlacedLine],
    ) -> None:
        if cand_lines_cnt not in dest:
            dest[cand_lines_cnt] = [(cand_cost, cand_lines)]
        else:
            bucket = dest[cand_lines_cnt]
            # Avoid identical line text structures
            if any(len(b[1]) == len(cand_lines) and all(l1.text == l2.text and l1.y == l2.y for l1, l2 in zip(b[1], cand_lines)) for b in bucket):
                return
            bucket.append((cand_cost, cand_lines))
            bucket.sort(key=lambda item: item[0])
            del bucket[max_per_bucket:]

    def dp(
        wi: int,
        ri: int,
        prev_slot_left: Optional[int],
        prev_slot_right: Optional[int],
        prev_center_x: Optional[float],
        prev_line_y: Optional[int],
        text_state: int,
    ) -> Dict[int, List[Tuple[float, List[PlacedLine]]]]:
        prof = get_solver_profile()
        prof.dp_invocations += 1
        if wi == nw:
            return {0: [(0.0, [])]}
        if ri == nr:
            return {}

        key = (
            wi, ri, prev_slot_left, prev_slot_right, prev_center_x,
            prev_line_y, text_state,
        )
        if key in memo:
            prof.dp_states_deduplicated += 1
            return memo[key]

        prof.dp_states_created += 1
        row = rows[ri]
        results_by_lines: Dict[int, List[Tuple[float, List[PlacedLine]]]] = {}

        prev_slot = (
            BandSlot(left=prev_slot_left, right=prev_slot_right, y_start=0, y_end=0)
            if prev_slot_left is not None and prev_slot_right is not None
            else None
        )

        # Option 1: place a run of words on one of this row's intervals.
        for slot in row.intervals:
            slot_w = slot.width
            run_w = 0
            for end in range(wi + 1, nw + 1):
                if end > wi + 1:
                    run_w += space_w
                run_w += word_widths[end - 1]
                if run_w > slot_w:
                    break

                ideal_x = int(round(slot.center - run_w / 2.0))
                x = max(slot.left, min(slot.right - run_w, ideal_x))
                curr_center_x = x + run_w / 2.0

                line_cost = _line_break_cost(
                    words, wi, end, slot_w, run_w,
                    zone_profile=zone_profile, row_y=row.y,
                )
                trans_cost = (
                    _transition_cost(prev_slot, prev_center_x, slot, curr_center_x, font_size)
                    if text_state == IN_TEXT else 0.0
                )
                spacing_cost = 0.0
                if text_state == IN_TEXT and prev_line_y is not None:
                    ideal_step = font_size + normal_gap
                    excess_step = max(0, row.y - prev_line_y - ideal_step)
                    deformation = excess_step / max(1.0, float(font_size))
                    spacing_cost = _WEIGHT_VERTICAL_GAP * deformation ** 2
                step_cost = line_cost + trans_cost + spacing_cost

                rem_dict = dp(
                    end,
                    ri + 1,
                    slot.left,
                    slot.right,
                    curr_center_x,
                    row.y,
                    IN_TEXT,
                )
                for rem_cnt, cand_list in rem_dict.items():
                    for rem_cost, rem_lines in cand_list:
                        this_line = PlacedLine(
                            text=" ".join(words[wi:end]),
                            y=row.y,
                            x=x,
                            width=run_w,
                            height=font_size,
                            slot=slot,
                        )
                        _add_candidates(
                            results_by_lines,
                            rem_cnt + 1,
                            step_cost + rem_cost,
                            [this_line] + rem_lines,
                        )

        # Option 2: traverse only a geometry-forced row. Once text has
        # started, a usable row must carry the next word rather than becoming
        # typographic whitespace.
        can_fit_next = wi < nw and _row_can_fit_word(row, word_widths[wi])
        if text_state == IN_TEXT and can_fit_next:
            skip_dict = {}
        else:
            skip_dict = dp(
                wi,
                ri + 1,
                prev_slot_left,
                prev_slot_right,
                prev_center_x,
                prev_line_y,
                text_state,
            )

        for rem_cnt, cand_list in skip_dict.items():
            for skip_cost, skip_lines in cand_list:
                _add_candidates(
                    results_by_lines,
                    rem_cnt,
                    skip_cost,
                    skip_lines,
                )

        memo[key] = results_by_lines
        return results_by_lines

    root_dict = dp(0, 0, None, None, None, None, BEFORE_TEXT)
    if not root_dict:
        return []

    candidates: List[List[PlacedLine]] = []
    for line_cnt in sorted(root_dict.keys()):
        for cost, lines in root_dict[line_cnt]:
            placed_words = sum(len(line.text.split()) for line in lines)
            if placed_words == nw:
                candidates.append(lines)

    return candidates


def _center_layout_block(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    target: PlacementTarget,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    max_search_radius: int = 24,
) -> Optional[List[PlacedLine]]:
    """Translate the entire finished text block towards the PlacementTarget center.

    Searches nearby legal integer translations (dx, dy) and validates candidate
    glyphs against the safe mask, picking the valid placement closest to ideal.
    """
    if not lines:
        return lines

    block_left = min(line.x for line in lines)
    block_right = max(line.x + line.width for line in lines)
    block_top = min(line.y for line in lines)
    block_bottom = max(line.y + line.height for line in lines)

    block_cx = (block_left + block_right) / 2.0
    block_cy = (block_top + block_bottom) / 2.0

    ideal_dx = int(round(target.center_x - block_cx))
    ideal_dy = int(round(target.center_y - block_cy))

    # Determine safe mask bounding box to bound translations early
    sx1, sy1, sx2, sy2 = geom.safe_bounding_box(font_size, stroke_width, margin)
    min_dy = sy1 - block_top
    max_dy = sy2 - block_bottom
    min_dx = sx1 - block_left
    max_dx = sx2 - block_right

    # Generate search offsets sorted by Euclidean distance from (ideal_dx, ideal_dy)
    offsets: List[Tuple[int, int]] = []
    seen = set()

    for r in range(0, max_search_radius + 1, 2):
        for step_x in range(-r, r + 1, 2):
            for step_y in range(-r, r + 1, 2):
                if max(abs(step_x), abs(step_y)) == r:
                    cand_dx = ideal_dx + step_x
                    cand_dy = ideal_dy + step_y
                    if min_dx <= cand_dx <= max_dx and min_dy <= cand_dy <= max_dy:
                        if (cand_dx, cand_dy) not in seen:
                            seen.add((cand_dx, cand_dy))
                            offsets.append((cand_dx, cand_dy))

    offsets.sort(key=lambda off: (off[0] - ideal_dx) ** 2 + (off[1] - ideal_dy) ** 2)

    h, w = geom.shape
    best_translated: Optional[List[PlacedLine]] = None

    for dx, dy in offsets:
        # Check overall block bounds first
        new_left = block_left + dx
        new_right = block_right + dx
        new_top = block_top + dy
        new_bottom = block_bottom + dy
        if new_left < 0 or new_top < 0 or new_right > w or new_bottom > h:
            continue

        translated = [
            PlacedLine(
                text=ln.text,
                y=ln.y + dy,
                x=ln.x + dx,
                width=ln.width,
                height=ln.height,
                slot=BandSlot(
                    left=ln.slot.left + dx,
                    right=ln.slot.right + dx,
                    y_start=ln.slot.y_start + dy,
                    y_end=ln.slot.y_end + dy,
                ),
            )
            for ln in lines
        ]

        valid, _ = _bbox_validate(translated, geom, font_size, stroke_width, margin)
        if valid:
            best_translated = translated
            break

    return best_translated if best_translated is not None else lines


def _optimize_x(
    lines: List[PlacedLine],
    lam1: float = 1.0,
    lam2: float = 0.5,
    lam3: float = 0.2,
    source_center_x: Optional[float] = None,
    zone_center_x: Optional[float] = None,
) -> List[PlacedLine]:
    """Greedy coordinate descent on line X positions.

    All cost terms operate on *line centers* (x + width / 2), never on left
    edges, so lines of different widths stay visually aligned.
    """
    if not lines:
        return lines

    n = len(lines)
    bounds: List[Tuple[int, int, float, int]] = []
    for line in lines:
        slot = line.slot
        L = slot.left
        R_w = max(slot.left, slot.right - line.width)
        bounds.append((L, R_w, slot.center, line.width))

    def _center(x: float, w: int) -> float:
        return x + w / 2.0

    xs = [max(b[0], min(b[1], int(round(b[2] - b[3] / 2.0))))
          for b in bounds]

    denom = lam1 + lam2 + 4.0 * lam3
    for _ in range(4):
        for i in range(n):
            L, R_w, c, w_i = bounds[i]
            if L >= R_w:
                continue
            c_prev = _center(xs[i - 1], bounds[i - 1][3]) if i > 0 else _center(xs[i], w_i)
            c_next = _center(xs[i + 1], bounds[i + 1][3]) if i < n - 1 else _center(xs[i], w_i)

            target = c
            if zone_center_x is not None:
                target = 0.50 * zone_center_x + 0.50 * target
            if source_center_x is not None:
                target = 0.70 * target + 0.30 * source_center_x
            num = lam1 * target + lam2 * c_prev + 2.0 * lam3 * (c_next + c_prev)
            X_opt = num / denom
            x_opt = X_opt - w_i / 2.0
            xs[i] = max(L, min(R_w, int(round(x_opt))))

    return [
        PlacedLine(text=line.text, y=line.y, x=xs[i],
                   width=line.width, height=line.height, slot=line.slot)
        for i, line in enumerate(lines)
    ]


def _x_cost(center_x: float, c: float, c_prev: float, c_next: float,
            lam1: float, lam2: float, lam3: float) -> float:
    """Cost on line centers: slot center attraction + jitter + curvature."""
    return (
        lam1 * (center_x - c) ** 2
        + lam2 * (center_x - c_prev) ** 2
        + lam3 * (c_next - 2.0 * center_x + c_prev) ** 2
    )


def _bbox_validate(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    epsilon: int = 0,
) -> Tuple[bool, float]:
    h, w = geom.shape
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    dist = geom.dist
    outside = 0
    dt_vals: List[float] = []

    for line in lines:
        lx, ly, lw, lh = line.x, line.y, line.width, line.height
        if lx < 0 or ly < 0 or lx + lw > w or ly + lh > h:
            return False, 0.0
        for sx, sy in [
            (lx, ly), (lx + lw - 1, ly),
            (lx, ly + lh - 1), (lx + lw - 1, ly + lh - 1),
            (lx + lw // 2, ly + lh // 2),
            (lx + lw // 4, ly + lh // 2),
            (lx + 3 * lw // 4, ly + lh // 2),
        ]:
            sx = max(0, min(w - 1, sx))
            sy = max(0, min(h - 1, sy))
            if not safe[sy, sx]:
                outside += 1
            dt_vals.append(float(dist[sy, sx]))

    if outside > epsilon:
        return False, 0.0
    p5 = float(np.percentile(dt_vals, 5)) if dt_vals else 0.0
    return True, p5


def _validate_glyph_pixels(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    epsilon: int = 3,
    clearance_percentile: int = 5,
) -> Tuple[bool, float]:
    h, w = geom.shape
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    dist = geom.dist
    glyph_dt_values: List[float] = []
    outside_count = 0

    for line in lines:
        ly, lx = line.y, line.x
        lw, lh = line.width, line.height

        if lx < 0 or ly < 0 or lx + lw > w or ly + lh > h:
            return False, 0.0

        alpha = _render_line_alpha(line, font_size)
        if alpha is not None:
            ay, ax = alpha.shape[:2]
            x2 = min(lx + ax, w)
            y2 = min(ly + ay, h)
            axc = x2 - lx
            ayc = y2 - ly
            if axc <= 0 or ayc <= 0:
                return False, 0.0
            glyph_mask = alpha[:ayc, :axc] > 127
            if np.any(glyph_mask):
                outside_count += int(np.sum(glyph_mask & ~safe[ly:y2, lx:x2]))
                glyph_dt_values.extend(dist[ly:y2, lx:x2][glyph_mask].tolist())
        else:
            ok, _ = _bbox_validate([line], geom, font_size, stroke_width, margin, epsilon)
            if not ok:
                return False, 0.0

    if outside_count > epsilon:
        return False, 0.0

    if not glyph_dt_values:
        return True, 0.0

    p5 = float(np.percentile(glyph_dt_values, clearance_percentile))
    return True, p5




# ---------------------------------------------------------------------------
# Phase 2 — text normalization (OCR hyphen-split repair)
# ---------------------------------------------------------------------------

# Hyphenated compounds that must survive normalization (never join these).
_COMPOUND_PREFIXES = {
    "X", "E", "SELF", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY",
    "SEVENTY", "EIGHTY", "NINETY", "WELL", "HALF", "FULL", "ALL",
    "PRO", "ANTI", "MID", "NEO", "PAN", "SUPER", "ULTRA", "MULTI",
}

_DICTIONARY_CACHE: Optional[Optional[set]] = None
_DICTIONARY_PATHS = (
    "/usr/share/dict/words",
    "/usr/share/dict/web2",
    "/usr/dict/words",
)


def _load_dictionary() -> Optional[set]:
    """Load a system word list for hyphenation evidence; None if unavailable."""
    global _DICTIONARY_CACHE
    if _DICTIONARY_CACHE is not None:
        return _DICTIONARY_CACHE
    words: Optional[set] = None
    for path in _DICTIONARY_PATHS:
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    words = {ln.strip().lower() for ln in f if ln.strip()}
                break
        except OSError:
            continue
    _DICTIONARY_CACHE = words
    return words


def _dict_contains(word: str) -> bool:
    dictionary = _load_dictionary()
    if dictionary is None:
        return False
    return word.lower() in dictionary


def normalize_words(words: List[str]) -> List[str]:
    """Repair OCR hyphen splits so ordinary words are atomic for the DP.

    "GOT- TEN" -> "GOTTEN", "IN- JURED" -> "INJURED", "SCRATCH- ES." ->
    "SCRATCHES." — but real compounds ("TWENTY-ONE", "SELF-DEFENSE",
    "X-RAY") stay intact. Decisions use dictionary evidence plus compound
    prefixes; when no dictionary exists, a conservative heuristic keeps the
    hyphen unless both fragments look like non-words (split proper names).
    """
    import re as _re

    dictionary = _load_dictionary()
    out: List[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        m_next = (
            _re.match(r"^([A-Za-z]+)([.,!?;:…]*)$", words[i + 1])
            if (w.endswith("-") and not w.endswith("--") and len(w) > 1 and i + 1 < len(words))
            else None
        )
        if m_next:
            frag1 = w[:-1]
            frag2, punct = m_next.group(1), m_next.group(2)

            # Known compound prefix: never join (TWENTY-ONE, X-RAY, ...).
            if frag1.upper() in _COMPOUND_PREFIXES:
                out.append(w)
                i += 1
                continue

            joined = frag1 + frag2
            if dictionary is not None:
                join_is_word = joined.lower() in dictionary
                frag1_is_word = frag1.lower() in dictionary
                frag2_is_word = frag2.lower() in dictionary
                if join_is_word:
                    out.append(joined + punct)
                    i += 2
                    continue
                # Split proper names / unlisted words: "HA- RUTO" -> "HARUTO".
                if not frag1_is_word and not frag2_is_word and len(joined) >= 4:
                    out.append(joined + punct)
                    i += 2
                    continue
            else:
                # No dictionary: join only when the second fragment cannot
                # stand alone (lowercase start strongly suggests a fragment).
                if frag2[:1].islower() and frag1[:1].isupper():
                    out.append(joined + punct)
                    i += 2
                    continue

        out.append(w)
        i += 1

    # Attach floating punctuation tokens (e.g. "?!", "...", "!") to preceding word
    cleaned: List[str] = []
    for w in out:
        if cleaned and not w.strip(_PUNCT_STRIP) and len(w) > 0:
            cleaned[-1] = cleaned[-1] + w
        else:
            cleaned.append(w)
    return cleaned


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
        }

    cx1, cy1, cx2, cy2 = crop_box
    cov_crop = (zone.coverable_damage_mask[cy1:cy2, cx1:cx2] > 0)
    w_crop = zone.coverage_weight_map[cy1:cy2, cx1:cx2]

    cov_ink = float(np.sum(w_crop[cov_crop & ink_crop])) / total_w
    cov_visual = float(np.sum(w_crop[cov_crop & vis_crop])) / total_w
    cov_block = float(np.sum(w_crop[cov_crop & block_crop])) / total_w

    # Coverage is earned by actual glyph pixels.  The allocated line rectangle
    # remains a diagnostic, but can never inflate the placement objective.
    c_damage = max(0.0, min(1.0, cov_ink))

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
        "u_damage": 1.0 - c_damage,
    }



def _free_text_words(text: str) -> List[str]:
    """Tokenize translated free text without inventing word fragments."""
    words: List[str] = []
    for word in text.replace("\n", " ").split():
        if words and not word.strip(_PUNCT_STRIP):
            words[-1] += word
        else:
            words.append(word)
    return words


_FREE_TEXT_MAX_LINE_SPACING = 0.25


def _free_text_line_spacing(value: Optional[float]) -> float:
    """Keep free-text leading typographic, never a source-height control."""
    return min(_FREE_TEXT_MAX_LINE_SPACING, max(0.0, float(value or 0.0)))


def _free_text_font_metrics(font_size: int) -> Dict[str, int]:
    """Return the active face's metric height and natural baseline advance."""
    try:
        from manga_translator.rendering import text_render

        if not text_render.FONT_SELECTION:
            text_render.set_font(get_default_eng_font())
        face = text_render.FONT_SELECTION[0]
        face.set_pixel_sizes(0, font_size)
        ascender = face.size.ascender >> 6
        descender = face.size.descender >> 6
        metric_height = max(1, ascender - descender)
        natural_advance = max(metric_height, face.size.height >> 6)
    except Exception:
        metric_height = max(1, int(math.ceil(font_size * 1.15)))
        natural_advance = metric_height
    return {
        "font_metric_height": int(metric_height),
        "natural_advance": int(natural_advance),
    }


def _free_text_line_height(font_size: int, line_spacing: float) -> int:
    metrics = _free_text_font_metrics(font_size)
    gap = int(round(font_size * _free_text_line_spacing(line_spacing)))
    return metrics["natural_advance"] + gap


def _mask_metrics(mask: np.ndarray, origin: Tuple[int, int] = (0, 0)) -> Dict[str, Any]:
    """Measure actual occupied pixels, rather than their allocated rectangle."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        x, y = origin
        return {
            "bbox": (x, y, x, y),
            "centroid": (float(x), float(y)),
            "width": 0,
            "height": 0,
            "area": 0,
        }
    ox, oy = origin
    return {
        "bbox": (int(xs.min()) + ox, int(ys.min()) + oy, int(xs.max()) + ox + 1, int(ys.max()) + oy + 1),
        "centroid": (ox + float(xs.mean()), oy + float(ys.mean())),
        "width": int(xs.max() - xs.min() + 1),
        "height": int(ys.max() - ys.min() + 1),
        "area": int(len(xs)),
    }


def _free_text_candidate_ink_metrics(candidate: LayoutCandidate) -> Dict[str, Any]:
    """Rasterize one page-independent candidate and report its true ink geometry."""
    if not candidate.lines:
        return _mask_metrics(np.zeros((0, 0), dtype=bool))

    right = max(line.x + line.width for line in candidate.lines) + candidate.font_size + 4
    bottom = max(line.y + line.height for line in candidate.lines) + candidate.font_size + 4
    _, ink_crop, _, _ = _candidate_cropped_visual_masks(
        candidate, 0, (max(1, bottom), max(1, right))
    )
    metrics = _mask_metrics(ink_crop)
    line_boxes = []
    for line in candidate.lines:
        alpha = _render_line_alpha(line, candidate.font_size)
        if alpha is None:
            continue
        line_metrics = _mask_metrics(alpha > 127, (line.x, line.y))
        if line_metrics["area"]:
            line_boxes.append(line_metrics["bbox"])

    line_gaps = [
        max(0, line_boxes[index + 1][1] - line_boxes[index][3])
        for index in range(len(line_boxes) - 1)
    ]
    metric = _free_text_font_metrics(candidate.font_size)
    advances = [candidate.lines[index + 1].y - candidate.lines[index].y for index in range(len(candidate.lines) - 1)]
    metrics.update({
        "ink_bbox": metrics["bbox"],
        "ink_width": metrics["width"],
        "ink_height": metrics["height"],
        "ink_area": metrics["area"],
        "ink_centroid": metrics["centroid"],
        "ink_line_height": int(round(np.mean([b[3] - b[1] for b in line_boxes]))) if line_boxes else 0,
        "font_metric_height": metric["font_metric_height"],
        "baseline_advance": int(round(np.mean(advances))) if advances else metric["natural_advance"],
        "baseline_advance_min": min(advances) if advances else metric["natural_advance"],
        "baseline_advance_max": max(advances) if advances else metric["natural_advance"],
        "visible_gap": max(line_gaps, default=0),
        "max_visible_gap": max(line_gaps, default=0),
    })
    return metrics


def _free_text_wrap_candidate(
    words: List[str],
    widths: List[int],
    space_width: int,
    font_size: int,
    line_spacing: float,
    line_count: int,
    target_width: float,
) -> Optional[LayoutCandidate]:
    """Build one ordinary paragraph shape; every word stays atomic."""
    if not words or line_count < 1 or line_count > len(words):
        return None

    states: Dict[Tuple[int, int], Tuple[float, List[Tuple[int, int, int]]]] = {(0, 0): (0.0, [])}
    for used in range(line_count):
        next_states: Dict[Tuple[int, int], Tuple[float, List[Tuple[int, int, int]]]] = {}
        for (start, _), (cost, chunks) in states.items():
            max_end = len(words) - (line_count - used - 1)
            run_width = 0
            for end in range(start + 1, max_end + 1):
                run_width += widths[end - 1]
                if end - start > 1:
                    run_width += space_width
                remaining = line_count - used - 1
                remaining_words = len(words) - end
                if remaining_words < remaining:
                    continue

                line_cost = ((run_width - target_width) / max(1.0, float(font_size))) ** 2
                if remaining:
                    line_cost += _phrase_break_penalty(words[end - 1], words[end])
                    if end - start == 1 and len(words[end - 1].strip(_PUNCT_STRIP)) <= 3:
                        line_cost += 18.0
                new_cost = cost + line_cost
                key = (end, used + 1)
                old = next_states.get(key)
                if old is None or new_cost < old[0]:
                    next_states[key] = (new_cost, chunks + [(start, end, run_width)])
        states = next_states

    state = states.get((len(words), line_count))
    if state is None:
        return None

    _, chunks = state
    line_height = _free_text_line_height(font_size, line_spacing)
    max_width = max(chunk[2] for chunk in chunks)
    lines: List[PlacedLine] = []
    for row, (start, end, width) in enumerate(chunks):
        x = int(round((max_width - width) / 2.0))
        slot = BandSlot(left=0, right=max_width, y_start=row * line_height, y_end=row * line_height + font_size)
        lines.append(PlacedLine(
            text=" ".join(words[start:end]),
            y=row * line_height,
            x=x,
            width=width,
            height=font_size,
            slot=slot,
        ))
    return LayoutCandidate(
        font_size=font_size,
        y_origin=0,
        line_spacing=line_spacing,
        lines=lines,
        penalty=0.0,
        glyph_clearance_p5=0.0,
        status="free_text_typography",
        valid=True,
    )


def _free_text_typography_score(
    candidate: LayoutCandidate,
    profile: OriginalLayoutProfile,
    target: Optional[FreeTextDamageTarget] = None,
) -> float:
    """Score real glyph footprint without page coordinates or obstacle geometry."""
    ink = _free_text_candidate_ink_metrics(candidate)
    widths = [float(line.width) for line in candidate.lines]
    target_width = float(target.width if target is not None else profile.block_width)
    target_height = float(target.height if target is not None else profile.block_height)
    target_width = max(1.0, target_width)
    target_height = max(1.0, target_height)
    target_ar = max(0.05, target_width / target_height)
    ink_width = max(1.0, float(ink["ink_width"]))
    ink_height = max(1.0, float(ink["ink_height"]))
    ink_ar = max(0.05, ink_width / ink_height)

    # Tall targets get a stronger height term; wide targets get a stronger width term.
    width_weight, height_weight = (1.35, 1.0) if target_ar >= 1.0 else (1.0, 1.35)
    footprint_penalty = (
        width_weight * abs(ink_width - target_width) / target_width
        + height_weight * abs(ink_height - target_height) / target_height
    ) * 6.0
    aspect_penalty = abs(math.log(ink_ar / target_ar)) * 5.0
    mean_width = max(1.0, float(np.mean(widths)))
    ragged_penalty = float(np.var(widths)) / (mean_width * mean_width) * 8.0
    orphan_penalty = sum(
        12.0 for line in candidate.lines[:-1]
        if len(line.text.split()) == 1 and len(_word_core(line.text)) <= 3
    )
    line_penalty = abs(len(candidate.lines) - profile.line_count) * 1.5
    font_penalty = abs(candidate.font_size - profile.font_size) / max(1.0, profile.font_size) * 8.0
    return footprint_penalty + aspect_penalty + ragged_penalty + orphan_penalty + line_penalty + font_penalty


def _free_text_typography_candidates(
    text: str,
    profile: OriginalLayoutProfile,
    config: Config,
    image_shape: Tuple[int, int],
    target: Optional[FreeTextDamageTarget] = None,
    target_height: Optional[int] = None,
) -> List[LayoutCandidate]:
    """Generate frozen paragraph candidates; ``target_height`` is legacy-only."""
    # Keep the old keyword source-compatible, but never turn erased height into leading.
    del target_height
    words = _free_text_words(text)
    if not words:
        return []

    render_cfg = config.render
    minimum = render_cfg.font_size_minimum
    if minimum == -1:
        minimum = round(sum(image_shape) / 200)
    minimum = max(1, int(minimum))
    source_font = max(1, int(round(profile.font_size)))
    source_max = max(source_font, int(round(profile.font_size * 1.15)))
    if render_cfg.font_size is not None:
        font_sizes = [max(minimum, min(source_max, int(render_cfg.font_size)))]
    else:
        font_sizes = sorted({
            max(minimum, min(source_max, int(round(source_font * scale))))
            for scale in (1.15, 1.08, 1.00, 0.92, 0.84, 0.76, 0.68, 0.60, 0.50)
        }, reverse=True)

    candidates: List[LayoutCandidate] = []
    for font_size in font_sizes:
        widths, space_width = _precompute_widths(words, font_size)
        total_width = sum(widths) + max(0, len(words) - 1) * space_width
        line_spacing = _free_text_line_spacing(render_cfg.line_spacing)
        line_height = _free_text_line_height(font_size, line_spacing)
        target_width = float(target.width if target is not None else profile.block_width)
        target_height_value = float(target.height if target is not None else profile.block_height)
        target_ar = max(0.05, target_width / max(1.0, target_height_value))
        preferred_lines = int(round(math.sqrt(total_width / max(1.0, target_ar * line_height))))
        preferred_lines = max(1, min(len(words), preferred_lines))
        line_counts = sorted({
            max(1, min(len(words), count))
            for count in range(min(preferred_lines, profile.line_count) - 2, max(preferred_lines, profile.line_count) + 3)
        })
        for line_count in line_counts:
            wrap_width = max(
                float(font_size * 2),
                total_width / line_count,
                target_ar * line_count * line_height,
            )
            candidate = _free_text_wrap_candidate(
                words, widths, space_width, font_size,
                line_spacing, line_count, wrap_width,
            )
            if candidate is None:
                continue
            candidate.penalty = _free_text_typography_score(candidate, profile, target)
            ink = _free_text_candidate_ink_metrics(candidate)
            candidate.qa = {
                "font_size": candidate.font_size,
                "line_spacing": candidate.line_spacing,
                "lines": [line.text for line in candidate.lines],
                "line_widths": [line.width for line in candidate.lines],
                "typography_score": candidate.penalty,
                "ink_bbox": list(ink["ink_bbox"]),
                "ink_width": ink["ink_width"],
                "ink_height": ink["ink_height"],
                "ink_area": ink["ink_area"],
                "ink_aspect_ratio": ink["ink_width"] / max(1.0, ink["ink_height"]),
                "ink_line_height": ink["ink_line_height"],
                "font_metric_height": ink["font_metric_height"],
                "baseline_advance": ink["baseline_advance"],
                "baseline_advance_min": ink["baseline_advance_min"],
                "baseline_advance_max": ink["baseline_advance_max"],
                "visible_gap": ink["visible_gap"],
                "baseline_consistent": ink["baseline_advance_min"] == ink["baseline_advance_max"],
                "line_gap_sane": ink["visible_gap"] <= max(4, ink["font_metric_height"] * 0.5),
                "target_width": target_width,
                "target_height": target_height_value,
                "target_aspect_ratio": target_ar,
            }
            candidates.append(candidate)

    candidates.sort(key=lambda candidate: candidate.penalty)
    unique: List[LayoutCandidate] = []
    seen = set()
    for candidate in candidates:
        key = (candidate.font_size, tuple(line.text for line in candidate.lines))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= 12:
            break
    return unique


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


def _free_text_hard_valid(
    crop_box: Tuple[int, int, int, int],
    visual_crop: np.ndarray,
    zone: FreeTextZone,
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
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
    crop_box, glyph, _, _ = _candidate_cropped_visual_masks(candidate, 0, image_shape)
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


def _solve_free_text_region(
    region: Any,
    zone: FreeTextZone,
    obstacles: PageObstacleMap,
    config: Config,
    image_shape: Tuple[int, int],
    solver_margin: float,
    solver_max_y_trials: int,
) -> Optional[Tuple[LayoutCandidate, OriginalLayoutProfile, Dict[str, Any]]]:
    """Solve FREE_TEXT as frozen typography followed by rigid placement.

    The inpainting mask determines where the paragraph belongs, never how its
    words wrap. Bubble geometry is intentionally not called from this path.
    """
    text = (
        region.get_translation_for_rendering()
        if hasattr(region, "get_translation_for_rendering")
        else (getattr(region, "translation", "") or getattr(region, "text", ""))
    ).strip()
    source_mask = getattr(region, "_free_text_source_mask", None)
    if not text or source_mask is None or not np.any(source_mask):
        return None

    profile = build_original_layout_profile(region, zone.ownership_mask)
    if profile is None:
        return None

    target = zone.damage_target
    prof = get_solver_profile()
    t_topo0 = perf_counter()
    typography = _free_text_typography_candidates(
        text,
        profile,
        config,
        image_shape,
        target=target,
    )
    prof.ft_typography_ms += (perf_counter() - t_topo0) * 1000.0
    if not typography:
        return None

    if target is None or target.area == 0:
        damage_centroid = profile.centroid
    else:
        damage_centroid = (target.centroid_x, target.centroid_y)
    target_width = target.width if target is not None else profile.block_width
    target_height = target.height if target is not None else profile.block_height

    stroke_width = max(1, int(max(candidate.font_size for candidate in typography) * 0.07))
    source = source_mask.astype(bool)
    other_text = obstacles.text_mask.astype(bool) & ~source
    evaluated: List[LayoutCandidate] = []
    typography.sort(key=lambda c: c.penalty)
    eval_candidates = typography[:16]

    for typography_candidate in eval_candidates:
        t_crop0 = perf_counter()
        prof.free_text_crops_rendered += 1
        base_box, ink_crop, visual_crop, block_crop = _candidate_cropped_visual_masks(
            typography_candidate, stroke_width, image_shape,
        )
        prof.ft_crops_rasterize_ms += (perf_counter() - t_crop0) * 1000.0
        bx1, by1, bx2, by2 = base_box
        if bx2 <= bx1 or by2 <= by1 or not np.any(ink_crop):
            continue
        ink_y, ink_x = np.nonzero(ink_crop)
        base_centroid = (bx1 + float(ink_x.mean()), by1 + float(ink_y.mean()))
        base_ink_bbox = (
            bx1 + int(ink_x.min()), by1 + int(ink_y.min()),
            bx1 + int(ink_x.max()) + 1, by1 + int(ink_y.max()) + 1,
        )
        ideal_dx = int(round(damage_centroid[0] - base_centroid[0]))
        ideal_dy = int(round(damage_centroid[1] - base_centroid[1]))
        max_radius = max(16, min(64, int(max(target_width, target_height, profile.block_width, profile.block_height))))

        # Stage A: Coarse offset exploration
        coarse_offsets = _free_text_offset_search(max_radius)
        best_coarse_offset: Optional[Tuple[int, int]] = None
        best_coarse_score = float("inf")

        tested_offsets = set()
        for rel_dx, rel_dy in coarse_offsets:
            tested_offsets.add((rel_dx, rel_dy))
            prof.free_text_offsets_tested += 1
            dx = ideal_dx + rel_dx
            dy = ideal_dy + rel_dy
            crop_box = (bx1 + dx, by1 + dy, bx2 + dx, by2 + dy)
            t_off0 = perf_counter()
            is_valid = _free_text_hard_valid(crop_box, visual_crop, zone, obstacles, other_text)
            prof.ft_offset_search_ms += (perf_counter() - t_off0) * 1000.0
            if not is_valid:
                continue
            prof.free_text_hard_valid_hits += 1

            t_cov0 = perf_counter()
            coverage = _measure_damage_coverage_crop(crop_box, visual_crop, ink_crop, block_crop, zone)
            prof.ft_coverage_ms += (perf_counter() - t_cov0) * 1000.0

            actual_centroid = (base_centroid[0] + dx, base_centroid[1] + dy)
            center_dx = actual_centroid[0] - damage_centroid[0]
            center_dy = actual_centroid[1] - damage_centroid[1]
            center_penalty = (
                (rel_dx / max(1.0, float(max(target_width, profile.block_width)))) ** 2
                + (rel_dy / max(1.0, float(max(target_height, profile.block_height)))) ** 2
            )
            source_drift = math.hypot(
                actual_centroid[0] - profile.centroid[0],
                actual_centroid[1] - profile.centroid[1],
            ) / max(1.0, math.hypot(profile.block_width, profile.block_height))
            score = (
                typography_candidate.penalty
                + center_penalty * 100.0
                + source_drift * 3.0
                - coverage["c_damage"] * 15.0
                + (1.0 - coverage["c_core"]) * 5.0
            )

            if score < best_coarse_score:
                best_coarse_score = score
                best_coarse_offset = (rel_dx, rel_dy)

            candidate = _free_text_shift_candidate(typography_candidate, dx, dy)
            cand_bbox = _candidate_bbox(candidate)
            ink_bbox = tuple(value + delta for value, delta in zip(base_ink_bbox, (dx, dy, dx, dy)))
            candidate.penalty = score
            candidate.qa.update({
                "placement_mode": PlacementMode.FREE_TEXT.value,
                "source_bbox": profile.bbox,
                "source_font_size": profile.font_size,
                "source_line_count": profile.line_count,
                "damage_bbox": target.bbox if target is not None else profile.bbox,
                "damage_centroid": [damage_centroid[0], damage_centroid[1]],
                "ink_centroid": [actual_centroid[0], actual_centroid[1]],
                "center_error_px": math.hypot(center_dx, center_dy),
                "placement_dx": rel_dx,
                "placement_dy": rel_dy,
                "coverage_ink": coverage["c_ink"],
                "coverage_visual": coverage["c_visual"],
                "coverage_block": coverage["c_block"],
                "damage_coverage": coverage["c_damage"],
                "core_damage_coverage": coverage["c_core"],
                "uncovered_damage": coverage["u_damage"],
                **_free_text_footprint_qa(profile, target, ink_bbox),
                "ink_overflow": _free_text_ink_overflow(candidate, image_shape, obstacles, other_text),
                "layout_width": cand_bbox[2] - cand_bbox[0],
                "layout_height": cand_bbox[3] - cand_bbox[1],
                "expansion_ratio": (ink_bbox[2] - ink_bbox[0]) * (ink_bbox[3] - ink_bbox[1]) / max(1.0, profile.block_width * profile.block_height),
                "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text"],
                "free_text_score": score,
            })
            candidate.status = "free_text"
            evaluated.append(candidate)

        # Stage B: Fine refinement around best coarse offset
        if best_coarse_offset is not None:
            fine_offsets = _free_text_offset_refine(best_coarse_offset[0], best_coarse_offset[1], step=2)
            for rel_dx, rel_dy in fine_offsets:
                if (rel_dx, rel_dy) in tested_offsets:
                    continue
                tested_offsets.add((rel_dx, rel_dy))
                prof.free_text_offsets_tested += 1
                dx = ideal_dx + rel_dx
                dy = ideal_dy + rel_dy
                crop_box = (bx1 + dx, by1 + dy, bx2 + dx, by2 + dy)
                t_off0 = perf_counter()
                is_valid = _free_text_hard_valid(crop_box, visual_crop, zone, obstacles, other_text)
                prof.ft_offset_search_ms += (perf_counter() - t_off0) * 1000.0
                if not is_valid:
                    continue
                prof.free_text_hard_valid_hits += 1

                t_cov0 = perf_counter()
                coverage = _measure_damage_coverage_crop(crop_box, visual_crop, ink_crop, block_crop, zone)
                prof.ft_coverage_ms += (perf_counter() - t_cov0) * 1000.0

                actual_centroid = (base_centroid[0] + dx, base_centroid[1] + dy)
                center_dx = actual_centroid[0] - damage_centroid[0]
                center_dy = actual_centroid[1] - damage_centroid[1]
                center_penalty = (
                    (rel_dx / max(1.0, float(max(target_width, profile.block_width)))) ** 2
                    + (rel_dy / max(1.0, float(max(target_height, profile.block_height)))) ** 2
                )
                source_drift = math.hypot(
                    actual_centroid[0] - profile.centroid[0],
                    actual_centroid[1] - profile.centroid[1],
                ) / max(1.0, math.hypot(profile.block_width, profile.block_height))
                score = (
                    typography_candidate.penalty
                    + center_penalty * 100.0
                    + source_drift * 3.0
                    - coverage["c_damage"] * 15.0
                    + (1.0 - coverage["c_core"]) * 5.0
                )

                candidate = _free_text_shift_candidate(typography_candidate, dx, dy)
                cand_bbox = _candidate_bbox(candidate)
                ink_bbox = tuple(value + delta for value, delta in zip(base_ink_bbox, (dx, dy, dx, dy)))
                candidate.penalty = score
                candidate.qa.update({
                    "placement_mode": PlacementMode.FREE_TEXT.value,
                    "source_bbox": profile.bbox,
                    "source_font_size": profile.font_size,
                    "source_line_count": profile.line_count,
                    "damage_bbox": target.bbox if target is not None else profile.bbox,
                    "damage_centroid": [damage_centroid[0], damage_centroid[1]],
                    "ink_centroid": [actual_centroid[0], actual_centroid[1]],
                    "center_error_px": math.hypot(center_dx, center_dy),
                    "placement_dx": rel_dx,
                    "placement_dy": rel_dy,
                    "coverage_ink": coverage["c_ink"],
                    "coverage_visual": coverage["c_visual"],
                    "coverage_block": coverage["c_block"],
                    "damage_coverage": coverage["c_damage"],
                    "core_damage_coverage": coverage["c_core"],
                    "uncovered_damage": coverage["u_damage"],
                    **_free_text_footprint_qa(profile, target, ink_bbox),
                    "ink_overflow": _free_text_ink_overflow(candidate, image_shape, obstacles, other_text),
                    "layout_width": cand_bbox[2] - cand_bbox[0],
                    "layout_height": cand_bbox[3] - cand_bbox[1],
                    "expansion_ratio": (ink_bbox[2] - ink_bbox[0]) * (ink_bbox[3] - ink_bbox[1]) / max(1.0, profile.block_width * profile.block_height),
                    "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text"],
                    "free_text_score": score,
                })
                candidate.status = "free_text"
                evaluated.append(candidate)

    if not evaluated:
        # Resilient fallback: ensure translated text is never dropped without a layout candidate
        for typography_candidate in typography:
            base_box, ink_crop, visual_crop, block_crop = _candidate_cropped_visual_masks(
                typography_candidate, stroke_width, image_shape,
            )
            bx1, by1, bx2, by2 = base_box
            if bx2 <= bx1 or by2 <= by1 or not np.any(ink_crop):
                continue
            ink_y, ink_x = np.nonzero(ink_crop)
            base_centroid = (bx1 + float(ink_x.mean()), by1 + float(ink_y.mean()))
            base_ink_bbox = (
                bx1 + int(ink_x.min()), by1 + int(ink_y.min()),
                bx1 + int(ink_x.max()) + 1, by1 + int(ink_y.max()) + 1,
            )
            ideal_dx = int(round(damage_centroid[0] - base_centroid[0]))
            ideal_dy = int(round(damage_centroid[1] - base_centroid[1]))
            clamp_dx = max(-bx1, min(image_shape[1] - bx2, ideal_dx))
            clamp_dy = max(-by1, min(image_shape[0] - by2, ideal_dy))
            candidate = _free_text_shift_candidate(typography_candidate, clamp_dx, clamp_dy)
            candidate.penalty = typography_candidate.penalty + 500.0
            candidate.status = "free_text_fallback"
            ink_bbox = tuple(value + delta for value, delta in zip(base_ink_bbox, (clamp_dx, clamp_dy, clamp_dx, clamp_dy)))
            candidate.qa.update({
                "placement_mode": PlacementMode.FREE_TEXT.value,
                "source_bbox": profile.bbox,
                "source_font_size": profile.font_size,
                "source_line_count": profile.line_count,
                "damage_bbox": target.bbox if target is not None else profile.bbox,
                "damage_centroid": [damage_centroid[0], damage_centroid[1]],
                "ink_centroid": [base_centroid[0] + clamp_dx, base_centroid[1] + clamp_dy],
                "center_error_px": math.hypot(clamp_dx - ideal_dx, clamp_dy - ideal_dy),
                "placement_dx": clamp_dx - ideal_dx,
                "placement_dy": clamp_dy - ideal_dy,
                **_free_text_footprint_qa(profile, target, ink_bbox),
                "ink_overflow": _free_text_ink_overflow(candidate, image_shape, obstacles, other_text),
                "free_text_score": candidate.penalty,
                "fallback": True,
            })
            evaluated.append(candidate)
            break

    if not evaluated:
        return None

    evaluated.sort(key=lambda candidate: candidate.penalty)
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


def _select_free_text_joint_candidates(
    regions: List[Any],
    plans: Dict[int, List[LayoutCandidate]],
    image_shape: Tuple[int, int],
    free_profiles: Optional[Dict[int, OriginalLayoutProfile]] = None,
) -> Dict[int, LayoutCandidate]:
    """Select optimal joint combination for all free-text regions without touching bubbles."""
    if not regions:
        return {}
    if len(regions) == 1:
        rid = id(regions[0])
        return {rid: plans[rid][0]} if plans.get(rid) else {}

    # Check pairwise candidates for collision and choose combination with lowest total penalty
    free_plans = [
        _RegionLayoutPlan(
            region=r,
            text=(
                r.get_translation_for_rendering()
                if hasattr(r, "get_translation_for_rendering")
                else (getattr(r, "translation", "") or getattr(r, "text", ""))
            ),
            source_profile=(free_profiles.get(id(r)) if free_profiles else getattr(r, "_source_profile", None)),
            candidates=plans.get(id(r), []),
            fg=getattr(r, "fg_color", (0, 0, 0)),
            bg=getattr(r, "bg_color", (255, 255, 255)),
            line_spacing=0.1,
            language=getattr(r, "target_lang", "ENG") or "ENG",
        )
        for r in regions if plans.get(id(r))
    ]
    chosen = _choose_joint_layout(free_plans, image_shape)
    if chosen is not None:
        return {id(free_plans[i].region): chosen[i] for i in range(len(chosen))}

    # Keep the hard collision invariant even when no full Cartesian
    # combination is legal; place the most constrained candidates greedily.
    selected: Dict[int, LayoutCandidate] = {}
    occupied = []
    for region in sorted(regions, key=lambda item: len(plans.get(id(item), [])) or 999):
        for candidate in plans.get(id(region), []):
            crop_box, glyph_mask, _, _ = _candidate_data(candidate, image_shape)
            if any(_cropped_masks_overlap(crop_box, glyph_mask, other_box, other_mask) for other_box, other_mask in occupied):
                continue
            selected[id(region)] = candidate
            occupied.append((crop_box, glyph_mask))
            break
    return selected


def _apply_free_text_candidate(
    region: Any,
    candidate: LayoutCandidate,
    profile: OriginalLayoutProfile,
    config: Config,
    image_shape: Tuple[int, int],
    layout_debug: bool = False,
) -> bool:
    text = (
        region.get_translation_for_rendering()
        if hasattr(region, "get_translation_for_rendering")
        else (getattr(region, "translation", "") or getattr(region, "text", ""))
    )
    all_x1 = min(line.x for line in candidate.lines)
    all_y1 = min(line.y for line in candidate.lines)
    all_x2 = max(line.x + line.width for line in candidate.lines)
    all_y2 = max(line.y + line.height for line in candidate.lines)
    layout_rect = [all_x1, all_y1, all_x2, all_y2]
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



def _ink_centroid(
    lines: List[PlacedLine],
    font_size: int,
) -> Tuple[float, float, int]:
    """Calculate centroid of the placed lines candidate block."""
    total_x = 0.0
    total_y = 0.0
    total_n = 0
    for line in lines:
        n = max(1, line.width * line.height)
        total_x += (line.x + line.width / 2.0) * n
        total_y += (line.y + line.height / 2.0) * n
        total_n += n

    if total_n == 0:
        return 0.0, 0.0, 0
    return total_x / total_n, total_y / total_n, total_n


def _glyph_mask_for_lines(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
) -> np.ndarray:
    """Rasterize a candidate into the geometry's local coordinate system."""
    mask = np.zeros(geom.shape, dtype=bool)
    h, w = geom.shape
    for line in lines:
        alpha = _render_line_alpha(line, font_size)
        if alpha is None:
            x1, y1 = line.x, line.y
            x2, y2 = min(w, x1 + line.width), min(h, y1 + line.height)
            if x1 < x2 and y1 < y2:
                mask[y1:y2, x1:x2] = True
            continue
        glyph = alpha > 127
        ay, ax = glyph.shape
        x1, y1 = line.x, line.y
        x2, y2 = min(w, x1 + ax), min(h, y1 + ay)
        if x1 < x2 and y1 < y2:
            mask[y1:y2, x1:x2] |= glyph[:y2 - y1, :x2 - x1]
    return mask


def build_original_layout_profile(region: Any, interior: np.ndarray) -> Optional[OriginalLayoutProfile]:
    """Derive the original typography from captured OCR line geometry.

    Everything needed is already in the capture (region.lines quads,
    region.font_size, bubble interior) — no extra ML model required.
    """
    region_lines = getattr(region, "lines", None)
    if region_lines is None or len(region_lines) == 0:
        return None

    quads = np.asarray(region_lines, dtype=np.float32).reshape(-1, 4, 2)
    texts = getattr(region, "texts", None) or []
    full_text = getattr(region, "text", "") or ""

    line_entries: List[Dict[str, Any]] = []
    x1_all = y1_all = float("inf")
    x2_all = y2_all = float("-inf")
    heights: List[float] = []
    for idx, quad in enumerate(quads):
        x1, y1 = float(quad[:, 0].min()), float(quad[:, 1].min())
        x2, y2 = float(quad[:, 0].max()), float(quad[:, 1].max())
        w, h = x2 - x1, y2 - y1
        heights.append(h)
        x1_all, y1_all = min(x1_all, x1), min(y1_all, y1)
        x2_all, y2_all = max(x2_all, x2), max(y2_all, y2)
        line_entries.append({
            "text": texts[idx] if idx < len(texts) else "",
            "center_x": (x1 + x2) / 2.0,
            "center_y": (y1 + y2) / 2.0,
            "width": w,
            "height": h,
        })

    font_size = float(getattr(region, "font_size", -1) or -1)
    if font_size <= 0:
        heights_sorted = sorted(heights)
        font_size = heights_sorted[len(heights_sorted) // 2] * 0.8
    font_size = max(1.0, font_size)

    weights = [max(1.0, e["width"] * e["height"]) for e in line_entries]
    wsum = sum(weights) if weights else 1.0
    centroid = (
        sum(e["center_x"] * w for e, w in zip(line_entries, weights)) / wsum,
        sum(e["center_y"] * w for e, w in zip(line_entries, weights)) / wsum,
    )

    block_w = x2_all - x1_all
    block_h = y2_all - y1_all
    interior_area = float(np.count_nonzero(interior))
    occupancy = (block_w * block_h) / interior_area if interior_area > 0 else 0.0

    # Calculate normalized centroid relative to bubble bounding box
    ys, xs = np.nonzero(interior)
    if len(ys) > 0 and len(xs) > 0:
        bx1, bx2 = float(xs.min()), float(xs.max()) + 1.0
        by1, by2 = float(ys.min()), float(ys.max()) + 1.0
        bw = max(1.0, bx2 - bx1)
        bh = max(1.0, by2 - by1)
        u = (centroid[0] - bx1) / bw
        v = (centroid[1] - by1) / bh
        norm_centroid = (max(0.0, min(1.0, u)), max(0.0, min(1.0, v)))
    else:
        norm_centroid = (0.5, 0.5)

    return OriginalLayoutProfile(
        font_size=font_size,
        line_count=len(line_entries),
        lines=line_entries,
        centroid=centroid,
        bbox=(int(x1_all), int(y1_all), int(x2_all), int(y2_all)),
        block_width=block_w,
        block_height=block_h,
        occupancy=occupancy,
        normalized_centroid=norm_centroid,
    )


# ---------------------------------------------------------------------------
# Phase 7/8/9 — composite soft objective
# ---------------------------------------------------------------------------

def _composite_penalty(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
    font_target: int,
    source_profile: Optional[OriginalLayoutProfile],
    stroke_width: int = 0,
    margin: float = 2.0,
    preferred_mask: Optional[np.ndarray] = None,
    target_geom: Optional[PlacementTarget] = None,
    zone_profile: Optional[ZoneShapeProfile] = None,
) -> Tuple[float, Dict[str, float]]:
    """Score a complete candidate block: language is already priced into the
    DP; this adds typography (font size vs target), composition (silhouette,
    fill variance, jitter), whitespace (occupancy band), placement (ink
    centroid), whitespace balance (top-vs-bottom and left-vs-right free space),
    vertical utilization, aspect ratio match, and — when available — similarity
    to the original layout."""
    if not lines:
        return float("inf"), {}

    n = len(lines)
    qa: Dict[str, float] = {}

    mask_area = float(np.count_nonzero(geom.cleaned_mask))
    ref_size = max(1.0, math.sqrt(mask_area))

    # A. Typography: prefer the target size; smaller sizes must earn it.
    p_font = _font_penalty(font_size, font_target)
    qa["p_font"] = p_font

    # B. Placement: actual glyph-pixel centroid vs the target.
    #    When target_geom is provided (e.g. from capacity weighting), align against it.
    ink_cx, ink_cy, glyph_n = _ink_centroid(lines, font_size)
    ink_global_cx = ink_cx + getattr(geom, "x_offset", 0)
    ink_global_cy = ink_cy + getattr(geom, "y_offset", 0)
    if target_geom is not None:
        target_cx = target_geom.center_x + getattr(geom, "x_offset", 0)
        target_cy = target_geom.center_y + getattr(geom, "y_offset", 0)
    elif source_profile is not None:
        target_cx, target_cy = source_profile.centroid
    else:
        cx_mask, cy_mask = geom.centroid()
        target_cx, target_cy = cx_mask + getattr(geom, "x_offset", 0), cy_mask + getattr(geom, "y_offset", 0)

    centroid_d = math.sqrt((ink_global_cx - target_cx) ** 2 + (ink_global_cy - target_cy) ** 2)
    p_centroid = (centroid_d / ref_size) * _WEIGHT_CENTROID
    qa["center_error_px"] = centroid_d
    qa["ink_cx"] = ink_global_cx
    qa["ink_cy"] = ink_global_cy
    qa["target_cx"] = target_cx
    qa["target_cy"] = target_cy

    # C. Whitespace & Balance (Phase 8 & 9)
    #    Measure usable free space from the safe mask around the rendered block.
    safe_zone = target_geom.mask if target_geom is not None else geom.safe_pixels(font_size, stroke_width, margin)
    zone_area = float(np.count_nonzero(safe_zone)) if np.any(safe_zone) else mask_area

    block_left = min(line.x for line in lines)
    block_right = max(line.x + line.width for line in lines)
    block_top = min(line.y for line in lines)
    block_bottom = max(line.y + line.height for line in lines)

    block_cx = (block_left + block_right) / 2.0
    block_cy = (block_top + block_bottom) / 2.0

    qa["block_center_dx"] = block_cx - (target_geom.center_x if target_geom is not None else (target_cx - getattr(geom, "x_offset", 0)))
    qa["block_center_dy"] = block_cy - (target_geom.center_y if target_geom is not None else (target_cy - getattr(geom, "y_offset", 0)))

    # Evaluate free-space capacity above, below, left, and right in the safe mask
    if zone_area > 0 and safe_zone.ndim == 2:
        h_mask, w_mask = safe_zone.shape
        top_clamped = max(0, min(h_mask, block_top))
        bottom_clamped = max(0, min(h_mask, block_bottom))
        left_clamped = max(0, min(w_mask, block_left))
        right_clamped = max(0, min(w_mask, block_right))

        a_above = float(np.count_nonzero(safe_zone[:top_clamped, :]))
        a_below = float(np.count_nonzero(safe_zone[bottom_clamped:, :]))
        a_left = float(np.count_nonzero(safe_zone[:, :left_clamped]))
        a_right = float(np.count_nonzero(safe_zone[:, right_clamped:]))

        top_free_ratio = a_above / zone_area
        bottom_free_ratio = a_below / zone_area
        left_free_ratio = a_left / zone_area
        right_free_ratio = a_right / zone_area

        v_balance = abs(a_above - a_below) / zone_area
        h_balance = abs(a_left - a_right) / zone_area
    else:
        top_free_ratio = 0.0
        bottom_free_ratio = 0.0
        left_free_ratio = 0.0
        right_free_ratio = 0.0
        v_balance = 0.0
        h_balance = 0.0

    qa["top_free_ratio"] = top_free_ratio
    qa["bottom_free_ratio"] = bottom_free_ratio
    qa["left_free_ratio"] = left_free_ratio
    qa["right_free_ratio"] = right_free_ratio
    qa["vertical_balance"] = v_balance
    qa["horizontal_balance"] = h_balance

    p_balance = (v_balance ** 2) * _WEIGHT_VERT_BALANCE + (h_balance ** 2) * _WEIGHT_HORIZ_BALANCE
    qa["p_balance"] = p_balance

    text_area = float(sum(line.width * line.height for line in lines))
    occupancy = text_area / mask_area if mask_area > 0 else 0.0
    occ_lo, occ_hi = _OCC_IDEAL
    if occupancy < occ_lo:
        p_occ = (occ_lo - occupancy) ** 2 * _WEIGHT_OCC_LOW
    elif occupancy > occ_hi:
        p_occ = (occupancy - occ_hi) ** 2 * _WEIGHT_OCC_HIGH
    else:
        p_occ = 0.0
    qa["occupancy"] = occupancy
    qa["p_occupancy"] = p_occ

    # D. Vertical utilization & Aspect ratio matching
    h_text = max(1.0, float(block_bottom - block_top))
    w_text = max(1.0, float(block_right - block_left))
    ar_text = w_text / h_text

    if zone_profile is not None:
        h_usable = max(1.0, float(zone_profile.height))
        w_usable = max(1.0, float(zone_profile.width))
        ar_zone = max(0.01, float(zone_profile.aspect_ratio))
    else:
        h_usable = max(1.0, float(geom.shape[0]))
        w_usable = max(1.0, float(geom.shape[1]))
        ar_zone = w_usable / h_usable

    r_v = min(1.5, h_text / h_usable)
    qa["vertical_utilization"] = r_v
    qa["ar_text"] = ar_text
    qa["ar_zone"] = ar_zone

    # Vertical fill penalty: desirable band 0.55 <= Rv <= 0.80
    p_vfill = 0.0
    if r_v < 0.55:
        p_vfill = ((0.55 - r_v) / 0.55) ** 2 * _WEIGHT_VFILL_LOW
    elif r_v > 0.80:
        p_vfill = ((r_v - 0.80) / 0.20) ** 2 * _WEIGHT_VFILL_HIGH
    qa["p_vertical_fill"] = p_vfill

    # Aspect ratio mismatch penalty
    aspect_ratio_diff = abs(math.log(max(0.05, ar_text) / max(0.05, ar_zone)))
    p_aspect = (aspect_ratio_diff ** 2) * _WEIGHT_ASPECT
    qa["aspect_mismatch"] = aspect_ratio_diff
    qa["p_aspect"] = p_aspect

    # Silhouette / Envelope match against width_by_y
    p_silhouette = 0.0
    if zone_profile is not None and zone_profile.width_by_y and len(lines) >= 2:
        y_base = zone_profile.bbox[1]
        line_ratios = []
        for line in lines:
            rel_y = max(0, min(len(zone_profile.width_by_y) - 1, int(line.y + line.height / 2.0 - y_base)))
            avail_w = max(1.0, zone_profile.width_by_y[rel_y])
            line_ratios.append(min(1.5, line.width / avail_w))
        ratio_diffs = [abs(line_ratios[i + 1] - line_ratios[i]) for i in range(len(line_ratios) - 1)]
        p_silhouette = (sum(ratio_diffs) / len(ratio_diffs)) * _WEIGHT_SILHOUETTE
    qa["p_silhouette"] = p_silhouette

    # E. Composition: smooth text silhouette (second derivative of widths),
    #    moderate fill variance, low jitter, mild raggedness, block compactness, and X-center coherence.
    widths = [float(line.width) for line in lines]
    if n >= 3:
        diffs = [widths[i + 1] - widths[i] for i in range(n - 1)]
        p_shape = sum(abs(diffs[i + 1] - diffs[i]) for i in range(len(diffs) - 1))
        p_shape = (p_shape / max(1.0, font_size * (n - 2))) * _WEIGHT_SHAPE * 10.0
    else:
        p_shape = 0.0

    fills = [line.width / max(1, line.slot.width) for line in lines]
    fill_var = float(np.var(fills)) if fills else 0.0
    p_fill_var = fill_var * _WEIGHT_FILL_VAR

    jitter = sum(abs((line.x + line.width / 2.0) - line.slot.center) for line in lines)
    p_jitter = jitter * _WEIGHT_JITTER

    ragged = 0.0
    for i in range(n - 1):
        avg = max(1.0, (widths[i] + widths[i + 1]) / 2.0)
        ragged += ((widths[i] - widths[i + 1]) / avg) ** 2
    p_ragged = ragged * _WEIGHT_RAGGED

    orphan_penalty = sum(
        _WEIGHT_ORPHAN for i, line in enumerate(lines)
        if i < n - 1 and len(line.text.split()) == 1 and len(_word_core(line.text)) <= 3
    )
    hyphen_penalty = sum(_WEIGHT_HYPHEN for line in lines[:-1] if line.text.endswith("-"))

    # Block compactness (vertical gap ratio inside rendered block)
    y_top = min(line.y for line in lines)
    y_bottom = max(line.y + line.height for line in lines)
    h_span = max(1.0, float(y_bottom - y_top))
    h_text_actual = float(sum(line.height for line in lines))
    gap_ratio = max(0.0, (h_span - h_text_actual) / h_span)
    p_block_gap = (gap_ratio ** 2) * _WEIGHT_BLOCK_GAP
    qa["gap_ratio"] = gap_ratio
    qa["p_block_gap"] = p_block_gap

    # Global X coherence (line center variance relative to geometry slots)
    centers = [line.x + line.width / 2.0 for line in lines]
    slot_centers = [line.slot.center for line in lines]
    c_var = float(np.var(centers)) if len(centers) > 1 else 0.0
    slot_c_var = float(np.var(slot_centers)) if len(slot_centers) > 1 else 0.0
    # Excessive variance not justified by slot alignment geometry
    excess_var = max(0.0, c_var - slot_c_var)
    norm_excess_var = excess_var / max(1.0, float(font_size ** 2))
    p_center_var = norm_excess_var * _WEIGHT_CENTER_VAR
    qa["center_variance"] = c_var
    qa["p_center_var"] = p_center_var

    p_composition = (
        p_shape
        + p_fill_var
        + p_jitter
        + p_ragged
        + orphan_penalty
        + hyphen_penalty
        + p_block_gap
        + p_center_var
    )
    qa["p_shape"] = p_shape
    qa["p_composition"] = p_composition

    # F. Source similarity: the original page is an artistic prior (Phase 10).
    # Weaken source-centroid weight when the region owns an entire single-region bubble.
    is_single_region = target_geom.is_single_region if target_geom is not None else True
    src_centroid_weight = _WEIGHT_SRC_CENTROID * (0.20 if is_single_region else 1.0)

    p_source = 0.0
    if source_profile is not None:
        prof_cx, prof_cy = source_profile.centroid
        d_cent = math.sqrt((ink_global_cx - prof_cx) ** 2 + (ink_global_cy - prof_cy) ** 2)
        p_src_centroid = (d_cent / ref_size) * src_centroid_weight

        p_src_lines = abs(n - source_profile.line_count) * _WEIGHT_SRC_LINES

        if source_profile.font_size > 0:
            p_src_font = ((font_size - source_profile.font_size) / source_profile.font_size) ** 2 * _WEIGHT_SRC_FONT
        else:
            p_src_font = 0.0

        block_w = max(line.x + line.width for line in lines) - min(line.x for line in lines)
        block_h = max(line.y + line.height for line in lines) - min(line.y for line in lines)
        if source_profile.block_width > 0 and source_profile.block_height > 0:
            d_bw = abs(block_w - source_profile.block_width) / source_profile.block_width
            d_bh = abs(block_h - source_profile.block_height) / source_profile.block_height
        else:
            d_bw = d_bh = 0.0
        p_src_bbox = (d_bw + d_bh) * _WEIGHT_SRC_BBOX

        # Relative line-center pattern matching
        p_src_pattern = 0.0
        if source_profile.lines and len(source_profile.lines) >= 2 and n >= 2:
            # Normalize source centers relative to source block width
            src_min_x = min(entry.get("center_x", 0.0) - entry.get("width", 0.0) / 2.0 for entry in source_profile.lines)
            src_w = max(1.0, float(source_profile.block_width))
            src_rel_centers = [
                (entry.get("center_x", 0.0) - src_min_x) / src_w for entry in source_profile.lines
            ]

            # Normalize candidate centers relative to candidate block width
            cand_min_x = min(line.x for line in lines)
            cand_w = max(1.0, float(block_w))
            cand_rel_centers = [
                ((line.x + line.width / 2.0) - cand_min_x) / cand_w for line in lines
            ]

            # Sample/interpolate to compare profile shape
            t_src = np.linspace(0.0, 1.0, len(src_rel_centers))
            t_cand = np.linspace(0.0, 1.0, len(cand_rel_centers))
            cand_resampled = np.interp(t_src, t_cand, cand_rel_centers)
            pattern_diff = float(np.mean(np.abs(np.array(src_rel_centers) - cand_resampled)))
            p_src_pattern = pattern_diff * _WEIGHT_SRC_PATTERN

        p_source = p_src_centroid + p_src_lines + p_src_font + p_src_bbox + p_src_pattern
        qa["src_lines_orig"] = float(source_profile.line_count)
        qa["src_font_orig"] = source_profile.font_size
        qa["p_src_pattern"] = p_src_pattern
    qa["p_source"] = p_source
    qa["source_sim"] = 1.0 / (1.0 + p_source)

    p_zone = 0.0
    if preferred_mask is not None and np.any(preferred_mask):
        glyph_mask = _glyph_mask_for_lines(lines, geom, font_size)
        glyph_count = int(np.count_nonzero(glyph_mask))
        if glyph_count:
            overflow = np.count_nonzero(glyph_mask & ~preferred_mask) / float(glyph_count)
            p_zone = overflow * overflow * _WEIGHT_ZONE_OVERFLOW
    qa["p_zone_overflow"] = p_zone

    penalty = p_font + p_centroid + p_balance + p_occ + p_composition + p_source + p_zone + p_vfill + p_aspect + p_silhouette
    qa["penalty"] = penalty
    qa["glyph_pixels"] = float(glyph_n)
    return penalty, qa


def _line_height(font_size: int, line_spacing: float) -> int:
    spacing = int(font_size * max(0.0, line_spacing))
    return int(math.ceil(font_size * 1.15)) + spacing


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


def _shared_bubble_groups(regions: List[Any]) -> List[BubbleLayoutGroup]:
    """Group regions that share the same prepared bubble interior into BubbleLayoutGroups."""
    groups: List[BubbleLayoutGroup] = []
    for region in regions:
        interior = getattr(region, "_bubble_interior", None)
        bubble_mask = getattr(region, "_bubble_mask", None)
        bubble_id = str(getattr(region, "bubble_id", "") or "")
        if bubble_id:
            shared = next((group for group in groups if group.bubble_id == bubble_id), None)
            if shared is not None:
                shared.regions.append(region)
                continue
        if interior is None or not np.any(interior):
            empty_mask = bubble_mask if bubble_mask is not None else np.zeros((0, 0), dtype=np.uint8)
            empty_interior = interior if interior is not None else np.zeros((0, 0), dtype=np.uint8)
            groups.append(BubbleLayoutGroup(
                bubble_mask=empty_mask, interior=empty_interior, regions=[region],
                bubble_id=bubble_id or None,
            ))
            continue
        for group in groups:
            other_interior = group.interior
            same_mask = (
                bubble_mask is not None
                and group.bubble_mask is not None
                and bubble_mask is group.bubble_mask
            )
            same_interior = (
                other_interior is not None
                and other_interior.shape == interior.shape
                and np.array_equal(other_interior, interior)
            )
            if same_mask or same_interior or (bubble_id and group.bubble_id == bubble_id):
                group.regions.append(region)
                break
        else:
            b_mask = bubble_mask if bubble_mask is not None else interior.copy()
            groups.append(BubbleLayoutGroup(
                bubble_mask=b_mask, interior=interior, regions=[region], bubble_id=bubble_id or None,
            ))
    return groups


def partition_bubble_zones(
    regions: List[Any],
    interior: np.ndarray,
    lobe_graph: Optional[LobeGraph] = None,
    apply_boundary_gap: bool = True,
) -> List[np.ndarray]:
    """Partition a shared speech bubble into geometry-aware source-owned placement zones.

    Uses source centroids and OCR bounding boxes as seeds, with distance transform propagation
    clipped strictly to the bubble interior, direction-aware cost scaling, optional neck penalties
    from LobeGraph, and slight erosion along mutual boundaries to preserve original whitespace rhythm.
    """
    if not regions:
        return []
    if len(regions) == 1:
        return [interior > 0]

    h, w = interior.shape[:2]
    ys, xs = np.nonzero(interior)
    if len(ys) == 0:
        return [np.zeros_like(interior, dtype=bool) for _ in regions]

    profiles = [build_original_layout_profile(reg, interior) for reg in regions]
    costs = []
    y_grid, x_grid = np.ogrid[:h, :w]

    for index, (region, profile) in enumerate(zip(regions, profiles)):
        source_mask = np.zeros_like(interior, dtype=np.uint8)
        lines = getattr(region, "lines", None)
        if lines is not None and len(lines):
            cv2.fillPoly(source_mask, [np.asarray(line, np.int32) for line in lines], 1)
        elif profile is not None:
            bx1, by1, bx2, by2 = profile.bbox
            source_mask[max(0, by1):min(h, by2), max(0, bx1):min(w, bx2)] = 1

        if not np.any(source_mask) and profile is not None:
            cx, cy = int(round(profile.centroid[0])), int(round(profile.centroid[1]))
            if 0 <= cy < h and 0 <= cx < w:
                source_mask[cy, cx] = 1

        # Base Euclidean distance from region source seeds
        dt = cv2.distanceTransform((source_mask == 0).astype(np.uint8), cv2.DIST_L2, 5)

        # Directional scaling: if regions are arranged vertically, vertical separation is primary
        if profile is not None:
            cx, cy = profile.centroid
            # Compute distance from centroid as soft guide
            cent_dist = np.sqrt((x_grid - cx) ** 2 + (y_grid - cy) ** 2)
            combined_d = 0.6 * dt + 0.4 * cent_dist
        else:
            combined_d = dt

        scale = max(
            1.0,
            float(profile.font_size) if profile is not None else 0.0,
            math.sqrt(float(np.count_nonzero(source_mask))),
        )
        cost = combined_d / scale

        # LobeGraph neck penalty: if a pixel crosses a narrow neck away from the seed lobe
        if lobe_graph is not None and getattr(lobe_graph, "necks", None):
            for neck in lobe_graph.necks:
                nx, ny = neck.get("center", (0, 0))
                n_ratio = neck.get("ratio", 1.0)
                if n_ratio < 0.65:
                    neck_d = np.sqrt((x_grid - nx) ** 2 + (y_grid - ny) ** 2)
                    neck_penalty = np.exp(-neck_d / 15.0) * (1.0 - n_ratio) * 20.0
                    cost = cost + neck_penalty

        costs.append(cost)

    owner = np.argmin(np.stack(costs, axis=0), axis=0)
    raw_zones = [(owner == i) & (interior > 0) for i in range(len(regions))]

    if not apply_boundary_gap:
        return raw_zones

    # Apply separation gap between neighboring zones derived from font size & original gap
    avg_font = np.mean([p.font_size for p in profiles if p is not None] or [12.0])
    gap_pixels = max(1, int(round(avg_font * 0.45)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gap_pixels * 2 + 1, gap_pixels * 2 + 1))

    eroded_zones = []
    for i, zone in enumerate(raw_zones):
        # Erode boundary with other zones
        other_union = np.zeros_like(interior, dtype=np.uint8)
        for j, other_zone in enumerate(raw_zones):
            if i != j:
                other_union |= other_zone.astype(np.uint8)

        dilated_others = cv2.dilate(other_union, kernel) > 0
        eroded = zone & (~dilated_others)
        # Ensure zone retains non-empty core if possible
        if not np.any(eroded) and np.any(zone):
            eroded = zone
        eroded_zones.append(eroded)

    return eroded_zones



def _rect_gap(first: Tuple[int, int, int, int], second: Tuple[int, int, int, int]) -> float:
    dx = max(first[0] - second[2], second[0] - first[2], 0)
    dy = max(first[1] - second[3], second[1] - first[3], 0)
    return math.hypot(dx, dy)


def _candidate_bbox(candidate: LayoutCandidate) -> Tuple[int, int, int, int]:
    return (
        min(line.x for line in candidate.lines),
        min(line.y for line in candidate.lines),
        max(line.x + line.width for line in candidate.lines),
        max(line.y + line.height for line in candidate.lines),
    )


def _candidate_data(candidate: LayoutCandidate, image_shape: Tuple[int, int]):
    crop_box, glyph_mask, _, _ = _candidate_cropped_visual_masks(candidate, 0, image_shape)
    if candidate.status.startswith("free_text") and np.any(glyph_mask):
        metrics = _mask_metrics(glyph_mask, (crop_box[0], crop_box[1]))
        return crop_box, glyph_mask, metrics["bbox"], metrics["centroid"]
    return crop_box, glyph_mask, _candidate_bbox(candidate), _ink_centroid(candidate.lines, candidate.font_size)



def _choose_joint_layout(
    plans: List[_RegionLayoutPlan],
    image_shape: Tuple[int, int],
) -> Optional[Tuple[LayoutCandidate, ...]]:
    if not plans or any(not plan.candidates for plan in plans):
        return None

    # ponytail: cap Cartesian search at 4096 combinations; callers use the
    # existing greedy collision-safe fallback for larger candidate spaces.
    if math.prod(len(plan.candidates) for plan in plans) > _MAX_JOINT_LAYOUT_COMBINATIONS:
        return None

    # Precompute candidate data once per unique candidate
    candidate_cache: Dict[int, Any] = {}
    for plan in plans:
        for cand in plan.candidates:
            cid = id(cand)
            if cid not in candidate_cache:
                candidate_cache[cid] = _candidate_data(cand, image_shape)

    best: Optional[Tuple[float, Tuple[LayoutCandidate, ...]]] = None
    for combination in itertools.product(*(plan.candidates for plan in plans)):
        # Check pairwise collision first using cached glyph masks.
        collision = False
        for i in range(len(combination)):
            box_i, res_i, _, _ = candidate_cache[id(combination[i])]
            for j in range(i + 1, len(combination)):
                box_j, res_j, _, _ = candidate_cache[id(combination[j])]
                if _cropped_masks_overlap(box_i, res_i, box_j, res_j):
                    collision = True
                    break
            if collision:
                break
        if collision:
            continue

        score = sum(candidate.penalty for candidate in combination)
        for first in range(len(combination)):
            first_cand = combination[first]
            _, _, first_bbox, first_centroid = candidate_cache[id(first_cand)]
            first_profile = plans[first].source_profile

            for second in range(first + 1, len(combination)):
                second_cand = combination[second]
                _, _, second_bbox, second_centroid = candidate_cache[id(second_cand)]
                second_profile = plans[second].source_profile

                if first_profile is None or second_profile is None:
                    continue

                source_dx = second_profile.centroid[0] - first_profile.centroid[0]
                source_dy = second_profile.centroid[1] - first_profile.centroid[1]
                rendered_dx = second_centroid[0] - first_centroid[0]
                rendered_dy = second_centroid[1] - first_centroid[1]
                font_scale = max(1.0, (first_cand.font_size + second_cand.font_size) / 2.0)

                # Strict spatial order preservation (B below A / B right of A)
                if source_dy * rendered_dy < 0:
                    score += 150.0
                if source_dx * rendered_dx < 0:
                    score += 80.0
                source_distance = math.hypot(source_dx, source_dy)
                rendered_distance = math.hypot(rendered_dx, rendered_dy)
                score += abs(rendered_distance - source_distance) / font_scale * 3.0

                source_gap = _rect_gap(first_profile.bbox, second_profile.bbox)
                rendered_gap = _rect_gap(first_bbox, second_bbox)
                score += abs(rendered_gap - source_gap) / font_scale * 3.5

        if best is None or score < best[0]:
            best = (score, combination)

    return best[1] if best is not None and math.isfinite(best[0]) else None


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
) -> Optional[_RegionLayoutPlan]:
    text = (
        region.get_translation_for_rendering()
        if hasattr(region, "get_translation_for_rendering")
        else (getattr(region, "translation", "") or getattr(region, "text", ""))
    )
    if not text.strip() or interior is None or not np.any(interior):
        return None

    render_cfg = config.render
    minimum = render_cfg.font_size_minimum
    if minimum == -1:
        minimum = round(sum(image_shape) / 200)
    minimum = max(1, minimum)

    source_font = getattr(region, "source_font_size", None)
    if source_font is None:
        source_font = int(getattr(region, "font_size", 0) or 0)
        region.source_font_size = source_font

    if render_cfg.font_size is not None and render_cfg.font_size > 0:
        calibrated_target = max(minimum, int(render_cfg.font_size))
    else:
        # Two-stage calibration: calculate feasible translated font target directly from bubble interior
        adaptive_target = _estimate_adaptive_font_size(interior, text, minimum)
        offset = getattr(render_cfg, "font_size_offset", 0) or 0
        calibrated_target = max(minimum, adaptive_target + offset)

    region.calibrated_font_size = calibrated_target
    target = calibrated_target

    fg, bg = fg_bg_compare(*region.get_font_colors())
    stroke_width = max(1, int(target * 0.07)) if bg is not None else 0

    # If an explicit placement zone is provided, use it as the region's BubbleGeometry
    active_mask = zone_geometry_mask if zone_geometry_mask is not None and np.any(zone_geometry_mask) else interior
    geom = BubbleGeometry(active_mask)
    source_profile = build_original_layout_profile(region, interior)
    zone_local = None
    if preferred_mask is not None:
        y1, y2 = geom.y_offset, geom.y_offset + geom.shape[0]
        x1, x2 = geom.x_offset, geom.x_offset + geom.shape[1]
        zone_local = preferred_mask[y1:y2, x1:x2]
    result = solve_layout(
        geom=geom,
        words=text.split(),
        font_size_max=target,
        font_size_min=minimum,
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
    candidates = result if isinstance(result, list) else ([result] if result is not None else [])
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
    box = render_positioned_lines(
        line_dicts, layout_rect, candidate.font_size, plan.fg, plan.bg,
        plan.line_spacing, plan.language, region.direction == "hr",
    )
    if box is None or not np.any(box[:, :, 3]):
        return False

    region.font_size = candidate.font_size
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
    return True


def create_placement_zones_visualization(
    img_rgb: np.ndarray,
    groups: List[BubbleLayoutGroup],
) -> np.ndarray:
    """Create a diagnostic debug visualization showing placement zones, centroids, and boundaries."""
    if img_rgb is None:
        return np.zeros((100, 100, 3), dtype=np.uint8)
    vis = img_rgb.copy()
    zone_colors = [
        (255, 120, 0),    # Blue-orange palette
        (0, 200, 100),
        (220, 50, 220),
        (255, 200, 0),
        (50, 180, 255),
        (180, 100, 255),
    ]

    for g_idx, group in enumerate(groups):
        interior = group.interior
        if interior is None or not np.any(interior):
            continue

        # Draw bubble safe interior boundary
        int_cnts, _ = cv2.findContours(interior.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(vis, int_cnts, -1, (180, 180, 180), 1)

        # Draw each placement zone
        for z_idx, (region, zone) in enumerate(zip(group.regions, group.zones)):
            color = zone_colors[z_idx % len(zone_colors)]
            if zone is not None and np.any(zone):
                # Tint zone area
                tint = np.zeros_like(vis)
                tint[zone > 0] = color
                cv2.addWeighted(tint, 0.25, vis, 1.0, 0, vis)

                # Zone contour
                z_cnts, _ = cv2.findContours((zone > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(vis, z_cnts, -1, color, 2)

            # Draw original source polygon & centroid
            lines = getattr(region, "lines", None)
            if lines is not None and len(lines):
                cv2.polylines(vis, [np.asarray(line, np.int32) for line in lines], True, (0, 0, 255), 1)

            profile = build_original_layout_profile(region, interior)
            if profile is not None:
                cx, cy = int(round(profile.centroid[0])), int(round(profile.centroid[1]))
                cv2.circle(vis, (cx, cy), 4, (0, 0, 255), -1)
                label = f"Z{z_idx+1}"
                cv2.putText(vis, label, (cx + 6, cy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2)

            # Draw target capacity center (+) for zone
            target_mask = zone if (zone is not None and np.any(zone)) else interior
            if target_mask is not None and np.any(target_mask):
                target_geom = compute_placement_target(
                    BubbleGeometry(target_mask),
                    int(getattr(region, "font_size", 12) or 12),
                    source_profile=profile,
                    is_single_region=(len(group.regions) == 1),
                )
                tcx = int(round(target_geom.center_x + target_geom.bbox[0] * 0))
                # Add geom offset if BubbleGeometry was cropped
                bg = BubbleGeometry(target_mask)
                tcx = int(round(target_geom.center_x + bg.x_offset))
                tcy = int(round(target_geom.center_y + bg.y_offset))
                # Draw cross (+) in magenta
                cv2.drawMarker(vis, (tcx, tcy), (255, 0, 255), cv2.MARKER_CROSS, 8, 1, cv2.LINE_AA)

            # Draw placed lines, band slots, centers, and transition connectors if solver ran
            placed_lines = None
            if hasattr(region, "layout_segments") and region.layout_segments:
                seg_lines = region.layout_segments[0].get("lines", [])
                if seg_lines:
                    placed_lines = seg_lines

            if placed_lines:
                prev_cx, prev_cy = None, None
                all_lx = [int(pl["x"]) for pl in placed_lines]
                all_ly = [int(pl["y"]) for pl in placed_lines]
                all_rx = [int(pl["x"]) + int(pl["width"]) for pl in placed_lines]
                all_by = [int(pl["y"]) + int(pl["height"]) for pl in placed_lines]
                bx1, by1, bx2, by2 = min(all_lx), min(all_ly), max(all_rx), max(all_by)

                # Draw block bounding box
                cv2.rectangle(vis, (bx1, by1), (bx2, by2), (0, 255, 255), 1)
                # Draw block center (x)
                bcx = (bx1 + bx2) // 2
                bcy = (by1 + by2) // 2
                cv2.drawMarker(vis, (bcx, bcy), (0, 255, 255), cv2.MARKER_TILTED_CROSS, 7, 1, cv2.LINE_AA)

                for l_idx, pl in enumerate(placed_lines):
                    lx, ly, lw, lh = int(pl["x"]), int(pl["y"]), int(pl["width"]), int(pl["height"])
                    # Slot bounding box
                    cv2.rectangle(vis, (lx, ly), (lx + lw, ly + lh), color, 1)
                    # Line center dot
                    cx_i = lx + lw // 2
                    cy_i = ly + lh // 2
                    cv2.circle(vis, (cx_i, cy_i), 3, (0, 255, 0), -1)

                    # Transition connector from previous line
                    if prev_cx is not None and prev_cy is not None:
                        # Draw connector line from previous center to current center
                        cv2.line(vis, (prev_cx, prev_cy), (cx_i, cy_i), (0, 255, 255), 1, cv2.LINE_AA)
                    prev_cx, prev_cy = cx_i, cy_i

        # Draw lobe graph features if present
        if group.lobe_graph is not None:
            for neck in group.lobe_graph.necks:
                nx, ny = neck["center"]
                cv2.circle(vis, (nx, ny), 3, (255, 255, 0), -1)

    return vis


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
    with _RENDER_LOCK:
        layout_timing = {
            "mask_prep_ms": 0.0,
            "mode_classification_ms": 0.0,
            "bubble_solver_ms": 0.0,
            "free_text_solver_ms": 0.0,
            "fallback_ms": 0.0,
        }
        if timing is not None:
            timing.update(layout_timing)

        active_font = font_path or getattr(config.render, "font_path", None) or get_default_eng_font()
        text_render.set_font(active_font)

        regions = ctx.text_regions or []
        img = getattr(ctx, "img_rgb", None)
        if img is None:
            return
        _ensure_region_identities(regions)
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
            interior = decode_safe_shape(safe_shape, img.shape[0], img.shape[1])
            if interior is not None and np.any(interior):
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
                prepared = prepare_bubbles(
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
        phase_start = perf_counter()
        bubble_padding = int(getattr(getattr(config, "bubble_detection", None), "padding", 9))
        if page_geometry is None or not page_geometry.matches(img, regions, bubble_padding):
            page_geometry, _ = prepare_page_geometry(
                img, regions, bubble_padding, return_cleanup=False,
            )
        ctx.page_geometry = page_geometry
        layout_timing["mask_prep_ms"] = (perf_counter() - phase_start) * 1000.0
        phase_start = perf_counter()
        classify_placement_modes(regions)
        layout_timing["mode_classification_ms"] = (perf_counter() - phase_start) * 1000.0

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
        bubble_groups = _shared_bubble_groups(bubble_regions)

        if legacy_only:
            unplaced_regions.extend(regions)

        phase_start = perf_counter()
        for group in bubble_groups if not legacy_only else []:
            active = [
                region for region in group.regions
                if not legacy_only
                and getattr(region, "_bubble_interior", None) is not None
                and np.any(getattr(region, "_bubble_interior", None))
                and (
                    region.get_translation_for_rendering()
                    if hasattr(region, "get_translation_for_rendering")
                    else (getattr(region, "translation", "") or getattr(region, "text", ""))
                ).strip()
            ]
            if not active:
                unplaced_regions.extend(group.regions)
                continue

            interior = getattr(active[0], "_bubble_interior", None)
            lobe_graph = build_lobe_graph(group.bubble_mask) if np.any(group.bubble_mask) else None
            group.lobe_graph = lobe_graph

            # Partition the bubble into geometry-aware placement zones
            zones = partition_bubble_zones(active, interior, lobe_graph=lobe_graph, apply_boundary_gap=(len(active) > 1))
            group.zones = zones

            plans = []
            for index, region in enumerate(active):
                zone_mask = zones[index] if index < len(zones) else interior
                plan = _build_region_layout_plan(
                    region=region,
                    interior=interior,
                    config=config,
                    image_shape=img.shape[:2],
                    solver_margin=solver_margin,
                    solver_max_y_trials=solver_max_y_trials,
                    preferred_mask=zone_mask if len(active) > 1 else None,
                    top_k=_JOINT_CANDIDATE_COUNT if len(active) > 1 else 1,
                    zone_geometry_mask=zone_mask if len(active) > 1 else None,
                )
                if plan is not None:
                    plans.append(plan)

            chosen = _choose_joint_layout(plans, img.shape[:2]) if len(active) > 1 else None
            if len(active) == 1 and plans and plans[0].candidates:
                chosen = (plans[0].candidates[0],)

            if chosen is not None and len(chosen) == len(plans) == len(active):
                for plan, candidate in zip(plans, chosen):
                    if not _apply_layout_candidate(plan, candidate, img.shape[:2]):
                        unplaced_regions.append(plan.region)
                active_ids = {id(region) for region in active}
                unplaced_regions.extend(region for region in group.regions if id(region) not in active_ids)
                continue

            active_ids = {id(region) for region in active}
            for region in group.regions:
                if id(region) in active_ids:
                    region._solver_status = "requires_compression"
                    unplaced_regions.append(region)
        layout_timing["bubble_solver_ms"] = (perf_counter() - phase_start) * 1000.0
        if not legacy_only and free_regions:
            phase_start = perf_counter()
            bubble_halo = max(2, int(round((render_cfg.font_size or 12) * 0.20)))
            obstacles = build_page_obstacle_map(regions, img.shape[:2], bubble_halo=bubble_halo)
            inpaint_mask = getattr(ctx, "inpaint_mask", None)
            if inpaint_mask is None:
                inpaint_mask = getattr(ctx, "text_mask", None)
            if inpaint_mask is None:
                inpaint_mask = getattr(ctx, "mask_raw", None)
            if inpaint_mask is None:
                inpaint_mask = getattr(ctx, "mask", None)
            free_zones = build_free_text_ownership_zones(free_regions, obstacles, inpaint_mask=inpaint_mask)
            free_plans: Dict[int, List[LayoutCandidate]] = {}
            free_profiles: Dict[int, OriginalLayoutProfile] = {}
            for region in free_regions:
                ft_zone = free_zones.get(id(region))
                if ft_zone is None:
                    continue
                result = _solve_free_text_region(
                    region=region,
                    zone=ft_zone,
                    obstacles=obstacles,
                    config=config,
                    image_shape=img.shape[:2],
                    solver_margin=solver_margin,
                    solver_max_y_trials=solver_max_y_trials,
                )
                if result is None:
                    region._solver_path = "free_text"
                    region._solver_status = "no_valid_layout"
                    region._solver_qa = {
                        "placement_mode": PlacementMode.FREE_TEXT.value,
                        "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text"],
                    }
                    region._render_suppressed = True
                    continue
                candidate, profile, _qa = result
                free_plans[id(region)] = getattr(region, "_free_text_candidate_pool", [candidate])
                free_profiles[id(region)] = profile

            chosen_free = _select_free_text_joint_candidates(
                free_regions, free_plans, img.shape[:2], free_profiles=free_profiles
            )
            for region in free_regions:
                candidate = chosen_free.get(id(region))
                profile = free_profiles.get(id(region))
                if candidate is None or profile is None:
                    if id(region) in free_plans:
                        region._solver_path = "free_text"
                        region._solver_status = "no_joint_layout"
                        region._render_suppressed = True
                    continue
                if not _apply_free_text_candidate(
                    region, candidate, profile, config, img.shape[:2], layout_debug=layout_debug
                ):
                    region._solver_path = "free_text"
                    region._solver_status = "rasterization_failed"
                    region._render_suppressed = True

            for region in free_regions:
                logger.info(
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
            layout_timing["free_text_solver_ms"] = (perf_counter() - phase_start) * 1000.0

        # Store layout groups on ctx for diagnostic overlay generation
        ctx._bubble_layout_groups = bubble_groups

        # If any regions were not placed by the shape-aware solver, run fallback
        group_regions = bool(
            getattr(getattr(config, "bubble_detection", None), "group_regions", False)
        )
        if unplaced_regions and not legacy_only:
            phase_start = perf_counter()
            prepared = prepare_bubbles(
                image=img,
                regions=unplaced_regions,
                font_path=active_font,
                render_config=render_cfg,
                group=group_regions,
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
            layout_timing["fallback_ms"] += (perf_counter() - phase_start) * 1000.0
        elif unplaced_regions and legacy_only:
            phase_start = perf_counter()
            prepared = prepare_bubbles(
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
            layout_timing["fallback_ms"] += (perf_counter() - phase_start) * 1000.0

        ctx._bubble_detection_done = True
        ctx._bubble_layout_ready = True
        _record_content_trace(regions, "layout")
        if timing is not None:
            timing.update(layout_timing)
