"""Pipeline rerun execution plan, prerequisite validation, checkpoint hydration, and atomic commitment."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from manga_translator.config import Config
from manga_translator.mask_builder import build_inpaint_masks
from manga_translator.pipeline.cpu import CPU_PRIORITY_NORMAL, run_cpu_stage
from manga_translator.pipeline.translation_remap import remap_translations, TranslationRemapResult
from manga_translator.pipeline.stages import PipelineStage, downstream_stages
from manga_translator.pipeline.run import (
    deserialize_textblocks,
    deserialize_textlines,
    serialize_editor_regions,
    serialize_regions,
)
from manga_translator.utils import Context, dump_image, load_image
from manga_translator.utils.image_storage import find_asset, save_jpeg
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


def _copy_file_atomic(source: Path, target: Path) -> None:
    """Replace one result file only after its complete staged copy is ready."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        if source.stat().st_size == 0:
            raise ValueError(f"Staged artifact is empty: {source.name}")
        shutil.copy2(source, temporary)
        if temporary.stat().st_size != source.stat().st_size:
            raise OSError(f"Staged artifact copy was incomplete: {source.name}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


_RERUN_ARTIFACTS = {
    "final.jpg": ("rendering", "final_image"),
    "final.png": ("rendering", "final_image"),
    "inpainted.jpg": ("inpainting", "image"),
    "inpainted.png": ("inpainting", "image"),
    "mask_raw.png": ("detection", "mask"),
    "text_mask.png": ("mask_generation", "text_mask"),
    "bubble_mask.png": ("mask_generation", "bubble_mask"),
    "mask_final.png": ("mask_generation", "inpaint_mask"),
    "inpaint_mask.png": ("mask_generation", "inpaint_mask"),
}


def _prepare_versioned_artifact(
    source: Path, result_root: Path, result_dir: Path, stage: str, artifact_type: str
) -> tuple[dict[str, Any], Path]:
    destination = result_dir / "pipeline_artifacts" / stage / f"{artifact_type}-{uuid.uuid4().hex}{source.suffix.lower()}"
    _copy_file_atomic(source, destination)
    try:
        with Image.open(destination) as image:
            width, height = image.size
            image.verify()
        digest = hashlib.sha256()
        with destination.open("rb") as artifact_file:
            for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                digest.update(chunk)
        checksum = digest.hexdigest()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    relative_path = destination.resolve().relative_to(result_root.resolve())
    mime_type = "image/jpeg" if destination.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return ({
        "stage": stage,
        "artifact_type": artifact_type,
        "relative_path": relative_path.as_posix(),
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "size_bytes": destination.stat().st_size,
        "checksum": checksum,
    }, destination)


async def load_rerun_context(
    result_dir: Path,
    plan: PipelineRerunPlan,
    config: Config,
    database: Any = None,
    record_id: Optional[str] = None,
) -> Tuple[Context, Dict[str, Any]]:
    """Hydrate a Context object from persisted checkpoint artifacts according to the plan."""
    ctx = Context()
    folder = result_dir.name
    ctx.debug_folder = folder
    ctx.image_context = {
        "subfolder": folder,
        "file_md5": folder.split("-")[-1] if "-" in folder else folder,
    }
    ctx.result_documents = {}

    # Load documents from database if available, else from files
    documents: Dict[str, Any] = {}
    if database is not None:
        try:
            documents = await database.get_documents(folder) or {}
        except Exception:
            documents = {}
        try:
            target_id = record_id or folder
            pg_regions = await database.get_text_regions(target_id)
            if not pg_regions and record_id and record_id != folder:
                pg_regions = await database.get_text_regions(folder)
            if pg_regions:
                if "text_regions.json" not in documents:
                    documents["text_regions.json"] = pg_regions
                if "translations.json" not in documents:
                    documents["translations.json"] = pg_regions
        except Exception:
            pass

    def _read_json(name: str) -> Optional[Any]:
        if name in documents:
            return documents[name]
        p = result_dir / name
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    # 1. Canvas / Image loading
    if plan.use_original_input:
        orig_path = (
            find_asset(result_dir, "input")
            or find_asset(result_dir, "original_canvas")
            or find_asset(result_dir, "inpainted")
            or final_file(result_dir)
        )
        if orig_path is None:
            raise RuntimeError("Original input image not found")
        with Image.open(orig_path) as img:
            ctx.input = img.convert("RGB")
            ctx.img_rgb, ctx.img_alpha = load_image(ctx.input)
    else:
        # Reprocess / typesetting / translation rerun
        upscaled_path = find_asset(result_dir, "upscaled") if plan.reuse_upscaled_canvas else None
        orig_path = (
            upscaled_path
            or find_asset(result_dir, "original_canvas")
            or find_asset(result_dir, "input")
            or find_asset(result_dir, "inpainted")
        )
        if orig_path is None:
            raise RuntimeError("Base canvas image not found")
        with Image.open(orig_path) as img:
            ctx.input = img.convert("RGB")
            ctx.img_rgb, ctx.img_alpha = load_image(ctx.input)

    # 2. Inpainted canvas & masks if skipping inpainting
    if not plan.run_inpainting:
        inpainted_path = find_asset(result_dir, "inpainted")
        if inpainted_path:
            with Image.open(inpainted_path) as img:
                ctx.img_inpainted = np.array(img.convert("RGB"))

        inpaint_mask_path = result_dir / "inpaint_mask.png"
        mask_final_path = result_dir / "mask_final.png"
        if inpaint_mask_path.is_file():
            ctx.inpaint_mask = cv2.imread(str(inpaint_mask_path), cv2.IMREAD_GRAYSCALE)
        elif mask_final_path.is_file():
            ctx.inpaint_mask = cv2.imread(str(mask_final_path), cv2.IMREAD_GRAYSCALE)
        ctx.mask = ctx.inpaint_mask

    # 3. Speech bubble detections
    bubble_dets_raw = _read_json("bubble_detections.json")
    if bubble_dets_raw:
        try:
            from manga_translator.detection.bubble import deserialize_bubble_detections
            ctx.bubble_detections = deserialize_bubble_detections(bubble_dets_raw, ctx.img_rgb.shape)
            ctx._bubble_detection_done = True
        except Exception:
            pass

    bubble_mask_path = result_dir / "bubble_mask.png"
    if bubble_mask_path.is_file():
        ctx.bubble_mask = cv2.imread(str(bubble_mask_path), cv2.IMREAD_GRAYSCALE)

    # 4. Text regions / OCR hydration
    old_translations_doc = (
        _read_json("translations.json")
        or _read_json("text_regions.json")
        or _read_json("text_regions_merged.json")
    )
    old_regions = deserialize_textblocks(old_translations_doc or [])

    if plan.mode == PipelineRerunMode.TYPESETTING:
        saved_regions = _read_json("text_regions.json") or old_translations_doc
        if not saved_regions:
            raise RuntimeError("No saved text regions available for typesetting")
        ctx.text_regions = deserialize_textblocks(saved_regions)
        for region in ctx.text_regions:
            if not getattr(region, "target_lang", None):
                region.target_lang = config.translator.target_lang

    elif plan.mode == PipelineRerunMode.TRANSLATION_TYPESETTING:
        # Load merged regions or OCR regions as translatable units
        merged_doc = (
            _read_json("text_regions_merged.json")
            or _read_json("ocr.json")
            or _read_json("text_regions.json")
        )
        if not merged_doc:
            raise RuntimeError("No saved OCR / merged regions available for translation")
        ctx.text_regions = deserialize_textblocks(merged_doc)
        # Clear existing translations to force translation pass
        for region in ctx.text_regions:
            region.translation = ""
            if not getattr(region, "target_lang", None):
                region.target_lang = config.translator.target_lang

    return ctx, {"old_regions": old_regions, "documents": documents}


async def execute_rerun_plan(
    translator: Any,
    ctx: Context,
    config: Config,
    plan: PipelineRerunPlan,
    state: Dict[str, Any],
    staging_dir: Path,
    progress_hook: Optional[Callable[[str], Any]] = None,
) -> Tuple[Context, Optional[TranslationRemapResult]]:
    """Execute selected canonical stages using the translator instance and save intermediate outputs to staging_dir."""
    async def report(stage: str):
        if progress_hook:
            await progress_hook(stage)

    remap_result: Optional[TranslationRemapResult] = None
    old_regions = state.get("old_regions", [])

    # Snapshot old metadata into staging dir
    if old_regions:
        (staging_dir / "old_regions_snapshot.json").write_text(
            json.dumps(serialize_regions(old_regions), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # 1. Full Pipeline Execution
    if plan.mode == PipelineRerunMode.FULL:
        # Reuse the production pipeline while routing every artifact into the
        # rerun staging directory; live page outputs change only at commit.
        if not hasattr(translator, "_translate"):
            raise RuntimeError("Full rerun requires the in-process translator")
        old_state = {
            "pipeline_run": getattr(translator, "_pipeline_run", None),
            "image_context": getattr(translator, "_current_image_context", None),
            "verbose": getattr(translator, "verbose", False),
            "result_path_override": getattr(translator, "_result_path_override", None),
        }
        try:
            translator._pipeline_run = None
            translator._current_image_context = None
            translator.verbose = True
            translator._result_path_override = staging_dir
            ctx = await translator._translate(config, ctx)
        finally:
            translator._pipeline_run = old_state["pipeline_run"]
            translator._current_image_context = old_state["image_context"]
            translator.verbose = old_state["verbose"]
            if old_state["result_path_override"] is None:
                delattr(translator, "_result_path_override")
            else:
                translator._result_path_override = old_state["result_path_override"]
        if ctx.result is None:
            raise RuntimeError("Full pipeline rerun produced no final image")
        ctx.debug_folder = state.get("folder") or ctx.debug_folder
        documents = dict(getattr(ctx, "result_documents", None) or {})
        documents.setdefault("translations.json", serialize_regions(ctx.text_regions or []))
        documents["layout.json"] = _frozen_layout_document(ctx, translator, config)
        documents["text_regions.json"] = serialize_editor_regions(ctx.text_regions or [])
        if getattr(ctx, "bubble_detections", None) is not None:
            from manga_translator.detection.bubble import serialize_bubble_detections
            documents["bubble_detections.json"] = serialize_bubble_detections(ctx.bubble_detections)
        for name, payload in documents.items():
            if Path(name).name == name and name.endswith(".json"):
                (staging_dir / name).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        return ctx, None

    # 2. Reprocess Text Mode (Detection -> OCR -> Textline Merge -> Bubble -> Mask -> Inpaint -> Remap -> Typeset)
    if plan.mode == PipelineRerunMode.REPROCESS_TEXT:
        # Text Detection
        await report("detection")
        ctx.textlines, ctx.mask_raw, ctx.mask = await translator._run_detection(config, ctx)
        if ctx.mask_raw is not None:
            cv2.imwrite(str(staging_dir / "mask_raw.png"), ctx.mask_raw)
        (staging_dir / "detection.json").write_text(
            json.dumps(serialize_regions(ctx.textlines), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if not ctx.textlines:
            ctx.text_regions = []
            ctx.img_inpainted = ctx.img_rgb.copy()
            save_jpeg(ctx.img_inpainted, staging_dir / "inpainted.jpg")
            save_jpeg(ctx.img_inpainted, staging_dir / "final.jpg")
            (staging_dir / "text_regions.json").write_text("[]", encoding="utf-8")
            ctx.result = dump_image(ctx.input, ctx.img_inpainted, ctx.img_alpha)
            return ctx, TranslationRemapResult(remapped_regions=[], total_new=0, total_old=len(old_regions))

        # OCR
        await report("ocr")
        ctx.textlines = await translator._run_ocr(config, ctx)
        (staging_dir / "ocr.json").write_text(
            json.dumps(serialize_regions(ctx.textlines), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Textline Merge
        await report("textline_merge")
        ctx.text_regions = await translator._run_textline_merge(config, ctx)
        (staging_dir / "text_regions_merged.json").write_text(
            json.dumps(serialize_regions(ctx.text_regions), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Bubble Association / Detection
        if not getattr(ctx, "bubble_detections", None):
            await translator._detect_speech_bubbles(config, ctx)
        else:
            # Reassociate existing bubble shapes
            from manga_translator.detection.bubble import associate_regions_with_bubbles
            ctx.text_regions = associate_regions_with_bubbles(ctx.text_regions, ctx.bubble_detections)

        if getattr(ctx, "bubble_detections", None):
            from manga_translator.detection.bubble import serialize_bubble_detections
            (staging_dir / "bubble_detections.json").write_text(
                json.dumps(serialize_bubble_detections(ctx.bubble_detections), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # Mask Generation
        await report("mask-generation")
        bundle = await run_cpu_stage(
            build_inpaint_masks,
            image=ctx.img_rgb,
            detector_textlines=getattr(ctx, "textlines", None),
            detector_mask=getattr(ctx, "mask_raw", None),
            text_regions=ctx.text_regions or [],
            bubble_detections=getattr(ctx, "bubble_detections", None),
            config=config,
            page_geometry=getattr(ctx, "page_geometry", None),
            priority=CPU_PRIORITY_NORMAL,
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

        with open(staging_dir / "profiling.json", "w", encoding="utf-8") as profile_file:
            json.dump(bundle.profile, profile_file, indent=2)

        cv2.imwrite(str(staging_dir / "text_mask.png"), ctx.text_mask)
        cv2.imwrite(str(staging_dir / "bubble_mask.png"), ctx.bubble_mask)
        cv2.imwrite(str(staging_dir / "mask_final.png"), ctx.mask)
        cv2.imwrite(str(staging_dir / "inpaint_mask.png"), ctx.inpaint_mask)

        # Inpainting
        await report("inpainting")
        ctx.img_inpainted = await translator._run_inpainting(config, ctx)
        save_jpeg(ctx.img_inpainted, staging_dir / "inpainted.jpg")

        # Translation Remapping
        await report("translation_remap")
        remap_result = remap_translations(
            old_regions=old_regions,
            new_regions=ctx.text_regions,
            canvas_size=(ctx.img_rgb.shape[1], ctx.img_rgb.shape[0]),
        )
        ctx.text_regions = remap_result.remapped_regions
        (staging_dir / "translations.json").write_text(
            json.dumps(serialize_regions(ctx.text_regions), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (staging_dir / "translation_remap.json").write_text(
            json.dumps(
                {
                    "summary": remap_result.summary(),
                    "details": [
                        {
                            "new_region_id": d.new_region_id,
                            "old_region_ids": d.old_region_ids,
                            "confidence": d.confidence,
                            "method": d.method,
                            "status": d.status,
                            "translation": d.translation,
                            "review_required": d.review_required,
                            "review_reason": d.review_reason,
                        }
                        for d in remap_result.details
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # Rendering & Layout
        await report("layout")
        await report("rendering")
        ctx.img_rendered = await translator._run_text_rendering(config, ctx)
        (staging_dir / "layout.json").write_text(
            json.dumps(_frozen_layout_document(ctx, translator, config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
        save_jpeg(np.array(ctx.result), staging_dir / "final.jpg")
        (staging_dir / "text_regions.json").write_text(
            json.dumps(serialize_editor_regions(ctx.text_regions), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ctx, remap_result

    # 3. Translation + Typesetting Mode
    if plan.mode == PipelineRerunMode.TRANSLATION_TYPESETTING:
        await report("translating")
        ctx.text_regions = await translator._run_text_translation(config, ctx)
        (staging_dir / "translations.json").write_text(
            json.dumps(serialize_regions(ctx.text_regions), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        await report("layout")
        await report("rendering")
        ctx.img_rendered = await translator._run_text_rendering(config, ctx)
        (staging_dir / "layout.json").write_text(
            json.dumps(_frozen_layout_document(ctx, translator, config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
        save_jpeg(np.array(ctx.result), staging_dir / "final.jpg")
        (staging_dir / "text_regions.json").write_text(
            json.dumps(serialize_editor_regions(ctx.text_regions), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ctx, None

    # 4. Typesetting Only Mode
    if plan.mode == PipelineRerunMode.TYPESETTING:
        await report("layout")
        await report("rendering")
        if hasattr(translator, "render_saved"):
            ctx = await translator.render_saved(ctx, config)
        else:
            ctx.img_rendered = await translator._run_text_rendering(config, ctx)
            ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)

        (staging_dir / "layout.json").write_text(
            json.dumps(_frozen_layout_document(ctx, translator, config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        save_jpeg(np.array(ctx.result), staging_dir / "final.jpg")
        (staging_dir / "text_regions.json").write_text(
            json.dumps(serialize_editor_regions(ctx.text_regions), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ctx, None

    raise ValueError(f"Unhandled rerun execution mode: {plan.mode}")


async def commit_rerun_artifacts(
    result_dir: Path,
    staging_dir: Path,
    plan: PipelineRerunPlan,
    remap_result: Optional[TranslationRemapResult] = None,
    database: Any = None,
    job_id: str = "",
) -> None:
    """Atomically commit successfully staged rerun artifacts to the live result directory."""
    final_target = final_file(result_dir) or (result_dir / "final.jpg")

    # Load existing manifest to track rerun provenance & revisions
    manifest_path = result_dir / "pipeline_manifest.json"
    manifest: Dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    current_revision = int(manifest.get("revision", 1))
    new_revision = current_revision + 1

    executed_stages: List[str] = []
    reused_stages: List[str] = []

    if plan.mode == PipelineRerunMode.FULL:
        executed_stages = ["input", "detection", "ocr", "textline_merge", "bubble_detection", "mask_generation", "inpainting", "translation", "rendering"]
    elif plan.mode == PipelineRerunMode.TYPESETTING:
        executed_stages = ["rendering"]
        reused_stages = ["input", "detection", "ocr", "textline_merge", "bubble_detection", "mask_generation", "inpainting", "translation"]
    elif plan.mode == PipelineRerunMode.TRANSLATION_TYPESETTING:
        executed_stages = ["translation", "rendering"]
        reused_stages = ["input", "detection", "ocr", "textline_merge", "bubble_detection", "mask_generation", "inpainting"]
    elif plan.mode == PipelineRerunMode.REPROCESS_TEXT:
        executed_stages = ["detection", "ocr", "textline_merge", "mask_generation", "inpainting", "translation_remap", "rendering"]
        reused_stages = ["input", "bubble_detection", "translation"]

    last_rerun_info: Dict[str, Any] = {
        "jobId": job_id,
        "mode": plan.mode.value,
        "completedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "parentRevision": current_revision,
        "revision": new_revision,
        "executedStages": executed_stages,
        "reusedStages": reused_stages,
    }
    if remap_result is not None:
        last_rerun_info["translationRemap"] = remap_result.summary()

    manifest["revision"] = new_revision
    manifest["lastRerun"] = last_rerun_info
    (staging_dir / "pipeline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Collect documents for database save
    db_documents: Dict[str, Any] = {}
    for json_file in staging_dir.glob("*.json"):
        if json_file.name.startswith("."):
            continue
        try:
            db_documents[json_file.name] = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    versioned_artifacts: list[dict[str, Any]] = []
    versioned_files: list[Path] = []
    page_id = (
        await database.get_page_id(result_dir.name)
        if database is not None and hasattr(database, "get_page_id")
        else None
    )
    if page_id:
        selected: dict[str, Path] = {}
        for staged_file in sorted(staging_dir.iterdir()):
            spec = _RERUN_ARTIFACTS.get(staged_file.name)
            if spec is None or staged_file.is_dir():
                continue
            artifact_type = spec[1]
            current = selected.get(artifact_type)
            if current is None or (current.suffix.lower() == ".png" and staged_file.suffix.lower() in {".jpg", ".jpeg"}):
                selected[artifact_type] = staged_file
        for staged_file in selected.values():
            stage_id, artifact_type = _RERUN_ARTIFACTS[staged_file.name]
            artifact, artifact_path = await asyncio.to_thread(
                _prepare_versioned_artifact,
                staged_file,
                result_dir.parent,
                result_dir,
                stage_id,
                artifact_type,
            )
            versioned_artifacts.append(artifact)
            versioned_files.append(artifact_path)

        try:
            committed = await database.commit_pipeline_outputs(
                result_dir.name, db_documents, versioned_artifacts
            )
        except Exception:
            for path in versioned_files:
                path.unlink(missing_ok=True)
            raise
        if not committed:
            for path in versioned_files:
                path.unlink(missing_ok=True)
            versioned_artifacts.clear()
            versioned_files.clear()
            await database.save_documents(result_dir.name, db_documents)
    elif database is not None and db_documents:
        await database.save_documents(result_dir.name, db_documents)

    # Atomically replace files from staging dir to live folder
    for staged_file in staging_dir.iterdir():
        if staged_file.name.startswith(".") or staged_file.is_dir():
            continue
        dest_name = staged_file.name
        if dest_name == "final.jpg" and final_target.suffix.lower() in {".png"}:
            dest_name = "final.png"
        target_path = result_dir / dest_name
        await asyncio.to_thread(_copy_file_atomic, staged_file, target_path)

    # Invalidate web view cached variants
    for variant in ("batch.webp", "cover.webp", "preview.webp", "reader.webp"):
        (result_dir / variant).unlink(missing_ok=True)
