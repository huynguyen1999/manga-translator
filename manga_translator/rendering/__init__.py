import os
import threading
import cv2
import functools
import numpy as np
from typing import Any, List, Optional, Tuple, Union
from shapely import affinity
from shapely.geometry import Polygon
from tqdm import tqdm

from .ballon_extractor import extract_ballon_region
from . import text_render
from .text_render_eng import render_textblock_list_eng
from .text_render_pillow_eng import render_textblock_list_eng as render_textblock_list_eng_pillow
from .bubble_layout import decode_rendered_box, restore_original, render_positioned_lines
from ..config import Renderer
from .constants import (
    DIRECTION_AUTO,
    DIRECTION_HORIZONTAL,
    DIRECTION_H,
    DIRECTION_VERTICAL,
    DIRECTION_V,
    DIRECTION_HORIZONTAL_LTR,
    ALIGN_CENTER,
    ALIGN_AUTO,
)

_RENDER_LOCK = threading.RLock()
from ..utils import (
    BASE_PATH,
    TextBlock,
    color_difference,
    get_logger,
    rotate_polygons,
    LANGUAGE_ORIENTATION_PRESETS,
)

logger = get_logger('render')

WILD_WORDS_FONT_NAMES = [
    'Wild Words.ttf',
    'wild_words.ttf',
    'wildwords.ttf',
    'Wild Words Roman.ttf',
    'wild_words_roman.ttf',
    'CC Wild Words Roman.ttf',
    'CCWildWords-Roman.ttf',
    'CC Wild Words.ttf',
    'CCWildWords.ttf',
    'Wild Words.otf',
    'wild_words.otf',
    'wildwords.otf',
]

def get_default_eng_font() -> str:
    """Return the default font path for English rendering, preferring Wild Words."""
    fonts_dir = os.path.join(BASE_PATH, 'fonts')
    for name in WILD_WORDS_FONT_NAMES:
        candidate = os.path.join(fonts_dir, name)
        if os.path.isfile(candidate):
            return candidate
    for fallback in ['anime_ace.ttf', 'comic shanns 2.ttf', 'NotoSansMonoCJK-VF.ttf.ttc']:
        candidate = os.path.join(fonts_dir, fallback)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(fonts_dir, 'Wild Words.ttf')

FONT_NAME_MAP = {
    'wildwords': WILD_WORDS_FONT_NAMES,
    'wild_words': WILD_WORDS_FONT_NAMES,
    'wild words': WILD_WORDS_FONT_NAMES,
    'anime_ace': ['anime_ace.ttf'],
    'anime_ace_3': ['anime_ace_3.ttf'],
    'comic_shanns': ['comic shanns 2.ttf', 'comic_shanns_2.ttf', 'comic_shanns.ttf'],
    'arial_unicode': ['Arial-Unicode-Regular.ttf', 'arial_unicode.ttf', 'ArialUnicode.ttf'],
    'noto_sans': ['NotoSansMonoCJK-VF.ttf.ttc', 'NotoSansCJK-VF.ttf.ttc', 'NotoSansCJK.ttc'],
    'msgothic': ['msgothic.ttc'],
    'msyh': ['msyh.ttc'],
}

def resolve_font_name_or_path(font_name_or_path: Optional[str] = None) -> str:
    """Resolve a font name, key, or path to a valid font file path, defaulting to Wild Words."""
    if not font_name_or_path or font_name_or_path in ('default', 'auto', 'Sans-serif'):
        return get_default_eng_font()

    # If it's already an existing file path, return it directly
    if os.path.isfile(font_name_or_path):
        return os.path.abspath(font_name_or_path)

    fonts_dir = os.path.join(BASE_PATH, 'fonts')
    direct_candidate = os.path.join(fonts_dir, font_name_or_path)
    if os.path.isfile(direct_candidate):
        return direct_candidate

    normalized = font_name_or_path.strip().lower().replace('-', '_').replace(' ', '_')
    candidates = FONT_NAME_MAP.get(normalized, [])
    for cand in candidates:
        cand_path = os.path.join(fonts_dir, cand)
        if os.path.isfile(cand_path):
            return cand_path

    # Try case-insensitive lookup in fonts_dir
    if os.path.isdir(fonts_dir):
        for fname in os.listdir(fonts_dir):
            clean_fname = fname.lower().replace('-', '_').replace(' ', '_')
            if clean_fname.startswith(normalized) or normalized in clean_fname:
                full_p = os.path.join(fonts_dir, fname)
                if os.path.isfile(full_p):
                    return full_p

    return get_default_eng_font()


