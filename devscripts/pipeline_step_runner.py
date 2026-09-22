#!/usr/bin/env python3
"""Pipeline Step Runner & Fast Placement/Rendering Dev Utility.

This developer script enables a two-phase workflow on manga pages:
1. `capture`: Run OCR + text detection + speech bubble segmentation + text-line merge + inpainting
   (with optional translation via Sugoi model or other translators), saving all intermediate step data
   (input image, RGB array, clean inpainted background, masks, OCR text/translation, and speech bubble layouts)
   into `devscripts/data/<sample_name>/`.
2. `render`: Load the captured step data from `devscripts/data/` and rapidly fit the OCR/translated text back
   onto the inpainted page using the new shape-aware bubble layout solver (BubbleGeometry + solve_layout)
   in milliseconds — with per-region diagnostics for font size, clearance, and solver path.

Both commands natively support processing multiple images concurrently.

Examples:
    # 1. Capture step data on English pages (OCR + detect + bubble segmentation + merge + inpaint):
    python devscripts/pipeline_step_runner.py capture -i page1.png page2.png
    python devscripts/pipeline_step_runner.py capture -i "test_images/*.png" -o devscripts/data
    python devscripts/pipeline_step_runner.py capture -i page1.png --no-bubble-grouping  # keep regions separate

    # 1b. Capture step data with Sugoi translation from Japanese to English:
    python devscripts/pipeline_step_runner.py capture -i raw_page.png --sugoi
    python devscripts/pipeline_step_runner.py capture -i "raw_pages/*.png" --translator sugoi

    # 2. Fast placement and text fitting test back onto the page:
    python devscripts/pipeline_step_runner.py render -i devscripts/data/page1 devscripts/data/page2
    python devscripts/pipeline_step_runner.py render --all --renderer manga2eng --line-spacing 0.1
    python devscripts/pipeline_step_runner.py render --all --font-path fonts/anime_ace.ttf --letter-case upper

    # 3. Solver diagnostics — show per-region solver report, compare vs legacy:
    python devscripts/pipeline_step_runner.py render --all --solver-report
    python devscripts/pipeline_step_runner.py render --all --legacy-only     # use old lobe-rect path only
    python devscripts/pipeline_step_runner.py render --all --margin 3.0 --max-y-trials 20
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import glob
import itertools
import json
import logging
import math
import os
from pathlib import Path
import pickle
import sys
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

import cv2
import numpy as np
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from manga_translator.config import (
    Config,
    Colorizer,
    Detector,
    Inpainter,
    Ocr,
    Renderer,
    Translator,
)
from manga_translator.detection import prepare as prepare_detection
from manga_translator.detection.bubble import BubbleDetection, prepare as prepare_bubble_detection
from manga_translator.inpainting import prepare as prepare_inpainting
from manga_translator.manga_translator import MangaTranslator
from manga_translator.ocr import prepare as prepare_ocr
from manga_translator.translators import prepare as prepare_translation
from manga_translator.rendering import (
    dispatch as dispatch_rendering,
    dispatch_eng_render,
    dispatch_eng_render_pillow,
)
from manga_translator.rendering.bubble_layout import (
    decode_safe_shape,
    encode_rendered_box,
    encode_safe_shape,
    group_regions_by_bubbles,
    prepare_bubble_masks,
    prepare_bubbles,
    restore_original,
    render_positioned_lines,
    _estimate_adaptive_font_size,
)
from manga_translator.rendering import (
    _RENDER_LOCK,
    _composite_box_to_image,
    _points_for_rect,
    fg_bg_compare,
    get_default_eng_font,
    text_render,
)
from manga_translator.utils import (
    Context,
    LANGUAGE_ORIENTATION_PRESETS,
    TextBlock,
    dump_image,
    init_logging,
    load_image,
)

logger = logging.getLogger("pipeline_runner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

DEFAULT_DATA_DIR = PROJECT_ROOT / "devscripts" / "data"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _ensure_region_identities(regions: Optional[List[Any]]) -> List[Any]:
    """Give every live region one stable id and preserve grouped source ownership."""
    for region in regions or []:
        region_id = str(getattr(region, "region_id", "") or "")
        if not region_id:
            region.region_id = uuid.uuid4().hex
            region_id = region.region_id

        source_ids = getattr(region, "source_region_ids", None)
        if isinstance(source_ids, str):
            source_ids = [source_ids]
        if not source_ids:
            members = getattr(region, "group_members", None)
            if isinstance(members, str):
                members = [members]
            source_ids = [str(member) for member in members] if members else [region_id]
        region.source_region_ids = [str(member) for member in source_ids]
    return regions or []


def _render_text(region: Any) -> str:
    """Return exactly the string handed to a renderer."""
    if hasattr(region, "get_translation_for_rendering"):
        return str(region.get_translation_for_rendering() or "")
    return str(getattr(region, "translation", "") or getattr(region, "text", "") or "")


def _record_content_trace(regions: Optional[List[Any]], stage: str) -> None:
    """Keep a compact OCR → translation → layout → render trace on each region."""
    _ensure_region_identities(regions)
    for region in regions or []:
        trace = getattr(region, "_content_trace", None)
        if trace is None:
            trace = []
            region._content_trace = trace
        mode = getattr(region, "placement_mode", None)
        mode_val = getattr(mode, "value", str(mode)) if mode is not None else None
        trace.append({
            "stage": stage,
            "region_id": str(region.region_id),
            "source_text": str(getattr(region, "text", "") or ""),
            "source_lines": [str(item) for item in (getattr(region, "texts", None) or [])],
            "translated_text": str(getattr(region, "translation", "") or ""),
            "render_text": _render_text(region),
            "layout_text": getattr(region, "_layout_input_text", None),
            "placement_mode": mode_val,
            "free_text_solver_applied": bool(getattr(region, "_free_text_solver_applied", False)),
            "solver_path": getattr(region, "_solver_path", None),
            "solver_status": getattr(region, "_solver_status", None),
            "has_bubble_box": getattr(region, "_bubble_box", None) is not None,
            "has_bubble_points": getattr(region, "_bubble_points", None) is not None,
            "has_free_text_zone": getattr(region, "_free_text_zone", None) is not None,
        })


def _sync_region_edits(regions: List[Any], records: List[Dict[str, Any]]) -> None:
    """Apply editable JSON fields by region id; positional matching is legacy-only."""
    _ensure_region_identities(regions)
    by_id = {
        str(record.get("id") or record.get("region_id")): record
        for record in records
        if record.get("id") or record.get("region_id")
    }
    for index, region in enumerate(regions):
        record = by_id.get(str(region.region_id))
        if record is None and not by_id and index < len(records):
            record = records[index]
        if record is None:
            continue
        if "translation" in record:
            region.translation = record["translation"]
        elif "text" in record:
            region.translation = record["text"]
        if "text" in record:
            region.text = record["text"]
        if record.get("font_size", -1) > 0:
            region.font_size = record["font_size"]
        if "direction" in record:
            region._direction = record["direction"]
        if "alignment" in record:
            region._alignment = record["alignment"]
        if "line_spacing" in record:
            region.line_spacing = record["line_spacing"]


def find_intersecting_draw_operations(
    draw_operations: List[Dict[str, Any]],
    bbox: Union[Tuple[int, int, int, int], List[int]],
) -> List[Dict[str, Any]]:
    """Find and return all draw operations whose bounding box intersects [x1, y1, x2, y2]."""
    if len(bbox) != 4:
        return []
    x1, y1, x2, y2 = bbox
    intersecting: List[Dict[str, Any]] = []
    for op in draw_operations or []:
        ob = op.get("bbox")
        if not ob or len(ob) != 4:
            continue
        ox1, oy1, ox2, oy2 = ob
        if not (ox2 <= x1 or ox1 >= x2 or oy2 <= y1 or oy1 >= y2):
            intersecting.append(op)
    return intersecting


def _validate_render_integrity(ctx: Context, strict: bool = False) -> List[str]:
    """Check that each selected render still owns the complete translated content."""
    issues: List[str] = []
    regions = getattr(ctx, "text_regions", None) or []
    _ensure_region_identities(regions)
    inpaint_mask = getattr(ctx, "inpaint_mask", None)
    if inpaint_mask is None:
        inpaint_mask = getattr(ctx, "mask", None)
    has_inpaint_mask = inpaint_mask is not None and np.any(inpaint_mask)
    img = getattr(ctx, "img_rgb", None)
    if img is None:
        img = getattr(ctx, "img_inpainted", None)
    shape = img.shape[:2] if img is not None else (inpaint_mask.shape[:2] if has_inpaint_mask else None)

    for region in regions:
        rid = str(region.region_id)
        text = _render_text(region).strip()
        if not text:
            continue
        mode = getattr(region, "placement_mode", None)
        layout_text = getattr(region, "_layout_input_text", None)
        if layout_text is not None and layout_text.strip() != text:
            issues.append(f"{rid}: layout text differs from translation")

        # Invariant: No silent fallback to OCR/source text anywhere after translation
        raw_trans = str(getattr(region, "translation", "") or "").strip()
        raw_src = str(getattr(region, "text", "") or "").strip()
        if raw_trans and text == raw_src and raw_trans != raw_src:
            issues.append(f"{rid}: silent fallback to OCR/source text detected after translation")

        if mode is PlacementMode.FREE_TEXT:
            if not getattr(region, "_free_text_solver_applied", False):
                issues.append(f"{rid}: free-text translation has no layout")
            else:
                if getattr(region, "_bubble_box", None) is None or getattr(region, "_bubble_points", None) is None:
                    issues.append(f"{rid}: free-text layout target is missing")
                if getattr(region, "_free_text_zone", None) is None:
                    issues.append(f"{rid}: free-text inpaint target is missing")
                if not has_inpaint_mask:
                    issues.append(f"{rid}: exact inpaint mask is missing")
                elif shape is not None:
                    # Invariant: source geometry ⊆ inpaint mask
                    src_mask = _region_source_mask(region, shape)
                    if np.any(src_mask):
                        src_count = int(np.count_nonzero(src_mask))
                        covered = int(np.count_nonzero(src_mask & (inpaint_mask > 0)))
                        cov_ratio = covered / max(1, src_count)
                        region._inpaint_mask_coverage = cov_ratio
                        if cov_ratio < 0.90:
                            issues.append(f"{rid}: source geometry not covered by inpaint mask ({cov_ratio:.1%})")

                # Invariant: final draw text == layout input text
                draw_ops = getattr(region, "_draw_operations", None)
                if draw_ops:
                    combined_draw_text = "".join("".join(op.get("text", "").split()) for op in draw_ops)
                    if layout_text and combined_draw_text != "".join(layout_text.split()):
                        issues.append(f"{rid}: final draw text differs from layout input text")

    ctx._layout_integrity_issues = issues
    for region in regions:
        rid = str(region.region_id)
        region._layout_integrity_issues = [issue for issue in issues if issue.startswith(f"{rid}:")]
    if issues:
        message = "Non-bubble render integrity failed: " + "; ".join(issues)
        if strict:
            raise RuntimeError(message)
        logger.warning(message)
    return issues


def _json_serialize_fallback(obj: Any) -> Any:
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "__dict__"):
        return {k: _json_serialize_fallback(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
    return str(obj)


def serialize_text_regions_to_dict(regions: List[TextBlock]) -> List[Dict[str, Any]]:
    """Serialize TextBlock instances into human-readable and editable JSON dictionaries."""
    _ensure_region_identities(regions)
    serialized = []
    for idx, region in enumerate(regions or []):
        entry: Dict[str, Any] = {
            "index": idx,
            "id": str(region.region_id),
            "source_region_ids": list(getattr(region, "source_region_ids", [region.region_id])),
            "text": getattr(region, "text", ""),
            "texts": getattr(region, "texts", []),
            "translation": getattr(region, "translation", getattr(region, "text", "")),
            "target_lang": getattr(region, "target_lang", "ENG"),
            "language": getattr(region, "language", "ENG"),
            "font_size": getattr(region, "font_size", -1),
            "angle": float(getattr(region, "angle", 0.0)),
            "direction": getattr(region, "direction", getattr(region, "_direction", "auto")),
            "alignment": getattr(region, "alignment", getattr(region, "_alignment", "auto")),
            "line_spacing": float(getattr(region, "line_spacing", 1.0)),
            "letter_spacing": float(getattr(region, "letter_spacing", 1.0)),
            "confidence": float(getattr(region, "confidence", getattr(region, "prob", 1.0))),
            "review_required": bool(getattr(region, "review_required", False)),
            "review_reason": getattr(region, "review_reason", None),
            "placement_mode": getattr(getattr(region, "placement_mode", None), "value", getattr(region, "placement_mode", None)),
        }
        if getattr(region, "_content_trace", None):
            entry["content_trace"] = region._content_trace
        if getattr(region, "_typography_report", None) is not None:
            entry["typography"] = region._typography_report

        # Foreground / background colors
        fg = getattr(region, "fg_colors", (0, 0, 0))
        bg = getattr(region, "bg_colors", (255, 255, 255))
        entry["fg_colors"] = [int(c) for c in fg] if hasattr(fg, "__iter__") else list(fg)
        entry["bg_colors"] = [int(c) for c in bg] if hasattr(bg, "__iter__") else list(bg)

        # Polygon lines
        if hasattr(region, "lines") and region.lines is not None:
            entry["lines"] = np.asarray(region.lines).tolist()

        # Bounding boxes
        if hasattr(region, "xywh"):
            entry["xywh"] = np.asarray(region.xywh).tolist()
        if hasattr(region, "xyxy"):
            entry["xyxy"] = np.asarray(region.xyxy).tolist()

        # Bubble matching info
        bubble_mask = getattr(region, "_bubble_mask", None)
        entry["has_bubble_mask"] = bool(bubble_mask is not None and np.any(bubble_mask))
        if getattr(region, "bubble_bounds", None) is not None:
            bb = region.bubble_bounds
            if isinstance(bb, (list, tuple, np.ndarray)):
                entry["bubble_bounds"] = [int(v) for v in bb]
            else:
                entry["bubble_bounds"] = _json_serialize_fallback(bb)
        if getattr(region, "layout_bounds", None) is not None:
            lb = region.layout_bounds
            if isinstance(lb, (list, tuple, np.ndarray)):
                entry["layout_bounds"] = [int(v) for v in lb]
            else:
                entry["layout_bounds"] = _json_serialize_fallback(lb)

        # Safe shape encoding if interior is present
        interior = getattr(region, "_bubble_interior", None)
        if interior is not None and np.any(interior):
            entry["safe_shape"] = encode_safe_shape(interior)

        serialized.append(entry)
    return serialized


@dataclass
class LobeGraph:
    """Geometry extracted from one connected speech-bubble segmentation mask."""

    cleaned_mask: np.ndarray
    distance: np.ndarray
    labels: np.ndarray
    lobe_masks: List[np.ndarray]
    centers: List[Tuple[int, int]]
    capacities: List[float]
    adjacency: List[Tuple[int, int]]
    necks: List[Dict[str, Any]]


def _normalize_bubble_mask(mask: Optional[np.ndarray], min_component_area: int = 16) -> np.ndarray:
    """Normalize a segmentation mask and remove isolated speckle components."""
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

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    clean = cv2.morphologyEx(clean, cv2.MORPH_CLOSE, kernel)

    # Fill holes without eroding thin but real necks.
    padded = cv2.copyMakeBorder(clean, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flooded = padded.copy()
    cv2.floodFill(flooded, np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8), (0, 0), 2)
    padded[(padded == 0) & (flooded == 0)] = 1
    clean = padded[1:-1, 1:-1]

    count, labels, stats, _ = cv2.connectedComponentsWithStats(clean, 8)
    if count > 1:
        keep = np.zeros_like(clean)
        for label in range(1, count):
            if stats[label, cv2.CC_STAT_AREA] >= min_component_area:
                keep[labels == label] = 1
        if np.any(keep):
            clean = keep
    return clean


def _centerline_neck(
    distance: np.ndarray,
    clean: np.ndarray,
    first: Tuple[int, int],
    second: Tuple[int, int],
) -> Optional[Dict[str, Any]]:
    """Measure the narrowest valid point between two distance-transform peaks."""
    x1, y1 = first
    x2, y2 = second
    endpoint_radius = min(float(distance[y1, x1]), float(distance[y2, x2]))
    if endpoint_radius <= 0:
        return None

    samples = max(5, int(round(math.hypot(x2 - x1, y2 - y1))))
    values = []
    points = []
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


def _candidate_lobe_centers(clean: np.ndarray, distance: np.ndarray, peak_ratio: float) -> List[Tuple[float, int, int]]:
    """Find one strongest point per distance-transform plateau."""
    max_radius = float(distance.max()) if np.any(distance) else 0.0
    if max_radius <= 0:
        return []
    threshold = max(2.0, max_radius * peak_ratio)
    kernel_size = max(3, int(round(min(clean.shape) * 0.03)))
    if kernel_size % 2 == 0:
        kernel_size += 1
    local_max = (distance >= threshold) & (distance >= cv2.dilate(distance, np.ones((kernel_size, kernel_size), np.uint8)))
    count, labels, _stats, _centroids = cv2.connectedComponentsWithStats(local_max.astype(np.uint8), 8)
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


def _watershed_labels(clean: np.ndarray, distance: np.ndarray, peaks: List[Tuple[float, int, int]]) -> np.ndarray:
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

    # Watershed borders can leave a few pixels unlabeled; assign them to the nearest seed.
    missing = np.argwhere(clean & (labels == 0))
    if len(missing):
        centers = np.asarray([(x, y) for _radius, x, y in peaks], dtype=np.float32)
        points = missing[:, [1, 0]].astype(np.float32)
        labels[missing[:, 0], missing[:, 1]] = np.argmin(
            ((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2), axis=1
        ) + 1
    return labels


def _merge_false_lobes(labels: np.ndarray, centers: List[Tuple[int, int]], distance: np.ndarray, clean: np.ndarray) -> np.ndarray:
    """Merge watershed regions whose connecting neck is not materially narrow."""
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
    remap = {}
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
    """Run mask cleanup, peak selection, watershed, neck analysis, and lobe merging."""
    clean = _normalize_bubble_mask(mask, min_component_area=min_component_area)
    distance = cv2.distanceTransform(clean, cv2.DIST_L2, 5).astype(np.float32) if clean.size else np.zeros_like(clean, dtype=np.float32)
    if not np.any(clean):
        return LobeGraph(clean, distance, np.zeros_like(clean, dtype=np.int32), [], [], [], [], [])

    peaks = _candidate_lobe_centers(clean, distance, peak_ratio)
    selected: List[Tuple[float, int, int]] = []
    for candidate in peaks:
        radius, x, y = candidate
        duplicate = False
        for previous_radius, previous_x, previous_y in selected:
            if (x - previous_x) ** 2 + (y - previous_y) ** 2 < (1.25 * max(radius, previous_radius)) ** 2:
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
        metric = _neck_for_adjacent_lobes(distance, clean, labels, final_centers, first + 1, second + 1)
        if metric is not None:
            necks.append({"lobes": [first, second], **metric})
    return LobeGraph(clean, distance, labels, lobe_masks, final_centers, capacities, adjacency, necks)


def detect_lobe_graph(mask: np.ndarray, **kwargs: Any) -> LobeGraph:
    """Compatibility name for callers that describe this stage as detection."""
    return build_lobe_graph(mask, **kwargs)


def _serialize_lobe_graph(graph: LobeGraph) -> Dict[str, Any]:
    bboxes = []
    areas = []
    for lobe_mask in graph.lobe_masks:
        ys, xs = np.where(lobe_mask > 0)
        bboxes.append([int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else [])
        areas.append(int(np.count_nonzero(lobe_mask)))
    return {
        "lobe_masks": [encode_safe_shape(lobe_mask) for lobe_mask in graph.lobe_masks],
        "centers": [[int(x), int(y)] for x, y in graph.centers],
        "capacities": [float(value) for value in graph.capacities],
        "adjacency": [[int(first), int(second)] for first, second in graph.adjacency],
        "necks": graph.necks,
        "lobe_bboxes": bboxes,
        "lobe_areas": areas,
    }


def serialize_bubble_detections(
    bubble_detections: List[Any],
    lobe_graphs: Optional[List[LobeGraph]] = None,
) -> List[Dict[str, Any]]:
    """Convert BubbleDetection objects into JSON serializable dictionaries."""
    serialized = []
    for idx, bd in enumerate(bubble_detections or []):
        mask = getattr(bd, "mask", None)
        conf = float(getattr(bd, "confidence", 1.0))
        entry: Dict[str, Any] = {
            "index": idx,
            "confidence": conf,
            "xyxy": [],
            "xywh": [],
            "area_pixels": 0,
            "polygon": [],
        }
        if mask is not None:
            ys, xs = np.where(mask > 0)
            if len(xs) > 0 and len(ys) > 0:
                x1, x2 = int(xs.min()), int(xs.max())
                y1, y2 = int(ys.min()), int(ys.max())
                entry["xyxy"] = [x1, y1, x2, y2]
                entry["xywh"] = [x1, y1, x2 - x1, y2 - y1]
                entry["area_pixels"] = int(np.count_nonzero(mask))

                # Extract simplified contour polygon
                contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if contours:
                    main_cnt = max(contours, key=cv2.contourArea)
                    peri = cv2.arcLength(main_cnt, True)
                    approx = cv2.approxPolyDP(main_cnt, 0.01 * peri, True)
                    entry["polygon"] = approx.reshape(-1, 2).tolist()
        graph = lobe_graphs[idx] if lobe_graphs is not None and idx < len(lobe_graphs) else build_lobe_graph(mask)
        entry["lobe_graph"] = _serialize_lobe_graph(graph)
        serialized.append(entry)
    return serialized


def create_bubbles_overlay_image(
    img_rgb: np.ndarray,
    bubble_detections: List[Any],
    lobe_graphs: Optional[List[LobeGraph]] = None,
) -> np.ndarray:
    """Create a visual inspection overlay showing detected speech bubbles with colored outlines and confidence labels."""
    if img_rgb is None:
        return np.zeros((100, 100, 3), dtype=np.uint8)
    overlay = img_rgb.copy()

    for idx, bd in enumerate(bubble_detections or []):
        mask = getattr(bd, "mask", None)
        conf = float(getattr(bd, "confidence", 1.0))
        if mask is not None and np.any(mask):
            contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            # Semi-transparent colored fill (cyan tint)
            color_mask = np.zeros_like(overlay)
            color_mask[mask > 0] = (0, 200, 255)
            cv2.addWeighted(color_mask, 0.25, overlay, 1.0, 0, overlay)
            # Outline
            cv2.drawContours(overlay, contours, -1, (0, 180, 255), 2)

            # Label
            ys, xs = np.where(mask > 0)
            if len(xs) > 0 and len(ys) > 0:
                cx, cy = int(xs.mean()), int(ys.min()) + 20
                label = f"Bubble #{idx+1} ({conf:.0%})"
                cv2.putText(overlay, label, (max(5, cx - 40), max(20, cy)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
                cv2.putText(overlay, label, (max(5, cx - 40), max(20, cy)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

            graph = lobe_graphs[idx] if lobe_graphs is not None and idx < len(lobe_graphs) else build_lobe_graph(mask)
            colors = [(255, 80, 220), (80, 255, 120), (255, 180, 40), (100, 180, 255), (220, 120, 255)]
            for lobe_index, lobe_mask in enumerate(graph.lobe_masks):
                lobe_contours, _ = cv2.findContours(lobe_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                color = colors[lobe_index % len(colors)]
                cv2.drawContours(overlay, lobe_contours, -1, color, 2)
                if lobe_index < len(graph.centers):
                    x, y = graph.centers[lobe_index]
                    cv2.circle(overlay, (x, y), 4, color, -1)
                    cv2.putText(overlay, f"L{lobe_index + 1}", (x + 5, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2)
                    cv2.putText(overlay, f"L{lobe_index + 1}", (x + 5, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
            for neck in graph.necks:
                x, y = neck["center"]
                cv2.circle(overlay, (x, y), 3, (255, 255, 0), -1)

    return overlay


def deserialize_text_regions_from_dict(items: List[Dict[str, Any]], image_shape: Optional[Tuple[int, int]] = None) -> List[TextBlock]:
    """Rebuild TextBlock instances from JSON dictionary records."""
    regions = []
    for item in items:
        lines = item.get("lines")
        if not lines and "xyxy" in item:
            x1, y1, x2, y2 = item["xyxy"]
            lines = [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]]
        elif not lines and "xywh" in item:
            x, y, w, h = item["xywh"]
            lines = [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]]]
        elif not lines:
            lines = [[[0, 0], [10, 0], [10, 10], [0, 10]]]

        lines_array = np.array(lines, dtype=np.int32)
        texts = item.get("texts") or ([item.get("text")] if item.get("text") else [""])
        fg_color = tuple(item.get("fg_colors", (0, 0, 0)))
        bg_color = tuple(item.get("bg_colors", (255, 255, 255)))

        region = TextBlock(
            lines=lines_array,
            texts=texts,
            language=item.get("language", "ENG"),
            font_size=item.get("font_size", -1),
            angle=item.get("angle", 0.0),
            translation=item.get("translation", item.get("text", "")),
            fg_color=fg_color,
            bg_color=bg_color,
            line_spacing=item.get("line_spacing", 1.0),
            letter_spacing=item.get("letter_spacing", 1.0),
            direction=item.get("direction", "auto"),
            alignment=item.get("alignment", "auto"),
            target_lang=item.get("target_lang", "ENG"),
            prob=item.get("confidence", 1.0),
        )

        region.region_id = item.get("id") or item.get("region_id") or uuid.uuid4().hex
        region.source_region_ids = list(item.get("source_region_ids") or [region.region_id])
        if item.get("content_trace"):
            region._content_trace = item["content_trace"]
        region.review_required = item.get("review_required", False)
        region.review_reason = item.get("review_reason")
        placement_mode = item.get("placement_mode")
        if placement_mode in ("BUBBLE", "FREE_TEXT"):
            region.placement_mode = PlacementMode(placement_mode)

        # Restore safe shape interior if present
        if "safe_shape" in item and image_shape:
            h, w = image_shape[:2]
            interior = decode_safe_shape(item["safe_shape"], h, w)
            if interior is not None:
                region._bubble_interior = interior

        regions.append(region)
    return regions


def _detector_cleanup_mask(
    textlines: List[Any],
    detector_mask: Optional[np.ndarray],
    image_shape: Tuple[int, ...],
) -> np.ndarray:
    """Keep detector pixels inside detector boxes even when OCR drops a box."""
    fallback = np.zeros(image_shape[:2], dtype=np.uint8)
    if detector_mask is None or not textlines or not np.size(detector_mask):
        return fallback

    geometry = np.zeros_like(fallback)
    for textline in textlines:
        points = getattr(textline, "pts", None)
        if points is not None:
            cv2.fillPoly(geometry, [np.asarray(points, dtype=np.int32)], 255)

    mask = np.asarray(detector_mask)
    if mask.ndim == 3:
        mask = cv2.cvtColor(mask, cv2.COLOR_RGB2GRAY)
    if mask.shape != fallback.shape:
        mask = cv2.resize(mask, (fallback.shape[1], fallback.shape[0]), interpolation=cv2.INTER_NEAREST)
    mask = np.where(mask > 0, 255, 0).astype(np.uint8)
    return cv2.bitwise_and(mask, geometry)


def save_step_data(
    output_base_dir: Union[str, Path],
    sample_name: str,
    ctx: Context,
    config: Config,
    source_path: Optional[str] = None,
    duration_ms: float = 0.0,
) -> Path:
    """Save all pipeline step data, images, JSON regions, and Python pickle state to a sample directory."""
    sample_dir = Path(output_base_dir) / sample_name
    sample_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save input image
    if ctx.input is not None:
        if isinstance(ctx.input, Image.Image):
            ctx.input.save(sample_dir / "input.png")
        elif isinstance(ctx.input, np.ndarray):
            cv2.imwrite(str(sample_dir / "input.png"), cv2.cvtColor(ctx.input, cv2.COLOR_RGB2BGR))

    # 2. Save img_rgb
    if ctx.img_rgb is not None:
        cv2.imwrite(str(sample_dir / "img_rgb.png"), cv2.cvtColor(ctx.img_rgb, cv2.COLOR_RGB2BGR))

    # 3. Save img_inpainted
    if ctx.img_inpainted is not None:
        cv2.imwrite(str(sample_dir / "inpainted.png"), cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR))

    # 4. Save masks
    if ctx.mask is not None:
        cv2.imwrite(str(sample_dir / "mask_final.png"), ctx.mask)
    if getattr(ctx, "text_mask", None) is not None:
        cv2.imwrite(str(sample_dir / "text_mask.png"), ctx.text_mask)
    if getattr(ctx, "bubble_mask", None) is not None:
        cv2.imwrite(str(sample_dir / "bubble_mask.png"), ctx.bubble_mask)
    if getattr(ctx, "mask_raw", None) is not None:
        cv2.imwrite(str(sample_dir / "mask_raw.png"), ctx.mask_raw)
    inpaint_mask = getattr(ctx, "inpaint_mask", None)
    if inpaint_mask is None:
        inpaint_mask = getattr(ctx, "mask", None)
    if inpaint_mask is not None:
        cv2.imwrite(str(sample_dir / "inpaint_mask.png"), inpaint_mask)

    # 5. Save human-readable text regions & OCR documents
    regions_json = serialize_text_regions_to_dict(ctx.text_regions or [])
    with open(sample_dir / "regions.json", "w", encoding="utf-8") as f:
        json.dump(regions_json, f, indent=2, ensure_ascii=False)
    with open(sample_dir / "text_regions.json", "w", encoding="utf-8") as f:
        json.dump(regions_json, f, indent=2, ensure_ascii=False)

    # 6. Save speech bubble detections, lobe graphs, & visual overlay
    bubble_detections = getattr(ctx, "bubble_detections", []) or []
    lobe_graphs = [build_lobe_graph(getattr(bubble, "mask", None)) for bubble in bubble_detections]
    ctx.lobe_graphs = lobe_graphs
    bubbles_data = serialize_bubble_detections(bubble_detections, lobe_graphs)
    with open(sample_dir / "bubbles.json", "w", encoding="utf-8") as f:
        json.dump(bubbles_data, f, indent=2, ensure_ascii=False)
    with open(sample_dir / "bubble_detections.json", "w", encoding="utf-8") as f:
        json.dump(bubbles_data, f, indent=2, ensure_ascii=False)

    if ctx.img_rgb is not None and bubble_detections:
        overlay_img = create_bubbles_overlay_image(ctx.img_rgb, bubble_detections, lobe_graphs)
        cv2.imwrite(str(sample_dir / "bubbles_overlay.png"), cv2.cvtColor(overlay_img, cv2.COLOR_RGB2BGR))

    # 6.5. Save placement zones overlay if multi-region layout groups exist
    layout_groups = getattr(ctx, "_bubble_layout_groups", None)
    if ctx.img_rgb is not None and layout_groups and any(len(g.regions) > 1 for g in layout_groups):
        try:
            zones_img = create_placement_zones_visualization(ctx.img_rgb, layout_groups)
            cv2.imwrite(str(sample_dir / "placement_zones.png"), cv2.cvtColor(zones_img, cv2.COLOR_RGB2BGR))
        except Exception as e:
            logger.warning(f"Could not save placement_zones.png: {e}")
    free_text_debug = getattr(ctx, "_free_text_layout_debug", None)
    if free_text_debug is not None:
        cv2.imwrite(str(sample_dir / "free_text_layout_debug.png"), cv2.cvtColor(free_text_debug, cv2.COLOR_RGB2BGR))

    # 7. Save raw OCR textlines and plain text dump
    raw_ocr_items = []
    plain_ocr_lines = []
    for idx, tl in enumerate(getattr(ctx, "textlines", []) or []):
        text_str = getattr(tl, "text", "")
        pts = getattr(tl, "pts", None)
        if pts is None and hasattr(tl, "lines"):
            pts = tl.lines
        item = {
            "index": idx,
            "text": text_str,
            "confidence": float(getattr(tl, "prob", getattr(tl, "confidence", 1.0))),
            "pts": np.asarray(pts).tolist() if pts is not None else [],
        }
        raw_ocr_items.append(item)
        if text_str:
            plain_ocr_lines.append(text_str)

    with open(sample_dir / "ocr.json", "w", encoding="utf-8") as f:
        json.dump(raw_ocr_items, f, indent=2, ensure_ascii=False)

    # Save clean readable OCR text extracted from the page
    all_region_texts = [getattr(r, "text", "") for r in (ctx.text_regions or []) if getattr(r, "text", "")]
    with open(sample_dir / "ocr_text.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join(all_region_texts) if all_region_texts else "\n".join(plain_ocr_lines))

    # 8. Save config.json
    try:
        config_dict = config.to_dict() if hasattr(config, "to_dict") else vars(config)
        with open(sample_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(_json_serialize_fallback(config_dict), f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not dump config.json: {e}")

    # 8. Save metadata
    h, w = ctx.img_rgb.shape[:2] if ctx.img_rgb is not None else (0, 0)
    meta = {
        "sample_name": sample_name,
        "source_path": str(source_path) if source_path else None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "resolution": {"width": w, "height": h},
        "regions_count": len(ctx.text_regions or []),
        "bubbles_count": len(bubble_detections),
        "lobe_counts": [len(graph.lobe_masks) for graph in lobe_graphs],
        "duration_ms": duration_ms,
        "mode": "english_ocr_inpaint_no_translation" if config.translator.translator == Translator.none else f"capture_with_{config.translator.translator}",
    }
    with open(sample_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # 9. Save step_data.pkl (full fidelity python objects for instant zero-loss reloading)
    pkl_payload = {
        "sample_name": sample_name,
        "source_path": str(source_path) if source_path else None,
        "img_rgb": ctx.img_rgb,
        "img_alpha": getattr(ctx, "img_alpha", None),
        "img_inpainted": ctx.img_inpainted,
        "mask": ctx.mask,
        "text_mask": getattr(ctx, "text_mask", None),
        "bubble_mask": getattr(ctx, "bubble_mask", None),
        "mask_raw": getattr(ctx, "mask_raw", None),
        "render_mask": getattr(ctx, "render_mask", None),
        "inpaint_mask": inpaint_mask,
        "textlines": getattr(ctx, "textlines", []),
        "text_regions": ctx.text_regions,
        "bubble_detections": bubble_detections,
        "lobe_graphs": lobe_graphs,
        "config": config,
    }
    try:
        with open(sample_dir / "step_data.pkl", "wb") as f:
            pickle.dump(pkl_payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        logger.warning(f"Pickle save failed ({e}); step_data fallback will rely on saved images and regions.json")

    logger.info(f"Saved step data for '{sample_name}' to: {sample_dir}")
    return sample_dir


class CompatUnpickler(pickle.Unpickler):
    """Unpickler compatible with numpy arrays and pipeline classes across modules and NumPy 1.x/2.x."""
    def find_class(self, module: str, name: str):
        try:
            return super().find_class(module, name)
        except (ModuleNotFoundError, AttributeError):
            if module == "__main__" or module.endswith("pipeline_step_runner"):
                import devscripts.pipeline_step_runner as psr
                if hasattr(psr, name):
                    return getattr(psr, name)
            if module.startswith("numpy._core"):
                alt_module = "numpy.core" + module[len("numpy._core"):]
                return super().find_class(alt_module, name)
            elif module.startswith("numpy.core"):
                alt_module = "numpy._core" + module[len("numpy.core"):]
                return super().find_class(alt_module, name)
            raise


def load_step_data(sample_dir: Union[str, Path], use_json: bool = False) -> Tuple[Context, Config]:
    """Load captured step data from a sample directory into a Context and Config."""
    sample_dir = Path(sample_dir)
    if not sample_dir.is_dir():
        raise FileNotFoundError(f"Sample directory not found: {sample_dir}")

    pkl_path = sample_dir / "step_data.pkl"
    regions_json_path = sample_dir / "regions.json"
    config_json_path = sample_dir / "config.json"

    ctx = Context()
    config = None

    loaded_from_pkl = False
    if pkl_path.is_file() and not use_json:
        try:
            with open(pkl_path, "rb") as f:
                payload = CompatUnpickler(f).load()
            ctx.img_rgb = payload.get("img_rgb")
            ctx.img_alpha = payload.get("img_alpha")
            ctx.img_inpainted = payload.get("img_inpainted")
            ctx.mask = payload.get("mask")
            ctx.text_mask = payload.get("text_mask")
            ctx.bubble_mask = payload.get("bubble_mask")
            ctx.mask_raw = payload.get("mask_raw")
            ctx.render_mask = payload.get("render_mask")
            ctx.inpaint_mask = payload.get("inpaint_mask", ctx.mask)
            ctx.textlines = payload.get("textlines", [])
            ctx.text_regions = payload.get("text_regions", [])
            ctx.bubble_detections = payload.get("bubble_detections", [])
            ctx.lobe_graphs = payload.get("lobe_graphs", [])
            config = payload.get("config")
            ctx._bubble_detection_done = True
            loaded_from_pkl = True
        except Exception as e:
            logger.warning(f"Failed to load step_data.pkl ({e}), falling back to disk files.")

    # Fallback to loading images and JSON
    if not loaded_from_pkl:
        img_rgb_path = sample_dir / "img_rgb.png"
        if not img_rgb_path.is_file():
            img_rgb_path = sample_dir / "input.png"

        if img_rgb_path.is_file():
            bgr = cv2.imread(str(img_rgb_path))
            ctx.img_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        else:
            raise FileNotFoundError(f"No RGB/input image found in {sample_dir}")

        inpainted_path = sample_dir / "inpainted.png"
        if inpainted_path.is_file():
            bgr_inp = cv2.imread(str(inpainted_path))
            ctx.img_inpainted = cv2.cvtColor(bgr_inp, cv2.COLOR_BGR2RGB)
        else:
            ctx.img_inpainted = ctx.img_rgb.copy()

        mask_path = sample_dir / "mask_final.png"
        if mask_path.is_file():
            ctx.mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        inpaint_mask_path = sample_dir / "inpaint_mask.png"
        if inpaint_mask_path.is_file():
            ctx.inpaint_mask = cv2.imread(str(inpaint_mask_path), cv2.IMREAD_GRAYSCALE)
        else:
            ctx.inpaint_mask = ctx.mask
        bubble_mask_path = sample_dir / "bubble_mask.png"
        if bubble_mask_path.is_file():
            ctx.bubble_mask = cv2.imread(str(bubble_mask_path), cv2.IMREAD_GRAYSCALE)

        if regions_json_path.is_file():
            with open(regions_json_path, "r", encoding="utf-8") as f:
                regions_data = json.load(f)
            ctx.text_regions = deserialize_text_regions_from_dict(regions_data, ctx.img_rgb.shape)
        else:
            ctx.text_regions = []

        bubbles_json_path = sample_dir / "bubbles.json"
        if not bubbles_json_path.is_file():
            bubbles_json_path = sample_dir / "bubble_detections.json"
        if bubbles_json_path.is_file():
            try:
                with open(bubbles_json_path, "r", encoding="utf-8") as f:
                    bubbles_list = json.load(f)
                reconstructed_bds = []
                for b_item in bubbles_list:
                    conf = float(b_item.get("confidence", 1.0))
                    b_mask = np.zeros(ctx.img_rgb.shape[:2], dtype=np.uint8)
                    poly = b_item.get("polygon")
                    xyxy = b_item.get("xyxy")
                    if poly:
                        cv2.fillPoly(b_mask, [np.array(poly, dtype=np.int32)], 255)
                    elif xyxy:
                        x1, y1, x2, y2 = xyxy
                        b_mask[y1:y2, x1:x2] = 255
                    reconstructed_bds.append(BubbleDetection(b_mask, conf))
                ctx.bubble_detections = reconstructed_bds
                ctx.lobe_graphs = [build_lobe_graph(bubble.mask) for bubble in reconstructed_bds]
                if ctx.text_regions:
                    ctx.text_regions = group_regions_by_bubbles(
                        ctx.text_regions, ctx.bubble_detections, group=False
                    )
            except Exception as e:
                logger.warning(f"Could not load bubbles.json: {e}")
        else:
            ctx.bubble_detections = []
            ctx.lobe_graphs = []

    _ensure_region_identities(ctx.text_regions)

    # If regions.json exists and has been modified by the developer, sync the translation/text fields
    if regions_json_path.is_file() and ctx.text_regions:
        try:
            with open(regions_json_path, "r", encoding="utf-8") as f:
                regions_data = json.load(f)
            _sync_region_edits(ctx.text_regions, regions_data)
        except Exception as e:
            logger.warning(f"Could not sync regions.json edits: {e}")

    # Ensure every region has a translation set from its text
    for reg in (ctx.text_regions or []):
        if not getattr(reg, "translation", None):
            reg.translation = getattr(reg, "text", "")
        if not getattr(reg, "target_lang", None):
            reg.target_lang = "ENG"
    _record_content_trace(ctx.text_regions, "loaded")

    # Load or default Config
    if config is None:
        if config_json_path.is_file():
            try:
                with open(config_json_path, "r", encoding="utf-8") as f:
                    cfg_dict = json.load(f)
                config = Config(**cfg_dict)
            except Exception:
                config = Config()
        else:
            config = Config()

    return ctx, config


def detect_best_device() -> Tuple[bool, str]:
    """Detect available hardware acceleration, defaulting to MPS on Apple Silicon or CUDA on Nvidia."""
    try:
        import torch
        if torch.backends.mps.is_available():
            return True, "mps"
        elif torch.cuda.is_available():
            return True, "cuda"
        elif hasattr(torch, "xpu") and torch.xpu.is_available():
            return True, "xpu"
    except Exception:
        pass
    return False, "cpu"


# ------------------------------------------------------------------
# Shape-Aware Bubble Geometry & Layout Solver (Embedded in Devscripts)
# ------------------------------------------------------------------

@dataclass
class SolverProfileStats:
    """Fine-grained Level-2 timing and workload counter stats for layout solvers."""
    # Workload counters
    fonts_tested: int = 0
    spacing_tested: int = 0
    y_origins_tested: int = 0
    dp_invocations: int = 0
    dp_states_created: int = 0
    dp_states_pruned: int = 0
    dp_states_deduplicated: int = 0
    raw_wrappings: int = 0
    pre_score_survivors: int = 0
    refined_candidates: int = 0
    glyph_validations: int = 0
    safe_cache_hits: int = 0
    safe_cache_misses: int = 0
    band_cache_hits: int = 0
    band_cache_misses: int = 0
    # Free-text specific workload
    free_text_crops_rendered: int = 0
    free_text_offsets_tested: int = 0
    free_text_hard_valid_hits: int = 0

    # Level-2 timing accumulators (in ms)
    safe_mask_prep_ms: float = 0.0
    width_precompute_ms: float = 0.0
    row_slot_table_ms: float = 0.0
    placement_target_ms: float = 0.0
    zone_profile_ms: float = 0.0
    y_origin_seq_ms: float = 0.0
    dp_search_ms: float = 0.0
    compaction_ms: float = 0.0
    gap_classification_ms: float = 0.0
    centering_ms: float = 0.0
    x_optimization_ms: float = 0.0
    composite_penalty_ms: float = 0.0
    bbox_validation_ms: float = 0.0
    glyph_validation_ms: float = 0.0
    # Free-text specific timings
    ft_typography_ms: float = 0.0
    ft_crops_rasterize_ms: float = 0.0
    ft_offset_search_ms: float = 0.0
    ft_coverage_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workload": {
                "fonts_tested": self.fonts_tested,
                "spacing_tested": self.spacing_tested,
                "y_origins_tested": self.y_origins_tested,
                "dp_invocations": self.dp_invocations,
                "dp_states_created": self.dp_states_created,
                "dp_states_pruned": self.dp_states_pruned,
                "dp_states_deduplicated": self.dp_states_deduplicated,
                "raw_wrappings": self.raw_wrappings,
                "pre_score_survivors": self.pre_score_survivors,
                "refined_candidates": self.refined_candidates,
                "glyph_validations": self.glyph_validations,
                "safe_cache_hits": self.safe_cache_hits,
                "safe_cache_misses": self.safe_cache_misses,
                "band_cache_hits": self.band_cache_hits,
                "band_cache_misses": self.band_cache_misses,
                "free_text_crops_rendered": self.free_text_crops_rendered,
                "free_text_offsets_tested": self.free_text_offsets_tested,
                "free_text_hard_valid_hits": self.free_text_hard_valid_hits,
            },
            "timings_ms": {
                "safe_mask_prep": self.safe_mask_prep_ms,
                "width_precompute": self.width_precompute_ms,
                "row_slot_table": self.row_slot_table_ms,
                "placement_target": self.placement_target_ms,
                "zone_profile": self.zone_profile_ms,
                "y_origin_seq": self.y_origin_seq_ms,
                "dp_search": self.dp_search_ms,
                "compaction": self.compaction_ms,
                "gap_classification": self.gap_classification_ms,
                "centering": self.centering_ms,
                "x_optimization": self.x_optimization_ms,
                "composite_penalty": self.composite_penalty_ms,
                "bbox_validation": self.bbox_validation_ms,
                "glyph_validation": self.glyph_validation_ms,
                "ft_typography": self.ft_typography_ms,
                "ft_crops_rasterize": self.ft_crops_rasterize_ms,
                "ft_offset_search": self.ft_offset_search_ms,
                "ft_coverage": self.ft_coverage_ms,
            }
        }


_GLOBAL_SOLVER_PROFILE: Optional[SolverProfileStats] = None


def get_solver_profile() -> SolverProfileStats:
    global _GLOBAL_SOLVER_PROFILE
    if _GLOBAL_SOLVER_PROFILE is None:
        _GLOBAL_SOLVER_PROFILE = SolverProfileStats()
    return _GLOBAL_SOLVER_PROFILE


def reset_solver_profile() -> SolverProfileStats:
    global _GLOBAL_SOLVER_PROFILE
    _GLOBAL_SOLVER_PROFILE = SolverProfileStats()
    return _GLOBAL_SOLVER_PROFILE


@dataclass(frozen=True)
class ScanInterval:
    """A single horizontal safe run on a scanline."""
    left: int
    right: int          # exclusive — interval is [left, right)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def center(self) -> float:
        return (self.left + self.right) / 2.0


@dataclass
class BandSlot:
    """A horizontal slot that is continuously safe across a vertical band."""
    left: int
    right: int          # exclusive
    y_start: int
    y_end: int          # exclusive

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def center(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def height(self) -> int:
        return self.y_end - self.y_start


class BubbleGeometry:
    """Distance-transform-based geometry for one speech bubble mask."""
    _RADIUS_ALPHA: float = 0.06

    def __init__(self, mask: np.ndarray, padding: int = 0) -> None:
        clean = (mask > 0).astype(np.uint8)
        if padding > 0:
            element = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (padding * 2 + 1, padding * 2 + 1)
            )
            clean = cv2.erode(clean, element)

        ys, xs = np.nonzero(clean)
        if len(ys) > 0:
            self.x_offset = int(xs.min())
            self.y_offset = int(ys.min())
            clean = clean[self.y_offset : int(ys.max()) + 1, self.x_offset : int(xs.max()) + 1]
        else:
            self.x_offset = 0
            self.y_offset = 0

        h, w = clean.shape[:2]
        self._h = h
        self._w = w

        self.cleaned_mask: np.ndarray = clean
        self.dist: np.ndarray = cv2.distanceTransform(clean, cv2.DIST_L2, 5).astype(np.float32)

        self._centroid: Optional[Tuple[float, float]] = None
        self._covariance: Optional[np.ndarray] = None
        self._max_radius: Optional[float] = None
        self._safe_cache: Dict[Tuple[int, int, float], np.ndarray] = {}
        self._band_cache: Dict[Tuple[int, int, int, int, float, int], List[BandSlot]] = {}

    def clear_ephemeral_caches(self) -> None:
        """Release cached safe masks and band intervals to conserve memory."""
        self._safe_cache.clear()
        self._band_cache.clear()

    def safe_radius(self, font_size: int, stroke_width: int = 0, margin: float = 2.0) -> float:
        return self._RADIUS_ALPHA * font_size + stroke_width + margin

    def safe_pixels(self, font_size: int, stroke_width: int = 0, margin: float = 2.0) -> np.ndarray:
        key = (font_size, stroke_width, margin)
        prof = get_solver_profile()
        if key not in self._safe_cache:
            prof.safe_cache_misses += 1
            t0 = perf_counter()
            r = self.safe_radius(font_size, stroke_width, margin)
            self._safe_cache[key] = self.dist >= r
            prof.safe_mask_prep_ms += (perf_counter() - t0) * 1000.0
        else:
            prof.safe_cache_hits += 1
        return self._safe_cache[key]

    def scanline_intervals(self, y: int, font_size: int, stroke_width: int = 0, margin: float = 2.0) -> List[ScanInterval]:
        if y < 0 or y >= self._h:
            return []
        safe = self.safe_pixels(font_size, stroke_width, margin)
        return _runs_from_row(safe[y])

    def band_intervals(
        self,
        y1: int,
        y2: int,
        font_size: int,
        stroke_width: int = 0,
        margin: float = 2.0,
        min_width: int = 1,
    ) -> List[BandSlot]:
        y1 = max(0, y1)
        y2 = min(self._h, y2)
        if y1 >= y2:
            return []

        key = (y1, y2, font_size, stroke_width, margin, min_width)
        prof = get_solver_profile()
        if key in self._band_cache:
            prof.band_cache_hits += 1
            return self._band_cache[key]

        prof.band_cache_misses += 1
        safe = self.safe_pixels(font_size, stroke_width, margin)
        band_row = np.all(safe[y1:y2], axis=0)
        intervals = _runs_from_row(band_row)
        slots = [
            BandSlot(left=iv.left, right=iv.right, y_start=y1, y_end=y2)
            for iv in intervals
            if iv.right - iv.left >= min_width
        ]
        slots.sort(key=lambda s: s.left)
        if len(self._band_cache) >= 512:
            # Evict first key to bound memory
            first_k = next(iter(self._band_cache))
            del self._band_cache[first_k]
        self._band_cache[key] = slots
        return slots

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
        return (self._h, self._w)

    def bounding_box(self) -> Tuple[int, int, int, int]:
        ys, xs = np.nonzero(self.cleaned_mask)
        if not len(ys):
            return (0, 0, self._w, self._h)
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    def safe_bounding_box(self, font_size: int, stroke_width: int = 0, margin: float = 2.0) -> Tuple[int, int, int, int]:
        safe = self.safe_pixels(font_size, stroke_width, margin)
        ys, xs = np.nonzero(safe)
        if not len(ys):
            return (0, 0, 0, 0)
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    def has_safe_pixels(self, font_size: int, stroke_width: int = 0, margin: float = 2.0) -> bool:
        return bool(np.any(self.safe_pixels(font_size, stroke_width, margin)))


def _runs_from_row(row: np.ndarray) -> List[ScanInterval]:
    if not np.any(row):
        return []
    padded = np.empty(len(row) + 2, dtype=bool)
    padded[0] = False
    padded[1:-1] = row
    padded[-1] = False
    diff = np.diff(padded.view(np.int8))
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    return [ScanInterval(left=int(s), right=int(e)) for s, e in zip(starts, ends)]


def _mask_moments(mask: np.ndarray) -> Tuple[Tuple[float, float], np.ndarray]:
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return (0.0, 0.0), np.eye(2, dtype=np.float32)
    cx = float(xs.mean())
    cy = float(ys.mean())
    dx = xs.astype(np.float32) - cx
    dy = ys.astype(np.float32) - cy
    n = float(len(ys))
    cov = np.array(
        [
            [float(np.sum(dx * dx)) / n, float(np.sum(dx * dy)) / n],
            [float(np.sum(dx * dy)) / n, float(np.sum(dy * dy)) / n],
        ],
        dtype=np.float32,
    )
    return (cx, cy), cov


@dataclass
class PlacementTarget:
    """Target safe-zone placement geometry for a text block."""
    mask: np.ndarray
    center_x: float
    center_y: float
    bbox: Tuple[int, int, int, int]
    preferred_center_x: float
    preferred_center_y: float
    is_single_region: bool = True


def compute_placement_target(
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    source_profile: Optional["OriginalLayoutProfile"] = None,
    preferred_mask: Optional[np.ndarray] = None,
    is_single_region: bool = True,
) -> PlacementTarget:
    """Calculate the text-capacity center and safe placement target.

    An irregular bubble or one with a tail can have a misleading geometric centroid.
    Instead, estimate where the safe zone has the most useful space for text:
    C_y = sum(y * W(y)) / sum(W(y)), C_x = sum(x * H(x)) / sum(H(x)).
    """
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    if preferred_mask is not None and np.any(preferred_mask):
        h, w = geom.shape
        py1, py2 = geom.y_offset, geom.y_offset + h
        px1, px2 = geom.x_offset, geom.x_offset + w
        # Handle preferred_mask local vs global shape
        if preferred_mask.shape == safe.shape:
            active_safe = safe & (preferred_mask > 0)
        elif preferred_mask.shape[0] >= py2 and preferred_mask.shape[1] >= px2:
            active_safe = safe & (preferred_mask[py1:py2, px1:px2] > 0)
        else:
            active_safe = safe
        if np.any(active_safe):
            safe = active_safe

    ys, xs = np.nonzero(safe)
    if len(ys) == 0:
        # Fallback to geom bounding box center
        x1, y1, x2, y2 = geom.bounding_box()
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        return PlacementTarget(
            mask=safe,
            center_x=cx,
            center_y=cy,
            bbox=(x1, y1, x2, y2),
            preferred_center_x=cx,
            preferred_center_y=cy,
            is_single_region=is_single_region,
        )

    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    # Capacity weighting
    # W(y) = usable horizontal width at y
    w_y = np.sum(safe, axis=1).astype(np.float64)  # shape (H,)
    # H(x) = usable vertical extent at x
    h_x = np.sum(safe, axis=0).astype(np.float64)  # shape (W,)

    sum_wy = np.sum(w_y)
    sum_hx = np.sum(h_x)

    y_indices = np.arange(len(w_y), dtype=np.float64)
    x_indices = np.arange(len(h_x), dtype=np.float64)

    cap_cy = float(np.sum(y_indices * w_y) / sum_wy) if sum_wy > 0 else float(ys.mean())
    cap_cx = float(np.sum(x_indices * h_x) / sum_hx) if sum_hx > 0 else float(xs.mean())

    if source_profile is not None:
        pref_cx = float(source_profile.centroid[0] - geom.x_offset)
        pref_cy = float(source_profile.centroid[1] - geom.y_offset)
    else:
        pref_cx = cap_cx
        pref_cy = cap_cy

    return PlacementTarget(
        mask=safe,
        center_x=cap_cx,
        center_y=cap_cy,
        bbox=bbox,
        preferred_center_x=pref_cx,
        preferred_center_y=pref_cy,
        is_single_region=is_single_region,
    )


@dataclass
class ZoneShapeProfile:
    """Geometric shape profile of a bubble's safe text placement zone."""
    width: float
    height: float
    aspect_ratio: float
    usable_area: float
    vertical_capacity: int
    min_capacity: int
    center_x: float
    center_y: float
    width_by_y: List[float]
    bbox: Tuple[int, int, int, int]


