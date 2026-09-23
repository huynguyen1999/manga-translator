"""Canonical pipeline stages and dependency-based invalidation."""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Iterable


class PipelineStage(str, Enum):
    INPUT = "input"
    COLORIZATION = "colorization"
    UPSCALE = "upscale"
    DETECTION = "detection"
    OCR = "ocr"
    BUBBLE_DETECTION = "bubble_detection"
    TEXT_GROUPING = "text_grouping"
    TRANSLATION = "translation"
    MASK_GENERATION = "mask_generation"
    LAYOUT = "layout"
    INPAINTING = "inpainting"
    RENDERING = "rendering"
    FINALIZE = "finalize"


class StageStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"
    INVALIDATED = "invalidated"


class ResourceClass(str, Enum):
    IO = "io"
    GPU = "gpu"
    CPU_HEAVY = "cpu_heavy"
    CPU_LIGHT = "cpu_light"
    NETWORK = "network"


STAGE_ORDER = tuple(PipelineStage)

STAGE_DEPENDENCIES: dict[PipelineStage, frozenset[PipelineStage]] = {
    PipelineStage.INPUT: frozenset(),
    PipelineStage.COLORIZATION: frozenset({PipelineStage.INPUT}),
    PipelineStage.UPSCALE: frozenset({PipelineStage.INPUT, PipelineStage.COLORIZATION}),
    PipelineStage.DETECTION: frozenset({PipelineStage.UPSCALE}),
    PipelineStage.OCR: frozenset({PipelineStage.DETECTION}),
    PipelineStage.BUBBLE_DETECTION: frozenset({PipelineStage.UPSCALE}),
    PipelineStage.TEXT_GROUPING: frozenset(
        {PipelineStage.OCR, PipelineStage.BUBBLE_DETECTION}
    ),
    PipelineStage.TRANSLATION: frozenset({PipelineStage.TEXT_GROUPING}),
    PipelineStage.MASK_GENERATION: frozenset(
        {
            PipelineStage.DETECTION,
            PipelineStage.OCR,
            PipelineStage.BUBBLE_DETECTION,
            PipelineStage.TEXT_GROUPING,
        }
    ),
    PipelineStage.LAYOUT: frozenset(
        {
            PipelineStage.TRANSLATION,
            PipelineStage.MASK_GENERATION,
            PipelineStage.BUBBLE_DETECTION,
            PipelineStage.TEXT_GROUPING,
        }
    ),
    PipelineStage.INPAINTING: frozenset({PipelineStage.MASK_GENERATION}),
    PipelineStage.RENDERING: frozenset(
        {PipelineStage.LAYOUT, PipelineStage.INPAINTING}
    ),
    PipelineStage.FINALIZE: frozenset({PipelineStage.RENDERING}),
}

STAGE_RESOURCES: dict[PipelineStage, ResourceClass] = {
    PipelineStage.INPUT: ResourceClass.IO,
    PipelineStage.COLORIZATION: ResourceClass.GPU,
    PipelineStage.UPSCALE: ResourceClass.GPU,
    PipelineStage.DETECTION: ResourceClass.GPU,
    PipelineStage.OCR: ResourceClass.GPU,
    PipelineStage.BUBBLE_DETECTION: ResourceClass.GPU,
    PipelineStage.TEXT_GROUPING: ResourceClass.CPU_LIGHT,
    PipelineStage.TRANSLATION: ResourceClass.NETWORK,
    PipelineStage.MASK_GENERATION: ResourceClass.CPU_HEAVY,
    PipelineStage.LAYOUT: ResourceClass.CPU_HEAVY,
    PipelineStage.INPAINTING: ResourceClass.GPU,
    PipelineStage.RENDERING: ResourceClass.CPU_LIGHT,
    PipelineStage.FINALIZE: ResourceClass.IO,
}

_SETTING_STAGE = {
    "source": PipelineStage.INPUT,
    "input": PipelineStage.INPUT,
    "colorizer": PipelineStage.COLORIZATION,
    "colorization": PipelineStage.COLORIZATION,
    "colorization_size": PipelineStage.COLORIZATION,
    "denoise_sigma": PipelineStage.COLORIZATION,
    "color_threshold": PipelineStage.COLORIZATION,
    "color_tolerance": PipelineStage.COLORIZATION,
    "restore_size": PipelineStage.COLORIZATION,
    "upscale": PipelineStage.UPSCALE,
    "upscaler": PipelineStage.UPSCALE,
    "upscale_ratio": PipelineStage.UPSCALE,
    "detection": PipelineStage.DETECTION,
    "detector": PipelineStage.DETECTION,
    "text_detector": PipelineStage.DETECTION,
    "detection_size": PipelineStage.DETECTION,
    "detection_resolution": PipelineStage.DETECTION,
    "box_threshold": PipelineStage.DETECTION,
    "custom_box_threshold": PipelineStage.DETECTION,
    "unclip_ratio": PipelineStage.DETECTION,
    "custom_unclip_ratio": PipelineStage.DETECTION,
    "ocr": PipelineStage.OCR,
    "prob": PipelineStage.OCR,
    "ocr_min_confidence": PipelineStage.OCR,
    "custom_ocr_prob": PipelineStage.OCR,
    "min_text_length": PipelineStage.OCR,
    "use_mocr_merge": PipelineStage.OCR,
    "bubble_detection": PipelineStage.BUBBLE_DETECTION,
    "bubble_model": PipelineStage.BUBBLE_DETECTION,
    "bubble_confidence": PipelineStage.BUBBLE_DETECTION,
    "bubble_mask_threshold": PipelineStage.BUBBLE_DETECTION,
    "bubble_padding": PipelineStage.BUBBLE_DETECTION,
    "bubble_grouping": PipelineStage.TEXT_GROUPING,
    "bubble_group_regions": PipelineStage.TEXT_GROUPING,
    "group_regions": PipelineStage.TEXT_GROUPING,
    "translator": PipelineStage.TRANSLATION,
    "translation": PipelineStage.TRANSLATION,
    "target_language": PipelineStage.TRANSLATION,
    "translation_quality": PipelineStage.TRANSLATION,
    "translation_batch_size": PipelineStage.TRANSLATION,
    "story_page_ranges": PipelineStage.TRANSLATION,
    "story_plan": PipelineStage.TRANSLATION,
    "mask": PipelineStage.MASK_GENERATION,
    "mask_dilation_offset": PipelineStage.MASK_GENERATION,
    "layout": PipelineStage.LAYOUT,
    "font": PipelineStage.LAYOUT,
    "font_path": PipelineStage.LAYOUT,
    "gimp_font": PipelineStage.LAYOUT,
    "font_size": PipelineStage.LAYOUT,
    "font_size_offset": PipelineStage.LAYOUT,
    "font_size_minimum": PipelineStage.LAYOUT,
    "font_color": PipelineStage.LAYOUT,
    "line_spacing": PipelineStage.LAYOUT,
    "no_hyphenation": PipelineStage.LAYOUT,
    "alignment": PipelineStage.LAYOUT,
    "direction": PipelineStage.LAYOUT,
    "render": PipelineStage.LAYOUT,
    "renderer": PipelineStage.LAYOUT,
    "render_font": PipelineStage.LAYOUT,
    "render_alignment": PipelineStage.LAYOUT,
    "render_text_direction": PipelineStage.LAYOUT,
    "custom_font_size": PipelineStage.LAYOUT,
    "inpainting": PipelineStage.INPAINTING,
    "inpainter": PipelineStage.INPAINTING,
    "inpainting_size": PipelineStage.INPAINTING,
}

