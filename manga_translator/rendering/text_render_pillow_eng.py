import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from typing import List, Optional, Tuple

from .ballon_extractor import extract_ballon_region, safe_ballon_bounds
from ..utils import TextBlock
from .text_render import select_hyphenator
from .text_render_eng import seg_eng

def _font_text_width(font, text: str) -> int:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


def _split_word_with_hyphens(word: str, font, max_width: int) -> List[str]:
    syllables = []
    hyphenator = select_hyphenator('en_US')
    if hyphenator:
        try:
            syllables = hyphenator.syllables(word)
        except Exception:
            syllables = []
    if not syllables:
        syllables = list(word)

    syllable_pieces: List[str] = []
    current = ''
    for syllable in syllables:
        candidate = current + syllable
        if current and _font_text_width(font, candidate + '-') > max_width:
            syllable_pieces.append(current)
            current = syllable
        else:
            current = candidate
    if current:
        syllable_pieces.append(current)

    result: List[str] = []
    for piece_index, piece in enumerate(syllable_pieces):
        chars = list(piece)
        start = 0
        while start < len(chars):
            best_length = 0
            for length in range(1, len(chars) - start + 1):
                has_more = start + length < len(chars) or piece_index < len(syllable_pieces) - 1
                candidate = ''.join(chars[start:start + length]) + ('-' if has_more else '')
                if _font_text_width(font, candidate) <= max_width or best_length == 0:
                    best_length = length
                else:
                    break
            has_more = start + best_length < len(chars) or piece_index < len(syllable_pieces) - 1
            result.append(''.join(chars[start:start + best_length]) + ('-' if has_more else ''))
            start += best_length
    return result or [word]


def merge_seg_eng(text: str, font, bbox_width, size_ratio=1.0) -> List[str]:
    """Wrap English at words and mark unavoidable word breaks with ``-``."""
    max_width = max(1, int(bbox_width * size_ratio))
    lines = []
    for raw_line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        grouped = seg_eng(raw_line)
        current_line = ''
        for word in grouped:
            if _font_text_width(font, word) > max_width:
                if current_line:
                    lines.append(current_line)
                    current_line = ''
                pieces = _split_word_with_hyphens(word, font, max_width)
                lines.extend(pieces[:-1])
                current_line = pieces[-1]
                continue

            test_line = f"{current_line} {word}" if current_line else word
            if _font_text_width(font, test_line) <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        lines.append(current_line)
    return lines or ['']

def widen_mask_opencv_round(mask, width):
    mask_uint8 = mask.astype(np.uint8)
    kernel_size = 2 * width + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    dilated_mask_uint8 = cv2.dilate(mask_uint8, iterations=1, kernel=kernel)
    return dilated_mask_uint8.astype(bool)


def _check_bbox_collision(b1, b2):
    """Check if two bboxes collide"""
    return not (b1[2] <= b2[0] or b1[0] >= b2[2] or b1[3] <= b2[1] or b1[1] >= b2[3])

def _spiral_points_generator(anchor_x, anchor_y, limit):
    """Generate spiral search points"""
    yield anchor_x, anchor_y
    for radius in range(1, int(limit**0.5)):
        # Top and bottom edges
        for dx in range(-radius, radius+1):
            yield anchor_x + dx, anchor_y - radius
            yield anchor_x + dx, anchor_y + radius
        # Left and right edges (excluding corners)
        for dy in range(-radius+1, radius):
            yield anchor_x - radius, anchor_y + dy
            yield anchor_x + radius, anchor_y + dy

def _find_collision_free_position(bbox_idx, bboxes, anchors, image_bounds, spiral_limit):
    """Find a collision-free position for a bbox"""
    max_x, max_y = image_bounds
    w = bboxes[bbox_idx][2] - bboxes[bbox_idx][0]
    h = bboxes[bbox_idx][3] - bboxes[bbox_idx][1]

    for x, y in _spiral_points_generator(anchors[bbox_idx][0], anchors[bbox_idx][1], spiral_limit):
        candidate = [x, y, x+w, y+h]

        # Check bounds
        if not (0 <= x and 0 <= y and x+w <= max_x and y+h <= max_y):
            continue

        # Check collisions with other boxes
        has_collision = False
        for k, other_bbox in enumerate(bboxes):
            if k != bbox_idx and _check_bbox_collision(candidate, other_bbox):
                has_collision = True
                break

        if not has_collision:
            return candidate

    return None

def solve_collisions_spiral_xyxy(image_shape, initial_bboxes_xyxy, max_iterations=10, spiral_limit=1e5, padding=0):
    """Adjust bounding boxes to avoid overlaps using spiral search"""
    bboxes = [[x1-padding, y1-padding, x2+padding, y2+padding]
              for x1, y1, x2, y2 in initial_bboxes_xyxy]

    if len(bboxes) <= 1:
        return bboxes

    anchors = [(b[0], b[1]) for b in bboxes]

    for _ in range(max_iterations):
        collision_found = False

        for i in range(len(bboxes)):
            for j in range(i+1, len(bboxes)):
                if _check_bbox_collision(bboxes[i], bboxes[j]):
                    collision_found = True
                    new_position = _find_collision_free_position(j, bboxes, anchors, image_shape, spiral_limit)
                    if new_position:
                        bboxes[j] = new_position
                    break

        if not collision_found:
            break

    return bboxes

