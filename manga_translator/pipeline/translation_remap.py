"""Geometry-first translation remapping between old and new text segmentation passes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np


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




from .translation_remap_geometry import (
    _calculate_bbox_iou,
    _calculate_iou,
    _get_bbox,
    _get_bubble_id,
    _get_centroid,
    _get_polygon,
    _get_region_id,
    _get_source_region_ids,
    _get_text,
    _get_translation,
    _score_pair,
)


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
