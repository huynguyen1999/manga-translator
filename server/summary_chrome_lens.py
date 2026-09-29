"""Chrome Lens OCR integration and text-panel merging for manga synopsis."""

from __future__ import annotations

import asyncio
import io
import logging
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

import numpy as np
from PIL import Image

try:
    from chrome_lens_py import LensAPI
except Exception:  # pragma: no cover
    LensAPI = None


def create_lens_api():
    global LensAPI
    if LensAPI is None:
        try:
            import sys
            for mod in [k for k in sys.modules if k.startswith("chrome_lens_py") or k.startswith("google.protobuf")]:
                sys.modules.pop(mod, None)
            from chrome_lens_py import LensAPI as _LensAPI
            LensAPI = _LensAPI
        except Exception as exc:
            logger.warning("Failed to import chrome_lens_py: %s", exc)
            return None
    try:
        return LensAPI()
    except Exception as exc:
        logger.warning("Failed to instantiate LensAPI: %s", exc)
        return None

from manga_translator.detection.bubble import BubbleDetector
from manga_translator.detection.panel import (
    PanelDetection,
    serialize_bubble_detections,
    serialize_panel_detections,
)
from manga_translator.professional_panels import _extract_panel_xyxy
from manga_translator.utils.image_storage import find_asset
from server.image_variants import final_file, source_file

logger = logging.getLogger(__name__)

DEFAULT_LENS_CONCURRENCY = 8


def page_input_file(page: dict[str, Any]) -> Path:
    """Resolve input or source image for a given page record."""
    path = page.get("path")
    if isinstance(path, (str, Path)):
        p = Path(path)
        if p.is_file():
            return p
        inp = find_asset(p, "input")
        if inp is not None and inp.is_file():
            return inp
        fin = final_file(p)
        if fin is not None and fin.is_file():
            return fin
        src = source_file(p)
        if src is not None and src.is_file():
            return src
        if (p / page.get("name", "")).is_file():
            return p / page["name"]
    raise FileNotFoundError("Input image is missing for page")


def lens_geometry_to_pixel(
    geometry: dict[str, Any], img_w: int, img_h: int
) -> tuple[int, int, int, int, list[list[int]], float]:
    """Convert normalized Chrome Lens geometry to pixel coordinates and a quad."""
    cx = float(geometry.get("center_x", 0.5)) * img_w
    cy = float(geometry.get("center_y", 0.5)) * img_h
    w = max(1.0, float(geometry.get("width", 0.0)) * img_w)
    h = max(1.0, float(geometry.get("height", 0.0)) * img_h)
    x1 = int(round(cx - w / 2.0))
    y1 = int(round(cy - h / 2.0))
    x2 = int(round(cx + w / 2.0))
    y2 = int(round(cy + h / 2.0))
    x1 = max(0, min(img_w - 1, x1))
    y1 = max(0, min(img_h - 1, y1))
    x2 = max(x1 + 1, min(img_w, x2))
    y2 = max(y1 + 1, min(img_h, y2))
    width = x2 - x1
    height = y2 - y1
    angle = float(geometry.get("angle_deg", 0.0) or 0.0)
    quad = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    return x1, y1, width, height, quad, angle