def _calculate_pillow_font_values(font, words, delimiter=' '):
    sw = max(font.size // 4, 1)
    line_height = font.getmetrics()[0] - font.getmetrics()[1]
    delimiter_len = int(font.getlength(delimiter))
    word_lengths = [int(font.getlength(w)) for w in words]
    base_length = max(word_lengths, default=-1)
    return sw, line_height, delimiter_len, base_length, word_lengths


def _init_pillow_enlarge_ratios(text_regions: List[TextBlock]):
    for region in text_regions:
        if not hasattr(region, 'enlarge_ratio'):
            region.enlarge_ratio = min(max(region.xywh[2] / region.xywh[3], region.xywh[3] / region.xywh[2]) * 1.5, 3)
        if not hasattr(region, 'enlarged_xyxy'):
            region.enlarged_xyxy = region.xyxy.copy()
            w_diff, h_diff = ((region.xywh[2:] * region.enlarge_ratio - region.xywh[2:]) // 2).astype(int)
            region.enlarged_xyxy[[0, 2]] += [-w_diff, w_diff]
            region.enlarged_xyxy[[1, 3]] += [-h_diff, h_diff]


def _create_pillow_text_layer(
    words: List[str],
    font,
    font_color,
    angle: float,
    bbox_center: Tuple[float, float],
    img_w: int,
    img_h: int,
    bounds_padding: int,
):
    words_text = '\n'.join(words)
    line_spacing_px = int(font.size * 0.01)
    padding = (font.size + max(font.size // 4, 1)) * 4

    temp_img = Image.new('RGBA', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    text_bbox = temp_draw.multiline_textbbox((0, 0), words_text, font=font, spacing=line_spacing_px, align="center")
    text_width = text_bbox[2] - text_bbox[0] + padding
    text_height = text_bbox[3] - text_bbox[1] + padding

    text_layer = Image.new('RGBA', (int(text_width), int(text_height)), (0, 0, 0, 0))
    draw_text = ImageDraw.Draw(text_layer)
    draw_text.multiline_text(
        (text_width // 2, text_height // 2), words_text, font=font,
        fill=font_color, align="center", spacing=line_spacing_px, anchor="mm"
    )
    tx1, ty1, tx2, ty2 = draw_text.textbbox(
        (text_width // 2, text_height // 2), words_text, font=font,
        align="center", spacing=line_spacing_px, anchor="mm"
    )

    rotated_text_layer = text_layer.rotate(angle, expand=True, fillcolor=(0, 0, 0, 0))
    rotated_width, rotated_height = rotated_text_layer.size

    paste_x = bbox_center[0] - rotated_width / 2
    paste_y = bbox_center[1] - rotated_height / 2
    paste_x = max(bounds_padding - tx1, min(paste_x, img_w - bounds_padding - tx2))
    paste_y = max(bounds_padding - ty1, min(paste_y, img_h - bounds_padding - ty2))
    paste_x, paste_y = int(paste_x), int(paste_y)

    bbox_entry = [
        [paste_x, paste_y, paste_x + rotated_width, paste_y + rotated_height],
        [paste_x + tx1 - bounds_padding, paste_y + ty1 - bounds_padding,
         paste_x + tx2 + bounds_padding, paste_y + ty2 + bounds_padding]
    ]
    return rotated_text_layer, bbox_entry


def _process_pillow_region(
    region: TextBlock,
    original_img: Optional[np.ndarray],
    font_path: str,
    max_font_size: int,
    ballonarea_thresh: float,
    downscale_constraint: float,
    bounds_padding: int,
    img_w: int,
    img_h: int,
    font_color,
):
    font_size = min(region.font_size, max_font_size)
    ballon_mask, xyxy = extract_ballon_region(original_img, region.xywh, enlarge_ratio=getattr(region, 'enlarge_ratio', 1))
    if isinstance(xyxy, tuple):
        xyxy = list(xyxy)
    font = ImageFont.truetype(font_path, font_size)
    layout_bounds = safe_ballon_bounds(
        original_img, region.xywh, enlarge_ratio=getattr(region, 'enlarge_ratio', 1)
    ) if original_img is not None else None
    if layout_bounds:
        region.layout_bounds = layout_bounds
    layout_width = (layout_bounds[2] - layout_bounds[0]) if layout_bounds else region.xywh[2]
    words = merge_seg_eng(region.translation, font, layout_width)
    if not words:
        return None

    sw, line_height, delimiter_len, base_length, word_lengths = _calculate_pillow_font_values(font, words)
    ballon_area = (ballon_mask > 0).sum()

    region.angle = -region.angle
    if abs(region.angle) > 3:
        ballon_mask = np.array(Image.fromarray(ballon_mask).rotate(region.angle, expand=True))

    line_width = sum(word_lengths) + delimiter_len * max(0, len(word_lengths) - 1)
    region_area = line_width * line_height
    area_ratio = ballon_area / max(region_area, 1)
    if area_ratio < ballonarea_thresh:
        resize_ratio = min(np.sqrt(ballonarea_thresh / area_ratio), (1 / downscale_constraint) ** 2)
        ballon_mask = cv2.resize(ballon_mask, None, fx=resize_ratio, fy=resize_ratio)

    region_x, region_y, region_w, region_h = cv2.boundingRect(cv2.findNonZero(ballon_mask))
    if word_lengths:
        longest_word_idx = max(range(len(word_lengths)), key=lambda i: word_lengths[i])
        base_length_word = words[longest_word_idx]
        if base_length_word:
            lines_needed = len(region.translation) / max(len(base_length_word), 1)
            lines_available = max(1, abs(xyxy[3] - xyxy[1]) // line_height + 1)
            font_size_multiplier = max(min(region_w / (base_length + 2 * sw), lines_available / lines_needed), downscale_constraint)
            if font_size_multiplier < 1:
                font_size = int(font_size * font_size_multiplier)
                font = ImageFont.truetype(font_path, font_size)
                words = merge_seg_eng(region.translation, font, layout_width)
                sw, line_height, delimiter_len, base_length, word_lengths = _calculate_pillow_font_values(font, words)

    bbox_center = ((xyxy[0] + xyxy[2]) / 2, (xyxy[1] + xyxy[3]) / 2)
    rotated_layer, bbox_entry = _create_pillow_text_layer(
        words, font, font_color, region.angle, bbox_center, img_w, img_h, bounds_padding
    )
    return rotated_layer, bbox_entry, sw


def _apply_pillow_strokes(
    img_array: np.ndarray,
    rotated_text_layers: list,
    bboxes: list,
    sws: list,
    stroke_color,
    img_w: int,
    img_h: int,
):
    for rotated_layer, bbox, sw in zip(rotated_text_layers, bboxes, sws):
        paste_x, paste_y = bbox[0][:2]
        text_mask = (np.array(rotated_layer).sum(axis=-1) > 0)
        text_mask = widen_mask_opencv_round(text_mask, sw)

        mask_h, mask_w = text_mask.shape
        y1, y2 = max(0, paste_y), min(img_h, paste_y + mask_h)
        x1, x2 = max(0, paste_x), min(img_w, paste_x + mask_w)

        if y2 > y1 and x2 > x1:
            mask_y1 = max(0, -paste_y)
            mask_y2 = mask_y1 + (y2 - y1)
            mask_x1 = max(0, -paste_x)
            mask_x2 = mask_x1 + (x2 - x1)
            img_array[y1:y2, x1:x2][text_mask[mask_y1:mask_y2, mask_x1:mask_x2], :3] = stroke_color


def render_textblock_list_eng(
    font_path: str,
    img: np.ndarray,
    text_regions: List[TextBlock],
    font_color=(0, 0, 0),
    stroke_color=(255, 255, 255),
    ballonarea_thresh: float = 2.0,
    downscale_constraint: float = 0.85,
    original_img: np.ndarray = None,
    max_font_size: int = 300,
    bounds_padding: int = 3
) -> np.ndarray:
    """Render text blocks onto image"""
    img_pil = Image.fromarray(img)
    _init_pillow_enlarge_ratios(text_regions)

    bboxes, rotated_text_layers, sws = [], [], []
    img_w, img_h = img.shape[1], img.shape[0]

    for region in text_regions:
        item = _process_pillow_region(
            region, original_img, font_path, max_font_size, ballonarea_thresh,
            downscale_constraint, bounds_padding, img_w, img_h, font_color
        )
        if item is None:
            continue
        rotated_layer, bbox_entry, sw = item
        bboxes.append(bbox_entry)
        rotated_text_layers.append(rotated_layer)
        sws.append(sw)

    new_bboxes = solve_collisions_spiral_xyxy((img_w, img_h), [b[1] for b in bboxes])
    for i, new_bbox in enumerate(new_bboxes):
        offset = [new_bbox[j] - bboxes[i][1][j] for j in range(4)]
        for j in range(4):
            bboxes[i][0][j] += int(offset[j])

    img_pil = img_pil.convert("RGB")
    img_array = np.array(img_pil)
    _apply_pillow_strokes(img_array, rotated_text_layers, bboxes, sws, stroke_color, img_w, img_h)

    img_pil = Image.fromarray(img_array)
    for layer, bbox in zip(rotated_text_layers, bboxes):
        img_pil.paste(layer, bbox[0][:2], mask=layer)

    return np.array(img_pil)
