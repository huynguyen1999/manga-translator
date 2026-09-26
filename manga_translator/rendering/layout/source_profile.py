"""Source OCR geometry and typography profile extraction."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from .models import OriginalLayoutProfile

def _source_geometry_font_size(region: Any) -> int:
    quads = np.asarray(getattr(region, "lines", []), dtype=np.float32)
    thicknesses = []
    if quads.size:
        for quad in quads.reshape(-1, 4, 2):
            width = float(quad[:, 0].max() - quad[:, 0].min())
            height = float(quad[:, 1].max() - quad[:, 1].min())
            if width > 0 and height > 0:
                thicknesses.append(min(width, height))
    return int(round(float(np.median(thicknesses)) * 0.9)) if thicknesses else 0


def _effective_source_font_size(region: Any, reported_size: Any) -> int:
    """Reject implausibly tiny positive OCR font estimates using source-line thickness."""
    reported = max(0, int(round(float(reported_size or 0))))
    estimates = [np.sqrt((b[2] - b[0]) * (b[3] - b[1]) / len(t)) for s in getattr(region, "source_regions", None) or []
        if (b := s.get("bbox")) and len(b) == 4 and b[2] > b[0] and b[3] > b[1]
        for t in ["".join(str(s.get("source_text") or getattr(region, "text", "") or "").split())] if len(t) > 1]
    text_geometry_size = int(round(float(np.median(estimates)))) if estimates else 0
    if reported <= 0:
        return text_geometry_size
    if text_geometry_size and reported > text_geometry_size * 3:
        return text_geometry_size
    measured = _source_geometry_font_size(region)
    return max(reported, measured) if measured and reported < measured * 0.6 else reported


def build_original_layout_profile(region: Any, interior: np.ndarray) -> Optional[OriginalLayoutProfile]:
    """Derive the original typography from captured OCR line geometry.

    Everything needed is already in the capture (region.lines quads,
    region.font_size, bubble interior) — no extra ML model required.
    """
    region_lines = getattr(region, "lines", None)
    if region_lines is None or len(region_lines) == 0:
        return None

    quads = np.asarray(region_lines, dtype=np.float32).reshape(-1, 4, 2)
    texts = getattr(region, "texts", None) or []
    line_entries: List[Dict[str, Any]] = []
    x1_all = y1_all = float("inf")
    x2_all = y2_all = float("-inf")
    for idx, quad in enumerate(quads):
        x1, y1 = float(quad[:, 0].min()), float(quad[:, 1].min())
        x2, y2 = float(quad[:, 0].max()), float(quad[:, 1].max())
        w, h = x2 - x1, y2 - y1
        x1_all, y1_all = min(x1_all, x1), min(y1_all, y1)
        x2_all, y2_all = max(x2_all, x2), max(y2_all, y2)
        line_entries.append({
            "text": texts[idx] if idx < len(texts) else "",
            "center_x": (x1 + x2) / 2.0,
            "center_y": (y1 + y2) / 2.0,
            "width": w,
            "height": h,
        })

    reported_font = getattr(region, "source_font_size", None)
    if reported_font is None:
        reported_font = getattr(region, "font_size", -1)
    font_size = float(_effective_source_font_size(region, reported_font))
    if font_size <= 0:
        font_size = float(max(1, _source_geometry_font_size(region)))

    weights = [max(1.0, e["width"] * e["height"]) for e in line_entries]
    wsum = sum(weights) if weights else 1.0
    centroid = (
        sum(e["center_x"] * w for e, w in zip(line_entries, weights)) / wsum,
        sum(e["center_y"] * w for e, w in zip(line_entries, weights)) / wsum,
    )

    block_w = x2_all - x1_all
    block_h = y2_all - y1_all
    interior_area = float(np.count_nonzero(interior))
    occupancy = (block_w * block_h) / interior_area if interior_area > 0 else 0.0

    # Calculate normalized centroid relative to bubble bounding box
    ys, xs = np.nonzero(interior)
    if len(ys) > 0 and len(xs) > 0:
        bx1, bx2 = float(xs.min()), float(xs.max()) + 1.0
        by1, by2 = float(ys.min()), float(ys.max()) + 1.0
        bw = max(1.0, bx2 - bx1)
        bh = max(1.0, by2 - by1)
        u = (centroid[0] - bx1) / bw
        v = (centroid[1] - by1) / bh
        norm_centroid = (max(0.0, min(1.0, u)), max(0.0, min(1.0, v)))
    else:
        norm_centroid = (0.5, 0.5)

    return OriginalLayoutProfile(
        font_size=font_size,
        line_count=len(line_entries),
        lines=line_entries,
        centroid=centroid,
        bbox=(int(x1_all), int(y1_all), int(x2_all), int(y2_all)),
        block_width=block_w,
        block_height=block_h,
        occupancy=occupancy,
        normalized_centroid=norm_centroid,
    )
