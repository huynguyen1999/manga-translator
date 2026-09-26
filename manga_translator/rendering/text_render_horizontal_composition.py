"""Horizontal text candidate selection and canvas composition."""

from typing import List, Optional, Tuple

import cv2
import numpy as np

from .text_render import add_color, compact_special_symbols, get_char_glyph
from .text_render_horizontal_layout import calc_horizontal
from .text_render_horizontal_glyphs import put_char_horizontal


def _score_layout_candidate(
    lines: List[str],
    widths: List[int],
    width: int,
    height: int,
    font_size: int,
    sz: int,
    cur_sp_y: int,
    try_hyphen: bool,
    bubble_ratio: float,
    is_centered: bool,
    bg,
) -> Optional[float]:
    aw = max(widths)
    line_h = int(np.ceil(sz * 1.15))
    ah = line_h * len(lines) + cur_sp_y * max(0, len(lines) - 1)
    if aw > width or ah > height:
        return None

    area_util = (aw * ah) / (width * height)
    h_fill = ah / height
    text_ratio = aw / max(1, ah)
    ratio_diff = abs(np.log(max(0.1, text_ratio) / max(0.1, bubble_ratio)))
    font_score = sz / font_size

    penalty = 0.25 if try_hyphen else 0.0
    if len(lines[-1].strip().split()) == 1 and len(lines[-1].strip()) <= 3 and len(lines) > 2:
        penalty += 0.15
    if len(lines) > 2 and bubble_ratio >= 0.75:
        short_single_word_lines = sum(
            1 for l in lines if len(l.strip().split()) == 1 and len(l.strip()) <= 4
        )
        penalty += short_single_word_lines * 0.35

    return area_util * 3.0 + h_fill * 2.5 + font_score * 1.0 - ratio_diff * 0.4 - penalty


def _evaluate_bubble_layout_candidates(
    text: str,
    width: int,
    height: int,
    font_size: int,
    min_font_size: int,
    alignment: str,
    line_spacing: int,
    lang: str,
    hyphenate: bool,
    bg,
):
    words = text.split()
    if len(words) <= 2 or width <= 30 or height <= 30:
        return None

    bubble_ratio = width / max(1, height)
    is_centered = alignment in ('center', 'auto')
    max_font = int(font_size) if min_font_size >= font_size else max(
        int(font_size), min(int(round(font_size * 1.60)), int(height * 0.45), int(width * 0.50), 64)
    )

    if bubble_ratio < 0.55:
        w_factors = [0.35, 0.42, 0.50, 0.58, 0.66, 0.75, 0.85]
    elif bubble_ratio < 0.75:
        w_factors = [0.45, 0.52, 0.60, 0.68, 0.76, 0.84, 0.92]
    elif bubble_ratio < 1.1:
        w_factors = [0.60, 0.68, 0.76, 0.84, 0.92, 0.96]
    else:
        w_factors = [0.72, 0.80, 0.88, 0.96]

    hyphen_passes = [True, False] if hyphenate else [False]
    candidates = []
    font_step = 2 if (max_font - min_font_size) >= 8 else 1
    font_sizes = list(range(max_font, min_font_size - 1, -font_step))
    if min_font_size not in font_sizes:
        font_sizes.append(min_font_size)

    for try_hyphen in hyphen_passes:
        best_cand_score = -999.0
        best_cand_sz = max_font
        for sz in font_sizes:
            if best_cand_score > 3.8 and sz < best_cand_sz - 4:
                break
            cur_sp_y = int(sz * (line_spacing or 0.01))
            for wf in w_factors:
                cw = max(1, int(round(width * wf)))
                lines, widths = calc_horizontal(
                    sz, text, cw, height, lang, try_hyphen, allow_width_expansion=False
                )
                if not lines or not widths:
                    continue
                score = _score_layout_candidate(
                    lines, widths, width, height, font_size, sz, cur_sp_y,
                    try_hyphen, bubble_ratio, is_centered, bg
                )
                if score is None:
                    continue
                candidates.append((score, sz, lines, widths))
                if score > best_cand_score:
                    best_cand_score = score
                    best_cand_sz = sz
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1], candidates[0][2], candidates[0][3]

    return None


def _binary_search_font_size(
    text: str,
    width: int,
    height: int,
    font_size: int,
    min_font_size: int,
    line_spacing: int,
    lang: str,
    hyphenate: bool,
    alignment: str = 'center',
    bg = None,
):
    bubble_ratio = width / max(1, height)
    is_centered = alignment in ('center', 'auto')
    w_eval_factors = [0.75, 0.85, 0.95] if bubble_ratio < 0.75 else [1.0]

    def test_font_size(sz: int, try_hyphen: bool, wf: float = 1.0):
        sp_y = int(sz * (line_spacing or 0.01))
        eval_w = max(1, int(round(width * wf)))
        l_text, l_width = calc_horizontal(
            sz, text, eval_w, height, lang, try_hyphen, allow_width_expansion=False
        )
        line_h = int(np.ceil(sz * 1.15))
        tot_h = line_h * len(l_width) + sp_y * max(0, len(l_width) - 1)
        m_w = max(l_width) if l_width else 0
        fits = (m_w <= width and tot_h <= height)
        return fits, l_text, l_width

    hyphen_passes = [True, False] if hyphenate else [False]
    for try_hyphen in hyphen_passes:
        for wf in w_eval_factors:
            fits, l_text, l_width = test_font_size(font_size, try_hyphen, wf)
            if fits:
                return font_size, l_text, l_width

            low = min_font_size
            high = font_size - 1
            cur_best = None
            while low <= high:
                mid = (low + high) // 2
                fits, l_text, l_width = test_font_size(mid, try_hyphen, wf)
                if fits:
                    cur_best = (mid, l_text, l_width)
                    low = mid + 1
                else:
                    high = mid - 1

            if cur_best is not None:
                return cur_best

    return None