def parse_font_paths(path: str, default: List[str] = None) -> List[str]:
    if path:
        parsed = path.split(',')
        parsed = list(filter(lambda p: os.path.isfile(p), parsed))
    else:
        parsed = default or []
    return parsed

def fg_bg_compare(fg, bg):
    fg_avg = np.mean(fg)
    if color_difference(fg, bg) < 30:
        bg = (255, 255, 255) if fg_avg <= 127 else (0, 0, 0)
    return fg, bg

def count_text_length(text: str) -> float:
    """Calculate text length, treating っッぁぃぅぇぉ as 0.5 characters"""
    half_width_chars = 'っッぁぃぅぇぉ'  
    length = 0.0
    for char in text.strip():
        if char in half_width_chars:
            length += 0.5
        else:
            length += 1.0
    return length


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
def _horizontal_layout(font_size, text, width, height, language, hyphenate, line_spacing):
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
    points[..., 0] = points[..., 0].clip(0, image_width - 1)
    points[..., 1] = points[..., 1].clip(0, image_height - 1)
    return points.astype(np.int64)


def _find_horizontal_placement(
    region,
    base_rect,
    image_shape,
    target_font_size,
    font_size_minimum,
    text,
    hyphenate,
    line_spacing,
    obstacles,
    is_bubble: bool = False,
):
    image_height, image_width = image_shape[:2]
    source_w = max(1, base_rect[2] - base_rect[0])
    source_h = max(1, base_rect[3] - base_rect[1])
    max_w_bound = source_w if is_bubble else image_width
    ideal_w = (
        min(image_width, max(source_w, int(round(source_h * 0.70))))
        if (not is_bubble and source_w < source_h * 0.70)
        else source_w
    )

    # When is_bubble is True, only obstacles that actually intrude into base_rect are relevant.
    # External obstacles outside the bubble CANNOT push or displace text inside the bubble!
    if is_bubble:
        effective_obstacles = [obs for obs in (obstacles or []) if _rects_overlap(base_rect, obs)]
    else:
        effective_obstacles = list(obstacles or [])

    def search_placement(font_start, font_end, active_obstacles):
        f_step = 2 if (font_start - font_end) >= 6 else 1
        font_range = list(range(font_start, font_end - 1, -f_step))
        if font_end not in font_range:
            font_range.append(font_end)
        for candidate_font in font_range:
            if is_bubble:
                w_factors = [1.0, 0.92, 0.84, 0.76, 0.68, 0.60, 0.52, 0.44]
                width_candidates = {max(1, int(round(source_w * wf))) for wf in w_factors}
            else:
                max_w_search = min(image_width, max(source_w * 3, int(round(source_h * 1.8)), 300))
                step = max(candidate_font * 2, int(source_w * 0.35), 48)
                width_candidates = {source_w, max(source_w, 2 * candidate_font), ideal_w}
                width_candidates.update(range(max(source_w, 2 * candidate_font), max_w_search + 1, step))
                if max_w_search < image_width:
                    width_candidates.add(image_width)
            min_preferred_w = min(ideal_w, max(source_w, int(round(source_h * 0.50))))
            sorted_widths = sorted(
                width_candidates,
                key=lambda cw: (0 if cw >= min_preferred_w else 1, abs(cw - ideal_w)),
            )
            for candidate_width in sorted_widths:
                if candidate_width <= 0 or candidate_width > max_w_bound:
                    continue
                needed_width, needed_height = _horizontal_layout(
                    candidate_font,
                    text,
                    candidate_width,
                    image_height - 1,
                    getattr(region, 'target_lang', 'en_US'),
                    hyphenate,
                    line_spacing,
                )
                candidate_width = max(candidate_width, needed_width)
                candidate_height = max(source_h, needed_height) if not is_bubble else max(needed_height, 1)
                if is_bubble and (candidate_width > source_w or candidate_height > source_h):
                    continue
                if candidate_width > image_width or candidate_height > image_height:
                    continue
                bubble_center = getattr(region, '_bubble_center', None) if is_bubble else None
                for rect in _placement_rects(base_rect, candidate_width, candidate_height, image_width, image_height, is_bubble=is_bubble, anchor_center=bubble_center):
                    interior = getattr(region, '_bubble_interior', None)
                    if interior is not None:
                        x1, y1, x2, y2 = map(int, rect)
                        if not np.all(interior[y1:y2 + 1, x1:x2 + 1]):
                            continue
                    if active_obstacles and any(_rects_overlap(rect, obstacle) for obstacle in active_obstacles):
                        continue
                    return candidate_font, rect
        return None

    target_font = int(target_font_size)
    pref_min_font = max(int(np.ceil(target_font * 0.85)), target_font - 3, int(font_size_minimum), 1)
    abs_min_font = max(int(font_size_minimum), 1)

    # Pass 1: Try preferred font range with obstacle avoidance
    res = search_placement(target_font, pref_min_font, effective_obstacles)
    if res is not None:
        return res

    # Pass 2: Relax obstacles in preferred font range
    if is_bubble and effective_obstacles:
        res = search_placement(target_font, pref_min_font, active_obstacles=[])
        if res is not None:
            return res

    # Pass 3: For speech bubbles, relax bubble bounds in preferred font range instead of shrinking font
    if is_bubble:
        def search_relaxed_bubble(font_start, font_end):
            f_step = 2 if (font_start - font_end) >= 6 else 1
            font_range = list(range(font_start, font_end - 1, -f_step))
            if font_end not in font_range:
                font_range.append(font_end)
            bubble_center = getattr(region, '_bubble_center', None) or (
                (base_rect[0] + base_rect[2]) / 2.0,
                (base_rect[1] + base_rect[3]) / 2.0,
            )
            for candidate_font in font_range:
                target_ar_w = max(source_w, int(round(source_h * 0.75)), candidate_font * 4)
                for candidate_width in [target_ar_w, source_w, int(round(source_w * 1.3)), image_width]:
                    if candidate_width <= 0:
                        continue
                    needed_width, needed_height = _horizontal_layout(
                        candidate_font,
                        text,
                        min(candidate_width, image_width),
                        image_height - 1,
                        getattr(region, 'target_lang', 'en_US'),
                        hyphenate,
                        line_spacing,
                    )
                    if needed_width > image_width or needed_height > image_height:
                        continue
                    w = min(image_width, max(needed_width, 1))
                    h = min(image_height, max(needed_height, 1))
                    cx, cy = bubble_center
                    x1 = int(round(cx - w / 2.0))
                    y1 = int(round(cy - h / 2.0))
                    if x1 < 0:
                        x1 = 0
                    if y1 < 0:
                        y1 = 0
                    if x1 + w > image_width:
                        x1 = max(0, image_width - w)
                    if y1 + h > image_height:
                        y1 = max(0, image_height - h)
                    rect = [x1, y1, x1 + w, y1 + h]
                    return candidate_font, rect
            return None

        res = search_relaxed_bubble(target_font, pref_min_font)
        if res is not None:
            return res

    # Pass 4: Emergency downscale to absolute minimum font size
    if pref_min_font > abs_min_font:
        res = search_placement(pref_min_font - 1, abs_min_font, effective_obstacles)
        if res is not None:
            return res
        if is_bubble and effective_obstacles:
            res = search_placement(pref_min_font - 1, abs_min_font, active_obstacles=[])
            if res is not None:
                return res

    return None

