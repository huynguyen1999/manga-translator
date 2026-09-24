"""Crop-local text rasterization shared by layout, collision checks, and debug."""

from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from .models import CandidateRaster, LayoutCandidate, PlacedLine


_LINE_ALPHA_CACHE: Dict[Tuple[object, str, int, int], Optional[np.ndarray]] = {}


def _render_line_alpha(line: PlacedLine, font_size: int) -> Optional[np.ndarray]:
    global _LINE_ALPHA_CACHE
    from manga_translator.rendering import text_render

    key = (text_render.FONT_SELECTION_KEY, line.text, line.width, font_size)
    if key in _LINE_ALPHA_CACHE:
        cached = _LINE_ALPHA_CACHE[key]
        return cached.copy() if cached is not None else None

    try:
        canvas = np.zeros((font_size + 4, line.width + font_size + 4), dtype=np.uint8)
        border = canvas.copy()
        pen = [0, font_size]
        for c in line.text:
            adv = text_render.put_char_horizontal(font_size, c, pen, canvas, border, border_size=0)
            pen[0] += adv
        if len(_LINE_ALPHA_CACHE) >= 1024:
            first_k = next(iter(_LINE_ALPHA_CACHE))
            del _LINE_ALPHA_CACHE[first_k]
        _LINE_ALPHA_CACHE[key] = canvas
        return canvas.copy()
    except Exception:
        if len(_LINE_ALPHA_CACHE) >= 1024:
            first_k = next(iter(_LINE_ALPHA_CACHE))
            del _LINE_ALPHA_CACHE[first_k]
        _LINE_ALPHA_CACHE[key] = None
        return None

def _candidate_crop_box(candidate: LayoutCandidate, stroke_width: int) -> Tuple[int, int, int, int]:
    if not candidate.lines:
        return (0, 0, 0, 0)
    b_left = min(line.x for line in candidate.lines)
    b_top = min(line.y for line in candidate.lines)
    b_right = max(line.x + line.width for line in candidate.lines)
    b_bottom = max(line.y + line.height for line in candidate.lines)
    radius = max(1, int(stroke_width))
    pad = radius + 2
    return b_left - pad, b_top - pad, b_right + pad, b_bottom + pad


