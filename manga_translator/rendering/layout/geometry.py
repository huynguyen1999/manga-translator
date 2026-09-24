"""Pure mask-to-geometry helpers used by production and dev tooling."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .models import BandSlot, LobeGraph, PlacementTarget, ScanInterval, ZoneShapeProfile


def _normalize_bubble_mask(mask: Optional[np.ndarray], min_component_area: int = 16) -> np.ndarray:
    if mask is None:
        return np.zeros((0, 0), dtype=np.uint8)
    source = np.asarray(mask)
    if source.ndim == 3:
        source = np.max(source, axis=2)
    if source.ndim != 2 or not source.size:
        return np.zeros(source.shape[:2] if source.ndim >= 2 else (0, 0), dtype=np.uint8)

    clean = (source > 0).astype(np.uint8)
    if not np.any(clean):
        return clean
    clean = cv2.morphologyEx(
        clean,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )

    padded = cv2.copyMakeBorder(clean, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flooded = padded.copy()
    cv2.floodFill(
        flooded,
        np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8),
        (0, 0),
        2,
    )
    padded[(padded == 0) & (flooded == 0)] = 1
    clean = padded[1:-1, 1:-1]

    count, labels, stats, _ = cv2.connectedComponentsWithStats(clean, 8)
    if count <= 1:
        return clean
    keep = np.zeros_like(clean)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= min_component_area:
            keep[labels == label] = 1
    return keep if np.any(keep) else clean


def _centerline_neck(
    distance: np.ndarray,
    clean: np.ndarray,
    first: Tuple[int, int],
    second: Tuple[int, int],
) -> Optional[Dict[str, Any]]:
    x1, y1 = first
    x2, y2 = second
    endpoint_radius = min(float(distance[y1, x1]), float(distance[y2, x2]))
    if endpoint_radius <= 0:
        return None
    samples = max(5, int(round(math.hypot(x2 - x1, y2 - y1))))
    values: List[float] = []
    points: List[Tuple[int, int]] = []
    for t in np.linspace(0.0, 1.0, samples):
        x = int(round(x1 + t * (x2 - x1)))
        y = int(round(y1 + t * (y2 - y1)))
        if not (0 <= y < clean.shape[0] and 0 <= x < clean.shape[1] and clean[y, x]):
            return None
        values.append(float(distance[y, x]))
        points.append((x, y))
    if len(values) < 3:
        return None
    middle = int(np.argmin(values[1:-1])) + 1
    neck_radius = values[middle]
    return {
        "center": [int(points[middle][0]), int(points[middle][1])],
        "width": round(neck_radius * 2.0, 2),
        "ratio": round(neck_radius / endpoint_radius, 3),
    }


def _candidate_lobe_centers(
    clean: np.ndarray, distance: np.ndarray, peak_ratio: float
) -> List[Tuple[float, int, int]]:
    max_radius = float(distance.max()) if np.any(distance) else 0.0
    if max_radius <= 0:
        return []
    threshold = max(2.0, max_radius * peak_ratio)
    kernel_size = max(3, int(round(min(clean.shape) * 0.03)))
    if kernel_size % 2 == 0:
        kernel_size += 1
    local_max = (distance >= threshold) & (
        distance >= cv2.dilate(distance, np.ones((kernel_size, kernel_size), np.uint8))
    )
    count, labels, _stats, _centroids = cv2.connectedComponentsWithStats(
        local_max.astype(np.uint8), 8
    )
    peaks = []
    for label in range(1, count):
        pixels = labels == label
        flat = int(np.argmax(np.where(pixels, distance, -1)))
        y, x = np.unravel_index(flat, distance.shape)
        peaks.append((float(distance[y, x]), int(x), int(y)))
    return sorted(peaks, reverse=True)


def _label_adjacency(labels: np.ndarray) -> List[Tuple[int, int]]:
    pairs = set()
    for left, right in ((labels[:, :-1], labels[:, 1:]), (labels[:-1, :], labels[1:, :])):
        active = (left > 0) & (right > 0) & (left != right)
        for first, second in zip(left[active].tolist(), right[active].tolist()):
            pairs.add(tuple(sorted((int(first), int(second)))))
    return sorted(pairs)


def _neck_for_adjacent_lobes(
    distance: np.ndarray,
    clean: np.ndarray,
    labels: np.ndarray,
    centers: List[Tuple[int, int]],
    first: int,
    second: int,
) -> Optional[Dict[str, Any]]:
    metric = _centerline_neck(distance, clean, centers[first - 1], centers[second - 1])
    if metric is not None:
        return metric
    contacts = []
    for left, right in ((labels[:, :-1], labels[:, 1:]), (labels[:-1, :], labels[1:, :])):
        active = ((left == first) & (right == second)) | ((left == second) & (right == first))
        contacts.extend(np.argwhere(active).tolist())
    if not contacts:
        return None
    point = min(contacts, key=lambda item: float(distance[item[0], item[1]]))
    endpoint_radius = min(
        float(distance[centers[first - 1][1], centers[first - 1][0]]),
        float(distance[centers[second - 1][1], centers[second - 1][0]]),
    )
    if endpoint_radius <= 0:
        return None
    neck_radius = float(distance[point[0], point[1]])
    return {
        "center": [int(point[1]), int(point[0])],
        "width": round(neck_radius * 2.0, 2),
        "ratio": round(neck_radius / endpoint_radius, 3),
    }


def _watershed_labels(
    clean: np.ndarray, distance: np.ndarray, peaks: List[Tuple[float, int, int]]
) -> np.ndarray:
    if len(peaks) <= 1:
        return clean.astype(np.int32)
    markers = np.zeros(clean.shape, dtype=np.int32)
    markers[clean == 0] = 1
    for index, (_radius, x, y) in enumerate(peaks, start=2):
        markers[y, x] = index
    elevation = cv2.normalize(distance, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    elevation = cv2.cvtColor(255 - elevation, cv2.COLOR_GRAY2BGR)
    cv2.watershed(elevation, markers)
    labels = np.where(clean & (markers >= 2), markers - 1, 0).astype(np.int32)
    missing = np.argwhere(clean & (labels == 0))
    if len(missing):
        centers = np.asarray([(x, y) for _radius, x, y in peaks], dtype=np.float32)
        points = missing[:, [1, 0]].astype(np.float32)
        labels[missing[:, 0], missing[:, 1]] = np.argmin(
            ((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2), axis=1
        ) + 1
    return labels


def _merge_false_lobes(
    labels: np.ndarray,
    centers: List[Tuple[int, int]],
    distance: np.ndarray,
    clean: np.ndarray,
) -> np.ndarray:
    parent = list(range(len(centers)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        first, second = find(first), find(second)
        if first != second:
            parent[second] = first

    for first, second in _label_adjacency(labels):
        metric = _neck_for_adjacent_lobes(distance, clean, labels, centers, first, second)
        if metric is not None and metric["ratio"] >= 0.70:
            union(first - 1, second - 1)
    if all(find(index) == index for index in range(len(parent))):
        return labels
    remap: Dict[int, int] = {}
    next_label = 1
    merged = np.zeros_like(labels)
    for label in range(1, len(centers) + 1):
        root = find(label - 1)
        if root not in remap:
            remap[root] = next_label
            next_label += 1
        merged[labels == label] = remap[root]
    return merged


def build_lobe_graph(
    mask: np.ndarray,
    *,
    min_component_area: int = 16,
    peak_ratio: float = 0.28,
) -> LobeGraph:
    """Convert one bubble mask into deterministic lobe and neck geometry."""
    clean = _normalize_bubble_mask(mask, min_component_area=min_component_area)
    distance = (
        cv2.distanceTransform(clean, cv2.DIST_L2, 5).astype(np.float32)
        if clean.size
        else np.zeros_like(clean, dtype=np.float32)
    )
    if not np.any(clean):
        return LobeGraph(clean, distance, np.zeros_like(clean, dtype=np.int32), [], [], [], [], [])

    peaks = _candidate_lobe_centers(clean, distance, peak_ratio)
    selected: List[Tuple[float, int, int]] = []
    for candidate in peaks:
        radius, x, y = candidate
        duplicate = False
        for previous_radius, previous_x, previous_y in selected:
            if (x - previous_x) ** 2 + (y - previous_y) ** 2 < (
                1.25 * max(radius, previous_radius)
            ) ** 2:
                duplicate = True
                break
            neck = _centerline_neck(distance, clean, (x, y), (previous_x, previous_y))
            if neck is not None and neck["ratio"] >= 0.70:
                duplicate = True
                break
        if not duplicate:
            selected.append(candidate)

    labels = _watershed_labels(clean, distance, selected)
    centers = []
    for label in sorted(int(value) for value in np.unique(labels) if value > 0):
        pixels = labels == label
        flat = int(np.argmax(np.where(pixels, distance, -1)))
        y, x = np.unravel_index(flat, distance.shape)
        centers.append((int(x), int(y)))

    labels = _merge_false_lobes(labels, centers, distance, clean)
    lobe_masks = []
    final_centers = []
    capacities = []
    for label in sorted(int(value) for value in np.unique(labels) if value > 0):
        lobe_mask = (labels == label).astype(np.uint8) * 255
        lobe_masks.append(lobe_mask)
        flat = int(np.argmax(np.where(labels == label, distance, -1)))
        y, x = np.unravel_index(flat, distance.shape)
        final_centers.append((int(x), int(y)))
        capacities.append(round(float(distance[labels == label].sum()), 2))

    adjacency = [(first - 1, second - 1) for first, second in _label_adjacency(labels)]
    necks = []
    for first, second in adjacency:
        metric = _neck_for_adjacent_lobes(
            distance, clean, labels, final_centers, first + 1, second + 1
        )
        if metric is not None:
            necks.append({"lobes": [first, second], **metric})
    return LobeGraph(clean, distance, labels, lobe_masks, final_centers, capacities, adjacency, necks)


class BubbleGeometry:
    """Distance-transform geometry for one speech-bubble mask."""

    _RADIUS_ALPHA = 0.06

    def __init__(self, mask: np.ndarray, padding: int = 0) -> None:
        clean = (np.asarray(mask) > 0).astype(np.uint8)
        if padding > 0:
            element = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (padding * 2 + 1, padding * 2 + 1)
            )
            clean = cv2.erode(clean, element)
        ys, xs = np.nonzero(clean)
        if len(ys):
            self.x_offset = int(xs.min())
            self.y_offset = int(ys.min())
            clean = clean[self.y_offset : int(ys.max()) + 1, self.x_offset : int(xs.max()) + 1]
        else:
            self.x_offset = self.y_offset = 0
        self.cleaned_mask = clean
        self._h, self._w = clean.shape[:2]
        self.dist = cv2.distanceTransform(clean, cv2.DIST_L2, 5).astype(np.float32)
        self._centroid = None
        self._covariance = None
        self._max_radius = None
        self._safe_cache: Dict[Tuple[int, int, float], np.ndarray] = {}
        self._safe_bbox_cache: Dict[Tuple[int, int, float], Tuple[int, int, int, int]] = {}
        self._band_cache: Dict[Tuple[int, int, int, int, float, int], List[BandSlot]] = {}

    def clear_ephemeral_caches(self) -> None:
        self._safe_cache.clear()
        self._safe_bbox_cache.clear()
        self._band_cache.clear()

    def safe_radius(self, font_size: int, stroke_width: int = 0, margin: float = 2.0) -> float:
        return self._RADIUS_ALPHA * font_size + stroke_width + margin

    def safe_pixels(self, font_size: int, stroke_width: int = 0, margin: float = 2.0) -> np.ndarray:
        key = (font_size, stroke_width, margin)
        if key not in self._safe_cache:
            self._safe_cache[key] = self.dist >= self.safe_radius(font_size, stroke_width, margin)
        return self._safe_cache[key]

    def scanline_intervals(self, y: int, font_size: int, stroke_width: int = 0, margin: float = 2.0) -> List[ScanInterval]:
        if y < 0 or y >= self._h:
            return []
        return _runs_from_row(self.safe_pixels(font_size, stroke_width, margin)[y])

    def band_intervals(self, y1: int, y2: int, font_size: int, stroke_width: int = 0, margin: float = 2.0, min_width: int = 1) -> List[BandSlot]:
        y1, y2 = max(0, y1), min(self._h, y2)
        if y1 >= y2:
            return []
        key = (y1, y2, font_size, stroke_width, margin, min_width)
        if key not in self._band_cache:
            band_row = np.all(self.safe_pixels(font_size, stroke_width, margin)[y1:y2], axis=0)
            self._band_cache[key] = [
                BandSlot(iv.left, iv.right, y1, y2)
                for iv in _runs_from_row(band_row)
                if iv.width >= min_width
            ]
        return self._band_cache[key]

    def centroid(self) -> Tuple[float, float]:
        if self._centroid is None:
            self._centroid, self._covariance = _mask_moments(self.cleaned_mask)
        return self._centroid

    def covariance(self) -> np.ndarray:
        if self._covariance is None:
            self._centroid, self._covariance = _mask_moments(self.cleaned_mask)
        return self._covariance

    def max_dt_radius(self) -> float:
        if self._max_radius is None:
            self._max_radius = float(self.dist.max()) if np.any(self.dist) else 0.0
        return self._max_radius

    @property
    def shape(self) -> Tuple[int, int]:
        return self._h, self._w

    def bounding_box(self) -> Tuple[int, int, int, int]:
        ys, xs = np.nonzero(self.cleaned_mask)
        return (0, 0, self._w, self._h) if not len(ys) else (
            int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        )

    def safe_bounding_box(self, font_size: int, stroke_width: int = 0, margin: float = 2.0) -> Tuple[int, int, int, int]:
        key = (font_size, stroke_width, margin)
        if key in self._safe_bbox_cache:
            return self._safe_bbox_cache[key]
        ys, xs = np.nonzero(self.safe_pixels(font_size, stroke_width, margin))
        result = (0, 0, 0, 0) if not len(ys) else (
            int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        )
        self._safe_bbox_cache[key] = result
        return result

    def has_safe_pixels(self, font_size: int, stroke_width: int = 0, margin: float = 2.0) -> bool:
        return bool(np.any(self.safe_pixels(font_size, stroke_width, margin)))


def compute_placement_target(
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    source_profile: Optional[Any] = None,
    preferred_mask: Optional[np.ndarray] = None,
    is_single_region: bool = True,
) -> PlacementTarget:
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    if preferred_mask is not None and np.any(preferred_mask):
        if preferred_mask.shape == safe.shape:
            constrained = safe & (preferred_mask > 0)
        else:
            y1, y2 = geom.y_offset, geom.y_offset + geom.shape[0]
            x1, x2 = geom.x_offset, geom.x_offset + geom.shape[1]
            constrained = safe & (preferred_mask[y1:y2, x1:x2] > 0)
        if np.any(constrained):
            safe = constrained
    ys, xs = np.nonzero(safe)
    if not len(ys):
        x1, y1, x2, y2 = geom.bounding_box()
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        return PlacementTarget(safe, cx, cy, (x1, y1, x2, y2), cx, cy, is_single_region)
    row_weights = np.sum(safe, axis=1).astype(np.float64)
    col_weights = np.sum(safe, axis=0).astype(np.float64)
    center_y = float(np.average(np.arange(len(row_weights)), weights=row_weights))
    center_x = float(np.average(np.arange(len(col_weights)), weights=col_weights))
    if source_profile is not None:
        preferred_x = float(source_profile.centroid[0] - geom.x_offset)
        preferred_y = float(source_profile.centroid[1] - geom.y_offset)
    else:
        preferred_x, preferred_y = center_x, center_y
    return PlacementTarget(
        safe,
        center_x,
        center_y,
        (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
        preferred_x,
        preferred_y,
        is_single_region,
    )


def compute_zone_shape_profile(
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    preferred_mask: Optional[np.ndarray] = None,
    line_h: Optional[int] = None,
    words: Optional[List[str]] = None,
    placement_target: Optional[PlacementTarget] = None,
) -> ZoneShapeProfile:
    safe = (placement_target or compute_placement_target(
        geom, font_size, stroke_width, margin, preferred_mask=preferred_mask
    )).mask
    ys, xs = np.nonzero(safe)
    if not len(ys):
        x1, y1, x2, y2 = geom.bounding_box()
        width, height = max(1.0, float(x2 - x1)), max(1.0, float(y2 - y1))
        return ZoneShapeProfile(width, height, width / height, 0.0, 1, 1, (x1 + x2) / 2, (y1 + y2) / 2, [], (x1, y1, x2, y2))
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    width, height = float(x2 - x1), float(y2 - y1)
    effective_line_h = line_h or int(math.ceil(font_size * 1.15))
    vertical_capacity = max(1, int(math.floor(height / max(1, effective_line_h))))
    min_capacity = max(1, int(math.ceil(len(words) / max(1.0, width / max(1.0, font_size * 2.5))))) if words else 1
    row_widths = [float(np.count_nonzero(safe[y])) for y in range(y1, y2)]
    row_weights = np.asarray(row_widths, dtype=np.float64)
    col_weights = np.sum(safe, axis=0).astype(np.float64)
    center_y = float(np.average(np.arange(y1, y2), weights=row_weights))
    center_x = float(np.average(np.arange(len(col_weights)), weights=col_weights))
    return ZoneShapeProfile(
        width, height, width / height, float(np.count_nonzero(safe)), vertical_capacity,
        min_capacity, center_x, center_y, row_widths, (x1, y1, x2, y2)
    )


def _runs_from_row(row: np.ndarray) -> List[ScanInterval]:
    if not np.any(row):
        return []
    padded = np.empty(len(row) + 2, dtype=bool)
    padded[0], padded[-1] = False, False
    padded[1:-1] = row
    diff = np.diff(padded.view(np.int8))
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    return [ScanInterval(int(start), int(end)) for start, end in zip(starts, ends)]


def _mask_moments(mask: np.ndarray) -> Tuple[Tuple[float, float], np.ndarray]:
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return (0.0, 0.0), np.eye(2, dtype=np.float32)
    cx, cy = float(xs.mean()), float(ys.mean())
    dx, dy = xs.astype(np.float32) - cx, ys.astype(np.float32) - cy
    n = float(len(ys))
    return (cx, cy), np.array(
        [[float(np.sum(dx * dx)) / n, float(np.sum(dx * dy)) / n],
         [float(np.sum(dx * dy)) / n, float(np.sum(dy * dy)) / n]],
        dtype=np.float32,
    )