def _detect_bubble_rect(
    img: np.ndarray,
    region: 'TextBlock',
    base_rect: list,
    text_regions: List['TextBlock'],
) -> list:
    orig_w, orig_h = region.unrotated_size
    min_bypass_w = max(60, min(140, int(img.shape[1] * 0.07)))
    if orig_w >= orig_h * 0.85 and orig_w >= min_bypass_w:
        return base_rect

    try:
        search_rect = [base_rect[0], base_rect[1], orig_w, orig_h]
        balloon_mask, balloon_window = extract_ballon_region(img, search_rect, enlarge_ratio=1.8)
        non_zero = cv2.findNonZero(balloon_mask)
        if non_zero is None or len(non_zero) <= 10:
            return base_rect

        rx, ry, rw, rh = cv2.boundingRect(non_zero)
        bubble_rect = [
            rx + balloon_window[0],
            ry + balloon_window[1],
            rx + balloon_window[0] + rw,
            ry + balloon_window[1] + rh,
        ]
        tx1, ty1, tx2, ty2 = base_rect
        window_width = balloon_window[2] - balloon_window[0]

        # Analyze boundary touch density to distinguish speech bubble tails from unbordered leaks
        mask_h, mask_w = balloon_mask.shape[:2]
        left_touches = np.count_nonzero(balloon_mask[:, 0])
        right_touches = np.count_nonzero(balloon_mask[:, -1])
        top_touches = np.count_nonzero(balloon_mask[0, :])
        bottom_touches = np.count_nonzero(balloon_mask[-1, :])

        is_left_leak = (left_touches > max(8, int(mask_h * 0.20)))
        is_right_leak = (right_touches > max(8, int(mask_h * 0.20)))
        is_top_leak = (top_touches > max(8, int(mask_w * 0.20)))
        is_bottom_leak = (bottom_touches > max(8, int(mask_w * 0.20)))

        spans_uncontained = (is_left_leak and is_right_leak) or (is_top_leak and is_bottom_leak)
        has_real_border = not spans_uncontained and (
            rw <= window_width - 6 or rx > 3 or (not is_left_leak and not is_right_leak)
        )

        # Ensure the detected bubble doesn't swallow another text region's center
        for other in text_regions:
            if other is region:
                continue
            o_rect = _bounds_from_region(other)
            if o_rect is None:
                continue
            o_cx = (o_rect[0] + o_rect[2]) / 2.0
            o_cy = (o_rect[1] + o_rect[3]) / 2.0
            if bubble_rect[0] <= o_cx <= bubble_rect[2] and bubble_rect[1] <= o_cy <= bubble_rect[3]:
                return base_rect

        if not (
            bubble_rect[0] <= tx2 and bubble_rect[2] >= tx1
            and bubble_rect[1] <= ty2 and bubble_rect[3] >= ty1
            and rw >= max(orig_w, int(round(orig_h * 0.4)))
            and rh >= orig_h * 0.5
            and has_real_border
        ):
            return base_rect

        pad_x = min(15, max(3, int(rw * 0.05)))
        pad_y = min(15, max(3, int(rh * 0.05)))
        safe_rect = [
            max(0, bubble_rect[0] + pad_x),
            max(0, bubble_rect[1] + pad_y),
            min(img.shape[1] - 1, bubble_rect[2] - pad_x),
            min(img.shape[0] - 1, bubble_rect[3] - pad_y),
        ]
        if safe_rect[2] > safe_rect[0] and safe_rect[3] > safe_rect[1]:
            return safe_rect
    except Exception:
        pass
    return base_rect


