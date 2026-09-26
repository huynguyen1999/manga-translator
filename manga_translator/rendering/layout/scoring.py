"""Bubble candidate scoring and glyph geometry."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from .geometry import BubbleGeometry
from .joint_layout import _ink_centroid
from .line_breaking import _word_core
from .models import LayoutCandidate, OriginalLayoutProfile, PlacementTarget, PlacedLine, ZoneShapeProfile
from .profiling import get_solver_profile
from .raster import _render_line_alpha


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

def _font_penalty(font_size: int, font_target: int) -> float:
    if font_target <= 0:
        return 0.0
    return ((font_size - font_target) / float(font_target)) ** 2 * _WEIGHT_FONT


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
    profile = get_solver_profile()
    edge_counts = profile.safe_zone_edge_counts.get(id(safe_zone))
    if edge_counts is None or edge_counts[0] is not safe_zone:
        row_counts = np.count_nonzero(safe_zone, axis=1)
        col_counts = np.count_nonzero(safe_zone, axis=0)
        row_prefix = np.empty(row_counts.size + 1, dtype=np.int64)
        col_prefix = np.empty(col_counts.size + 1, dtype=np.int64)
        row_prefix[0] = col_prefix[0] = 0
        np.cumsum(row_counts, out=row_prefix[1:])
        np.cumsum(col_counts, out=col_prefix[1:])
        edge_counts = (safe_zone, row_prefix, col_prefix)
        profile.safe_zone_edge_counts[id(safe_zone)] = edge_counts
    _, row_prefix, col_prefix = edge_counts
    zone_area = float(row_prefix[-1]) if row_prefix[-1] else mask_area

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

        a_above = float(row_prefix[top_clamped])
        a_below = float(row_prefix[-1] - row_prefix[bottom_clamped])
        a_left = float(col_prefix[left_clamped])
        a_right = float(col_prefix[-1] - col_prefix[right_clamped])

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


