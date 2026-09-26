"""Font-size policy calculations for region layout."""

from __future__ import annotations

from typing import Any, Optional

from ...utils import is_preserved_region
from .models import RegionFontPolicy
from .source_profile import _effective_source_font_size

PAGE_FONT_FLOOR_RATIO = 0.90
PREFERRED_FONT_MAX_DROP_PX = 2
_WEIGHT_PAGE_FONT_OVER = 15.0
_WEIGHT_PAGE_FONT_UNDER = 60.0


def page_font_penalty(font_size: int, page_baseline: Optional[int]) -> float:
    if not page_baseline or page_baseline <= 0:
        return 0.0
    ratio = font_size / float(page_baseline)
    if ratio >= 1.0:
        return ((ratio - 1.0) ** 2) * _WEIGHT_PAGE_FONT_OVER
    return ((1.0 - ratio) ** 2) * _WEIGHT_PAGE_FONT_UNDER


def compression_severity(font_ratio: float) -> str:
    if font_ratio >= 0.90:
        return "satisfactory"
    return "mild" if font_ratio >= 0.85 else "substantial"


def build_region_font_policy(
    region: Any,
    adaptive_target: int,
    page_baseline: Optional[int],
    minimum: int,
    render_config: Any,
) -> RegionFontPolicy:
    source_font = getattr(region, "source_font_size", None)
    if source_font is None:
        source_font = int(getattr(region, "font_size", 0) or 0)
        region.source_font_size = source_font
    calibrated_source = _effective_source_font_size(region, source_font)
    if is_preserved_region(region) and source_font and source_font > 0:
        preferred_size = max(minimum, int(source_font))
        consistency_floor = preferred_size
        mild_compression_floor = preferred_size
        return RegionFontPolicy(
            region_target=preferred_size,
            page_baseline=None,
            preferred_size=preferred_size,
            consistency_floor=consistency_floor,
            mild_compression_floor=mild_compression_floor,
            absolute_minimum=minimum,
            hyphenation_trigger_size=consistency_floor,
            source_font_size=source_font,
        )

    if getattr(render_config, "font_size", None) is not None and render_config.font_size > 0:
        preferred_size = max(minimum, int(render_config.font_size))
        consistency_floor = max(minimum, min((preferred_size * 9 + 9) // 10, preferred_size - PREFERRED_FONT_MAX_DROP_PX))
        mild_compression_floor = max(minimum, int(round(preferred_size * 0.80)))
        return RegionFontPolicy(
            region_target=preferred_size,
            page_baseline=page_baseline,
            preferred_size=preferred_size,
            consistency_floor=consistency_floor,
            mild_compression_floor=mild_compression_floor,
            absolute_minimum=minimum,
            hyphenation_trigger_size=consistency_floor,
            source_font_size=source_font,
        )

    offset = getattr(render_config, "font_size_offset", 0) or 0
    source_target = calibrated_source if calibrated_source > 0 else adaptive_target
    if calibrated_source <= 0 and page_baseline:
        source_target = min(source_target, page_baseline)
    region_target = max(minimum, source_target + offset)
    preferred_size = region_target

    baseline_floor = round(page_baseline * PAGE_FONT_FLOOR_RATIO) if page_baseline and region_target > page_baseline * 1.5 else minimum
    consistency_floor = max(
        minimum,
        min((preferred_size * 9 + 9) // 10, preferred_size - PREFERRED_FONT_MAX_DROP_PX),
        baseline_floor,
    )
    mild_compression_floor = max(
        minimum,
        round(preferred_size * 0.80),
        round(page_baseline * 0.75) if page_baseline else minimum,
    )

    return RegionFontPolicy(
        region_target=region_target,
        page_baseline=page_baseline,
        preferred_size=preferred_size,
        consistency_floor=consistency_floor,
        mild_compression_floor=mild_compression_floor,
        absolute_minimum=minimum,
        hyphenation_trigger_size=consistency_floor,
        source_font_size=source_font,
    )