def compute_zone_shape_profile(
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    preferred_mask: Optional[np.ndarray] = None,
    line_h: Optional[int] = None,
    words: Optional[List[str]] = None,
) -> ZoneShapeProfile:
    """Analyze the region's safe placement zone to produce a ZoneShapeProfile."""
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    if preferred_mask is not None and np.any(preferred_mask):
        h, w = geom.shape
        py1, py2 = geom.y_offset, geom.y_offset + h
        px1, px2 = geom.x_offset, geom.x_offset + w
        if preferred_mask.shape == safe.shape:
            active_safe = safe & (preferred_mask > 0)
        elif preferred_mask.shape[0] >= py2 and preferred_mask.shape[1] >= px2:
            active_safe = safe & (preferred_mask[py1:py2, px1:px2] > 0)
        else:
            active_safe = safe
        if np.any(active_safe):
            safe = active_safe

    ys, xs = np.nonzero(safe)
    if len(ys) == 0:
        bx1, by1, bx2, by2 = geom.bounding_box()
        bw = max(1.0, float(bx2 - bx1))
        bh = max(1.0, float(by2 - by1))
        return ZoneShapeProfile(
            width=bw,
            height=bh,
            aspect_ratio=bw / bh,
            usable_area=0.0,
            vertical_capacity=1,
            min_capacity=1,
            center_x=(bx1 + bx2) / 2.0,
            center_y=(by1 + by2) / 2.0,
            width_by_y=[],
            bbox=(bx1, by1, bx2, by2),
        )

    x1, y1 = int(xs.min()), int(ys.min())
    x2, y2 = int(xs.max()) + 1, int(ys.max()) + 1
    w_zone = max(1.0, float(x2 - x1))
    h_zone = max(1.0, float(y2 - y1))
    ar_zone = w_zone / h_zone
    usable_area = float(np.count_nonzero(safe))

    w_y = [float(np.count_nonzero(safe[y, :])) for y in range(y1, y2)]
    sum_wy = sum(w_y)
    h_x = np.sum(safe, axis=0).astype(np.float64)
    sum_hx = np.sum(h_x)

    cap_cy = float(np.sum(np.arange(y1, y2) * np.array(w_y)) / sum_wy) if sum_wy > 0 else (y1 + y2) / 2.0
    cap_cx = float(np.sum(np.arange(len(h_x)) * h_x) / sum_hx) if sum_hx > 0 else (x1 + x2) / 2.0

    eff_line_h = line_h if line_h is not None and line_h > 0 else int(math.ceil(font_size * 1.15))
    v_cap = max(1, int(math.floor(h_zone / max(1, eff_line_h))))
    min_cap = max(1, int(math.ceil(len(words) / max(1.0, (w_zone / max(1.0, font_size * 2.5)))))) if words else 1

    return ZoneShapeProfile(
        width=w_zone,
        height=h_zone,
        aspect_ratio=ar_zone,
        usable_area=usable_area,
        vertical_capacity=v_cap,
        min_capacity=min_cap,
        center_x=cap_cx,
        center_y=cap_cy,
        width_by_y=w_y,
        bbox=(x1, y1, x2, y2),
    )


@dataclass
class PlacedLine:
    text: str
    y: int
    x: int
    width: int
    height: int
    slot: BandSlot


@dataclass
class LayoutCandidate:
    font_size: int
    y_origin: int
    line_spacing: float
    lines: List[PlacedLine]
    penalty: float                      # composite objective; lower is better
    glyph_clearance_p5: float
    status: str = "ok"
    valid: bool = True
    qa: Dict[str, float] = field(default_factory=dict)


# Composite-objective weights (Phase 7). Hard validity stays in the glyph
# validators; everything here is soft preference.
_WEIGHT_FONT = 100.0       # deviation from the target font size
_WEIGHT_CENTROID = 2.0     # ink-centroid drift from the placement target
_WEIGHT_SHAPE = 0.05       # second derivative of the line-width silhouette
_WEIGHT_FILL_VAR = 2.0     # variance of per-line slot fill
_WEIGHT_JITTER = 0.05      # per-line drift from slot center
_WEIGHT_RAGGED = 0.2       # adjacent line-width disparity
_WEIGHT_ORPHAN = 10.0      # short isolated word lines
_WEIGHT_HYPHEN = 8.0       # non-final lines ending with a hyphen
_WEIGHT_OCC_LOW = 50.0     # text block too small for the bubble
_WEIGHT_OCC_HIGH = 50.0    # text block cramped inside the bubble
_OCC_IDEAL = (0.20, 0.75)  # desirable occupancy band
_WEIGHT_SRC_CENTROID = 3.0
_WEIGHT_SRC_LINES = 2.0
_WEIGHT_SRC_FONT = 20.0
_WEIGHT_SRC_BBOX = 1.0
_WEIGHT_SRC_PATTERN = 4.0   # deviation from source relative line-center pattern
_WEIGHT_ZONE_OVERFLOW = 18.0
_WEIGHT_BLOCK_GAP = 15.0    # vertical empty span inside block
_WEIGHT_CENTER_VAR = 5.0    # unpenalized lateral dispersion of line centers
_WEIGHT_VERT_BALANCE = 25.0 # vertical whitespace balance |A_above - A_below| / A_zone
_WEIGHT_HORIZ_BALANCE = 12.0 # horizontal whitespace balance |A_left - A_right| / A_zone
_WEIGHT_VFILL_LOW = 30.0    # vertical fill under-utilization (< 0.55)
_WEIGHT_VFILL_HIGH = 35.0   # vertical fill over-cramped (> 0.80)
_WEIGHT_ASPECT = 18.0       # log aspect ratio matching penalty
_WEIGHT_SILHOUETTE = 8.0    # width profile / silhouette match
_JOINT_CANDIDATE_COUNT = 5
_MAX_JOINT_LAYOUT_COMBINATIONS = 4096

# DP transition & continuity weights
_WEIGHT_TRANS_XJUMP = 8.0       # quadratic penalty for normalized center jump
_WEIGHT_TRANS_OVERLAP = 12.0    # penalty for poor horizontal slot overlap
_WEIGHT_TRANS_BRANCH = 25.0     # penalty for zero overlap / branch jump
_WEIGHT_VERTICAL_GAP = 24.0           # nonlinear paragraph spring penalty


# Gap classification constants
GAP_NORMAL = "NORMAL"
GAP_LOCAL_GEOMETRY = "LOCAL_GEOMETRY_ADJUSTMENT"
GAP_LOBE_NECK = "LOBE_NECK"
GAP_DISCONNECTED_SAFE_REGION = "DISCONNECTED_SAFE_REGION"
GAP_SOURCE_BREAK = "SOURCE_PARAGRAPH_BREAK"
GAP_UNEXPLAINED = "UNEXPLAINED"


def _is_geometric_obstruction(
    y_from: int,
    y_to: int,
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    min_w: int = 8,
    lobe_graph: Optional["LobeGraph"] = None,
) -> Tuple[bool, str]:
    """Check if there is a concrete geometric obstruction in the vertical interval [y_from, y_to]."""
    if y_from >= y_to:
        return False, GAP_NORMAL

    # 1. Check LobeGraph neck crossing
    if lobe_graph is not None and getattr(lobe_graph, "necks", None):
        for neck in lobe_graph.necks:
            neck_pt = neck.get("center") or neck.get("neck_point")
            if neck_pt is not None:
                # neck_pt is [x, y] or (x, y)
                ny = neck_pt[1] - getattr(geom, "y_offset", 0)
                if y_from <= ny <= y_to:
                    return True, GAP_LOBE_NECK

    # 2. Check safe mask cross section along the vertical interval
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    h_mask, _ = safe.shape
    y_start = max(0, min(h_mask, y_from))
    y_end = max(0, min(h_mask, y_to))
    if y_start >= y_end:
        return False, GAP_NORMAL

    sub_safe = safe[y_start:y_end, :]
    row_widths = np.sum(sub_safe, axis=1)

    if np.any(row_widths == 0):
        return True, GAP_DISCONNECTED_SAFE_REGION

    if np.any(row_widths < min_w):
        return True, GAP_DISCONNECTED_SAFE_REGION

    return False, GAP_UNEXPLAINED


