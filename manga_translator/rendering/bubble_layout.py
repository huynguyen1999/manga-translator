"""Conservative, atomic layout and erasure for enclosed dialogue."""
import copy
import base64
from functools import cached_property
from typing import List, Tuple, Optional, Dict, Any

import cv2
import numpy as np

from ..geometry.bubbles import compose_bubble_cleanup, prepare_page_geometry


def encode_safe_shape(interior, scale_x=1.0, scale_y=1.0):
    """Store one group's safe interior, not the page-wide union mask."""
    if interior is None or not np.any(interior):
        return None
    ys, xs = np.nonzero(interior)
    x, y = int(xs.min()), int(ys.min())
    crop = (interior[y:int(ys.max()) + 1, x:int(xs.max()) + 1] > 0).astype(np.uint8) * 255
    if scale_x != 1.0 or scale_y != 1.0:
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale_x)),
                                 max(1, round(crop.shape[0] * scale_y))), interpolation=cv2.INTER_NEAREST)
    ok, encoded = cv2.imencode('.png', crop)
    if not ok:
        return None
    return {"x": round(x * scale_x), "y": round(y * scale_y),
            "png": base64.b64encode(encoded).decode('ascii')}


def decode_safe_shape(shape, height, width):
    if not isinstance(shape, dict):
        return None
    try:
        if not isinstance(shape.get('png'), str) or len(shape['png']) > 2_000_000:
            return None
        raw = np.frombuffer(base64.b64decode(shape['png'], validate=True), np.uint8)
        crop = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
        x, y = int(shape['x']), int(shape['y'])
        if crop is None or x < 0 or y < 0 or x + crop.shape[1] > width or y + crop.shape[0] > height:
            return None
        interior = np.zeros((height, width), np.uint8)
        interior[y:y + crop.shape[0], x:x + crop.shape[1]] = crop > 0
        return interior
    except (KeyError, ValueError, TypeError):
        return None


def encode_rendered_box(box, scale_x=1.0, scale_y=1.0):
    """Keep the glyph pixels that passed the safe-mask check for editor display."""
    if box is None:
        return None
    if scale_x != 1.0 or scale_y != 1.0:
        box = cv2.resize(box, (max(1, round(box.shape[1] * scale_x)),
                               max(1, round(box.shape[0] * scale_y))), interpolation=cv2.INTER_LINEAR)
    ok, encoded = cv2.imencode('.png', cv2.cvtColor(box, cv2.COLOR_RGBA2BGRA))
    return base64.b64encode(encoded).decode('ascii') if ok else None


def decode_rendered_box(encoded_box):
    if not isinstance(encoded_box, str) or len(encoded_box) > 8_000_000:
        return None
    try:
        raw = np.frombuffer(base64.b64decode(encoded_box, validate=True), np.uint8)
        box = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
        if box is None or box.ndim != 3 or box.shape[2] != 4:
            return None
        return cv2.cvtColor(box, cv2.COLOR_BGRA2RGBA)
    except (ValueError, cv2.error):
        return None


def _largest_rect_containing(mask, x, y):
    """Largest all-mask rectangle containing one interior point."""
    # ponytail: O(h²) per lobe is small for speech bubbles; use a histogram-stack
    # maximal-rectangle pass only if page-scale masks make this measurable.
    if not mask[y, x]:
        ys, xs = np.nonzero(mask)
        if not len(xs):
            return None
        nearest = int(np.argmin((xs - x) ** 2 + (ys - y) ** 2))
        x, y = int(xs[nearest]), int(ys[nearest])

    runs = []
    for row in range(mask.shape[0]):
        if not mask[row, x]:
            runs.append(None)
            continue
        left = x
        right = x
        while left > 0 and mask[row, left - 1]:
            left -= 1
        while right + 1 < mask.shape[1] and mask[row, right + 1]:
            right += 1
        runs.append((left, right + 1))

    best = None
    top = y
    left, right = runs[y]
    while top >= 0 and runs[top] is not None:
        left = max(left, runs[top][0])
        right = min(right, runs[top][1])
        if right <= left:
            break
        bottom = y
        inner_left, inner_right = left, right
        while bottom < mask.shape[0] and runs[bottom] is not None:
            inner_left = max(inner_left, runs[bottom][0])
            inner_right = min(inner_right, runs[bottom][1])
            if inner_right <= inner_left:
                break
            rect = [inner_left, top, inner_right, bottom + 1]
            area = (inner_right - inner_left) * (bottom + 1 - top)
            if best is None or area > best[0]:
                best = (area, rect)
            bottom += 1
        top -= 1
    return best[1] if best else None


