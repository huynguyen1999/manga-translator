"""Non-fatal page-layout integrity checks."""

from typing import Any, List, Tuple

import numpy as np

from ...utils import is_preserved_region, resolve_render_content
from .geometry import BubbleGeometry
from .models import LayoutDiagnostics, PageLayoutResult, PlacedLine
from .render_output_validation import (
    _region_render_boxes,
    _warped_alpha_crop,
    validate_render_output,
)
from .raster import _render_line_alpha


def validate_layout(ctx: Any, result: PageLayoutResult) -> LayoutDiagnostics:
    diagnostics = result.diagnostics
    regions = getattr(ctx, "text_regions", []) or []
    ownership = {}
    region_metrics = {}
    regions_by_id = {str(getattr(region, "region_id", "") or ""): region for region in regions}

    def suppress(region_id: str, reason: str) -> None:
        region = regions_by_id.get(region_id)
        if region is not None:
            region._render_suppressed = True
            region.review_required = True
            region.review_reason = getattr(region, "review_reason", None) or reason

    for region in regions:
        text = resolve_render_content(region).strip()
        if not text:
            if not is_preserved_region(region):
                diagnostics.warnings.append(f"incomplete translation for region {getattr(region, 'region_id', '')}")
            continue
        region_id = str(getattr(region, "region_id", "") or "")
        layout = result.for_region(region_id)
        has_render_data = bool(
            getattr(region, "layout_segments", None) or getattr(region, "_bubble_segments", None)
            or getattr(region, "_bubble_box", None) is not None
        )
        if not layout or (not layout.lines and not has_render_data and not getattr(region, "_render_suppressed", False)):
            diagnostics.errors.append(f"missing layout for translated region {region_id}")
        source_ids = list(getattr(region, "source_region_ids", []) or [region_id])
        duplicates = [source_id for source_id in source_ids if source_id in ownership]
        if duplicates:
            diagnostics.errors.append(f"duplicate source owner for {', '.join(duplicates)}")
        else:
            ownership.update({source_id: region_id for source_id in source_ids})

        lines = [line.text for line in (layout.lines if layout else [])]
        final_text = " ".join(lines).strip()
        source_snapshot = getattr(region, "source_text_snapshot", None)
        source_text = str(source_snapshot if source_snapshot is not None else (getattr(region, "text", "") or ""))
        source_font = int(getattr(region, "source_font_size", 0) or 0)
        final_font = int(getattr(region, "font_size", 0) or 0)
        preserved_exact = not is_preserved_region(region) or final_text == source_text
        source_leak = (
            not is_preserved_region(region)
            and str(getattr(region, "target_lang", "")).upper().startswith("EN")
            and bool(source_text.strip()) and final_text == source_text.strip()
        )
        if not preserved_exact:
            diagnostics.errors.append(f"preserved content changed for region {region_id}")
        if source_leak:
            diagnostics.errors.append(f"source text leak for region {region_id}")

        font_policy = (
            getattr(region, "_font_policy_diagnostics", {})
            or getattr(region, "_solver_qa", {})
            or {}
        )
        explicit_font = bool(font_policy.get("explicit_font_size"))
        readability_target = int(
            getattr(region, "calibrated_font_size", 0)
            or (getattr(layout, "calibrated_font_size", 0) if layout else 0)
            or source_font
        )
        segment_sizes = [
            int(segment.get("font_size", 0) or 0)
            for segment in (getattr(region, "layout_segments", []) or [])
        ]
        segment_sizes = [size for size in segment_sizes if size > 0]
        if not segment_sizes and final_font > 0:
            segment_sizes = [final_font]
        readability_ratio = min(segment_sizes) / readability_target if segment_sizes and readability_target else None
        readability_review = bool(
            not is_preserved_region(region) and not explicit_font
            and readability_ratio is not None and readability_ratio < 0.85
        )
        if readability_review:
            region.review_required = True
            region.review_reason = getattr(region, "review_reason", None) or "font_below_readability_floor"
            region._render_suppressed = True
        region_metrics[region_id] = {
            "font_ratio_to_source": final_font / source_font if source_font else None,
            "font_ratio_to_calibrated": readability_ratio,
            "font_drop_px": source_font - final_font if source_font else None,
            "readability_review_required": readability_review,
            "hyphen_count": max(0, final_text.count("-") - text.count("-")),
            "preserved_content_exact": preserved_exact,
            "duplicate_source_owner": bool(duplicates),
            "source_text_leak": source_leak,
            "cleanup_mask_coverage": float(getattr(region, "_solver_qa", {}).get("cleanup_mask_coverage", 0.0)),
            "bubble_overlap_pixels": 0,
        }

    validate_render_output(ctx, result, diagnostics, regions, regions_by_id, region_metrics, suppress)

    diagnostics.metrics["translated_regions"] = sum(
        bool(str(getattr(region, "translation", "") or "").strip()) for region in regions
    )
    diagnostics.metrics["layout_regions"] = len(result.regions)
    diagnostics.metrics["render_ownership"] = ownership
    diagnostics.metrics["regions"] = region_metrics
    return diagnostics


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