def merge_lens_lines_with_bubbles_and_panels(
    lines_data: list[dict[str, Any]],
    bubbles: list[Any],
    panels: list[PanelDetection],
    image_shape: tuple[int, int],
) -> list[dict[str, Any]]:
    """Merge raw Chrome Lens line blocks into speech bubbles and manga panels."""
    img_h, img_w = image_shape[:2]
    parsed_lines: list[dict[str, Any]] = []
    for item in lines_data:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        geom = item.get("geometry") or {}
        x, y, w, h, quad, angle = lens_geometry_to_pixel(geom, img_w, img_h)
        parsed_lines.append({
            "text": text,
            "x": x,
            "y": y,
            "width": w,
            "height": h,
            "center": (x + w / 2.0, y + h / 2.0),
            "quad": quad,
            "angle": angle,
        })

    if not parsed_lines:
        return []

    # 1. Bubble containment grouping
    bubble_groups: dict[int, list[dict[str, Any]]] = {i: [] for i in range(len(bubbles))}
    unbubbled_lines: list[dict[str, Any]] = []

    for line in parsed_lines:
        cx, cy = line["center"]
        assigned_bubble = None
        for b_idx, bd in enumerate(bubbles):
            mask = getattr(bd, "mask", None)
            if mask is not None and 0 <= int(cy) < mask.shape[0] and 0 <= int(cx) < mask.shape[1]:
                if mask[int(cy), int(cx)] > 0:
                    assigned_bubble = b_idx
                    break
        if assigned_bubble is not None:
            bubble_groups[assigned_bubble].append(line)
        else:
            unbubbled_lines.append(line)

    raw_regions: list[dict[str, Any]] = []

    # Process bubble-contained groups
    for b_idx, group in bubble_groups.items():
        if not group:
            continue
        group.sort(key=lambda l: (round(l["y"] / 20.0), l["y"], l["x"]))
        min_x = min(l["x"] for l in group)
        min_y = min(l["y"] for l in group)
        max_x = max(l["x"] + l["width"] for l in group)
        max_y = max(l["y"] + l["height"] for l in group)
        combined_text = "\n".join(l["text"] for l in group)
        all_quads = [l["quad"] for l in group]
        raw_regions.append({
            "x": min_x,
            "y": min_y,
            "width": max_x - min_x,
            "height": max_y - min_y,
            "lines": all_quads,
            "text": combined_text,
            "angle": group[0]["angle"],
            "font_size": max(12, int(round((max_y - min_y) / max(1, len(group)) * 0.8))),
        })

    # Process unbubbled lines (cluster nearby vertical lines if overlapping x-span)
    unbubbled_lines.sort(key=lambda l: (round(l["y"] / 30.0), l["y"], l["x"]))
    used_unbubbled = [False] * len(unbubbled_lines)
    for i, line in enumerate(unbubbled_lines):
        if used_unbubbled[i]:
            continue
        cluster = [line]
        used_unbubbled[i] = True
        for j in range(i + 1, len(unbubbled_lines)):
            if used_unbubbled[j]:
                continue
            cand = unbubbled_lines[j]
            if 0 <= cand["y"] - (cluster[-1]["y"] + cluster[-1]["height"]) <= max(24, cluster[-1]["height"] * 1.5):
                ov_x1 = max(cluster[-1]["x"], cand["x"])
                ov_x2 = min(cluster[-1]["x"] + cluster[-1]["width"], cand["x"] + cand["width"])
                if ov_x2 - ov_x1 > 0 or abs(cand["center"][0] - cluster[-1]["center"][0]) <= 80:
                    cluster.append(cand)
                    used_unbubbled[j] = True

        min_x = min(l["x"] for l in cluster)
        min_y = min(l["y"] for l in cluster)
        max_x = max(l["x"] + l["width"] for l in cluster)
        max_y = max(l["y"] + l["height"] for l in cluster)
        combined_text = "\n".join(l["text"] for l in cluster)
        all_quads = [l["quad"] for l in cluster]
        raw_regions.append({
            "x": min_x,
            "y": min_y,
            "width": max_x - min_x,
            "height": max_y - min_y,
            "lines": all_quads,
            "text": combined_text,
            "angle": cluster[0]["angle"],
            "font_size": max(12, int(round((max_y - min_y) / max(1, len(cluster)) * 0.8))),
        })

    # 2. Assign regions to smallest containing panel
    panel_boxes = [_extract_panel_xyxy(p) for p in panels]
    final_regions: list[dict[str, Any]] = []

    for idx, reg in enumerate(raw_regions):
        rcx = reg["x"] + reg["width"] / 2.0
        rcy = reg["y"] + reg["height"] / 2.0
        assigned_panel_idx = None
        if panel_boxes:
            inside = [
                (p_idx, max(0, box[2] - box[0]) * max(0, box[3] - box[1]))
                for p_idx, box in enumerate(panel_boxes)
                if box is not None and box[0] <= rcx <= box[2] and box[1] <= rcy <= box[3]
            ]
            if inside:
                assigned_panel_idx = min(inside, key=lambda it: it[1])[0]

        final_regions.append({
            "id": f"bubble_{idx}",
            "x": int(reg["x"]),
            "y": int(reg["y"]),
            "width": int(reg["width"]),
            "height": int(reg["height"]),
            "lines": reg["lines"],
            "original_text": reg["text"],
            "translation": reg["text"],
            "font_size": int(reg["font_size"]),
            "font_family": "Comic Neue",
            "fg_color": [0, 0, 0],
            "bg_color": [255, 255, 255],
            "stroke_width": 2,
            "angle": float(reg["angle"]),
            "direction": "h",
            "alignment": "center",
            "line_spacing": 1,
            "letter_spacing": 1,
            "bold": False,
            "italic": False,
            "panel_index": assigned_panel_idx,
        })

    return final_regions