def _centered_or_largest_rect(partition, x, y, radius, minimum_font):
    """Find a safe text rectangle well-centered at the interior peak (x, y)."""
    h, w = partition.shape[:2]
    if not (0 <= y < h and 0 <= x < w and partition[y, x]):
        ys, xs = np.nonzero(partition)
        if not len(xs):
            return None
        nearest = int(np.argmin((xs - x) ** 2 + (ys - y) ** 2))
        x, y = int(xs[nearest]), int(ys[nearest])

    ys, xs = np.nonzero(partition)
    if not len(xs):
        return None
    y_min, y_max = int(ys.min()), int(ys.max())

    best_rect = None
    best_score = float('-inf')
    max_drift = max(6.0, radius * 0.35)
    step_y = max(1, int(round(radius * 0.08)))

    for y1 in range(y_min, y, step_y):
        for y2 in range(y + 1, y_max + 1, step_y):
            rh = y2 - y1
            if rh < minimum_font:
                continue
            sub = partition[y1:y2, :]
            valid_cols = np.all(sub == 1, axis=0)
            if not valid_cols[x]:
                continue
            left = x
            while left > 0 and valid_cols[left - 1]:
                left -= 1
            right = x
            while right + 1 < len(valid_cols) and valid_cols[right + 1]:
                right += 1

            rw = right + 1 - left
            if rw < minimum_font * 2:
                continue

            rcx = (left + right + 1) / 2.0
            rcy = (y1 + y2) / 2.0
            dist_to_peak = np.sqrt((rcx - x) ** 2 + (rcy - y) ** 2)
            if dist_to_peak > max_drift:
                continue

            ar = rw / rh
            if not (0.30 <= ar <= 2.8):
                continue

            area = rw * rh
            score = area - 8.0 * (dist_to_peak ** 1.5)
            if score > best_score:
                best_score = score
                best_rect = [left, y1, right + 1, y2]

    if best_rect is not None:
        return best_rect

    return _largest_rect_containing(partition, x, y)


def _estimate_adaptive_font_size(interior, text, minimum_font):
    """Estimate a balanced target font size that comfortably fills ~55-65% of the bubble interior."""
    if interior is None or not text:
        return max(8, int(minimum_font))
    area = int(np.count_nonzero(interior))
    if area < 80:
        return max(8, int(minimum_font))
    distance = cv2.distanceTransform(interior.astype(np.uint8), cv2.DIST_L2, 5)
    max_radius = float(distance.max()) if np.any(distance) else 15.0
    char_count = max(1, len(text.strip()))

    target_text_area = area * 0.58
    est_size = float(np.sqrt(target_text_area / (0.55 * char_count)))

    ys, xs = np.nonzero(interior)
    bw = float(xs.max() - xs.min() + 1) if len(xs) else interior.shape[1]
    bh = float(ys.max() - ys.min() + 1) if len(ys) else interior.shape[0]

    words = text.strip().split()
    max_word_len = max((len(w) for w in words), default=1)
    word_cap = (bw * 0.82) / max(1.0, 0.55 * max_word_len)

    font_cap = min(max_radius * 1.35, max(bh, bw) * 0.38, word_cap, 54.0)
    ideal = max(float(minimum_font), min(font_cap, est_size))
    return int(round(ideal))


def _has_verified_neck(interior, first, second):
    """Return whether the mask narrows materially between two interior peaks."""
    _r1, x1, y1 = first
    _r2, x2, y2 = second

    def span(values, center):
        active = np.flatnonzero(values)
        if not len(active):
            return 0
        center = int(active[np.argmin(np.abs(active - center))])
        left = right = center
        while left > 0 and values[left - 1]:
            left -= 1
        while right + 1 < len(values) and values[right + 1]:
            right += 1
        return right - left + 1

    thicknesses = []
    if abs(y2 - y1) >= abs(x2 - x1):
        start, end = sorted((y1, y2))
        for y in range(start, end + 1):
            t = (y - y1) / max(1, y2 - y1)
            thicknesses.append(span(interior[y, :], round(x1 + t * (x2 - x1))))
    else:
        start, end = sorted((x1, x2))
        for x in range(start, end + 1):
            t = (x - x1) / max(1, x2 - x1)
            thicknesses.append(span(interior[:, x], round(y1 + t * (y2 - y1))))

    if len(thicknesses) < 3:
        return False
    endpoint = min(thicknesses[0], thicknesses[-1])
    return endpoint > 0 and min(thicknesses[1:-1]) <= endpoint * 0.65


