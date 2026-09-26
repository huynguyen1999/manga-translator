"""Shape-aware candidate search for bubble and free-text regions."""

from __future__ import annotations

import math
from time import perf_counter
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

from .geometry import BubbleGeometry
from .models import BandSlot, LayoutCandidate, PlacedLine
from .scoring import _WEIGHT_SRC_LINES, _composite_penalty, _font_penalty
from .validation import _bbox_validate, _validate_glyph_pixels
from .line_breaking import (
    _cached_placement_target,
    _cached_row_slot_table,
    _cached_zone_shape_profile,
    _precompute_widths,
    _try_placement_rows,
    _y_origin_sequence,
)
from .paragraph_flow import _classify_adjacent_gaps, _compact_vertical_rhythm
from .centering import _center_layout_block, _optimize_x
from .profiling import get_solver_profile
from .text_normalization import normalize_words


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
    forced_break_after: Optional[int] = None,
) -> Union[Optional[LayoutCandidate], List[LayoutCandidate]]:
    """Generate and rank layout candidates; return one or the best ``top_k``.

    The mask determines *where text may exist* (hard validation); language,
    typography, and the original page's layout profile shape the soft score.
    ``hyphenate`` is accepted for API compatibility — normalized words are
    treated as atomic unless an explicit rescue break is supplied.
    """
    if not words:
        return None

    # Phase 2: repair OCR hyphen splits so ordinary words are atomic.
    norm_words = list(words) if forced_break_after is not None else normalize_words(words)
    if forced_break_after is not None and not 0 <= forced_break_after < len(norm_words) - 1:
        return None

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
        row_slot_table = _cached_row_slot_table(
            geom, S, stroke_width, margin, min_width=min_slot_w
        )
        prof.row_slot_table_ms += (perf_counter() - t_rst0) * 1000.0
        if not row_slot_table:
            continue

        t_pt0 = perf_counter()
        target_geom = _cached_placement_target(
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
            zone_profile = _cached_zone_shape_profile(
                geom, S, stroke_width, margin,
                preferred_mask=preferred_mask,
                line_h=line_h,
                words=norm_words,
                placement_target=target_geom,
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
                    forced_break_after=forced_break_after,
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
                                forced_break_after=forced_break_after,
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


def _line_height(font_size: int, line_spacing: float) -> int:
    spacing = int(font_size * max(0.0, line_spacing))
    return int(math.ceil(font_size * 1.15)) + spacing