async def extract_page_chrome_lens(
    image_bytes: bytes,
    api: Any,
    bubble_detector: BubbleDetector | None = None,
    max_retries: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Process an image using Chrome Lens and YOLO26 bubble/panel detection."""
    if api is None:
        raise RuntimeError("chrome-lens-py is not available")

    with Image.open(io.BytesIO(image_bytes)) as opened:
        img_rgb = np.array(opened.convert("RGB"))

    img_h, img_w = img_rgb.shape[:2]

    lens_result = None
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            lens_result = await api.process_image(
                image_path=image_bytes,
                output_format="lines",
                source_translation_language="en",
            )
            break
        except Exception as exc:
            last_err = exc
            if attempt < max_retries:
                await asyncio.sleep(1.0 * (2 ** attempt))

    if lens_result is None:
        raise RuntimeError(f"Chrome Lens extraction failed: {last_err}") from last_err

    line_blocks = lens_result.get("line_blocks") or []
    detector = bubble_detector or BubbleDetector("shadowb", device="auto")
    bubbles, panels = detector.detect_joint(img_rgb)

    regions = merge_lens_lines_with_bubbles_and_panels(line_blocks, bubbles, panels, (img_h, img_w))
    p_docs = serialize_panel_detections(panels, (img_h, img_w))
    b_docs = serialize_bubble_detections(bubbles)

    return regions, p_docs, b_docs


async def run_chrome_lens_summary_job(
    job: Any,
    runtime: Any,
    concurrency_limit: int = DEFAULT_LENS_CONCURRENCY,
) -> tuple[int, list[str], dict[str, str]]:
    """Execute Flow 3 (Chrome Lens + YOLO26) for original manga synopsis extraction."""
    pages = job.pages
    store = job.store
    group_value = job.group_value
    clean_title = job.clean_title
    refresh_text = job.refresh_text
    has_group_text = job.has_group_text
    extraction_required = job.extraction_required
    pages_with_text = job.pages_with_text
    pause_event = job.pause_event
    page_count = len(pages)

    _summary_log = runtime.summary_log
    _update_summary_job_for = runtime.update_summary_job_for
    is_page_text_extracted = runtime.is_page_text_extracted
    empty_device_cache = runtime.empty_device_cache

    failed_pages: list[str] = []
    ocr_errors: dict[str, str] = {}

    pending = [
        (idx, page)
        for idx, page in enumerate(pages)
        if refresh_text or not is_page_text_extracted(page, has_group_text=has_group_text)
    ]
    cached_page_count = page_count - len(pending)
    pending_positions = {idx: pos + 1 for pos, (idx, _) in enumerate(pending)}

    if not pending:
        return pages_with_text, failed_pages, ocr_errors

    from server.summary_ocr import persist_summary_ocr

    lens_api = create_lens_api()
    bubble_detector = BubbleDetector("shadowb", device="auto")
    semaphore = asyncio.Semaphore(concurrency_limit)
    completed_count = 0
    lock = asyncio.Lock()

    async def wait_if_paused(page_number: int) -> None:
        if pause_event is not None and not pause_event.is_set():
            _summary_log("paused", clean_title, group_value, page=f"{page_number}/{page_count}")
            await pause_event.wait()
            _summary_log("resumed", clean_title, group_value, page=f"{page_number}/{page_count}")

    async def process_page(index: int, page: dict[str, Any]) -> None:
        nonlocal pages_with_text, completed_count
        current_page = index + 1
        started_at = time.perf_counter()
        await wait_if_paused(current_page)

        async with semaphore:
            await wait_if_paused(current_page)
            try:
                _summary_log(
                    "chrome_lens_started",
                    clean_title,
                    group_value,
                    page=f"{current_page}/{page_count}",
                    file=page["name"],
                )
                input_path = page_input_file(page)
                image_bytes = await asyncio.to_thread(input_path.read_bytes)
                regions, p_docs, b_docs = await extract_page_chrome_lens(
                    image_bytes, lens_api, bubble_detector=bubble_detector
                )
                page["textRegions"] = regions
                page["panel_detections"] = p_docs
                page["bubble_detections"] = b_docs
                await persist_summary_ocr(
                    page,
                    regions,
                    panels=p_docs,
                    bubbles=b_docs,
                    get_store=runtime.get_store,
                )
                async with lock:
                    if regions:
                        pages_with_text += 1
                    completed_count += 1

                _summary_log(
                    "chrome_lens_completed",
                    clean_title,
                    group_value,
                    page=f"{current_page}/{page_count}",
                    file=page["name"],
                    regions=len(regions),
                    panels=len(p_docs),
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 1),
                )
            except Exception as exc:
                _summary_log(
                    "chrome_lens_failed",
                    clean_title,
                    group_value,
                    level=logging.WARNING,
                    exc_info=True,
                    page=f"{current_page}/{page_count}",
                    file=page["name"],
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 1),
                    error=str(exc),
                )
                async with lock:
                    completed_count += 1
                    failed_pages.append(page["name"])
                    ocr_errors[page["name"]] = str(exc)

            passed_count = cached_page_count + completed_count
            progress = round((passed_count / max(1, page_count)) * 70)
            await _update_summary_job_for(
                store,
                group_value,
                clean_title,
                "generating",
                None,
                "detecting",
                progress,
                f"Chrome Lens OCR · {passed_count}/{page_count} pages",
                current_page,
                page_count,
                pages_with_text,
                extraction_required,
                stage_passed_count=passed_count,
            )

    try:
        await asyncio.gather(*(process_page(idx, page) for idx, page in pending))
    finally:
        if lens_api is not None and hasattr(lens_api, "aclose"):
            await lens_api.aclose()
        empty_device_cache()

    return pages_with_text, failed_pages, ocr_errors