def _render_horizontal_lines(
    line_text_list: List[str],
    line_width_list: List[int],
    font_size: int,
    bg_size: int,
    spacing_y: int,
    reversed_direction: bool,
    alignment: str,
    fg: Tuple[int, int, int],
    bg: Tuple[int, int, int],
    stroke_width: int = None,
):
    canvas_w = max(line_width_list) + (font_size + bg_size) * 2
    canvas_h = font_size * len(line_width_list) + spacing_y * max(0, len(line_width_list) - 1) + (font_size + bg_size) * 2
    canvas_text = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
    canvas_border = canvas_text.copy()

    pen_orig = [font_size + bg_size, font_size + bg_size]
    if reversed_direction:
        pen_orig[0] = canvas_w - bg_size - 10

    for line_text, line_width in zip(line_text_list, line_width_list):
        pen_line = pen_orig.copy()
        if alignment == 'center':
            pen_line[0] += (max(line_width_list) - line_width) // 2 * (-1 if reversed_direction else 1)
        elif alignment == 'right' and not reversed_direction:
            pen_line[0] += max(line_width_list) - line_width
        elif alignment == 'left' and reversed_direction:
            pen_line[0] -= max(line_width_list) - line_width
            pen_line[0] = max(line_width, pen_line[0])

        for c in line_text:
            if reversed_direction:
                cdpt, rot_degree = CJK_Compatibility_Forms_translate(c, 0)
                glyph = get_char_glyph(cdpt, font_size, 0)
                offset_x = glyph.metrics.horiAdvance >> 6
                pen_line[0] -= offset_x
            offset_x = put_char_horizontal(font_size, c, pen_line, canvas_text, canvas_border, border_size=bg_size, stroke_width=stroke_width)
            if not reversed_direction:
                pen_line[0] += offset_x
        pen_orig[1] += spacing_y + font_size

    canvas_border = np.clip(canvas_border, 0, 255)
    line_box = add_color(canvas_text, fg, canvas_border, bg)
    bounding_mask = canvas_border if (bg is not None and np.any(canvas_border)) else canvas_text
    return line_box, bounding_mask


def _crop_and_position_box(
    line_box: np.ndarray,
    bounding_mask: np.ndarray,
    width: int,
    height: int,
    alignment: str,
    reversed_direction: bool,
) -> np.ndarray:
    x, y, w, h = cv2.boundingRect(bounding_mask)
    content = line_box[y:y + h, x:x + w]
    if content.size == 0:
        return np.zeros((height, width, 4), dtype=np.uint8)
    if content.shape[0] > height or content.shape[1] > width:
        return None

    output = np.zeros((height, width, 4), dtype=np.uint8)

    if alignment in ('center', 'auto'):
        offset_x = max(0, (width - content.shape[1]) // 2)
    elif alignment == 'right' and not reversed_direction:
        offset_x = max(0, width - content.shape[1])
    else:
        offset_x = 0
    offset_y = max(0, (height - content.shape[0]) // 2)

    # Clip content to output bounds (overflow is zero-alpha, visually safe)
    paste_h = min(content.shape[0], height - offset_y)
    paste_w = min(content.shape[1], width - offset_x)
    if paste_h > 0 and paste_w > 0:
        output[offset_y:offset_y + paste_h, offset_x:offset_x + paste_w] = content[:paste_h, :paste_w]
    return output


def put_text_horizontal(font_size: int, text: str, width: int, height: int, alignment: str,
                        reversed_direction: bool, fg: Tuple[int, int, int], bg: Tuple[int, int, int],
                        lang: str = 'en_US', hyphenate: bool = True, line_spacing: int = 0,
                        font_size_minimum: int = None, stroke_width: int = None):
    if str(lang).lower() not in {'eng', 'en', 'en_us', 'en-us', 'english'}:
        text = compact_special_symbols(text)
    if not text:
        return None

    width = max(1, int(width))
    height = max(1, int(height))
    effective_min_font = max(
        int(font_size_minimum) if font_size_minimum is not None and font_size_minimum > 0 else 1,
        int(np.ceil(font_size * 0.8)),
        1,
    )
    min_font_size = min(int(font_size), effective_min_font)

    result = _evaluate_bubble_layout_candidates(
        text, width, height, font_size, min_font_size, alignment, line_spacing, lang, hyphenate, bg
    )
    if result is None:
        result = _binary_search_font_size(
            text, width, height, font_size, min_font_size, line_spacing, lang, hyphenate,
            alignment=alignment, bg=bg
        )

    if result is not None:
        font_size, line_text_list, line_width_list = result
    else:
        return None

    bg_size = (max(int(stroke_width), 1) if stroke_width is not None else int(max(font_size * 0.07, 1))) if bg is not None else 0
    spacing_y = int(font_size * (line_spacing or 0.01))

    line_box, bounding_mask = _render_horizontal_lines(
        line_text_list, line_width_list, font_size, bg_size, spacing_y,
        reversed_direction, alignment, fg, bg, stroke_width=stroke_width
    )
    return _crop_and_position_box(line_box, bounding_mask, width, height, alignment, reversed_direction)
