"""Widen white glyph margins when dark free text blends into artwork."""

import numpy as np


def _low_contrast(image, footprint, cleanup, fg):
    (x1, y1, x2, y2), ink = footprint
    sample = ink.copy()
    if cleanup is not None and cleanup.shape[:2] == image.shape[:2]:
        sample &= cleanup[y1:y2, x1:x2] == 0
    pixels = image[y1:y2, x1:x2][sample]
    if len(pixels) < 20:
        return False

    def luminance(rgb):
        srgb = np.asarray(rgb, dtype=np.float32) / 255.0
        linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
        return linear @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

    bg_luminance = luminance(pixels)
    fg_luminance = float(luminance(fg))
    ratio = (np.maximum(bg_luminance, fg_luminance) + 0.05) / (np.minimum(bg_luminance, fg_luminance) + 0.05)
    return np.count_nonzero(ratio < 4.5) / len(ratio) >= 0.20


def apply_free_text_contrast(ctx, free_regions, regions, obstacles, cleanup):
    """Freeze the widest safe white outline after text placement."""
    from .. import fg_bg_compare
    from .free_text_search import _free_text_hard_valid
    from .render_output_validation import _region_render_boxes, _warped_alpha_crop

    image = ctx.img_rgb
    if image is None:
        return
    shape = image.shape[:2]
    for region in free_regions:
        if getattr(region, "_render_suppressed", False) or not getattr(region, "layout_segments", None):
            continue
        fg, bg = fg_bg_compare(*region.get_font_colors())
        if np.mean(fg) >= 128 or np.mean(bg) < 128:
            continue
        boxes, error = _region_render_boxes(region, None, image.shape)
        if error or not boxes:
            continue
        footprints = [_warped_alpha_crop(box, points, image.shape)[0] for box, points in boxes]
        if not any(footprint is not None and _low_contrast(image, footprint, cleanup, fg) for footprint in footprints):
            continue

        qa = dict(getattr(region, "_solver_qa", {}) or {})
        original_width = int(qa.get("layout_stroke_width", 0))
        widest = max(original_width + 1, 2, round(int(region.font_size) * 0.11))
        zone = getattr(region, "_free_text_zone", None)
        bubble = getattr(region, "_bubble_interior", None)
        if bubble is None:
            bubble = getattr(region, "_bubble_mask", None)
        if zone is None and (bubble is None or np.asarray(bubble).shape[:2] != shape):
            continue
        other_text = None
        if zone is not None:
            source = getattr(region, "_free_text_source_mask", None)
            other_text = obstacles.text_mask.astype(bool).copy()
            if source is not None and source.shape == shape:
                other_text &= ~source.astype(bool)
            for other in regions:
                if other is region or getattr(other, "_render_suppressed", False):
                    continue
                other_boxes, _ = _region_render_boxes(other, None, image.shape)
                for box, points in other_boxes:
                    other_footprint, _ = _warped_alpha_crop(box, points, image.shape)
                    if other_footprint is not None:
                        (x1, y1, x2, y2), mask = other_footprint
                        other_text[y1:y2, x1:x2] |= mask

        for width in range(widest, original_width, -1):
            region._solver_qa = {**qa, "layout_stroke_width": width}
            boxes, error = _region_render_boxes(region, None, image.shape)
            if error or not boxes:
                continue
            valid = True
            for box, points in boxes:
                footprint, outside = _warped_alpha_crop(box, points, image.shape)
                if outside or footprint is None:
                    valid = False
                    break
                crop, mask = footprint
                if zone is not None:
                    valid = _free_text_hard_valid(crop, mask, zone, obstacles, other_text)
                else:
                    x1, y1, x2, y2 = footprint[0]
                    valid = not np.any(mask & (np.asarray(bubble)[y1:y2, x1:x2] == 0))
                if not valid:
                    break
            if valid:
                region._solver_qa["contrast_treatment"] = "white_margin"
                break
        else:
            region._solver_qa = qa