def _lobe_rects(interior, source_lines, minimum_font, target_lang=None):
    """Split a connected mask at its broad interior peaks, not its narrow neck."""
    distance = cv2.distanceTransform(interior.astype(np.uint8), cv2.DIST_L2, 5)
    if not np.any(distance):
        return []
    max_r = float(distance.max())
    local_max = distance == cv2.dilate(distance, np.ones((9, 9), np.uint8))
    local_max &= distance >= max(8.0, max_r * 0.28, minimum_font * 0.8)
    count, labels, _stats, centroids = cv2.connectedComponentsWithStats(local_max.astype(np.uint8), 8)
    peaks = []
    for label in range(1, count):
        pixels = labels == label
        flat = int(np.argmax(np.where(pixels, distance, -1)))
        y, x = np.unravel_index(flat, distance.shape)
        peaks.append((float(distance[y, x]), int(x), int(y)))
    peaks.sort(reverse=True)

    def span(values, center):
        active = np.flatnonzero(values)
        if not len(active):
            return 0
        center = int(active[np.argmin(np.abs(active - center))])
        left = right = center
        while left > 0 and values[left - 1]:
            left -= 1
        while right + 1 < len(values) and values[right + 1]:
            right += 1
        return right - left + 1

    # Suppress plateaus from one lobe, retain up to three meaningful lobes.
    selected = []
    for radius, x, y in peaks:
        suppressed = False
        for pr, px, py in selected:
            d2 = (x - px) ** 2 + (y - py) ** 2
            if d2 < (1.25 * max(radius, pr)) ** 2:
                suppressed = True
                break
            if abs(y - py) >= abs(x - px):
                x_shift = abs(x - px)
                if x_shift < max(radius, pr) * 0.5:
                    y_start, y_end = sorted((y, py))
                    spans = [span(interior[cy, :], round(x + (cy - y) / (py - y) * (px - x)))
                             for cy in range(y_start, y_end + 1, 2)]
                    if spans and min(spans) >= 0.70 * min(spans[0], spans[-1]):
                        suppressed = True
                        break
            else:
                y_shift = abs(y - py)
                if y_shift < max(radius, pr) * 0.5:
                    x_start, x_end = sorted((x, px))
                    spans = [span(interior[:, cx], round(y + (cx - x) / (px - x) * (py - y)))
                             for cx in range(x_start, x_end + 1, 2)]
                    if spans and min(spans) >= 0.70 * min(spans[0], spans[-1]):
                        suppressed = True
                        break
        if not suppressed:
            selected.append((radius, x, y))
        if len(selected) == 3:
            break
    if not selected:
        return []
    if len(selected) == 1:
        selected = [max(peaks)]

    # Preserve source order for non-English targets; English reads top-to-bottom.
    source_centers = []
    for line in source_lines:
        points = np.asarray(line).reshape(-1, 2)
        source_centers.append((float(points[:, 0].mean()), float(points[:, 1].mean())))

    def _peak_order_key(peak):
        if not source_centers:
            return (0, peak[2], peak[1])
        closest_idx = min(
            range(len(source_centers)),
            key=lambda index: (peak[1] - source_centers[index][0]) ** 2 + (peak[2] - source_centers[index][1]) ** 2,
        )
        return (closest_idx, peak[2], peak[1])

    if str(target_lang).lower() in {'eng', 'en', 'en_us', 'en-us', 'english'}:
        horizontal_flow = (max(peak[1] for peak in selected) - min(peak[1] for peak in selected)
                           > 2 * (max(peak[2] for peak in selected) - min(peak[2] for peak in selected)))
        selected.sort(key=(lambda peak: (peak[1], peak[2])) if horizontal_flow
                      else (lambda peak: (peak[2], peak[1])))
    else:
        selected.sort(key=_peak_order_key)

    # Radius-weighted partitions between adjacent peaks
    num_peaks = len(selected)
    partitions = [interior.copy() for _ in range(num_peaks)]
    for i in range(num_peaks):
        for j in range(i + 1, num_peaks):
            r_i, xi, yi = selected[i]
            r_j, xj, yj = selected[j]
            r_sum = max(1.0, r_i + r_j)
            if abs(yi - yj) >= abs(xi - xj):
                # Vertical separation: radius-weighted horizontal split
                split_y = int(round(yi * (r_j / r_sum) + yj * (r_i / r_sum)))
                if yi < yj:
                    partitions[i][split_y:, :] = 0
                    partitions[j][:split_y, :] = 0
                else:
                    partitions[i][:split_y, :] = 0
                    partitions[j][split_y:, :] = 0
            else:
                # Horizontal separation: radius-weighted vertical split
                split_x = int(round(xi * (r_j / r_sum) + xj * (r_i / r_sum)))
                if xi < xj:
                    partitions[i][:, split_x:] = 0
                    partitions[j][:, :split_x] = 0
                else:
                    partitions[i][:, :split_x] = 0
                    partitions[j][:, split_x:] = 0

    rects = []
    for index, (radius, x, y) in enumerate(selected):
        rect = _centered_or_largest_rect(partitions[index], x, y, radius, minimum_font)
        if rect and rect[2] - rect[0] >= minimum_font * 2 and rect[3] - rect[1] >= minimum_font:
            rects.append(rect)
    return rects


def _stepped_lobe_rects(interior, minimum_font):
    """Split one connected lobe when its upper and lower row centers form distinct steps."""
    profile = build_geometry_profile(interior)
    if profile is None:
        return []
    y0, y1 = profile["y_min"], profile["y_max"]
    min_band = max(minimum_font * 2, int(round((y1 - y0 + 1) * 0.20)))
    best = None
    for split in range(y0 + min_band, y1 - min_band + 1):
        top = profile["center"][y0:split]
        bottom = profile["center"][split:y1 + 1]
        top = top[top > 0]
        bottom = bottom[bottom > 0]
        if not len(top) or not len(bottom):
            continue
        shift = abs(float(np.median(top)) - float(np.median(bottom)))
        balance = min(len(top), len(bottom)) / max(len(top), len(bottom))
        score = shift * (0.5 + 0.5 * balance)
        if best is None or score > best[0]:
            best = (score, shift, split)
    if best is None or best[1] < max(minimum_font * 2, float(profile["width"].max()) * 0.25):
        return []

    rects = []
    for start, end in ((y0, best[2]), (best[2], y1 + 1)):
        band = interior[start:end]
        ys, xs = np.nonzero(band)
        if not len(xs):
            return []
        rects.append([int(xs.min()), start + int(ys.min()), int(xs.max()) + 1, start + int(ys.max()) + 1])
    return rects