def _rasterize_candidate_crop(
    candidate: LayoutCandidate,
    stroke_width: int,
    crop_box: Tuple[int, int, int, int],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rasterize masks into the requested crop, which may extend off-page."""
    cx1, cy1, cx2, cy2 = crop_box
    ch = cy2 - cy1
    cw = cx2 - cx1
    if ch <= 0 or cw <= 0:
        return np.zeros((0, 0), bool), np.zeros((0, 0), bool), np.zeros((0, 0), bool)

    ink_crop = np.zeros((ch, cw), dtype=bool)
    block_crop = np.zeros((ch, cw), dtype=bool)
    radius = max(1, int(stroke_width))

    for line in candidate.lines:
        lx1 = max(0, line.x - cx1)
        ly1 = max(0, line.y - cy1)
        lx2 = min(cw, line.x + line.width - cx1)
        ly2 = min(ch, line.y + line.height - cy1)
        if lx2 > lx1 and ly2 > ly1:
            block_crop[ly1:ly2, lx1:lx2] = True

        alpha = _render_line_alpha(line, candidate.font_size)
        if alpha is None:
            if lx2 > lx1 and ly2 > ly1:
                ink_crop[ly1:ly2, lx1:lx2] = True
        else:
            glyph = alpha > 127
            ay, ax = glyph.shape
            gx1 = max(0, line.x - cx1)
            gy1 = max(0, line.y - cy1)
            gx2 = min(cw, line.x + ax - cx1)
            gy2 = min(ch, line.y + ay - cy1)
            if gx2 > gx1 and gy2 > gy1:
                ink_crop[gy1:gy2, gx1:gx2] |= glyph[:gy2 - gy1, :gx2 - gx1]

    if stroke_width > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        vis_crop = cv2.dilate(ink_crop.astype(np.uint8), kernel) > 0
    else:
        vis_crop = ink_crop.copy()
    return ink_crop, vis_crop, block_crop


def rasterize_candidate(candidate: LayoutCandidate, stroke_width: int) -> CandidateRaster:
    """Rasterize a typography candidate once in page-independent coordinates."""
    crop_box = _candidate_crop_box(candidate, stroke_width)
    ink, visual, block = _rasterize_candidate_crop(candidate, stroke_width, crop_box)
    ys, xs = np.nonzero(ink)
    if len(xs):
        ink_bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        centroid = (float(xs.mean()), float(ys.mean()))
    else:
        ink_bbox = (0, 0, 0, 0)
        centroid = (0.0, 0.0)
    for mask in (ink, visual, block):
        mask.setflags(write=False)
    return CandidateRaster(crop_box, ink, visual, block, ink_bbox, centroid, int(len(xs)))


def _candidate_raster_at_offset(
    raster: CandidateRaster,
    dx: int,
    dy: int,
    image_shape: Tuple[int, int],
) -> Tuple[Tuple[int, int, int, int], np.ndarray, np.ndarray, np.ndarray]:
    """Translate cached masks and clip their view to page bounds without rerasterizing."""
    x1, y1, x2, y2 = raster.crop_box
    x1 += dx
    y1 += dy
    x2 += dx
    y2 += dy
    h, w = image_shape[:2]
    cx1, cy1, cx2, cy2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if cx1 >= cx2 or cy1 >= cy2:
        empty = np.zeros((0, 0), dtype=bool)
        return (cx1, cy1, cx2, cy2), empty, empty, empty
    sx1, sy1, sx2, sy2 = cx1 - x1, cy1 - y1, cx2 - x1, cy2 - y1
    return (
        (cx1, cy1, cx2, cy2),
        raster.ink_crop[sy1:sy2, sx1:sx2],
        raster.visual_crop[sy1:sy2, sx1:sx2],
        raster.block_crop[sy1:sy2, sx1:sx2],
    )


def _candidate_cropped_visual_masks(
    candidate: LayoutCandidate,
    stroke_width: int,
    image_shape: Tuple[int, int],
) -> Tuple[Tuple[int, int, int, int], np.ndarray, np.ndarray, np.ndarray]:
    """Rasterize candidate ink, stroke, and line blocks in a page-clipped crop."""
    box = _candidate_crop_box(candidate, stroke_width)
    ink, visual, block = _rasterize_candidate_crop(candidate, stroke_width, box)
    raster = CandidateRaster(box, ink, visual, block, (0, 0, 0, 0), (0.0, 0.0), 0)
    return _candidate_raster_at_offset(raster, 0, 0, image_shape)

def _candidate_visual_masks(
    candidate: LayoutCandidate,
    image_shape: Tuple[int, int],
    stroke_width: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rasterize candidate ink_mask, visual_mask (ink + stroke), and block_mask."""
    h, w = image_shape[:2]
    crop_box, ink_c, vis_c, blk_c = _candidate_cropped_visual_masks(candidate, stroke_width, (h, w))
    cx1, cy1, cx2, cy2 = crop_box
    ink_mask = np.zeros((h, w), dtype=bool)
    visual_mask = np.zeros((h, w), dtype=bool)
    block_mask = np.zeros((h, w), dtype=bool)
    if cx2 > cx1 and cy2 > cy1:
        ink_mask[cy1:cy2, cx1:cx2] = ink_c
        visual_mask[cy1:cy2, cx1:cx2] = vis_c
        block_mask[cy1:cy2, cx1:cx2] = blk_c
    return ink_mask, visual_mask, block_mask

def _candidate_global_glyph_mask(
    candidate: LayoutCandidate,
    shape: Tuple[int, int],
) -> np.ndarray:
    """Rasterize a candidate in page coordinates for collision checks."""
    mask = np.zeros(shape, dtype=bool)
    h, w = shape
    for line in candidate.lines:
        alpha = _render_line_alpha(line, candidate.font_size)
        if alpha is None:
            glyph = np.ones((line.height, line.width), dtype=bool)
        else:
            glyph = alpha > 127
        ay, ax = glyph.shape
        x1, y1 = max(0, line.x), max(0, line.y)
        x2, y2 = min(w, line.x + ax), min(h, line.y + ay)
        if x1 < x2 and y1 < y2:
            mask[y1:y2, x1:x2] |= glyph[y1 - line.y:y2 - line.y, x1 - line.x:x2 - line.x]
    return mask

def _cropped_masks_overlap(first_box, first_mask, second_box, second_mask) -> bool:
    """Check raster overlap using only the intersection of two candidate crops."""
    x1 = max(first_box[0], second_box[0])
    y1 = max(first_box[1], second_box[1])
    x2 = min(first_box[2], second_box[2])
    y2 = min(first_box[3], second_box[3])
    if x1 >= x2 or y1 >= y2:
        return False
    first = first_mask[y1 - first_box[1]:y2 - first_box[1], x1 - first_box[0]:x2 - first_box[0]]
    second = second_mask[y1 - second_box[1]:y2 - second_box[1], x1 - second_box[0]:x2 - second_box[0]]
    return bool(np.any(first & second))
