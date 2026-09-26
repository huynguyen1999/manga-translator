"""Bubble row geometry and dynamic-programming text line breaking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from .geometry import (
    BubbleGeometry,
    _runs_from_row,
    compute_placement_target,
    compute_zone_shape_profile,
)
from .models import (
    BandSlot,
    OriginalLayoutProfile,
    PlacementTarget,
    PlacedLine,
    ZoneShapeProfile,
)
from .profiling import get_solver_profile
from .hard_line_breaks import HARD_LINE_BREAK, next_break_index, precompute_widths as _precompute_widths, skip_forced_break

_WEIGHT_TRANS_XJUMP = 8.0       # quadratic penalty for normalized center jump
_WEIGHT_TRANS_OVERLAP = 12.0    # penalty for poor horizontal slot overlap
_WEIGHT_TRANS_BRANCH = 25.0     # penalty for zero overlap / branch jump
_WEIGHT_VERTICAL_GAP = 24.0           # nonlinear paragraph spring penalty


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
    first_y = max(0, y1)
    stop_y = min(h_mask - font_size + 1, y2 - font_size + 1)
    if stop_y <= first_y:
        return table

    # A row is safe only when its font-height window contains no unsafe pixels.
    # Prefix counts preserve the original np.all rule while avoiding a full
    # font-height scan for every possible y coordinate.
    unsafe = ~safe[first_y : stop_y + font_size]
    unsafe_prefix = np.empty((unsafe.shape[0] + 1, w_mask), dtype=np.uint32)
    unsafe_prefix[0] = 0
    np.cumsum(unsafe, axis=0, dtype=np.uint32, out=unsafe_prefix[1:])
    row_count = stop_y - first_y
    valid_rows = unsafe_prefix[font_size : font_size + row_count] == unsafe_prefix[:row_count]
    for y, band_row in enumerate(valid_rows, start=first_y):
        intervals = _runs_from_row(band_row)
        slots = [
            BandSlot(left=iv.left, right=iv.right, y_start=y, y_end=y + font_size)
            for iv in intervals
            if iv.right - iv.left >= min_width
        ]
        slots.sort(key=lambda s: s.left)
        table[y] = slots
    return table


def _cached_row_slot_table(
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int,
    margin: float,
    min_width: int,
) -> Dict[int, List[BandSlot]]:
    profile = get_solver_profile()
    key = (geom, font_size, stroke_width, margin, min_width)
    cached = profile.row_slot_tables.get(key)
    if cached is not None:
        profile.bubble_row_slot_table_cache_hits += 1
        return cached
    profile.bubble_row_slot_table_builds += 1
    table = _build_row_slot_table(geom, font_size, stroke_width, margin, min_width)
    profile.row_slot_tables[key] = table
    return table


def _max_usable_row_width(
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int,
    margin: float,
    min_width: int,
) -> int:
    profile = get_solver_profile()
    key = (geom, font_size, stroke_width, margin, min_width)
    if key in profile.row_slot_max_widths:
        return profile.row_slot_max_widths[key]
    table = profile.row_slot_tables.get(key)
    if table is not None:
        profile.bubble_row_slot_table_cache_hits += 1
        result = max((slot.width for row in table.values() for slot in row), default=0)
    else:
        _, y1, _, y2 = geom.safe_bounding_box(font_size, stroke_width, margin)
        safe = geom.safe_pixels(font_size, stroke_width, margin)
        result = 0
        for y in range(max(0, y1), min(safe.shape[0] - font_size + 1, y2 - font_size + 1)):
            result = max(
                result,
                max((slot.width for slot in _runs_from_row(np.all(safe[y:y + font_size], axis=0)) if slot.width >= min_width), default=0),
            )
    profile.row_slot_max_widths[key] = result
    return result


def _cached_placement_target(
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int,
    margin: float,
    source_profile: Optional[OriginalLayoutProfile],
    preferred_mask: Optional[np.ndarray],
    is_single_region: bool,
) -> PlacementTarget:
    profile = get_solver_profile()
    key = (geom, font_size, stroke_width, margin, id(source_profile), id(preferred_mask), is_single_region)
    cached = profile.placement_targets.get(key)
    if cached is None:
        cached = compute_placement_target(
            geom, font_size, stroke_width, margin,
            source_profile=source_profile,
            preferred_mask=preferred_mask,
            is_single_region=is_single_region,
        )
        profile.placement_targets[key] = cached
    return cached


def _cached_zone_shape_profile(
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int,
    margin: float,
    preferred_mask: Optional[np.ndarray],
    line_h: int,
    words: List[str],
    placement_target: PlacementTarget,
) -> ZoneShapeProfile:
    profile = get_solver_profile()
    key = (geom, font_size, stroke_width, margin, id(preferred_mask), line_h, tuple(words), id(placement_target))
    cached = profile.zone_shape_profiles.get(key)
    if cached is None:
        cached = compute_zone_shape_profile(
            geom, font_size, stroke_width, margin,
            preferred_mask=preferred_mask,
            line_h=line_h,
            words=words,
            placement_target=placement_target,
        )
        profile.zone_shape_profiles[key] = cached
    return cached


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
    forced_break_after: Optional[int] = None,
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
        forced_break_after=forced_break_after,
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
    is_final = end >= len(words) or (end < len(words) and words[end] == HARD_LINE_BREAK)

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
    forced_break_after: Optional[int] = None,
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
    word_width_prefix = [0]
    for width in word_widths:
        word_width_prefix.append(word_width_prefix[-1] + width)

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

        if skip_forced_break(
            words, wi, dp, memo, key,
            (ri, prev_slot_left, prev_slot_right, prev_center_x, prev_line_y, text_state),
        ):
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
        break_index = next_break_index(words, wi, nw)
        for slot in row.intervals:
            slot_w = slot.width
            for end in range(wi + 1, break_index + 1):
                if forced_break_after is not None and wi <= forced_break_after < end - 1:
                    break
                run_w = word_width_prefix[end] - word_width_prefix[wi] + space_w * (end - wi - 1)
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

                next_wi = end + 1 if end == break_index and break_index < nw else end
                rem_dict = dp(
                    next_wi,
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
    expected_words = sum(word != HARD_LINE_BREAK for word in words)
    for line_cnt in sorted(root_dict.keys()):
        for cost, lines in root_dict[line_cnt]:
            placed_words = sum(len(line.text.split()) for line in lines)
            if placed_words == expected_words:
                candidates.append(lines)

    return candidates
