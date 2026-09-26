"""Geometry and pair scoring used by translation remapping."""

from __future__ import annotations

import difflib
from typing import Any, Optional, Set, Tuple

import numpy as np
from shapely.geometry import Polygon, MultiPoint
from shapely.ops import unary_union


def _get_region_id(region: Any) -> str:
    rid = getattr(region, "region_id", None) or getattr(region, "id", None)
    return str(rid) if rid is not None else ""


def _get_source_region_ids(region: Any) -> Set[str]:
    sids = getattr(region, "source_region_ids", None)
    if sids:
        return {str(s) for s in sids if s}
    rid = _get_region_id(region)
    return {rid} if rid else set()


def _get_polygon(region: Any) -> Optional[Polygon]:
    lines = getattr(region, "lines", None)
    if lines is not None:
        try:
            arr = np.asarray(lines, dtype=np.float32)
            if arr.ndim == 3 and arr.shape[0] > 0:
                pts = arr.reshape(-1, 2)
                if len(pts) >= 3:
                    poly = MultiPoint(pts).convex_hull
                    if poly.is_valid and poly.area > 0:
                        return poly
            elif arr.ndim == 2 and arr.shape[0] >= 3:
                poly = Polygon(arr)
                if poly.is_valid and poly.area > 0:
                    return poly
                poly = MultiPoint(arr).convex_hull
                if poly.is_valid and poly.area > 0:
                    return poly
        except Exception:
            pass

    # Fallback to bbox / xyxy
    xyxy = getattr(region, "xyxy", None)
    if xyxy is not None:
        try:
            x1, y1, x2, y2 = xyxy
            if x2 > x1 and y2 > y1:
                return Polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2)])
        except Exception:
            pass

    # Fallback to x, y, width, height
    x = getattr(region, "x", None)
    y = getattr(region, "y", None)
    w = getattr(region, "width", None)
    h = getattr(region, "height", None)
    if x is not None and y is not None and w is not None and h is not None and w > 0 and h > 0:
        return Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])

    return None


def _get_bbox(region: Any) -> Tuple[float, float, float, float]:
    xyxy = getattr(region, "xyxy", None)
    if xyxy is not None:
        try:
            x1, y1, x2, y2 = [float(v) for v in xyxy]
            return (x1, y1, x2, y2)
        except Exception:
            pass

    poly = _get_polygon(region)
    if poly is not None and not poly.is_empty:
        minx, miny, maxx, maxy = poly.bounds
        return (float(minx), float(miny), float(maxx), float(maxy))

    x = float(getattr(region, "x", 0) or 0)
    y = float(getattr(region, "y", 0) or 0)
    w = float(getattr(region, "width", 0) or 0)
    h = float(getattr(region, "height", 0) or 0)
    return (x, y, x + w, y + h)


def _get_centroid(region: Any) -> Tuple[float, float]:
    poly = _get_polygon(region)
    if poly is not None and not poly.is_empty:
        c = poly.centroid
        return (float(c.x), float(c.y))
    x1, y1, x2, y2 = _get_bbox(region)
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _calculate_iou(poly1: Optional[Polygon], poly2: Optional[Polygon]) -> float:
    if poly1 is None or poly2 is None or poly1.is_empty or poly2.is_empty:
        return 0.0
    try:
        if not poly1.intersects(poly2):
            return 0.0
        intersection = poly1.intersection(poly2).area
        union = poly1.union(poly2).area
        return float(intersection / union) if union > 0 else 0.0
    except Exception:
        return 0.0


def _calculate_bbox_iou(bbox1: Tuple[float, float, float, float], bbox2: Tuple[float, float, float, float]) -> float:
    x1 = max(bbox1[0], bbox2[0])
    y1 = max(bbox1[1], bbox2[1])
    x2 = min(bbox1[2], bbox2[2])
    y2 = min(bbox1[3], bbox2[3])

    if x2 <= x1 or y2 <= y1:
        return 0.0

    intersection = (x2 - x1) * (y2 - y1)
    area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
    area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
    union = area1 + area2 - intersection
    return float(intersection / union) if union > 0 else 0.0


