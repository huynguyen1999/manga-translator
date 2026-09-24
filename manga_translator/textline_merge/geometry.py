from dataclasses import asdict, dataclass
from math import acos, degrees
from typing import Any

import numpy as np


@dataclass(frozen=True)
class TextlinePairGeometry:
    direction: str
    angle_delta_degrees: float
    font_ratio: float
    cross_gap_px: float
    cross_gap_font_units: float
    along_overlap_px: float
    along_overlap_ratio: float
    along_gap_px: float
    cross_overlap_ratio: float
    leading_edge_delta_font_units: float
    trailing_edge_delta_font_units: float
    old_distance_px: float | None
    merge_class: str
    score: float
    rejection_reason: str | None

    @property
    def can_merge(self) -> bool:
        return self.merge_class != "rejected"

    def to_dict(self, first_index: int, second_index: int) -> dict[str, Any]:
        return {
            "first_index": first_index,
            "second_index": second_index,
            **asdict(self),
            "accepted": self.can_merge,
        }


def _flow_axis(line) -> np.ndarray:
    points = np.asarray(line.pts, dtype=np.float64)
    if line.direction == "v":
        vector = (points[2] + points[3] - points[0] - points[1]) / 2.0
    else:
        vector = (points[1] + points[2] - points[0] - points[3]) / 2.0
    length = float(np.linalg.norm(vector))
    if length <= 1e-8:
        return np.array((0.0, 1.0) if line.direction == "v" else (1.0, 0.0))
    return vector / length


def _interval(points: np.ndarray, axis: np.ndarray) -> tuple[float, float]:
    projected = points @ axis
    return float(projected.min()), float(projected.max())


def _gap(first: tuple[float, float], second: tuple[float, float]) -> float:
    return max(0.0, max(first[0], second[0]) - min(first[1], second[1]))


def analyze_textline_pair(first, second) -> TextlinePairGeometry:
    """Measure whether two OCR lines form neighboring rows or columns."""
    first_flow = _flow_axis(first)
    second_flow = _flow_axis(second)
    dot = float(np.dot(first_flow, second_flow))
    if dot < 0:
        second_flow = -second_flow
        dot = -dot
    angle_delta = degrees(acos(float(np.clip(dot, -1.0, 1.0))))
    flow = first_flow + second_flow
    flow_length = float(np.linalg.norm(flow))
    flow = flow / flow_length if flow_length > 1e-8 else first_flow
    cross = np.array((-flow[1], flow[0]))

    first_points = np.asarray(first.pts, dtype=np.float64)
    second_points = np.asarray(second.pts, dtype=np.float64)
    first_along = _interval(first_points, flow)
    second_along = _interval(second_points, flow)
    first_cross = _interval(first_points, cross)
    second_cross = _interval(second_points, cross)

    font_a = max(1.0, float(first.font_size))
    font_b = max(1.0, float(second.font_size))
    font_size = min(font_a, font_b)
    font_ratio = max(font_a, font_b) / font_size
    cross_gap = _gap(first_cross, second_cross)
    overlap = max(
        0.0,
        min(first_along[1], second_along[1]) - max(first_along[0], second_along[0]),
    )
    shorter_span = max(1.0, min(first_along[1] - first_along[0], second_along[1] - second_along[0]))
    overlap_ratio = overlap / shorter_span
    along_gap = _gap(first_along, second_along)
    cross_overlap = max(
        0.0,
        min(first_cross[1], second_cross[1]) - max(first_cross[0], second_cross[0]),
    )
    thinner_span = max(1.0, min(first_cross[1] - first_cross[0], second_cross[1] - second_cross[0]))
    cross_overlap_ratio = cross_overlap / thinner_span
    leading_delta = abs(first_along[0] - second_along[0]) / font_size
    trailing_delta = abs(first_along[1] - second_along[1]) / font_size
    cross_gap_fs = cross_gap / font_size
    along_gap_fs = along_gap / font_size
    font_difference = abs(font_a - font_b) / max(font_a, font_b)
    score = (
        1.5 * cross_gap_fs
        + (1.0 - overlap_ratio)
        + 0.4 * font_difference
        + 0.4 * (angle_delta / 15.0)
        + 0.5 * along_gap_fs
    )

    old_distance = None
    try:
        old_distance = float(first.distance(second))
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        pass

    reason = None
    merge_class = "rejected"
    if first.direction != second.direction:
        reason = "orientation_mismatch"
    elif angle_delta > 15.0:
        reason = "angle_mismatch"
    elif font_ratio > 2.0:
        reason = "font_size_mismatch"
    elif (
        cross_gap_fs <= 0.75
        and cross_overlap_ratio <= 0.25
        and overlap_ratio >= 0.85
        and font_ratio <= 1.35
        and angle_delta <= 12.0
    ):
        merge_class = "strong"
    elif cross_gap_fs <= 0.75 and cross_overlap_ratio <= 0.5 and min(leading_delta, trailing_delta) <= 1.5:
        merge_class = "possible"
    elif cross_gap_fs > 0.75:
        reason = "perpendicular_gap"
    elif cross_overlap_ratio > 0.5:
        reason = "perpendicular_overlap"
    elif overlap_ratio < 0.40 and min(leading_delta, trailing_delta) > 1.5:
        reason = "flow_span_mismatch"
    else:
        reason = "weak_alignment"

    return TextlinePairGeometry(
        direction=first.direction if first.direction == second.direction else "mixed",
        angle_delta_degrees=angle_delta,
        font_ratio=font_ratio,
        cross_gap_px=cross_gap,
        cross_gap_font_units=cross_gap_fs,
        along_overlap_px=overlap,
        along_overlap_ratio=overlap_ratio,
        along_gap_px=along_gap,
        cross_overlap_ratio=cross_overlap_ratio,
        leading_edge_delta_font_units=leading_delta,
        trailing_edge_delta_font_units=trailing_delta,
        old_distance_px=old_distance,
        merge_class=merge_class,
        score=score,
        rejection_reason=reason,
    )
