from typing import List
import cv2
import numpy as np
from time import perf_counter

from .text_mask_utils import complete_mask_fill, complete_mask
from ..utils import TextBlock, Quadrilateral
from ..utils.bubble import is_ignore


def _dilate_components(mask: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Dilate each connected component in a padded crop and compose the result."""
    binary = np.where(mask > 0, 1, 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    result = np.zeros_like(mask)
    height, width = mask.shape[:2]
    pad = max(kernel.shape)
    for label in range(1, count):
        x, y, w, h = [int(value) for value in stats[label, :4]]
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(width, x + w + pad), min(height, y + h + pad)
        component = (labels[y0:y1, x0:x1] == label).astype(np.uint8) * 255
        dilated = cv2.dilate(component, kernel, iterations=1)
        result[y0:y1, x0:x1] = cv2.bitwise_or(result[y0:y1, x0:x1], dilated)
    return result


async def dispatch(text_regions: List[TextBlock], raw_image: np.ndarray, raw_mask: np.ndarray, method: str = 'fit_text', dilation_offset: int = 0, ignore_bubble: int = 0, verbose: bool = False,kernel_size:int=3, profile=None) -> np.ndarray:
    if raw_image is None:
        return np.zeros((100, 100), dtype=np.uint8)
    if raw_mask is None:
        raw_mask = np.zeros((raw_image.shape[0], raw_image.shape[1]), dtype=np.uint8)

    # Larger sized mask images will probably have crisper and thinner mask segments due to being able to fit the text pixels better
    # so we dont want to size them down as much to not lose information
    mask_h = raw_mask.shape[0] if raw_mask.shape[0] > 0 else 1
    scale_factor = max(min((raw_mask.shape[0] - raw_image.shape[0] / 3) / mask_h, 1), 0.5)

    stage_start = perf_counter()
    img_resized = cv2.resize(raw_image, (int(raw_image.shape[1] * scale_factor), int(raw_image.shape[0] * scale_factor)), interpolation = cv2.INTER_LINEAR)
    mask_resized = cv2.resize(raw_mask, (int(raw_image.shape[1] * scale_factor), int(raw_image.shape[0] * scale_factor)), interpolation = cv2.INTER_LINEAR)
    if profile is not None:
        timings = profile.setdefault("timings_ms", {})
        timings["input_resize_ms"] = timings.get("input_resize_ms", 0.0) + (perf_counter() - stage_start) * 1000.0
        profile.setdefault("workload", {}).update({
            "image_pixels": int(raw_image.shape[0] * raw_image.shape[1]),
            "resized_pixels": int(img_resized.shape[0] * img_resized.shape[1]),
        })

    mask_resized[mask_resized > 0] = 255
    textlines = []
    for region in text_regions:
        for l in region.lines:
            q = Quadrilateral(l * scale_factor, '', 0)
            textlines.append(q)

    stage_start = perf_counter()
    final_mask = complete_mask(
        img_resized, mask_resized, textlines,
        dilation_offset=dilation_offset, kernel_size=kernel_size, profile=profile,
    ) if method == 'fit_text' else complete_mask_fill([txtln.aabb.xywh for txtln in textlines])
    if profile is not None:
        timings = profile.setdefault("timings_ms", {})
        timings["mask_refinement_ms"] = timings.get("mask_refinement_ms", 0.0) + (perf_counter() - stage_start) * 1000.0
    if final_mask is None:
        final_mask = np.zeros((raw_image.shape[0], raw_image.shape[1]), dtype = np.uint8)
    else:
        final_mask = cv2.resize(final_mask, (raw_image.shape[1], raw_image.shape[0]), interpolation = cv2.INTER_LINEAR)
        final_mask[final_mask > 0] = 255

    # Refinement may discard small components (dots/thin strokes) or erode
    # detected glyphs. Keep confident source-mask pixels inside selected text
    # polygons, at original resolution; never fill entire text rectangles.
    stage_start = perf_counter()
    source_mask = cv2.resize(raw_mask, (raw_image.shape[1], raw_image.shape[0]), interpolation=cv2.INTER_NEAREST)
    selected = np.zeros(raw_image.shape[:2], dtype=np.uint8)
    for region in text_regions:
        cv2.fillPoly(selected, [np.asarray(line, dtype=np.int32) for line in region.lines], 255)
    source_mask = np.where((source_mask >= 128) & (selected > 0), 255, 0).astype(np.uint8)
    final_mask = cv2.bitwise_or(final_mask, source_mask)
    if profile is not None:
        timings = profile.setdefault("timings_ms", {})
        timings["source_mask_recovery_ms"] = timings.get("source_mask_recovery_ms", 0.0) + (perf_counter() - stage_start) * 1000.0
    # Recover visible glyphs that the detector missed, without erasing the whole
    # text polygon. OCR provides foreground/background colors for this split.
    stage_start = perf_counter()
    color_mask = np.zeros_like(selected)
    for region in text_regions:
        lines = getattr(region, 'lines', None)
        if lines is None or len(lines) == 0:
            continue
        fg, bg = region.get_font_colors()
        fg = np.asarray(fg, dtype=np.float32)
        bg = np.asarray(bg, dtype=np.float32)
        if np.linalg.norm(fg - bg) < 10:
            continue
        all_pts = np.concatenate([np.asarray(line, dtype=np.int32).reshape(-1, 2) for line in region.lines], axis=0)
        rx, ry, rw, rh = cv2.boundingRect(all_pts)
        rx1, ry1 = max(0, rx), max(0, ry)
        rx2, ry2 = min(raw_image.shape[1], rx + rw), min(raw_image.shape[0], ry + rh)
        if rx2 <= rx1 or ry2 <= ry1:
            continue

        crop_pixels = raw_image[ry1:ry2, rx1:rx2]
        region_crop_mask = np.zeros((ry2 - ry1, rx2 - rx1), dtype=np.uint8)
        for line in region.lines:
            offset_line = np.asarray(line, dtype=np.int32).reshape(-1, 2) - np.array([rx1, ry1], dtype=np.int32)
            cv2.fillPoly(region_crop_mask, [offset_line], 255)

        # Keep the existing distance calculation while releasing each RGB
        # scratch array before building the other one.
        crop_pixels = raw_image[ry1:ry2, rx1:rx2].astype(np.float32)
        fg_diff = crop_pixels - fg
        fg_dist_sq = np.sum(fg_diff * fg_diff, axis=2)
        del fg_diff
        bg_diff = crop_pixels - bg
        bg_dist_sq = np.sum(bg_diff * bg_diff, axis=2)
        del bg_diff, crop_pixels

        matched = (region_crop_mask > 0) & (fg_dist_sq <= bg_dist_sq)
        color_mask[ry1:ry2, rx1:rx2][matched] = 255
    final_mask = cv2.bitwise_or(final_mask, color_mask)
    if profile is not None:
        timings = profile.setdefault("timings_ms", {})
        timings["color_recovery_ms"] = timings.get("color_recovery_ms", 0.0) + (perf_counter() - stage_start) * 1000.0

    if ignore_bubble < 1 or ignore_bubble > 50:
        return final_mask

    # bubble
    stage_start = perf_counter()
    kernel_size = max(1, int(max(final_mask.shape) * 0.025))
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    final_mask = _dilate_components(final_mask, kernel)
    if profile is not None:
        workload = profile.setdefault("workload", {})
        workload["dilated_pixels"] = int(np.count_nonzero(final_mask))
        workload["full_page_dilation_calls"] = 0
    # border
    contours, _ = cv2.findContours(final_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        textblock = np.ascontiguousarray(raw_image[y:y + h, x:x + w])
        if is_ignore(textblock, ignore_bubble):
            cv2.drawContours(final_mask, [cnt], -1, 0, -1)

    if profile is not None:
        timings = profile.setdefault("timings_ms", {})
        timings["ignore_bubble_ms"] = timings.get("ignore_bubble_ms", 0.0) + (perf_counter() - stage_start) * 1000.0

    return final_mask
