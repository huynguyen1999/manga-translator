"""Rerun presets and prerequisite checks."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from manga_translator.pipeline.stages import PipelineStage, downstream_stages
from manga_translator.utils.image_storage import find_asset
from server.image_variants import final_file


def _frozen_layout_document(ctx, translator, config):
    from manga_translator.detection.bubble import serialize_bubble_detections
    from manga_translator.rendering import get_default_eng_font
    from manga_translator.rendering.layout.frozen import serialize_frozen_layout

    font_path = (
        getattr(translator, "font_path", None)
        or getattr(config.render, "font_path", None)
        or get_default_eng_font()
    )
    return serialize_frozen_layout(
        ctx,
        config,
        font_path,
        serialize_bubble_detections(getattr(ctx, "bubble_detections", None) or []),
    )


class PipelineRerunMode(str, Enum):
    FULL = "full"
    TYPESETTING = "typesetting"
    TRANSLATION_TYPESETTING = "translation_typesetting"
    REPROCESS_TEXT = "reprocess_text"


@dataclass
class PipelineRerunPlan:
    mode: PipelineRerunMode
    use_original_input: bool
    reuse_upscaled_canvas: bool
    run_detection: bool
    run_ocr: bool
    run_textline_merge: bool
    run_bubble_detection: bool
    run_mask_generation: bool
    run_inpainting: bool
    run_translation: bool
    remap_translation: bool
    run_rendering: bool

    @property
    def stages_to_invalidate(self) -> tuple[PipelineStage, ...]:
        start = {
            PipelineRerunMode.FULL: PipelineStage.INPUT,
            PipelineRerunMode.TYPESETTING: PipelineStage.LAYOUT,
            PipelineRerunMode.TRANSLATION_TYPESETTING: PipelineStage.TRANSLATION,
            PipelineRerunMode.REPROCESS_TEXT: PipelineStage.DETECTION,
        }[self.mode]
        return downstream_stages(start)


def resolve_rerun_plan(mode: str | PipelineRerunMode) -> PipelineRerunPlan:
    """Resolve a rerun preset into an internal, validated execution plan."""
    if isinstance(mode, str):
        mode = PipelineRerunMode(mode.lower().strip())

    if mode == PipelineRerunMode.FULL:
        return PipelineRerunPlan(
            mode=mode,
            use_original_input=True,
            reuse_upscaled_canvas=False,
            run_detection=True,
            run_ocr=True,
            run_textline_merge=True,
            run_bubble_detection=True,
            run_mask_generation=True,
            run_inpainting=True,
            run_translation=True,
            remap_translation=False,
            run_rendering=True,
        )

    if mode == PipelineRerunMode.TYPESETTING:
        return PipelineRerunPlan(
            mode=mode,
            use_original_input=False,
            reuse_upscaled_canvas=True,
            run_detection=False,
            run_ocr=False,
            run_textline_merge=False,
            run_bubble_detection=False,
            run_mask_generation=False,
            run_inpainting=False,
            run_translation=False,
            remap_translation=False,
            run_rendering=True,
        )

    if mode == PipelineRerunMode.TRANSLATION_TYPESETTING:
        return PipelineRerunPlan(
            mode=mode,
            use_original_input=False,
            reuse_upscaled_canvas=True,
            run_detection=False,
            run_ocr=False,
            run_textline_merge=False,
            run_bubble_detection=False,
            run_mask_generation=False,
            run_inpainting=False,
            run_translation=True,
            remap_translation=False,
            run_rendering=True,
        )

    if mode == PipelineRerunMode.REPROCESS_TEXT:
        return PipelineRerunPlan(
            mode=mode,
            use_original_input=False,
            reuse_upscaled_canvas=True,
            run_detection=True,
            run_ocr=True,
            run_textline_merge=True,
            run_bubble_detection=False,  # Reused from saved bubble_detections.json if available
            run_mask_generation=True,
            run_inpainting=True,
            run_translation=False,
            remap_translation=True,
            run_rendering=True,
        )

    raise ValueError(f"Unknown pipeline rerun mode: {mode}")


def validate_rerun_prerequisites(
    result_dir: Path,
    mode: str | PipelineRerunMode,
    database: Any = None,
    record: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """Verify that all required upstream artifacts exist before scheduling a rerun."""
    if not result_dir.is_dir():
        return False, f"Result folder {result_dir.name} is missing or inaccessible"

    if isinstance(mode, str):
        try:
            mode = PipelineRerunMode(mode.lower().strip())
        except ValueError:
            return False, f"Invalid rerun mode: {mode}"

    has_text_regions = False
    if database is not None:
        has_text_regions = True
    elif record and (record.get("hasTextRegions") or record.get("hasRegions") or record.get("pageId")):
        has_text_regions = True
    elif (result_dir / "text_regions.json").is_file() or (result_dir / "translations.json").is_file():
        has_text_regions = True

    if mode == PipelineRerunMode.FULL:
        orig = (
            find_asset(result_dir, "input")
            or find_asset(result_dir, "original_canvas")
            or find_asset(result_dir, "inpainted")
            or final_file(result_dir)
        )
        if orig is None:
            return False, "Original input image is unavailable for full pipeline rerun"
        return True, None

    if mode == PipelineRerunMode.TYPESETTING:
        inpainted = (
            find_asset(result_dir, "inpainted")
            or final_file(result_dir)
            or find_asset(result_dir, "input")
        )
        if not inpainted:
            return False, "Saved image canvas is required for typesetting rerun"
        if not has_text_regions:
            return False, "Saved text regions are required for typesetting rerun"
        return True, None

    if mode == PipelineRerunMode.TRANSLATION_TYPESETTING:
        inpainted = (
            find_asset(result_dir, "inpainted")
            or final_file(result_dir)
            or find_asset(result_dir, "input")
        )
        merged = result_dir / "text_regions_merged.json"
        ocr = result_dir / "ocr.json"
        if not inpainted:
            return False, "Saved image canvas is required for translation rerun"
        if not (merged.is_file() or ocr.is_file() or has_text_regions):
            return False, "Saved OCR / textline regions are required for translation rerun"
        return True, None

    if mode == PipelineRerunMode.REPROCESS_TEXT:
        canvas = (
            find_asset(result_dir, "upscaled")
            or find_asset(result_dir, "original_canvas")
            or find_asset(result_dir, "input")
            or find_asset(result_dir, "inpainted")
            or final_file(result_dir)
        )
        if not canvas:
            return False, "Saved canvas image is required to re-detect text and inpaint"
        return True, None

    return False, f"Unsupported rerun mode: {mode}"