def _collect_obstacles(
    region: 'TextBlock',
    text_regions: List['TextBlock'],
    reserved_regions,
    placed_rects: list,
) -> list:
    obstacles = []
    for other in text_regions:
        if other is region:
            continue
        other_rect = _bounds_from_region(other)
        if other_rect is not None:
            obstacles.append(other_rect)
    for reserved in reserved_regions or []:
        reserved_rect = _bounds_from_region(reserved)
        if reserved_rect is not None:
            obstacles.append(reserved_rect)
    obstacles.extend(placed_rects)
    return obstacles


def _expand_horizontal_region(
    img: np.ndarray,
    region: 'TextBlock',
    target_font_size: int,
    font_size_minimum: int,
    hyphenate: bool,
    line_spacing: int,
    reserved_regions,
    placed_rects: list,
    text_regions: List['TextBlock'],
):
    prepared_points = getattr(region, '_bubble_points', None)
    if prepared_points is not None and (
        getattr(region, '_bubble_box', None) is not None
        or getattr(region, '_bubble_segments', None)
    ):
        return True, np.asarray(prepared_points), int(region.font_size)
    orig_base_rect = _bounds_from_region(region)
    if orig_base_rect is None:
        orig_base_rect = [0, 0, img.shape[1] - 1, img.shape[0] - 1]
    base_rect = _detect_bubble_rect(img, region, orig_base_rect, text_regions)
    region.layout_bounds = list(base_rect)

    obstacles = _collect_obstacles(region, text_regions, reserved_regions, placed_rects)
    placement = _find_horizontal_placement(
        region,
        base_rect,
        img.shape,
        target_font_size,
        font_size_minimum,
        region.get_translation_for_rendering(),
        hyphenate=hyphenate,
        line_spacing=line_spacing or 0,
        obstacles=obstacles,
        is_bubble=(base_rect != orig_base_rect),
    )
    # If bubble-expanded base_rect failed to place, retry with original region rect
    if placement is None and base_rect != orig_base_rect:
        placement = _find_horizontal_placement(
            region,
            orig_base_rect,
            img.shape,
            target_font_size,
            font_size_minimum,
            region.get_translation_for_rendering(),
            hyphenate=hyphenate,
            line_spacing=line_spacing or 0,
            obstacles=obstacles,
            is_bubble=False,
        )

    if placement is None:
        logger.warning(
            f"Unable to find collision-free horizontal placement for '{region.translation[:40]}'. Flagging for review and using fallback placement."
        )
        region.review_reason = getattr(region, "review_reason", None) or "text_does_not_fit"
        region.review_required = True
        region._render_suppressed = False
        dst_points = _points_for_rect(region, base_rect, img.shape[1], img.shape[0])
        return True, dst_points, max(int(font_size_minimum), 1)

    new_font_size, placed_rect = placement
    region._render_suppressed = False
    dst_points = _points_for_rect(region, placed_rect, img.shape[1], img.shape[0])
    return True, dst_points, new_font_size