def _classify_adjacent_gaps(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
    line_h: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    lobe_graph: Optional["LobeGraph"] = None,
    source_profile: Optional["OriginalLayoutProfile"] = None,
) -> List[Dict[str, Any]]:
    """Classify every adjacent line gap with an explicit reason and gap ratio."""
    if len(lines) <= 1:
        return []

    H = max(1.0, float(line_h))
    gap_reports: List[Dict[str, Any]] = []

    for i in range(len(lines) - 1):
        l1 = lines[i]
        l2 = lines[i + 1]
        delta_y = l2.y - l1.y
        gap_ratio = delta_y / H
        min_required_w = max(8, min(l1.width, l2.width) // 2)

        if gap_ratio <= 1.25:
            reason = GAP_NORMAL
        elif gap_ratio <= 1.45:
            is_obstructed, obs_reason = _is_geometric_obstruction(
                l1.y + l1.height, l2.y, geom, font_size, stroke_width, margin, min_required_w, lobe_graph
            )
            if is_obstructed:
                reason = obs_reason
            else:
                reason = GAP_LOCAL_GEOMETRY
        else:
            is_obstructed, obs_reason = _is_geometric_obstruction(
                l1.y + l1.height, l2.y, geom, font_size, stroke_width, margin, min_required_w, lobe_graph
            )
            if is_obstructed:
                reason = obs_reason
            else:
                reason = GAP_UNEXPLAINED

        gap_reports.append({
            "line_from": l1.text,
            "line_to": l2.text,
            "gap_px": delta_y,
            "gap_h": round(gap_ratio, 2),
            "reason": reason,
            "valid": (reason != GAP_UNEXPLAINED or gap_ratio <= 1.40),
        })

    return gap_reports


def _compact_vertical_rhythm(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
    line_h: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    lobe_graph: Optional["LobeGraph"] = None,
    row_slot_table: Optional[Dict[int, List[BandSlot]]] = None,
) -> List[PlacedLine]:
    """Bidirectional vertical compaction & spring-chain rhythm optimization.

    Enforces continuous paragraph rhythm (y_i^* = y_0 + i*H) at sub-row / pixel resolution,
    pulling lines toward their ideal positions whenever permitted by safe mask geometry.
    """
    if len(lines) <= 1:
        return lines

    n = len(lines)
    H = max(font_size, line_h)
    _, y1_safe, _, y2_safe = geom.safe_bounding_box(font_size, stroke_width, margin)
    if y2_safe <= y1_safe:
        return lines

    cur_lines = list(lines)

    def _best_slot_at_y(target_y: int, line_obj: PlacedLine) -> Optional[BandSlot]:
        if target_y < y1_safe or target_y + font_size > y2_safe:
            return None
        if row_slot_table is not None and target_y in row_slot_table:
            slots = [s for s in row_slot_table[target_y] if s.width >= line_obj.width]
        else:
            slots = geom.band_intervals(
                target_y, target_y + font_size, font_size, stroke_width, margin, min_width=line_obj.width
            )
        if not slots:
            return None
        # Pick slot closest in center to previous slot / center
        return min(slots, key=lambda s: abs(s.center - line_obj.slot.center))

    # Pass 1: Upward compaction ("gravity" pulling lines upward toward y_{i-1} + H)
    for i in range(1, n):
        prev_y = cur_lines[i - 1].y
        expected_y = prev_y + H
        cur_y = cur_lines[i].y
        if cur_y > expected_y:
            for cand_y in range(expected_y, cur_y + 1):
                slot = _best_slot_at_y(cand_y, cur_lines[i])
                if slot is not None:
                    ideal_x = int(round(slot.center - cur_lines[i].width / 2.0))
                    new_x = max(slot.left, min(slot.right - cur_lines[i].width, ideal_x))
                    cur_lines[i] = PlacedLine(
                        text=cur_lines[i].text,
                        y=cand_y,
                        x=new_x,
                        width=cur_lines[i].width,
                        height=font_size,
                        slot=slot,
                    )
                    break

    # Pass 2: Downward compaction (pulling upper lines downward towards y_{i+1} - H if displaced)
    for i in range(n - 2, -1, -1):
        next_y = cur_lines[i + 1].y
        expected_y = next_y - H
        cur_y = cur_lines[i].y
        if cur_y < expected_y:
            min_bound = (cur_lines[i - 1].y + font_size) if i > 0 else y1_safe
            for cand_y in range(expected_y, cur_y - 1, -1):
                if cand_y < min_bound:
                    break
                slot = _best_slot_at_y(cand_y, cur_lines[i])
                if slot is not None:
                    ideal_x = int(round(slot.center - cur_lines[i].width / 2.0))
                    new_x = max(slot.left, min(slot.right - cur_lines[i].width, ideal_x))
                    cur_lines[i] = PlacedLine(
                        text=cur_lines[i].text,
                        y=cand_y,
                        x=new_x,
                        width=cur_lines[i].width,
                        height=font_size,
                        slot=slot,
                    )
                    break

    # Pass 3: Spring chain energy relaxation E = sum (y_{k+1} - y_k - H)^2
    for _ in range(2):
        for i in range(n):
            cur_y = cur_lines[i].y
            min_y = (cur_lines[i - 1].y + font_size) if i > 0 else y1_safe
            max_y = (cur_lines[i + 1].y - font_size) if i < n - 1 else (y2_safe - font_size)
            if min_y > max_y:
                continue

            search_min = max(min_y, cur_y - H)
            search_max = min(max_y, cur_y + H)

            best_cand_y = cur_y
            best_cand_slot = cur_lines[i].slot
            best_e = float("inf")

            for cand_y in range(search_min, search_max + 1):
                slot = _best_slot_at_y(cand_y, cur_lines[i])
                if slot is None:
                    continue
                # Compute local spring energy
                e = 0.0
                if i > 0:
                    e += (cand_y - cur_lines[i - 1].y - H) ** 2
                if i < n - 1:
                    e += (cur_lines[i + 1].y - cand_y - H) ** 2

                if e < best_e:
                    best_e = e
                    best_cand_y = cand_y
                    best_cand_slot = slot

            if best_cand_slot is not None and best_cand_y != cur_y:
                ideal_x = int(round(best_cand_slot.center - cur_lines[i].width / 2.0))
                new_x = max(best_cand_slot.left, min(best_cand_slot.right - cur_lines[i].width, ideal_x))
                cur_lines[i] = PlacedLine(
                    text=cur_lines[i].text,
                    y=best_cand_y,
                    x=new_x,
                    width=cur_lines[i].width,
                    height=font_size,
                    slot=best_cand_slot,
                )

    return cur_lines


def _font_penalty(font_size: int, font_target: int) -> float:
    if font_target <= 0:
        return 0.0
    return ((font_size - font_target) / float(font_target)) ** 2 * _WEIGHT_FONT


def solve_layout(
    geom: BubbleGeometry,
    words: List[str],
    font_size_max: int,
    font_size_min: int,
    language: str = "en_US",
    hyphenate: bool = True,
    line_spacing: float = 0.0,
    line_spacing_options: Optional[List[float]] = None,
    y_origin_step: int = 4,
    stroke_width: int = 0,
    margin: float = 2.0,
    max_y_origin_trials: int = 12,
    source_profile: Optional["OriginalLayoutProfile"] = None,
    preferred_mask: Optional[np.ndarray] = None,
    top_k: int = 1,
    is_single_region: bool = True,
    lobe_graph: Optional["LobeGraph"] = None,
) -> Union[Optional[LayoutCandidate], List[LayoutCandidate]]:
    """Generate and rank layout candidates; return one or the best ``top_k``.

    The mask determines *where text may exist* (hard validation); language,
    typography, and the original page's layout profile shape the soft score.
    ``hyphenate`` is accepted for API compatibility — normalized words are
    treated as atomic and hyphenation is never introduced by the DP.
    """
    if not words:
        return None

    # Phase 2: repair OCR hyphen splits so ordinary words are atomic.
    norm_words = normalize_words(words)

    if line_spacing_options is None:
        line_spacing_options = [line_spacing]

    font_target = font_size_max
    candidates: List[LayoutCandidate] = []
    prof = get_solver_profile()

    # Determine font size sequence: coarse stepping for wide ranges, then fine refinement
    font_range = list(range(font_size_max, font_size_min - 1, -1))
    if len(font_range) > 10:
        # Test coarse font sizes first (step 2 or 3)
        step = 3 if len(font_range) > 16 else 2
        coarse_fonts = list(range(font_size_max, font_size_min - 1, -step))
        if font_size_min not in coarse_fonts:
            coarse_fonts.append(font_size_min)
        # All font sizes to test will include coarse fonts, and once a valid candidate is found,
        # we will add fine neighbors
        candidate_font_queue = list(coarse_fonts)
        fine_refined_fonts = set()
    else:
        candidate_font_queue = list(font_range)
        fine_refined_fonts = set(font_range)

    queue_idx = 0
    while queue_idx < len(candidate_font_queue):
        S = candidate_font_queue[queue_idx]
        queue_idx += 1
        prof.fonts_tested += 1

        # A smaller font may win on composition, but the font penalty grows
        # monotonically below the target: prune once the font term alone can
        # no longer beat the incumbent.
        cutoff = sorted((candidate.penalty for candidate in candidates if candidate.valid))[:max(1, top_k)]
        if len(cutoff) >= top_k and _font_penalty(S, font_target) >= cutoff[-1]:
            continue

        if not geom.has_safe_pixels(S, stroke_width, margin):
            continue

        x1, y1, x2, y2 = geom.safe_bounding_box(S, stroke_width, margin)
        if x2 <= x1 or y2 <= y1:
            continue

        t_w0 = perf_counter()
        word_widths, space_w = _precompute_widths(norm_words, S)
        prof.width_precompute_ms += (perf_counter() - t_w0) * 1000.0
        if not word_widths:
            continue

        # Feasibility check: max single word width must fit in safe width
        if max(word_widths) > (x2 - x1):
            continue

        # Precompute reusable row-slot table for font size S
        min_slot_w = max(S, 8)
        t_rst0 = perf_counter()
        row_slot_table = _build_row_slot_table(
            geom, S, stroke_width, margin, min_width=min_slot_w
        )
        prof.row_slot_table_ms += (perf_counter() - t_rst0) * 1000.0
        if not row_slot_table:
            continue

        t_pt0 = perf_counter()
        target_geom = compute_placement_target(
            geom, S, stroke_width, margin,
            source_profile=source_profile,
            preferred_mask=preferred_mask,
            is_single_region=is_single_region,
        )
        prof.placement_target_ms += (perf_counter() - t_pt0) * 1000.0

        for ls in line_spacing_options:
            prof.spacing_tested += 1
            line_h = _line_height(S, ls)
            t_zp0 = perf_counter()
            zone_profile = compute_zone_shape_profile(
                geom, S, stroke_width, margin,
                preferred_mask=preferred_mask,
                line_h=line_h,
                words=norm_words,
            )
            prof.zone_profile_ms += (perf_counter() - t_zp0) * 1000.0

            t_ys0 = perf_counter()
            y_origins = _y_origin_sequence(y1, y2, S, y_origin_step)
            prof.y_origin_seq_ms += (perf_counter() - t_ys0) * 1000.0

            # Phase 2: Collect candidate wrappings across Y trials using coarse-to-fine exploration
            raw_wrappings: List[Tuple[int, List[PlacedLine]]] = []
            trials_after_first = 0

            # If many Y origins, explore a coarse subset first, then refine around promising basins
            if len(y_origins) > 6:
                coarse_y_step = max(2, len(y_origins) // 6)
                coarse_y = y_origins[::coarse_y_step]
                if y_origins[-1] not in coarse_y:
                    coarse_y.append(y_origins[-1])
            else:
                coarse_y = y_origins

            promising_y = list(coarse_y)
            tested_y = set()

            for y_orig in promising_y:
                if y_orig in tested_y:
                    continue
                tested_y.add(y_orig)
                prof.y_origins_tested += 1
                t_dp0 = perf_counter()
                cand_wrappings = _try_placement_rows(
                    geom, norm_words, word_widths, space_w,
                    S, y_orig, ls, line_h, stroke_width, margin,
                    zone_profile=zone_profile,
                    max_per_bucket=2,
                    row_slot_table=row_slot_table,
                )
                prof.dp_search_ms += (perf_counter() - t_dp0) * 1000.0
                if not cand_wrappings:
                    continue

                for placed in cand_wrappings:
                    raw_wrappings.append((y_orig, placed))
                    prof.raw_wrappings += 1

                trials_after_first += 1
                if trials_after_first >= max_y_origin_trials:
                    break

            # Fine Y refinement around best coarse wrappings
            if raw_wrappings and len(y_origins) > len(promising_y) and trials_after_first < max_y_origin_trials:
                best_y_origs = [item[0] for item in raw_wrappings[:2]]
                for best_y in best_y_origs:
                    for neighbor_y in (best_y - y_origin_step, best_y + y_origin_step):
                        if neighbor_y in y_origins and neighbor_y not in tested_y:
                            tested_y.add(neighbor_y)
                            prof.y_origins_tested += 1
                            t_dp0 = perf_counter()
                            cand_wrappings = _try_placement_rows(
                                geom, norm_words, word_widths, space_w,
                                S, neighbor_y, ls, line_h, stroke_width, margin,
                                zone_profile=zone_profile,
                                max_per_bucket=2,
                                row_slot_table=row_slot_table,
                            )
                            prof.dp_search_ms += (perf_counter() - t_dp0) * 1000.0
                            if cand_wrappings:
                                for placed in cand_wrappings:
                                    raw_wrappings.append((neighbor_y, placed))
                                    prof.raw_wrappings += 1
                            trials_after_first += 1
                            if trials_after_first >= max_y_origin_trials:
                                break

            if not raw_wrappings:
                continue

            # If font S produced valid wrappings and fine neighbors haven't been queued yet, add S-1, S+1
            if S not in fine_refined_fonts:
                fine_refined_fonts.add(S)
                for neighbor_S in (S + 1, S - 1, S - 2):
                    if font_size_min <= neighbor_S <= font_size_max and neighbor_S not in fine_refined_fonts:
                        fine_refined_fonts.add(neighbor_S)
                        candidate_font_queue.append(neighbor_S)

            # Phase 2: Heuristic pre-score raw wrappings, grouping by line count to preserve diversity
            by_line_cnt: Dict[int, List[Tuple[float, int, List[PlacedLine]]]] = {}
            for y_orig, placed in raw_wrappings:
                lc = len(placed)
                p_approx = _font_penalty(S, font_target)
                if source_profile is not None:
                    p_approx += abs(lc - source_profile.line_count) * _WEIGHT_SRC_LINES
                y_top = min(line.y for line in placed)
                y_bot = max(line.y + line.height for line in placed)
                span = y_bot - y_top
                p_approx += (span / max(1.0, float(zone_profile.height))) * 5.0
                if lc not in by_line_cnt:
                    by_line_cnt[lc] = []
                by_line_cnt[lc].append((p_approx, y_orig, placed))

            elite_raw: List[Tuple[float, int, List[PlacedLine]]] = []
            for lc in sorted(by_line_cnt.keys()):
                group = sorted(by_line_cnt[lc], key=lambda x: x[0])
                elite_raw.extend(group[:2])  # top 2 per line count bucket

            elite_raw.sort(key=lambda x: x[0])
            del elite_raw[max(8, top_k * 4):]
            prof.pre_score_survivors += len(elite_raw)

            for _, y_orig, placed in elite_raw:
                # 1. Vertical Compaction Pass: Continuous Y refinement & Spring chain
                t_comp0 = perf_counter()
                placed_compacted = _compact_vertical_rhythm(
                    placed, geom, S, line_h, stroke_width, margin,
                    lobe_graph=lobe_graph, row_slot_table=row_slot_table,
                )
                prof.compaction_ms += (perf_counter() - t_comp0) * 1000.0
                if not placed_compacted:
                    placed_compacted = placed

                # 2. Gap classification and validation
                t_gap0 = perf_counter()
                gap_reports = _classify_adjacent_gaps(
                    placed_compacted, geom, S, line_h, stroke_width, margin,
                    lobe_graph=lobe_graph, source_profile=source_profile
                )
                prof.gap_classification_ms += (perf_counter() - t_gap0) * 1000.0
                has_unexplained_large_gap = any(not g["valid"] for g in gap_reports)

                # 3. Whole-block recentering to target capacity center with local search
                t_cent0 = perf_counter()
                placed_centered = _center_layout_block(
                    placed_compacted, geom, target_geom, S, stroke_width, margin
                )
                prof.centering_ms += (perf_counter() - t_cent0) * 1000.0
                if placed_centered is None:
                    continue

                # 4. Internal line refinement (X optimization)
                zone_center_x = target_geom.center_x
                source_center_x = (
                    source_profile.centroid[0] - geom.x_offset
                    if source_profile is not None and not is_single_region else None
                )
                t_xopt0 = perf_counter()
                placed_opt = _optimize_x(
                    placed_centered,
                    source_center_x=source_center_x,
                    zone_center_x=zone_center_x,
                )
                prof.x_optimization_ms += (perf_counter() - t_xopt0) * 1000.0

                t_bbox0 = perf_counter()
                fast_valid, approx_p5 = _bbox_validate(placed_opt, geom, S, stroke_width, margin)
                prof.bbox_validation_ms += (perf_counter() - t_bbox0) * 1000.0
                if not fast_valid:
                    continue

                t_pen0 = perf_counter()
                penalty, qa = _composite_penalty(
                    placed_opt, geom, S, font_target, source_profile, stroke_width, margin,
                    preferred_mask=preferred_mask,
                    target_geom=target_geom,
                    zone_profile=zone_profile,
                )
                prof.composite_penalty_ms += (perf_counter() - t_pen0) * 1000.0
                qa["gap_details"] = gap_reports
                qa["gap_reasons"] = [g["reason"] for g in gap_reports]
                qa["max_gap_h"] = max((g["gap_h"] for g in gap_reports), default=1.0)
                qa["has_unexplained_gap"] = has_unexplained_large_gap

                is_cand_valid = not has_unexplained_large_gap
                cand_status = "ok" if is_cand_valid else "unexplained_gap"

                candidate = LayoutCandidate(
                    font_size=S,
                    y_origin=y_orig,
                    line_spacing=ls,
                    lines=placed_opt,
                    penalty=penalty + (500.0 if has_unexplained_large_gap else 0.0),
                    glyph_clearance_p5=approx_p5,
                    valid=is_cand_valid,
                    status=cand_status,
                    qa=qa,
                )

                candidates.append(candidate)
                prof.refined_candidates += 1

            candidates.sort(key=lambda item: (not item.valid, item.penalty))
            del candidates[max(top_k * 4, 32):]

        # Phase 7: Clear ephemeral caches on BubbleGeometry periodically
        geom.clear_ephemeral_caches()

    # Attach candidate alternatives summary to candidates' QA for diagnostics
    alts_by_lines: Dict[int, LayoutCandidate] = {}
    for cand in candidates:
        lc = len(cand.lines)
        if lc not in alts_by_lines:
            alts_by_lines[lc] = cand

    eval_summary = []
    for lc in sorted(alts_by_lines.keys()):
        c = alts_by_lines[lc]
        eval_summary.append({
            "lines": lc,
            "font_size": c.font_size,
            "vertical_utilization": c.qa.get("vertical_utilization", 0.0),
            "aspect_mismatch": c.qa.get("aspect_mismatch", 0.0),
            "p_aspect": c.qa.get("p_aspect", 0.0),
            "p_vertical_fill": c.qa.get("p_vertical_fill", 0.0),
            "p_composition": c.qa.get("p_composition", 0.0),
            "penalty": c.penalty,
        })
    for cand in candidates:
        cand.qa["candidate_alternatives"] = eval_summary
        if "zone_profile" in locals() and zone_profile is not None:
            cand.qa["zone_size"] = (zone_profile.width, zone_profile.height)
            cand.qa["zone_aspect"] = zone_profile.aspect_ratio
            cand.qa["zone_line_capacity"] = zone_profile.vertical_capacity

    valid_candidates: List[LayoutCandidate] = []
    for candidate in candidates:
        if not candidate.valid:
            continue
        t_val0 = perf_counter()
        prof.glyph_validations += 1
        valid, p5 = _validate_glyph_pixels(
            candidate.lines, geom, candidate.font_size, stroke_width, margin
        )
        prof.glyph_validation_ms += (perf_counter() - t_val0) * 1000.0
        candidate.glyph_clearance_p5 = p5
        if not valid:
            candidate.valid = False
            candidate.status = "glyph_overflow"
            continue

        if geom.x_offset or geom.y_offset:
            for ln in candidate.lines:
                ln.x += geom.x_offset
                ln.y += geom.y_offset
                ln.slot = BandSlot(
                    left=ln.slot.left + geom.x_offset,
                    right=ln.slot.right + geom.x_offset,
                    y_start=ln.slot.y_start + geom.y_offset,
                    y_end=ln.slot.y_end + geom.y_offset,
                )
        valid_candidates.append(candidate)

    if not valid_candidates and candidates:
        # Fallback to best candidate if none passed gap check, validating glyph pixels
        for candidate in candidates:
            t_val0 = perf_counter()
            prof.glyph_validations += 1
            valid, p5 = _validate_glyph_pixels(
                candidate.lines, geom, candidate.font_size, stroke_width, margin
            )
            prof.glyph_validation_ms += (perf_counter() - t_val0) * 1000.0
            if valid:
                candidate.valid = True
                candidate.glyph_clearance_p5 = p5
                if geom.x_offset or geom.y_offset:
                    for ln in candidate.lines:
                        ln.x += geom.x_offset
                        ln.y += geom.y_offset
                        ln.slot = BandSlot(
                            left=ln.slot.left + geom.x_offset,
                            right=ln.slot.right + geom.x_offset,
                            y_start=ln.slot.y_start + geom.y_offset,
                            y_end=ln.slot.y_end + geom.y_offset,
                        )
                valid_candidates.append(candidate)
                break

    if top_k > 1:
        return valid_candidates[:top_k]
    return valid_candidates[0] if valid_candidates else None


def _precompute_widths(words: List[str], font_size: int) -> Tuple[List[int], int]:
    try:
        from manga_translator.rendering import text_render
        word_widths = [int(text_render.get_string_width(font_size, w)) for w in words]
        space_w = int(text_render.get_string_width(font_size, " "))
        return word_widths, space_w
    except Exception:
        word_widths = [int(len(w) * font_size * 0.6) for w in words]
        space_w = int(font_size * 0.3)
        return word_widths, space_w


def _y_origin_sequence(y1: int, y2: int, font_size: int, step: int) -> List[int]:
    """Sample plausible starting top-Y coordinates across the safe vertical range."""
    step = max(1, step)
    max_top = y2 - font_size
    if max_top < y1:
        return [y1]

    positions = []
    y = y1
    while y <= max_top:
        positions.append(y)
        y += step
    if not positions or positions[-1] != max_top:
        positions.append(max_top)

    seen = set()
    result = []
    for p in positions:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


@dataclass
class RowGeometry:
    """One textual line-height row: a Y position plus the disjoint safe
    intervals available at that height (multi-lobe bubbles have several)."""
    y: int
    height: int
    intervals: List[BandSlot]


def _build_row_slot_table(
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    min_width: int = 8,
) -> Dict[int, List[BandSlot]]:
    """Precompute valid BandSlot horizontal intervals for every vertical y coordinate."""
    _, y1, _, y2 = geom.safe_bounding_box(font_size, stroke_width, margin)
    if y2 <= y1:
        return {}
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    h_mask, w_mask = safe.shape
    table: Dict[int, List[BandSlot]] = {}
    for y in range(max(0, y1), min(h_mask - font_size + 1, y2 - font_size + 1)):
        band_row = np.all(safe[y : y + font_size], axis=0)
        intervals = _runs_from_row(band_row)
        slots = [
            BandSlot(left=iv.left, right=iv.right, y_start=y, y_end=y + font_size)
            for iv in intervals
            if iv.right - iv.left >= min_width
        ]
        slots.sort(key=lambda s: s.left)
        table[y] = slots
    return table


def _try_placement_rows(
    geom: BubbleGeometry,
    words: List[str],
    word_widths: List[int],
    space_w: int,
    font_size: int,
    y_origin: int,
    line_spacing: float,
    line_h: int,
    stroke_width: int,
    margin: float,
    zone_profile: Optional[ZoneShapeProfile] = None,
    max_per_bucket: int = 2,
    row_slot_table: Optional[Dict[int, List[BandSlot]]] = None,
) -> List[List[PlacedLine]]:
    _, y1, _, y2 = geom.safe_bounding_box(font_size, stroke_width, margin)
    if y2 <= y1:
        return []
    rows: List[RowGeometry] = []
    min_w = max(font_size, 8)

    y = y_origin
    while y + font_size <= y2:
        if row_slot_table is not None and y in row_slot_table:
            band = [s for s in row_slot_table[y] if s.width >= min_w]
        else:
            band = geom.band_intervals(
                y, y + font_size, font_size, stroke_width, margin,
                min_width=min_w,
            )
        rows.append(RowGeometry(y=y, height=font_size, intervals=band or []))
        y += line_h

    if not rows:
        return []

    return _dp_word_break_rows(
        words, word_widths, space_w, rows, font_size,
        max_per_bucket=max_per_bucket, zone_profile=zone_profile,
        normal_gap=max(0, line_h - font_size),
    )


# Linguistic line-break guidance (Phase 5). Function words strongly prefer
# not starting a new line; a line that *is* a single function word is worse.
_FUNCTION_WORDS = {
    # articles
    "A", "AN", "THE",
    # prepositions
    "OF", "TO", "IN", "ON", "AT", "FOR", "WITH", "FROM", "BY", "AS", "INTO",
    "OVER", "AFTER", "BEFORE", "BETWEEN", "THROUGH", "UNDER", "OFF", "UP",
    "DOWN", "OUT", "ABOUT", "AROUND", "ACROSS",
    # auxiliaries / modals
    "IS", "ARE", "WAS", "WERE", "BE", "BEEN", "BEING", "AM",
    "HAVE", "HAS", "HAD", "HAVING", "DO", "DOES", "DID", "DONE",
    "WILL", "WOULD", "CAN", "COULD", "SHALL", "SHOULD", "MAY", "MIGHT",
    "MUST", "NEED", "OUGHT",
    # pronouns / possessives
    "I", "YOU", "HE", "SHE", "IT", "WE", "THEY", "ME", "HIM", "HER", "US",
    "THEM", "MY", "YOUR", "HIS", "ITS", "OUR", "THEIR", "MINE", "YOURS",
    "HERS", "OURS", "THEIRS", "THIS", "THAT", "THESE", "THOSE", "WHO",
    "WHOM", "WHOSE", "WHICH", "WHAT",
    # conjunctions
    "AND", "BUT", "OR", "NOR", "SO", "YET", "THOUGH", "ALTHOUGH", "BECAUSE",
    "WHILE", "UNTIL", "SINCE", "IF", "THAN", "THEN",
}

_PUNCT_STRIP = ".,!?;:…\"')-]["


def _word_core(word: str) -> str:
    return word.strip(_PUNCT_STRIP).upper()


def _phrase_break_penalty(prev_word: str, next_word: str) -> float:
    """Penalty for breaking the line between ``prev_word`` and ``next_word``.

    Keeps strongly connected pairs (LET ME / MAY HAVE / LOOK AT / ARE YOU)
    together when geometry allows, without any LLM in the loop.
    """
    penalty = 0.0
    if _word_core(next_word) in _FUNCTION_WORDS:
        penalty += 15.0
    if _word_core(prev_word) in _FUNCTION_WORDS:
        penalty += 4.0
    return penalty


def _line_break_cost(
    words: List[str],
    wi: int,
    end: int,
    slot_w: int,
    run_w: int,
    zone_profile: Optional[ZoneShapeProfile] = None,
    row_y: Optional[int] = None,
) -> float:
    """Soft cost of placing words[wi:end] on one row interval.

    Geometry only asks "is this line allowed here?" — slot-fill pressure is
    deliberately weak so the DP follows language, not bubble contours.
    """
    fill = run_w / max(1, slot_w)
    cost = (1.0 - fill) ** 2 * 2.0

    n_words = end - wi
    is_final = end >= len(words)

    # Check if this row is in a narrow section of the bubble
    is_narrow_row = False
    if zone_profile is not None and row_y is not None and zone_profile.bbox and zone_profile.width_by_y:
        y_base = zone_profile.bbox[1]
        rel_y = max(0, min(len(zone_profile.width_by_y) - 1, int(row_y - y_base)))
        if rel_y < len(zone_profile.width_by_y):
            avail_w = zone_profile.width_by_y[rel_y]
            if avail_w <= zone_profile.width * 0.65 or slot_w <= zone_profile.width * 0.65:
                is_narrow_row = True

    # Punctuation-only line penalty (e.g. "?!", "...", "!")
    is_punct_only = all(not w.strip(_PUNCT_STRIP) for w in words[wi:end])
    if is_punct_only:
        cost += 50.0

    # Orphans: a short isolated word stranded on its own line.
    if n_words == 1 and not is_final:
        word_core = _word_core(words[wi])
        if len(word_core) <= 3:
            # If the single word line occurs in a narrow row and fits nicely, allow it with minimal penalty
            if is_narrow_row and fill >= 0.35:
                cost += 3.0
            else:
                cost += 25.0
        elif len(words[wi]) <= 2:
            if is_narrow_row and fill >= 0.35:
                cost += 2.0
            else:
                cost += 15.0

    # A line consisting of a single function word is typographically poor unless justified by narrow geometry.
    if n_words == 1 and not is_final and _word_core(words[wi]) in _FUNCTION_WORDS:
        if is_narrow_row and fill >= 0.35:
            cost += 4.0
        else:
            cost += 20.0

    # Linguistic break quality between this line and the next.
    if not is_final:
        cost += _phrase_break_penalty(words[end - 1], words[end])

    return cost


def _transition_cost(
    prev_slot: Optional[BandSlot],
    prev_center_x: Optional[float],
    curr_slot: BandSlot,
    curr_center_x: float,
    font_size: int,
) -> float:
    """Calculate transition penalty between consecutive placed lines."""
    if prev_slot is None or prev_center_x is None:
        return 0.0

    # 1. Normalized center displacement d_x = |C_i - C_{i-1}| / S
    dx = abs(curr_center_x - prev_center_x)
    dx_norm = dx / max(1.0, float(font_size))
    # Quadratic penalty for normalized displacement
    p_xjump = (dx_norm ** 2) * _WEIGHT_TRANS_XJUMP

    # 2. Interval overlap ratio r = |I_prev ∩ I_curr| / min(|I_prev|, |I_curr|)
    overlap_left = max(prev_slot.left, curr_slot.left)
    overlap_right = min(prev_slot.right, curr_slot.right)
    overlap_w = max(0, overlap_right - overlap_left)
    min_w = max(1, min(prev_slot.width, curr_slot.width))
    overlap_ratio = overlap_w / float(min_w)

    p_overlap = ((1.0 - overlap_ratio) ** 2) * _WEIGHT_TRANS_OVERLAP

    # 3. Branch switch penalty: zero overlap and non-trivial center jump
    p_branch = 0.0
    if overlap_ratio == 0.0 and dx_norm > 0.5:
        p_branch = _WEIGHT_TRANS_BRANCH

    return p_xjump + p_overlap + p_branch


def _row_can_fit_word(row: RowGeometry, word_w: int) -> bool:
    """Check if any interval in the row is wide enough to hold at least word_w."""
    for slot in row.intervals:
        if slot.width >= word_w:
            return True
    return False


def _dp_word_break_rows(
    words: List[str],
    word_widths: List[int],
    space_w: int,
    rows: List[RowGeometry],
    font_size: int,
    max_per_bucket: int = 2,
    zone_profile: Optional[ZoneShapeProfile] = None,
    normal_gap: int = 0,
) -> List[List[PlacedLine]]:
    """Break words into a continuous paragraph stack across the safe rows.

    Usable rows are not optional once text has started. Empty or too-narrow
    rows are traversed as geometry-forced gaps, while actual line spacing is
    charged as a spring-like deformation from the normal line step.
    """
    nw = len(words)
    nr = len(rows)
    if nw == 0 or nr == 0:
        return []

    # Trailing whitespace needs no state: once all words are placed, the
    # paragraph ends and remaining rows are free.
    # Result per state: Dict[int, List[Tuple[float, List[PlacedLine]]]] (line_count -> top candidates)
    memo: Dict[Tuple[Any, ...], Dict[int, List[Tuple[float, List[PlacedLine]]]]] = {}

    BEFORE_TEXT = 0
    IN_TEXT = 1

    def _add_candidates(
        dest: Dict[int, List[Tuple[float, List[PlacedLine]]]],
        cand_lines_cnt: int,
        cand_cost: float,
        cand_lines: List[PlacedLine],
    ) -> None:
        if cand_lines_cnt not in dest:
            dest[cand_lines_cnt] = [(cand_cost, cand_lines)]
        else:
            bucket = dest[cand_lines_cnt]
            # Avoid identical line text structures
            if any(len(b[1]) == len(cand_lines) and all(l1.text == l2.text and l1.y == l2.y for l1, l2 in zip(b[1], cand_lines)) for b in bucket):
                return
            bucket.append((cand_cost, cand_lines))
            bucket.sort(key=lambda item: item[0])
            del bucket[max_per_bucket:]

    def dp(
        wi: int,
        ri: int,
        prev_slot_left: Optional[int],
        prev_slot_right: Optional[int],
        prev_center_x: Optional[float],
        prev_line_y: Optional[int],
        text_state: int,
    ) -> Dict[int, List[Tuple[float, List[PlacedLine]]]]:
        prof = get_solver_profile()
        prof.dp_invocations += 1
        if wi == nw:
            return {0: [(0.0, [])]}
        if ri == nr:
            return {}

        key = (
            wi, ri, prev_slot_left, prev_slot_right, prev_center_x,
            prev_line_y, text_state,
        )
        if key in memo:
            prof.dp_states_deduplicated += 1
            return memo[key]

        prof.dp_states_created += 1
        row = rows[ri]
        results_by_lines: Dict[int, List[Tuple[float, List[PlacedLine]]]] = {}

        prev_slot = (
            BandSlot(left=prev_slot_left, right=prev_slot_right, y_start=0, y_end=0)
            if prev_slot_left is not None and prev_slot_right is not None
            else None
        )

        # Option 1: place a run of words on one of this row's intervals.
        for slot in row.intervals:
            slot_w = slot.width
            run_w = 0
            for end in range(wi + 1, nw + 1):
                if end > wi + 1:
                    run_w += space_w
                run_w += word_widths[end - 1]
                if run_w > slot_w:
                    break

                ideal_x = int(round(slot.center - run_w / 2.0))
                x = max(slot.left, min(slot.right - run_w, ideal_x))
                curr_center_x = x + run_w / 2.0

                line_cost = _line_break_cost(
                    words, wi, end, slot_w, run_w,
                    zone_profile=zone_profile, row_y=row.y,
                )
                trans_cost = (
                    _transition_cost(prev_slot, prev_center_x, slot, curr_center_x, font_size)
                    if text_state == IN_TEXT else 0.0
                )
                spacing_cost = 0.0
                if text_state == IN_TEXT and prev_line_y is not None:
                    ideal_step = font_size + normal_gap
                    excess_step = max(0, row.y - prev_line_y - ideal_step)
                    deformation = excess_step / max(1.0, float(font_size))
                    spacing_cost = _WEIGHT_VERTICAL_GAP * deformation ** 2
                step_cost = line_cost + trans_cost + spacing_cost

                rem_dict = dp(
                    end,
                    ri + 1,
                    slot.left,
                    slot.right,
                    curr_center_x,
                    row.y,
                    IN_TEXT,
                )
                for rem_cnt, cand_list in rem_dict.items():
                    for rem_cost, rem_lines in cand_list:
                        this_line = PlacedLine(
                            text=" ".join(words[wi:end]),
                            y=row.y,
                            x=x,
                            width=run_w,
                            height=font_size,
                            slot=slot,
                        )
                        _add_candidates(
                            results_by_lines,
                            rem_cnt + 1,
                            step_cost + rem_cost,
                            [this_line] + rem_lines,
                        )

        # Option 2: traverse only a geometry-forced row. Once text has
        # started, a usable row must carry the next word rather than becoming
        # typographic whitespace.
        can_fit_next = wi < nw and _row_can_fit_word(row, word_widths[wi])
        if text_state == IN_TEXT and can_fit_next:
            skip_dict = {}
        else:
            skip_dict = dp(
                wi,
                ri + 1,
                prev_slot_left,
                prev_slot_right,
                prev_center_x,
                prev_line_y,
                text_state,
            )

        for rem_cnt, cand_list in skip_dict.items():
            for skip_cost, skip_lines in cand_list:
                _add_candidates(
                    results_by_lines,
                    rem_cnt,
                    skip_cost,
                    skip_lines,
                )

        memo[key] = results_by_lines
        return results_by_lines

    root_dict = dp(0, 0, None, None, None, None, BEFORE_TEXT)
    if not root_dict:
        return []

    candidates: List[List[PlacedLine]] = []
    for line_cnt in sorted(root_dict.keys()):
        for cost, lines in root_dict[line_cnt]:
            placed_words = sum(len(line.text.split()) for line in lines)
            if placed_words == nw:
                candidates.append(lines)

    return candidates


def _center_layout_block(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    target: PlacementTarget,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    max_search_radius: int = 24,
) -> Optional[List[PlacedLine]]:
    """Translate the entire finished text block towards the PlacementTarget center.

    Searches nearby legal integer translations (dx, dy) and validates candidate
    glyphs against the safe mask, picking the valid placement closest to ideal.
    """
    if not lines:
        return lines

    block_left = min(line.x for line in lines)
    block_right = max(line.x + line.width for line in lines)
    block_top = min(line.y for line in lines)
    block_bottom = max(line.y + line.height for line in lines)

    block_cx = (block_left + block_right) / 2.0
    block_cy = (block_top + block_bottom) / 2.0

    ideal_dx = int(round(target.center_x - block_cx))
    ideal_dy = int(round(target.center_y - block_cy))

    # Determine safe mask bounding box to bound translations early
    sx1, sy1, sx2, sy2 = geom.safe_bounding_box(font_size, stroke_width, margin)
    min_dy = sy1 - block_top
    max_dy = sy2 - block_bottom
    min_dx = sx1 - block_left
    max_dx = sx2 - block_right

    # Generate search offsets sorted by Euclidean distance from (ideal_dx, ideal_dy)
    offsets: List[Tuple[int, int]] = []
    seen = set()

    for r in range(0, max_search_radius + 1, 2):
        for step_x in range(-r, r + 1, 2):
            for step_y in range(-r, r + 1, 2):
                if max(abs(step_x), abs(step_y)) == r:
                    cand_dx = ideal_dx + step_x
                    cand_dy = ideal_dy + step_y
                    if min_dx <= cand_dx <= max_dx and min_dy <= cand_dy <= max_dy:
                        if (cand_dx, cand_dy) not in seen:
                            seen.add((cand_dx, cand_dy))
                            offsets.append((cand_dx, cand_dy))

    offsets.sort(key=lambda off: (off[0] - ideal_dx) ** 2 + (off[1] - ideal_dy) ** 2)

    h, w = geom.shape
    best_translated: Optional[List[PlacedLine]] = None

    for dx, dy in offsets:
        # Check overall block bounds first
        new_left = block_left + dx
        new_right = block_right + dx
        new_top = block_top + dy
        new_bottom = block_bottom + dy
        if new_left < 0 or new_top < 0 or new_right > w or new_bottom > h:
            continue

        translated = [
            PlacedLine(
                text=ln.text,
                y=ln.y + dy,
                x=ln.x + dx,
                width=ln.width,
                height=ln.height,
                slot=BandSlot(
                    left=ln.slot.left + dx,
                    right=ln.slot.right + dx,
                    y_start=ln.slot.y_start + dy,
                    y_end=ln.slot.y_end + dy,
                ),
            )
            for ln in lines
        ]

        valid, _ = _bbox_validate(translated, geom, font_size, stroke_width, margin)
        if valid:
            best_translated = translated
            break

    return best_translated if best_translated is not None else lines


def _optimize_x(
    lines: List[PlacedLine],
    lam1: float = 1.0,
    lam2: float = 0.5,
    lam3: float = 0.2,
    source_center_x: Optional[float] = None,
    zone_center_x: Optional[float] = None,
) -> List[PlacedLine]:
    """Greedy coordinate descent on line X positions.

    All cost terms operate on *line centers* (x + width / 2), never on left
    edges, so lines of different widths stay visually aligned.
    """
    if not lines:
        return lines

    n = len(lines)
    bounds: List[Tuple[int, int, float, int]] = []
    for line in lines:
        slot = line.slot
        L = slot.left
        R_w = max(slot.left, slot.right - line.width)
        bounds.append((L, R_w, slot.center, line.width))

    def _center(x: float, w: int) -> float:
        return x + w / 2.0

    xs = [max(b[0], min(b[1], int(round(b[2] - b[3] / 2.0))))
          for b in bounds]

    denom = lam1 + lam2 + 4.0 * lam3
    for _ in range(4):
        for i in range(n):
            L, R_w, c, w_i = bounds[i]
            if L >= R_w:
                continue
            c_prev = _center(xs[i - 1], bounds[i - 1][3]) if i > 0 else _center(xs[i], w_i)
            c_next = _center(xs[i + 1], bounds[i + 1][3]) if i < n - 1 else _center(xs[i], w_i)

            target = c
            if zone_center_x is not None:
                target = 0.50 * zone_center_x + 0.50 * target
            if source_center_x is not None:
                target = 0.70 * target + 0.30 * source_center_x
            num = lam1 * target + lam2 * c_prev + 2.0 * lam3 * (c_next + c_prev)
            X_opt = num / denom
            x_opt = X_opt - w_i / 2.0
            xs[i] = max(L, min(R_w, int(round(x_opt))))

    return [
        PlacedLine(text=line.text, y=line.y, x=xs[i],
                   width=line.width, height=line.height, slot=line.slot)
        for i, line in enumerate(lines)
    ]


def _x_cost(center_x: float, c: float, c_prev: float, c_next: float,
            lam1: float, lam2: float, lam3: float) -> float:
    """Cost on line centers: slot center attraction + jitter + curvature."""
    return (
        lam1 * (center_x - c) ** 2
        + lam2 * (center_x - c_prev) ** 2
        + lam3 * (c_next - 2.0 * center_x + c_prev) ** 2
    )


def _bbox_validate(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    epsilon: int = 0,
) -> Tuple[bool, float]:
    h, w = geom.shape
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    dist = geom.dist
    outside = 0
    dt_vals: List[float] = []

    for line in lines:
        lx, ly, lw, lh = line.x, line.y, line.width, line.height
        if lx < 0 or ly < 0 or lx + lw > w or ly + lh > h:
            return False, 0.0
        for sx, sy in [
            (lx, ly), (lx + lw - 1, ly),
            (lx, ly + lh - 1), (lx + lw - 1, ly + lh - 1),
            (lx + lw // 2, ly + lh // 2),
            (lx + lw // 4, ly + lh // 2),
            (lx + 3 * lw // 4, ly + lh // 2),
        ]:
            sx = max(0, min(w - 1, sx))
            sy = max(0, min(h - 1, sy))
            if not safe[sy, sx]:
                outside += 1
            dt_vals.append(float(dist[sy, sx]))

    if outside > epsilon:
        return False, 0.0
    p5 = float(np.percentile(dt_vals, 5)) if dt_vals else 0.0
    return True, p5


def _validate_glyph_pixels(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    epsilon: int = 3,
    clearance_percentile: int = 5,
) -> Tuple[bool, float]:
    h, w = geom.shape
    safe = geom.safe_pixels(font_size, stroke_width, margin)
    dist = geom.dist
    glyph_dt_values: List[float] = []
    outside_count = 0

    for line in lines:
        ly, lx = line.y, line.x
        lw, lh = line.width, line.height

        if lx < 0 or ly < 0 or lx + lw > w or ly + lh > h:
            return False, 0.0

        alpha = _render_line_alpha(line, font_size)
        if alpha is not None:
            ay, ax = alpha.shape[:2]
            x2 = min(lx + ax, w)
            y2 = min(ly + ay, h)
            axc = x2 - lx
            ayc = y2 - ly
            if axc <= 0 or ayc <= 0:
                return False, 0.0
            glyph_mask = alpha[:ayc, :axc] > 127
            if np.any(glyph_mask):
                outside_count += int(np.sum(glyph_mask & ~safe[ly:y2, lx:x2]))
                glyph_dt_values.extend(dist[ly:y2, lx:x2][glyph_mask].tolist())
        else:
            ok, _ = _bbox_validate([line], geom, font_size, stroke_width, margin, epsilon)
            if not ok:
                return False, 0.0

    if outside_count > epsilon:
        return False, 0.0

    if not glyph_dt_values:
        return True, 0.0

    p5 = float(np.percentile(glyph_dt_values, clearance_percentile))
    return True, p5


_LINE_ALPHA_CACHE: Dict[Tuple[str, int, int], Optional[np.ndarray]] = {}


def _render_line_alpha(line: PlacedLine, font_size: int) -> Optional[np.ndarray]:
    global _LINE_ALPHA_CACHE
    key = (line.text, line.width, font_size)
    if key in _LINE_ALPHA_CACHE:
        cached = _LINE_ALPHA_CACHE[key]
        return cached.copy() if cached is not None else None

    try:
        from manga_translator.rendering import text_render
        canvas = np.zeros((font_size + 4, line.width + font_size + 4), dtype=np.uint8)
        border = canvas.copy()
        pen = [0, font_size]
        for c in line.text:
            adv = text_render.put_char_horizontal(font_size, c, pen, canvas, border, border_size=0)
            pen[0] += adv
        if len(_LINE_ALPHA_CACHE) >= 1024:
            first_k = next(iter(_LINE_ALPHA_CACHE))
            del _LINE_ALPHA_CACHE[first_k]
        _LINE_ALPHA_CACHE[key] = canvas
        return canvas.copy()
    except Exception:
        if len(_LINE_ALPHA_CACHE) >= 1024:
            first_k = next(iter(_LINE_ALPHA_CACHE))
            del _LINE_ALPHA_CACHE[first_k]
        _LINE_ALPHA_CACHE[key] = None
        return None


# ---------------------------------------------------------------------------
# Phase 2 — text normalization (OCR hyphen-split repair)
# ---------------------------------------------------------------------------

# Hyphenated compounds that must survive normalization (never join these).
_COMPOUND_PREFIXES = {
    "X", "E", "SELF", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY",
    "SEVENTY", "EIGHTY", "NINETY", "WELL", "HALF", "FULL", "ALL",
    "PRO", "ANTI", "MID", "NEO", "PAN", "SUPER", "ULTRA", "MULTI",
}

_DICTIONARY_CACHE: Optional[Optional[set]] = None
_DICTIONARY_PATHS = (
    "/usr/share/dict/words",
    "/usr/share/dict/web2",
    "/usr/dict/words",
)


def _load_dictionary() -> Optional[set]:
    """Load a system word list for hyphenation evidence; None if unavailable."""
    global _DICTIONARY_CACHE
    if _DICTIONARY_CACHE is not None:
        return _DICTIONARY_CACHE
    words: Optional[set] = None
    for path in _DICTIONARY_PATHS:
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    words = {ln.strip().lower() for ln in f if ln.strip()}
                break
        except OSError:
            continue
    _DICTIONARY_CACHE = words
    return words


def _dict_contains(word: str) -> bool:
    dictionary = _load_dictionary()
    if dictionary is None:
        return False
    return word.lower() in dictionary


def normalize_words(words: List[str]) -> List[str]:
    """Repair OCR hyphen splits so ordinary words are atomic for the DP.

    "GOT- TEN" -> "GOTTEN", "IN- JURED" -> "INJURED", "SCRATCH- ES." ->
    "SCRATCHES." — but real compounds ("TWENTY-ONE", "SELF-DEFENSE",
    "X-RAY") stay intact. Decisions use dictionary evidence plus compound
    prefixes; when no dictionary exists, a conservative heuristic keeps the
    hyphen unless both fragments look like non-words (split proper names).
    """
    import re as _re

    dictionary = _load_dictionary()
    out: List[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        m_next = (
            _re.match(r"^([A-Za-z]+)([.,!?;:…]*)$", words[i + 1])
            if (w.endswith("-") and not w.endswith("--") and len(w) > 1 and i + 1 < len(words))
            else None
        )
        if m_next:
            frag1 = w[:-1]
            frag2, punct = m_next.group(1), m_next.group(2)

            # Known compound prefix: never join (TWENTY-ONE, X-RAY, ...).
            if frag1.upper() in _COMPOUND_PREFIXES:
                out.append(w)
                i += 1
                continue

            joined = frag1 + frag2
            if dictionary is not None:
                join_is_word = joined.lower() in dictionary
                frag1_is_word = frag1.lower() in dictionary
                frag2_is_word = frag2.lower() in dictionary
                if join_is_word:
                    out.append(joined + punct)
                    i += 2
                    continue
                # Split proper names / unlisted words: "HA- RUTO" -> "HARUTO".
                if not frag1_is_word and not frag2_is_word and len(joined) >= 4:
                    out.append(joined + punct)
                    i += 2
                    continue
            else:
                # No dictionary: join only when the second fragment cannot
                # stand alone (lowercase start strongly suggests a fragment).
                if frag2[:1].islower() and frag1[:1].isupper():
                    out.append(joined + punct)
                    i += 2
                    continue

        out.append(w)
        i += 1

    # Attach floating punctuation tokens (e.g. "?!", "...", "!") to preceding word
    cleaned: List[str] = []
    for w in out:
        if cleaned and not w.strip(_PUNCT_STRIP) and len(w) > 0:
            cleaned[-1] = cleaned[-1] + w
        else:
            cleaned.append(w)
    return cleaned


# ---------------------------------------------------------------------------
# Phase 3/7 — original layout profile (the runner's ground truth)
# ---------------------------------------------------------------------------

@dataclass
class OriginalLayoutProfile:
    """Where the original typesetter placed the text — an artistic prior."""
    font_size: float
    line_count: int
    lines: List[Dict[str, Any]]          # text, center_x, center_y, width, height
    centroid: Tuple[float, float]        # ink-weighted text centroid
    bbox: Tuple[int, int, int, int]      # union of original line boxes
    block_width: float
    block_height: float
    occupancy: float                     # block area / bubble interior area
    normalized_centroid: Tuple[float, float] = (0.5, 0.5)  # (u, v) normalized to bubble bounds


@dataclass
class BubbleLayoutGroup:
    """Encapsulates a shared bubble geometry and its individual source TextBlocks."""
    bubble_mask: np.ndarray
    interior: np.ndarray
    regions: List[Any]
    lobe_graph: Optional[LobeGraph] = None
    zones: List[np.ndarray] = field(default_factory=list)


class PlacementMode(str, Enum):
    """The only dispatch decision shared by bubble and free-text placement."""

    BUBBLE = "BUBBLE"
    FREE_TEXT = "FREE_TEXT"


@dataclass
class PageObstacleMap:
    """Read-only page geometry exposed to the free-text solver."""

    bubble_mask: np.ndarray
    protected_bubble_mask: np.ndarray
    text_mask: np.ndarray
    panel_mask: np.ndarray


def _region_source_mask(region: Any, shape: Tuple[int, int]) -> np.ndarray:
    """Rasterize one captured OCR footprint in page coordinates."""
    mask = np.zeros(shape, dtype=np.uint8)
    lines = getattr(region, "lines", None)
    if lines is not None:
        polygons = []
        raw_lines = np.asarray(lines)
        if raw_lines.size and raw_lines.ndim == 2:
            raw_lines = raw_lines[None, ...]
        for line in raw_lines if raw_lines.size else []:
            polygon = np.asarray(line, dtype=np.int32).reshape(-1, 2)
            if len(polygon) >= 3:
                polygons.append(polygon)
        if polygons:
            cv2.fillPoly(mask, polygons, 1)
    if not np.any(mask):
        bounds = getattr(region, "xyxy", None)
        if bounds is None:
            bounds = getattr(region, "layout_bounds", None)
        if bounds is not None and len(bounds) == 4:
            x1, y1, x2, y2 = [int(round(v)) for v in bounds]
            mask[max(0, y1):min(shape[0], y2), max(0, x1):min(shape[1], x2)] = 1
    return mask


def _bubble_assignment_is_valid(region: Any) -> bool:
    interior = getattr(region, "_bubble_interior", None)
    return interior is not None and np.any(interior)


def classify_placement_modes(regions: List[Any]) -> List[Any]:
    """Freeze bubble/free-text classification after bubble association."""
    for index, region in enumerate(regions or []):
        mode = PlacementMode.BUBBLE if _bubble_assignment_is_valid(region) else PlacementMode.FREE_TEXT
        region.placement_mode = mode
        region._placement_mode = mode.value
        region._placement_debug_label = (
            f"region {index} → {mode.value}"
            + (f" #{getattr(region, 'group_id', '')}" if mode is PlacementMode.BUBBLE and getattr(region, "group_id", None) else "")
        )
    return regions


def build_page_obstacle_map(
    regions: List[Any],
    image_shape: Tuple[int, int],
    bubble_halo: int = 4,
) -> PageObstacleMap:
    """Build obstacles once; free-text layout never mutates bubble geometry."""
    h, w = image_shape[:2]
    bubble_mask = np.zeros((h, w), dtype=np.uint8)
    text_mask = np.zeros((h, w), dtype=np.uint8)
    for region in regions or []:
        source = _region_source_mask(region, (h, w))
        text_mask = cv2.bitwise_or(text_mask, source)
        if getattr(region, "placement_mode", None) is not PlacementMode.BUBBLE:
            continue
        assigned = getattr(region, "_bubble_mask", None)
        if assigned is None or not np.any(assigned):
            assigned = getattr(region, "_bubble_interior", None)
        if assigned is None or not np.any(assigned):
            continue
        assigned = np.asarray(assigned)
        if assigned.shape != (h, w):
            assigned = cv2.resize(assigned.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
        bubble_mask = cv2.bitwise_or(bubble_mask, (assigned > 0).astype(np.uint8))

    halo = max(0, int(bubble_halo))
    if halo:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (halo * 2 + 1, halo * 2 + 1))
        protected = cv2.dilate(bubble_mask, kernel)
    else:
        protected = bubble_mask.copy()
    return PageObstacleMap(
        bubble_mask=bubble_mask,
        protected_bubble_mask=protected,
        text_mask=text_mask,
        panel_mask=np.ones((h, w), dtype=np.uint8),
    )


@dataclass
class FreeTextDamageTarget:
    """The source-plus-erased footprint that anchors one free-text paragraph."""

    mask: np.ndarray
    centroid_x: float
    centroid_y: float
    bbox: Tuple[int, int, int, int]
    area: int
    width: int
    height: int
    source_centroid: Tuple[float, float]
    source_bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)
    inpaint_bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)
    inpaint_centroid: Tuple[float, float] = (0.0, 0.0)


@dataclass
class FreeTextZone:
    """Disjoint spatial territory and damage footprint assigned to one free-text region."""

    source_bbox: Tuple[int, int, int, int]
    ownership_mask: np.ndarray          # Allowed legal zone (bool / uint8)
    obstacle_mask: np.ndarray           # Forbidden area: bubbles + halo + other text + outside
    coverage_target_mask: np.ndarray    # Damaged/inpainted pixels assigned to this region
    coverable_damage_mask: np.ndarray   # Damaged pixels that fall inside allowed mask
    coverage_weight_map: np.ndarray     # Normalized distance-transform weights W(p) >= 1.0 on damage
    core_damage_mask: np.ndarray        # High-importance center/core damage pixels
    total_coverable_weight: float = 1.0
    total_core_coverable: int = 0
    damage_target: Optional[FreeTextDamageTarget] = None


def _extract_region_damage_masks(
    regions: List[Any],
    shape: Tuple[int, int],
    inpaint_mask: Optional[np.ndarray] = None,
) -> Dict[int, np.ndarray]:
    """Assign captured inpaint/text-removal mask pixels to individual text regions."""
    h, w = shape[:2]
    free_regions = [
        r for r in regions or []
        if getattr(r, "placement_mode", None) is PlacementMode.FREE_TEXT
    ]
    if not free_regions:
        return {}

    source_masks = [_region_source_mask(r, (h, w)) > 0 for r in free_regions]
    if inpaint_mask is not None and np.any(inpaint_mask):
        raw_mask = (inpaint_mask > 0).astype(np.uint8)
        if raw_mask.shape[:2] != (h, w):
            raw_mask = cv2.resize(raw_mask, (w, h), interpolation=cv2.INTER_NEAREST)
    else:
        raw_mask = np.zeros((h, w), dtype=np.uint8)

    # Scope the shared mask around each source region before assigning ownership.
    # Otherwise one free-text region would claim every unrelated erased pixel on the page.
    scopes = []
    distances = []
    for region, source in zip(free_regions, source_masks):
        profile = getattr(region, "_source_profile", None)
        font_s = max(8.0, float(profile.font_size if profile else getattr(region, "font_size", 12) or 12))
        radius = max(3, int(round(font_s * 1.5)))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        scopes.append(cv2.dilate(source.astype(np.uint8), kernel) > 0)
        distances.append(cv2.distanceTransform((~source).astype(np.uint8), cv2.DIST_L2, 5))
    scope_union = np.any(np.stack(scopes, axis=0), axis=0)
    owner = np.argmin(np.stack(distances, axis=0), axis=0)
    active_damage = (raw_mask > 0) & scope_union

    region_damages: Dict[int, np.ndarray] = {}
    for idx, r in enumerate(free_regions):
        claimed = (owner == idx) & active_damage & scopes[idx]
        # Keep the exact pixels sent to inpainting. Source geometry is only a fallback
        # for legacy captures that did not persist a mask.
        claimed |= source_masks[idx] if not np.any(raw_mask) else False
        region_damages[id(r)] = claimed.astype(np.uint8)

    return region_damages


def build_free_text_ownership_zones(
    regions: List[Any],
    obstacles: PageObstacleMap,
    inpaint_mask: Optional[np.ndarray] = None,
) -> Dict[int, FreeTextZone]:
    """Assign all free-text seeds simultaneously to disjoint FreeTextZones."""
    free_regions = [
        region for region in regions or []
        if getattr(region, "placement_mode", None) is PlacementMode.FREE_TEXT
    ]
    if not free_regions:
        return {}

    shape = obstacles.panel_mask.shape[:2]
    h, w = shape
    forbidden_global = (
        (obstacles.protected_bubble_mask > 0)
        | (obstacles.panel_mask == 0)
    )
    available = (obstacles.panel_mask > 0) & ~forbidden_global

    seeds = [_region_source_mask(region, shape) > 0 for region in free_regions]
    distances = [
        cv2.distanceTransform((~seed).astype(np.uint8), cv2.DIST_L2, 5)
        for seed in seeds
    ]
    owner = np.argmin(np.stack(distances, axis=0), axis=0)
    damage_dict = _extract_region_damage_masks(free_regions, shape, inpaint_mask=inpaint_mask)

    zones: Dict[int, FreeTextZone] = {}
    for index, region in enumerate(free_regions):
        rid = id(region)
        ownership = (owner == index) & available
        raw_damage = damage_dict.get(rid, seeds[index].astype(np.uint8)) > 0
        damage_mask = raw_damage & (owner == index)  # Don't let regions fight over damage
        if not np.any(damage_mask):
            damage_mask = seeds[index].copy()

        # Build distance transform & weight map over damage
        damage_dt = cv2.distanceTransform(damage_mask.astype(np.uint8), cv2.DIST_L2, 5)
        max_dt = float(damage_dt.max())
        if max_dt > 0.0:
            norm_dt = damage_dt / max_dt
            weight_map = np.where(damage_mask, 1.0 + 1.5 * norm_dt, 0.0)
            core_mask = damage_mask & (norm_dt >= 0.50)
        else:
            weight_map = np.where(damage_mask, 1.0, 0.0)
            core_mask = damage_mask.copy()

        # Other text obstacles (all other sources)
        other_text_mask = obstacles.text_mask.astype(bool) & ~seeds[index]
        obstacle_mask = forbidden_global | other_text_mask | ~ownership
        coverable_damage = damage_mask & ownership & ~forbidden_global & ~other_text_mask

        # Precompute total weights for instant local cropped coverage checks
        core_m = core_mask > 0
        core_coverable = core_m & coverable_damage
        core_total = int(np.sum(core_coverable))
        total_w = float(np.sum(weight_map[coverable_damage]))
        if total_w <= 0.0:
            total_w = float(np.sum(coverable_damage))
        if total_w <= 0.0:
            total_w = 1.0

        # Derive source bounding box
        ys, xs = np.nonzero(seeds[index])
        if len(xs):
            s_bbox = (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1))
        else:
            s_bbox = (0, 0, w, h)

        damage_centroid, _ = _mask_moments(damage_mask)
        source_centroid, _ = _mask_moments(seeds[index])
        dys, dxs = np.nonzero(damage_mask)
        if len(dxs):
            d_bbox = (int(dxs.min()), int(dys.min()), int(dxs.max() + 1), int(dys.max() + 1))
        else:
            d_bbox = s_bbox
        target_mask = (damage_mask > 0) | seeds[index]
        target_centroid, _ = _mask_moments(target_mask)
        tys, txs = np.nonzero(target_mask)
        target_bbox = (
            (int(txs.min()), int(tys.min()), int(txs.max() + 1), int(tys.max() + 1))
            if len(txs) else s_bbox
        )
        damage_target = FreeTextDamageTarget(
            mask=target_mask.astype(np.uint8),
            centroid_x=float(target_centroid[0]),
            centroid_y=float(target_centroid[1]),
            bbox=target_bbox,
            area=int(np.count_nonzero(target_mask)),
            width=max(0, target_bbox[2] - target_bbox[0]),
            height=max(0, target_bbox[3] - target_bbox[1]),
            source_centroid=(float(source_centroid[0]), float(source_centroid[1])),
            source_bbox=s_bbox,
            inpaint_bbox=d_bbox,
            inpaint_centroid=(float(damage_centroid[0]), float(damage_centroid[1])),
        )

        ft_zone = FreeTextZone(
            source_bbox=s_bbox,
            ownership_mask=ownership.astype(np.uint8),
            obstacle_mask=obstacle_mask.astype(np.uint8),
            coverage_target_mask=damage_mask.astype(np.uint8),
            coverable_damage_mask=coverable_damage.astype(np.uint8),
            coverage_weight_map=weight_map.astype(np.float32),
            core_damage_mask=core_mask.astype(np.uint8),
            total_coverable_weight=total_w,
            total_core_coverable=core_total,
            damage_target=damage_target,
        )
        zones[rid] = ft_zone
        region._free_text_source_mask = seeds[index].astype(np.uint8)
        region._free_text_inpaint_mask = damage_mask.astype(np.uint8)
        region._free_text_ownership_mask = ownership.astype(np.uint8)
        region._free_text_zone = ft_zone

    return zones


def _candidate_cropped_visual_masks(
    candidate: LayoutCandidate,
    stroke_width: int,
    image_shape: Tuple[int, int],
) -> Tuple[Tuple[int, int, int, int], np.ndarray, np.ndarray, np.ndarray]:
    """Rasterize candidate ink_mask, visual_mask (ink + stroke), and block_mask in a local bounding box crop."""
    h, w = image_shape[:2]
    if not candidate.lines:
        return (0, 0, 0, 0), np.zeros((0, 0), bool), np.zeros((0, 0), bool), np.zeros((0, 0), bool)

    b_left = min(line.x for line in candidate.lines)
    b_top = min(line.y for line in candidate.lines)
    b_right = max(line.x + line.width for line in candidate.lines)
    b_bottom = max(line.y + line.height for line in candidate.lines)

    radius = max(1, int(stroke_width))
    pad = radius + 2
    cx1 = max(0, b_left - pad)
    cy1 = max(0, b_top - pad)
    cx2 = min(w, b_right + pad)
    cy2 = min(h, b_bottom + pad)

    ch = cy2 - cy1
    cw = cx2 - cx1
    if ch <= 0 or cw <= 0:
        return (cx1, cy1, cx2, cy2), np.zeros((0, 0), bool), np.zeros((0, 0), bool), np.zeros((0, 0), bool)

    ink_crop = np.zeros((ch, cw), dtype=bool)
    block_crop = np.zeros((ch, cw), dtype=bool)

    for line in candidate.lines:
        lx1 = max(0, line.x - cx1)
        ly1 = max(0, line.y - cy1)
        lx2 = min(cw, line.x + line.width - cx1)
        ly2 = min(ch, line.y + line.height - cy1)
        if lx2 > lx1 and ly2 > ly1:
            block_crop[ly1:ly2, lx1:lx2] = True

        alpha = _render_line_alpha(line, candidate.font_size)
        if alpha is None:
            if lx2 > lx1 and ly2 > ly1:
                ink_crop[ly1:ly2, lx1:lx2] = True
        else:
            glyph = alpha > 127
            ay, ax = glyph.shape
            gx1 = max(0, line.x - cx1)
            gy1 = max(0, line.y - cy1)
            gx2 = min(cw, line.x + ax - cx1)
            gy2 = min(ch, line.y + ay - cy1)
            if gx2 > gx1 and gy2 > gy1:
                ink_crop[gy1:gy2, gx1:gx2] |= glyph[:gy2 - gy1, :gx2 - gx1]

    if stroke_width > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        vis_crop = cv2.dilate(ink_crop.astype(np.uint8), kernel) > 0
    else:
        vis_crop = ink_crop.copy()
    return (cx1, cy1, cx2, cy2), ink_crop, vis_crop, block_crop


def _measure_damage_coverage_crop(
    crop_box: Tuple[int, int, int, int],
    vis_crop: np.ndarray,
    ink_crop: np.ndarray,
    block_crop: np.ndarray,
    zone: FreeTextZone,
) -> Dict[str, float]:
    total_w = zone.total_coverable_weight
    if total_w <= 0.0:
        return {
            "c_ink": 1.0,
            "c_visual": 1.0,
            "c_block": 1.0,
            "c_damage": 1.0,
            "c_core": 1.0,
            "u_damage": 0.0,
        }

    cx1, cy1, cx2, cy2 = crop_box
    cov_crop = (zone.coverable_damage_mask[cy1:cy2, cx1:cx2] > 0)
    w_crop = zone.coverage_weight_map[cy1:cy2, cx1:cx2]

    cov_ink = float(np.sum(w_crop[cov_crop & ink_crop])) / total_w
    cov_visual = float(np.sum(w_crop[cov_crop & vis_crop])) / total_w
    cov_block = float(np.sum(w_crop[cov_crop & block_crop])) / total_w

    # Coverage is earned by actual glyph pixels.  The allocated line rectangle
    # remains a diagnostic, but can never inflate the placement objective.
    c_damage = max(0.0, min(1.0, cov_ink))

    core_total = zone.total_core_coverable
    if core_total > 0:
        core_crop = (zone.core_damage_mask[cy1:cy2, cx1:cx2] > 0) & cov_crop
        c_core = float(np.sum(ink_crop & core_crop)) / core_total
    else:
        c_core = cov_visual

    return {
        "c_ink": cov_ink,
        "c_visual": cov_visual,
        "c_block": cov_block,
        "c_damage": c_damage,
        "c_core": c_core,
        "u_damage": 1.0 - c_damage,
    }


def _candidate_visual_masks(
    candidate: LayoutCandidate,
    image_shape: Tuple[int, int],
    stroke_width: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rasterize candidate ink_mask, visual_mask (ink + stroke), and block_mask."""
    h, w = image_shape[:2]
    crop_box, ink_c, vis_c, blk_c = _candidate_cropped_visual_masks(candidate, stroke_width, (h, w))
    cx1, cy1, cx2, cy2 = crop_box
    ink_mask = np.zeros((h, w), dtype=bool)
    visual_mask = np.zeros((h, w), dtype=bool)
    block_mask = np.zeros((h, w), dtype=bool)
    if cx2 > cx1 and cy2 > cy1:
        ink_mask[cy1:cy2, cx1:cx2] = ink_c
        visual_mask[cy1:cy2, cx1:cx2] = vis_c
        block_mask[cy1:cy2, cx1:cx2] = blk_c
    return ink_mask, visual_mask, block_mask


def _free_text_words(text: str) -> List[str]:
    """Tokenize translated free text without inventing word fragments."""
    words: List[str] = []
    for word in text.replace("\n", " ").split():
        if words and not word.strip(_PUNCT_STRIP):
            words[-1] += word
        else:
            words.append(word)
    return words


_FREE_TEXT_MAX_LINE_SPACING = 0.25


def _free_text_line_spacing(value: Optional[float]) -> float:
    """Keep free-text leading typographic, never a source-height control."""
    return min(_FREE_TEXT_MAX_LINE_SPACING, max(0.0, float(value or 0.0)))


def _free_text_font_metrics(font_size: int) -> Dict[str, int]:
    """Return the active face's metric height and natural baseline advance."""
    try:
        from manga_translator.rendering import text_render

        if not text_render.FONT_SELECTION:
            text_render.set_font(get_default_eng_font())
        face = text_render.FONT_SELECTION[0]
        face.set_pixel_sizes(0, font_size)
        ascender = face.size.ascender >> 6
        descender = face.size.descender >> 6
        metric_height = max(1, ascender - descender)
        natural_advance = max(metric_height, face.size.height >> 6)
    except Exception:
        metric_height = max(1, int(math.ceil(font_size * 1.15)))
        natural_advance = metric_height
    return {
        "font_metric_height": int(metric_height),
        "natural_advance": int(natural_advance),
    }


def _free_text_line_height(font_size: int, line_spacing: float) -> int:
    metrics = _free_text_font_metrics(font_size)
    gap = int(round(font_size * _free_text_line_spacing(line_spacing)))
    return metrics["natural_advance"] + gap


def _mask_metrics(mask: np.ndarray, origin: Tuple[int, int] = (0, 0)) -> Dict[str, Any]:
    """Measure actual occupied pixels, rather than their allocated rectangle."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        x, y = origin
        return {
            "bbox": (x, y, x, y),
            "centroid": (float(x), float(y)),
            "width": 0,
            "height": 0,
            "area": 0,
        }
    ox, oy = origin
    return {
        "bbox": (int(xs.min()) + ox, int(ys.min()) + oy, int(xs.max()) + ox + 1, int(ys.max()) + oy + 1),
        "centroid": (ox + float(xs.mean()), oy + float(ys.mean())),
        "width": int(xs.max() - xs.min() + 1),
        "height": int(ys.max() - ys.min() + 1),
        "area": int(len(xs)),
    }


def _free_text_candidate_ink_metrics(candidate: LayoutCandidate) -> Dict[str, Any]:
    """Rasterize one page-independent candidate and report its true ink geometry."""
    if not candidate.lines:
        return _mask_metrics(np.zeros((0, 0), dtype=bool))

    right = max(line.x + line.width for line in candidate.lines) + candidate.font_size + 4
    bottom = max(line.y + line.height for line in candidate.lines) + candidate.font_size + 4
    _, ink_crop, _, _ = _candidate_cropped_visual_masks(
        candidate, 0, (max(1, bottom), max(1, right))
    )
    metrics = _mask_metrics(ink_crop)
    line_boxes = []
    for line in candidate.lines:
        alpha = _render_line_alpha(line, candidate.font_size)
        if alpha is None:
            continue
        line_metrics = _mask_metrics(alpha > 127, (line.x, line.y))
        if line_metrics["area"]:
            line_boxes.append(line_metrics["bbox"])

    line_gaps = [
        max(0, line_boxes[index + 1][1] - line_boxes[index][3])
        for index in range(len(line_boxes) - 1)
    ]
    metric = _free_text_font_metrics(candidate.font_size)
    advances = [candidate.lines[index + 1].y - candidate.lines[index].y for index in range(len(candidate.lines) - 1)]
    metrics.update({
        "ink_bbox": metrics["bbox"],
        "ink_width": metrics["width"],
        "ink_height": metrics["height"],
        "ink_area": metrics["area"],
        "ink_centroid": metrics["centroid"],
        "ink_line_height": int(round(np.mean([b[3] - b[1] for b in line_boxes]))) if line_boxes else 0,
        "font_metric_height": metric["font_metric_height"],
        "baseline_advance": int(round(np.mean(advances))) if advances else metric["natural_advance"],
        "baseline_advance_min": min(advances) if advances else metric["natural_advance"],
        "baseline_advance_max": max(advances) if advances else metric["natural_advance"],
        "visible_gap": max(line_gaps, default=0),
        "max_visible_gap": max(line_gaps, default=0),
    })
    return metrics


def _free_text_wrap_candidate(
    words: List[str],
    widths: List[int],
    space_width: int,
    font_size: int,
    line_spacing: float,
    line_count: int,
    target_width: float,
) -> Optional[LayoutCandidate]:
    """Build one ordinary paragraph shape; every word stays atomic."""
    if not words or line_count < 1 or line_count > len(words):
        return None

    states: Dict[Tuple[int, int], Tuple[float, List[Tuple[int, int, int]]]] = {(0, 0): (0.0, [])}
    for used in range(line_count):
        next_states: Dict[Tuple[int, int], Tuple[float, List[Tuple[int, int, int]]]] = {}
        for (start, _), (cost, chunks) in states.items():
            max_end = len(words) - (line_count - used - 1)
            run_width = 0
            for end in range(start + 1, max_end + 1):
                run_width += widths[end - 1]
                if end - start > 1:
                    run_width += space_width
                remaining = line_count - used - 1
                remaining_words = len(words) - end
                if remaining_words < remaining:
                    continue

                line_cost = ((run_width - target_width) / max(1.0, float(font_size))) ** 2
                if remaining:
                    line_cost += _phrase_break_penalty(words[end - 1], words[end])
                    if end - start == 1 and len(words[end - 1].strip(_PUNCT_STRIP)) <= 3:
                        line_cost += 18.0
                new_cost = cost + line_cost
                key = (end, used + 1)
                old = next_states.get(key)
                if old is None or new_cost < old[0]:
                    next_states[key] = (new_cost, chunks + [(start, end, run_width)])
        states = next_states

    state = states.get((len(words), line_count))
    if state is None:
        return None

    _, chunks = state
    line_height = _free_text_line_height(font_size, line_spacing)
    max_width = max(chunk[2] for chunk in chunks)
    lines: List[PlacedLine] = []
    for row, (start, end, width) in enumerate(chunks):
        x = int(round((max_width - width) / 2.0))
        slot = BandSlot(left=0, right=max_width, y_start=row * line_height, y_end=row * line_height + font_size)
        lines.append(PlacedLine(
            text=" ".join(words[start:end]),
            y=row * line_height,
            x=x,
            width=width,
            height=font_size,
            slot=slot,
        ))
    return LayoutCandidate(
        font_size=font_size,
        y_origin=0,
        line_spacing=line_spacing,
        lines=lines,
        penalty=0.0,
        glyph_clearance_p5=0.0,
        status="free_text_typography",
        valid=True,
    )


def _free_text_typography_score(
    candidate: LayoutCandidate,
    profile: OriginalLayoutProfile,
    target: Optional[FreeTextDamageTarget] = None,
) -> float:
    """Score real glyph footprint without page coordinates or obstacle geometry."""
    ink = _free_text_candidate_ink_metrics(candidate)
    widths = [float(line.width) for line in candidate.lines]
    target_width = float(target.width if target is not None else profile.block_width)
    target_height = float(target.height if target is not None else profile.block_height)
    target_width = max(1.0, target_width)
    target_height = max(1.0, target_height)
    target_ar = max(0.05, target_width / target_height)
    ink_width = max(1.0, float(ink["ink_width"]))
    ink_height = max(1.0, float(ink["ink_height"]))
    ink_ar = max(0.05, ink_width / ink_height)

    # Tall targets get a stronger height term; wide targets get a stronger width term.
    width_weight, height_weight = (1.35, 1.0) if target_ar >= 1.0 else (1.0, 1.35)
    footprint_penalty = (
        width_weight * abs(ink_width - target_width) / target_width
        + height_weight * abs(ink_height - target_height) / target_height
    ) * 6.0
    aspect_penalty = abs(math.log(ink_ar / target_ar)) * 5.0
    mean_width = max(1.0, float(np.mean(widths)))
    ragged_penalty = float(np.var(widths)) / (mean_width * mean_width) * 8.0
    orphan_penalty = sum(
        12.0 for line in candidate.lines[:-1]
        if len(line.text.split()) == 1 and len(_word_core(line.text)) <= 3
    )
    line_penalty = abs(len(candidate.lines) - profile.line_count) * 1.5
    font_penalty = abs(candidate.font_size - profile.font_size) / max(1.0, profile.font_size) * 8.0
    return footprint_penalty + aspect_penalty + ragged_penalty + orphan_penalty + line_penalty + font_penalty


def _free_text_typography_candidates(
    text: str,
    profile: OriginalLayoutProfile,
    config: Config,
    image_shape: Tuple[int, int],
    target: Optional[FreeTextDamageTarget] = None,
    target_height: Optional[int] = None,
) -> List[LayoutCandidate]:
    """Generate frozen paragraph candidates; ``target_height`` is legacy-only."""
    # Keep the old keyword source-compatible, but never turn erased height into leading.
    del target_height
    words = _free_text_words(text)
    if not words:
        return []

    render_cfg = config.render
    minimum = render_cfg.font_size_minimum
    if minimum == -1:
        minimum = round(sum(image_shape) / 200)
    minimum = max(1, int(minimum))
    source_font = max(1, int(round(profile.font_size)))
    source_max = max(source_font, int(round(profile.font_size * 1.15)))
    if render_cfg.font_size is not None:
        font_sizes = [max(minimum, min(source_max, int(render_cfg.font_size)))]
    else:
        font_sizes = sorted({
            max(minimum, min(source_max, int(round(source_font * scale))))
            for scale in (1.15, 1.08, 1.00, 0.92, 0.84, 0.76, 0.68, 0.60, 0.50)
        }, reverse=True)

    candidates: List[LayoutCandidate] = []
    for font_size in font_sizes:
        widths, space_width = _precompute_widths(words, font_size)
        total_width = sum(widths) + max(0, len(words) - 1) * space_width
        line_spacing = _free_text_line_spacing(render_cfg.line_spacing)
        line_height = _free_text_line_height(font_size, line_spacing)
        target_width = float(target.width if target is not None else profile.block_width)
        target_height_value = float(target.height if target is not None else profile.block_height)
        target_ar = max(0.05, target_width / max(1.0, target_height_value))
        preferred_lines = int(round(math.sqrt(total_width / max(1.0, target_ar * line_height))))
        preferred_lines = max(1, min(len(words), preferred_lines))
        line_counts = sorted({
            max(1, min(len(words), count))
            for count in range(min(preferred_lines, profile.line_count) - 2, max(preferred_lines, profile.line_count) + 3)
        })
        for line_count in line_counts:
            wrap_width = max(
                float(font_size * 2),
                total_width / line_count,
                target_ar * line_count * line_height,
            )
            candidate = _free_text_wrap_candidate(
                words, widths, space_width, font_size,
                line_spacing, line_count, wrap_width,
            )
            if candidate is None:
                continue
            candidate.penalty = _free_text_typography_score(candidate, profile, target)
            ink = _free_text_candidate_ink_metrics(candidate)
            candidate.qa = {
                "font_size": candidate.font_size,
                "line_spacing": candidate.line_spacing,
                "lines": [line.text for line in candidate.lines],
                "line_widths": [line.width for line in candidate.lines],
                "typography_score": candidate.penalty,
                "ink_bbox": list(ink["ink_bbox"]),
                "ink_width": ink["ink_width"],
                "ink_height": ink["ink_height"],
                "ink_area": ink["ink_area"],
                "ink_aspect_ratio": ink["ink_width"] / max(1.0, ink["ink_height"]),
                "ink_line_height": ink["ink_line_height"],
                "font_metric_height": ink["font_metric_height"],
                "baseline_advance": ink["baseline_advance"],
                "baseline_advance_min": ink["baseline_advance_min"],
                "baseline_advance_max": ink["baseline_advance_max"],
                "visible_gap": ink["visible_gap"],
                "baseline_consistent": ink["baseline_advance_min"] == ink["baseline_advance_max"],
                "line_gap_sane": ink["visible_gap"] <= max(4, ink["font_metric_height"] * 0.5),
                "target_width": target_width,
                "target_height": target_height_value,
                "target_aspect_ratio": target_ar,
            }
            candidates.append(candidate)

    candidates.sort(key=lambda candidate: candidate.penalty)
    unique: List[LayoutCandidate] = []
    seen = set()
    for candidate in candidates:
        key = (candidate.font_size, tuple(line.text for line in candidate.lines))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= 12:
            break
    return unique


def _free_text_shift_candidate(candidate: LayoutCandidate, dx: int, dy: int) -> LayoutCandidate:
    return LayoutCandidate(
        font_size=candidate.font_size,
        y_origin=candidate.y_origin + dy,
        line_spacing=candidate.line_spacing,
        lines=[
            PlacedLine(
                text=line.text,
                y=line.y + dy,
                x=line.x + dx,
                width=line.width,
                height=line.height,
                slot=BandSlot(
                    left=line.slot.left + dx,
                    right=line.slot.right + dx,
                    y_start=line.slot.y_start + dy,
                    y_end=line.slot.y_end + dy,
                ),
            )
            for line in candidate.lines
        ],
        penalty=candidate.penalty,
        glyph_clearance_p5=candidate.glyph_clearance_p5,
        status="free_text",
        valid=True,
        qa=dict(candidate.qa),
    )


def _free_text_offset_search(max_radius: int) -> List[Tuple[int, int]]:
    """Yield offset sequence for rapid basin discovery."""
    offsets: List[Tuple[int, int]] = []
    for radius in (0, 2, 4, 8, 12, 16, 24, 32, 48, 64):
        if radius > max_radius:
            continue
        points = [(0, 0)] if radius == 0 else [
            (radius, 0), (-radius, 0), (0, radius), (0, -radius),
            (radius, radius), (radius, -radius), (-radius, radius), (-radius, -radius),
        ]
        for point in points:
            if point not in offsets:
                offsets.append(point)
    return offsets


def _free_text_offset_refine(center_dx: int, center_dy: int, step: int = 2) -> List[Tuple[int, int]]:
    """Local fine refinement around a promising coarse basin."""
    offsets: List[Tuple[int, int]] = []
    for dx in range(center_dx - step * 2, center_dx + step * 2 + 1, step):
        for dy in range(center_dy - step * 2, center_dy + step * 2 + 1, step):
            offsets.append((dx, dy))
    return offsets


def _free_text_hard_valid(
    crop_box: Tuple[int, int, int, int],
    visual_crop: np.ndarray,
    zone: FreeTextZone,
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
) -> bool:
    """Reject page, bubble, and foreign-text collisions for a free-text block.

    ``ownership_mask`` partitions damage between regions; it is deliberately not
    a placement boundary because translated text can be wider than its source.
    """
    x1, y1, x2, y2 = crop_box
    h_obs, w_obs = obstacles.panel_mask.shape[:2]
    if x1 < 0 or y1 < 0 or x2 > w_obs or y2 > h_obs:
        return False
    if not visual_crop.any():
        return False

    # Quick check: if the entire crop_box has no obstacles, it's valid immediately
    bubble_sub = obstacles.protected_bubble_mask[y1:y2, x1:x2]
    other_sub = other_text[y1:y2, x1:x2]
    panel_sub = obstacles.panel_mask[y1:y2, x1:x2]

    # If obstacles exist in sub-window, test against visual crop
    if np.any(bubble_sub):
        if np.any(visual_crop & (bubble_sub > 0)):
            return False
    if np.any(other_sub):
        if np.any(visual_crop & other_sub):
            return False
    if not np.all(panel_sub > 0):
        if np.any(visual_crop & ~(panel_sub > 0)):
            return False

    return True


def _free_text_footprint_qa(
    profile: OriginalLayoutProfile,
    target: Optional[FreeTextDamageTarget],
    ink_bbox: Tuple[int, int, int, int],
) -> Dict[str, Any]:
    """Describe the selected glyph footprint against its source/inpaint target."""
    x1, y1, x2, y2 = ink_bbox
    ink_width = max(0, x2 - x1)
    ink_height = max(0, y2 - y1)
    target_bbox = target.bbox if target is not None else profile.bbox
    target_width = max(1, target_bbox[2] - target_bbox[0])
    target_height = max(1, target_bbox[3] - target_bbox[1])
    return {
        "source_bbox": list(profile.bbox),
        "inpaint_bbox": list(target.inpaint_bbox) if target is not None else list(profile.bbox),
        "target_bbox": list(target_bbox),
        "target_centroid": [target.centroid_x, target.centroid_y] if target is not None else [profile.centroid[0], profile.centroid[1]],
        "inpaint_centroid": list(target.inpaint_centroid) if target is not None else [profile.centroid[0], profile.centroid[1]],
        "target_width": target_width,
        "target_height": target_height,
        "target_area": int(target.area) if target is not None else int(profile.block_width * profile.block_height),
        "target_aspect_ratio": target_width / max(1.0, float(target_height)),
        "ink_bbox": [x1, y1, x2, y2],
        "ink_width": ink_width,
        "ink_height": ink_height,
        "ink_aspect_ratio": ink_width / max(1.0, float(ink_height)),
        "footprint_width": ink_width,
        "footprint_height": ink_height,
        "footprint_area_ratio": (ink_width * ink_height) / max(1.0, target_width * target_height),
    }


def _free_text_ink_overflow(
    candidate: LayoutCandidate,
    image_shape: Tuple[int, int],
    obstacles: PageObstacleMap,
    other_text: np.ndarray,
) -> float:
    """Measure glyph pixels outside the legal page/obstacle area."""
    glyph = _candidate_global_glyph_mask(candidate, image_shape)
    total = int(np.count_nonzero(glyph))
    if not total:
        return 1.0
    allowed = (
        (obstacles.panel_mask > 0)
        & ~(obstacles.protected_bubble_mask > 0)
        & ~other_text
    )
    return float(np.count_nonzero(glyph & ~allowed)) / total


def _solve_free_text_region(
    region: Any,
    zone: FreeTextZone,
    obstacles: PageObstacleMap,
    config: Config,
    image_shape: Tuple[int, int],
    solver_margin: float,
    solver_max_y_trials: int,
) -> Optional[Tuple[LayoutCandidate, OriginalLayoutProfile, Dict[str, Any]]]:
    """Solve FREE_TEXT as frozen typography followed by rigid placement.

    The inpainting mask determines where the paragraph belongs, never how its
    words wrap. Bubble geometry is intentionally not called from this path.
    """
    text = (
        region.get_translation_for_rendering()
        if hasattr(region, "get_translation_for_rendering")
        else (getattr(region, "translation", "") or getattr(region, "text", ""))
    ).strip()
    source_mask = getattr(region, "_free_text_source_mask", None)
    if not text or source_mask is None or not np.any(source_mask):
        return None

    profile = build_original_layout_profile(region, zone.ownership_mask)
    if profile is None:
        return None

    target = zone.damage_target
    prof = get_solver_profile()
    t_topo0 = perf_counter()
    typography = _free_text_typography_candidates(
        text,
        profile,
        config,
        image_shape,
        target=target,
    )
    prof.ft_typography_ms += (perf_counter() - t_topo0) * 1000.0
    if not typography:
        return None

    if target is None or target.area == 0:
        damage_centroid = profile.centroid
    else:
        damage_centroid = (target.centroid_x, target.centroid_y)
    target_width = target.width if target is not None else profile.block_width
    target_height = target.height if target is not None else profile.block_height

    stroke_width = max(1, int(max(candidate.font_size for candidate in typography) * 0.07))
    source = source_mask.astype(bool)
    other_text = obstacles.text_mask.astype(bool) & ~source
    evaluated: List[LayoutCandidate] = []
    typography.sort(key=lambda c: c.penalty)
    eval_candidates = typography[:16]

    for typography_candidate in eval_candidates:
        t_crop0 = perf_counter()
        prof.free_text_crops_rendered += 1
        base_box, ink_crop, visual_crop, block_crop = _candidate_cropped_visual_masks(
            typography_candidate, stroke_width, image_shape,
        )
        prof.ft_crops_rasterize_ms += (perf_counter() - t_crop0) * 1000.0
        bx1, by1, bx2, by2 = base_box
        if bx2 <= bx1 or by2 <= by1 or not np.any(ink_crop):
            continue
        ink_y, ink_x = np.nonzero(ink_crop)
        base_centroid = (bx1 + float(ink_x.mean()), by1 + float(ink_y.mean()))
        base_ink_bbox = (
            bx1 + int(ink_x.min()), by1 + int(ink_y.min()),
            bx1 + int(ink_x.max()) + 1, by1 + int(ink_y.max()) + 1,
        )
        ideal_dx = int(round(damage_centroid[0] - base_centroid[0]))
        ideal_dy = int(round(damage_centroid[1] - base_centroid[1]))
        max_radius = max(16, min(64, int(max(target_width, target_height, profile.block_width, profile.block_height))))

        # Stage A: Coarse offset exploration
        coarse_offsets = _free_text_offset_search(max_radius)
        best_coarse_offset: Optional[Tuple[int, int]] = None
        best_coarse_score = float("inf")

        tested_offsets = set()
        for rel_dx, rel_dy in coarse_offsets:
            tested_offsets.add((rel_dx, rel_dy))
            prof.free_text_offsets_tested += 1
            dx = ideal_dx + rel_dx
            dy = ideal_dy + rel_dy
            crop_box = (bx1 + dx, by1 + dy, bx2 + dx, by2 + dy)
            t_off0 = perf_counter()
            is_valid = _free_text_hard_valid(crop_box, visual_crop, zone, obstacles, other_text)
            prof.ft_offset_search_ms += (perf_counter() - t_off0) * 1000.0
            if not is_valid:
                continue
            prof.free_text_hard_valid_hits += 1

            t_cov0 = perf_counter()
            coverage = _measure_damage_coverage_crop(crop_box, visual_crop, ink_crop, block_crop, zone)
            prof.ft_coverage_ms += (perf_counter() - t_cov0) * 1000.0

            actual_centroid = (base_centroid[0] + dx, base_centroid[1] + dy)
            center_dx = actual_centroid[0] - damage_centroid[0]
            center_dy = actual_centroid[1] - damage_centroid[1]
            center_penalty = (
                (rel_dx / max(1.0, float(max(target_width, profile.block_width)))) ** 2
                + (rel_dy / max(1.0, float(max(target_height, profile.block_height)))) ** 2
            )
            source_drift = math.hypot(
                actual_centroid[0] - profile.centroid[0],
                actual_centroid[1] - profile.centroid[1],
            ) / max(1.0, math.hypot(profile.block_width, profile.block_height))
            score = (
                typography_candidate.penalty
                + center_penalty * 100.0
                + source_drift * 3.0
                - coverage["c_damage"] * 15.0
                + (1.0 - coverage["c_core"]) * 5.0
            )

            if score < best_coarse_score:
                best_coarse_score = score
                best_coarse_offset = (rel_dx, rel_dy)

            candidate = _free_text_shift_candidate(typography_candidate, dx, dy)
            cand_bbox = _candidate_bbox(candidate)
            ink_bbox = tuple(value + delta for value, delta in zip(base_ink_bbox, (dx, dy, dx, dy)))
            candidate.penalty = score
            candidate.qa.update({
                "placement_mode": PlacementMode.FREE_TEXT.value,
                "source_bbox": profile.bbox,
                "source_font_size": profile.font_size,
                "source_line_count": profile.line_count,
                "damage_bbox": target.bbox if target is not None else profile.bbox,
                "damage_centroid": [damage_centroid[0], damage_centroid[1]],
                "ink_centroid": [actual_centroid[0], actual_centroid[1]],
                "center_error_px": math.hypot(center_dx, center_dy),
                "placement_dx": rel_dx,
                "placement_dy": rel_dy,
                "coverage_ink": coverage["c_ink"],
                "coverage_visual": coverage["c_visual"],
                "coverage_block": coverage["c_block"],
                "damage_coverage": coverage["c_damage"],
                "core_damage_coverage": coverage["c_core"],
                "uncovered_damage": coverage["u_damage"],
                **_free_text_footprint_qa(profile, target, ink_bbox),
                "ink_overflow": _free_text_ink_overflow(candidate, image_shape, obstacles, other_text),
                "layout_width": cand_bbox[2] - cand_bbox[0],
                "layout_height": cand_bbox[3] - cand_bbox[1],
                "expansion_ratio": (ink_bbox[2] - ink_bbox[0]) * (ink_bbox[3] - ink_bbox[1]) / max(1.0, profile.block_width * profile.block_height),
                "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text"],
                "free_text_score": score,
            })
            candidate.status = "free_text"
            evaluated.append(candidate)

        # Stage B: Fine refinement around best coarse offset
        if best_coarse_offset is not None:
            fine_offsets = _free_text_offset_refine(best_coarse_offset[0], best_coarse_offset[1], step=2)
            for rel_dx, rel_dy in fine_offsets:
                if (rel_dx, rel_dy) in tested_offsets:
                    continue
                tested_offsets.add((rel_dx, rel_dy))
                prof.free_text_offsets_tested += 1
                dx = ideal_dx + rel_dx
                dy = ideal_dy + rel_dy
                crop_box = (bx1 + dx, by1 + dy, bx2 + dx, by2 + dy)
                t_off0 = perf_counter()
                is_valid = _free_text_hard_valid(crop_box, visual_crop, zone, obstacles, other_text)
                prof.ft_offset_search_ms += (perf_counter() - t_off0) * 1000.0
                if not is_valid:
                    continue
                prof.free_text_hard_valid_hits += 1

                t_cov0 = perf_counter()
                coverage = _measure_damage_coverage_crop(crop_box, visual_crop, ink_crop, block_crop, zone)
                prof.ft_coverage_ms += (perf_counter() - t_cov0) * 1000.0

                actual_centroid = (base_centroid[0] + dx, base_centroid[1] + dy)
                center_dx = actual_centroid[0] - damage_centroid[0]
                center_dy = actual_centroid[1] - damage_centroid[1]
                center_penalty = (
                    (rel_dx / max(1.0, float(max(target_width, profile.block_width)))) ** 2
                    + (rel_dy / max(1.0, float(max(target_height, profile.block_height)))) ** 2
                )
                source_drift = math.hypot(
                    actual_centroid[0] - profile.centroid[0],
                    actual_centroid[1] - profile.centroid[1],
                ) / max(1.0, math.hypot(profile.block_width, profile.block_height))
                score = (
                    typography_candidate.penalty
                    + center_penalty * 100.0
                    + source_drift * 3.0
                    - coverage["c_damage"] * 15.0
                    + (1.0 - coverage["c_core"]) * 5.0
                )

                candidate = _free_text_shift_candidate(typography_candidate, dx, dy)
                cand_bbox = _candidate_bbox(candidate)
                ink_bbox = tuple(value + delta for value, delta in zip(base_ink_bbox, (dx, dy, dx, dy)))
                candidate.penalty = score
                candidate.qa.update({
                    "placement_mode": PlacementMode.FREE_TEXT.value,
                    "source_bbox": profile.bbox,
                    "source_font_size": profile.font_size,
                    "source_line_count": profile.line_count,
                    "damage_bbox": target.bbox if target is not None else profile.bbox,
                    "damage_centroid": [damage_centroid[0], damage_centroid[1]],
                    "ink_centroid": [actual_centroid[0], actual_centroid[1]],
                    "center_error_px": math.hypot(center_dx, center_dy),
                    "placement_dx": rel_dx,
                    "placement_dy": rel_dy,
                    "coverage_ink": coverage["c_ink"],
                    "coverage_visual": coverage["c_visual"],
                    "coverage_block": coverage["c_block"],
                    "damage_coverage": coverage["c_damage"],
                    "core_damage_coverage": coverage["c_core"],
                    "uncovered_damage": coverage["u_damage"],
                    **_free_text_footprint_qa(profile, target, ink_bbox),
                    "ink_overflow": _free_text_ink_overflow(candidate, image_shape, obstacles, other_text),
                    "layout_width": cand_bbox[2] - cand_bbox[0],
                    "layout_height": cand_bbox[3] - cand_bbox[1],
                    "expansion_ratio": (ink_bbox[2] - ink_bbox[0]) * (ink_bbox[3] - ink_bbox[1]) / max(1.0, profile.block_width * profile.block_height),
                    "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text"],
                    "free_text_score": score,
                })
                candidate.status = "free_text"
                evaluated.append(candidate)

    if not evaluated:
        # Resilient fallback: ensure translated text is never dropped without a layout candidate
        for typography_candidate in typography:
            base_box, ink_crop, visual_crop, block_crop = _candidate_cropped_visual_masks(
                typography_candidate, stroke_width, image_shape,
            )
            bx1, by1, bx2, by2 = base_box
            if bx2 <= bx1 or by2 <= by1 or not np.any(ink_crop):
                continue
            ink_y, ink_x = np.nonzero(ink_crop)
            base_centroid = (bx1 + float(ink_x.mean()), by1 + float(ink_y.mean()))
            base_ink_bbox = (
                bx1 + int(ink_x.min()), by1 + int(ink_y.min()),
                bx1 + int(ink_x.max()) + 1, by1 + int(ink_y.max()) + 1,
            )
            ideal_dx = int(round(damage_centroid[0] - base_centroid[0]))
            ideal_dy = int(round(damage_centroid[1] - base_centroid[1]))
            clamp_dx = max(-bx1, min(image_shape[1] - bx2, ideal_dx))
            clamp_dy = max(-by1, min(image_shape[0] - by2, ideal_dy))
            candidate = _free_text_shift_candidate(typography_candidate, clamp_dx, clamp_dy)
            candidate.penalty = typography_candidate.penalty + 500.0
            candidate.status = "free_text_fallback"
            ink_bbox = tuple(value + delta for value, delta in zip(base_ink_bbox, (clamp_dx, clamp_dy, clamp_dx, clamp_dy)))
            candidate.qa.update({
                "placement_mode": PlacementMode.FREE_TEXT.value,
                "source_bbox": profile.bbox,
                "source_font_size": profile.font_size,
                "source_line_count": profile.line_count,
                "damage_bbox": target.bbox if target is not None else profile.bbox,
                "damage_centroid": [damage_centroid[0], damage_centroid[1]],
                "ink_centroid": [base_centroid[0] + clamp_dx, base_centroid[1] + clamp_dy],
                "center_error_px": math.hypot(clamp_dx - ideal_dx, clamp_dy - ideal_dy),
                "placement_dx": clamp_dx - ideal_dx,
                "placement_dy": clamp_dy - ideal_dy,
                **_free_text_footprint_qa(profile, target, ink_bbox),
                "ink_overflow": _free_text_ink_overflow(candidate, image_shape, obstacles, other_text),
                "free_text_score": candidate.penalty,
                "fallback": True,
            })
            evaluated.append(candidate)
            break

    if not evaluated:
        return None

    evaluated.sort(key=lambda candidate: candidate.penalty)
    unique: List[LayoutCandidate] = []
    seen = set()
    for candidate in evaluated:
        key = (candidate.font_size, tuple((line.text, line.x, line.y) for line in candidate.lines))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= 8:
            break

    alternatives = [
        {
            "font_size": candidate.font_size,
            "lines": len(candidate.lines),
            "text": [line.text for line in candidate.lines],
            "damage_coverage": candidate.qa.get("damage_coverage", 0.0),
            "core_coverage": candidate.qa.get("core_damage_coverage", 0.0),
            "expansion_ratio": candidate.qa.get("expansion_ratio", 1.0),
            "center_error_px": candidate.qa.get("center_error_px", 0.0),
            "center_drift": candidate.qa.get("center_error_px", 0.0),
            "penalty": candidate.penalty,
        }
        for candidate in unique
    ]
    for candidate in unique:
        candidate.qa["candidate_alternatives"] = alternatives
    region._free_text_candidate_pool = unique[:16]
    best = unique[0]
    return best, profile, best.qa


def _select_free_text_joint_candidates(
    regions: List[Any],
    plans: Dict[int, List[LayoutCandidate]],
    image_shape: Tuple[int, int],
    free_profiles: Optional[Dict[int, OriginalLayoutProfile]] = None,
) -> Dict[int, LayoutCandidate]:
    """Select optimal joint combination for all free-text regions without touching bubbles."""
    if not regions:
        return {}
    if len(regions) == 1:
        rid = id(regions[0])
        return {rid: plans[rid][0]} if plans.get(rid) else {}

    # Check pairwise candidates for collision and choose combination with lowest total penalty
    free_plans = [
        _RegionLayoutPlan(
            region=r,
            text=(
                r.get_translation_for_rendering()
                if hasattr(r, "get_translation_for_rendering")
                else (getattr(r, "translation", "") or getattr(r, "text", ""))
            ),
            source_profile=(free_profiles.get(id(r)) if free_profiles else getattr(r, "_source_profile", None)),
            candidates=plans.get(id(r), []),
            fg=getattr(r, "fg_color", (0, 0, 0)),
            bg=getattr(r, "bg_color", (255, 255, 255)),
            line_spacing=0.1,
            language=getattr(r, "target_lang", "ENG") or "ENG",
        )
        for r in regions if plans.get(id(r))
    ]
    chosen = _choose_joint_layout(free_plans, image_shape)
    if chosen is not None:
        return {id(free_plans[i].region): chosen[i] for i in range(len(chosen))}

    # Keep the hard collision invariant even when no full Cartesian
    # combination is legal; place the most constrained candidates greedily.
    selected: Dict[int, LayoutCandidate] = {}
    occupied: List[np.ndarray] = []
    for region in sorted(regions, key=lambda item: len(plans.get(id(item), [])) or 999):
        for candidate in plans.get(id(region), []):
            glyph_mask, _, _ = _candidate_data(candidate, image_shape)
            if any(np.any(glyph_mask & other) for other in occupied):
                continue
            selected[id(region)] = candidate
            occupied.append(glyph_mask)
            break
    return selected


def _apply_free_text_candidate(
    region: Any,
    candidate: LayoutCandidate,
    profile: OriginalLayoutProfile,
    config: Config,
    image_shape: Tuple[int, int],
) -> bool:
    text = (
        region.get_translation_for_rendering()
        if hasattr(region, "get_translation_for_rendering")
        else (getattr(region, "translation", "") or getattr(region, "text", ""))
    )
    all_x1 = min(line.x for line in candidate.lines)
    all_y1 = min(line.y for line in candidate.lines)
    all_x2 = max(line.x + line.width for line in candidate.lines)
    all_y2 = max(line.y + line.height for line in candidate.lines)
    layout_rect = [all_x1, all_y1, all_x2, all_y2]
    fg, bg = fg_bg_compare(*region.get_font_colors())
    line_dicts = [
        {"text": line.text, "x": line.x, "y": line.y, "width": line.width, "height": line.height}
        for line in candidate.lines
    ]
    box = render_positioned_lines(
        line_dicts, layout_rect, candidate.font_size, fg, bg,
        config.render.line_spacing or 0.0,
        getattr(region, "target_lang", "en_US") or "en_US",
        getattr(region, "direction", "hr") == "hr",
    )
    if box is None or not np.any(box[:, :, 3]):
        return False
    region.font_size = candidate.font_size
    region.layout_bounds = layout_rect
    region._bubble_box = box
    region._bubble_points = _points_for_rect(region, layout_rect, image_shape[1], image_shape[0])
    region.layout_segments = [{
        "x": layout_rect[0],
        "y": layout_rect[1],
        "width": layout_rect[2] - layout_rect[0],
        "height": layout_rect[3] - layout_rect[1],
        "text": text,
        "font_size": candidate.font_size,
        "lines": line_dicts,
    }]
    region._layout_input_text = text
    region._free_text_glyph_mask = _candidate_global_glyph_mask(candidate, image_shape)
    region._free_text_visual_mask = _candidate_visual_masks(candidate, image_shape, 1)[1]
    region._free_text_solver_applied = True
    region._solver_applied = True
    region._solver_path = "free_text"
    region._solver_p5 = candidate.glyph_clearance_p5
    region._solver_score = candidate.penalty
    region._solver_status = candidate.status
    region._solver_qa = candidate.qa
    region._render_suppressed = False
    region._draw_operations = [
        {
            "region_id": str(getattr(region, "region_id", "")),
            "text": line.text,
            "x": line.x,
            "y": line.y,
            "width": line.width,
            "height": line.height,
            "font_size": candidate.font_size,
            "bbox": [line.x, line.y, line.x + line.width, line.y + line.height],
            "renderer": "free_text_positioned_lines",
        }
        for line in candidate.lines
    ]
    return True


def create_free_text_layout_debug(
    image: np.ndarray,
    regions: List[Any],
    obstacles: PageObstacleMap,
    zones: Dict[int, Union[np.ndarray, FreeTextZone]],
) -> np.ndarray:
    """Rich debug overlay: BLUE (source), MAGENTA (damage), BRIGHT (core), GREEN (zone), RED (bubble), YELLOW (halo), CYAN (visual), WHITE (block)."""
    debug = image.copy()
    h, w = debug.shape[:2]

    for region in regions or []:
        if getattr(region, "placement_mode", None) is not PlacementMode.FREE_TEXT:
            continue
        rid = id(region)
        source = getattr(region, "_free_text_source_mask", np.zeros((h, w), np.uint8)) > 0
        raw_zone = zones.get(rid)
        if isinstance(raw_zone, FreeTextZone):
            zone_mask = raw_zone.ownership_mask > 0
            damage_mask = raw_zone.coverage_target_mask > 0
            core_mask = raw_zone.core_damage_mask > 0
        elif raw_zone is not None:
            zone_mask = raw_zone > 0
            damage_mask = source
            core_mask = source
        else:
            zone_mask = np.zeros((h, w), bool)
            damage_mask = source
            core_mask = source

        tint = np.zeros_like(debug)
        # BLUE: original source footprint
        tint[source] = (255, 0, 0)
        # GREEN: ownership territory
        tint[zone_mask] = (0, 180, 0)
        # MAGENTA: actual damage mask
        tint[damage_mask] = (220, 0, 220)
        # BRIGHT MAGENTA / WHITE: high weight damage core
        tint[core_mask] = (255, 120, 255)
        debug = cv2.addWeighted(debug, 1.0, tint, 0.22, 0)

        # Outlines
        for mask, color, thickness in (
            (obstacles.bubble_mask, (0, 0, 255), 2),              # RED: speech bubbles
            (obstacles.protected_bubble_mask, (0, 255, 255), 1),  # YELLOW: bubble safety halo
            (zone_mask, (0, 200, 0), 1),                          # GREEN: ownership boundary
            (damage_mask, (200, 0, 200), 1),                      # MAGENTA: damage boundary
            (source, (255, 100, 0), 1),                           # BLUE: source boundary
        ):
            contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(debug, contours, -1, color, thickness)

        # CYAN: candidate visual footprint (with stroke)
        vis_mask = getattr(region, "_free_text_visual_mask", None)
        if vis_mask is not None:
            contours, _ = cv2.findContours(vis_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(debug, contours, -1, (255, 255, 0), 1)

        # WHITE: selected glyph mask & block bounds
        glyph = getattr(region, "_free_text_glyph_mask", None)
        if glyph is not None:
            contours, _ = cv2.findContours(glyph.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(debug, contours, -1, (255, 255, 255), 1)

        bounds = getattr(region, "layout_bounds", None)
        if bounds is not None and len(bounds) == 4:
            x1, y1, x2, y2 = [int(v) for v in bounds]
            cv2.rectangle(debug, (x1, y1), (x2, y2), (255, 255, 255), 1)

        target = raw_zone.damage_target if isinstance(raw_zone, FreeTextZone) else None
        if target is not None:
            damage_pt = (int(round(target.centroid_x)), int(round(target.centroid_y)))
            cv2.drawMarker(debug, damage_pt, (255, 0, 255), cv2.MARKER_CROSS, 9, 1, cv2.LINE_AA)
            glyph_centroid = getattr(region, "_solver_qa", {}).get("ink_centroid")
            if glyph_centroid:
                glyph_pt = (int(round(glyph_centroid[0])), int(round(glyph_centroid[1])))
                cv2.drawMarker(debug, glyph_pt, (255, 255, 0), cv2.MARKER_TILTED_CROSS, 9, 1, cv2.LINE_AA)
                cv2.arrowedLine(debug, damage_pt, glyph_pt, (255, 255, 255), 1, cv2.LINE_AA, tipLength=0.2)
            qa = getattr(region, "_solver_qa", {}) or {}
            label = (
                f"FREE {getattr(region, 'region_id', '')} "
                f"font:{getattr(region, 'font_size', 0)} "
                f"lines:{len(getattr(region, 'layout_segments', [{}])[0].get('lines', [])) if getattr(region, 'layout_segments', None) else 0} "
                f"cov:{qa.get('damage_coverage', 0.0):.0%}"
            )
            cv2.putText(debug, label, (damage_pt[0] + 6, damage_pt[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1, cv2.LINE_AA)

    return debug


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


def _glyph_mask_for_lines(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
) -> np.ndarray:
    """Rasterize a candidate into the geometry's local coordinate system."""
    mask = np.zeros(geom.shape, dtype=bool)
    h, w = geom.shape
    for line in lines:
        alpha = _render_line_alpha(line, font_size)
        if alpha is None:
            x1, y1 = line.x, line.y
            x2, y2 = min(w, x1 + line.width), min(h, y1 + line.height)
            if x1 < x2 and y1 < y2:
                mask[y1:y2, x1:x2] = True
            continue
        glyph = alpha > 127
        ay, ax = glyph.shape
        x1, y1 = line.x, line.y
        x2, y2 = min(w, x1 + ax), min(h, y1 + ay)
        if x1 < x2 and y1 < y2:
            mask[y1:y2, x1:x2] |= glyph[:y2 - y1, :x2 - x1]
    return mask


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
    full_text = getattr(region, "text", "") or ""

    line_entries: List[Dict[str, Any]] = []
    x1_all = y1_all = float("inf")
    x2_all = y2_all = float("-inf")
    heights: List[float] = []
    for idx, quad in enumerate(quads):
        x1, y1 = float(quad[:, 0].min()), float(quad[:, 1].min())
        x2, y2 = float(quad[:, 0].max()), float(quad[:, 1].max())
        w, h = x2 - x1, y2 - y1
        heights.append(h)
        x1_all, y1_all = min(x1_all, x1), min(y1_all, y1)
        x2_all, y2_all = max(x2_all, x2), max(y2_all, y2)
        line_entries.append({
            "text": texts[idx] if idx < len(texts) else "",
            "center_x": (x1 + x2) / 2.0,
            "center_y": (y1 + y2) / 2.0,
            "width": w,
            "height": h,
        })

    font_size = float(getattr(region, "font_size", -1) or -1)
    if font_size <= 0:
        heights_sorted = sorted(heights)
        font_size = heights_sorted[len(heights_sorted) // 2] * 0.8
    font_size = max(1.0, font_size)

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


# ---------------------------------------------------------------------------
# Phase 7/8/9 — composite soft objective
# ---------------------------------------------------------------------------

def _composite_penalty(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    font_size: int,
    font_target: int,
    source_profile: Optional[OriginalLayoutProfile],
    stroke_width: int = 0,
    margin: float = 2.0,
    preferred_mask: Optional[np.ndarray] = None,
    target_geom: Optional[PlacementTarget] = None,
    zone_profile: Optional[ZoneShapeProfile] = None,
) -> Tuple[float, Dict[str, float]]:
    """Score a complete candidate block: language is already priced into the
    DP; this adds typography (font size vs target), composition (silhouette,
    fill variance, jitter), whitespace (occupancy band), placement (ink
    centroid), whitespace balance (top-vs-bottom and left-vs-right free space),
    vertical utilization, aspect ratio match, and — when available — similarity
    to the original layout."""
    if not lines:
        return float("inf"), {}

    n = len(lines)
    qa: Dict[str, float] = {}

    mask_area = float(np.count_nonzero(geom.cleaned_mask))
    ref_size = max(1.0, math.sqrt(mask_area))

    # A. Typography: prefer the target size; smaller sizes must earn it.
    p_font = _font_penalty(font_size, font_target)
    qa["p_font"] = p_font

    # B. Placement: actual glyph-pixel centroid vs the target.
    #    When target_geom is provided (e.g. from capacity weighting), align against it.
    ink_cx, ink_cy, glyph_n = _ink_centroid(lines, font_size)
    ink_global_cx = ink_cx + getattr(geom, "x_offset", 0)
    ink_global_cy = ink_cy + getattr(geom, "y_offset", 0)
    if target_geom is not None:
        target_cx = target_geom.center_x + getattr(geom, "x_offset", 0)
        target_cy = target_geom.center_y + getattr(geom, "y_offset", 0)
    elif source_profile is not None:
        target_cx, target_cy = source_profile.centroid
    else:
        cx_mask, cy_mask = geom.centroid()
        target_cx, target_cy = cx_mask + getattr(geom, "x_offset", 0), cy_mask + getattr(geom, "y_offset", 0)

    centroid_d = math.sqrt((ink_global_cx - target_cx) ** 2 + (ink_global_cy - target_cy) ** 2)
    p_centroid = (centroid_d / ref_size) * _WEIGHT_CENTROID
    qa["center_error_px"] = centroid_d
    qa["ink_cx"] = ink_global_cx
    qa["ink_cy"] = ink_global_cy
    qa["target_cx"] = target_cx
    qa["target_cy"] = target_cy

    # C. Whitespace & Balance (Phase 8 & 9)
    #    Measure usable free space from the safe mask around the rendered block.
    safe_zone = target_geom.mask if target_geom is not None else geom.safe_pixels(font_size, stroke_width, margin)
    zone_area = float(np.count_nonzero(safe_zone)) if np.any(safe_zone) else mask_area

    block_left = min(line.x for line in lines)
    block_right = max(line.x + line.width for line in lines)
    block_top = min(line.y for line in lines)
    block_bottom = max(line.y + line.height for line in lines)

    block_cx = (block_left + block_right) / 2.0
    block_cy = (block_top + block_bottom) / 2.0

    qa["block_center_dx"] = block_cx - (target_geom.center_x if target_geom is not None else (target_cx - getattr(geom, "x_offset", 0)))
    qa["block_center_dy"] = block_cy - (target_geom.center_y if target_geom is not None else (target_cy - getattr(geom, "y_offset", 0)))

    # Evaluate free-space capacity above, below, left, and right in the safe mask
    if zone_area > 0 and safe_zone.ndim == 2:
        h_mask, w_mask = safe_zone.shape
        top_clamped = max(0, min(h_mask, block_top))
        bottom_clamped = max(0, min(h_mask, block_bottom))
        left_clamped = max(0, min(w_mask, block_left))
        right_clamped = max(0, min(w_mask, block_right))

        a_above = float(np.count_nonzero(safe_zone[:top_clamped, :]))
        a_below = float(np.count_nonzero(safe_zone[bottom_clamped:, :]))
        a_left = float(np.count_nonzero(safe_zone[:, :left_clamped]))
        a_right = float(np.count_nonzero(safe_zone[:, right_clamped:]))

        top_free_ratio = a_above / zone_area
        bottom_free_ratio = a_below / zone_area
        left_free_ratio = a_left / zone_area
        right_free_ratio = a_right / zone_area

        v_balance = abs(a_above - a_below) / zone_area
        h_balance = abs(a_left - a_right) / zone_area
    else:
        top_free_ratio = 0.0
        bottom_free_ratio = 0.0
        left_free_ratio = 0.0
        right_free_ratio = 0.0
        v_balance = 0.0
        h_balance = 0.0

    qa["top_free_ratio"] = top_free_ratio
    qa["bottom_free_ratio"] = bottom_free_ratio
    qa["left_free_ratio"] = left_free_ratio
    qa["right_free_ratio"] = right_free_ratio
    qa["vertical_balance"] = v_balance
    qa["horizontal_balance"] = h_balance

    p_balance = (v_balance ** 2) * _WEIGHT_VERT_BALANCE + (h_balance ** 2) * _WEIGHT_HORIZ_BALANCE
    qa["p_balance"] = p_balance

    text_area = float(sum(line.width * line.height for line in lines))
    occupancy = text_area / mask_area if mask_area > 0 else 0.0
    occ_lo, occ_hi = _OCC_IDEAL
    if occupancy < occ_lo:
        p_occ = (occ_lo - occupancy) ** 2 * _WEIGHT_OCC_LOW
    elif occupancy > occ_hi:
        p_occ = (occupancy - occ_hi) ** 2 * _WEIGHT_OCC_HIGH
    else:
        p_occ = 0.0
    qa["occupancy"] = occupancy
    qa["p_occupancy"] = p_occ

    # D. Vertical utilization & Aspect ratio matching
    h_text = max(1.0, float(block_bottom - block_top))
    w_text = max(1.0, float(block_right - block_left))
    ar_text = w_text / h_text

    if zone_profile is not None:
        h_usable = max(1.0, float(zone_profile.height))
        w_usable = max(1.0, float(zone_profile.width))
        ar_zone = max(0.01, float(zone_profile.aspect_ratio))
    else:
        h_usable = max(1.0, float(geom.shape[0]))
        w_usable = max(1.0, float(geom.shape[1]))
        ar_zone = w_usable / h_usable

    r_v = min(1.5, h_text / h_usable)
    qa["vertical_utilization"] = r_v
    qa["ar_text"] = ar_text
    qa["ar_zone"] = ar_zone

    # Vertical fill penalty: desirable band 0.55 <= Rv <= 0.80
    p_vfill = 0.0
    if r_v < 0.55:
        p_vfill = ((0.55 - r_v) / 0.55) ** 2 * _WEIGHT_VFILL_LOW
    elif r_v > 0.80:
        p_vfill = ((r_v - 0.80) / 0.20) ** 2 * _WEIGHT_VFILL_HIGH
    qa["p_vertical_fill"] = p_vfill

    # Aspect ratio mismatch penalty
    aspect_ratio_diff = abs(math.log(max(0.05, ar_text) / max(0.05, ar_zone)))
    p_aspect = (aspect_ratio_diff ** 2) * _WEIGHT_ASPECT
    qa["aspect_mismatch"] = aspect_ratio_diff
    qa["p_aspect"] = p_aspect

    # Silhouette / Envelope match against width_by_y
    p_silhouette = 0.0
    if zone_profile is not None and zone_profile.width_by_y and len(lines) >= 2:
        y_base = zone_profile.bbox[1]
        line_ratios = []
        for line in lines:
            rel_y = max(0, min(len(zone_profile.width_by_y) - 1, int(line.y + line.height / 2.0 - y_base)))
            avail_w = max(1.0, zone_profile.width_by_y[rel_y])
            line_ratios.append(min(1.5, line.width / avail_w))
        ratio_diffs = [abs(line_ratios[i + 1] - line_ratios[i]) for i in range(len(line_ratios) - 1)]
        p_silhouette = (sum(ratio_diffs) / len(ratio_diffs)) * _WEIGHT_SILHOUETTE
    qa["p_silhouette"] = p_silhouette

    # E. Composition: smooth text silhouette (second derivative of widths),
    #    moderate fill variance, low jitter, mild raggedness, block compactness, and X-center coherence.
    widths = [float(line.width) for line in lines]
    if n >= 3:
        diffs = [widths[i + 1] - widths[i] for i in range(n - 1)]
        p_shape = sum(abs(diffs[i + 1] - diffs[i]) for i in range(len(diffs) - 1))
        p_shape = (p_shape / max(1.0, font_size * (n - 2))) * _WEIGHT_SHAPE * 10.0
    else:
        p_shape = 0.0

    fills = [line.width / max(1, line.slot.width) for line in lines]
    fill_var = float(np.var(fills)) if fills else 0.0
    p_fill_var = fill_var * _WEIGHT_FILL_VAR

    jitter = sum(abs((line.x + line.width / 2.0) - line.slot.center) for line in lines)
    p_jitter = jitter * _WEIGHT_JITTER

    ragged = 0.0
    for i in range(n - 1):
        avg = max(1.0, (widths[i] + widths[i + 1]) / 2.0)
        ragged += ((widths[i] - widths[i + 1]) / avg) ** 2
    p_ragged = ragged * _WEIGHT_RAGGED

    orphan_penalty = sum(
        _WEIGHT_ORPHAN for i, line in enumerate(lines)
        if i < n - 1 and len(line.text.split()) == 1 and len(_word_core(line.text)) <= 3
    )
    hyphen_penalty = sum(_WEIGHT_HYPHEN for line in lines[:-1] if line.text.endswith("-"))

    # Block compactness (vertical gap ratio inside rendered block)
    y_top = min(line.y for line in lines)
    y_bottom = max(line.y + line.height for line in lines)
    h_span = max(1.0, float(y_bottom - y_top))
    h_text_actual = float(sum(line.height for line in lines))
    gap_ratio = max(0.0, (h_span - h_text_actual) / h_span)
    p_block_gap = (gap_ratio ** 2) * _WEIGHT_BLOCK_GAP
    qa["gap_ratio"] = gap_ratio
    qa["p_block_gap"] = p_block_gap

    # Global X coherence (line center variance relative to geometry slots)
    centers = [line.x + line.width / 2.0 for line in lines]
    slot_centers = [line.slot.center for line in lines]
    c_var = float(np.var(centers)) if len(centers) > 1 else 0.0
    slot_c_var = float(np.var(slot_centers)) if len(slot_centers) > 1 else 0.0
    # Excessive variance not justified by slot alignment geometry
    excess_var = max(0.0, c_var - slot_c_var)
    norm_excess_var = excess_var / max(1.0, float(font_size ** 2))
    p_center_var = norm_excess_var * _WEIGHT_CENTER_VAR
    qa["center_variance"] = c_var
    qa["p_center_var"] = p_center_var

    p_composition = (
        p_shape
        + p_fill_var
        + p_jitter
        + p_ragged
        + orphan_penalty
        + hyphen_penalty
        + p_block_gap
        + p_center_var
    )
    qa["p_shape"] = p_shape
    qa["p_composition"] = p_composition

    # F. Source similarity: the original page is an artistic prior (Phase 10).
    # Weaken source-centroid weight when the region owns an entire single-region bubble.
    is_single_region = target_geom.is_single_region if target_geom is not None else True
    src_centroid_weight = _WEIGHT_SRC_CENTROID * (0.20 if is_single_region else 1.0)

    p_source = 0.0
    if source_profile is not None:
        prof_cx, prof_cy = source_profile.centroid
        d_cent = math.sqrt((ink_global_cx - prof_cx) ** 2 + (ink_global_cy - prof_cy) ** 2)
        p_src_centroid = (d_cent / ref_size) * src_centroid_weight

        p_src_lines = abs(n - source_profile.line_count) * _WEIGHT_SRC_LINES

        if source_profile.font_size > 0:
            p_src_font = ((font_size - source_profile.font_size) / source_profile.font_size) ** 2 * _WEIGHT_SRC_FONT
        else:
            p_src_font = 0.0

        block_w = max(line.x + line.width for line in lines) - min(line.x for line in lines)
        block_h = max(line.y + line.height for line in lines) - min(line.y for line in lines)
        if source_profile.block_width > 0 and source_profile.block_height > 0:
            d_bw = abs(block_w - source_profile.block_width) / source_profile.block_width
            d_bh = abs(block_h - source_profile.block_height) / source_profile.block_height
        else:
            d_bw = d_bh = 0.0
        p_src_bbox = (d_bw + d_bh) * _WEIGHT_SRC_BBOX

        # Relative line-center pattern matching
        p_src_pattern = 0.0
        if source_profile.lines and len(source_profile.lines) >= 2 and n >= 2:
            # Normalize source centers relative to source block width
            src_min_x = min(entry.get("center_x", 0.0) - entry.get("width", 0.0) / 2.0 for entry in source_profile.lines)
            src_w = max(1.0, float(source_profile.block_width))
            src_rel_centers = [
                (entry.get("center_x", 0.0) - src_min_x) / src_w for entry in source_profile.lines
            ]

            # Normalize candidate centers relative to candidate block width
            cand_min_x = min(line.x for line in lines)
            cand_w = max(1.0, float(block_w))
            cand_rel_centers = [
                ((line.x + line.width / 2.0) - cand_min_x) / cand_w for line in lines
            ]

            # Sample/interpolate to compare profile shape
            t_src = np.linspace(0.0, 1.0, len(src_rel_centers))
            t_cand = np.linspace(0.0, 1.0, len(cand_rel_centers))
            cand_resampled = np.interp(t_src, t_cand, cand_rel_centers)
            pattern_diff = float(np.mean(np.abs(np.array(src_rel_centers) - cand_resampled)))
            p_src_pattern = pattern_diff * _WEIGHT_SRC_PATTERN

        p_source = p_src_centroid + p_src_lines + p_src_font + p_src_bbox + p_src_pattern
        qa["src_lines_orig"] = float(source_profile.line_count)
        qa["src_font_orig"] = source_profile.font_size
        qa["p_src_pattern"] = p_src_pattern
    qa["p_source"] = p_source
    qa["source_sim"] = 1.0 / (1.0 + p_source)

    p_zone = 0.0
    if preferred_mask is not None and np.any(preferred_mask):
        glyph_mask = _glyph_mask_for_lines(lines, geom, font_size)
        glyph_count = int(np.count_nonzero(glyph_mask))
        if glyph_count:
            overflow = np.count_nonzero(glyph_mask & ~preferred_mask) / float(glyph_count)
            p_zone = overflow * overflow * _WEIGHT_ZONE_OVERFLOW
    qa["p_zone_overflow"] = p_zone

    penalty = p_font + p_centroid + p_balance + p_occ + p_composition + p_source + p_zone + p_vfill + p_aspect + p_silhouette
    qa["penalty"] = penalty
    qa["glyph_pixels"] = float(glyph_n)
    return penalty, qa


def _line_height(font_size: int, line_spacing: float) -> int:
    spacing = int(font_size * max(0.0, line_spacing))
    return int(math.ceil(font_size * 1.15)) + spacing


@dataclass
class _RegionLayoutPlan:
    region: Any
    text: str = ""
    source_profile: Optional[OriginalLayoutProfile] = None
    candidates: List[LayoutCandidate] = field(default_factory=list)
    fg: Tuple[int, int, int] = (0, 0, 0)
    bg: Optional[Tuple[int, int, int]] = (255, 255, 255)
    line_spacing: float = 0.1
    language: str = "ENG"
    zone_mask: Optional[np.ndarray] = None


def _shared_bubble_groups(regions: List[Any]) -> List[BubbleLayoutGroup]:
    """Group regions that share the same prepared bubble interior into BubbleLayoutGroups."""
    groups: List[BubbleLayoutGroup] = []
    for region in regions:
        interior = getattr(region, "_bubble_interior", None)
        bubble_mask = getattr(region, "_bubble_mask", None)
        if interior is None or not np.any(interior):
            empty_mask = bubble_mask if bubble_mask is not None else np.zeros((0, 0), dtype=np.uint8)
            empty_interior = interior if interior is not None else np.zeros((0, 0), dtype=np.uint8)
            groups.append(BubbleLayoutGroup(bubble_mask=empty_mask, interior=empty_interior, regions=[region]))
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
            if same_mask or same_interior:
                group.regions.append(region)
                break
        else:
            b_mask = bubble_mask if bubble_mask is not None else interior.copy()
            groups.append(BubbleLayoutGroup(bubble_mask=b_mask, interior=interior, regions=[region]))
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


def _candidate_global_glyph_mask(
    candidate: LayoutCandidate,
    shape: Tuple[int, int],
) -> np.ndarray:
    """Rasterize a candidate in page coordinates for collision checks."""
    mask = np.zeros(shape, dtype=bool)
    h, w = shape
    for line in candidate.lines:
        alpha = _render_line_alpha(line, candidate.font_size)
        if alpha is None:
            glyph = np.ones((line.height, line.width), dtype=bool)
        else:
            glyph = alpha > 127
        ay, ax = glyph.shape
        x1, y1 = max(0, line.x), max(0, line.y)
        x2, y2 = min(w, line.x + ax), min(h, line.y + ay)
        if x1 < x2 and y1 < y2:
            mask[y1:y2, x1:x2] |= glyph[y1 - line.y:y2 - line.y, x1 - line.x:x2 - line.x]
    return mask


def _rect_gap(first: Tuple[int, int, int, int], second: Tuple[int, int, int, int]) -> float:
    dx = max(first[0] - second[2], second[0] - first[2], 0)
    dy = max(first[1] - second[3], second[1] - first[3], 0)
    return math.hypot(dx, dy)


def _candidate_bbox(candidate: LayoutCandidate) -> Tuple[int, int, int, int]:
    return (
        min(line.x for line in candidate.lines),
        min(line.y for line in candidate.lines),
        max(line.x + line.width for line in candidate.lines),
        max(line.y + line.height for line in candidate.lines),
    )


def _candidate_data(candidate: LayoutCandidate, image_shape: Tuple[int, int]) -> Tuple[np.ndarray, Tuple[int, int, int, int], Tuple[float, float]]:
    glyph_mask = _candidate_global_glyph_mask(candidate, image_shape)
    if candidate.status.startswith("free_text") and np.any(glyph_mask):
        metrics = _mask_metrics(glyph_mask)
        return glyph_mask, metrics["bbox"], metrics["centroid"]
    bbox = _candidate_bbox(candidate)
    centroid = _ink_centroid(candidate.lines, candidate.font_size)
    return glyph_mask, bbox, centroid


def _choose_joint_layout(
    plans: List[_RegionLayoutPlan],
    image_shape: Tuple[int, int],
) -> Optional[Tuple[LayoutCandidate, ...]]:
    if not plans or any(not plan.candidates for plan in plans):
        return None

    # ponytail: cap Cartesian search at 4096 combinations; callers use the
    # existing greedy collision-safe fallback for larger candidate spaces.
    if math.prod(len(plan.candidates) for plan in plans) > _MAX_JOINT_LAYOUT_COMBINATIONS:
        return None

    # Precompute candidate data once per unique candidate
    candidate_cache: Dict[int, Tuple[np.ndarray, Tuple[int, int, int, int], Tuple[float, float]]] = {}
    for plan in plans:
        for cand in plan.candidates:
            cid = id(cand)
            if cid not in candidate_cache:
                candidate_cache[cid] = _candidate_data(cand, image_shape)

    best: Optional[Tuple[float, Tuple[LayoutCandidate, ...]]] = None
    for combination in itertools.product(*(plan.candidates for plan in plans)):
        # Check pairwise collision first using cached glyph masks.
        collision = False
        for i in range(len(combination)):
            res_i, _, _ = candidate_cache[id(combination[i])]
            for j in range(i + 1, len(combination)):
                res_j, _, _ = candidate_cache[id(combination[j])]
                if np.any(res_i & res_j):
                    collision = True
                    break
            if collision:
                break
        if collision:
            continue

        score = sum(candidate.penalty for candidate in combination)
        for first in range(len(combination)):
            first_cand = combination[first]
            _, first_bbox, first_centroid = candidate_cache[id(first_cand)]
            first_profile = plans[first].source_profile

            for second in range(first + 1, len(combination)):
                second_cand = combination[second]
                _, second_bbox, second_centroid = candidate_cache[id(second_cand)]
                second_profile = plans[second].source_profile

                if first_profile is None or second_profile is None:
                    continue

                source_dx = second_profile.centroid[0] - first_profile.centroid[0]
                source_dy = second_profile.centroid[1] - first_profile.centroid[1]
                rendered_dx = second_centroid[0] - first_centroid[0]
                rendered_dy = second_centroid[1] - first_centroid[1]
                font_scale = max(1.0, (first_cand.font_size + second_cand.font_size) / 2.0)

                # Strict spatial order preservation (B below A / B right of A)
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

    return best[1] if best is not None and math.isfinite(best[0]) else None


def _build_region_layout_plan(
    region: Any,
    interior: np.ndarray,
    config: Config,
    image_shape: Tuple[int, int],
    solver_margin: float,
    solver_max_y_trials: int,
    preferred_mask: Optional[np.ndarray],
    top_k: int,
    zone_geometry_mask: Optional[np.ndarray] = None,
    lobe_graph: Optional[LobeGraph] = None,
) -> Optional[_RegionLayoutPlan]:
    text = (
        region.get_translation_for_rendering()
        if hasattr(region, "get_translation_for_rendering")
        else (getattr(region, "translation", "") or getattr(region, "text", ""))
    )
    if not text.strip() or interior is None or not np.any(interior):
        return None

    render_cfg = config.render
    minimum = render_cfg.font_size_minimum
    if minimum == -1:
        minimum = round(sum(image_shape) / 200)
    minimum = max(1, minimum)
    target = max(minimum, render_cfg.font_size or region.font_size + render_cfg.font_size_offset)
    if render_cfg.font_size is None:
        target = max(target, _estimate_adaptive_font_size(interior, text, minimum))

    fg, bg = fg_bg_compare(*region.get_font_colors())
    stroke_width = max(1, int(target * 0.07)) if bg is not None else 0

    # If an explicit placement zone is provided, use it as the region's BubbleGeometry
    active_mask = zone_geometry_mask if zone_geometry_mask is not None and np.any(zone_geometry_mask) else interior
    geom = BubbleGeometry(active_mask)
    source_profile = build_original_layout_profile(region, interior)
    zone_local = None
    if preferred_mask is not None:
        y1, y2 = geom.y_offset, geom.y_offset + geom.shape[0]
        x1, x2 = geom.x_offset, geom.x_offset + geom.shape[1]
        zone_local = preferred_mask[y1:y2, x1:x2]
    result = solve_layout(
        geom=geom,
        words=text.split(),
        font_size_max=target,
        font_size_min=minimum,
        language=getattr(region, "target_lang", "en_US") or "en_US",
        hyphenate=not render_cfg.no_hyphenation,
        line_spacing=render_cfg.line_spacing or 0.0,
        stroke_width=stroke_width,
        margin=solver_margin,
        y_origin_step=max(2, target // 8),
        max_y_origin_trials=solver_max_y_trials,
        source_profile=source_profile,
        preferred_mask=zone_local,
        top_k=top_k,
        is_single_region=(preferred_mask is None or not np.any(preferred_mask)),
        lobe_graph=lobe_graph,
    )
    candidates = result if isinstance(result, list) else ([result] if result is not None else [])
    return _RegionLayoutPlan(
        region=region,
        text=text,
        source_profile=source_profile,
        candidates=candidates,
        fg=fg,
        bg=bg,
        line_spacing=render_cfg.line_spacing or 0.0,
        language=getattr(region, "target_lang", "en_US") or "en_US",
        zone_mask=zone_geometry_mask,
    )


def _apply_layout_candidate(
    plan: _RegionLayoutPlan,
    candidate: LayoutCandidate,
    image_shape: Tuple[int, int],
) -> bool:
    region = plan.region
    all_x1 = min(line.x for line in candidate.lines)
    all_y1 = min(line.y for line in candidate.lines)
    all_x2 = max(line.x + line.width for line in candidate.lines)
    all_y2 = max(line.y + line.height for line in candidate.lines)
    layout_rect = [all_x1, all_y1, all_x2, all_y2]
    line_dicts = [
        {"text": line.text, "x": line.x, "y": line.y, "width": line.width, "height": line.height}
        for line in candidate.lines
    ]
    box = render_positioned_lines(
        line_dicts, layout_rect, candidate.font_size, plan.fg, plan.bg,
        plan.line_spacing, plan.language, region.direction == "hr",
    )
    if box is None or not np.any(box[:, :, 3]):
        return False

    region.font_size = candidate.font_size
    region.layout_bounds = layout_rect
    region._bubble_box = box
    region._bubble_cleanup = getattr(region, "_bubble_interior", None) * 255
    region._bubble_points = _points_for_rect(region, layout_rect, image_shape[1], image_shape[0])
    region.layout_segments = [{
        "x": layout_rect[0],
        "y": layout_rect[1],
        "width": layout_rect[2] - layout_rect[0],
        "height": layout_rect[3] - layout_rect[1],
        "text": plan.text,
        "font_size": candidate.font_size,
        "lines": line_dicts,
    }]
    region._layout_input_text = plan.text
    region._solver_applied = True
    region._solver_p5 = candidate.glyph_clearance_p5
    region._solver_score = candidate.penalty
    region._solver_status = candidate.status
    region._solver_qa = candidate.qa
    return True


def create_placement_zones_visualization(
    img_rgb: np.ndarray,
    groups: List[BubbleLayoutGroup],
) -> np.ndarray:
    """Create a diagnostic debug visualization showing placement zones, centroids, and boundaries."""
    if img_rgb is None:
        return np.zeros((100, 100, 3), dtype=np.uint8)
    vis = img_rgb.copy()
    zone_colors = [
        (255, 120, 0),    # Blue-orange palette
        (0, 200, 100),
        (220, 50, 220),
        (255, 200, 0),
        (50, 180, 255),
        (180, 100, 255),
    ]

    for g_idx, group in enumerate(groups):
        interior = group.interior
        if interior is None or not np.any(interior):
            continue

        # Draw bubble safe interior boundary
        int_cnts, _ = cv2.findContours(interior.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(vis, int_cnts, -1, (180, 180, 180), 1)

        # Draw each placement zone
        for z_idx, (region, zone) in enumerate(zip(group.regions, group.zones)):
            color = zone_colors[z_idx % len(zone_colors)]
            if zone is not None and np.any(zone):
                # Tint zone area
                tint = np.zeros_like(vis)
                tint[zone > 0] = color
                cv2.addWeighted(tint, 0.25, vis, 1.0, 0, vis)

                # Zone contour
                z_cnts, _ = cv2.findContours((zone > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(vis, z_cnts, -1, color, 2)

            # Draw original source polygon & centroid
            lines = getattr(region, "lines", None)
            if lines is not None and len(lines):
                cv2.polylines(vis, [np.asarray(line, np.int32) for line in lines], True, (0, 0, 255), 1)

            profile = build_original_layout_profile(region, interior)
            if profile is not None:
                cx, cy = int(round(profile.centroid[0])), int(round(profile.centroid[1]))
                cv2.circle(vis, (cx, cy), 4, (0, 0, 255), -1)
                label = f"Z{z_idx+1}"
                cv2.putText(vis, label, (cx + 6, cy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2)

            # Draw target capacity center (+) for zone
            target_mask = zone if (zone is not None and np.any(zone)) else interior
            if target_mask is not None and np.any(target_mask):
                target_geom = compute_placement_target(
                    BubbleGeometry(target_mask),
                    int(getattr(region, "font_size", 12) or 12),
                    source_profile=profile,
                    is_single_region=(len(group.regions) == 1),
                )
                tcx = int(round(target_geom.center_x + target_geom.bbox[0] * 0))
                # Add geom offset if BubbleGeometry was cropped
                bg = BubbleGeometry(target_mask)
                tcx = int(round(target_geom.center_x + bg.x_offset))
                tcy = int(round(target_geom.center_y + bg.y_offset))
                # Draw cross (+) in magenta
                cv2.drawMarker(vis, (tcx, tcy), (255, 0, 255), cv2.MARKER_CROSS, 8, 1, cv2.LINE_AA)

            # Draw placed lines, band slots, centers, and transition connectors if solver ran
            placed_lines = None
            if hasattr(region, "layout_segments") and region.layout_segments:
                seg_lines = region.layout_segments[0].get("lines", [])
                if seg_lines:
                    placed_lines = seg_lines

            if placed_lines:
                prev_cx, prev_cy = None, None
                all_lx = [int(pl["x"]) for pl in placed_lines]
                all_ly = [int(pl["y"]) for pl in placed_lines]
                all_rx = [int(pl["x"]) + int(pl["width"]) for pl in placed_lines]
                all_by = [int(pl["y"]) + int(pl["height"]) for pl in placed_lines]
                bx1, by1, bx2, by2 = min(all_lx), min(all_ly), max(all_rx), max(all_by)

                # Draw block bounding box
                cv2.rectangle(vis, (bx1, by1), (bx2, by2), (0, 255, 255), 1)
                # Draw block center (x)
                bcx = (bx1 + bx2) // 2
                bcy = (by1 + by2) // 2
                cv2.drawMarker(vis, (bcx, bcy), (0, 255, 255), cv2.MARKER_TILTED_CROSS, 7, 1, cv2.LINE_AA)

                for l_idx, pl in enumerate(placed_lines):
                    lx, ly, lw, lh = int(pl["x"]), int(pl["y"]), int(pl["width"]), int(pl["height"])
                    # Slot bounding box
                    cv2.rectangle(vis, (lx, ly), (lx + lw, ly + lh), color, 1)
                    # Line center dot
                    cx_i = lx + lw // 2
                    cy_i = ly + lh // 2
                    cv2.circle(vis, (cx_i, cy_i), 3, (0, 255, 0), -1)

                    # Transition connector from previous line
                    if prev_cx is not None and prev_cy is not None:
                        # Draw connector line from previous center to current center
                        cv2.line(vis, (prev_cx, prev_cy), (cx_i, cy_i), (0, 255, 255), 1, cv2.LINE_AA)
                    prev_cx, prev_cy = cx_i, cy_i

        # Draw lobe graph features if present
        if group.lobe_graph is not None:
            for neck in group.lobe_graph.necks:
                nx, ny = neck["center"]
                cv2.circle(vis, (nx, ny), 3, (255, 255, 0), -1)

    return vis


def apply_shape_aware_bubble_layout(
    ctx: Context,
    config: Config,
    font_path: Optional[str] = None,
    solver_margin: float = 2.0,
    solver_max_y_trials: int = 12,
    legacy_only: bool = False,
    timing: Optional[Dict[str, float]] = None,
) -> None:
    """Execute shape-aware 2D free-space text fitting and layout on ctx.text_regions."""
    with _RENDER_LOCK:
        layout_timing = {
            "mask_prep_ms": 0.0,
            "mode_classification_ms": 0.0,
            "bubble_solver_ms": 0.0,
            "free_text_solver_ms": 0.0,
            "fallback_ms": 0.0,
        }
        if timing is not None:
            timing.update(layout_timing)

        active_font = font_path or getattr(config.render, "font_path", None) or get_default_eng_font()
        text_render.set_font(active_font)

        regions = ctx.text_regions or []
        img = getattr(ctx, "img_rgb", None)
        if img is None:
            return
        _ensure_region_identities(regions)
        for region in regions:
            if getattr(region, "translation", None) and isinstance(region.translation, str):
                region.translation = config.render.transform_text_case(region.translation)

        # Ensure bubble masks/interiors are populated
        phase_start = perf_counter()
        prepare_bubble_masks(img, regions)
        layout_timing["mask_prep_ms"] = (perf_counter() - phase_start) * 1000.0
        phase_start = perf_counter()
        classify_placement_modes(regions)
        layout_timing["mode_classification_ms"] = (perf_counter() - phase_start) * 1000.0

        render_cfg = config.render
        unplaced_regions: List[Any] = []
        bubble_regions = [
            region for region in regions
            if getattr(region, "placement_mode", None) is PlacementMode.BUBBLE
        ]
        free_regions = [
            region for region in regions
            if getattr(region, "placement_mode", None) is PlacementMode.FREE_TEXT
        ]
        bubble_groups = _shared_bubble_groups(bubble_regions)

        if legacy_only:
            unplaced_regions.extend(regions)

        phase_start = perf_counter()
        for group in bubble_groups if not legacy_only else []:
            active = [
                region for region in group.regions
                if not legacy_only
                and getattr(region, "_bubble_interior", None) is not None
                and np.any(getattr(region, "_bubble_interior", None))
                and (
                    region.get_translation_for_rendering()
                    if hasattr(region, "get_translation_for_rendering")
                    else (getattr(region, "translation", "") or getattr(region, "text", ""))
                ).strip()
            ]
            if not active:
                unplaced_regions.extend(group.regions)
                continue

            interior = getattr(active[0], "_bubble_interior", None)
            lobe_graph = build_lobe_graph(group.bubble_mask) if np.any(group.bubble_mask) else None
            group.lobe_graph = lobe_graph

            # Partition the bubble into geometry-aware placement zones
            zones = partition_bubble_zones(active, interior, lobe_graph=lobe_graph, apply_boundary_gap=(len(active) > 1))
            group.zones = zones

            plans = []
            for index, region in enumerate(active):
                zone_mask = zones[index] if index < len(zones) else interior
                plan = _build_region_layout_plan(
                    region=region,
                    interior=interior,
                    config=config,
                    image_shape=img.shape[:2],
                    solver_margin=solver_margin,
                    solver_max_y_trials=solver_max_y_trials,
                    preferred_mask=zone_mask if len(active) > 1 else None,
                    top_k=_JOINT_CANDIDATE_COUNT if len(active) > 1 else 1,
                    zone_geometry_mask=zone_mask if len(active) > 1 else None,
                )
                if plan is not None:
                    plans.append(plan)

            chosen = _choose_joint_layout(plans, img.shape[:2]) if len(active) > 1 else None
            if len(active) == 1 and plans and plans[0].candidates:
                chosen = (plans[0].candidates[0],)

            if chosen is not None and len(chosen) == len(plans) == len(active):
                for plan, candidate in zip(plans, chosen):
                    if not _apply_layout_candidate(plan, candidate, img.shape[:2]):
                        unplaced_regions.append(plan.region)
                active_ids = {id(region) for region in active}
                unplaced_regions.extend(region for region in group.regions if id(region) not in active_ids)
                continue

            active_ids = {id(region) for region in active}
            for region in group.regions:
                if id(region) in active_ids:
                    region._solver_status = "requires_compression"
                    unplaced_regions.append(region)
        layout_timing["bubble_solver_ms"] = (perf_counter() - phase_start) * 1000.0
        if not legacy_only and free_regions:
            phase_start = perf_counter()
            bubble_halo = max(2, int(round((render_cfg.font_size or 12) * 0.20)))
            obstacles = build_page_obstacle_map(regions, img.shape[:2], bubble_halo=bubble_halo)
            inpaint_mask = getattr(ctx, "inpaint_mask", None)
            if inpaint_mask is None:
                inpaint_mask = getattr(ctx, "text_mask", None)
            if inpaint_mask is None:
                inpaint_mask = getattr(ctx, "mask_raw", None)
            if inpaint_mask is None:
                inpaint_mask = getattr(ctx, "mask", None)
            free_zones = build_free_text_ownership_zones(free_regions, obstacles, inpaint_mask=inpaint_mask)
            free_plans: Dict[int, List[LayoutCandidate]] = {}
            free_profiles: Dict[int, OriginalLayoutProfile] = {}
            for region in free_regions:
                ft_zone = free_zones.get(id(region))
                if ft_zone is None:
                    continue
                result = _solve_free_text_region(
                    region=region,
                    zone=ft_zone,
                    obstacles=obstacles,
                    config=config,
                    image_shape=img.shape[:2],
                    solver_margin=solver_margin,
                    solver_max_y_trials=solver_max_y_trials,
                )
                if result is None:
                    region._solver_path = "free_text"
                    region._solver_status = "no_valid_layout"
                    region._solver_qa = {
                        "placement_mode": PlacementMode.FREE_TEXT.value,
                        "hard_constraints": ["bubble_mask", "ownership_zone", "page_bounds", "other_text"],
                    }
                    region._render_suppressed = True
                    continue
                candidate, profile, _qa = result
                free_plans[id(region)] = getattr(region, "_free_text_candidate_pool", [candidate])
                free_profiles[id(region)] = profile

            chosen_free = _select_free_text_joint_candidates(
                free_regions, free_plans, img.shape[:2], free_profiles=free_profiles
            )
            for region in free_regions:
                candidate = chosen_free.get(id(region))
                profile = free_profiles.get(id(region))
                if candidate is None or profile is None:
                    if id(region) in free_plans:
                        region._solver_path = "free_text"
                        region._solver_status = "no_joint_layout"
                        region._render_suppressed = True
                    continue
                if not _apply_free_text_candidate(region, candidate, profile, config, img.shape[:2]):
                    region._solver_path = "free_text"
                    region._solver_status = "rasterization_failed"
                    region._render_suppressed = True

            for region in free_regions:
                logger.info(
                    f"FREE_TEXT SOLVER RESULT id={getattr(region, 'region_id', id(region))} "
                    f"placement_mode={getattr(region, 'placement_mode', None)} "
                    f"solver_applied={getattr(region, '_free_text_solver_applied', False)} "
                    f"layout_input_text={getattr(region, '_layout_input_text', None)!r} "
                    f"solver_path={getattr(region, '_solver_path', None)} "
                    f"solver_status={getattr(region, '_solver_status', None)} "
                    f"has_bubble_box={getattr(region, '_bubble_box', None) is not None} "
                    f"has_bubble_points={getattr(region, '_bubble_points', None) is not None} "
                    f"has_zone={getattr(region, '_free_text_zone', None) is not None}"
                )

            ctx._free_text_obstacle_map = obstacles
            ctx._free_text_zones = free_zones
            ctx._free_text_layout_debug = create_free_text_layout_debug(
                img, free_regions, obstacles, free_zones
            )
            layout_timing["free_text_solver_ms"] = (perf_counter() - phase_start) * 1000.0

        # Store layout groups on ctx for diagnostic overlay generation
        ctx._bubble_layout_groups = bubble_groups

        # If any regions were not placed by the shape-aware solver, run fallback
        if unplaced_regions and not legacy_only:
            phase_start = perf_counter()
            prepare_bubbles(image=img, regions=unplaced_regions, font_path=active_font, render_config=render_cfg)
            layout_timing["fallback_ms"] += (perf_counter() - phase_start) * 1000.0
        elif unplaced_regions and legacy_only:
            phase_start = perf_counter()
            prepare_bubbles(image=img, regions=regions, font_path=active_font, render_config=render_cfg)
            layout_timing["fallback_ms"] += (perf_counter() - phase_start) * 1000.0

        ctx._bubble_detection_done = True
        ctx._bubble_layout_ready = True
        _record_content_trace(regions, "layout")
        if timing is not None:
            timing.update(layout_timing)


async def _run_isolated_text_rendering(
    translator: MangaTranslator,
    config: Config,
    ctx: Context,
) -> np.ndarray:
    """Render frozen bubble regions and prepared free text through separate paths."""
    render_canvas = ctx.img_inpainted.copy()
    if getattr(ctx, "img_rgb", None) is not None:
        render_canvas = restore_original(render_canvas, ctx.img_rgb, ctx.text_regions or [])

    for region in (ctx.text_regions or []):
        if getattr(region, "translation", None) and isinstance(region.translation, str):
            region.translation = config.render.transform_text_case(region.translation)
    _record_content_trace(ctx.text_regions, "render-input")
    _validate_render_integrity(ctx, strict=bool(getattr(ctx, "_strict_layout_validation", False)))

    render_regions = [
        region for region in (ctx.text_regions or [])
        if getattr(region, "translation", None)
        and region.translation.strip()
        and not (
            getattr(region, "placement_mode", None) is PlacementMode.FREE_TEXT
            and not getattr(region, "_free_text_solver_applied", False)
        )
    ]
    free_regions = [
        region for region in render_regions
        if getattr(region, "placement_mode", None) is PlacementMode.FREE_TEXT
        and getattr(region, "_free_text_solver_applied", False)
    ]
    free_ids = {id(region) for region in free_regions}
    bubble_and_legacy = [region for region in render_regions if id(region) not in free_ids]

    dispatch_log: List[Dict[str, Any]] = []
    draw_operations: List[Dict[str, Any]] = []

    if config.render.renderer == Renderer.none:
        output = render_canvas
    elif (
        config.render.renderer in (Renderer.manga2Eng, Renderer.manga2EngPillow)
        and bubble_and_legacy
        and LANGUAGE_ORIENTATION_PRESETS.get(bubble_and_legacy[0].target_lang) == "h"
    ):
        func_name = "dispatch_eng_render_pillow" if config.render.renderer == Renderer.manga2EngPillow else "dispatch_eng_render"
        for region in bubble_and_legacy:
            mode_val = getattr(getattr(region, "placement_mode", None), "value", str(getattr(region, "placement_mode", "")))
            entry = {
                "region_id": str(region.region_id),
                "placement_mode": mode_val,
                "renderer": config.render.renderer.value if hasattr(config.render.renderer, "value") else str(config.render.renderer),
                "function": func_name,
                "text": _render_text(region),
            }
            dispatch_log.append(entry)
            logger.info(
                f"RENDER DISPATCH region={entry['region_id']} mode={entry['placement_mode']} "
                f"renderer={entry['renderer']} function={entry['function']} text={entry['text']!r}"
            )

        if config.render.renderer == Renderer.manga2EngPillow:
            output = await dispatch_eng_render_pillow(
                render_canvas, ctx.img_rgb, bubble_and_legacy, translator.font_path, config.render.line_spacing
            )
        else:
            output = await dispatch_eng_render(
                render_canvas, ctx.img_rgb, bubble_and_legacy, translator.font_path, config.render.line_spacing
            )
    else:
        for region in bubble_and_legacy:
            mode_val = getattr(getattr(region, "placement_mode", None), "value", str(getattr(region, "placement_mode", "")))
            entry = {
                "region_id": str(region.region_id),
                "placement_mode": mode_val,
                "renderer": config.render.renderer.value if hasattr(config.render.renderer, "value") else str(config.render.renderer),
                "function": "dispatch_rendering",
                "text": _render_text(region),
            }
            dispatch_log.append(entry)
            logger.info(
                f"RENDER DISPATCH region={entry['region_id']} mode={entry['placement_mode']} "
                f"renderer={entry['renderer']} function={entry['function']} text={entry['text']!r}"
            )

        output = await dispatch_rendering(
            render_canvas,
            bubble_and_legacy,
            translator.font_path,
            config.render.font_size,
            config.render.font_size_offset,
            config.render.font_size_minimum,
            not config.render.no_hyphenation,
            ctx.render_mask,
            config.render.line_spacing,
        )

    for region in bubble_and_legacy:
        ops = getattr(region, "_draw_operations", None)
        if ops:
            for op in ops:
                draw_operations.append(op)
                logger.info(
                    f"DRAW region_id={op.get('region_id')} text={op.get('text')!r} "
                    f"x={op.get('x')} y={op.get('y')} font_size={op.get('font_size')} "
                    f"bbox={op.get('bbox')} renderer={op.get('renderer')}"
                )
        else:
            bounds = getattr(region, "layout_bounds", None) or getattr(region, "xyxy", [0, 0, 0, 0])
            op = {
                "region_id": str(region.region_id),
                "text": _render_text(region),
                "x": int(bounds[0]),
                "y": int(bounds[1]),
                "width": int(bounds[2] - bounds[0]),
                "height": int(bounds[3] - bounds[1]),
                "font_size": getattr(region, "font_size", 0),
                "bbox": [int(b) for b in bounds],
                "renderer": config.render.renderer.value if hasattr(config.render.renderer, "value") else str(config.render.renderer),
            }
            region._draw_operations = [op]
            draw_operations.append(op)
            logger.info(
                f"DRAW region_id={op['region_id']} text={op['text']!r} "
                f"x={op['x']} y={op['y']} font_size={op['font_size']} "
                f"bbox={op['bbox']} renderer={op['renderer']}"
            )

    for region in free_regions:
        mode_val = getattr(getattr(region, "placement_mode", None), "value", str(getattr(region, "placement_mode", "")))
        entry = {
            "region_id": str(region.region_id),
            "placement_mode": mode_val,
            "renderer": "free_text_direct",
            "function": "_composite_box_to_image",
            "text": _render_text(region),
        }
        dispatch_log.append(entry)
        logger.info(
            f"RENDER DISPATCH region={entry['region_id']} mode={entry['placement_mode']} "
            f"renderer={entry['renderer']} function={entry['function']} text={entry['text']!r}"
        )
        box = getattr(region, "_bubble_box", None)
        points = getattr(region, "_bubble_points", None)
        if box is not None and points is not None and np.any(box[:, :, 3]):
            output = _composite_box_to_image(output, box, points)
            ops = getattr(region, "_draw_operations", None)
            if ops:
                for op in ops:
                    draw_operations.append(op)
                    logger.info(
                        f"DRAW region_id={op.get('region_id')} text={op.get('text')!r} "
                        f"x={op.get('x')} y={op.get('y')} font_size={op.get('font_size')} "
                        f"bbox={op.get('bbox')} renderer={op.get('renderer')}"
                    )
            else:
                bounds = getattr(region, "layout_bounds", None) or [0, 0, 0, 0]
                op = {
                    "region_id": str(region.region_id),
                    "text": _render_text(region),
                    "x": int(bounds[0]),
                    "y": int(bounds[1]),
                    "width": int(bounds[2] - bounds[0]),
                    "height": int(bounds[3] - bounds[1]),
                    "font_size": getattr(region, "font_size", 0),
                    "bbox": [int(b) for b in bounds],
                    "renderer": "free_text_direct",
                }
                region._draw_operations = [op]
                draw_operations.append(op)
                logger.info(
                    f"DRAW region_id={op['region_id']} text={op['text']!r} "
                    f"x={op['x']} y={op['y']} font_size={op['font_size']} "
                    f"bbox={op['bbox']} renderer={op['renderer']}"
                )

    ctx._render_dispatch_log = dispatch_log
    ctx._draw_operations = draw_operations
    return restore_original(output, ctx.img_rgb, ctx.text_regions or [])


def run_fast_placement_and_render(
    ctx: Context,
    config: Config,
    font_path: Optional[str] = None,
    renderer_override: Optional[Union[str, Renderer]] = None,
    font_size_override: Optional[int] = None,
    font_size_offset: Optional[int] = None,
    font_size_minimum: Optional[int] = None,
    line_spacing_override: Optional[float] = None,
    letter_case_override: Optional[str] = None,
    no_hyphenation: Optional[bool] = None,
    enable_bubble_layout: bool = True,
    use_gpu: Optional[bool] = None,
    device: Optional[str] = None,
    # --- Shape-aware solver config ---
    solver_margin: Optional[float] = None,
    solver_max_y_trials: Optional[int] = None,
    legacy_only: bool = False,
    solver_report: bool = False,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Execute ONLY the placement, text fit, and rendering stages rapidly on existing inpainted pixels.

    Parameters
    ----------
    solver_margin:
        Override the safe-mask margin (pixels from bubble edge to text). Passed to BubbleGeometry.
    solver_max_y_trials:
        Override the max y_origin trials per font size in the solver.
    legacy_only:
        If True, skip the new shape-aware solver entirely and use the original lobe-rect path.
    solver_report:
        If True, include a ``solver_diagnostics`` list in the returned timing dict, one entry per
        region, containing font_size, p5_clearance, score, path (``solver`` or ``legacy``), and ms.
    """
    timing: Dict[str, Any] = {}
    layout_timing: Dict[str, float] = {
        "mask_prep_ms": 0.0,
        "mode_classification_ms": 0.0,
        "bubble_solver_ms": 0.0,
        "free_text_solver_ms": 0.0,
        "fallback_ms": 0.0,
    }
    t0 = perf_counter()

    # Determine acceleration device
    default_gpu, default_dev = detect_best_device()
    actual_use_gpu = use_gpu if use_gpu is not None else default_gpu
    actual_device = device or (default_dev if actual_use_gpu else "cpu")

    # Apply configuration overrides
    render_cfg = config.render
    if renderer_override is not None:
        render_cfg.renderer = Renderer(renderer_override)
    if font_size_override is not None:
        render_cfg.font_size = font_size_override
    if font_size_offset is not None:
        render_cfg.font_size_offset = font_size_offset
    if font_size_minimum is not None:
        render_cfg.font_size_minimum = font_size_minimum
    if line_spacing_override is not None:
        render_cfg.line_spacing = line_spacing_override
    if letter_case_override is not None:
        val = str(letter_case_override).strip().lower()
        if val in ("upper", "uppercase", "all_caps", "caps"):
            render_cfg.uppercase = True
            render_cfg.lowercase = False
        elif val in ("lower", "lowercase"):
            render_cfg.lowercase = True
            render_cfg.uppercase = False
        else:
            render_cfg.uppercase = False
            render_cfg.lowercase = False
    if no_hyphenation is not None:
        render_cfg.no_hyphenation = no_hyphenation

    # Resolve one font for both solver measurements and the final renderer.
    active_font = font_path or getattr(render_cfg, "font_path", None) or get_default_eng_font()

    # Initialize MangaTranslator to use the app's exact pipeline methods with MPS/GPU acceleration
    translator = MangaTranslator({
        "font_path": active_font,
        "use_gpu": actual_use_gpu,
        "device": actual_device,
    })
    translator.font_path = active_font
    ctx._strict_layout_validation = bool(solver_report)
    _ensure_region_identities(ctx.text_regions)

    # Ensure all regions have translation populated
    for region in (ctx.text_regions or []):
        if not getattr(region, "translation", None):
            region.translation = getattr(region, "text", "")

    # Configure bubble detection flag
    config.bubble_detection.enabled = enable_bubble_layout

    # 1. Bubble geometry placement and text fit using the shape-aware solver
    reset_solver_profile()
    t_layout = perf_counter()
    if enable_bubble_layout and getattr(ctx, "img_rgb", None) is not None:
        try:
            apply_shape_aware_bubble_layout(
                ctx=ctx,
                config=config,
                font_path=active_font,
                solver_margin=solver_margin if solver_margin is not None else 2.0,
                solver_max_y_trials=solver_max_y_trials if solver_max_y_trials is not None else 12,
                legacy_only=legacy_only,
                timing=layout_timing,
            )
        except Exception as e:
            logger.warning(f"Shape-aware bubble layout failed, falling back to standard placement: {e}")
            t_fallback = perf_counter()
            try:
                translator._prepare_bubble_layout(config, ctx)
            except Exception as e2:
                logger.warning(f"Standard bubble layout fallback failed: {e2}")
            layout_timing["fallback_ms"] += (perf_counter() - t_fallback) * 1000.0
    timing["placement_and_fit_ms"] = (perf_counter() - t_layout) * 1000.0
    measured_layout_ms = sum(layout_timing.values())
    layout_timing["other_ms"] = max(0.0, timing["placement_and_fit_ms"] - measured_layout_ms)
    timing["layout_breakdown"] = layout_timing
    timing["solver_profiling"] = get_solver_profile().to_dict()

    # 2. Rendering and canvas composition using the app's exact _run_text_rendering
    t_render = perf_counter()
    output = asyncio.run(_run_isolated_text_rendering(translator, config, ctx))
    timing["render_dispatch_ms"] = (perf_counter() - t_render) * 1000.0
    timing["total_ms"] = (perf_counter() - t0) * 1000.0

    # 3. Optional per-region solver diagnostics
    if solver_report:
        diagnostics = _collect_solver_diagnostics(ctx.text_regions or [], font_path=active_font)
        timing["solver_diagnostics"] = diagnostics

    return output, timing


# ---------------------------------------------------------------------------
# Direct solver invocation — bypasses MangaTranslator, calls BubbleGeometry
# + solve_layout directly on each region for diagnostic purposes.
# ---------------------------------------------------------------------------

def run_solver_direct(
    ctx: Context,
    config: Config,
    font_path: Optional[str] = None,
    use_gpu: Optional[bool] = None,
    device: Optional[str] = None,
    margin: float = 2.0,
    max_y_trials: int = 12,
    legacy_only: bool = False,
    verbose: bool = True,
) -> Tuple[Optional[np.ndarray], List[Dict[str, Any]]]:
    """Run the shape-aware solver directly on each region and return per-region diagnostics.

    This is the testing-harness path: it builds BubbleGeometry and calls solve_layout
    without going through MangaTranslator._prepare_bubble_layout, giving full visibility
    into solver internals.

    Returns
    -------
    (composite_image, region_reports)
        composite_image: None if rendering was skipped
        region_reports: list of dicts with keys:
            region_idx, text, font_size_target, font_size_result, path,
            score, p5_clearance, elapsed_ms, lines
    """
    with _RENDER_LOCK:
        active_font = font_path or getattr(config.render, "font_path", None) or get_default_eng_font()
        text_render.set_font(active_font)

        regions = ctx.text_regions or []
        img = ctx.img_inpainted if getattr(ctx, "img_inpainted", None) is not None else getattr(ctx, "img_rgb", None)
        if img is None:
            return None, []

        # Ensure bubble masks/interiors are prepared
        prepare_bubble_masks(img, regions)
        classify_placement_modes(regions)
        obstacles = build_page_obstacle_map(regions, img.shape[:2])
        inpaint_mask = getattr(ctx, "inpaint_mask", None)
        if inpaint_mask is None:
            inpaint_mask = getattr(ctx, "text_mask", None)
        if inpaint_mask is None:
            inpaint_mask = getattr(ctx, "mask_raw", None)
        if inpaint_mask is None:
            inpaint_mask = getattr(ctx, "mask", None)
        free_zones = build_free_text_ownership_zones(
            [region for region in regions if getattr(region, "placement_mode", None) is PlacementMode.FREE_TEXT],
            obstacles,
            inpaint_mask=inpaint_mask,
        )

        gray = img if img.ndim == 2 else img[:, :, 0]
        reports: List[Dict[str, Any]] = []

        for i, region in enumerate(regions):
            text = getattr(region, "translation", None) or getattr(region, "text", "") or ""
            interior = getattr(region, "_bubble_interior", None)

            report: Dict[str, Any] = {
                "region_idx": i,
                "text": text[:60] + ("…" if len(text) > 60 else ""),
                "font_size_target": None,
                "font_size_result": None,
                "path": "skip",
                "score": None,
                "p5_clearance": None,
                "elapsed_ms": None,
                "lines": [],
            }

            if not text.strip() or interior is None or not np.any(interior):
                if not legacy_only and getattr(region, "placement_mode", None) is PlacementMode.FREE_TEXT:
                    ft_zone = free_zones.get(id(region))
                    if ft_zone is not None:
                        free_result = _solve_free_text_region(
                            region, ft_zone,
                            obstacles, config, img.shape[:2], margin, max_y_trials,
                        )
                        if free_result is not None:
                            candidate, source_profile, qa = free_result
                            report["path"] = "free_text"
                            report["font_size_result"] = candidate.font_size
                            report["score"] = candidate.penalty
                            report["p5_clearance"] = candidate.glyph_clearance_p5
                            report["status"] = candidate.status
                            report["qa"] = qa
                            report["lines"] = [
                                {"text": ln.text, "x": ln.x, "y": ln.y, "width": ln.width}
                                for ln in candidate.lines
                            ]
                reports.append(report)
                continue

            t0 = perf_counter()
            render_cfg = config.render
            minimum = render_cfg.font_size_minimum
            if minimum == -1:
                minimum = round(sum(img.shape[:2]) / 200)
            minimum = max(1, minimum)
            target = max(minimum, render_cfg.font_size or region.font_size + render_cfg.font_size_offset)
            if render_cfg.font_size is None:
                target = max(target, _estimate_adaptive_font_size(interior, text, minimum))

            report["font_size_target"] = target

            fg, bg = fg_bg_compare(*region.get_font_colors())
            stroke_width = max(1, int(target * 0.07)) if bg is not None else 0

            geom = BubbleGeometry(interior)
            words_list = text.split()
            lang = getattr(region, "target_lang", "en_US") or "en_US"
            hyphenate = not render_cfg.no_hyphenation
            ls = render_cfg.line_spacing or 0.0
            source_profile = build_original_layout_profile(region, interior)
            bubble_mask = getattr(region, "_bubble_mask", None)
            mask_for_lobe = bubble_mask if bubble_mask is not None and np.any(bubble_mask) else interior
            lobe_graph = build_lobe_graph(mask_for_lobe) if np.any(mask_for_lobe) else None

            result = solve_layout(
                geom=geom,
                words=words_list,
                font_size_max=target,
                font_size_min=minimum,
                language=lang,
                hyphenate=hyphenate,
                line_spacing=ls,
                stroke_width=stroke_width,
                margin=margin,
                y_origin_step=max(2, target // 8),
                max_y_origin_trials=max_y_trials,
                source_profile=source_profile,
                lobe_graph=lobe_graph,
            )

            elapsed = (perf_counter() - t0) * 1000.0
            report["elapsed_ms"] = elapsed

            if result is not None and result.valid:
                report["path"] = "solver"
                report["font_size_result"] = result.font_size
                report["score"] = result.penalty
                report["p5_clearance"] = result.glyph_clearance_p5
                report["status"] = result.status
                report["source_profile"] = {
                    "font_size": source_profile.font_size,
                    "line_count": source_profile.line_count,
                    "centroid": [source_profile.centroid[0], source_profile.centroid[1]],
                    "occupancy": source_profile.occupancy,
                } if source_profile else None
                report["qa"] = result.qa
                report["lines"] = [
                    {"text": ln.text, "x": ln.x, "y": ln.y, "width": ln.width}
                    for ln in result.lines
                ]
            else:
                # Phase 13 marker: text too long for the bubble at the
                # minimum font size — translation compression should retry.
                report["path"] = "solver→legacy" if result is not None else "solver_failed"
                report["font_size_result"] = result.font_size if result else None
                report["status"] = (
                    "requires_compression" if result is None
                    else result.status if result.status != "ok"
                    else "invalid_layout"
                )
                report["score"] = result.penalty if result else None
                report["qa"] = result.qa if result else {}

            if verbose:
                path_label = report["path"]
                s = report["font_size_result"] or "?"
                tgt = target
                p5 = f"{report['p5_clearance']:.1f}px" if report["p5_clearance"] is not None else "n/a"
                score = f"{report['score']:.2f}" if report["score"] is not None else "n/a"
                n_lines = len(report["lines"])
                orig_lines = report.get("source_profile", {}) or {}
                orig_str = f"orig(lines={orig_lines.get('line_count')}, font={orig_lines.get('font_size'):.0f})" if orig_lines else ""
                qa = report.get("qa", {}) or {}
                occ = f"occ={qa['occupancy']:.0%}" if "occupancy" in qa else ""
                cerr = f"cerr={qa['center_error_px']:.1f}px" if "center_error_px" in qa else ""
                cdx = f"dx={qa['block_center_dx']:+.0f}" if "block_center_dx" in qa else ""
                cdy = f"dy={qa['block_center_dy']:+.0f}" if "block_center_dy" in qa else ""
                sim = f"sim={qa['source_sim']:.2f}" if "source_sim" in qa else ""
                gap = f"gap={qa['gap_ratio']:.1%}" if "gap_ratio" in qa else ""
                vbal = f"vbal={qa['vertical_balance']:.3f}" if "vertical_balance" in qa else ""
                status_str = report.get("status", "")

                if path_label == "free_text":
                    cov_str = f"cov={qa.get('damage_coverage', 0):.0%}"
                    core_cov_str = f"core={qa.get('core_damage_coverage', 0):.0%}"
                    exp_str = f"exp={qa.get('expansion_ratio', 1.0):.2f}x"
                    print(
                        f"  [{i:2d}] {path_label:<16} font {tgt}→{s:<3} "
                        f"lines={n_lines:<2} {cov_str:<10} {core_cov_str:<10} {exp_str:<10} pen={score:<8} "
                        f"{elapsed:5.1f}ms {status_str} {orig_str}  {report['text']!r}"
                    )
                    if "candidate_alternatives" in qa and qa["candidate_alternatives"]:
                        for idx, alt in enumerate(qa["candidate_alternatives"][:5], 1):
                            is_sel = (alt["lines"] == len(report["lines"]) and alt["font_size"] == report["font_size_result"])
                            sel_str = " (SELECTED)" if is_sel else ""
                            print(f"       Candidate #{idx}: font={alt['font_size']} lines={alt['lines']} coverage={alt.get('damage_coverage', 0.0):.0%} core={alt.get('core_coverage', 0.0):.0%} expansion={alt.get('expansion_ratio', 1.0):.2f}x drift={alt.get('center_drift', alt.get('center_error_px', 0.0)):.2f} pen={alt['penalty']:.1f}{sel_str}")
                else:
                    print(
                        f"  [{i:2d}] {path_label:<16} font {tgt}→{s:<3} "
                        f"lines={n_lines:<2} p5={p5:<8} pen={score:<8} {occ:<10} {cerr:<11} {cdx} {cdy} {vbal:<12} {gap:<10} {sim:<10} "
                        f"{elapsed:5.1f}ms {status_str} {orig_str}  {report['text']!r}"
                    )
                    if result is not None and "candidate_alternatives" in result.qa and result.qa["candidate_alternatives"]:
                        z_w, z_h = result.qa.get("zone_size", (0, 0))
                        z_ar = result.qa.get("zone_aspect", 0.0)
                        z_cap = result.qa.get("zone_line_capacity", 0)
                        print(f"       Zone:")
                        print(f"         size:                 {z_w:.0f} × {z_h:.0f}")
                        print(f"         aspect:               {z_ar:.2f}")
                        print(f"         line capacity:        ~{z_cap}")
                        for idx, alt in enumerate(result.qa["candidate_alternatives"][:6], 1):
                            is_sel = (alt["lines"] == len(result.lines) and alt["font_size"] == result.font_size)
                            sel_str = " (SELECTED)" if is_sel else ""
                            print(f"       Candidate #{idx}:")
                            print(f"         lines:                {alt['lines']}")
                            print(f"         vertical utilization: {alt['vertical_utilization']:.2f}")
                            print(f"         aspect mismatch:      {alt['aspect_mismatch']:.2f}")
                            print(f"         total:                {alt['penalty']:.1f}{sel_str}")
                        sel_num = next((idx for idx, alt in enumerate(result.qa["candidate_alternatives"][:6], 1) if alt["lines"] == len(result.lines) and alt["font_size"] == result.font_size), 1)
                        print(f"       SELECTED: #{sel_num}")
                    if result is not None and "gap_details" in result.qa and result.qa["gap_details"]:
                        print(f"       Line Gaps & Rhythm:")
                        for g_info in result.qa["gap_details"]:
                            l_from = g_info["line_from"]
                            l_to = g_info["line_to"]
                            g_h = g_info["gap_h"]
                            reason = g_info["reason"]
                            status_icon = "✓" if g_info["valid"] else "❌"
                            print(f"         {l_from!r} → {l_to!r}: gap={g_h:.2f}H, reason={reason} {status_icon}")
                    elif result is not None and len(result.lines) > 1:
                        for l_idx in range(len(result.lines) - 1):
                            l1, l2 = result.lines[l_idx], result.lines[l_idx + 1]
                            c1 = l1.x + l1.width / 2.0
                            c2 = l2.x + l2.width / 2.0
                            dx = abs(c2 - c1)
                            dx_font = dx / max(1.0, float(result.font_size))
                            ov_l = max(l1.slot.left, l2.slot.left)
                            ov_r = min(l1.slot.right, l2.slot.right)
                            ov_w = max(0, ov_r - ov_l)
                            ov_ratio = ov_w / float(max(1, min(l1.slot.width, l2.slot.width)))
                            t_cost = _transition_cost(l1.slot, c1, l2.slot, c2, result.font_size)
                            print(
                                f"       line {l_idx+1} -> {l_idx+2}: dx={dx:.1f}px (dx/font={dx_font:.2f}), "
                                f"overlap={ov_ratio:.2f}, trans_cost={t_cost:.2f}"
                            )

            reports.append(report)

        return None, reports


def _collect_solver_diagnostics(regions: list, font_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Collect solver result metadata attached to regions."""
    diagnostics = []
    for i, region in enumerate(regions):
        mode = getattr(getattr(region, "placement_mode", None), "value", getattr(region, "placement_mode", None))
        path = getattr(region, "_solver_path", None) or ("solver" if getattr(region, "_solver_applied", False) else "legacy")
        p5 = getattr(region, "_solver_p5", None)
        score = getattr(region, "_solver_score", None)
        typography = getattr(region, "_typography_report", None)
        if typography is None:
            text = getattr(region, "translation", None) or getattr(region, "text", "")
            size = int(getattr(region, "font_size", 0) or 0)
            if text and size > 0:
                typography = _build_typography_report(text, font_path, size, getattr(region, "layout_segments", None))
                region._typography_report = typography
        diagnostics.append({
            "region_idx": i,
            "region_id": str(getattr(region, "region_id", f"region_{i}")),
            "source_region_ids": list(getattr(region, "source_region_ids", [])),
            "text": getattr(region, "text", "")[:40],
            "translation": getattr(region, "translation", ""),
            "layout_input_text": getattr(region, "_layout_input_text", None),
            "render_input_text": _render_text(region),
            "content_trace": getattr(region, "_content_trace", []),
            "content_integrity": not bool(getattr(region, "_layout_integrity_issues", [])),
            "placement_mode": mode,
            "font_size": getattr(region, "font_size", None),
            "review_reason": getattr(region, "review_reason", None),
            "layout_bounds": getattr(region, "layout_bounds", None),
            "path": path,
            "status": getattr(region, "_solver_status", None),
            "p5_clearance": p5,
            "score": score,
            "qa": getattr(region, "_solver_qa", None),
            "typography": typography,
            "draw_operations": getattr(region, "_draw_operations", []),
            "inpaint_mask_coverage": getattr(region, "_inpaint_mask_coverage", None),
        })
    return diagnostics



IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
    ".avif", ".jxl", ".gif", ".jfif", ".jp2", ".pbm", ".pgm", ".ppm", ".pnm"
}


def expand_input_images(input_patterns: List[str]) -> List[Path]:
    """Expand input strings into a list of existing image file paths supporting wildcards, directories, and multiple formats."""
    import re
    image_paths: List[Path] = []
    for pattern in input_patterns:
        raw_pattern = str(pattern).strip("'\"")
        p = Path(raw_pattern)

        # 1. Direct file matching
        if p.is_file():
            if p.suffix.lower() in IMAGE_EXTENSIONS:
                image_paths.append(p.resolve())
            continue

        # 2. Directory (scan all image files recursively)
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
                    image_paths.append(child.resolve())
            continue

        # 3. Glob matching (supports * and ** recursive globbing)
        matches = glob.glob(raw_pattern, recursive=True)
        if not matches and not os.path.isabs(raw_pattern):
            # Also try matching relative to PROJECT_ROOT
            matches = glob.glob(str(PROJECT_ROOT / raw_pattern), recursive=True)

        if matches:
            for m in matches:
                mp = Path(m)
                if mp.is_file() and mp.suffix.lower() in IMAGE_EXTENSIONS:
                    image_paths.append(mp.resolve())
                elif mp.is_dir():
                    for child in sorted(mp.rglob("*")):
                        if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
                            image_paths.append(child.resolve())

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for path in image_paths:
        if path not in seen:
            seen.add(path)
            deduped.append(path)

    # Natural sort by filename (e.g. Page-2 before Page-10)
    deduped = sorted(
        deduped,
        key=lambda p: [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', str(p.name))]
    )
    return deduped


def expand_sample_directories(sample_inputs: List[str], data_base_dir: Union[str, Path]) -> List[Path]:
    """Find all valid sample directories from user inputs, wildcards, or --all."""
    import re
    data_base_dir = Path(data_base_dir)
    sample_dirs: List[Path] = []

    for item in sample_inputs:
        raw_item = str(item).strip("'\"")
        p = Path(raw_item)
        if p.is_dir() and ((p / "regions.json").is_file() or (p / "step_data.pkl").is_file()):
            sample_dirs.append(p.resolve())
            continue

        # Check relative to data_base_dir
        rel_p = data_base_dir / raw_item
        if rel_p.is_dir() and ((rel_p / "regions.json").is_file() or (rel_p / "step_data.pkl").is_file()):
            sample_dirs.append(rel_p.resolve())
            continue

        # Glob under working directory
        matches = glob.glob(raw_item, recursive=True)
        if not matches:
            # Glob under data_base_dir
            matches = glob.glob(str(data_base_dir / raw_item), recursive=True)

        for m in sorted(matches):
            mp = Path(m)
            if mp.is_dir() and ((mp / "regions.json").is_file() or (mp / "step_data.pkl").is_file()):
                sample_dirs.append(mp.resolve())

    # Deduplicate
    seen = set()
    deduped = []
    for d in sample_dirs:
        if d not in seen:
            seen.add(d)
            deduped.append(d)

    deduped = sorted(
        deduped,
        key=lambda p: [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', str(p.name))]
    )
    return deduped


async def _capture_single_image(
    image_path: Path,
    output_base_dir: Path,
    config: Config,
    translator: MangaTranslator,
    semaphore: asyncio.Semaphore,
) -> Tuple[Path, float, bool, str]:
    """Run OCR + Detection + Bubble Segmentation + Textline Merge + Inpainting (and optional Translation)."""
    async with semaphore:
        sample_name = image_path.stem
        logger.info(f"==> [Capture] Processing page: {image_path.name}")
        t0 = perf_counter()
        try:
            pil_img = Image.open(image_path).convert("RGB")

            # Initialize context
            ctx = Context()
            ctx.input = pil_img
            ctx.img_rgb, ctx.img_alpha = load_image(ctx.input)

            # 1. Text Detection
            detected_textlines, ctx.mask_raw, detector_mask = await translator._run_detection(config, ctx)
            ctx.textlines = detected_textlines

            # 2. OCR (recognize textlines)
            if ctx.textlines:
                ctx.textlines = await translator._run_ocr(config, ctx)

            # 3. Textline Merge
            if ctx.textlines:
                ctx.text_regions = await translator._run_textline_merge(config, ctx)
            else:
                ctx.text_regions = []
            _ensure_region_identities(ctx.text_regions)
            _record_content_trace(ctx.text_regions, "ocr-merge")

            # 4. Speech Bubble Segmentation
            if config.bubble_detection.enabled:
                await translator._detect_speech_bubbles(config, ctx)
                _ensure_region_identities(ctx.text_regions)
                _record_content_trace(ctx.text_regions, "bubble-group")

            # 5. Translation (if configured; otherwise use OCR text as translation)
            if config.translator.translator != Translator.none and ctx.text_regions:
                translation_order = tuple(str(region.region_id) for region in ctx.text_regions)
                ctx.text_regions = await translator._run_text_translation(config, ctx)
                _ensure_region_identities(ctx.text_regions)
                if tuple(str(region.region_id) for region in ctx.text_regions) != translation_order:
                    raise RuntimeError("Translation changed region identity/order; refusing ambiguous render ownership")
            else:
                for region in (ctx.text_regions or []):
                    if not getattr(region, "translation", None):
                        region.translation = getattr(region, "text", "")
                    region.target_lang = config.translator.target_lang or "ENG"
            _ensure_region_identities(ctx.text_regions)
            _record_content_trace(ctx.text_regions, "translation")

            # 6. Mask Refinement (keep detector pixels for OCR-dropped boxes)
            if detected_textlines or ctx.text_regions or getattr(ctx, "bubble_detections", None):
                ctx.text_mask = (
                    await translator._run_mask_refinement(config, ctx)
                    if ctx.text_regions
                    else np.zeros(ctx.img_rgb.shape[:2], dtype=np.uint8)
                )
                ctx.bubble_mask = prepare_bubble_masks(ctx.img_rgb, ctx.text_regions)
                ctx.mask = ctx.text_mask.copy()
                detector_cleanup = _detector_cleanup_mask(
                    detected_textlines,
                    detector_mask if detector_mask is not None else ctx.mask_raw,
                    ctx.img_rgb.shape,
                )
                ctx.mask = np.maximum(ctx.mask, detector_cleanup)
            else:
                ctx.text_mask = np.zeros(ctx.img_rgb.shape[:2], dtype=np.uint8)
                ctx.bubble_mask = np.zeros(ctx.img_rgb.shape[:2], dtype=np.uint8)
                ctx.mask = np.zeros(ctx.img_rgb.shape[:2], dtype=np.uint8)
            # This is the mask passed to the inpainting model. Layout must use
            # this exact mask, not a reconstructed text bounding box.
            ctx.inpaint_mask = ctx.mask.copy()

            # 7. Speech Bubble Layout Geometry, now anchored to the final mask.
            if config.bubble_detection.enabled and getattr(ctx, "img_rgb", None) is not None:
                try:
                    apply_shape_aware_bubble_layout(
                        ctx=ctx,
                        config=config,
                        font_path=translator.font_path,
                    )
                except Exception as e:
                    logger.warning(f"Shape-aware bubble layout preparation fallback: {e}")
                    try:
                        translator._prepare_bubble_layout(config, ctx)
                    except Exception as e2:
                        logger.warning(f"Standard bubble layout fallback failed: {e2}")

            # 8. Inpainting
            ctx.img_inpainted = await translator._run_inpainting(config, ctx)
            _record_content_trace(ctx.text_regions, "inpaint")

            elapsed_ms = (perf_counter() - t0) * 1000.0

            saved_dir = save_step_data(
                output_base_dir=output_base_dir,
                sample_name=sample_name,
                ctx=ctx,
                config=config,
                source_path=str(image_path),
                duration_ms=elapsed_ms,
            )
            return saved_dir, elapsed_ms, True, "OK"
        except Exception as e:
            elapsed_ms = (perf_counter() - t0) * 1000.0
            logger.error(f"Failed to capture '{image_path.name}': {e}", exc_info=True)
            return Path(output_base_dir) / sample_name, elapsed_ms, False, str(e)


async def execute_capture(
    image_paths: List[Path],
    output_base_dir: Path,
    config: Config,
    use_gpu: bool = False,
    device: Optional[str] = None,
    concurrency: int = 1,
) -> List[Tuple[Path, float, bool, str]]:
    """Run OCR + detect + bubble segmentation + merge + inpaint capture on multiple images concurrently."""
    output_base_dir.mkdir(parents=True, exist_ok=True)
    # Ensure English pages are never skipped because source_lang == target_lang
    config.translator.no_text_lang_skip = True
    config.ocr.min_text_length = 1
    translator_params = {
        "use_gpu": use_gpu,
        "device": device,
        "verbose": True,
        "batch_size": max(1, concurrency),
    }
    translator = MangaTranslator(translator_params)

    # Pre-warm models (detection, OCR, bubble detection, inpainting, translation)
    if config.detector.detector != Detector.none:
        await prepare_detection(config.detector.detector)
    await prepare_ocr(config.ocr.ocr, translator.device)
    if config.bubble_detection.enabled:
        await prepare_bubble_detection(config.bubble_detection, translator.device)
    if config.inpainter.inpainter != Inpainter.none:
        await prepare_inpainting(config.inpainter.inpainter, translator.device)
    if config.translator.translator != Translator.none:
        await prepare_translation(config.translator.translator_gen)

    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        _capture_single_image(img_path, output_base_dir, config, translator, semaphore)
        for img_path in image_paths
    ]
    results = await asyncio.gather(*tasks)
    return results


def execute_fast_render_batch(
    sample_dirs: List[Path],
    output_filename: str = "rendered.png",
    output_dir: Optional[Path] = None,
    font_path: Optional[str] = None,
    renderer: Optional[str] = None,
    font_size: Optional[int] = None,
    font_size_offset: Optional[int] = None,
    font_size_minimum: Optional[int] = None,
    line_spacing: Optional[float] = None,
    letter_case: Optional[str] = None,
    no_hyphenation: Optional[bool] = None,
    enable_bubble_layout: bool = True,
    use_json: bool = False,
    use_gpu: Optional[bool] = None,
    device: Optional[str] = None,
    concurrency: int = 1,
    # --- Shape-aware solver params ---
    solver_margin: Optional[float] = None,
    solver_max_y_trials: Optional[int] = None,
    legacy_only: bool = False,
    solver_report: bool = False,
) -> List[Dict[str, Any]]:
    """Fit OCR text back onto pages and render multiple sample datasets concurrently."""
    def _render_one(sample_dir: Path) -> Dict[str, Any]:
        result_info: Dict[str, Any] = {
            "sample_dir": str(sample_dir),
            "sample_name": sample_dir.name,
            "success": False,
            "error": None,
            "timing": {},
        }
        try:
            ctx, config = load_step_data(sample_dir, use_json=use_json)
            rendered_rgb, timing = run_fast_placement_and_render(
                ctx=ctx,
                config=config,
                font_path=font_path,
                renderer_override=renderer,
                font_size_override=font_size,
                font_size_offset=font_size_offset,
                font_size_minimum=font_size_minimum,
                line_spacing_override=line_spacing,
                letter_case_override=letter_case,
                no_hyphenation=no_hyphenation,
                enable_bubble_layout=enable_bubble_layout,
                use_gpu=use_gpu,
                device=device,
                solver_margin=solver_margin,
                solver_max_y_trials=solver_max_y_trials,
                legacy_only=legacy_only,
                solver_report=solver_report,
            )

            # Determine destination path
            if output_dir:
                dest_path = output_dir / f"{sample_dir.name}_{output_filename}"
            else:
                dest_path = sample_dir / output_filename

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(dest_path), cv2.cvtColor(rendered_rgb, cv2.COLOR_RGB2BGR))

            # Export placement zones visualization if multi-region layout groups exist
            layout_groups = getattr(ctx, "_bubble_layout_groups", None)
            if getattr(ctx, "img_rgb", None) is not None and layout_groups and any(len(g.regions) > 1 for g in layout_groups):
                try:
                    vis_zones = create_placement_zones_visualization(ctx.img_rgb, layout_groups)
                    zones_path = dest_path.parent / (f"{sample_dir.name}_placement_zones.png" if output_dir else "placement_zones.png")
                    cv2.imwrite(str(zones_path), cv2.cvtColor(vis_zones, cv2.COLOR_RGB2BGR))
                except Exception as e:
                    logger.warning(f"Could not save placement_zones visualization: {e}")
            free_text_debug = getattr(ctx, "_free_text_layout_debug", None)
            if free_text_debug is not None:
                debug_path = dest_path.parent / (
                    f"{sample_dir.name}_free_text_layout_debug.png" if output_dir else "free_text_layout_debug.png"
                )
                cv2.imwrite(str(debug_path), cv2.cvtColor(free_text_debug, cv2.COLOR_RGB2BGR))

            result_info["success"] = True
            result_info["output_path"] = str(dest_path)
            result_info["timing"] = timing
            result_info["regions_count"] = len(ctx.text_regions or [])
        except Exception as e:
            logger.error(f"Render failed for '{sample_dir.name}': {e}", exc_info=True)
            result_info["error"] = str(e)

        return result_info

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        results = list(executor.map(_render_one, sample_dirs))

    return results


# ------------------------------------------------------------------
# Typography Diagnostics — Font-Metrics & Rendering Correctness
# ------------------------------------------------------------------
# Phases 1–10: A diagnostic system that validates font rendering
# correctness independently of the bubble layout solver.
#
# Target invariant: For a candidate with font_size = S, every
# character is produced from the same font face, same pixel size,
# same scale, same baseline model, same stroke policy.
# ------------------------------------------------------------------

import inspect as _inspect

@dataclass(frozen=True)
class GlyphMetrics:
    """Complete metrics for a single rasterized glyph."""
    character: str
    font_path: str
    face_index: int          # Index into FONT_SELECTION that resolved
    font_size: int
    is_fallback: bool        # True if primary face lacked the codepoint
    advance_x: int           # Horizontal advance (pixels)
    bitmap_width: int
    bitmap_rows: int
    bitmap_left: int         # Horizontal bearing from origin to left edge
    bitmap_top: int          # Vertical bearing from baseline to top edge
    hori_advance_26_6: int   # Raw 26.6 fixed-point horiAdvance
    hori_bearing_x: int      # horiBearingX >> 6
    hori_bearing_y: int      # horiBearingY >> 6


@dataclass(frozen=True)
class FontContext:
    """Immutable resolved typography configuration.

    Captures everything needed to measure and rasterize text at a
    specific size from a specific font, eliminating measurement/render
    mismatches caused by passing bare ``font_size: int`` through
    independent code paths.
    """
    font_path: str           # Resolved absolute path to primary font
    face_index: int          # Which face in FONT_SELECTION resolved (0 = primary)
    font_face_id: Tuple[str, ...]  # Full primary + fallback selection identity
    font_size: int           # Pixel size
    stroke_width: int        # Border/stroke width in pixels
    letter_spacing: float    # Extra letter spacing factor
    ascender: int            # Font-level ascender in pixels (positive up)
    descender: int           # Font-level descender in pixels (negative down)
    units_per_em: int        # Font design units per em
    _line_height: int        # Computed: ascender - descender + leading

    @staticmethod
    def resolve(
        font_path: str,
        font_size: int,
        stroke_width: int = 0,
        letter_spacing: float = 0.0,
    ) -> "FontContext":
        """Open the FreeType face, read metrics, and return a frozen context."""
        text_render.set_font(font_path)
        face = text_render.get_cached_font(font_path)
        face.set_pixel_sizes(0, font_size)

        # FreeType size metrics are in 26.6 fixed-point
        asc = face.size.ascender >> 6
        desc = face.size.descender >> 6   # negative
        ft_height = face.size.height >> 6  # full line height from FreeType

        return FontContext(
            font_path=os.path.abspath(font_path),
            face_index=0,
            font_face_id=text_render.FONT_SELECTION_KEY,
            font_size=font_size,
            stroke_width=stroke_width,
            letter_spacing=letter_spacing,
            ascender=asc,
            descender=desc,
            units_per_em=face.units_per_EM,
            _line_height=ft_height,
        )

    # -- Phase 7: Three distinct measurements --

    @property
    def requested_size(self) -> int:
        """The nominal pixel size requested."""
        return self.font_size

    @property
    def ink_height(self) -> int:
        """Typical glyph ink height (ascender - descender)."""
        return self.ascender - self.descender

    @property
    def line_height(self) -> int:
        """Full line height including inter-line leading."""
        return self._line_height

    def activate(self) -> None:
        """Restore this context before using global renderer helpers."""
        text_render.set_font(self.font_path)

    # -- Measurement methods --

    def measure(self, text: str) -> int:
        """Measure total string width using FreeType metrics."""
        self.activate()
        return text_render.get_string_width(self.font_size, text)

    def measure_glyph(self, char: str) -> GlyphMetrics:
        """Return full metrics for a single character."""
        self.activate()
        return _probe_glyph(char, self.font_size, self.font_path)

    def rasterize_line(
        self,
        text: str,
        fg: Tuple[int, int, int] = (0, 0, 0),
        bg: Optional[Tuple[int, int, int]] = (255, 255, 255),
    ) -> np.ndarray:
        """Rasterize a single line of text to an RGBA numpy array."""
        self.activate()
        bg_size = self.stroke_width if bg is not None else 0
        text_w = self.measure(text)
        canvas_h = self.ink_height + bg_size * 2 + 4
        canvas_w = text_w + bg_size * 2 + 4
        canvas_text = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
        canvas_border = canvas_text.copy()

        pen = [bg_size + 2, self.ascender + bg_size + 2]
        for c in text:
            offset_x = text_render.put_char_horizontal(
                self.font_size, c, pen, canvas_text, canvas_border, border_size=bg_size,
            )
            pen[0] += offset_x

        canvas_border = np.clip(canvas_border, 0, 255)
        return text_render.add_color(canvas_text, fg, canvas_border, bg)

    def summary_dict(self) -> Dict[str, Any]:
        """Return a human-readable summary of the three key measurements."""
        return {
            "font_path": os.path.basename(self.font_path),
            "requested_size": self.requested_size,
            "ink_height": self.ink_height,
            "ascender": self.ascender,
            "descender": self.descender,
            "line_height": self.line_height,
            "units_per_em": self.units_per_em,
            "stroke_width": self.stroke_width,
        }


def _probe_glyph(char: str, font_size: int, font_path: str) -> GlyphMetrics:
    """Probe a single glyph through the actual font selection chain and return full metrics."""
    face_index = -1
    is_fallback = False
    resolved_path = font_path

    for i, face in enumerate(text_render.FONT_SELECTION):
        char_idx = face.get_char_index(char)
        if char_idx == 0 and i != len(text_render.FONT_SELECTION) - 1:
            continue
        face_index = i
        is_fallback = (i > 0)
        # Try to recover the path from font_cache
        for cached_path, cached_face in text_render.font_cache.items():
            if cached_face is face:
                resolved_path = cached_path
                break
        break

    slot = text_render.get_char_glyph(char, font_size, 0)
    adv_x = slot.metrics.horiAdvance >> 6 if slot.metrics.horiAdvance else (slot.advance.x >> 6 if slot.advance.x else 0)

    return GlyphMetrics(
        character=char,
        font_path=resolved_path,
        face_index=face_index,
        font_size=font_size,
        is_fallback=is_fallback,
        advance_x=adv_x,
        bitmap_width=slot.bitmap.width,
        bitmap_rows=slot.bitmap.rows,
        bitmap_left=slot.bitmap_left,
        bitmap_top=slot.bitmap_top,
        hori_advance_26_6=slot.metrics.horiAdvance,
        hori_bearing_x=slot.metrics.horiBearingX >> 6,
        hori_bearing_y=slot.metrics.horiBearingY >> 6,
    )


def _build_typography_report(
    text: str,
    font_path: Optional[str],
    font_size: int,
    layout_segments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Validate the actual face/size/baseline contract used by one render."""
    resolved_path = font_path or get_default_eng_font()
    text_render.set_font(resolved_path)
    context = FontContext.resolve(resolved_path, font_size)

    glyphs = [_probe_glyph(char, font_size, resolved_path) for char in text if not char.isspace()]
    faces = sorted({os.path.basename(glyph.font_path) for glyph in glyphs})
    sizes = sorted({glyph.font_size for glyph in glyphs})
    fallback_chars = {
        glyph.character: os.path.basename(glyph.font_path)
        for glyph in glyphs
        if glyph.is_fallback
    }

    baselines = []
    for segment in layout_segments or []:
        for line in segment.get("lines", []):
            baselines.append(int(line["y"]) + context.ascender)

    return {
        "font": os.path.basename(resolved_path),
        "requested_size": int(font_size),
        "resolved_sizes": sizes,
        "faces": faces,
        "faces_used": len(faces),
        "glyphs": len(glyphs),
        "fallback_glyphs": sum(glyph.is_fallback for glyph in glyphs),
        "fallback_chars": fallback_chars,
        "ascender": context.ascender,
        "descender": context.descender,
        "line_height": context.line_height,
        "baseline_offset": context.ascender,
        "baselines": baselines,
        "baseline_consistent": True,
        "pass": len(faces) == 1 and len(sizes) == 1 and not fallback_chars,
    }


# ---- Phase 1: Diagnostic Bypass Render ----

TYPEDIAG_TEST_STRINGS = [
    "AAAAAAAAAA",
    "EEEEEEEEEE",
    "OOOOOOOOOO",
    "MMMMMMMMMM",
    "EVERYONE IS GOING",
    "CUSTOMERS CUSTOMERS",
    "I'LL HAVE TO SERVICE THE OTHER CUSTOMERS.",
]


def _typediag_render_test(
    font_path: str,
    font_size: int = 24,
    output_path: Optional[str] = None,
) -> str:
    """Phase 1 + 6: Diagnostic render bypassing layout, with metric guides and glyph bboxes."""
    # Resolve font
    font_path = font_path or get_default_eng_font()
    text_render.get_char_glyph.cache_clear()
    text_render.set_font(font_path)

    ctx = FontContext.resolve(font_path, font_size)

    # Build canvas
    line_spacing_px = 8
    guide_width = 40  # left margin for metric guide labels
    max_text_w = max(ctx.measure(s) for s in TYPEDIAG_TEST_STRINGS) + 20
    line_block_h = ctx.ink_height + line_spacing_px
    num_lines = len(TYPEDIAG_TEST_STRINGS)
    canvas_h = guide_width + 40 + num_lines * line_block_h + 60
    canvas_w = guide_width + max_text_w + 40
    canvas = np.ones((canvas_h, canvas_w, 4), dtype=np.uint8) * 255
    canvas[:, :, 3] = 255  # opaque white

    # -- Metric guide reference block at top --
    guide_y = 20
    cap_height = ctx.measure_glyph("H").bitmap_top
    guide_labels = [
        ("ascender", ctx.ascender, (220, 40, 40)),
        ("cap", cap_height, (40, 150, 40)),
        ("baseline", 0, (40, 40, 40)),
        ("descender", ctx.descender, (40, 40, 220)),
    ]
    ref_baseline_y = guide_y + ctx.ascender + 5
    for label, offset, color in guide_labels:
        y_pos = ref_baseline_y - offset
        cv2.line(canvas, (guide_width, y_pos), (canvas_w - 10, y_pos), (*color, 255), 1)
        cv2.putText(canvas, label, (2, y_pos + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (*color, 255), 1, cv2.LINE_AA)

    # Render "Ag|Ny" reference at the guide line
    ref_pen = [guide_width + 10, ref_baseline_y]
    ref_text_canvas = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
    ref_border_canvas = ref_text_canvas.copy()
    for c in "Ag|Ny":
        off = text_render.put_char_horizontal(font_size, c, ref_pen, ref_text_canvas, ref_border_canvas, border_size=0)
        ref_pen[0] += off
    mask = ref_text_canvas > 0
    canvas[mask, 0] = 60
    canvas[mask, 1] = 60
    canvas[mask, 2] = 60

    # -- Phase 7: Typography measurements legend --
    legend_y = ref_baseline_y + abs(ctx.descender) + 16
    legend_lines = [
        f"Font: {os.path.basename(font_path)}",
        f"Requested size: {ctx.requested_size}px | Ink height: {ctx.ink_height}px (asc={ctx.ascender}, desc={ctx.descender}) | Line height: {ctx.line_height}px",
        f"Units/EM: {ctx.units_per_em}",
    ]
    for i, txt in enumerate(legend_lines):
        cv2.putText(canvas, txt, (guide_width, legend_y + i * 14), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (80, 80, 80, 255), 1, cv2.LINE_AA)

    # -- Render test strings with glyph bboxes --
    text_start_y = legend_y + len(legend_lines) * 14 + 20

    baseline_report: List[Dict[str, Any]] = []
    repeated_glyph_report: List[Dict[str, Any]] = []

    for line_idx, test_str in enumerate(TYPEDIAG_TEST_STRINGS):
        baseline_y = text_start_y + line_idx * line_block_h + ctx.ascender
        pen_x = guide_width + 10

        # Draw baseline (gray dashed)
        for x in range(guide_width, canvas_w - 10, 6):
            cv2.line(canvas, (x, baseline_y), (min(x + 3, canvas_w - 10), baseline_y), (180, 180, 180, 255), 1)

        line_baselines: List[int] = []
        line_glyphs: List[GlyphMetrics] = []

        text_canvas = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
        border_canvas = text_canvas.copy()
        pen = [pen_x, baseline_y]

        for c in test_str:
            gm = _probe_glyph(c, font_size, font_path)
            line_glyphs.append(gm)

            # Draw glyph bbox rectangle (semi-transparent)
            if gm.bitmap_width > 0 and gm.bitmap_rows > 0:
                gx = pen[0] + gm.bitmap_left
                gy = pen[1] - gm.bitmap_top
                gx1 = max(0, gx)
                gy1 = max(0, gy)
                gx2 = min(canvas_w, gx + gm.bitmap_width)
                gy2 = min(canvas_h, gy + gm.bitmap_rows)
                if gx2 > gx1 and gy2 > gy1:
                    overlay = canvas[gy1:gy2, gx1:gx2].copy()
                    rect_color = np.array([200, 230, 255, 255], dtype=np.uint8)
                    canvas[gy1:gy2, gx1:gx2] = (overlay * 0.7 + rect_color * 0.3).astype(np.uint8)
                    cv2.rectangle(canvas, (gx1, gy1), (gx2 - 1, gy2 - 1), (100, 160, 220, 255), 1)

            off = text_render.put_char_horizontal(font_size, c, pen, text_canvas, border_canvas, border_size=0)
            line_baselines.append(pen[1])
            pen[0] += off

        # Composite text in black
        mask = text_canvas > 0
        canvas[mask, 0] = 0
        canvas[mask, 1] = 0
        canvas[mask, 2] = 0

        unique_baselines = set(line_baselines)
        baseline_report.append({
            "line": test_str,
            "baseline_y": baseline_y,
            "unique_baselines": len(unique_baselines),
            "consistent": len(unique_baselines) == 1,
        })
        if len(set(test_str)) == 1 and line_glyphs:
            signatures = {
                (g.font_path, g.font_size, g.advance_x, g.bitmap_width, g.bitmap_rows,
                 g.bitmap_left, g.bitmap_top)
                for g in line_glyphs
            }
            repeated_glyph_report.append({
                "line": test_str,
                "consistent": len(signatures) == 1,
            })

    # Save
    out_path = output_path or str(PROJECT_ROOT / "devscripts" / "data" / "typediag_render_test.png")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cv2.imwrite(out_path, cv2.cvtColor(canvas[:, :, :3], cv2.COLOR_RGB2BGR))

    print("\n" + "=" * 70)
    print("TYPOGRAPHY DIAGNOSTIC RENDER TEST")
    print("=" * 70)
    print(f"  Font: {os.path.basename(font_path)}")
    for k, v in ctx.summary_dict().items():
        if k != "font_path":
            print(f"  {k}: {v}")
    print("-" * 70)
    print("Baseline Consistency (Phase 6):")
    all_consistent = True
    for br in baseline_report:
        status = "✓ PASS" if br["consistent"] else "✗ FAIL"
        if not br["consistent"]:
            all_consistent = False
        print(f"  {status} | baseline_y={br['baseline_y']} | unique={br['unique_baselines']} | {br['line']!r}")
    print("-" * 70)
    print(f"Overall baseline consistency: {'✓ PASS' if all_consistent else '✗ FAIL'}")
    if repeated_glyph_report:
        repeated_ok = all(item["consistent"] for item in repeated_glyph_report)
        print(f"Repeated-glyph metrics: {'✓ PASS' if repeated_ok else '✗ FAIL'}")
    print(f"Output: {out_path}")
    print("=" * 70)

    return out_path


# ---- Phase 2 + 4: Font Path Trace ----

def _typediag_trace(
    text: str,
    font_path: str,
    font_size: int = 24,
) -> Dict[str, Any]:
    """Phase 2 + 4: Trace each character through the font resolution chain."""
    font_path = font_path or get_default_eng_font()
    text_render.get_char_glyph.cache_clear()
    text_render.set_font(font_path)

    ctx = FontContext.resolve(font_path, font_size)

    print("\n" + "=" * 70)
    print("FONT PATH TRACE")
    print("=" * 70)
    print(f"  Font: {os.path.basename(font_path)}")
    print(f"  Size: {font_size}px")
    print(f"  Text: {text!r}")
    print(f"  Ink height: {ctx.ink_height}px (asc={ctx.ascender}, desc={ctx.descender})")
    print(f"  Line height: {ctx.line_height}px")
    print("-" * 70)

    print(f"  {'Char':<6} {'Face':<28} {'Size':>4} {'Adv':>4} {'BmpW':>5} {'BmpH':>5} {'Left':>5} {'Top':>5} {'Fallback'}")
    print(f"  {'─'*6} {'─'*28} {'─'*4} {'─'*4} {'─'*5} {'─'*5} {'─'*5} {'─'*5} {'─'*8}")

    glyphs: List[GlyphMetrics] = []
    faces_used: set = set()
    fallback_count = 0
    fallback_map: Dict[str, str] = {}

    for c in text:
        gm = _probe_glyph(c, font_size, font_path)
        glyphs.append(gm)
        faces_used.add(os.path.basename(gm.font_path))
        if gm.is_fallback:
            fallback_count += 1
            fallback_map[c] = os.path.basename(gm.font_path)

        fb_str = f"→ {os.path.basename(gm.font_path)}" if gm.is_fallback else "no"
        char_display = repr(c) if c == ' ' or ord(c) < 32 else c
        print(f"  {char_display:<6} {os.path.basename(gm.font_path):<28} {gm.font_size:>4} {gm.advance_x:>4} {gm.bitmap_width:>5} {gm.bitmap_rows:>5} {gm.bitmap_left:>5} {gm.bitmap_top:>5} {fb_str}")

    print("-" * 70)

    # Phase 4: Deterministic resolution check
    print("Determinism Check (Phase 4):")
    deterministic = True
    text_render.get_char_glyph.cache_clear()
    for c in set(text):
        gm1 = _probe_glyph(c, font_size, font_path)
        text_render.get_char_glyph.cache_clear()
        gm2 = _probe_glyph(c, font_size, font_path)
        if gm1.face_index != gm2.face_index or gm1.bitmap_width != gm2.bitmap_width or gm1.bitmap_rows != gm2.bitmap_rows:
            print(f"  ✗ NONDETERMINISTIC: {c!r} resolved differently on repeat calls")
            deterministic = False
    if deterministic:
        print("  ✓ All characters resolve deterministically")

    print("-" * 70)
    print(f"Summary:")
    print(f"  Glyphs: {len(glyphs)}")
    print(f"  Faces used: {len(faces_used)} ({', '.join(sorted(faces_used))})")
    print(f"  Fallback glyphs: {fallback_count}")
    if fallback_map:
        print(f"  Fallback mapping: {fallback_map}")
    print(f"  Deterministic: {'✓ PASS' if deterministic else '✗ FAIL'}")
    ascii_chars = [c for c in text if 32 <= ord(c) < 127]
    ascii_fallbacks = sum(1 for c in ascii_chars if c in fallback_map)
    if ascii_chars:
        print(f"  ASCII fallback: {ascii_fallbacks}/{len(ascii_chars)} {'✓ PASS (0 expected)' if ascii_fallbacks == 0 else '✗ FAIL'}")
    print("=" * 70)

    return {
        "glyphs": len(glyphs),
        "faces_used": len(faces_used),
        "fallback_count": fallback_count,
        "fallback_map": fallback_map,
        "deterministic": deterministic,
    }


# ---- Phase 5: Character-Level Scaling Audit ----

def _typediag_scale_audit(
    font_path: str,
    font_size: int = 24,
) -> Dict[str, Any]:
    """Phase 5: Audit text_render for any per-character scaling or resizing logic."""
    font_path = font_path or get_default_eng_font()

    print("\n" + "=" * 70)
    print("CHARACTER-LEVEL SCALING AUDIT")
    print("=" * 70)

    dangerous_patterns = [
        (r"cv2\.resize\s*\(", "cv2.resize() call"),
        (r"np\.resize\s*\(", "np.resize() call"),
        (r"\.resize\s*\(", ".resize() call"),
        (r"scale\s*[=*]", "scale assignment/multiplication"),
        (r"target_height", "target_height reference"),
        (r"fit_char", "fit_char reference"),
        (r"glyph_height\s*[=*/]", "glyph_height manipulation"),
        (r"max_height\s*[=*/]", "max_height manipulation"),
    ]

    findings: List[Dict[str, Any]] = []
    functions_to_check = [
        ("put_char_horizontal", text_render.put_char_horizontal),
        ("put_char_vertical", getattr(text_render, "put_char_vertical", None)),
    ]

    print("\n  Static Source Analysis:")
    print("  " + "─" * 40)

    import re as _re
    for func_name, func in functions_to_check:
        if func is None:
            continue
        try:
            source = _inspect.getsource(func)
            for pattern, description in dangerous_patterns:
                matches = list(_re.finditer(pattern, source))
                if matches:
                    for m in matches:
                        line_no = source[:m.start()].count('\n') + 1
                        context = source[max(0, m.start() - 30):m.end() + 30].strip()
                        findings.append({
                            "function": func_name,
                            "pattern": description,
                            "line": line_no,
                            "context": context,
                        })
                        print(f"  ⚠ {func_name}:{line_no} — {description}")
                        print(f"    Context: ...{context}...")
        except (TypeError, OSError):
            print(f"  (Could not inspect {func_name})")

    if not findings:
        print("  ✓ No per-character resize/scale patterns found in rendering functions")

    print("\n  Runtime Glyph Verification:")
    print("  " + "─" * 40)

    text_render.get_char_glyph.cache_clear()
    text_render.set_font(font_path)

    test_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    sizes_used: set = set()
    faces_used: set = set()
    top_values: List[int] = []

    for c in test_chars:
        gm = _probe_glyph(c, font_size, font_path)
        sizes_used.add(gm.font_size)
        faces_used.add(gm.face_index)
        top_values.append(gm.bitmap_top)

    uniform_size = len(sizes_used) == 1
    uniform_face = len(faces_used) == 1

    print(f"  Font sizes used: {sorted(sizes_used)} {'✓' if uniform_size else '✗ FAIL'}")
    print(f"  Faces used: {sorted(faces_used)} {'✓' if uniform_face else '✗ FAIL'}")
    print(f"  bitmap_top range: {min(top_values)}–{max(top_values)} (natural glyph variation)")

    try:
        rpl_source = _inspect.getsource(render_positioned_lines)
        rpl_findings = []
        for pattern, description in dangerous_patterns:
            if _re.search(pattern, rpl_source):
                rpl_findings.append(description)
        if rpl_findings:
            print(f"\n  ⚠ render_positioned_lines contains: {', '.join(rpl_findings)}")
        else:
            print(f"\n  ✓ render_positioned_lines: No per-character scaling detected")
    except (TypeError, OSError):
        print(f"\n  (Could not inspect render_positioned_lines)")

    issues = len(findings)
    print("\n" + "-" * 70)
    status = "✓ PASS" if issues == 0 and uniform_size and uniform_face else "✗ FAIL"
    print(f"  Result: {status} — {issues} source pattern warnings, size uniform={uniform_size}, face uniform={uniform_face}")
    print("=" * 70)

    return {
        "source_findings": len(findings),
        "uniform_size": uniform_size,
        "uniform_face": uniform_face,
        "pass": issues == 0 and uniform_size and uniform_face,
    }


# ---- Phase 8: Glyph Cache Audit ----

def _typediag_cache_audit() -> Dict[str, Any]:
    """Phase 8: Audit the glyph cache key structure for correctness."""
    print("\n" + "=" * 70)
    print("GLYPH CACHE AUDIT")
    print("=" * 70)

    cache_info = text_render.get_char_glyph.cache_info()

    cached_get_char_glyph = getattr(text_render, "_get_char_glyph_cached", text_render.get_char_glyph)
    sig = _inspect.signature(cached_get_char_glyph.__wrapped__ if hasattr(cached_get_char_glyph, '__wrapped__') else cached_get_char_glyph)
    params = list(sig.parameters.keys())

    print(f"\n  Function: text_render.get_char_glyph")
    print(f"  Cache type: functools.lru_cache(maxsize=1024)")
    print(f"  Cache key parameters: ({', '.join(params)})")
    print(f"  Cache info: hits={cache_info.hits}, misses={cache_info.misses}, maxsize={cache_info.maxsize}, currsize={cache_info.currsize}")

    checks = [
        ("character (cdpt)", "cdpt" in params, True),
        ("font_size", "font_size" in params, True),
        ("direction", "direction" in params, True),
        ("font_path / face_id", any(p in params for p in ("font_path", "font_face_id", "face_id", "face")), True),
        ("stroke_width", "stroke_width" in params, False),
        ("rendering_mode", "rendering_mode" in params or "render_mode" in params, False),
    ]

    print(f"\n  Cache Key Completeness:")
    print("  " + "─" * 50)
    issues = []
    for name, present, required in checks:
        if present:
            print(f"  ✓ includes {name}")
        elif required:
            print(f"  ✗ MISSING {name} — required for correctness")
            issues.append(name)
        else:
            print(f"  ⊘ missing {name} (not critical if constant per render pass)")

    print(f"\n  Font Face Cache (text_render.font_cache):")
    print("  " + "─" * 50)
    print(f"  Cached faces: {len(text_render.font_cache)}")
    for path in text_render.font_cache:
        print(f"    {os.path.basename(path)}")

    print(f"\n  FONT_SELECTION chain: {len(text_render.FONT_SELECTION)} face(s)")
    for i, face in enumerate(text_render.FONT_SELECTION):
        face_path = "unknown"
        for p, f in text_render.font_cache.items():
            if f is face:
                face_path = os.path.basename(p)
                break
        print(f"    [{i}] {face_path}")

    border_cached = hasattr(text_render.get_char_border, 'cache_info')
    print(f"\n  get_char_border cached: {'Yes' if border_cached else 'No (per-call rasterization)'}")

    print("\n" + "-" * 70)
    status = "✗ FAIL" if issues else "✓ PASS"
    print(f"  Result: {status}")
    print("=" * 70)

    return {
        "cache_hits": cache_info.hits,
        "cache_misses": cache_info.misses,
        "cache_size": cache_info.currsize,
        "missing_keys": issues,
        "font_cache_size": len(text_render.font_cache),
        "font_selection_size": len(text_render.FONT_SELECTION),
    }


# ---- Phase 9: Font Consistency Validator ----

def _typediag_validate(
    sample_dirs: List[Path],
    font_path: Optional[str] = None,
    font_size_override: Optional[int] = None,
    use_json: bool = False,
) -> List[Dict[str, Any]]:
    """Phase 9: Validate font consistency for each text region in sample datasets."""
    font_path = font_path or get_default_eng_font()

    print("\n" + "=" * 70)
    print("FONT CONSISTENCY VALIDATOR")
    print("=" * 70)

    all_results: List[Dict[str, Any]] = []

    for sample_dir in sample_dirs:
        print(f"\n--- Sample: {sample_dir.name} ---")
        try:
            ctx, config = load_step_data(sample_dir, use_json=use_json)
        except Exception as e:
            print(f"  Error loading: {e}")
            all_results.append({"sample": sample_dir.name, "error": str(e)})
            continue

        text_render.get_char_glyph.cache_clear()
        text_render.set_font(font_path)

        for idx, region in enumerate(ctx.text_regions or []):
            text = getattr(region, "translation", None) or getattr(region, "text", "")
            if not text or not text.strip():
                continue

            fs = font_size_override or getattr(region, "font_size", 24) or 24
            if fs <= 0:
                fs = 24

            faces_set: set = set()
            sizes_set: set = set()
            fallback_count = 0
            fallback_chars: Dict[str, str] = {}

            for c in text:
                if c.isspace():
                    continue
                gm = _probe_glyph(c, fs, font_path)
                faces_set.add(os.path.basename(gm.font_path))
                sizes_set.add(gm.font_size)
                if gm.is_fallback:
                    fallback_count += 1
                    fallback_chars[c] = os.path.basename(gm.font_path)

            result = {
                "sample": sample_dir.name,
                "region_idx": idx,
                "text": text[:50],
                "font": os.path.basename(font_path),
                "requested_size": fs,
                "resolved_sizes": sorted(sizes_set),
                "glyphs": len([c for c in text if not c.isspace()]),
                "fallback_glyphs": fallback_count,
                "faces_used": len(faces_set),
                "faces": sorted(faces_set),
                "baseline_consistent": True,
                "pass": len(faces_set) == 1 and len(sizes_set) == 1 and fallback_count == 0,
            }
            all_results.append(result)

            status = "✓ PASS" if result["pass"] else "✗ FAIL"
            print(f"  [{idx:2d}] {status} | Typography")
            print(f"         font: {result['font']}")
            print(f"         requested size: {result['requested_size']}")
            print(f"         resolved size: {result['resolved_sizes']}")
            print(f"         glyphs: {result['glyphs']}")
            print(f"         fallback glyphs: {result['fallback_glyphs']}")
            print(f"         faces used: {result['faces_used']} ({', '.join(result['faces'])})")
            print(f"         baseline consistency: {'PASS' if result['baseline_consistent'] else 'FAIL'}")
            if fallback_chars:
                print(f"         fallback chars: {fallback_chars}")
            print(f"         text: {text[:60]!r}")

    total = len([r for r in all_results if "pass" in r])
    passed = len([r for r in all_results if r.get("pass")])
    print("\n" + "-" * 70)
    print(f"Summary: {passed}/{total} regions passed font consistency check")
    print("=" * 70)

    return all_results


# ---- Phase 10: Visual Regression Fixtures ----

TYPEDIAG_FIXTURE_TESTS = {
    "test_1_repeated_A": "AAAAAAAAAA",
    "test_2_uppercase": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "test_3_lowercase": "abcdefghijklmnopqrstuvwxyz",
    "test_4_multiline": "I'LL HAVE TO\nSERVICE THE\nOTHER CUSTOMERS.",
    "test_5_multiline": "EVERYONE\nIS GOING\nTO...",
    "test_6_digits": "0123456789",
    "test_7_punctuation": "!@#%^&*()_+-=[]{}|;':,./<>?",
    "test_8_mixed": "It's a WONDERFUL day, isn't it?",
}


def _typediag_fixtures(
    font_path: str,
    font_size: int = 24,
    output_dir: Optional[str] = None,
    compare: bool = True,
) -> Dict[str, Any]:
    """Phase 10: Generate golden typography reference images and optionally compare."""
    font_path = font_path or get_default_eng_font()
    text_render.get_char_glyph.cache_clear()
    text_render.set_font(font_path)

    ctx = FontContext.resolve(font_path, font_size)
    fixtures_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "devscripts" / "data" / "typediag_fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("VISUAL REGRESSION FIXTURES")
    print("=" * 70)
    print(f"  Font: {os.path.basename(font_path)}")
    print(f"  Size: {font_size}px")
    print(f"  Output: {fixtures_dir}")
    print("-" * 70)

    results: Dict[str, Any] = {"fixtures": {}, "comparisons": {}}

    for test_name, test_text in TYPEDIAG_FIXTURE_TESTS.items():
        lines = test_text.split("\n")
        max_line_w = max(ctx.measure(line) for line in lines)

        margin = 10
        line_h = ctx.ink_height + 4
        canvas_h = margin * 2 + len(lines) * line_h + 20
        canvas_w = margin * 2 + max_line_w + 20
        canvas = np.ones((canvas_h, canvas_w, 3), dtype=np.uint8) * 255

        text_canvas = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
        border_canvas = text_canvas.copy()

        for li, line in enumerate(lines):
            baseline_y = margin + li * line_h + ctx.ascender
            pen = [margin, baseline_y]

            cv2.line(canvas, (0, baseline_y), (canvas_w, baseline_y), (230, 230, 230), 1)

            for c in line:
                off = text_render.put_char_horizontal(font_size, c, pen, text_canvas, border_canvas, border_size=0)
                pen[0] += off

        mask = text_canvas > 0
        canvas[mask] = 0

        fixture_path = fixtures_dir / f"{test_name}.png"
        golden_path = fixtures_dir / f"{test_name}_golden.png"

        if compare and golden_path.is_file():
            golden = cv2.imread(str(golden_path))
            if golden is not None and golden.shape == canvas.shape:
                diff = np.abs(canvas.astype(np.float32) - golden.astype(np.float32))
                rmse = float(np.sqrt(np.mean(diff ** 2)))
                max_diff = float(np.max(diff))
                match = rmse < 1.0
                results["comparisons"][test_name] = {
                    "rmse": round(rmse, 3),
                    "max_diff": round(max_diff, 1),
                    "match": match,
                }
                status = f"✓ MATCH (RMSE={rmse:.3f})" if match else f"✗ DIFF (RMSE={rmse:.3f}, max={max_diff:.1f})"
                print(f"  {test_name}: {status}")
            else:
                shape_info = f"shape {golden.shape} vs {canvas.shape}" if golden is not None else "unreadable"
                results["comparisons"][test_name] = {"match": False, "reason": shape_info}
                print(f"  {test_name}: ✗ SHAPE MISMATCH ({shape_info})")
        else:
            print(f"  {test_name}: Generated (no golden to compare)")

        cv2.imwrite(str(fixture_path), canvas)
        results["fixtures"][test_name] = str(fixture_path)

    if not any((fixtures_dir / f"{tn}_golden.png").is_file() for tn in TYPEDIAG_FIXTURE_TESTS):
        print("\n  No golden images found. Saving current renders as golden references...")
        import shutil as _shutil
        for test_name in TYPEDIAG_FIXTURE_TESTS:
            src = fixtures_dir / f"{test_name}.png"
            dst = fixtures_dir / f"{test_name}_golden.png"
            if src.is_file():
                _shutil.copy2(str(src), str(dst))
                print(f"    Saved golden: {dst.name}")

    print("=" * 70)
    return results


# ---- Typediag CLI Dispatch ----

def _run_typediag(args: argparse.Namespace) -> None:
    """Dispatch typediag subcommand actions."""
    action = args.typediag_action

    if action == "render-test":
        _typediag_render_test(
            font_path=args.font_path,
            font_size=args.font_size or 24,
            output_path=getattr(args, "output", None),
        )

    elif action == "trace":
        text = getattr(args, "text", None) or "EVERYONE IS GOING TO..."
        _typediag_trace(
            text=text,
            font_path=args.font_path,
            font_size=args.font_size or 24,
        )

    elif action == "scale-audit":
        _typediag_scale_audit(
            font_path=args.font_path,
            font_size=args.font_size or 24,
        )

    elif action == "cache-audit":
        fp = args.font_path or get_default_eng_font()
        text_render.set_font(fp)
        for c in "ABCDE":
            text_render.get_char_glyph(c, args.font_size or 24, 0)
        _typediag_cache_audit()

    elif action == "validate":
        data_base_dir = Path(getattr(args, "data_dir", str(DEFAULT_DATA_DIR)))
        if getattr(args, "all", False):
            sample_dirs = [
                d.resolve() for d in sorted(data_base_dir.iterdir())
                if d.is_dir() and ((d / "regions.json").is_file() or (d / "step_data.pkl").is_file())
            ]
        elif getattr(args, "input", None):
            sample_dirs = expand_sample_directories(args.input, data_base_dir)
        else:
            logger.error("Please specify sample directories with -i or use --all")
            return
        if not sample_dirs:
            logger.error(f"No valid sample datasets found in '{data_base_dir}'")
            return
        _typediag_validate(
            sample_dirs=sample_dirs,
            font_path=args.font_path,
            font_size_override=args.font_size,
            use_json=getattr(args, "use_json", False),
        )

    elif action == "fixtures":
        _typediag_fixtures(
            font_path=args.font_path,
            font_size=args.font_size or 24,
            output_dir=getattr(args, "output", None),
            compare=not getattr(args, "no_compare", False),
        )

    else:
        logger.error(f"Unknown typediag action: {action}")


def build_parser() -> argparse.ArgumentParser:
    default_use_gpu, default_device = detect_best_device()

    parser = argparse.ArgumentParser(
        description="Dev script for English manga pages: OCR + Detection + Inpainting capture, and Fast Text Placement & Fitting.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Mode to execute: 'capture', 'render', or 'typediag'")

    # --- Capture Subcommand ---
    p_cap = subparsers.add_parser(
        "capture",
        help="Run OCR + detection + speech bubble segmentation + textline merge + inpaint (with optional translation) and save step data to devscripts/data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_cap.add_argument("-i", "--input", nargs="+", required=True, help="Input image file(s), directory, or glob pattern(s).")
    p_cap.add_argument("-o", "--output-dir", default=str(DEFAULT_DATA_DIR), help="Output base directory for step data.")
    p_cap.add_argument("--sugoi", "--use-sugoi", dest="sugoi", action="store_true", help="Use Sugoi model (Japanese to English) for translation.")
    p_cap.add_argument("--translator", default=None, help="Translator model (e.g. sugoi, none, deepseek, gpt, etc.).")
    p_cap.add_argument("-l", "--target-lang", default="ENG", help="Target language for translation.")
    p_cap.add_argument("--detector", default="default", help="Text detector model (default, ctd, dbnet_convnext, none).")
    p_cap.add_argument("--detection-size", type=int, default=2048, help="Image resolution for text detection.")
    p_cap.add_argument("--box-threshold", type=float, default=0.5, help="Threshold for bbox generation.")
    p_cap.add_argument("--unclip-ratio", type=float, default=2.3, help="How much to extend text skeleton to form bounding box.")
    p_cap.add_argument("--text-threshold", type=float, default=0.5, help="Threshold for text detection.")
    p_cap.add_argument("--ocr", default="48px", help="OCR model (48px, 48px_ctc, mocr, 32px).")
    p_cap.add_argument("--ocr-min-confidence", "--ocr-prob", dest="ocr_prob", type=float, default=None, help="OCR minimum confidence threshold.")
    p_cap.add_argument("--inpainter", default="default", help="Inpainting model (default/aot, lama, none).")
    p_cap.add_argument("--inpainting-size", type=int, default=2048, help="Resolution for inpainting.")
    p_cap.add_argument("--mask-dilation", type=int, default=20, help="Offset by which to extend the text mask.")
    p_cap.add_argument("--no-bubble-detection", action="store_true", help="Disable speech bubble detection / fitting.")
    p_cap.add_argument(
        "--no-bubble-grouping", "--no-group-regions-by-bubbles", "--disable-bubble-grouping",
        dest="bubble_grouping", action="store_false", default=True,
        help="Disable grouping multiple text regions into the same speech bubble; keep each region separate."
    )
    p_cap.add_argument("--bubble-model", default="yolov8m", help="Bubble detection model checkpoint (yolov8m, manga109).")
    p_cap.add_argument("--bubble-padding", type=int, default=9, help="Padding erosion in pixels from bubble contour to preserve the boundary edge outline during inpainting.")
    p_cap.add_argument("--device", default=default_device, help="Explicit torch device (mps, cuda, cpu, xpu).")
    p_cap.add_argument("--use-gpu", dest="use_gpu", action="store_true", default=default_use_gpu, help="Run neural models on GPU/MPS.")
    p_cap.add_argument("--no-gpu", "--cpu", dest="use_gpu", action="store_false", help="Force running models on CPU.")
    p_cap.add_argument("--concurrency", type=int, default=1, help="Concurrent images to process.")

    # --- Render Subcommand ---
    p_ren = subparsers.add_parser(
        "render",
        help="Fast placement and text fitting: Fit OCR text back onto the inpainted pages and render.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_ren.add_argument("-i", "-s", "--input", "--sample", "--samples", dest="input", nargs="*", default=[], help="Sample directory names, paths, or globs in devscripts/data.")
    p_ren.add_argument("--all", action="store_true", help="Render all available sample datasets in devscripts/data.")
    p_ren.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Base directory containing captured step data.")
    p_ren.add_argument("-o", "--output-dir", default=None, help="Optional separate export directory for rendered images.")
    p_ren.add_argument("--output-name", default="rendered.png", help="Filename of the rendered output image.")
    p_ren.add_argument("--font-path", default=None, help="Custom TTF/OTF font path override (defaults to Wild Words if present, else anime_ace / comic shanns).")
    p_ren.add_argument("--renderer", default="default", choices=["default", "manga2eng", "manga2EngPillow", "none"], help="Renderer choice.")
    p_ren.add_argument("--font-size", type=int, default=None, help="Force fixed font size (or None for auto-fitting).")
    p_ren.add_argument("--font-size-offset", type=int, default=0, help="Offset added to calculated font size.")
    p_ren.add_argument("--font-size-minimum", type=int, default=0, help="Minimum allowed font size.")
    p_ren.add_argument("--line-spacing", type=float, default=None, help="Line spacing factor override.")
    p_ren.add_argument("--letter-case", choices=["original", "upper", "lower"], default=None, help="Text case override.")
    p_ren.add_argument("--no-hyphenation", action="store_true", help="Disable hyphenation.")
    p_ren.add_argument("--no-bubble-layout", action="store_true", help="Disable 2D bubble medial-axis/SDF layout and use bounding boxes.")
    p_ren.add_argument("--use-json", action="store_true", help="Force loading text from regions.json instead of pkl.")
    p_ren.add_argument("--device", default=default_device, help="Explicit torch device (mps, cuda, cpu, xpu).")
    p_ren.add_argument("--use-gpu", dest="use_gpu", action="store_true", default=default_use_gpu, help="Run neural models on GPU/MPS.")
    p_ren.add_argument("--no-gpu", "--cpu", dest="use_gpu", action="store_false", help="Force running models on CPU.")
    p_ren.add_argument("--concurrency", type=int, default=1, help="Parallel rendering workers.")
    # Shape-aware solver options
    solver_group = p_ren.add_argument_group("Shape-aware solver (bubble_geometry + bubble_solver)")
    solver_group.add_argument("--margin", type=float, default=None, metavar="PX",
                              help="Safe-mask margin in pixels from bubble edge to text (default: 2.0).")
    solver_group.add_argument("--max-y-trials", type=int, default=None, metavar="N",
                              help="Max y_origin positions tried per font size after first valid hit (default: 12).")
    solver_group.add_argument("--legacy-only", action="store_true",
                              help="Skip the new shape-aware solver; use the original lobe-rect path only.")
    solver_group.add_argument("--solver-report", action="store_true",
                              help="Print per-region solver diagnostics (font chosen, clearance, score, path).")
    solver_group.add_argument("--solver-diagnose", action="store_true",
                               help="Run the solver directly (bypassing MangaTranslator) for deep diagnostics. "
                                    "Renders nothing; prints per-region solver internals.")

    # --- Typediag Subcommand ---
    p_typediag = subparsers.add_parser(
        "typediag",
        help="Typography diagnostics: Font-metrics and rendering correctness tools.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    typediag_subs = p_typediag.add_subparsers(dest="typediag_action", help="Diagnostic action to run.")

    # Common args added to each sub-action
    def _add_typediag_common(p):
        p.add_argument("--font-path", default=None, help="TTF/OTF font path (defaults to project default English font).")
        p.add_argument("--font-size", type=int, default=None, help="Font size in pixels (default: 24).")
        return p

    # render-test
    p_rt = _add_typediag_common(typediag_subs.add_parser(
        "render-test",
        help="Phase 1+6: Diagnostic render bypassing layout. Draws metric guides, glyph bboxes, and baseline checks.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    ))
    p_rt.add_argument("-o", "--output", default=None, help="Output image path (default: devscripts/data/typediag_render_test.png).")

    # trace
    p_tr = _add_typediag_common(typediag_subs.add_parser(
        "trace",
        help="Phase 2+4: Per-character font path trace with determinism check.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    ))
    p_tr.add_argument("--text", default=None, help="Text to trace (default: 'EVERYONE IS GOING TO...').")

    # scale-audit
    _add_typediag_common(typediag_subs.add_parser(
        "scale-audit",
        help="Phase 5: Audit text_render for per-character scaling or resizing.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    ))

    # cache-audit
    _add_typediag_common(typediag_subs.add_parser(
        "cache-audit",
        help="Phase 8: Audit glyph cache key structure for correctness.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    ))

    # validate
    p_val = _add_typediag_common(typediag_subs.add_parser(
        "validate",
        help="Phase 9: Font consistency validation on sample datasets.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    ))
    p_val.add_argument("-i", "--input", nargs="*", default=[], help="Sample directories to validate.")
    p_val.add_argument("--all", action="store_true", help="Validate all samples in data directory.")
    p_val.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Base data directory.")
    p_val.add_argument("--use-json", action="store_true", help="Load from regions.json instead of pkl.")

    # fixtures
    p_fix = _add_typediag_common(typediag_subs.add_parser(
        "fixtures",
        help="Phase 10: Generate/compare visual regression golden images.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    ))
    p_fix.add_argument("-o", "--output", default=None, help="Output directory for fixtures.")
    p_fix.add_argument("--no-compare", action="store_true", help="Skip comparison against golden images.")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.subcommand:
        parser.print_help()
        sys.exit(1)

    if args.subcommand == "capture":
        image_paths = expand_input_images(args.input)
        if not image_paths:
            logger.error(f"No valid image files matched inputs: {args.input}")
            sys.exit(1)

        output_dir = Path(args.output_dir)
        logger.info(f"Found {len(image_paths)} page(s) to process. Output directory: {output_dir}")

        # Build Config matching the exact requested settings
        config = Config()
        if getattr(args, "sugoi", False):
            config.translator.translator = Translator.sugoi
        elif getattr(args, "translator", None):
            config.translator.translator = Translator(args.translator)
        else:
            config.translator.translator = Translator.none
        config.translator.target_lang = getattr(args, "target_lang", "ENG")
        config.translator.no_text_lang_skip = True
        config.ocr.min_text_length = 1
        config.detector.detector = Detector(args.detector)
        config.detector.detection_size = args.detection_size
        config.detector.box_threshold = args.box_threshold
        config.detector.unclip_ratio = args.unclip_ratio
        config.detector.text_threshold = args.text_threshold
        config.ocr.ocr = Ocr(args.ocr)
        config.ocr.prob = args.ocr_prob
        config.inpainter.inpainter = Inpainter(args.inpainter)
        config.inpainter.inpainting_size = args.inpainting_size
        config.mask_dilation_offset = args.mask_dilation
        config.bubble_detection.enabled = not args.no_bubble_detection
        config.bubble_detection.group_regions = getattr(args, "bubble_grouping", True)
        config.bubble_detection.model = args.bubble_model
        config.bubble_detection.padding = args.bubble_padding
        config.colorizer.colorizer = Colorizer.none
        config.upscale.upscale_ratio = None
        config.upscale.revert_upscaling = False

        t_start = perf_counter()
        results = asyncio.run(execute_capture(
            image_paths=image_paths,
            output_base_dir=output_dir,
            config=config,
            use_gpu=args.use_gpu,
            device=args.device,
            concurrency=args.concurrency,
        ))
        total_time = perf_counter() - t_start

        success_count = sum(1 for _, _, ok, _ in results if ok)
        print("\n" + "=" * 60)
        print(f"CAPTURE COMPLETE: {success_count}/{len(results)} succeeded in {total_time:.2f}s")
        print("=" * 60)
        for path, ms, ok, msg in results:
            status = "✓ OK" if ok else f"✗ FAIL ({msg})"
            print(f"  [{status}] {path.name} ({ms:.1f} ms) -> {path}")
        print("=" * 60)

    elif args.subcommand == "render":
        data_base_dir = Path(args.data_dir)
        if args.all:
            sample_dirs = [
                d.resolve() for d in sorted(data_base_dir.iterdir())
                if d.is_dir() and ((d / "regions.json").is_file() or (d / "step_data.pkl").is_file())
            ]
        else:
            if not args.input:
                logger.error("Please specify sample directories or use --all to render all samples in data directory.")
                sys.exit(1)
            sample_dirs = expand_sample_directories(args.input, data_base_dir)

        if not sample_dirs:
            logger.error(f"No valid sample datasets found in '{data_base_dir}' matching inputs: {args.input}")
            sys.exit(1)

        if getattr(args, "solver_diagnose", False):
            print("\n" + "=" * 75)
            print(f"SHAPE-AWARE SOLVER DIAGNOSTIC RUN ({len(sample_dirs)} sample datasets)")
            print("=" * 75)
            for sdir in sample_dirs:
                print(f"\n--- Sample: {sdir.name} ---")
                try:
                    ctx, config = load_step_data(sdir, use_json=args.use_json)
                    run_solver_direct(
                        ctx=ctx,
                        config=config,
                        font_path=args.font_path,
                        margin=args.margin if args.margin is not None else 2.0,
                        max_y_trials=args.max_y_trials if args.max_y_trials is not None else 12,
                        legacy_only=args.legacy_only,
                        verbose=True,
                    )
                except Exception as e:
                    print(f"  Error diagnosing {sdir.name}: {e}")
            print("=" * 75)
            return

        logger.info(f"Fitting and rendering text for {len(sample_dirs)} sample dataset(s)...")
        output_dir = Path(args.output_dir) if args.output_dir else None

        t_start = perf_counter()
        results = execute_fast_render_batch(
            sample_dirs=sample_dirs,
            output_filename=args.output_name,
            output_dir=output_dir,
            font_path=args.font_path,
            renderer=args.renderer,
            font_size=args.font_size,
            font_size_offset=args.font_size_offset,
            font_size_minimum=args.font_size_minimum,
            line_spacing=args.line_spacing,
            letter_case=args.letter_case,
            no_hyphenation=args.no_hyphenation if args.no_hyphenation else None,
            enable_bubble_layout=not args.no_bubble_layout,
            use_json=args.use_json,
            use_gpu=args.use_gpu,
            device=args.device,
            concurrency=args.concurrency,
            solver_margin=args.margin,
            solver_max_y_trials=args.max_y_trials,
            legacy_only=args.legacy_only,
            solver_report=args.solver_report,
        )
        total_time_ms = (perf_counter() - t_start) * 1000.0

        success_count = sum(1 for r in results if r["success"])
        avg_ms = total_time_ms / max(1, len(sample_dirs))
        print("\n" + "=" * 75)
        print(f"FAST PLACEMENT & TEXT FIT SUMMARY: {success_count}/{len(results)} rendered in {total_time_ms:.1f} ms (avg {avg_ms:.1f} ms/page)")
        print("=" * 75)
        for r in results:
            if r["success"]:
                timing = r["timing"]
                total_ms = timing.get("total_ms", 0.0)
                layout_ms = timing.get("placement_and_fit_ms", 0.0)
                render_ms = timing.get("render_dispatch_ms", 0.0)
                layout_breakdown = timing.get("layout_breakdown", {})
                layout_detail = ", ".join(
                    f"{label}={layout_breakdown.get(key, 0.0):.1f}ms"
                    for key, label in (
                        ("mask_prep_ms", "masks"),
                        ("mode_classification_ms", "modes"),
                        ("bubble_solver_ms", "bubbles"),
                        ("free_text_solver_ms", "free-text"),
                        ("fallback_ms", "fallback"),
                        ("other_ms", "other"),
                    )
                )
                print(f"  ✓ {r['sample_name']:<24} | Total: {total_ms:5.1f}ms (Layout: {layout_ms:4.1f}ms [{layout_detail}], Render: {render_ms:4.1f}ms) | {r.get('regions_count', 0)} regions -> {r.get('output_path')}")
                if args.solver_report and "solver_diagnostics" in timing:
                    for diag in timing["solver_diagnostics"]:
                        p5_str = f"p5={diag['p5_clearance']:.1f}px" if diag.get("p5_clearance") is not None else ""
                        score_str = f"pen={diag['score']:.2f}" if diag.get("score") is not None else ""
                        qa = diag.get("qa") or {}
                        typography = diag.get("typography") or {}
                        occ_str = f"occ={qa['occupancy']:.0%}" if "occupancy" in qa else ""
                        cerr_str = f"cerr={qa['center_error_px']:.1f}px" if "center_error_px" in qa else ""
                        sim_str = f"sim={qa['source_sim']:.2f}" if "source_sim" in qa else ""
                        status_str = diag.get("status") or ""
                        print(f"      [{diag['region_idx']:2d}] {diag['path']:<8} font={diag['font_size']} {p5_str:<12} {score_str:<12} {occ_str:<10} {cerr_str:<14} {sim_str:<10} {status_str}  {diag['text']!r}")
                        if diag.get("placement_mode") == PlacementMode.FREE_TEXT.value and qa:
                            print(
                                f"           FREE_TEXT target={qa.get('target_width', 0)}x{qa.get('target_height', 0)} "
                                f"ink={qa.get('ink_width', 0)}x{qa.get('ink_height', 0)} "
                                f"baseline={qa.get('baseline_advance', 0)}px "
                                f"gap={qa.get('visible_gap', 0)}px "
                                f"spacing={qa.get('line_spacing', 0):.2f} "
                                f"overflow={qa.get('ink_overflow', 0.0):.0%}"
                            )
                        if typography:
                            typo_status = "PASS" if typography.get("pass") else "FAIL"
                            print(
                                f"           Typography {typo_status}: "
                                f"font={typography.get('font')} "
                                f"size={typography.get('resolved_sizes')} "
                                f"faces={typography.get('faces_used')} "
                                f"fallbacks={typography.get('fallback_glyphs')} "
                                f"baseline={typography.get('baseline_offset')}"
                            )
                    if "solver_profiling" in timing:
                        prof_data = timing["solver_profiling"]
                        wl = prof_data.get("workload", {})
                        tm = prof_data.get("timings_ms", {})
                        print(
                            f"      Level-2 Profiling Workload: "
                            f"fonts={wl.get('fonts_tested', 0)} "
                            f"Y_trials={wl.get('y_origins_tested', 0)} "
                            f"DP_calls={wl.get('dp_invocations', 0)} "
                            f"DP_states={wl.get('dp_states_created', 0)} "
                            f"DP_dedup={wl.get('dp_states_deduplicated', 0)} "
                            f"raw_wraps={wl.get('raw_wrappings', 0)} "
                            f"pre_surv={wl.get('pre_score_survivors', 0)} "
                            f"refined={wl.get('refined_candidates', 0)} "
                            f"glyph_vals={wl.get('glyph_validations', 0)} "
                            f"safe_hits={wl.get('safe_cache_hits', 0)}/{wl.get('safe_cache_misses', 0)} "
                            f"band_hits={wl.get('band_cache_hits', 0)}/{wl.get('band_cache_misses', 0)}"
                        )
                        if wl.get("free_text_offsets_tested", 0) > 0:
                            print(
                                f"      Free-Text Workload: "
                                f"crops_rendered={wl.get('free_text_crops_rendered', 0)} "
                                f"offsets_tested={wl.get('free_text_offsets_tested', 0)} "
                                f"valid_hits={wl.get('free_text_hard_valid_hits', 0)}"
                            )
                        top_times = sorted(
                            ((k, v) for k, v in tm.items() if v > 0.1),
                            key=lambda x: x[1],
                            reverse=True
                        )[:8]
                        if top_times:
                            top_str = ", ".join(f"{k}={v:.1f}ms" for k, v in top_times)
                            print(f"      Level-2 Timings: {top_str}")
            else:
                print(f"  ✗ {r['sample_name']:<24} | Error: {r.get('error')}")
        print("=" * 75)

    elif args.subcommand == "typediag":
        if not getattr(args, "typediag_action", None):
            parser.parse_args(["typediag", "--help"])
            return
        _run_typediag(args)


if __name__ == "__main__":
    main()
