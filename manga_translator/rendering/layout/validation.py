"""Non-fatal page-layout integrity checks."""

from typing import Any

import numpy as np

from .models import LayoutDiagnostics, PageLayoutResult


def validate_layout(ctx: Any, result: PageLayoutResult) -> LayoutDiagnostics:
    diagnostics = result.diagnostics
    regions = getattr(ctx, "text_regions", []) or []
    for region in regions:
        text = str(getattr(region, "translation", "") or "").strip()
        if not text:
            continue
        region_id = str(getattr(region, "region_id", "") or "")
        if not result.for_region(region_id):
            diagnostics.errors.append(f"missing layout for translated region {region_id}")
    image = getattr(ctx, "img_rgb", None)
    if image is not None:
        height, width = image.shape[:2]
        for layout in result.regions.values():
            for line in layout.lines:
                if line.x < 0 or line.y < 0 or line.x + line.width > width or line.y + line.height > height:
                    diagnostics.errors.append(f"line outside page for region {layout.region_id}")
    diagnostics.metrics["translated_regions"] = sum(
        bool(str(getattr(region, "translation", "") or "").strip()) for region in regions
    )
    diagnostics.metrics["layout_regions"] = len(result.regions)
    return diagnostics