def _get_bubble_id(region: Any) -> Optional[str]:
    # Check bubble group assignment or bubble_bounds / group_id
    bid = getattr(region, "group_id", None) or getattr(region, "bubble_id", None)
    if bid is not None:
        return str(bid)
    return None


def _get_text(region: Any) -> str:
    text = getattr(region, "text", "") or getattr(region, "original_text", "") or ""
    return str(text).strip()


def _get_translation(region: Any) -> str:
    trans = getattr(region, "translation", "") or ""
    return str(trans).strip()


def _score_pair(
    old_region: Any,
    new_region: Any,
    old_poly: Optional[Polygon],
    new_poly: Optional[Polygon],
    old_bbox: Tuple[float, float, float, float],
    new_bbox: Tuple[float, float, float, float],
    old_center: Tuple[float, float],
    new_center: Tuple[float, float],
    canvas_diag: float,
) -> Tuple[float, str]:
    """Calculate multi-factor matching score between an old and a new text region."""
    # 1. Provenance / source_region_ids match (Weight: 40%)
    old_sids = _get_source_region_ids(old_region)
    new_sids = _get_source_region_ids(new_region)
    provenance_score = 0.0
    if old_sids and new_sids:
        common = old_sids.intersection(new_sids)
        if common:
            provenance_score = len(common) / max(len(old_sids), len(new_sids))

    # 2. Polygon IoU / BBox IoU (Weight: 25%)
    poly_iou = _calculate_iou(old_poly, new_poly)
    bbox_iou = _calculate_bbox_iou(old_bbox, new_bbox)
    geometric_iou = max(poly_iou, bbox_iou * 0.9)

    # 3. Speech bubble co-membership (Weight: 15%)
    old_bubble = _get_bubble_id(old_region)
    new_bubble = _get_bubble_id(new_region)
    bubble_score = 0.0
    if old_bubble and new_bubble and old_bubble == new_bubble:
        bubble_score = 1.0

    # 4. Normalized center distance (Weight: 10%)
    dx = old_center[0] - new_center[0]
    dy = old_center[1] - new_center[1]
    dist = (dx * dx + dy * dy) ** 0.5
    norm_dist = min(1.0, dist / max(1.0, canvas_diag))
    center_score = max(0.0, 1.0 - (norm_dist * 5.0))  # Decays to 0 at 20% diagonal

    # 5. OCR source text similarity (Weight: 10%)
    old_text = _get_text(old_region)
    new_text = _get_text(new_region)
    text_score = 0.0
    if old_text and new_text:
        text_score = difflib.SequenceMatcher(None, old_text, new_text).ratio()

    # Multi-factor score calculation
    # If provenance matches, it provides a strong foundation.
    # If geometric overlap (IoU) is high (e.g. >0.7), that alone is strong evidence of the same speech bubble/text box.
    if provenance_score > 0:
        score = (
            0.40 * provenance_score
            + 0.30 * geometric_iou
            + 0.15 * bubble_score
            + 0.10 * center_score
            + 0.05 * text_score
        )
    else:
        # Pure geometric/vision matching
        score = (
            0.55 * geometric_iou
            + 0.20 * bubble_score
            + 0.15 * center_score
            + 0.10 * text_score
        )

    # Bonus: If IoU is high (> 0.7), give an extra boost
    if geometric_iou >= 0.8:
        score = min(1.0, score + 0.20)
    elif geometric_iou >= 0.6:
        score = min(1.0, score + 0.10)

    method = "provenance+geometry" if provenance_score > 0 else "geometry"
    if bubble_score > 0:
        method += "+bubble"
    if text_score > 0.8:
        method += "+text"

    return float(score), method