def _expand_vertical_region(img: np.ndarray, region: 'TextBlock'):
    used_cols = len(region.texts)
    line_text_list, _ = text_render.calc_vertical(
        region.font_size,
        region.translation,
        max_height=region.unrotated_size[1],
    )
    needed_cols = len(line_text_list)
    if needed_cols <= used_cols:
        return False, None

    scale_x = min(((needed_cols - used_cols) / used_cols) * 1 + 1, 2.0)
    try:
        poly = Polygon(region.unrotated_min_rect[0])
        poly = affinity.scale(poly, xfact=1.0, yfact=scale_x, origin='center')

        pts = np.array(poly.exterior.coords[:4])
        dst_points = rotate_polygons(
            region.center, pts.reshape(1, -1), -region.angle,
            to_int=False
        ).reshape(-1, 4, 2)
        dst_points[..., 0] = dst_points[..., 0].clip(0, img.shape[1] - 1)
        dst_points[..., 1] = dst_points[..., 1].clip(0, img.shape[0] - 1)
        return True, dst_points.astype(np.int64)
    except Exception:
        return False, None


def _scale_region_fallback(
    img: np.ndarray,
    region: 'TextBlock',
    target_font_size: int,
    original_region_font_size: int,
):
    orig_text = getattr(region, "text_raw", region.text)
    char_count_orig = count_text_length(orig_text)
    char_count_trans = count_text_length(region.translation.strip())
    target_scale = 1

    if char_count_orig > 0 and char_count_trans > char_count_orig:
        increase_percentage = (char_count_trans - char_count_orig) / char_count_orig
        font_increase_ratio = 1 + (increase_percentage * 0.3)
        font_increase_ratio = min(1.5, max(1.0, font_increase_ratio))
        target_font_size = int(target_font_size * font_increase_ratio)
        target_scale = max(1, min(1 + increase_percentage * 0.3, 2))

    font_size_scale = (
        (((target_font_size - original_region_font_size) / original_region_font_size) * 0.4 + 1)
        if original_region_font_size > 0 else 1.0
    )
    final_scale = max(font_size_scale, target_scale)
    final_scale = max(1, min(final_scale, 1.1))

    if final_scale <= 1.001:
        return region.min_rect, target_font_size

    try:
        poly = Polygon(region.unrotated_min_rect[0])
        poly = affinity.scale(poly, xfact=final_scale, yfact=final_scale, origin='center')
        scaled_unrotated_points = np.array(poly.exterior.coords[:4])

        dst_points = rotate_polygons(
            region.center, scaled_unrotated_points.reshape(1, -1), -region.angle, to_int=False
        ).reshape(-1, 4, 2)
        dst_points[..., 0] = dst_points[..., 0].clip(0, img.shape[1] - 1)
        dst_points[..., 1] = dst_points[..., 1].clip(0, img.shape[0] - 1)
        dst_points = dst_points.astype(np.int64)
        return dst_points.reshape((-1, 4, 2)), target_font_size
    except Exception:
        return region.min_rect, target_font_size


def resize_regions_to_font_size(
    img: np.ndarray,
    text_regions: List['TextBlock'],
    font_size_fixed: int,
    font_size_offset: int,
    font_size_minimum: int,
    reserved_regions=None,
    line_spacing: int = None,
    hyphenate: bool = True,
):
    """
    Adjust text region size to accommodate font size and translated text length.

    Args:  
        img: Input image
        text_regions: List of text regions to process
        font_size_fixed: Fixed font size (overrides other font parameters)
        font_size_offset: Font size offset
        font_size_minimum: Minimum font size (-1 for auto-calculation)

    Returns:  
        List of adjusted text region bounding boxes
    """
    if font_size_minimum == -1:
        font_size_minimum = round((img.shape[0] + img.shape[1]) / 200)
    font_size_minimum = max(1, font_size_minimum)

    if font_size_fixed is None and len(text_regions) > 1:
        valid_sizes = [
            r.font_size for r in text_regions
            if getattr(r, 'font_size', 0) > 0 and getattr(r, 'translation', None)
        ]
        page_baseline = int(round(np.percentile(valid_sizes, 70))) if valid_sizes else None
    else:
        page_baseline = None

    dst_points_list = []
    placed_rects = []
    for region in text_regions:
        if (
            getattr(region, '_bubble_box', None) is not None
            or getattr(region, '_solver_applied', False)
            or getattr(region, '_free_text_solver_applied', False)
        ):
            pts = getattr(region, '_bubble_points', None)
            if pts is None:
                bounds = getattr(region, 'layout_bounds', None) or getattr(region, 'xyxy', None)
                if bounds is not None:
                    pts = _points_for_rect(region, bounds, img.shape[1], img.shape[0])
                else:
                    pts = getattr(region, 'min_rect', None)
            dst_points_list.append(pts)
            if region.horizontal and not getattr(region, "_render_suppressed", False) and pts is not None:
                placed_rects.append(_bounds_from_region(pts))
            continue

        original_region_font_size = region.font_size if region.font_size > 0 else font_size_minimum
        if font_size_fixed is not None:
            target_font_size = font_size_fixed
        else:
            target_font_size = original_region_font_size + font_size_offset
            if page_baseline is not None:
                target_font_size = max(target_font_size, int(round(page_baseline * 0.88)))
        target_font_size = max(target_font_size, font_size_minimum, 1)

        single_axis_expanded = False
        dst_points = None

        if region.horizontal:
            single_axis_expanded, dst_points, target_font_size = _expand_horizontal_region(
                img, region, target_font_size, font_size_minimum, hyphenate, line_spacing,
                reserved_regions, placed_rects, text_regions
            )

        if region.vertical and not single_axis_expanded:
            single_axis_expanded, dst_points = _expand_vertical_region(img, region)

        if not single_axis_expanded:
            dst_points, target_font_size = _scale_region_fallback(
                img, region, target_font_size, original_region_font_size
            )

        dst_points_list.append(dst_points)
        if region.horizontal and not getattr(region, "_render_suppressed", False):
            placed_rects.append(_bounds_from_region(dst_points))
        region.font_size = int(target_font_size)

    return dst_points_list

