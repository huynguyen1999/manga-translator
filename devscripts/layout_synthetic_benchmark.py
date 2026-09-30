"""Benchmark representative synthetic pages through the production layout path."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PAGE_SHAPE = (512, 640)
FONT = ROOT / "fonts" / "anime_ace.ttf"


def _polygon(bounds, angle=0):
    x1, y1, x2, y2 = bounds
    points = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)
    if angle:
        center = points.mean(axis=0)
        radians = np.deg2rad(angle)
        rotation = np.array([[np.cos(radians), -np.sin(radians)], [np.sin(radians), np.cos(radians)]])
        points = (points - center) @ rotation.T + center
    return np.rint(points).astype(np.int32)


def _bubble_mask(shape, kind, center, size):
    mask = np.zeros(shape, dtype=np.uint8)
    cx, cy = center
    rx, ry = size
    if kind == "ellipse":
        cv2.ellipse(mask, center, size, 0, 0, 360, 1, -1)
    elif kind == "irregular":
        points = []
        for index in range(48):
            angle = 2 * np.pi * index / 48
            radius = 1 + 0.10 * np.sin(5 * angle) + 0.05 * np.cos(3 * angle)
            points.append((cx + rx * radius * np.cos(angle), cy + ry * radius * np.sin(angle)))
        cv2.fillPoly(mask, [np.rint(points).astype(np.int32)], 1)
    return mask


def _source_region(region_id, translation, bounds, angle=0):
    from manga_translator.utils import TextBlock

    region = TextBlock(
        lines=[_polygon(bounds, angle)], texts=["元の台詞"], translation=translation,
        font_size=24, target_lang="ENG", source_lang="JPN", direction="h",
        angle=angle, region_id=region_id,
    )
    region.source_font_size = 24
    return region


def _make_case(name):
    from manga_translator.config import Config
    from manga_translator.detection.bubble import BubbleDetection
    from manga_translator.utils import Context

    image = np.full((*PAGE_SHAPE, 3), 255, dtype=np.uint8)
    regions = []
    bubble_detections = []
    if name == "ordinary_bubble":
        mask = _bubble_mask(PAGE_SHAPE, "ellipse", (320, 135), (168, 78))
        regions = [_source_region("ordinary", "A quiet day at last.", (244, 119, 396, 151))]
        bubble_detections = [BubbleDetection(mask, 0.99)]
        masks = [(regions[0], mask, "ordinary-bubble")]
    elif name == "irregular_bubble":
        mask = _bubble_mask(PAGE_SHAPE, "irregular", (320, 135), (172, 82))
        regions = [_source_region("irregular", "We made it through!", (244, 118, 396, 152))]
        bubble_detections = [BubbleDetection(mask, 0.99)]
        masks = [(regions[0], mask, "irregular-bubble")]
    elif name == "connected_bubbles":
        mask = np.zeros(PAGE_SHAPE, dtype=np.uint8)
        cv2.circle(mask, (220, 250), 88, 1, -1)
        cv2.circle(mask, (420, 250), 88, 1, -1)
        cv2.rectangle(mask, (220, 243), (420, 257), 1, -1)
        regions = [
            _source_region("connected-left", "I found the clue.", (145, 232, 295, 268)),
            _source_region("connected-right", "Then let's move!", (345, 232, 495, 268)),
        ]
        bubble_detections = [BubbleDetection(mask, 0.99)]
        masks = [(region, mask, "connected") for region in regions]
    elif name == "rotated_free_text":
        regions = [_source_region("rotated", "The sign points toward the station.", (166, 137, 474, 183), angle=-12)]
        masks = []
    elif name == "long_paragraph":
        paragraph = (
            "The long paragraph case checks how translated prose wraps across a broad free-text area. "
            "It keeps the full sentence sequence together while the layout solver balances readable "
            "lettering, line breaks, and the original placement on the page."
        )
        regions = [_source_region("paragraph", paragraph, (112, 163, 528, 337))]
        masks = []
    elif name == "conflicting_regions":
        bounds = (230, 218, 410, 278)
        regions = [
            _source_region("conflict-first", "Please listen to me.", bounds),
            _source_region("conflict-second", "I was here first!", bounds),
        ]
        masks = []
    else:
        raise ValueError(f"unknown synthetic case: {name}")

    for region, mask, bubble_id in masks:
        region._bubble_mask = mask
        region._bubble_interior = cv2.erode(mask, np.ones((9, 9), np.uint8))
        region.bubble_id = bubble_id
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(image, contours, -1, (25, 25, 25), 3)

    source_ink_masks = {}
    for index, region in enumerate(regions):
        ink = np.zeros(PAGE_SHAPE, dtype=np.uint8)
        x1, y1, x2, y2 = map(int, region.xyxy)
        cv2.putText(
            ink, f"SRC{index + 1}", (x1 + 5, (y1 + y2) // 2 + 6),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1, 2, cv2.LINE_AA,
        )
        polygon = np.zeros(PAGE_SHAPE, dtype=np.uint8)
        cv2.fillPoly(polygon, [np.asarray(region.lines[0], dtype=np.int32)], 1)
        ink &= polygon
        source_ink_masks[region.region_id] = ink.astype(bool)
        image[ink > 0] = 20

    inpainted = image.copy()
    for source_mask in source_ink_masks.values():
        inpainted[source_mask] = 255
    config = Config()
    config.render.font_size_minimum = 8
    config.render.no_hyphenation = True
    config.render.line_spacing = 0
    ctx = Context(
        img_rgb=image, img_inpainted=inpainted, text_regions=regions,
        mask=np.zeros(PAGE_SHAPE, dtype=np.uint8),
        bubble_detections=bubble_detections, panel_detections=[],
        bubble_detection_state="completed_with_results",
        _collect_layout_profile=True, _layout_queue_wait_ms=0.0,
    )
    return ctx, config, source_ink_masks


def _compact(text):
    return "".join(str(text or "").split())


def _checks(ctx, result, source_ink_masks, rendered):
    regions = {str(region.region_id): region for region in ctx.text_regions}
    errors = list(result.diagnostics.errors)
    suppressed = {region_id for region_id, region in regions.items() if getattr(region, "_render_suppressed", False)}
    ownership = result.diagnostics.metrics.get("render_ownership", {})
    expected_sources = {
        source_id for region in regions.values()
        for source_id in (getattr(region, "source_region_ids", []) or [region.region_id])
    }
    owned_once = len(ownership) == len(expected_sources) and set(ownership) == expected_sources
    text_matches = True
    for region_id, region in regions.items():
        if region_id in suppressed:
            continue
        layout = result.for_region(region_id)
        actual = _compact(" ".join(line.text for line in layout.lines))
        if actual != _compact(region.translation):
            text_matches = False

    containment_terms = (
        "outside page", "leaves local domain", "leaves panel", "leaves safe shape", "leaves its",
        "overlaps protected speech bubble", "overlaps restored source text",
    )
    containment_errors = [error for error in errors if any(term in error for term in containment_terms)]
    containment_ok = all(
        any(region_id in error and region_id in suppressed for region_id in regions)
        for error in containment_errors
    )
    collisions = [error for error in errors if "render collision between " in error]
    collision_ok = all(
        any(region_id in error and region_id in suppressed for region_id in regions)
        for error in collisions
    )

    restoration = {}
    active_bubble_ids = {
        getattr(region, "bubble_id", None) for region in regions.values()
        if getattr(region, "bubble_id", None) and str(region.region_id) not in suppressed
    }
    from manga_translator.rendering.layout.failure_policy import should_restore_source
    for region_id in suppressed:
        region = regions[region_id]
        if not should_restore_source(region, active_bubble_ids):
            restoration[region_id] = "not_required_by_source_ownership"
            continue
        ink = source_ink_masks[region_id]
        restoration[region_id] = (
            None if rendered is None else bool(np.array_equal(rendered[ink], ctx.img_rgb[ink]))
        )
    restoration_ok = None if rendered is None else all(
        value is True or value == "not_required_by_source_ownership" for value in restoration.values()
    )
    return {
        "accepted_text_contained": containment_ok,
        "active_layouts_collision_free": collision_ok,
        "text_ownership_complete": owned_once and text_matches,
        "source_restoration": restoration_ok,
        "restored_suppressed_regions": restoration,
        "suppressed_regions": sorted(suppressed),
        "containment_diagnostics": containment_errors,
        "collision_diagnostics": collisions,
        "text_matches_translation": text_matches,
        "ownership": ownership,
    }


def _run(name, phase, index, *, render_check=False):
    from manga_translator.rendering import get_default_eng_font, render_page
    from manga_translator.rendering.layout.engine import layout_page

    ctx, config, ink_masks = _make_case(name)
    font = str(FONT if FONT.is_file() else get_default_eng_font())
    started = perf_counter()
    result = layout_page(ctx, config, font, options={"progressive_search": True})
    caller_ms = (perf_counter() - started) * 1000
    profile = getattr(ctx, "_solver_profile", {}) or {}
    run = {
        "case": name, "phase": phase, "run": index,
        "caller_wall_ms": caller_ms,
        "layout_execution_ms": profile.get("layout_total_ms", result.timings.get("total_ms")),
        "queue_wait_ms": profile.get("queue_wait_ms", 0.0),
        "profile": profile,
        "diagnostics": {
            "errors": list(result.diagnostics.errors),
            "warnings": list(result.diagnostics.warnings),
            "metrics": result.diagnostics.metrics,
        },
    }
    rendered = None
    if render_check:
        render_started = perf_counter()
        rendered = asyncio.run(render_page(ctx, config, font))
        run["render_check_ms"] = (perf_counter() - render_started) * 1000
        run["render_rgb_sha256"] = hashlib.sha256(rendered.tobytes()).hexdigest()
    run["checks"] = _checks(ctx, result, ink_masks, rendered)
    return run


def _summary(runs):
    output = {}
    for name in sorted({run["case"] for run in runs}):
        rows = [run for run in runs if run["case"] == name and run["phase"] == "measured"]
        output[name] = {
            "measured_runs": len(rows),
            **{
                f"median_{field}": round(statistics.median(row[field] for row in rows), 3)
                for field in ("caller_wall_ms", "layout_execution_ms", "queue_wait_ms")
                if rows and all(row.get(field) is not None for row in rows)
            },
            "all_checks_pass": all(all(value for key, value in row["checks"].items()
                                        if key in ("accepted_text_contained", "active_layouts_collision_free",
                                                   "text_ownership_complete", "source_restoration")
                                        and value is not None) for row in rows),
        }
    return output


def benchmark(warmups=1, repeats=3):
    cv2.setNumThreads(1)
    os.environ.setdefault("YOLO_CONFIG_DIR", "/tmp/layout-synthetic-ultralytics")
    cases = (
        "ordinary_bubble", "irregular_bubble", "connected_bubbles",
        "rotated_free_text", "long_paragraph", "conflicting_regions",
    )
    runs = []
    for name in cases:
        for index in range(1, warmups + 1):
            runs.append(_run(name, "warmup", index))
        for index in range(1, repeats + 1):
            runs.append(_run(name, "measured", index, render_check=index == repeats))
    return {
        "benchmark": "synthetic-layout-page",
        "font": str(FONT if FONT.is_file() else "default English font"),
        "page_shape": list(PAGE_SHAPE), "warmups": warmups, "repeats": repeats, "opencv_threads": 1,
        "queue_note": "Direct layout_page calls do not enter the CPU queue; queue wait is reported as zero.",
        "runs": runs, "summary": _summary(runs),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/layout-synthetic-validation.json"))
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    if args.warmups < 0 or args.repeats < 1:
        parser.error("--warmups must be nonnegative and --repeats must be at least one")
    output = benchmark(args.warmups, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    summary = {
        "output": str(args.output), "cases": len(output["summary"]),
        "runs": len(output["runs"]),
        "failed_cases": [name for name, row in output["summary"].items() if not row["all_checks_pass"]],
    }
    print(json.dumps(summary, indent=2))
    return 1 if summary["failed_cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
