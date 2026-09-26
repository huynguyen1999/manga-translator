"""Small placement geometry and cached text measurement helpers."""

import functools

import numpy as np

from . import text_render
from ..utils import rotate_polygons


def _bounds_from_region(region):
    """Return an axis-aligned [x1, y1, x2, y2] for a region or polygon."""
    if hasattr(region, 'xyxy'):
        points = np.asarray(region.xyxy)
    elif hasattr(region, 'lines'):
        points = np.asarray(region.lines)
    else:
        points = np.asarray(region)
    if points.size < 4:
        return None
    points = points.reshape(-1, 2)
    return [
        int(np.floor(points[:, 0].min())),
        int(np.floor(points[:, 1].min())),
        int(np.ceil(points[:, 0].max())),
        int(np.ceil(points[:, 1].max())),
    ]


def _rects_overlap(left, right):
    return min(left[2], right[2]) > max(left[0], right[0]) and min(left[3], right[3]) > max(left[1], right[1])


@functools.lru_cache(maxsize=4096)
def _horizontal_layout(font_key, font_size, text, width, height, language, hyphenate, line_spacing):
    """Cache wrapping separately for each selected font chain."""
    lines, widths = text_render.calc_horizontal(
        font_size,
        text,
        max(1, int(width)),
        max(1, int(height)),
        language=language,
        hyphenate=hyphenate,
        allow_width_expansion=False,
    )
    spacing = int(font_size * (line_spacing or 0.01))
    # FreeType glyphs include ascender/descender pixels beyond the nominal size.
    line_height = int(np.ceil(font_size * 1.15)) * len(lines) + spacing * max(0, len(lines) - 1)
    return max(widths) if widths else 0, line_height


def _placement_rects(anchor, width, height, image_width, image_height, is_bubble: bool = False, anchor_center: tuple = None):
    """Yield page-bounded rectangles that still contain the source region."""
    x1, y1, x2, y2 = anchor
    max_x = image_width
    max_y = image_height
    if width > max_x or height > max_y:
        return []

    if anchor_center is None:
        anchor_center = ((x1 + x2) / 2, (y1 + y2) / 2)
    if is_bubble:
        # Strictly contained within bubble bounds: anchor center must stay inside bubble
        if width > (x2 - x1) or height > (y2 - y1):
            return []
        cx = int(round(anchor_center[0] - width / 2))
        cy = int(round(anchor_center[1] - height / 2))
        cx = max(x1, min(x2 - width, cx))
        cy = max(y1, min(y2 - height, cy))
        candidates = [[cx, cy, cx + width, cy + height]]
        for dy in (-4, 4, -8, 8, -12, 12):
            for dx in (-4, 4, -8, 8):
                ncx = max(x1, min(x2 - width, cx + dx))
                ncy = max(y1, min(y2 - height, cy + dy))
                cand = [ncx, ncy, ncx + width, ncy + height]
                if cand not in candidates:
                    candidates.append(cand)
        return candidates

    # For non-bubble regions, prefer centered placement, then fine nudges, and only then extremities
    x_centered = max(0, min(max_x - width, int(round(anchor_center[0] - width / 2))))
    y_centered = max(0, min(max_y - height, int(round(anchor_center[1] - height / 2))))

    min_cand_x = max(0, min(x1, x2 - width))
    max_cand_x = min(max_x - width, max(x1, x2 - width))
    x_set = {x_centered, min_cand_x, max_cand_x}
    x_step = max(6, int(width * 0.06))
    for off in range(x_step, int(width), x_step):
        if x_centered - off >= min_cand_x:
            x_set.add(x_centered - off)
        if x_centered + off <= max_cand_x:
            x_set.add(x_centered + off)

    min_cand_y = max(0, min(y1, y2 - height))
    max_cand_y = min(max_y - height, max(y1, y2 - height))
    y_set = {y_centered, min_cand_y, max_cand_y}
    y_step = max(6, int(height * 0.06))
    for off in range(y_step, int(height), y_step):
        if y_centered - off >= min_cand_y:
            y_set.add(y_centered - off)
        if y_centered + off <= max_cand_y:
            y_set.add(y_centered + off)

    candidates = []
    seen = set()
    for candidate_x in x_set:
        for candidate_y in y_set:
            rect = (candidate_x, candidate_y, candidate_x + width, candidate_y + height)
            if rect in seen:
                continue
            seen.add(rect)
            if rect[0] <= x1 and rect[1] <= y1 and rect[2] >= x2 and rect[3] >= y2:
                distance = ((rect[0] + rect[2]) / 2 - anchor_center[0]) ** 2 + ((rect[1] + rect[3]) / 2 - anchor_center[1]) ** 2
                candidates.append((distance, list(rect)))
    candidates.sort(key=lambda item: item[0])
    return [rect for _, rect in candidates]


def _points_for_rect(region, rect, image_width, image_height):
    x1, y1, x2, y2 = rect
    points = np.array([[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]], dtype=np.float32)
    if abs(getattr(region, 'angle', 0)) > 3:
        center = np.array([(x1 + x2) / 2, (y1 + y2) / 2])
        points = rotate_polygons(center, points.reshape(1, -1), -region.angle, to_int=False).reshape(-1, 4, 2)
    return points
