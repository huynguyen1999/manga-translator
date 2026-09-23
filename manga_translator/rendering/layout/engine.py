"""Production page-layout entry point."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Dict, Optional
from .. import get_default_eng_font
from .models import PageLayoutResult, PlacedLine, RegionLayout
from .obstacles import build_page_obstacle_map, classify_placement_modes
from .regions import prepare_regions
from .validation import validate_layout



def _region_layout(region: Any, font_path: Optional[str]) -> RegionLayout:
    lines = []
    for item in getattr(region, "layout_segments", []) or []:
        for line in item.get("lines", []) or []:
            lines.append(PlacedLine(
                text=str(line.get("text", "")), x=int(line.get("x", 0)), y=int(line.get("y", 0)),
                width=int(line.get("width", 0)), height=int(line.get("height", 0)),
            ))
    mode = getattr(region, "placement_mode", None)
    return RegionLayout(
        region_id=str(region.region_id),
        source_region_ids=list(getattr(region, "source_region_ids", [region.region_id])),
        placement_mode=mode,
        font=font_path,
        source_font_size=int(getattr(region, "source_font_size", 0) or getattr(region, "font_size", 0) or 0),
        calibrated_font_size=int(getattr(region, "calibrated_font_size", 0) or getattr(region, "font_size", 0) or 0),
        font_size=int(getattr(region, "font_size", 0) or 0),
        lines=lines,
        target_geometry=(
            getattr(region, "_free_text_zone", None)
            if getattr(region, "_free_text_zone", None) is not None
            else getattr(region, "_bubble_interior", None)
        ),
        source_geometry=getattr(region, "lines", None),
        solver_path=getattr(region, "_solver_path", None) or "legacy",
        solver_status=getattr(region, "_solver_status", None) or "prepared",
        qa_metrics=dict(getattr(region, "_solver_qa", {}) or {}),
    )


def layout_page(ctx: Any, config: Any, font_path: Optional[str] = None, options: Any = None) -> PageLayoutResult:
    """Run the production shape-aware solver and freeze its result for rendering."""
    started = perf_counter()
    regions = prepare_regions(getattr(ctx, "text_regions", []) or [])
    result = PageLayoutResult()
    if getattr(ctx, "img_rgb", None) is None:
        result.diagnostics.errors.append("layout requires ctx.img_rgb")
        return result

    active_font = font_path or getattr(getattr(config, "render", None), "font_path", None) or get_default_eng_font()
    option_debug = options.get("layout_debug", False) if isinstance(options, dict) else getattr(options, "layout_debug", False)
    layout_debug = bool(option_debug or getattr(ctx, "_layout_debug_enabled", False))
    timing: Dict[str, float] = {}
    from .solver import apply_shape_aware_bubble_layout

    apply_shape_aware_bubble_layout(
        ctx,
        config,
        font_path=active_font,
        infer_bubbles=True,
        timing=timing,
        layout_debug=layout_debug,
        page_geometry=getattr(ctx, "page_geometry", None),
    )

    regions = ctx.text_regions
    prepare_regions(regions)
    classify_placement_modes(regions)
    ctx._layout_obstacles = build_page_obstacle_map(regions, ctx.img_rgb.shape[:2])

    for region in regions:
        result.regions[str(region.region_id)] = _region_layout(region, active_font)
    result.timings.update(timing)
    result.timings["total_ms"] = (perf_counter() - started) * 1000.0
    validate_layout(ctx, result)
    ctx.layout = result
    ctx._bubble_layout_ready = True
    return result
