"""Lightweight source typography measurements from OCR line crops."""

import cv2
import numpy as np


def analyze_source_typography(textlines, image) -> None:
    """Attach image measurements to OCR lines before later stages change the image."""
    if image is None or not textlines:
        return

    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    sizes = [float(line.font_size) for line in textlines if getattr(line, "font_size", 0) > 0]
    page_size = float(np.median(sizes)) if sizes else 0.0

    for line in textlines:
        points = np.asarray(line.pts, dtype=np.int32)
        x1, y1 = np.maximum(points.min(axis=0), 0)
        x2, y2 = np.minimum(points.max(axis=0) + 1, [width, height])
        font_size = float(getattr(line, "font_size", 0) or 0)
        style = {
            "glyph_height": round(font_size, 2),
            "relative_size": round(font_size / page_size, 3) if page_size else None,
            "orientation": getattr(line, "direction", None),
            "rotation_degrees": round(float(np.rad2deg(line.angle)), 2),
            "fg_color": [int(line.fg_r), int(line.fg_g), int(line.fg_b)],
            "bg_color": [int(line.bg_r), int(line.bg_g), int(line.bg_b)],
            "stroke_width_median": None,
            "stroke_width_relative": None,
            "ink_density": None,
        }
        if x2 - x1 < 2 or y2 - y1 < 2:
            line.source_style = style
            continue

        crop = gray[y1:y2, x1:x2]
        polygon = points.copy()
        polygon[:, 0] -= x1
        polygon[:, 1] -= y1
        region = np.zeros(crop.shape, dtype=np.uint8)
        cv2.fillPoly(region, [polygon], 255)
        values = crop[region > 0]
        if values.size < 8:
            line.source_style = style
            continue

        threshold, _ = cv2.threshold(values.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        fg_gray = float(np.mean(style["fg_color"]))
        bg_gray = float(np.mean(style["bg_color"]))
        light_ink = fg_gray > bg_gray if bg_gray != fg_gray else False
        ink = (crop > threshold if light_ink else crop <= threshold) & (region > 0)
        if np.any(ink):
            distances = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
            stroke_width = float(np.median(distances[ink]) * 2)
            style["stroke_width_median"] = round(stroke_width, 2)
            style["stroke_width_relative"] = round(stroke_width / max(font_size, 1.0), 4)
            style["ink_density"] = round(float(np.count_nonzero(ink) / np.count_nonzero(region)), 4)
        line.source_style = style
