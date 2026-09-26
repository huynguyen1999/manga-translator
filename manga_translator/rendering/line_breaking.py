"""Line fitting and glyph rendering for prepared bubble layouts."""
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def analyze_semantic_breakpoints(text: str) -> List[float]:
    """Calculate semantic breakpoint penalty costs at every word boundary (1 <= k < num_words)."""
    words = text.split()
    if len(words) <= 1:
        return []
    costs = []
    conjunctions = {
        'and', 'or', 'but', 'nor', 'so', 'for', 'yet', 'if', 'because', 'although',
        'though', 'while', 'when', 'that', 'which', 'who', 'whom', 'whose', 'where',
        'from', 'with', 'by', 'at', 'in', 'on', 'to', 'as', 'since', 'until', 'unless',
    }
    for k in range(1, len(words)):
        w_prev = words[k - 1]
        w_next = words[k].lower().strip('“"\'')
        if w_prev.endswith(('.', '!', '?', '~', '…', '♡', '♥', '”', '’', '"', '⁉', '‼')):
            cost = 0.0
        elif w_prev.endswith((',', ';', ':', '—', '–', '-')):
            cost = 5.0
        elif w_next in conjunctions:
            cost = 15.0
        else:
            cost = 30.0
        costs.append(cost)
    return costs