_PROGRESS_STAGE = {
    "input": PipelineStage.INPUT,
    "colorizing": PipelineStage.COLORIZATION,
    "upscaling": PipelineStage.UPSCALE,
    "detection": PipelineStage.DETECTION,
    "ocr": PipelineStage.OCR,
    "bubble-detection": PipelineStage.BUBBLE_DETECTION,
    "bubble_detection": PipelineStage.BUBBLE_DETECTION,
    "textline_merge": PipelineStage.TEXT_GROUPING,
    "text-grouping": PipelineStage.TEXT_GROUPING,
    "translating": PipelineStage.TRANSLATION,
    "translation_remap": PipelineStage.TRANSLATION,
    "analyzing-story": PipelineStage.TRANSLATION,
    "mask-generation": PipelineStage.MASK_GENERATION,
    "mask_generation": PipelineStage.MASK_GENERATION,
    "inpainting": PipelineStage.INPAINTING,
    "layout": PipelineStage.LAYOUT,
    "rendering": PipelineStage.RENDERING,
    "saving": PipelineStage.FINALIZE,
    "downscaling": PipelineStage.FINALIZE,
    "finalize": PipelineStage.FINALIZE,
    "finished": PipelineStage.FINALIZE,
}


def _stage(stage: str | PipelineStage) -> PipelineStage:
    return stage if isinstance(stage, PipelineStage) else PipelineStage(stage)


def stage_from_progress(state: str) -> PipelineStage | None:
    """Map existing translator progress messages to the canonical stage IDs."""
    value = str(state).lower()
    if value.startswith(("drafting:", "editing:")):
        return PipelineStage.TRANSLATION
    return _PROGRESS_STAGE.get(value)


def downstream_stages(
    stage: str | PipelineStage, *, include_self: bool = True
) -> tuple[PipelineStage, ...]:
    """Return a stage and all dependents in execution order."""
    source = _stage(stage)
    affected = {source}
    changed = True
    while changed:
        changed = False
        for candidate in STAGE_ORDER:
            if candidate not in affected and STAGE_DEPENDENCIES[candidate] & affected:
                affected.add(candidate)
                changed = True
    if not include_self:
        affected.remove(source)
    return tuple(candidate for candidate in STAGE_ORDER if candidate in affected)


def stages_affected_by_settings(keys: Iterable[str]) -> tuple[PipelineStage, ...]:
    """Return the ordered invalidation closure for changed setting names."""
    roots = {
        stage
        for key in keys
        if (
            stage := _SETTING_STAGE.get(
                re.sub(r"(?<!^)(?=[A-Z])", "_", str(key).rsplit(".", 1)[-1]).lower()
            )
        ) is not None
    }
    affected = {
        dependent
        for stage in roots
        for dependent in downstream_stages(stage)
    }
    return tuple(stage for stage in STAGE_ORDER if stage in affected)


def settings_for_stage(settings: dict[str, Any], stage: str | PipelineStage) -> dict[str, Any]:
    """Pick recognized settings that can change one stage's output."""
    target = _stage(stage)

    def normalize(key: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()

    def collect(values: dict[str, Any]) -> dict[str, Any]:
        selected = {}
        for key, value in values.items():
            mapped = _SETTING_STAGE.get(normalize(str(key)))
            if isinstance(value, dict):
                if mapped is target:
                    nested = {
                        child: child_value
                        for child, child_value in value.items()
                        if _SETTING_STAGE.get(normalize(str(child))) in (None, target)
                    }
                    selected[str(key)] = nested
                else:
                    nested = collect(value)
                    if nested:
                        selected[str(key)] = nested
            elif mapped is target:
                selected[str(key)] = value
        return selected

    return collect(settings)


def fingerprint(value: Any) -> str:
    """Stable SHA-256 for JSON-compatible stage inputs and settings."""
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
