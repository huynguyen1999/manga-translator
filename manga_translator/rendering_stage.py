"""Rendering-stage orchestration used by MangaTranslator."""

import logging
import time

from .config import Config
from .rendering import get_default_eng_font, render_page
from .rendering.layout.frozen import hydrate_layout, layout_input_fingerprints, serialize_frozen_layout
from .rendering.layout import layout_page
from .detection.bubble import serialize_bubble_detections
from .pipeline.cpu import CPU_PRIORITY_BACKGROUND, CPU_PRIORITY_NORMAL, run_cpu_stage
from .utils import is_preserved_region


async def run_text_rendering(
    owner,
    config: Config,
    ctx,
    *,
    logger: logging.Logger,
    run_cpu_stage_fn=run_cpu_stage,
    render_page_fn=render_page,
):
    current_time = time.time()
    owner._model_usage_timestamps[("rendering", config.render.renderer)] = current_time
    active_font = owner.font_path or getattr(config.render, "font_path", None) or get_default_eng_font()
    cpu_priority = (
        CPU_PRIORITY_BACKGROUND
        if getattr(ctx, "_background_batch_render", False)
        else CPU_PRIORITY_NORMAL
    )
    bubble_detection_enabled = bool(getattr(getattr(config, "bubble_detection", None), "enabled", False))

    transform_text_case = getattr(config.render, "transform_text_case", None)
    if transform_text_case:
        for region in (ctx.text_regions or []):
            if is_preserved_region(region):
                region.translation = region.text
            elif getattr(region, "translation", None) and isinstance(region.translation, str):
                region.translation = transform_text_case(region.translation)

    pipeline_run = getattr(owner, "_pipeline_run", None)
    layout_stage = next((
        stage for stage in getattr(pipeline_run, "manifest", {}).get("stages", [])
        if stage.get("id") == "layout"
    ), None)
    if layout_stage and layout_stage.get("status") == "completed" and pipeline_run is not None:
        layout_doc = pipeline_run._document("layout.json")
        if layout_doc is not None:
            bubble_doc = pipeline_run._document("bubble_detections.json")
            if bubble_doc is None:
                bubble_doc = serialize_bubble_detections(getattr(ctx, "bubble_detections", None) or [])
            inputs = layout_input_fingerprints(
                ctx.text_regions or [], config, active_font, bubble_doc,
                getattr(ctx.img_rgb, "shape", None),
                getattr(ctx, "inpaint_mask", None) if getattr(ctx, "inpaint_mask", None) is not None else getattr(ctx, "mask", None),
                layout_doc.get("input_fingerprints", {}).get("mask")
                if isinstance(layout_doc, dict) and getattr(ctx, "inpaint_mask", None) is None and getattr(ctx, "mask", None) is None
                else None,
            )
            try:
                hydrate_layout(ctx, layout_doc, inputs["fingerprint"])
            except ValueError as error:
                logger.info("Cached layout is stale; recomputing it: %s", error)
            else:
                ctx._bubble_detection_done = True
                ctx._bubble_layout_ready = True

    if bubble_detection_enabled and not getattr(ctx, "_bubble_detection_done", False):
        await owner._detect_speech_bubbles(config, ctx, report_progress=False)

    if getattr(ctx, "img_rgb", None) is not None and not getattr(ctx, "_bubble_layout_ready", False):
        try:
            if hasattr(owner, "_progress_hooks"):
                await owner._report_progress("layout")
            await run_cpu_stage_fn(layout_page, ctx, config, active_font, priority=cpu_priority)
            if getattr(owner, "_pipeline_run", None) is not None:
                bubble_doc = serialize_bubble_detections(getattr(ctx, "bubble_detections", None) or [])
                owner._pipeline_run.write_json("layout.json", serialize_frozen_layout(
                    ctx, config, active_font, bubble_doc,
                ))
        except Exception as error:
            logger.warning("Bubble layout failed; preserving the existing render path: %s", error)

    if ctx.img_inpainted is None and getattr(ctx, "img_rgb", None) is not None:
        if ctx.mask is None:
            ctx.mask = getattr(ctx, "bubble_mask", None)
        ctx.img_inpainted = ctx.img_rgb.copy()

    return await run_cpu_stage_fn(render_page_fn, ctx, config, active_font, priority=cpu_priority)