async def dispatch(
    img: np.ndarray,
    text_regions: List[TextBlock],
    font_path: str = '',
    font_size_fixed: int = None,
    font_size_offset: int = 0,
    font_size_minimum: int = 0,
    hyphenate: bool = True,
    render_mask: np.ndarray = None,
    line_spacing: int = None,
    disable_font_border: bool = False,
    reserved_regions=None,
    ) -> np.ndarray:

    with _RENDER_LOCK:
        text_render.set_font(font_path)
        _horizontal_layout.cache_clear()
        text_regions = list(filter(lambda region: region.translation, text_regions))

        # Resize regions that are too small
        dst_points_list = resize_regions_to_font_size(
            img,
            text_regions,
            font_size_fixed,
            font_size_offset,
            font_size_minimum,
            reserved_regions=reserved_regions,
            line_spacing=line_spacing,
            hyphenate=hyphenate,
        )

        # Render text
        for region, dst_points in tqdm(zip(text_regions, dst_points_list), '[render]', total=len(text_regions)):
            if getattr(region, "_render_suppressed", False):
                continue
            if render_mask is not None:
                # set render_mask to 1 for the region that is inside dst_points
                cv2.fillConvexPoly(render_mask, dst_points.astype(np.int32), 1)
            img = render(img, region, dst_points, hyphenate, line_spacing, disable_font_border, font_size_minimum=font_size_minimum)
        return img

def _should_render_horizontally(region: TextBlock) -> bool:
    forced_direction = region._direction if hasattr(region, "_direction") else region.direction
    if forced_direction == DIRECTION_AUTO:
        return region.horizontal
    if forced_direction in [DIRECTION_HORIZONTAL, DIRECTION_H]:
        return True
    if forced_direction in [DIRECTION_VERTICAL, DIRECTION_V]:
        return False
    return region.horizontal


def _adjust_vertical_box(temp_box: np.ndarray, r_temp: float, r_orig: float, alignment: str) -> np.ndarray:
    h, w, _ = temp_box.shape
    if r_temp > r_orig:
        h_ext = int(w / (2 * r_orig) - h / 2) if r_orig > 0 else 0
        if h_ext < 0:
            return temp_box.copy()
        box = np.zeros((h + h_ext * 2, w, 4), dtype=np.uint8)
        if alignment in (ALIGN_CENTER, ALIGN_AUTO):
            box[h_ext:h_ext + h, 0:w] = temp_box
        else:
            box[0:h, 0:w] = temp_box
        return box

    w_ext = int((h * r_orig - w) / 2)
    if w_ext < 0:
        return temp_box.copy()
    box = np.zeros((h, w + w_ext * 2, 4), dtype=np.uint8)
    box[0:h, w_ext:w_ext + w] = temp_box
    return box


