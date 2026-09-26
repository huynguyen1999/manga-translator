"""Pipeline stage retries, kept behind the PipelineRun compatibility method."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from ..config import Config


async def execute_retry_stage(
    run,
    stage_id: str,
    new_config: dict | Config,
    translator,
    *,
    runtime: Any,
    defer_bubble_detection: bool = False,
    precomputed_ocr: list | None = None,
    precomputed_upscale: Image.Image | None = None,
    precomputed_detection: tuple[list, np.ndarray | None, np.ndarray | None] | None = None,
    precomputed_bubbles: list | None = None,
    precomputed_inpainting: np.ndarray | None = None,
    stage_already_running: bool = False,
) -> dict[str, Any]:
    CPU_PRIORITY_BACKGROUND = runtime.CPU_PRIORITY_BACKGROUND
    build_inpaint_masks = runtime.build_inpaint_masks
    cv2 = runtime.cv2
    dump_image = runtime.dump_image
    find_asset = runtime.find_asset
    load_image = runtime.load_image
    np = runtime.np
    run_cpu_stage = runtime.run_cpu_stage
    save_jpeg = runtime.save_jpeg
    serialize_editor_regions = runtime.serialize_editor_regions
    serialize_regions = runtime.serialize_regions
    deserialize_textblocks = runtime.deserialize_textblocks
    deserialize_textlines = runtime.deserialize_textlines
    time = runtime.time
    _now = runtime._now
    Config = runtime.Config
    Image = runtime.Image
    valid_stages = {
        "colorization",
        "upscaling",
        "detection",
        "ocr",
        "textline_merge",
        "bubble_detection",
        "translation",
        "mask_generation",
        "layout",
        "inpainting",
        "rendering",
    }
    if stage_id not in valid_stages:
        raise ValueError(f"Stage '{stage_id}' is not retryable")

    if isinstance(new_config, dict):
        base_config = dict(run.manifest.get("config", {}) or {})
        base_config.update(new_config)
        config = Config.parse_obj(base_config)
    else:
        config = new_config

    run.config = config
    run.manifest["config"] = config.dict() if hasattr(config, "dict") else dict(config)

    stage = run._stage(stage_id)
    if stage_already_running:
        if stage.get("status") != "running":
            raise RuntimeError(f"Stage {stage_id} was not marked running before batched inference")
    else:
        stage["status"] = "running"
    stage["startedAt"] = _now()
    run.started[stage_id] = time.monotonic()
    run._memory_begin(stage_id)
    run.refresh()

    ctx = run._ensure_context()

    try:
        if stage_id == "colorization":
            if ctx.input is None:
                raise RuntimeError("No input image available for colorization")
            ctx.img_colorized = await translator._run_colorizer(config, ctx)
            colorized = np.array(ctx.img_colorized)
            if len(colorized.shape) == 3 and colorized.shape[2] == 3:
                colorized = cv2.cvtColor(colorized, cv2.COLOR_RGB2BGR)
            cv2.imwrite(str(run.path / "colorized.png"), colorized)

        elif stage_id == "upscaling":
            if ctx.img_colorized is None:
                ctx.img_colorized = ctx.input
            if ctx.img_colorized is None:
                raise RuntimeError("No image available for upscaling")
            ctx.upscaled = (
                precomputed_upscale
                if precomputed_upscale is not None
                else await translator._run_upscaling(config, ctx)
            )
            ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)
            upscaled = np.array(ctx.upscaled)
            if len(upscaled.shape) == 3 and upscaled.shape[2] == 3:
                upscaled = cv2.cvtColor(upscaled, cv2.COLOR_RGB2BGR)
            cv2.imwrite(str(run.path / "upscaled.png"), upscaled)

        elif stage_id == "detection":
            if ctx.img_rgb is None:
                raise RuntimeError("No image canvas available for detection")
            if precomputed_detection is None:
                ctx.textlines, ctx.mask_raw, ctx.mask = await translator._run_detection(config, ctx)
            else:
                ctx.textlines, ctx.mask_raw, ctx.mask = precomputed_detection
            if ctx.mask_raw is not None:
                cv2.imwrite(str(run.path / "mask_raw.png"), ctx.mask_raw)
            canvas = np.asarray(ctx.upscaled)
            if len(canvas.shape) == 3 and canvas.shape[2] == 3:
                canvas = cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR)
            cv2.imwrite(str(run.path / "original_canvas.png"), canvas)
            run.write_json("detection.json", serialize_regions(ctx.textlines))

        elif stage_id == "ocr":
            if ctx.img_rgb is None:
                raise RuntimeError("No image canvas available for OCR")
            if not getattr(ctx, "textlines", None):
                detection = run._document("detection.json")
                if detection is not None:
                    ctx.textlines = deserialize_textlines(detection)
            if not getattr(ctx, "textlines", None):
                ctx.textlines = []
                run.write_json("ocr.json", [])
            else:
                ctx.textlines = (
                    precomputed_ocr
                    if precomputed_ocr is not None
                    else await translator._run_ocr(config, ctx)
                )
                run.write_json("ocr.json", serialize_regions(ctx.textlines))

        elif stage_id == "textline_merge":
            if ctx.img_rgb is None:
                raise RuntimeError("No image canvas available for textline merge")
            if not getattr(ctx, "textlines", None):
                source = run._document("ocr.json") or run._document("detection.json")
                if source is not None:
                    ctx.textlines = deserialize_textlines(source)
            if not getattr(ctx, "textlines", None):
                raise RuntimeError("No textlines available to merge")
            ctx.text_regions = await translator._run_textline_merge(config, ctx)
            bubble_data = run._document("bubble_detections.json")
            if bubble_data is not None:
                from ..detection.bubble import deserialize_bubble_detections
                from ..rendering.bubble_layout import group_regions_by_bubbles

                ctx.bubble_detections = deserialize_bubble_detections(
                    bubble_data, ctx.img_rgb.shape
                )
                ctx.text_regions = group_regions_by_bubbles(
                    ctx.text_regions,
                    ctx.bubble_detections,
                    group=bool(getattr(config.bubble_detection, "group_regions", False)),
                )
            elif not defer_bubble_detection:
                await translator._detect_speech_bubbles(config, ctx, report_progress=False)
            ctx._bubble_detection_done = True
            run.write_json("text_regions_merged.json", serialize_regions(ctx.text_regions))

        elif stage_id == "bubble_detection":
            if ctx.img_rgb is None:
                raise RuntimeError("No image canvas available for bubble detection")
            if not getattr(ctx, "text_regions", None):
                merged = run._document("text_regions_merged.json")
                if merged is not None:
                    ctx.text_regions = deserialize_textblocks(merged)
            await translator._detect_speech_bubbles(
                config,
                ctx,
                report_progress=False,
                precomputed_detections=precomputed_bubbles,
            )
            detections = getattr(ctx, "bubble_detections", None) or []
            from ..detection.bubble import serialize_bubble_detections

            run.write_json("bubble_detections.json", serialize_bubble_detections(detections))
            if ctx.text_regions:
                run.write_json("text_regions_merged.json", serialize_regions(ctx.text_regions))
            if detections:
                bubble_mask = np.zeros(ctx.img_rgb.shape[:2], np.uint8)
                for detection in detections:
                    bubble_mask = np.maximum(bubble_mask, np.asarray(detection.mask, dtype=np.uint8))
                cv2.imwrite(str(run.path / "bubble_mask.png"), bubble_mask)

        elif stage_id == "translation":
            if not getattr(ctx, "text_regions", None):
                merged = run._document("text_regions_merged.json")
                if merged is not None:
                    ctx.text_regions = deserialize_textblocks(merged)
            if not getattr(ctx, "text_regions", None):
                raise RuntimeError("No text regions available for translation")
            translation_request = [
                {"index": index, "text": getattr(region, "text", "")}
                for index, region in enumerate(ctx.text_regions)
            ]
            ctx.text_regions = await translator._run_text_translation(config, ctx)
            run.record_translation(
                config,
                translation_request,
                [
                    {"index": index, "translation": getattr(region, "translation", "")}
                    for index, region in enumerate(ctx.text_regions or [])
                ],
                ctx,
            )
            run.write_json("translations.json", serialize_regions(ctx.text_regions if isinstance(ctx.text_regions, list) else []))

        elif stage_id == "mask_generation":
            if ctx.img_rgb is None:
                raise RuntimeError("No image canvas available for mask generation")
            if not getattr(ctx, "text_regions", None):
                merged = run._document("text_regions_merged.json")
                if merged is not None:
                    ctx.text_regions = deserialize_textblocks(merged)
            if getattr(ctx, "mask_raw", None) is None:
                mask_raw_path = run.path / "mask_raw.png"
                if mask_raw_path.is_file():
                    ctx.mask_raw = cv2.imread(str(mask_raw_path), cv2.IMREAD_GRAYSCALE)
            if not getattr(ctx, "bubble_detections", None):
                bubble_data = run._document("bubble_detections.json")
                if bubble_data is not None:
                    from ..detection.bubble import deserialize_bubble_detections

                    ctx.bubble_detections = deserialize_bubble_detections(
                        bubble_data, ctx.img_rgb.shape
                    )
            bundle = await run_cpu_stage(
                build_inpaint_masks,
                image=ctx.img_rgb,
                detector_textlines=getattr(ctx, "textlines", None),
                detector_mask=getattr(ctx, "mask_raw", None),
                text_regions=ctx.text_regions or [],
                bubble_detections=getattr(ctx, "bubble_detections", None),
                config=config,
                page_geometry=getattr(ctx, "page_geometry", None),
                priority=CPU_PRIORITY_BACKGROUND,
            )
            ctx.mask_bundle = bundle
            ctx.mask_profile = bundle.profile
            ctx.page_geometry = bundle.page_geometry
            ctx.text_mask = bundle.text_mask
            ctx.bubble_mask = bundle.bubble_cleanup_mask
            ctx.detector_rescue_mask = bundle.detector_rescue_mask
            ctx.bubble_residual_mask = bundle.bubble_residual_mask
            ctx.protected_edge_mask = bundle.protected_edge_mask
            ctx.mask = bundle.final_inpaint_mask
            ctx.inpaint_mask = bundle.final_inpaint_mask
            cv2.imwrite(str(run.path / "text_mask.png"), ctx.text_mask)
            cv2.imwrite(str(run.path / "bubble_mask.png"), ctx.bubble_mask)
            cv2.imwrite(str(run.path / "mask_final.png"), ctx.mask)
            cv2.imwrite(str(run.path / "detector_rescue_mask.png"), ctx.detector_rescue_mask)
            cv2.imwrite(str(run.path / "bubble_residual_mask.png"), ctx.bubble_residual_mask)
            cv2.imwrite(str(run.path / "protected_bubble_edge.png"), ctx.protected_edge_mask)
            run.write_json("profiling.json", bundle.profile)
            ctx.cleanup_mask_diagnostics()
            bundle = None

        elif stage_id == "layout":
            from ..rendering.layout import layout_page
            from ..rendering.layout.frozen import serialize_frozen_layout

            if getattr(ctx, "inpaint_mask", None) is None:
                mask_final_path = run.path / "mask_final.png"
                if mask_final_path.is_file():
                    ctx.inpaint_mask = cv2.imread(str(mask_final_path), cv2.IMREAD_GRAYSCALE)
                    ctx.mask = ctx.inpaint_mask
            if not getattr(ctx, "text_regions", None):
                source = run._document("translations.json") or run._document("text_regions_merged.json")
                if source is not None:
                    ctx.text_regions = deserialize_textblocks(source)
            if not getattr(ctx, "text_regions", None):
                raise RuntimeError("No text regions available for layout")
            bubble_data = run._document("bubble_detections.json")
            if bubble_data is not None:
                from ..detection.bubble import deserialize_bubble_detections
                from ..rendering.bubble_layout import restore_bubble_assignments

                ctx.bubble_detections = deserialize_bubble_detections(bubble_data, ctx.img_rgb.shape)
                if ctx.bubble_detections:
                    restore_bubble_assignments(ctx.text_regions, ctx.bubble_detections)
                ctx._bubble_detection_done = True
            else:
                bubble_data = []
            transform = getattr(getattr(config, "render", None), "transform_text_case", None)
            if transform:
                for region in ctx.text_regions:
                    if getattr(region, "translation", None):
                        region.translation = transform(region.translation)
            await run_cpu_stage(
                layout_page,
                ctx,
                config,
                getattr(translator, "font_path", None)
                or getattr(getattr(config, "render", None), "font_path", None),
                priority=CPU_PRIORITY_BACKGROUND,
            )
            font_path = (
                getattr(translator, "font_path", None)
                or getattr(getattr(config, "render", None), "font_path", None)
            )
            if not font_path:
                from ..rendering import get_default_eng_font
                font_path = get_default_eng_font()
            run.write_json("layout.json", serialize_frozen_layout(
                ctx, config, font_path, bubble_data
            ))

        elif stage_id == "inpainting":
            if ctx.img_rgb is None:
                raise RuntimeError("No image canvas available for inpainting")
            if getattr(ctx, "mask", None) is None:
                mask_final_path = run.path / "mask_final.png"
                if mask_final_path.is_file():
                    ctx.mask = cv2.imread(str(mask_final_path), cv2.IMREAD_GRAYSCALE)
            if getattr(ctx, "protected_edge_mask", None) is None:
                protected_path = run.path / "protected_bubble_edge.png"
                if protected_path.is_file():
                    ctx.protected_edge_mask = cv2.imread(str(protected_path), cv2.IMREAD_GRAYSCALE)
            if not getattr(ctx, "text_regions", None):
                regions = run._document("translations.json") or run._document("text_regions_merged.json")
                if regions is not None:
                    ctx.text_regions = deserialize_textblocks(regions)
            ctx.img_inpainted = (
                precomputed_inpainting
                if precomputed_inpainting is not None
                else await translator._run_inpainting(config, ctx)
            )
            if ctx.img_inpainted is not None:
                save_jpeg(ctx.img_inpainted, run.path / "inpainted.jpg")
                (run.path / "inpainted.png").unlink(missing_ok=True)

        elif stage_id == "rendering":
            if getattr(ctx, "img_inpainted", None) is None:
                inpainted_path = find_asset(run.path, "inpainted")
                if inpainted_path is not None:
                    with Image.open(inpainted_path) as image:
                        ctx.img_inpainted = np.array(image.convert("RGB"))
                elif ctx.img_rgb is not None:
                    ctx.img_inpainted = ctx.img_rgb.copy()
            if not getattr(ctx, "text_regions", None):
                translations = run._document("translations.json")
                if translations is not None:
                    ctx.text_regions = deserialize_textblocks(translations)
            if ctx.img_inpainted is None:
                raise RuntimeError("No inpainted canvas available for rendering")
            layout_data = run._document("layout.json")
            if layout_data is not None:
                bubble_data = run._document("bubble_detections.json")
                if bubble_data is not None:
                    from ..detection.bubble import deserialize_bubble_detections
                    from ..rendering.bubble_layout import restore_bubble_assignments

                    ctx.bubble_detections = deserialize_bubble_detections(bubble_data, ctx.img_rgb.shape)
                    if ctx.bubble_detections:
                        restore_bubble_assignments(ctx.text_regions or [], ctx.bubble_detections)
                    ctx._bubble_detection_done = True
                else:
                    bubble_data = []
                font_path = (
                    getattr(translator, "font_path", None)
                    or getattr(getattr(config, "render", None), "font_path", None)
                )
                if not font_path:
                    from ..rendering import get_default_eng_font
                    font_path = get_default_eng_font()
                mask_path = run.path / "mask_final.png"
                if mask_path.is_file():
                    ctx.inpaint_mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
            ctx.img_rendered = await translator._run_text_rendering(config, ctx)
            ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
            run.write_json("text_regions.json", serialize_editor_regions(ctx.text_regions))
            final_img = np.array(ctx.result)
            save_jpeg(final_img, run.path / "final.jpg")
            run.write_json("meta.json", translator._build_result_metadata(config, ctx))

        run._finish(stage_id, "completed")
        await run.checkpoint()
        return {
            "status": "ok",
            "stage": stage_id,
            "durationMs": stage.get("durationMs", 0),
            "manifest": run.manifest,
        }
    except Exception as exc:
        run._finish(stage_id, "failed", str(exc))
        await run.checkpoint()
        raise