def build_line_slots(safe_mask: np.ndarray, y_start: int, font_size: int, line_spacing: float, y_end: int, lobe_center_x: float, rect: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Generate variable-width horizontal line slots across the safe mask."""
    h, w = safe_mask.shape[:2]
    spacing = int(font_size * (line_spacing or 0.01))
    line_step = int(np.ceil(font_size * 1.15)) + spacing
    slots = []
    
    rx1, ry1, rx2, ry2 = rect if rect is not None else (0, 0, w, h)
    cur_y = max(int(y_start), ry1)
    limit_y = min(y_end + 1, h, ry2)

    while cur_y + font_size <= limit_y:
        y_bot = cur_y + font_size
        slice_mask = safe_mask[cur_y:y_bot, :]
        valid_cols = np.all(slice_mask > 0, axis=0)

        cx = int(round(lobe_center_x))
        cx = max(rx1, min(rx2 - 1, cx))

        if not valid_cols[cx]:
            true_cols = np.where(valid_cols)[0]
            true_cols = [c for c in true_cols if rx1 <= c < rx2]
            if not len(true_cols):
                cur_y += line_step
                continue
            cx = int(true_cols[np.argmin(np.abs(np.array(true_cols) - cx))])

        left = cx
        while left > rx1 and valid_cols[left - 1]:
            left -= 1
        right = cx
        while right + 1 < rx2 and valid_cols[right + 1]:
            right += 1

        # FreeType strokes can extend slightly left/right of advance metrics.
        left += 2
        right -= 2
        slot_w = right + 1 - left
        if slot_w >= font_size * 1.5:
            slots.append({
                "y": cur_y,
                "x_min": left,
                "x_max": right + 1,
                "width": slot_w,
                "center": (left + right + 1) / 2.0,
            })
        cur_y += line_step

    return slots


def dp_break_lines_for_lobe(words: List[str], slots: List[Dict[str, Any]], lobe_center_x: float, font_size: int, language: str = 'en_US', hyphenate: bool = True, line_spacing: float = 0.0):
    """Dynamic programming variable-width line breaker with contour clamping and wobble dampening."""
    from . import text_render

    num_words = len(words)
    num_slots = len(slots)
    if not num_words or not num_slots:
        return None

    memo = {}

    def dp_solve(w_idx: int, s_idx: int):
        if w_idx == num_words:
            return 0.0, []
        if s_idx == num_slots:
            return float('inf'), []

        state = (w_idx, s_idx)
        if state in memo:
            return memo[state]

        slot = slots[s_idx]
        slot_w = slot["width"]
        best_cost = float('inf')
        best_lines = []

        for next_w in range(w_idx + 1, num_words + 1):
            cand_text = " ".join(words[w_idx:next_w])
            text_w = text_render.get_string_width(font_size, cand_text)

            if text_w > slot_w:
                break

            ideal_x = lobe_center_x - text_w / 2.0
            actual_x = max(slot["x_min"], min(slot["x_max"] - text_w, ideal_x))
            actual_center = actual_x + text_w / 2.0
            wobble = abs(actual_center - lobe_center_x)

            fill_ratio = text_w / max(1.0, slot_w)
            raggedness_penalty = 10.0 * ((1.0 - fill_ratio) ** 2)

            orphan_penalty = 0.0
            if (next_w - w_idx == 1) and len(cand_text) <= 3 and (next_w < num_words):
                orphan_penalty = 25.0

            line_cost = wobble * 0.4 + raggedness_penalty + orphan_penalty

            rem_cost, rem_lines = dp_solve(next_w, s_idx + 1)
            total_cost = line_cost + rem_cost

            if total_cost < best_cost:
                best_cost = total_cost
                best_lines = [{
                    "text": cand_text,
                    "x": int(round(actual_x)),
                    "y": slot["y"],
                    "width": int(text_w),
                    "height": font_size,
                }] + rem_lines

        memo[state] = (best_cost, best_lines)
        return memo[state]

    cost, lines = dp_solve(0, 0)
    if cost == float('inf') or not lines or len(lines) == 0:
        return None
    placed_words = sum(len(line["text"].split()) for line in lines)
    if placed_words != num_words:
        return None
    return lines


def optimize_lobe_center_x(lines: List[Dict[str, Any]], p_mask: np.ndarray, font_size: int, initial_cx: float, rect: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    """Optimize horizontal block center by evaluating left/right whitespace balance across all line spans."""
    if not lines:
        return initial_cx, lines

    h, w = p_mask.shape[:2] if p_mask is not None else (1000, 1000)
    rx1, ry1, rx2, ry2 = rect
    spans = []

    for line in lines:
        y_top = max(0, int(line["y"]))
        y_bot = min(h, int(line["y"] + font_size))
        line_w = int(line["width"])
        if y_bot <= y_top:
            continue

        if p_mask is not None and np.any(p_mask[y_top:y_bot, :]):
            slice_mask = p_mask[y_top:y_bot, :]
            valid_cols = np.all(slice_mask > 0, axis=0)

            cx_seed = int(round(initial_cx))
            cx_seed = max(rx1, min(rx2 - 1, cx_seed))
            if not valid_cols[cx_seed]:
                true_cols = np.where(valid_cols)[0]
                true_cols = [c for c in true_cols if rx1 <= c < rx2]
                if len(true_cols):
                    cx_seed = int(true_cols[np.argmin(np.abs(np.array(true_cols) - cx_seed))])
                else:
                    spans.append((rx1, rx2, (rx1 + rx2) / 2.0, line_w))
                    continue

            left = cx_seed
            while left > rx1 and valid_cols[left - 1]:
                left -= 1
            right = cx_seed
            while right + 1 < rx2 and valid_cols[right + 1]:
                right += 1

            spans.append((left, right + 1, (left + right + 1) / 2.0, line_w))
        else:
            spans.append((rx1, rx2, (rx1 + rx2) / 2.0, line_w))

    if not spans:
        return initial_cx, lines

    estimated_center = float(np.median([s[2] for s in spans]))
    search_min = max(rx1, int(round(estimated_center - 30)))
    search_max = min(rx2, int(round(estimated_center + 30)))

    best_cx = estimated_center
    best_penalty = float('inf')

    for cand_cx in range(search_min, search_max + 1):
        penalty = 0.0
        overflow = False
        min_clearance = float('inf')

        for L, R, span_center, line_w in spans:
            text_L = cand_cx - line_w / 2.0
            text_R = cand_cx + line_w / 2.0
            left_gap = text_L - L
            right_gap = R - text_R

            if left_gap < 0 or right_gap < 0:
                overflow = True
                break

            clearance = min(left_gap, right_gap)
            if clearance < min_clearance:
                min_clearance = clearance

            # Symmetry: balance left gap vs right gap
            gap_diff = abs(left_gap - right_gap)
            penalty += gap_diff * 1.5
            # Small penalty for drift from available span center
            penalty += abs(cand_cx - span_center) * 0.4

        if overflow:
            continue

        # Reward higher minimum clearance from boundary
        penalty -= min_clearance * 2.0

        if penalty < best_penalty:
            best_penalty = penalty
            best_cx = float(cand_cx)

    # Position each line using best_cx
    positioned_lines = []
    for line in lines:
        line_copy = dict(line)
        line_w = line["width"]
        line_copy["x"] = int(round(best_cx - line_w / 2.0))
        positioned_lines.append(line_copy)

    return best_cx, positioned_lines


def render_positioned_lines(lines: List[Dict[str, Any]], rect: List[int], font_size: int, fg: Tuple[int, int, int], bg: Optional[Tuple[int, int, int]], line_spacing: float, language: str, reversed_direction: bool = False, stroke_width: int = None):
    """Render optical-centered text lines onto an unwarped RGBA box matching rect dimensions."""
    from . import text_render

    if not lines:
        return None

    rx1, ry1, rx2, ry2 = rect
    box_w = max(1, rx2 - rx1)
    box_h = max(1, ry2 - ry1)

    bg_size = (max(int(stroke_width), 1) if stroke_width is not None else int(max(font_size * 0.07, 1))) if bg is not None else 0

    canvas_text = np.zeros((box_h, box_w), dtype=np.uint8)
    canvas_border = canvas_text.copy()

    for line in lines:
        line_x_rel = line["x"] - rx1
        line_y_rel = line["y"] - ry1 + font_size
        pen = [line_x_rel, line_y_rel]

        for c in line["text"]:
            offset_x = text_render.put_char_horizontal(font_size, c, pen, canvas_text, canvas_border, bg_size, stroke_width)
            pen[0] += offset_x

    canvas_border = np.clip(canvas_border, 0, 255)
    line_box = text_render.add_color(canvas_text, fg, canvas_border, bg)
    return line_box
