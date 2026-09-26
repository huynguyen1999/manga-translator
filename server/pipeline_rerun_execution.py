"""Execute the selected rerun stages into a staging directory."""

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from manga_translator.config import Config
from manga_translator.mask_builder import build_inpaint_masks
from manga_translator.pipeline.cpu import CPU_PRIORITY_NORMAL, run_cpu_stage
from manga_translator.pipeline.translation_remap import remap_translations, TranslationRemapResult
from manga_translator.pipeline.stages import PipelineStage
from manga_translator.pipeline.run import (
    serialize_editor_regions,
    serialize_regions,
)
from manga_translator.utils import Context, dump_image
from manga_translator.utils.image_storage import save_jpeg
from server.pipeline_rerun_plan import (
    PipelineRerunMode,
    PipelineRerunPlan,
    _frozen_layout_document,
)


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
        ctx.inpaint_mask = bundle.final_inpaint_mask

        with open(staging_dir / "profiling.json", "w", encoding="utf-8") as profile_file:
            json.dump(bundle.profile, profile_file, indent=2)

        cv2.imwrite(str(staging_dir / "text_mask.png"), ctx.text_mask)
        cv2.imwrite(str(staging_dir / "bubble_mask.png"), ctx.bubble_mask)
        cv2.imwrite(str(staging_dir / "mask_final.png"), ctx.mask)
        cv2.imwrite(str(staging_dir / "inpaint_mask.png"), ctx.inpaint_mask)
        ctx.cleanup_mask_diagnostics()
        bundle = None

        # Inpainting
        await report("inpainting")
        ctx.img_inpainted = await translator._run_inpainting(config, ctx)
        save_jpeg(ctx.img_inpainted, staging_dir / "inpainted.jpg")
        if getattr(ctx, "_bubble_layout_ready", False):
            ctx.cleanup_mask_workspace()

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
