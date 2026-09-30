"""Associate OCR source geometry with saved panel detections."""

from typing import Any, Iterable

import numpy as np

def match_panel_to_source(source_mask: np.ndarray, panel_detections: Iterable[Any]) -> Any:
    """Return the best substantial panel match, or None for CV fallback."""
    ys, xs = np.nonzero(source_mask)
    if not len(xs):
        return None

    source_bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    source_area = (source_bbox[2] - source_bbox[0]) * (source_bbox[3] - source_bbox[1])
    cx, cy = float(xs.mean()), float(ys.mean())
    candidates, centroid_fallbacks = [], []
    for panel in panel_detections:
        bounds = getattr(panel, "xyxy", None)
        if bounds is None or len(bounds) != 4:
            continue
        px1, py1, px2, py2 = map(float, bounds)
        overlap = max(0.0, min(source_bbox[2], px2) - max(source_bbox[0], px1)) * max(
            0.0, min(source_bbox[3], py2) - max(source_bbox[1], py1)
        )
        coverage = overlap / source_area if source_area else 0.0
        area = max(0.0, px2 - px1) * max(0.0, py2 - py1)
        confidence = float(getattr(panel, "confidence", 0.9))
        if coverage >= 0.50:
            candidates.append((coverage, -area, confidence, panel))
        elif px1 <= cx <= px2 and py1 <= cy <= py2:
            pixel_coverage = np.count_nonzero(
                (xs >= px1) & (xs < px2) & (ys >= py1) & (ys < py2)
            ) / len(xs)
            if pixel_coverage >= 0.50:
                centroid_fallbacks.append((pixel_coverage, -area, confidence, panel))

    matches = candidates or centroid_fallbacks
    return max(matches, key=lambda item: item[:3])[3] if matches else None
