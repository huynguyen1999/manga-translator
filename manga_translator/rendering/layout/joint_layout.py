"""Page-level selection of non-overlapping layout candidates."""

from __future__ import annotations

import itertools
import math
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .font_policy import page_font_penalty as _page_font_penalty
from .models import LayoutCandidate, PlacedLine
from .profiling import get_solver_profile
from .raster import _candidate_cropped_visual_masks, _cropped_masks_overlap
from .free_text_typography import _mask_metrics

_MAX_JOINT_LAYOUT_COMBINATIONS = 4096


def _ink_centroid(
    lines: List[PlacedLine],
    font_size: int,
) -> Tuple[float, float, int]:
    """Calculate centroid of the placed lines candidate block."""
    total_x = 0.0
    total_y = 0.0
    total_n = 0
    for line in lines:
        n = max(1, line.width * line.height)
        total_x += (line.x + line.width / 2.0) * n
        total_y += (line.y + line.height / 2.0) * n
        total_n += n

    if total_n == 0:
        return 0.0, 0.0, 0
    return total_x / total_n, total_y / total_n, total_n


def _rect_gap(first: Tuple[int, int, int, int], second: Tuple[int, int, int, int]) -> float:
    dx = max(first[0] - second[2], second[0] - first[2], 0)
    dy = max(first[1] - second[3], second[1] - first[3], 0)
    return math.hypot(dx, dy)


def _candidate_bbox(candidate: LayoutCandidate) -> Tuple[int, int, int, int]:
    if candidate.layout_bounds is not None: return candidate.layout_bounds
    return (
        min(line.x for line in candidate.lines),
        min(line.y for line in candidate.lines),
        max(line.x + line.width for line in candidate.lines),
        max(line.y + line.height for line in candidate.lines),
    )


def _candidate_data(candidate: LayoutCandidate, image_shape: Tuple[int, int]):
    crop_box, glyph_mask, _, _ = _candidate_cropped_visual_masks(candidate, 0, image_shape)
    if candidate.status.startswith("free_text") and np.any(glyph_mask):
        metrics = _mask_metrics(glyph_mask, (crop_box[0], crop_box[1]))
        return crop_box, glyph_mask, metrics["bbox"], metrics["centroid"]
    return crop_box, glyph_mask, _candidate_bbox(candidate), _ink_centroid(candidate.lines, candidate.font_size)



def _choose_joint_layout(
    plans: List[_RegionLayoutPlan],
    image_shape: Tuple[int, int],
) -> Optional[Tuple[LayoutCandidate, ...]]:
    profile = get_solver_profile()
    started = perf_counter()
    profile.joint_layout_plans += len(plans)
    candidate_counts = [len(plan.candidates) for plan in plans]
    profile.joint_layout_candidates += sum(candidate_counts)
    profile.joint_layout_candidate_counts.extend(candidate_counts)
    combinations = math.prod(len(plan.candidates) for plan in plans) if plans else 0
    profile.joint_layout_cartesian_product += combinations
    try:
        if not plans or any(not plan.candidates for plan in plans):
            return None

        # ponytail: cap Cartesian search at 4096 combinations; callers use the
        # existing greedy collision-safe fallback for larger candidate spaces.
        if combinations > _MAX_JOINT_LAYOUT_COMBINATIONS:
            return None

        candidate_cache: Dict[int, Any] = {}
        for plan in plans:
            for cand in plan.candidates:
                cid = id(cand)
                if cid not in candidate_cache:
                    candidate_cache[cid] = _candidate_data(cand, image_shape)

        best: Optional[Tuple[float, Tuple[LayoutCandidate, ...]]] = None
        for combination in itertools.product(*(plan.candidates for plan in plans)):
            profile.joint_layout_combinations_tested += 1
            collision = False
            for i in range(len(combination)):
                box_i, res_i, _, _ = candidate_cache[id(combination[i])]
                for j in range(i + 1, len(combination)):
                    box_j, res_j, _, _ = candidate_cache[id(combination[j])]
                    if _cropped_masks_overlap(box_i, res_i, box_j, res_j):
                        collision = True
                        break
                if collision:
                    break
            if collision:
                profile.joint_layout_collisions_rejected += 1
                continue

            score = sum(
                candidate.penalty + _page_font_penalty(candidate.font_size, getattr(plan.region, "_font_policy_diagnostics", {}).get("page_baseline") if plan.region else None)
                for plan, candidate in zip(plans, combination)
            )
            for first in range(len(combination)):
                first_cand = combination[first]
                _, _, first_bbox, first_centroid = candidate_cache[id(first_cand)]
                first_profile = plans[first].source_profile
                for second in range(first + 1, len(combination)):
                    second_cand = combination[second]
                    _, _, second_bbox, second_centroid = candidate_cache[id(second_cand)]
                    second_profile = plans[second].source_profile
                    if first_profile is None or second_profile is None:
                        continue

                    source_dx = second_profile.centroid[0] - first_profile.centroid[0]
                    source_dy = second_profile.centroid[1] - first_profile.centroid[1]
                    rendered_dx = second_centroid[0] - first_centroid[0]
                    rendered_dy = second_centroid[1] - first_centroid[1]
                    font_scale = max(1.0, (first_cand.font_size + second_cand.font_size) / 2.0)
                    if source_dy * rendered_dy < 0:
                        score += 150.0
                    if source_dx * rendered_dx < 0:
                        score += 80.0
                    source_distance = math.hypot(source_dx, source_dy)
                    rendered_distance = math.hypot(rendered_dx, rendered_dy)
                    score += abs(rendered_distance - source_distance) / font_scale * 3.0
                    source_gap = _rect_gap(first_profile.bbox, second_profile.bbox)
                    rendered_gap = _rect_gap(first_bbox, second_bbox)
                    score += abs(rendered_gap - source_gap) / font_scale * 3.5

            if best is None or score < best[0]:
                best = (score, combination)

        profile.joint_layout_winner_score = best[0] if best is not None else None
        return best[1] if best is not None and math.isfinite(best[0]) else None
    finally:
        profile.joint_layout_ms += (perf_counter() - started) * 1000.0
