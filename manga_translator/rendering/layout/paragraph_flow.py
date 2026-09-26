"""Paragraph-gap classification and vertical rhythm compaction."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .geometry import BubbleGeometry
from .models import BandSlot, LobeGraph, OriginalLayoutProfile, PlacedLine

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
    feasible_y_cache = {}

    def _candidate_ys(low: int, high: int, width: int, reverse: bool = False):
        if row_slot_table is None:
            return range(high, low - 1, -1) if reverse else range(low, high + 1)
        feasible = feasible_y_cache.get(width)
        if feasible is None:
            feasible = [
                y for y, slots in row_slot_table.items()
                if any(slot.width >= width for slot in slots)
            ]
            feasible.sort()
            feasible_y_cache[width] = feasible
        start = bisect_left(feasible, low)
        stop = bisect_right(feasible, high)
        selected = feasible[start:stop]
        return reversed(selected) if reverse else selected

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
            for cand_y in _candidate_ys(expected_y, cur_y, cur_lines[i].width):
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
            for cand_y in _candidate_ys(cur_y, expected_y, cur_lines[i].width, reverse=True):
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

            for cand_y in _candidate_ys(search_min, search_max, cur_lines[i].width):
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


