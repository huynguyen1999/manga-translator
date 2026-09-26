"""Shared-bubble grouping and source-owned bubble zones."""

from __future__ import annotations

import math
from typing import Any, List, Optional

import cv2
import numpy as np

from ..bubble_layout import _estimate_adaptive_font_size
from ...utils import is_preserved_region
from .models import BubbleLayoutGroup, LobeGraph
from .profiling import _render_text
from .source_profile import build_original_layout_profile


def _shared_bubble_groups(regions: List[Any]) -> List[BubbleLayoutGroup]:
    """Group regions that share the same prepared bubble interior into BubbleLayoutGroups."""
    groups: List[BubbleLayoutGroup] = []
    for region in regions:
        interior = getattr(region, "_bubble_interior", None)
        bubble_mask = getattr(region, "_bubble_mask", None)
        bubble_id = str(getattr(region, "bubble_id", "") or "")
        if bubble_id:
            shared = next((group for group in groups if group.bubble_id == bubble_id), None)
            if shared is not None:
                shared.regions.append(region)
                continue
        if interior is None or not np.any(interior):
            empty_mask = bubble_mask if bubble_mask is not None else np.zeros((0, 0), dtype=np.uint8)
            empty_interior = interior if interior is not None else np.zeros((0, 0), dtype=np.uint8)
            groups.append(BubbleLayoutGroup(
                bubble_mask=empty_mask, interior=empty_interior, regions=[region],
                bubble_id=bubble_id or None,
            ))
            continue
        for group in groups:
            other_interior = group.interior
            same_mask = (
                bubble_mask is not None
                and group.bubble_mask is not None
                and bubble_mask is group.bubble_mask
            )
            same_interior = (
                other_interior is not None
                and other_interior.shape == interior.shape
                and np.array_equal(other_interior, interior)
            )
            if same_mask or same_interior or (bubble_id and group.bubble_id == bubble_id):
                group.regions.append(region)
                break
        else:
            b_mask = bubble_mask if bubble_mask is not None else interior.copy()
            groups.append(BubbleLayoutGroup(
                bubble_mask=b_mask, interior=interior, regions=[region], bubble_id=bubble_id or None,
            ))
    return groups


def partition_bubble_zones(
    regions: List[Any],
    interior: np.ndarray,
    lobe_graph: Optional[LobeGraph] = None,
    apply_boundary_gap: bool = True,
) -> List[np.ndarray]:
    """Partition a shared speech bubble into geometry-aware source-owned placement zones.

    Uses source centroids and OCR bounding boxes as seeds, with distance transform propagation
    clipped strictly to the bubble interior, direction-aware cost scaling, optional neck penalties
    from LobeGraph, and slight erosion along mutual boundaries to preserve original whitespace rhythm.
    """
    if not regions:
        return []
    if len(regions) == 1:
        return [interior > 0]

    h, w = interior.shape[:2]
    ys, xs = np.nonzero(interior)
    if len(ys) == 0:
        return [np.zeros_like(interior, dtype=bool) for _ in regions]

    profiles = [build_original_layout_profile(reg, interior) for reg in regions]
    costs = []
    y_grid, x_grid = np.ogrid[:h, :w]

    for index, (region, profile) in enumerate(zip(regions, profiles)):
        source_mask = np.zeros_like(interior, dtype=np.uint8)
        lines = getattr(region, "lines", None)
        if lines is not None and len(lines):
            cv2.fillPoly(source_mask, [np.asarray(line, np.int32) for line in lines], 1)
        elif profile is not None:
            bx1, by1, bx2, by2 = profile.bbox
            source_mask[max(0, by1):min(h, by2), max(0, bx1):min(w, bx2)] = 1

        if not np.any(source_mask) and profile is not None:
            cx, cy = int(round(profile.centroid[0])), int(round(profile.centroid[1]))
            if 0 <= cy < h and 0 <= cx < w:
                source_mask[cy, cx] = 1

        # Base Euclidean distance from region source seeds
        dt = cv2.distanceTransform((source_mask == 0).astype(np.uint8), cv2.DIST_L2, 5)

        # Directional scaling: if regions are arranged vertically, vertical separation is primary
        if profile is not None:
            cx, cy = profile.centroid
            # Compute distance from centroid as soft guide
            cent_dist = np.sqrt((x_grid - cx) ** 2 + (y_grid - cy) ** 2)
            combined_d = 0.6 * dt + 0.4 * cent_dist
        else:
            combined_d = dt

        scale = max(
            1.0,
            float(profile.font_size) if profile is not None else 0.0,
            math.sqrt(float(np.count_nonzero(source_mask))),
        )
        cost = combined_d / scale

        # LobeGraph neck penalty: if a pixel crosses a narrow neck away from the seed lobe
        if lobe_graph is not None and getattr(lobe_graph, "necks", None):
            for neck in lobe_graph.necks:
                nx, ny = neck.get("center", (0, 0))
                n_ratio = neck.get("ratio", 1.0)
                if n_ratio < 0.65:
                    neck_d = np.sqrt((x_grid - nx) ** 2 + (y_grid - ny) ** 2)
                    neck_penalty = np.exp(-neck_d / 15.0) * (1.0 - n_ratio) * 20.0
                    cost = cost + neck_penalty

        costs.append(cost)

    owner = np.argmin(np.stack(costs, axis=0), axis=0)
    raw_zones = [(owner == i) & (interior > 0) for i in range(len(regions))]

    if not apply_boundary_gap:
        return raw_zones

    # Apply separation gap between neighboring zones derived from font size & original gap
    avg_font = np.mean([p.font_size for p in profiles if p is not None] or [12.0])
    gap_pixels = max(1, int(round(avg_font * 0.45)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gap_pixels * 2 + 1, gap_pixels * 2 + 1))

    eroded_zones = []
    for i, zone in enumerate(raw_zones):
        # Erode boundary with other zones
        other_union = np.zeros_like(interior, dtype=np.uint8)
        for j, other_zone in enumerate(raw_zones):
            if i != j:
                other_union |= other_zone.astype(np.uint8)

        dilated_others = cv2.dilate(other_union, kernel) > 0
        eroded = zone & (~dilated_others)
        # Ensure zone retains non-empty core if possible
        if not np.any(eroded) and np.any(zone):
            eroded = zone
        eroded_zones.append(eroded)

    return eroded_zones



from .validation import _bbox_validate, _validate_glyph_pixels

from .joint_layout import (
    _MAX_JOINT_LAYOUT_COMBINATIONS,
    _candidate_bbox,
    _candidate_data,
    _choose_joint_layout,
    _ink_centroid,
    _rect_gap,
)

