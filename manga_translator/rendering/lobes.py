"""Geometry helpers for splitting connected bubbles into text lobes."""
from typing import Any, Dict, List, Optional

import cv2
import numpy as np


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

    font_cap = min(max_radius * 1.35, max(bh, bw) * 0.38, 54.0)
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
