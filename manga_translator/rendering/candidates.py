"""Candidate scoring and text fitting across bubble lobes."""
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

from .line_breaking import (
    build_line_slots,
    dp_break_lines_for_lobe,
    optimize_lobe_center_x,
    render_positioned_lines,
)


def calculate_mask_moments(mask: np.ndarray) -> Tuple[Tuple[float, float], np.ndarray]:
    """Calculate centroid (cx, cy) and central 2x2 covariance/inertia tensor of a mask."""
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return (0.0, 0.0), np.eye(2, dtype=np.float32)
    cx = float(xs.mean())
    cy = float(ys.mean())
    dx = xs.astype(np.float32) - cx
    dy = ys.astype(np.float32) - cy
    n = float(len(ys))
    cov = np.array([
        [float(np.sum(dx * dx)) / n, float(np.sum(dx * dy)) / n],
        [float(np.sum(dx * dy)) / n, float(np.sum(dy * dy)) / n],
    ], dtype=np.float32)
    return (cx, cy), cov


def calculate_text_moments(lines: List[Dict[str, Any]], font_size: int) -> Tuple[Tuple[float, float], np.ndarray]:
    """Calculate centroid and 2x2 covariance/inertia tensor of rendered text lines."""
    if not lines:
        return (0.0, 0.0), np.eye(2, dtype=np.float32)
    weights = []
    centers_x = []
    centers_y = []
    for line in lines:
        w = max(1.0, float(line["width"]))
        weight = w * font_size
        cx = float(line["x"]) + w / 2.0
        cy = float(line["y"]) + font_size / 2.0
        weights.append(weight)
        centers_x.append(cx)
        centers_y.append(cy)
    weights = np.array(weights, dtype=np.float32)
    total_w = float(weights.sum())
    if total_w <= 0:
        return (0.0, 0.0), np.eye(2, dtype=np.float32)
    mean_x = float(np.sum(weights * np.array(centers_x)) / total_w)
    mean_y = float(np.sum(weights * np.array(centers_y)) / total_w)
    dx = np.array(centers_x) - mean_x
    dy = np.array(centers_y) - mean_y
    cov_xx = float(np.sum(weights * (dx * dx + (np.array([l["width"] for l in lines], dtype=np.float32) ** 2) / 12.0)) / total_w)
    cov_yy = float(np.sum(weights * (dy * dy + (font_size ** 2) / 12.0)) / total_w)
    cov_xy = float(np.sum(weights * (dx * dy)) / total_w)
    return (mean_x, mean_y), np.array([[cov_xx, cov_xy], [cov_xy, cov_yy]], dtype=np.float32)