def build_geometry_profile(interior: np.ndarray, margin_ratio: float = 0.04) -> Optional[Dict[str, Any]]:
    """Compute row-by-row horizontal spans, smoothed contours, medial peaks, and neck transitions."""
    if interior is None or not np.any(interior):
        return None
    h, w = interior.shape[:2]
    k = max(3, int(round(min(h, w) * margin_ratio)))
    if k % 2 == 0:
        k += 1
    safe_mask = cv2.erode(interior.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    if not np.any(safe_mask):
        safe_mask = interior.copy()

    ys, xs = np.nonzero(safe_mask)
    if not len(ys):
        return None
    y_min, y_max = int(ys.min()), int(ys.max())

    left = np.zeros(h, dtype=np.int32)
    right = np.zeros(h, dtype=np.int32)
    width = np.zeros(h, dtype=np.float32)
    center = np.zeros(h, dtype=np.float32)

    for y in range(y_min, y_max + 1):
        row_xs = np.where(safe_mask[y, :] > 0)[0]
        if len(row_xs):
            l, r = int(row_xs.min()), int(row_xs.max())
            left[y] = l
            right[y] = r + 1
            width[y] = float(r + 1 - l)
            center[y] = float(l + r + 1) / 2.0

    width_smooth = width.copy()
    center_smooth = center.copy()
    if (y_max - y_min + 1) >= 5:
        radius = 4
        x = np.arange(-radius, radius + 1)
        kernel = np.exp(-0.5 * (x / 2.5) ** 2)
        kernel /= kernel.sum()

        w_active = width[y_min:y_max + 1]
        c_active = center[y_min:y_max + 1]

        w_conv = np.convolve(w_active, kernel, mode='same')
        c_conv = np.convolve(c_active, kernel, mode='same')

        width_smooth[y_min:y_max + 1] = w_conv
        center_smooth[y_min:y_max + 1] = c_conv

    d_width = np.gradient(width_smooth)
    d_center = np.gradient(center_smooth)

    dist = cv2.distanceTransform(safe_mask.astype(np.uint8), cv2.DIST_L2, 5)
    max_r = float(dist.max()) if np.any(dist) else 10.0
    local_max = dist == cv2.dilate(dist, np.ones((9, 9), np.uint8))
    local_max &= dist >= max(8.0, max_r * 0.28)
    count, labels, _stats, _ = cv2.connectedComponentsWithStats(local_max.astype(np.uint8), 8)
    peaks = []
    for label in range(1, count):
        pixels = labels == label
        flat = int(np.argmax(np.where(pixels, dist, -1)))
        py, px = np.unravel_index(flat, dist.shape)
        peaks.append((float(dist[py, px]), int(px), int(py)))
    peaks.sort(reverse=True)

    selected_peaks = []
    for radius, px, py in peaks:
        if any((px - sx) ** 2 + (py - sy) ** 2 < (1.25 * max(radius, sr)) ** 2 for sr, sx, sy in selected_peaks):
            continue
        selected_peaks.append((radius, px, py))
        if len(selected_peaks) == 3:
            break

    return {
        "safe_mask": safe_mask,
        "y_min": y_min,
        "y_max": y_max,
        "left": left,
        "right": right,
        "width": width,
        "center": center,
        "width_smooth": width_smooth,
        "center_smooth": center_smooth,
        "d_width": d_width,
        "d_center": d_center,
        "peaks": selected_peaks,
        "distance": dist,
        "max_radius": max_r,
    }


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


def render_positioned_lines(lines: List[Dict[str, Any]], rect: List[int], font_size: int, fg: Tuple[int, int, int], bg: Optional[Tuple[int, int, int]], line_spacing: float, language: str, reversed_direction: bool = False):
    """Render optical-centered text lines onto an unwarped RGBA box matching rect dimensions."""
    from . import text_render

    if not lines:
        return None

    rx1, ry1, rx2, ry2 = rect
    box_w = max(1, rx2 - rx1)
    box_h = max(1, ry2 - ry1)

    bg_size = int(max(font_size * 0.07, 1)) if bg is not None else 0

    canvas_text = np.zeros((box_h, box_w), dtype=np.uint8)
    canvas_border = canvas_text.copy()

    for line in lines:
        line_x_rel = line["x"] - rx1
        line_y_rel = line["y"] - ry1 + font_size
        pen = [line_x_rel, line_y_rel]

        for c in line["text"]:
            offset_x = text_render.put_char_horizontal(
                font_size, c, pen, canvas_text, canvas_border, border_size=bg_size
            )
            pen[0] += offset_x

    canvas_border = np.clip(canvas_border, 0, 255)
    line_box = text_render.add_color(canvas_text, fg, canvas_border, bg)
    return line_box


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
    from . import text_render, fg_bg_compare, _horizontal_layout

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
                        opt_lines, rect, s, fg, bg, line_spacing, lang, region.direction == 'hr'
                    )
                    if c_box is not None and np.any(c_box[:, :, 3]):
                        best = (s, candidate, c_box, rect, opt_lines)
                        break

            # 2. Fallback to standard put_text_horizontal inside inscribed rect
            nw, nh = _horizontal_layout(
                s, candidate, w, h, lang, hyphenate, line_spacing,
            )
            if nw <= w and nh <= h:
                box = text_render.put_text_horizontal(
                    s, candidate, w, h, region.alignment,
                    region.direction == 'hr', fg, bg, lang,
                    hyphenate, line_spacing, font_size_minimum=s,
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
    preferred_min = max(int(np.ceil(target_font * 0.85)), target_font - 3, minimum_font)
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


def group_regions_by_bubbles(regions, detections, minimum_overlap: float = 0.35, group: bool = False):
    """Associate OCR regions with bubbles without merging them by default.

    If group=False, regions are not merged into a single multi-line text region,
    preserving each region independently while still assigning the matching bubble mask.
    Pass group=True only when one semantic translation unit per bubble is desired.
    """
    if not detections:
        for index, region in enumerate(regions):
            if not getattr(region, "region_id", None):
                region.region_id = f"region_{index}"
            if not getattr(region, "source_region_ids", None):
                region.source_region_ids = [str(region.region_id)]
            if not getattr(region, "source_regions", None):
                region.source_regions = [_source_region_snapshot(region, index)]
        return list(regions)
    for index, region in enumerate(regions):
        region._bubble_source_order = index
        if not getattr(region, "region_id", None):
            region.region_id = f"region_{index}"
        if not getattr(region, "source_region_ids", None):
            region.source_region_ids = [str(region.region_id)]
        if not getattr(region, "source_regions", None):
            region.source_regions = [_source_region_snapshot(region, index)]
    assignments = {}
    for index, region in enumerate(regions):
        polygon = np.zeros(detections[0].mask.shape, np.uint8)
        cv2.fillPoly(polygon, [np.asarray(line, np.int32) for line in region.lines], 1)
        area = max(1, int(np.count_nonzero(polygon)))
        scores = [np.count_nonzero(polygon & (detection.mask > 0)) / area for detection in detections]
        if scores and max(scores) >= minimum_overlap:
            assignments.setdefault(int(np.argmax(scores)), []).append((index, region))

    result = []
    assigned = {index for members in assignments.values() for index, _ in members}
    for index, region in enumerate(regions):
        if index not in assigned:
            result.append(region)
    for detection_index, members in assignments.items():
        members.sort(key=lambda item: item[0])
        if not group:
            for _, region in members:
                region._bubble_mask = detections[detection_index].mask
                region.bubble_id = f"bubble_{detection_index}"
                region._bubble_detection_confidence = detections[detection_index].confidence
                result.append(region)
        elif len(members) == 1:
            region = members[0][1]
            region._bubble_mask = detections[detection_index].mask
            region.bubble_id = f"bubble_{detection_index}"
            region._bubble_detection_confidence = detections[detection_index].confidence
            result.append(region)
        else:
            region = copy.copy(members[0][1])
            for name, descriptor in vars(type(region)).items():
                if isinstance(descriptor, cached_property):
                    region.__dict__.pop(name, None)
            region.lines = np.concatenate([item.lines for _, item in members])
            region.texts = [item.text for _, item in members]
            region.text = "\n".join(region.texts)
            region.group_id = f"bubble_{detection_index}"
            region.bubble_id = f"bubble_{detection_index}"
            region.region_id = region.group_id
            region.source_region_ids = [
                str(getattr(item, "region_id", f"source_{source_index}"))
                for source_index, item in members
            ]
            region.group_members = list(region.source_region_ids)
            region.source_regions = [
                _source_region_snapshot(item, source_index) for source_index, item in members
            ]
            region._bubble_mask = detections[detection_index].mask
            region._bubble_detection_confidence = detections[detection_index].confidence
            result.append(region)
    return sorted(result, key=lambda item: getattr(item, "_bubble_source_order", 0))


def restore_bubble_assignments(regions, detections):
    """Reconnect persisted region bubble IDs to the reloaded detection masks."""
    by_id = {f"bubble_{index}": detection for index, detection in enumerate(detections or [])}
    missing = []
    for region in regions or []:
        detection = by_id.get(str(getattr(region, "bubble_id", "")))
        if detection is None:
            missing.append(region)
            continue
        region._bubble_mask = detection.mask
        region._bubble_detection_confidence = detection.confidence
    if missing:
        group_regions_by_bubbles(missing, detections, group=False)
    return regions


def _source_region_snapshot(region, reading_order: int):
    """Capture source identity and geometry before a bubble group is merged."""
    lines = np.asarray(getattr(region, "lines", []))
    return {
        "id": str(getattr(region, "region_id", f"source_{reading_order}")),
        "polygons": lines.tolist(),
        "bbox": np.asarray(getattr(region, "xyxy", [0, 0, 0, 0])).tolist(),
        "centroid": np.asarray(getattr(region, "center", [0, 0])).astype(float).tolist(),
        "source_text": str(getattr(region, "text", "") or ""),
        "reading_order": int(reading_order),
    }


def prepare_bubble_masks(image, regions, padding: int = 9, profile=None, return_cleanup: bool = True, page_geometry=None):
    """Compatibility wrapper for callers that still request a page cleanup mask."""
    if page_geometry is not None and page_geometry.matches(image, regions, padding):
        if profile is not None:
            workload = profile.setdefault("workload", {})
            workload["unique_bubble_contours"] = 0
            workload["distance_transform_calls"] = 0
            workload["unique_bubble_count"] = len(page_geometry.bubbles)
        if not return_cleanup:
            return None
        return compose_bubble_cleanup(page_geometry, image.shape[:2])
    _, cleanup = prepare_page_geometry(
        image, regions, padding, return_cleanup=return_cleanup, profile=profile,
    )
    return cleanup

def prepare_bubbles(image, regions, font_path, render_config, group: bool = True):
    from . import _RENDER_LOCK, text_render, _horizontal_layout, _find_horizontal_placement, _points_for_rect, fg_bg_compare

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    custom_mode = any(getattr(region, "_bubble_mask", None) is not None for region in regions)
    # ponytail: flat light interiors only; textured/dark enclosures need a separate
    # validated segmenter before automatic cleanup can safely support them.
    count, labels, stats, _ = cv2.connectedComponentsWithStats((gray >= 220).astype(np.uint8), connectivity=4)
    # A narrow opening can connect a real bubble to the page background. Use a
    # second pass only to identify a review candidate, never to justify erasure.
    closed_count, closed_labels, closed_stats, _ = cv2.connectedComponentsWithStats(
        cv2.morphologyEx((gray >= 220).astype(np.uint8), cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)),
        connectivity=4)
    groups = {}
    custom_masks = {}
    untouched = []
    for index, region in enumerate(regions):
        region._bubble_source_order = index
        if not region.translation:
            untouched.append(region)
            continue
        if custom_mode:
            mask = getattr(region, "_bubble_mask", None)
            if mask is None or not np.any(mask):
                untouched.append(region)
                continue
            label = id(mask)
            custom_masks[label] = (mask > 0).astype(np.uint8)
            groups.setdefault((False, label), []).append((index, region))
            continue
        selected = np.zeros(gray.shape, np.uint8)
        cv2.fillPoly(selected, [np.asarray(line, np.int32) for line in region.lines], 1)
        values = labels[selected > 0]
        votes = np.bincount(values, minlength=count)
        votes[0] = 0
        label = int(votes.argmax())
        if not label or votes[label] < max(1, len(values) * .45):
            untouched.append(region)
            continue
        x, y, w, h, area = stats[label]
        enclosed = x > 0 and y > 0 and x + w < gray.shape[1] and y + h < gray.shape[0]
        uncertain_boundary = False
        if not enclosed or area < 100:
            closed_votes = np.bincount(closed_labels[selected > 0], minlength=closed_count)
            closed_votes[0] = 0
            alternate = int(closed_votes.argmax())
            ax, ay, aw, ah, aa = closed_stats[alternate]
            if (alternate and closed_votes[alternate] >= len(values) * .45 and aa >= 100
                    and ax > 0 and ay > 0 and ax + aw < gray.shape[1] and ay + ah < gray.shape[0]):
                label = alternate
                uncertain_boundary = True
            else:
                untouched.append(region)
                continue
        groups.setdefault((uncertain_boundary, label), []).append((index, region))

    result = list(untouched)
    if not group:
        # Keep each OCR region independent while reusing the same detected
        # component. The recursive call uses custom-mask mode, so sibling ink
        # cannot make an otherwise valid region look like an uncertain cleanup.
        for (uncertain_boundary, label), members in groups.items():
            component = custom_masks[label] if custom_mode else (
                (closed_labels if uncertain_boundary else labels) == label
            ).astype(np.uint8)
            for index, member in members:
                separate = copy.copy(member)
                separate._bubble_mask = component
                separate._bubble_source_order = index
                prepared_regions = prepare_bubbles(
                    image, [separate], font_path, render_config, group=True
                )
                for prepared_region in prepared_regions:
                    prepared_region.region_id = getattr(member, "region_id", None)
                    prepared_region.source_region_ids = list(
                        getattr(member, "source_region_ids", []) or [prepared_region.region_id]
                    )
                    prepared_region.source_regions = copy.deepcopy(
                        getattr(member, "source_regions", [])
                    )
                    if getattr(member, "_bubble_mask", None) is not None:
                        prepared_region._bubble_mask = member._bubble_mask
                    elif hasattr(prepared_region, "_bubble_mask"):
                        del prepared_region._bubble_mask
                    if hasattr(prepared_region, "group_members"):
                        del prepared_region.group_members
                    result.append(prepared_region)
        return sorted(result, key=lambda item: getattr(item, "_bubble_source_order", 0))
    with _RENDER_LOCK:
        text_render.set_font(font_path)
        _horizontal_layout.cache_clear()

        # Pre-calculate page-level baseline target font size across all bubble groups
        page_target_estimates = []
        for (u_b, lbl), mems in groups.items():
            comp = custom_masks[lbl] if custom_mode else ((closed_labels if u_b else labels) == lbl).astype(np.uint8)
            cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            inter = np.zeros_like(comp)
            cv2.drawContours(inter, cnts, -1, 1, cv2.FILLED)
            inter = cv2.erode(inter, np.ones((7, 7), np.uint8))
            t = "\n".join(r.translation for _, r in mems)
            min_f = render_config.font_size_minimum
            if min_f == -1:
                min_f = round(sum(gray.shape) / 200)
            min_f = max(1, min_f)
            est = _estimate_adaptive_font_size(inter, t, min_f)
            if est > min_f:
                page_target_estimates.append(est)
        page_target_font = int(round(np.percentile(page_target_estimates, 70))) if page_target_estimates else None

        for (uncertain_boundary, label), members in groups.items():
            # Existing pipeline region order is already the source reading order.
            members.sort(key=lambda pair: pair[0])
            region = copy.copy(members[0][1])
            for name, descriptor in vars(type(region)).items():
                if isinstance(descriptor, cached_property):
                    region.__dict__.pop(name, None)
            region._bounding_rect = None
            region.angle = 0
            region.lines = np.concatenate([r.lines for _, r in members])
            region.texts = [r.text for _, r in members]
            region.text = "\n".join(region.texts)
            region.translation = "\n".join(r.translation for _, r in members)
            region.group_id = f"bubble_{members[0][0]}"
            region.region_id = region.group_id
            region.source_region_ids = [
                source_id
                for source_index, item in members
                for source_id in (
                    getattr(item, "source_region_ids", None)
                    or [str(getattr(item, "region_id", f"source_{source_index}"))]
                )
            ]
            region.group_members = list(region.source_region_ids)
            source_regions = []
            for source_index, item in members:
                records = getattr(item, "source_regions", None) or [_source_region_snapshot(item, source_index)]
                for source in records:
                    record = copy.deepcopy(source)
                    record["reading_order"] = len(source_regions)
                    source_regions.append(record)
            region.source_regions = source_regions
            region.review_required = any(getattr(r, "review_required", False) for _, r in members)
            region.review_reason = next(
                (getattr(r, "review_reason", None) for _, r in members if getattr(r, "review_required", False)),
                None,
            )
            component = custom_masks[label] if custom_mode else ((closed_labels if uncertain_boundary else labels) == label).astype(np.uint8)
            contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            interior = np.zeros_like(component)
            cv2.drawContours(interior, contours, -1, 1, cv2.FILLED)
            region._bubble_restore = interior.copy()
            interior = cv2.erode(interior, np.ones((7, 7), np.uint8))
            x, y, w, h = cv2.boundingRect(interior)
            region.bubble_bounds = [x, y, x+w, y+h]
            region.layout_bounds = list(region.bubble_bounds)
            region._bubble_interior = interior
            dist = cv2.distanceTransform(interior.astype(np.uint8), cv2.DIST_L2, 5)
            if np.any(dist):
                flat = int(np.argmax(dist))
                cy, cx = np.unravel_index(flat, dist.shape)
                region._bubble_center = (int(cx), int(cy))
            else:
                region._bubble_center = ((x + x + w) // 2, (y + y + h) // 2)

            selected = np.zeros_like(component)
            cv2.fillPoly(selected, [np.asarray(line, np.int32) for line in region.lines], 1)
            region._bubble_restore = cv2.bitwise_or(region._bubble_restore, selected)
            nearby = cv2.dilate(selected, np.ones((7, 7), np.uint8))
            ink = ((gray < 200) & (interior > 0)).astype(np.uint8)
            n, ink_labels, ink_stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
            cleanup = np.zeros_like(component)
            uncertain = False
            if custom_mode:
                cleanup = interior * 255
            else:
                for ink_id in range(1, n):
                    pixels = ink_labels == ink_id
                    if np.any(nearby[pixels]):
                        cleanup[pixels] = 255
                    elif ink_stats[ink_id, cv2.CC_STAT_AREA] >= 3:
                        uncertain = True
                for _, member in members:
                    member_selected = np.zeros_like(component)
                    cv2.fillPoly(member_selected, [np.asarray(line, np.int32) for line in member.lines], 1)
                    if not np.any(ink & member_selected):
                        uncertain = True
                cleanup = cv2.dilate(cleanup, np.ones((3, 3), np.uint8)) * interior
            region._bubble_cleanup = cleanup
            if not custom_mode and (uncertain_boundary or not interior.any() or np.count_nonzero(selected & interior) < .95 * np.count_nonzero(selected)):
                region.review_reason = "uncertain_boundary"
            elif not custom_mode and (uncertain or not cleanup.any()):
                region.review_reason = "uncertain_cleanup"
            elif not custom_mode and not region.horizontal:
                region.review_reason = "text_does_not_fit"

            minimum = render_config.font_size_minimum
            if minimum == -1:
                minimum = round(sum(gray.shape) / 200)
            minimum = max(1, minimum)
            target = max(minimum, render_config.font_size or region.font_size + render_config.font_size_offset)
            text = region.get_translation_for_rendering()
            if render_config.font_size is None:
                adaptive_target = _estimate_adaptive_font_size(interior, text, minimum)
                target = max(target, adaptive_target)
                if page_target_font is not None:
                    target = max(target, int(round(page_target_font * 0.90)))

            preferred_minimum = max(int(np.ceil(target * 0.85)), target - 3, minimum)

            lobe_rects = _lobe_rects(
                interior, region.lines, max(8, minimum), getattr(region, "target_lang", None)
            )
            if len(lobe_rects) > 1:
                target = max(minimum, render_config.font_size or region.font_size + render_config.font_size_offset)
            lobe_layout = None
            bubble_center = getattr(region, '_bubble_center', None) or (
                (region.bubble_bounds[0] + region.bubble_bounds[2]) / 2,
                (region.bubble_bounds[1] + region.bubble_bounds[3]) / 2,
            )
            off_center_lobe = bool(lobe_rects) and (
                abs((lobe_rects[0][0] + lobe_rects[0][2]) / 2 - bubble_center[0]) > minimum
                or abs((lobe_rects[0][1] + lobe_rects[0][3]) / 2 - bubble_center[1]) > minimum
            )
            if len(lobe_rects) > 1 or off_center_lobe:
                lobe_layout = _fit_lobe_text(
                    region, lobe_rects, text, target, minimum,
                    False, render_config.line_spacing or 0,
                )
                if lobe_layout is None:
                    region.review_reason = region.review_reason or "text_does_not_fit"
                    region.review_required = True
                    region._render_suppressed = False
            if lobe_layout is not None:
                region.font_size, segments = lobe_layout
                region.layout_segments = [
                    {
                        "x": segment["bounds"][0],
                        "y": segment["bounds"][1],
                        "width": segment["bounds"][2] - segment["bounds"][0],
                        "height": segment["bounds"][3] - segment["bounds"][1],
                        "text": segment["text"],
                        "font_size": segment.get("font_size", region.font_size),
                        "lines": segment.get("lines", []),
                    }
                    for segment in segments
                ]
                region._bubble_segments = segments
                if len(segments) == 1 and "box" in segments[0]:
                    region._bubble_box = segments[0]["box"]
                region.layout_bounds = [
                    min(segment["bounds"][0] for segment in segments),
                    min(segment["bounds"][1] for segment in segments),
                    max(segment["bounds"][2] for segment in segments),
                    max(segment["bounds"][3] for segment in segments),
                ]
                region._bubble_points = _points_for_rect(
                    region, region.layout_bounds, image.shape[1], image.shape[0])
                region._bubble_cleanup = interior * 255
            else:
                # Single-lobe bubbles fallback to centered rectangular placement path.
                placement = _find_horizontal_placement(
                    region, region.bubble_bounds, image.shape, target, preferred_minimum,
                    text, False,
                    render_config.line_spacing or 0, [], is_bubble=True)
                if placement is None:
                    # Try relaxed placement in consistent range before emergency fallback
                    placement = _find_horizontal_placement(
                        region, region.bubble_bounds, image.shape, target, preferred_minimum,
                        text, False,
                        render_config.line_spacing or 0, [], is_bubble=False)
                    if placement is None:
                        placement = _find_horizontal_placement(
                            region, region.bubble_bounds, image.shape, minimum, 1,
                            text, False,
                            render_config.line_spacing or 0, [], is_bubble=False)
                    if placement is None:
                        placement = (preferred_minimum, region.bubble_bounds)
                    region.review_reason = region.review_reason or "text_does_not_fit"
            if lobe_layout is None and placement is not None:
                region.font_size, rect = placement
                region.layout_bounds = list(rect)
                region._bubble_points = _points_for_rect(region, rect, image.shape[1], image.shape[0])
                fg, bg = fg_bg_compare(*region.get_font_colors())
                box = text_render.put_text_horizontal(
                    region.font_size, region.get_translation_for_rendering(),
                    max(1, rect[2] - rect[0]), max(1, rect[3] - rect[1]), region.alignment,
                    region.direction == 'hr', fg, bg, region.target_lang,
                    False, render_config.line_spacing,
                    font_size_minimum=1)
                if box is None or not np.any(box[:, :, 3]):
                    box = text_render.put_text_horizontal(
                        max(1, region.font_size), region.get_translation_for_rendering(),
                        max(1, rect[2] - rect[0]), max(1, rect[3] - rect[1]), region.alignment,
                        region.direction == 'hr', fg, bg, region.target_lang,
                        False, render_config.line_spacing)
                if box is not None and np.any(box[:, :, 3]):
                    region._bubble_box = box
                    region._bubble_cleanup = interior * 255
                if box is None or not np.any(box[:, :, 3]) or box.shape[1] > (rect[2] - rect[0] + 3) or box.shape[0] > (rect[3] - rect[1] + 3):
                    region.review_reason = region.review_reason or "text_does_not_fit"
            region.review_required = bool(region.review_reason)
            result.append(region)
    return sorted(result, key=lambda r: r._bubble_source_order)


def constrain_mask(mask, regions):
    mask = mask.copy()
    original = mask.copy()
    preserve = np.zeros_like(mask)
    for region in regions:
        has_trans = bool(getattr(region, "translation", None) and region.translation.strip())
        if getattr(region, "_bubble_interior", None) is None and (not getattr(region, "review_required", False) or has_trans):
            cv2.fillPoly(preserve, [np.asarray(line, np.int32) for line in region.lines], 255)
    for region in regions:
        has_trans = bool(getattr(region, "translation", None) and region.translation.strip())
        interior = getattr(region, "_bubble_interior", None)
        if interior is not None:
            protected = cv2.dilate(region._bubble_restore, np.ones((7, 7), np.uint8))
            mask[protected > 0] = 0
            if not region.review_required or has_trans:
                cleanup = getattr(region, "_bubble_cleanup", None)
                if cleanup is not None and np.any(cleanup):
                    mask = cv2.bitwise_or(mask, cleanup)
                else:
                    mask = cv2.bitwise_or(mask, (interior * 255).astype(np.uint8))
        elif getattr(region, "review_required", False) and not has_trans:
            cv2.fillPoly(mask, [np.asarray(line, np.int32) for line in region.lines], 0)
    return cv2.bitwise_or(mask, cv2.bitwise_and(original, preserve))


def restore_original(canvas, original, regions):
    for region in regions:
        suppressed = getattr(region, "_render_suppressed", False)
        if not getattr(region, "review_required", False) and not suppressed:
            continue
        if not suppressed and getattr(region, "translation", None) and region.translation.strip():
            continue
        interior = getattr(region, "_bubble_restore", None)
        if interior is None:
            lines = getattr(region, "lines", None)
            if lines is not None and len(lines) > 0:
                interior = np.zeros(original.shape[:2], np.uint8)
                cv2.fillPoly(interior, [np.asarray(line, np.int32) for line in lines], 1)
            else:
                continue
        canvas[interior > 0] = original[interior > 0]
    return canvas
