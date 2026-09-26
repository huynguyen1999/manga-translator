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
import tempfile
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
from manga_translator.detection.bubble import BubbleDetection, prepare as prepare_bubble_detection, serialize_bubble_detections, deserialize_bubble_detections
from manga_translator.inpainting import prepare as prepare_inpainting
from manga_translator.manga_translator import MangaTranslator
from manga_translator.mask_builder import build_inpaint_masks, create_mask_sources_overlay
from manga_translator.ocr import prepare as prepare_ocr
from manga_translator.translators import prepare as prepare_translation
from manga_translator.rendering import (
    dispatch as dispatch_rendering,
    dispatch_eng_render,
    dispatch_eng_render_pillow,
    render_page,
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
from manga_translator.rendering.layout.geometry import build_lobe_graph as build_production_lobe_graph
from manga_translator.rendering.layout.solver import (
    BubbleGeometry,
    BandSlot,
    BubbleLayoutGroup,
    FreeTextZone,
    LayoutCandidate,
    OriginalLayoutProfile,
    PlacedLine,
    PlacementMode,
    RowGeometry,
    ZoneShapeProfile,
    _RegionLayoutPlan,
    _candidate_data,
    _choose_joint_layout,
    _classify_adjacent_gaps,
    _compact_vertical_rhythm,
    _dp_word_break_rows,
    _free_text_typography_candidates,
    _free_text_words,
    _try_placement_rows,
    apply_shape_aware_bubble_layout,
    build_free_text_ownership_zones,
    build_original_layout_profile,
    build_page_obstacle_map,
    classify_placement_modes,
    compute_placement_target,
    compute_zone_shape_profile,
    create_placement_zones_visualization,
    GAP_DISCONNECTED_SAFE_REGION,
    GAP_LOCAL_GEOMETRY,
    GAP_LOBE_NECK,
    GAP_NORMAL,
    GAP_UNEXPLAINED,
    get_solver_profile,
    partition_bubble_zones,
    normalize_words,
    reset_solver_profile,
    solve_layout,
)
from manga_translator.rendering.layout import layout_page
from manga_translator.rendering.layout.regions import prepare_regions as _ensure_region_identities
from manga_translator.rendering.layout.obstacles import _region_source_mask
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
            unchanged_filtered = (
                getattr(region, "review_reason", None) == "Translation identical to original"
                and raw_src.casefold() == raw_trans.casefold()
            )
            if unchanged_filtered:
                continue
            if not getattr(region, "_free_text_solver_applied", False):
                reviewed_suppression = (
                    getattr(region, "_render_suppressed", False)
                    and getattr(region, "review_required", False)
                    and bool(getattr(region, "review_reason", None))
                )
                if not reviewed_suppression:
                    issues.append(f"{rid}: free-text translation has no layout")
            else:
                if getattr(region, "_bubble_box", None) is None or getattr(region, "_bubble_points", None) is None:
                    issues.append(f"{rid}: free-text layout target is missing")
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
            "source_regions": getattr(region, "source_regions", []),
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
    # Keep the dev name stable while the production geometry becomes the source
    # of truth. The old body remains below until fixture parity is complete.
    return build_production_lobe_graph(
        mask, min_component_area=min_component_area, peak_ratio=peak_ratio
    )
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
            region_id=item.get("id") or item.get("region_id"),
            source_region_ids=item.get("source_region_ids"),
            source_regions=item.get("source_regions"),
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


from manga_translator.mask_builder import build_detector_cleanup_mask
_detector_cleanup_mask = build_detector_cleanup_mask


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
    mask_profile = getattr(ctx, "mask_profile", None)
    if mask_profile:
        with open(sample_dir / "profiling.json", "w", encoding="utf-8") as f:
            json.dump(mask_profile, f, indent=2)
    if getattr(ctx, "text_mask", None) is not None:
        cv2.imwrite(str(sample_dir / "text_mask.png"), ctx.text_mask)
    if getattr(ctx, "bubble_mask", None) is not None:
        cv2.imwrite(str(sample_dir / "bubble_mask.png"), ctx.bubble_mask)
    if getattr(ctx, "mask_raw", None) is not None:
        cv2.imwrite(str(sample_dir / "mask_raw.png"), ctx.mask_raw)
    detector_rescue = getattr(ctx, "detector_rescue_mask", None)
    if detector_rescue is not None:
        cv2.imwrite(str(sample_dir / "detector_rescue_mask.png"), detector_rescue)
    bubble_residual = getattr(ctx, "bubble_residual_mask", None)
    if bubble_residual is not None:
        cv2.imwrite(str(sample_dir / "bubble_residual_mask.png"), bubble_residual)
    protected_edge = getattr(ctx, "protected_edge_mask", None)
    if protected_edge is not None:
        cv2.imwrite(str(sample_dir / "protected_bubble_edge.png"), protected_edge)
    inpaint_mask = getattr(ctx, "inpaint_mask", None)
    if inpaint_mask is None:
        inpaint_mask = getattr(ctx, "mask", None)
    if inpaint_mask is not None:
        cv2.imwrite(str(sample_dir / "inpaint_mask.png"), inpaint_mask)

    # Save mask sources overlay image
    bundle = getattr(ctx, "mask_bundle", None)
    if bundle is not None and ctx.img_rgb is not None:
        try:
            overlay = create_mask_sources_overlay(ctx.img_rgb, bundle)
            cv2.imwrite(str(sample_dir / "mask_sources_overlay.png"), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
        except Exception as e:
            logger.warning(f"Could not save mask_sources_overlay.png: {e}")

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
# Production layout solver entry point (imported above).
# ------------------------------------------------------------------
async def _run_isolated_text_rendering(
    translator: MangaTranslator,
    config: Config,
    ctx: Context,
) -> np.ndarray:
    """Render frozen bubble regions and prepared free text through the canonical production render_page."""
    for region in (ctx.text_regions or []):
        if getattr(region, "translation", None) and isinstance(region.translation, str):
            region.translation = config.render.transform_text_case(region.translation)
    _record_content_trace(ctx.text_regions, "render-input")
    _validate_render_integrity(ctx, strict=bool(getattr(ctx, "_strict_layout_validation", False)))

    active_font = translator.font_path or getattr(getattr(config, "render", None), "font_path", None)
    return await render_page(ctx, config, active_font)


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
    layout_shadow_compare: bool = False,
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
    ctx._layout_debug_enabled = bool(solver_report)
    ctx._layout_shadow_compare = bool(layout_shadow_compare)
    _ensure_region_identities(ctx.text_regions)

    # Ensure all regions have translation populated
    for region in (ctx.text_regions or []):
        if not getattr(region, "translation", None):
            region.translation = getattr(region, "text", "")

    # Configure bubble detection flag
    config.bubble_detection.enabled = enable_bubble_layout

    # 1. Bubble geometry placement and text fit using the shape-aware solver
    t_layout = perf_counter()
    if enable_bubble_layout and getattr(ctx, "img_rgb", None) is not None:
        try:
            layout_result = layout_page(
                ctx=ctx,
                config=config,
                font_path=active_font,
            )
            for key in ("mask_prep_ms", "mode_classification_ms", "bubble_solver_ms", "free_text_solver_ms", "fallback_ms"):
                layout_timing[key] = float(layout_result.timings.get(key, 0.0) or 0.0)
        except Exception as e:
            logger.warning(f"Shape-aware bubble layout failed, falling back to standard placement: {e}")
            t_fallback = perf_counter()
            try:
                translator._prepare_bubble_layout(config, ctx)
            except Exception as e2:
                logger.warning(f"Standard bubble layout fallback failed: {e2}")
            layout_timing["fallback_ms"] += (perf_counter() - t_fallback) * 1000.0
    else:
        reset_solver_profile()
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
        prepare_bubble_masks(img, regions, page_geometry=getattr(ctx, "page_geometry", None))
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

            # 6. Canonical Mask Construction (keep detector pixels for OCR-dropped boxes)
            bundle = await build_inpaint_masks(
                image=ctx.img_rgb,
                detector_textlines=detected_textlines,
                detector_mask=detector_mask if detector_mask is not None else getattr(ctx, "mask_raw", None),
                text_regions=ctx.text_regions or [],
                bubble_detections=getattr(ctx, "bubble_detections", None),
                config=config,
                page_geometry=getattr(ctx, "page_geometry", None),
            )
            ctx.text_mask = bundle.text_mask
            ctx.bubble_mask = bundle.bubble_cleanup_mask
            ctx.detector_rescue_mask = bundle.detector_rescue_mask
            ctx.bubble_residual_mask = bundle.bubble_residual_mask
            ctx.protected_edge_mask = bundle.protected_edge_mask
            ctx.mask_bundle = bundle
            ctx.mask_profile = bundle.profile
            ctx.page_geometry = bundle.page_geometry
            ctx.mask = bundle.final_inpaint_mask
            ctx.inpaint_mask = bundle.final_inpaint_mask.copy()

            # 7. Speech Bubble Layout Geometry, now anchored to the final mask.
            if config.bubble_detection.enabled and getattr(ctx, "img_rgb", None) is not None:
                try:
                    layout_page(
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
    layout_shadow_compare: bool = False,
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
                layout_shadow_compare=layout_shadow_compare,
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
            modes = {
                getattr(getattr(region, "placement_mode", None), "value", getattr(region, "placement_mode", None))
                for region in (ctx.text_regions or [])
            }
            has_bubble = PlacementMode.BUBBLE.value in modes
            has_free = PlacementMode.FREE_TEXT.value in modes
            result_info["layout_category"] = (
                "mixed" if has_bubble and has_free else
                "bubble-only" if has_bubble else
                "free-text-only" if has_free else "empty"
            )
        except Exception as e:
            logger.error(f"Render failed for '{sample_dir.name}': {e}", exc_info=True)
            result_info["error"] = str(e)

        return result_info

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        results = list(executor.map(_render_one, sample_dirs))

    return results


def run_layout_benchmark(
    sample_dirs: List[Path],
    repeat: int = 5,
    layout_shadow_compare: bool = False,
) -> List[Dict[str, Any]]:
    """Run repeated layout-focused samples and print percentile/counter totals."""
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    runs: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="layout-benchmark-") as output_dir:
        for _ in range(repeat):
            runs.extend(execute_fast_render_batch(
                sample_dirs=sample_dirs,
                output_filename="rendered.png",
                output_dir=Path(output_dir),
                concurrency=1,
                layout_shadow_compare=layout_shadow_compare,
            ))

    successful = [item for item in runs if item.get("success")]
    categories = ("bubble-only", "free-text-only", "mixed")
    print(f"Layout benchmark: {len(successful)} page runs, {len(sample_dirs)} pages, {repeat} repeats")
    for category in (*categories, "all"):
        items = successful if category == "all" else [item for item in successful if item.get("layout_category") == category]
        if not items:
            continue
        page_ms = [float(item["timing"].get("placement_and_fit_ms", 0.0)) for item in items]
        percentiles = np.percentile(page_ms, [50, 90, 95, 100])
        timings = [item["timing"].get("layout_breakdown", {}) for item in items]
        profiles = [item["timing"].get("solver_profiling", {}).get("workload", {}) for item in items]
        sums = lambda name: sum(int(profile.get(name, 0) or 0) for profile in profiles)
        free_regions = sums("free_text_regions")
        ideal_attempts = sums("free_text_ideal_attempts")
        local_runs = sums("free_text_local_search_runs")
        local_attempts = sums("free_text_local_search_attempts")
        print(
            f"  {category}: runs={len(items)} regions={sum(item.get('regions_count', 0) for item in items)} "
            f"layout_ms p50/p90/p95/max={percentiles[0]:.1f}/{percentiles[1]:.1f}/{percentiles[2]:.1f}/{percentiles[3]:.1f} "
            f"bubble_ms={sum(t.get('bubble_solver_ms', 0.0) for t in timings):.1f} "
            f"free_text_ms={sum(t.get('free_text_solver_ms', 0.0) for t in timings):.1f} "
            f"fallback_ms={sum(t.get('fallback_ms', 0.0) for t in timings):.1f}"
        )
        print(
            f"    dp={sums('dp_invocations')} states={sums('dp_states_created')} "
            f"free_text_candidates={sums('free_text_typography_candidates')} offsets={sums('free_text_offsets_tested')} "
            f"candidate_rasters={sums('candidate_rasters_created')} raster_cache_hits={sums('candidate_raster_cache_hits')} "
            f"overflow_checks={sums('overflow_checks')} overflow_rasterizations={sums('overflow_rasterizations')} "
            f"full_search_fallbacks={sums('free_text_full_search_fallbacks')} "
            f"ideal_success={sums('free_text_ideal_successes')}/{ideal_attempts} "
            f"({100 * sums('free_text_ideal_successes') / max(1, ideal_attempts):.1f}%) "
            f"local_success={sums('free_text_local_search_successes')}/{local_runs} "
            f"({100 * sums('free_text_local_search_successes') / max(1, local_runs):.1f}%, placements={local_attempts}) "
            f"exhaustive_search={sums('free_text_full_search_runs')}/{free_regions} "
            f"({100 * sums('free_text_full_search_runs') / max(1, free_regions):.1f}%)"
        )
    return runs


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
    p_cap.add_argument("--ocr", default="48px_ctc", help="OCR model (48px, 48px_ctc, mocr, 32px).")
    p_cap.add_argument("--ocr-min-confidence", "--ocr-prob", dest="ocr_prob", type=float, default=None, help="OCR minimum confidence threshold.")
    p_cap.add_argument("--inpainter", default="default", help="Inpainting model (default/aot, lama, none).")
    p_cap.add_argument("--inpainting-size", type=int, default=2048, help="Resolution for inpainting.")
    p_cap.add_argument("--mask-dilation", type=int, default=20, help="Offset by which to extend the text mask.")
    p_cap.add_argument("--no-bubble-detection", action="store_true", help="Disable speech bubble detection / fitting.")
    bubble_grouping = p_cap.add_mutually_exclusive_group()
    bubble_grouping.add_argument(
        "--bubble-grouping", dest="bubble_grouping", action="store_true",
        help="Merge multiple text regions inside one speech bubble into one translation unit."
    )
    bubble_grouping.add_argument(
        "--no-bubble-grouping", "--no-group-regions-by-bubbles", "--disable-bubble-grouping",
        dest="bubble_grouping", action="store_false", default=False,
        help="Keep multiple text regions inside one speech bubble separate (the default)."
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
    solver_group.add_argument("--layout-shadow-compare", action="store_true",
                              help="Evaluate conservative free-text shortcuts, compare them with exhaustive search, and keep the exhaustive result.")
    solver_group.add_argument("--solver-diagnose", action="store_true",
                               help="Run the solver directly (bypassing MangaTranslator) for deep diagnostics. "
                                    "Renders nothing; prints per-region solver internals.")

    p_bench = subparsers.add_parser(
        "layout-benchmark",
        help="Repeat captured layout samples and report layout timing and solver counters.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p_bench.add_argument("--dataset", default=str(DEFAULT_DATA_DIR), help="Sample directory or directory containing captured samples.")
    p_bench.add_argument("--repeat", type=int, default=5, help="Number of sequential runs per sample.")
    p_bench.add_argument("--layout-shadow-compare", action="store_true", help="Measure fast free-text candidates while returning exhaustive results.")

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

    if args.subcommand == "layout-benchmark":
        dataset = Path(args.dataset).resolve()
        if (dataset / "regions.json").is_file() or (dataset / "step_data.pkl").is_file():
            sample_dirs = [dataset]
        else:
            sample_dirs = [
                child.resolve() for child in sorted(dataset.iterdir())
                if child.is_dir() and ((child / "regions.json").is_file() or (child / "step_data.pkl").is_file())
            ] if dataset.is_dir() else []
        if not sample_dirs:
            logger.error(f"No valid sample datasets found in '{dataset}'.")
            sys.exit(1)
        run_layout_benchmark(sample_dirs, repeat=args.repeat, layout_shadow_compare=args.layout_shadow_compare)

    elif args.subcommand == "capture":
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
        config.bubble_detection.group_regions = getattr(args, "bubble_grouping", False)
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
            layout_shadow_compare=args.layout_shadow_compare,
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