def _render_text_box(
    region: TextBlock,
    norm_h: np.ndarray,
    norm_v: np.ndarray,
    fg: Tuple[int, int, int],
    bg: Optional[Tuple[int, int, int]],
    hyphenate: bool,
    line_spacing: int,
    font_size_minimum: Optional[int],
    render_horizontally: bool,
    r_orig: float,
) -> np.ndarray:
    if render_horizontally:
        return text_render.put_text_horizontal(
            region.font_size,
            region.get_translation_for_rendering(),
            round(norm_h[0]),
            round(norm_v[0]),
            region.alignment,
            region.direction == DIRECTION_HORIZONTAL_LTR,
            fg,
            bg,
            region.target_lang,
            hyphenate,
            line_spacing,
            font_size_minimum=font_size_minimum,
        )

    temp_box = text_render.put_text_vertical(
        region.font_size,
        region.get_translation_for_rendering(),
        round(norm_v[0]),
        region.alignment,
        fg,
        bg,
        line_spacing,
    )
    h, w, _ = temp_box.shape
    r_temp = w / h
    return _adjust_vertical_box(temp_box, r_temp, r_orig, region.alignment)


def _composite_box_to_image(img: np.ndarray, box: np.ndarray, dst_points: np.ndarray) -> np.ndarray:
    src_points = np.array([[0, 0], [box.shape[1], 0], [box.shape[1], box.shape[0]], [0, box.shape[0]]]).astype(np.float32)

    x, y, w, h = cv2.boundingRect(dst_points.astype(np.int32))
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(img.shape[1], x + w)
    y2 = min(img.shape[0], y + h)
    bw, bh = x2 - x1, y2 - y1
    if bw <= 0 or bh <= 0:
        return img

    dst_local = dst_points.astype(np.float32) - np.array([x1, y1], dtype=np.float32)
    M, _ = cv2.findHomography(src_points, dst_local, cv2.RANSAC, 5.0)
    if M is None:
        M, _ = cv2.findHomography(src_points, dst_local)
    if M is None:
        return img

    rgba_region = cv2.warpPerspective(box, M, (bw, bh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    canvas_region = rgba_region[:, :, :3]
    mask_region = rgba_region[:, :, 3:4].astype(np.float32) / 255.0
    img[y1:y2, x1:x2] = np.clip(
        (img[y1:y2, x1:x2].astype(np.float32) * (1 - mask_region) + canvas_region.astype(np.float32) * mask_region),
        0, 255
    ).astype(np.uint8)
    return img


def _render_frozen_region(img: np.ndarray, region: TextBlock, font_path: str) -> np.ndarray:
    text_render.set_font(font_path)
    fg, bg = fg_bg_compare(*region.get_font_colors())
    for segment in getattr(region, "layout_segments", []) or []:
        lines = segment.get("lines") or []
        x, y = int(segment.get("x", 0)), int(segment.get("y", 0))
        width, height = int(segment.get("width", 0)), int(segment.get("height", 0))
        if width <= 0 or height <= 0:
            continue
        box = decode_rendered_box(segment.get("rendered_png")) if not lines else None
        if not lines and box is None:
            continue
        if lines:
            font_size = int(segment.get("font_size", getattr(region, "font_size", 0)) or 0)
            box = render_positioned_lines(
                lines,
                [x, y, x + width, y + height],
                font_size,
                fg,
                bg,
                float(getattr(region, "line_spacing", 0.0) or 0.0),
                getattr(region, "target_lang", "ENG") or "ENG",
                getattr(region, "direction", "") == "hr",
            )
        if box is not None and np.any(box[:, :, 3]):
            points = _points_for_rect(region, [x, y, x + width, y + height], img.shape[1], img.shape[0])
            img = _composite_box_to_image(img, box, points)
    return img


def render(
    img,
    region: TextBlock,
    dst_points,
    hyphenate,
    line_spacing,
    disable_font_border,
    font_size_minimum: int = None
):
    prepared_segments = getattr(region, '_bubble_segments', None)
    if prepared_segments:
        for segment in prepared_segments:
            points = _points_for_rect(region, segment['bounds'], img.shape[1], img.shape[0])
            img = _composite_box_to_image(img, segment['box'], points)
        return img

    prepared_box = getattr(region, '_bubble_box', None)
    if prepared_box is not None and np.any(prepared_box[:, :, 3]):
        points = getattr(region, '_bubble_points', None)
        if points is not None:
            return _composite_box_to_image(img, prepared_box, points)
        return _composite_box_to_image(img, prepared_box, dst_points)

    fg, bg = region.get_font_colors()
    fg, bg = fg_bg_compare(fg, bg)

    if disable_font_border:
        bg = None

    middle_pts = (dst_points[:, [1, 2, 3, 0]] + dst_points) / 2
    norm_h = np.linalg.norm(middle_pts[:, 1] - middle_pts[:, 3], axis=1)
    norm_v = np.linalg.norm(middle_pts[:, 2] - middle_pts[:, 0], axis=1)
    r_orig = np.mean(norm_h / norm_v)

    render_horizontally = _should_render_horizontally(region)
    box = _render_text_box(
        region, norm_h, norm_v, fg, bg, hyphenate, line_spacing, font_size_minimum, render_horizontally, r_orig
    )
    return _composite_box_to_image(img, box, dst_points)

async def dispatch_eng_render(img_canvas: np.ndarray, original_img: np.ndarray, text_regions: List[TextBlock], font_path: str = '', line_spacing: int = 0, disable_font_border: bool = False) -> np.ndarray:
    if len(text_regions) == 0:
        return img_canvas

    with _RENDER_LOCK:
        if not font_path:
            font_path = get_default_eng_font()
        text_render.set_font(font_path)

        return render_textblock_list_eng(img_canvas, text_regions, line_spacing=line_spacing, size_tol=1.2, original_img=original_img, downscale_constraint=0.8,disable_font_border=disable_font_border)

async def dispatch_eng_render_pillow(img_canvas: np.ndarray, original_img: np.ndarray, text_regions: List[TextBlock], font_path: str = '', line_spacing: int = 0, disable_font_border: bool = False) -> np.ndarray:
    if len(text_regions) == 0:
        return img_canvas

    if not font_path:
        font_path = os.path.join(BASE_PATH, 'fonts/NotoSansMonoCJK-VF.ttf.ttc')
    text_render.set_font(font_path)

    return render_textblock_list_eng_pillow(font_path, img_canvas, text_regions, original_img=original_img, downscale_constraint=0.95)


async def render_page(
    ctx: Any,
    config: Any,
    font_path: Optional[str] = None,
) -> np.ndarray:
    """Canonical production page rendering function.
    
    Renders placed dialogue/free-text directly onto the inpainted canvas
    without reflowing or rewrapping solved lines, and restores original
    pixels for unplaced or review-flagged regions.
    """
    if getattr(ctx, "img_inpainted", None) is None:
        raise ValueError("render_page requires ctx.img_inpainted")

    render_canvas = ctx.img_inpainted.copy()
    if getattr(ctx, "img_rgb", None) is not None:
        render_canvas = restore_original(render_canvas, ctx.img_rgb, ctx.text_regions or [])

    active_font = font_path or getattr(getattr(config, "render", None), "font_path", None) or get_default_eng_font()

    transform_text_case = getattr(getattr(config, "render", None), "transform_text_case", None)
    for region in (ctx.text_regions or []):
        if transform_text_case and getattr(region, "translation", None) and isinstance(region.translation, str):
            region.translation = transform_text_case(region.translation)

    render_regions = [
        region for region in (ctx.text_regions or [])
        if getattr(region, "translation", None)
        and str(region.translation).strip()
    ]
    frozen_regions = [region for region in render_regions if getattr(region, "_layout_frozen", False)]
    frozen_ids = {id(region) for region in frozen_regions}
    bubble_and_legacy = [region for region in render_regions if id(region) not in frozen_ids]

    if not render_regions and (ctx.text_regions or []):
        bubble_and_legacy = list(ctx.text_regions or [])

    render_cfg = getattr(config, "render", None)
    renderer_type = getattr(render_cfg, "renderer", Renderer.default)

    if renderer_type == Renderer.none:
        output = render_canvas
    elif (
        renderer_type in (Renderer.manga2Eng, Renderer.manga2EngPillow)
        and bubble_and_legacy
        and LANGUAGE_ORIENTATION_PRESETS.get(getattr(bubble_and_legacy[0], "target_lang", "ENG")) == "h"
    ):
        if renderer_type == Renderer.manga2EngPillow:
            output = await dispatch_eng_render_pillow(
                render_canvas, ctx.img_rgb, bubble_and_legacy, active_font, render_cfg.line_spacing
            )
        else:
            output = await dispatch_eng_render(
                render_canvas, ctx.img_rgb, bubble_and_legacy, active_font, render_cfg.line_spacing
            )
    else:
        output = await dispatch(
            render_canvas,
            bubble_and_legacy,
            active_font,
            render_cfg.font_size,
            render_cfg.font_size_offset,
            render_cfg.font_size_minimum,
            False,
            getattr(ctx, "render_mask", None),
            render_cfg.line_spacing,
        )

    if renderer_type != Renderer.none:
        with _RENDER_LOCK:
            for region in frozen_regions:
                output = _render_frozen_region(output, region, active_font)

    if getattr(ctx, "img_rgb", None) is not None:
        output = restore_original(output, ctx.img_rgb, ctx.text_regions or [])

    return output