def check_sdf_clearance(dist: np.ndarray, lines: List[Dict[str, Any]], font_size: int, margin: float = 2.0) -> Tuple[bool, float]:
    """Validate that lines remain within safe boundary and compute clearance penalty from distance transform."""
    h, w = dist.shape[:2]
    total_penalty = 0.0
    for line in lines:
        lx = int(line["x"])
        ly = int(line["y"])
        lw = int(line["width"])
        lh = int(font_size)
        if lx < 0 or ly < 0 or lx + lw > w or ly + lh > h:
            return False, float('inf')
        sample_pts = [
            (lx, ly), (lx + lw, ly), (lx, ly + lh), (lx + lw, ly + lh),
            (lx + lw // 2, ly), (lx + lw // 2, ly + lh),
            (lx, ly + lh // 2), (lx + lw // 2, ly + lh // 2),
        ]
        for sx, sy in sample_pts:
            d = dist[min(h - 1, max(0, sy)), min(w - 1, max(0, sx))]
            if d <= 0:
                return False, float('inf')
            if d < margin:
                total_penalty += (margin - d) ** 2
    return True, total_penalty


def _evaluate_candidate_layout(
    lobe_results: List[Tuple[int, str, np.ndarray, List[int], List[Dict[str, Any]]]],
    font_size: int,
    capacities: List[float],
    lobe_areas: List[float],
    mask_centroid: Tuple[float, float],
    mask_cov: np.ndarray,
    char_size: float,
    dist: np.ndarray,
    words: List[str],
    partition: List[int],
    target_font: int,
    minimum_font: int,
) -> float:
    """Compute unified layout objective score evaluating occupancy, clearance, moments, and balance."""
    all_lines = [l for res in lobe_results for l in res[4]]
    if not all_lines:
        return float('inf')

    # 1. Overflow & boundary clearance check via distance transform
    valid_sdf, c_clearance = check_sdf_clearance(dist, all_lines, font_size)
    if not valid_sdf:
        return float('inf')

    # 2. Width utilization & fill ratios
    fills = []
    prev_idx = 0
    from . import text_render
    for i, end_idx in enumerate(partition):
        lobe_words = words[prev_idx:end_idx]
        advance = sum(text_render.get_string_width(font_size, w) for w in lobe_words)
        advance += max(0, len(lobe_words) - 1) * text_render.get_string_width(font_size, " ")
        cap = max(1.0, capacities[i])
        fills.append(advance / cap)
        prev_idx = end_idx

    u_range = max(fills) - min(fills) if fills else 0.0
    u_var = float(np.var(fills)) if len(fills) > 1 else 0.0
    c_utilization = u_range * 1.5 + u_var * 5.0

    # 3. Raggedness (adjacent line width difference)
    c_ragged = 0.0
    for res in lobe_results:
        lobe_lines = res[4]
        for j in range(len(lobe_lines) - 1):
            w1 = float(lobe_lines[j]["width"])
            w2 = float(lobe_lines[j + 1]["width"])
            avg_w = max(1.0, (w1 + w2) / 2.0)
            c_ragged += ((w1 - w2) / avg_w) ** 2

    # 4. Centroid alignment
    text_centroid, text_cov = calculate_text_moments(all_lines, font_size)
    c_centroid = (((text_centroid[0] - mask_centroid[0]) ** 2 +
                   (text_centroid[1] - mask_centroid[1]) ** 2) / max(1.0, char_size ** 2))

    # 5. Second moments / inertia tensor matching
    tr_text = float(np.trace(text_cov))
    tr_mask = float(np.trace(mask_cov))
    if tr_text > 0 and tr_mask > 0:
        diff_cov = (text_cov / tr_text) - (mask_cov / tr_mask)
        c_shape = float(np.sum(diff_cov * diff_cov))
    else:
        c_shape = 0.0

    # 6. Lobe area proportionality
    total_lobe_area = max(1.0, sum(lobe_areas))
    p_areas = [a / total_lobe_area for a in lobe_areas]
    advances = []
    prev_idx = 0
    for i, end_idx in enumerate(partition):
        lobe_words = words[prev_idx:end_idx]
        adv = sum(text_render.get_string_width(font_size, w) for w in lobe_words)
        adv += max(0, len(lobe_words) - 1) * text_render.get_string_width(font_size, " ")
        advances.append(adv)
        prev_idx = end_idx
    total_adv = max(1.0, sum(advances))
    q_advances = [adv / total_adv for adv in advances]
    c_lobe = sum((q - p) ** 2 for q, p in zip(q_advances, p_areas)) if len(lobe_areas) > 1 else 0.0

    # 7. Semantic breakpoint cost & punctuation awareness
    conjunctions = {
        'and', 'or', 'but', 'nor', 'so', 'for', 'yet', 'if', 'because', 'although',
        'though', 'while', 'when', 'that', 'which', 'who', 'whom', 'whose', 'where',
        'from', 'with', 'by', 'at', 'in', 'on', 'to', 'as', 'since', 'until', 'unless',
    }
    c_linguistic = 0.0
    for end in partition[:-1]:
        w_prev = words[end - 1]
        w_next = words[end].lower().strip('“"\'')
        if w_prev.endswith(('.', '!', '?', '~', '♡', '♥', '”', '’', '⁉', '‼', '...')):
            c_linguistic += 0.0
        elif w_prev.endswith((',', ';', ':', '—', '"', '–', '-')):
            c_linguistic += 1.0
        elif w_next in conjunctions:
            c_linguistic += 2.0
        else:
            c_linguistic += 8.0

    # 8. Font disparity penalty across lobes
    font_sizes = [res[0] for res in lobe_results]
    disparity = max(font_sizes) - min(font_sizes) if font_sizes else 0
    c_disparity = 5.0 * max(0, disparity - 5) + 0.5 * disparity

    # 9. Font size reward
    r_font = (font_size - minimum_font) / max(1.0, float(target_font - minimum_font))

    total_score = (
        c_utilization * 1.0 +
        c_ragged * 0.2 +
        c_centroid * 1.5 +
        c_shape * 1.0 +
        c_lobe * 15.0 +
        c_linguistic * 1.5 +
        c_disparity * 2.0 +
        min(c_clearance * 0.05, 3.0) -
        r_font * 1.0
    )
    return total_score


def _fit_lobe_text(region, rects, text, target_font, minimum_font, hyphenate, line_spacing):
    """Allocate consecutive words across safe lobes with DP variable-width line breaking and semantic breakpoints."""
    from . import text_render, fg_bg_compare, _horizontal_layout, stroke

    words = text.split()
    if not words or not rects:
        return None
    fg, bg = fg_bg_compare(*region.get_font_colors())
    target_font = max(8, int(target_font))

    interior = getattr(region, "_bubble_interior", None)
    if len(rects) == 1 and interior is not None and np.any(interior):
        # A single lobe may be stepped, bent, or otherwise non-rectangular. Let
        # row-specific line slots use its complete safe mask instead of clipping
        # the layout to one inscribed rectangle.
        ys, xs = np.nonzero(interior)
        rects = [[int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]]

    rect_areas = [(r[2] - r[0]) * (r[3] - r[1]) for r in rects]
    total_area = max(1, sum(rect_areas))
    primary_idx = int(np.argmax(rect_areas))

    num_rects = len(rects)
    num_words = len(words)
    candidate_partitions = []
    if num_rects == 1 or num_words < num_rects:
        rects = [rects[primary_idx]]
        rect_areas = [rect_areas[primary_idx]]
        total_area = rect_areas[0]
        num_rects = 1
        primary_idx = 0
        candidate_partitions = [[num_words]]
    elif num_rects == 2 and num_words >= 2:
        candidate_partitions = [[k, num_words] for k in range(1, num_words)]
    elif num_rects == 3 and num_words >= 3:
        candidate_partitions = [
            [k1, k2, num_words]
            for k1 in range(1, num_words - 1)
            for k2 in range(k1 + 1, num_words)
        ]
    else:
        candidate_partitions = [[num_words]]

    # Pre-extract partition masks and lobe centers for each rect
    partition_masks = []
    lobe_centers = []
    lobe_y_spans = []

    if interior is not None and np.any(interior) and num_rects >= 2:
        # Build radius-weighted partitions across the full interior
        partitions = [interior.copy() for _ in range(num_rects)]
        centers = []
        for rect in rects:
            rx1, ry1, rx2, ry2 = rect
            centers.append(((rx1 + rx2) / 2.0, (ry1 + ry2) / 2.0, max(rect_areas)))
        for i in range(num_rects):
            for j in range(i + 1, num_rects):
                xi, yi, _ = centers[i]
                xj, yj, _ = centers[j]
                if abs(yi - yj) >= abs(xi - xj):
                    split_y = int(round((yi + yj) / 2.0))
                    if yi < yj:
                        partitions[i][split_y:, :] = 0
                        partitions[j][:split_y, :] = 0
                    else:
                        partitions[i][:split_y, :] = 0
                        partitions[j][split_y:, :] = 0
                else:
                    split_x = int(round((xi + xj) / 2.0))
                    if xi < xj:
                        partitions[i][:, split_x:] = 0
                        partitions[j][:, :split_x] = 0
                    else:
                        partitions[i][:, :split_x] = 0
                        partitions[j][:, split_x:] = 0
        for i, rect in enumerate(rects):
            p_mask = partitions[i]
            p_ys, p_xs = np.nonzero(p_mask)
            if len(p_ys):
                ly0, ly1 = int(p_ys.min()), int(p_ys.max())
                row_centers = [float(xs.mean()) for y in range(rect[1], rect[3])
                               if len(xs := np.flatnonzero(p_mask[y, :]))]
                rcx = float(np.median(row_centers)) if row_centers else (rect[0] + rect[2]) / 2.0
            else:
                ly0, ly1 = rect[1], rect[3]
                rcx = (rect[0] + rect[2]) / 2.0
            partition_masks.append(p_mask)
            lobe_centers.append(rcx)
            lobe_y_spans.append((ly0, ly1))
    else:
        for rect in rects:
            rx1, ry1, rx2, ry2 = rect
            rcx = (rx1 + rx2) / 2.0
            lobe_centers.append(rcx)
            lobe_y_spans.append((ry1, ry2))
            if interior is not None and np.any(interior):
                p_mask = np.zeros_like(interior)
                p_mask[ry1:ry2, rx1:rx2] = interior[ry1:ry2, rx1:rx2]
            else:
                p_mask = np.zeros((ry2 + 10, rx2 + 10), dtype=np.uint8)
                p_mask[ry1:ry2, rx1:rx2] = 1
            partition_masks.append(p_mask)

    fit_cache = {}

    def get_best_fit(rect_idx, start_idx, end_idx, font_size_limit=None):
        key = (rect_idx, start_idx, end_idx, font_size_limit)
        if key in fit_cache:
            return fit_cache[key]

        rect = rects[rect_idx]
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        candidate_words = words[start_idx:end_idx]
        candidate = " ".join(candidate_words)
        p_mask = partition_masks[rect_idx]
        rcx = lobe_centers[rect_idx]
        ly0, ly1 = max(lobe_y_spans[rect_idx][0], rect[1]), min(lobe_y_spans[rect_idx][1], rect[3])
        lang = getattr(region, 'target_lang', 'en_US')

        max_search = font_size_limit if font_size_limit is not None else target_font
        search_sizes = list(range(max_search, minimum_font - 1, -1))

        best = None
        for s in search_sizes:
            spacing = int(s * (line_spacing or 0.01))
            line_step = int(np.ceil(s * 1.15)) + spacing
            y_mid = (ly0 + ly1) / 2.0

            # 1. Variable-width slot DP line breaker across natural lobe span
            slots = build_line_slots(p_mask, ly0, s, line_spacing, ly1, rcx, rect=rect)
            if slots:
                lines = dp_break_lines_for_lobe(
                    candidate_words, slots, rcx, s, lang, hyphenate, line_spacing
                )
                if lines:
                    actual_h = (len(lines) - 1) * line_step + s
                    avail_h = ly1 - ly0
                    if avail_h > actual_h + line_step * 2:
                        ideal_y = int(round(y_mid - actual_h / 2.0))
                        if abs(ideal_y - lines[0]["y"]) >= line_step:
                            centered_slots = build_line_slots(p_mask, ideal_y, s, line_spacing, ly1, rcx, rect=rect)
                            centered_lines = dp_break_lines_for_lobe(
                                candidate_words, centered_slots, rcx, s, lang, hyphenate, line_spacing
                            )
                            if centered_lines and len(centered_lines) == len(lines):
                                lines = centered_lines

                    opt_cx, opt_lines = optimize_lobe_center_x(lines, p_mask, s, rcx, rect)
                    c_box = render_positioned_lines(
                        opt_lines, rect, s, fg, bg, line_spacing, lang, region.direction == 'hr', stroke_width=stroke.get_text_stroke_width(s, bg, region.bg_colors)
                    )
                    if c_box is not None and np.any(c_box[:, :, 3]):
                        best = (s, candidate, c_box, rect, opt_lines)
                        break

            # 2. Fallback to standard put_text_horizontal inside inscribed rect
            nw, nh = _horizontal_layout(
                text_render.FONT_SELECTION_KEY,
                s, candidate, w, h, lang, hyphenate, line_spacing,
            )
            if nw <= w and nh <= h:
                box = text_render.put_text_horizontal(
                    s, candidate, w, h, region.alignment,
                    region.direction == 'hr', fg, bg, lang,
                    hyphenate, line_spacing, font_size_minimum=s, stroke_width=stroke.get_text_stroke_width(s, bg, region.bg_colors),
                )
                if box is not None and np.any(box[:, :, 3]) and box.shape[1] <= w and box.shape[0] <= h:
                    best = (s, candidate, box, rect, [{"x": rect[0], "y": rect[1], "width": w, "text": candidate}])
                    break

        fit_cache[key] = best
        return fit_cache[key]

    capacities = [
        sum(slot["width"] for slot in build_line_slots(mask, span[0], target_font, line_spacing,
            span[1], center, rect=rect))
        for mask, span, center, rect in zip(partition_masks, lobe_y_spans, lobe_centers, rects)
    ]
    if any(capacity <= 0 for capacity in capacities):
        return None

    # Calculate safe mask centroid and covariance for geometric mass matching
    mask_centroid, mask_cov = calculate_mask_moments(interior if interior is not None else np.zeros((10, 10)))
    char_size = float(np.sqrt(np.count_nonzero(interior))) if interior is not None else 50.0
    dist = cv2.distanceTransform(interior.astype(np.uint8), cv2.DIST_L2, 5) if interior is not None else np.ones((10, 10), np.float32)
    lobe_areas = [float(np.count_nonzero(p_mask)) for p_mask in partition_masks]

    # Balance actual measured text advance against safe line-slot capacity and geometry.
    best_score = float('inf')
    best_layout = None
    stranded = {'a', 'an', 'the', 'to', 'of', 'for', 'in', 'on', 'at', 'by', 'with', 'from'}

    # 1. Candidate uniform font sizes across preferred range
    preferred_min = max(int(np.ceil(target_font * 0.90)), target_font - 2, minimum_font)
    for s in range(target_font, preferred_min - 1, -1):
        for partition in candidate_partitions:
            if any(words[end - 1].lower().strip('“"\'') in stranded or
                   words[end].startswith((',', '.', ';', ':', '!', '?')) for end in partition[:-1]):
                continue
            previous = 0
            fitted = []
            fills = []
            for i, end in enumerate(partition):
                res = get_best_fit(i, previous, end, font_size_limit=s)
                if res is None or res[0] != s:
                    break
                fitted.append(res)
                advance = sum(text_render.get_string_width(s, word) for word in words[previous:end])
                advance += max(0, end - previous - 1) * text_render.get_string_width(s, ' ')
                fills.append(advance / max(1.0, capacities[i]))
                previous = end
            if len(fitted) != num_rects:
                continue

            fill_diff = max(fills) - min(fills) if fills else 0.0
            score = _evaluate_candidate_layout(
                fitted, s, capacities, lobe_areas, mask_centroid, mask_cov,
                char_size, dist, words, partition, target_font, minimum_font,
            )
            score += fill_diff * 2.0 - 2.0  # uniform font reward
            if score < best_score:
                best_score, best_layout = score, (partition, fitted)

    # 2. Candidate per-lobe adaptive font sizes
    for partition in candidate_partitions:
        if any(words[end - 1].lower().strip('“"\'') in stranded or
               words[end].startswith((',', '.', ';', ':', '!', '?')) for end in partition[:-1]):
            continue
        previous = 0
        fitted = []
        fills = []
        for i, end in enumerate(partition):
            fit = get_best_fit(i, previous, end)
            if fit is None:
                break
            fitted.append(fit)
            advance = sum(text_render.get_string_width(target_font, word) for word in words[previous:end])
            advance += max(0, end - previous - 1) * text_render.get_string_width(target_font, ' ')
            fills.append(advance / max(1.0, capacities[i]))
            previous = end
        if len(fitted) != num_rects:
            continue

        fill_diff = max(fills) - min(fills) if fills else 0.0
        candidate_font = fitted[primary_idx][0]
        score = _evaluate_candidate_layout(
            fitted, candidate_font, capacities, lobe_areas, mask_centroid, mask_cov,
            char_size, dist, words, partition, target_font, minimum_font,
        )
        score += fill_diff * 2.0

        if score < best_score:
            best_score, best_layout = score, (partition, fitted)

    if best_layout is not None:
        partition, lobe_results = best_layout
        segments = []
        for (s_i, candidate, box, b_rect, lines) in lobe_results:
            segments.append({
                "bounds": list(map(int, b_rect)),
                "text": candidate,
                "box": box,
                "font_size": s_i,
                "lines": lines,
            })
        representative_font = lobe_results[primary_idx][0]
        return representative_font, segments

    return None
