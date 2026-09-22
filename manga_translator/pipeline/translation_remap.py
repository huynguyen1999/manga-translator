"""Geometry-first translation remapping between old and new text segmentation passes."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from shapely.geometry import Polygon, MultiPoint
from shapely.ops import unary_union


@dataclass
class RegionRemapDetail:
    new_region_id: str
    old_region_ids: List[str]
    confidence: float
    method: str
    status: str  # "exact", "geometric", "merged", "ambiguous_split", "unmatched"
    translation: str
    review_required: bool = False
    review_reason: Optional[str] = None


@dataclass
class TranslationRemapResult:
    remapped_regions: List[Any]  # TextBlock instances with updated translations & remap metadata
    details: List[RegionRemapDetail] = field(default_factory=list)
    total_new: int = 0
    total_old: int = 0
    matched: int = 0
    needs_review: int = 0
    unmatched: int = 0

    def summary(self) -> Dict[str, Any]:
        return {
            "total_new": self.total_new,
            "total_old": self.total_old,
            "matched": self.matched,
            "needs_review": self.needs_review,
            "unmatched": self.unmatched,
        }


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


def remap_translations(
    old_regions: List[Any],
    new_regions: List[Any],
    canvas_size: Optional[Tuple[int, int]] = None,
) -> TranslationRemapResult:
    """Remap existing translations from old semantic regions to new detector/OCR regions.

    Handles 1:1, 1:N (split), N:1 (merge), and unmatched regions with confidence scoring.
    """
    result = TranslationRemapResult(
        remapped_regions=list(new_regions or []),
        total_new=len(new_regions or []),
        total_old=len(old_regions or []),
    )

    if not new_regions:
        return result

    if not old_regions:
        # All new regions are unmatched
        for region in new_regions:
            region_id = _get_region_id(region)
            setattr(region, "translation", "")
            setattr(region, "translation_source", "none")
            setattr(
                region,
                "translation_remap",
                {
                    "old_region_ids": [],
                    "confidence": 0.0,
                    "method": "none",
                    "status": "unmatched",
                },
            )
            result.details.append(
                RegionRemapDetail(
                    new_region_id=region_id,
                    old_region_ids=[],
                    confidence=0.0,
                    method="none",
                    status="unmatched",
                    translation="",
                    review_required=False,
                )
            )
            result.unmatched += 1
        return result

    # Compute canvas diagonal for normalization
    if canvas_size and canvas_size[0] > 0 and canvas_size[1] > 0:
        canvas_diag = (canvas_size[0] ** 2 + canvas_size[1] ** 2) ** 0.5
    else:
        # Estimate from region bounding boxes
        all_boxes = [_get_bbox(r) for r in (old_regions + new_regions)]
        max_x = max((b[2] for b in all_boxes), default=1000.0)
        max_y = max((b[3] for b in all_boxes), default=1000.0)
        canvas_diag = max(100.0, (max_x ** 2 + max_y ** 2) ** 0.5)

    # Precompute geometric features
    old_polys = [_get_polygon(r) for r in old_regions]
    old_bboxes = [_get_bbox(r) for r in old_regions]
    old_centers = [_get_centroid(r) for r in old_regions]

    new_polys = [_get_polygon(r) for r in new_regions]
    new_bboxes = [_get_bbox(r) for r in new_regions]
    new_centers = [_get_centroid(r) for r in new_regions]

    # Compute cost / affinity matrix [num_old, num_new]
    score_matrix = np.zeros((len(old_regions), len(new_regions)), dtype=np.float32)
    method_matrix = [["" for _ in range(len(new_regions))] for _ in range(len(old_regions))]

    for i, old_r in enumerate(old_regions):
        for j, new_r in enumerate(new_regions):
            score, method = _score_pair(
                old_r,
                new_r,
                old_polys[i],
                new_polys[j],
                old_bboxes[i],
                new_bboxes[j],
                old_centers[i],
                new_centers[j],
                canvas_diag,
            )
            score_matrix[i, j] = score
            method_matrix[i][j] = method

    # Determine candidate associations for N:1 and 1:N
    # 1. Find best old region for each new region
    new_to_old_candidates: Dict[int, List[Tuple[int, float]]] = {}
    for j in range(len(new_regions)):
        candidates = []
        for i in range(len(old_regions)):
            score = float(score_matrix[i, j])
            if score >= 0.40:
                candidates.append((i, score))
        candidates.sort(key=lambda x: x[1], reverse=True)
        new_to_old_candidates[j] = candidates

    # 2. Find best new regions for each old region
    old_to_new_candidates: Dict[int, List[Tuple[int, float]]] = {}
    for i in range(len(old_regions)):
        candidates = []
        for j in range(len(new_regions)):
            score = float(score_matrix[i, j])
            if score >= 0.40:
                candidates.append((j, score))
        candidates.sort(key=lambda x: x[1], reverse=True)
        old_to_new_candidates[i] = candidates

    # Grouping / Matching resolution
    assigned_new: Set[int] = set()
    assigned_old: Set[int] = set()

    # Pass 1: High-confidence 1:1 matches (score >= 0.75 and mutual top choice)
    for j in range(len(new_regions)):
        if j in assigned_new:
            continue
        cands = new_to_old_candidates.get(j, [])
        if not cands:
            continue
        best_old_idx, best_score = cands[0]
        if best_old_idx in assigned_old:
            continue

        # Check if j is also the top choice for this old region
        old_cands = old_to_new_candidates.get(best_old_idx, [])
        if old_cands and old_cands[0][0] == j and best_score >= 0.60:
            # Mutual 1:1 match
            assigned_new.add(j)
            assigned_old.add(best_old_idx)
            old_r = old_regions[best_old_idx]
            new_r = new_regions[j]
            trans = _get_translation(old_r)
            method = method_matrix[best_old_idx][j]

            status = "exact" if best_score >= 0.85 else "geometric"
            review_required = best_score < 0.85
            review_reason = (
                "Automatic remap confidence is below 85%" if review_required else None
            )

            _apply_remap(
                new_r,
                translation=trans,
                old_region_ids=[_get_region_id(old_r)],
                confidence=best_score,
                method=method,
                status=status,
                review_required=review_required,
                review_reason=review_reason,
            )

            result.details.append(
                RegionRemapDetail(
                    new_region_id=_get_region_id(new_r),
                    old_region_ids=[_get_region_id(old_r)],
                    confidence=best_score,
                    method=method,
                    status=status,
                    translation=trans,
                    review_required=review_required,
                    review_reason=review_reason,
                )
            )
            result.matched += 1
            if review_required:
                result.needs_review += 1

    # Pass 2: N:1 Merges (Multiple old regions merged into one new region)
    for j in range(len(new_regions)):
        if j in assigned_new:
            continue
        # Check if multiple unassigned old regions overlap significantly with new region j
        overlapping_old = []
        for i in range(len(old_regions)):
            if i in assigned_old:
                continue
            # Check if old region is within / overlaps new region
            old_p = old_polys[i]
            new_p = new_polys[j]
            if old_p is not None and new_p is not None and not old_p.is_empty and not new_p.is_empty:
                intersection = old_p.intersection(new_p).area
                if intersection > 0 and (intersection / old_p.area) >= 0.50:
                    overlapping_old.append((i, float(score_matrix[i, j])))
            else:
                bbox_iou = _calculate_bbox_iou(old_bboxes[i], new_bboxes[j])
                if bbox_iou >= 0.35:
                    overlapping_old.append((i, float(score_matrix[i, j])))

        if len(overlapping_old) > 1:
            # Sort old regions by vertical position / reading order
            overlapping_old.sort(key=lambda item: (old_centers[item[0]][1], old_centers[item[0]][0]))
            old_indices = [item[0] for item in overlapping_old]
            old_ids = [_get_region_id(old_regions[idx]) for idx in old_indices]
            combined_trans = "\n".join(
                _get_translation(old_regions[idx])
                for idx in old_indices
                if _get_translation(old_regions[idx])
            ).strip()

            avg_score = float(np.mean([item[1] for item in overlapping_old]))
            method = "bubble_merge+geometry"

            for idx in old_indices:
                assigned_old.add(idx)
            assigned_new.add(j)

            new_r = new_regions[j]
            _apply_remap(
                new_r,
                translation=combined_trans,
                old_region_ids=old_ids,
                confidence=avg_score,
                method=method,
                status="merged",
                review_required=True,
                review_reason=f"Merged from {len(old_ids)} previous text regions",
            )

            result.details.append(
                RegionRemapDetail(
                    new_region_id=_get_region_id(new_r),
                    old_region_ids=old_ids,
                    confidence=avg_score,
                    method=method,
                    status="merged",
                    translation=combined_trans,
                    review_required=True,
                    review_reason=f"Merged from {len(old_ids)} previous text regions",
                )
            )
            result.matched += 1
            result.needs_review += 1

    # Pass 3: 1:N Splits (One old region split into multiple new regions)
    for i in range(len(old_regions)):
        if i in assigned_old:
            continue
        # Find all unassigned new regions covered by old region i
        split_new = []
        for j in range(len(new_regions)):
            if j in assigned_new:
                continue
            old_p = old_polys[i]
            new_p = new_polys[j]
            if old_p is not None and new_p is not None and not old_p.is_empty and not new_p.is_empty:
                intersection = old_p.intersection(new_p).area
                if intersection > 0 and (intersection / new_p.area) >= 0.50:
                    split_new.append((j, float(score_matrix[i, j])))
            else:
                bbox_iou = _calculate_bbox_iou(old_bboxes[i], new_bboxes[j])
                if bbox_iou >= 0.35:
                    split_new.append((j, float(score_matrix[i, j])))

        if len(split_new) > 1:
            # We do NOT blindly split the translation text. Mark as ambiguous_split requiring review.
            old_r = old_regions[i]
            old_id = _get_region_id(old_r)
            trans = _get_translation(old_r)
            assigned_old.add(i)

            # Assign to the highest scoring new region with review requirement, leave others empty
            split_new.sort(key=lambda item: item[1], reverse=True)
            primary_j, primary_score = split_new[0]
            assigned_new.add(primary_j)

            primary_r = new_regions[primary_j]
            _apply_remap(
                primary_r,
                translation=trans,
                old_region_ids=[old_id],
                confidence=primary_score * 0.7,
                method="split_primary",
                status="ambiguous_split",
                review_required=True,
                review_reason=f"Region was split into {len(split_new)} segments; translation attached to primary",
            )
            result.details.append(
                RegionRemapDetail(
                    new_region_id=_get_region_id(primary_r),
                    old_region_ids=[old_id],
                    confidence=primary_score * 0.7,
                    method="split_primary",
                    status="ambiguous_split",
                    translation=trans,
                    review_required=True,
                    review_reason=f"Region was split into {len(split_new)} segments; translation attached to primary",
                )
            )
            result.matched += 1
            result.needs_review += 1

            for secondary_j, secondary_score in split_new[1:]:
                assigned_new.add(secondary_j)
                secondary_r = new_regions[secondary_j]
                _apply_remap(
                    secondary_r,
                    translation="",
                    old_region_ids=[old_id],
                    confidence=secondary_score * 0.5,
                    method="split_secondary",
                    status="ambiguous_split",
                    review_required=True,
                    review_reason="Split segment from previous region; translation needs manual alignment",
                )
                result.details.append(
                    RegionRemapDetail(
                    new_region_id=_get_region_id(secondary_r),
                    old_region_ids=[old_id],
                    confidence=secondary_score * 0.5,
                    method="split_secondary",
                    status="ambiguous_split",
                    translation="",
                    review_required=True,
                    review_reason="Split segment from previous region; translation needs manual alignment",
                )
            )
            result.needs_review += 1

    # Pass 4: Remaining greedy matching (Score >= 0.45)
    for j in range(len(new_regions)):
        if j in assigned_new:
            continue
        cands = new_to_old_candidates.get(j, [])
        for old_idx, score in cands:
            if old_idx not in assigned_old and score >= 0.45:
                assigned_new.add(j)
                assigned_old.add(old_idx)
                old_r = old_regions[old_idx]
                new_r = new_regions[j]
                trans = _get_translation(old_r)
                method = method_matrix[old_idx][j]

                status = "geometric"
                review_required = True
                review_reason = f"Geometric match confidence is {int(score * 100)}%"

                _apply_remap(
                    new_r,
                    translation=trans,
                    old_region_ids=[_get_region_id(old_r)],
                    confidence=score,
                    method=method,
                    status=status,
                    review_required=review_required,
                    review_reason=review_reason,
                )

                result.details.append(
                    RegionRemapDetail(
                        new_region_id=_get_region_id(new_r),
                        old_region_ids=[_get_region_id(old_r)],
                        confidence=score,
                        method=method,
                        status=status,
                        translation=trans,
                        review_required=review_required,
                        review_reason=review_reason,
                    )
                )
                result.matched += 1
                result.needs_review += 1
                break

    # Pass 5: Unmatched new regions
    for j in range(len(new_regions)):
        if j in assigned_new:
            continue
        new_r = new_regions[j]
        region_id = _get_region_id(new_r)
        _apply_remap(
            new_r,
            translation="",
            old_region_ids=[],
            confidence=0.0,
            method="none",
            status="unmatched",
            review_required=True,
            review_reason="Translation could not be confidently remapped after OCR rerun",
        )
        result.details.append(
            RegionRemapDetail(
                new_region_id=region_id,
                old_region_ids=[],
                confidence=0.0,
                method="none",
                status="unmatched",
                translation="",
                review_required=True,
                review_reason="Translation could not be confidently remapped after OCR rerun",
            )
        )
        result.unmatched += 1
        result.needs_review += 1

    return result


def _apply_remap(
    region: Any,
    translation: str,
    old_region_ids: List[str],
    confidence: float,
    method: str,
    status: str,
    review_required: bool,
    review_reason: Optional[str] = None,
) -> None:
    setattr(region, "translation", translation)
    setattr(region, "translation_source", "remapped" if translation else "none")
    setattr(
        region,
        "translation_remap",
        {
            "old_region_ids": old_region_ids,
            "confidence": round(float(confidence), 3),
            "method": method,
            "status": status,
        },
    )
    if review_required:
        setattr(region, "review_required", True)
        if review_reason:
            setattr(region, "review_reason", review_reason)
    else:
        setattr(region, "review_required", False)
        setattr(region, "review_reason", None)
