"""Pre-translation pipeline orchestration for one image."""

from __future__ import annotations

import asyncio
import json
import traceback
from typing import Any, Callable

import cv2

from ..colorization import is_image_colored, prepare as prepare_colorization
from ..config import Colorizer, Config
from ..detection import prepare as prepare_detection
from ..detection.bubble import serialize_bubble_detections
from ..geometry.bubbles import prepare_page_geometry
from ..ocr import prepare as prepare_ocr
from ..pipeline.run import serialize_regions
from ..translators import prepare as prepare_translation
from ..upscaling import prepare as prepare_upscaling
from ..utils import Context, load_image


async def translate_until_translation(
    translator: Any,
    image: Any,
    config: Config,
    prepare_canvas: bool = True,
    *,
    logger: Any,
    save_jpeg_fn: Callable,
    save_documents_fn: Callable,
    load_dictionary_fn: Callable,
    apply_dictionary_fn: Callable,
) -> Context:
    """
    执行翻译之前的所有步骤（彩色化、上采样、检测、OCR、文本行合并）
    """
    ctx = Context()
    ctx.input = image
    ctx.result = None
    ctx.result_documents = {}

    # 保存原始输入图片用于对比和调试
    try:
        result_path = translator._result_path('input.jpg')
        await asyncio.to_thread(save_jpeg_fn, image, result_path)
    except Exception as e:
        logger.error(f"Error saving input.jpg debug image: {e}")
        logger.debug(f"Exception details: {traceback.format_exc()}")

    # preload and download models (not strictly necessary, remove to lazy load)
    if ( translator.models_ttl == 0 ):
        logger.info('Loading models')
        if config.upscale.upscale_ratio:
            await prepare_upscaling(config.upscale.upscaler)
        await prepare_detection(config.detector.detector)
        await prepare_ocr(config.ocr.ocr, translator.device)
        await prepare_translation(config.translator.translator_gen)
        if config.colorizer.colorizer != Colorizer.none:
            await prepare_colorization(config.colorizer.colorizer)

    translator._log_memory_boundary("models_ready", ctx)
    # Start the background cleanup job once if not already started.
    if translator._model_cleanup_task is None:
        translator._model_cleanup_task = asyncio.create_task(translator._model_cleanup_job())

    # -- Colorization
    if config.colorizer.colorizer != Colorizer.none:
        color_threshold = getattr(config.colorizer, 'color_threshold', 31.0)
        if color_threshold is None:
            color_threshold = getattr(config.colorizer, 'color_tolerance', 31.0)
        is_col = False
        if color_threshold is not None and color_threshold > 0:
            is_col, col_details = is_image_colored(ctx.input, color_threshold=color_threshold)
            if is_col:
                logger.info(f"Image appears to be already colored ({col_details.get('reason', '')}). Skipping colorization.")
                ctx.img_colorized = ctx.input

        if not is_col:
            await translator._report_progress('colorizing')
            try:
                ctx.img_colorized = await translator._run_colorizer(config, ctx)
            except Exception as e:
                logger.error(f"Error during colorizing:\n{traceback.format_exc()}")
                if not translator.ignore_errors:
                    raise
                ctx.img_colorized = ctx.input
    else:
        ctx.img_colorized = ctx.input

    # -- Upscaling
    if config.upscale.upscale_ratio:
        await translator._report_progress('upscaling')
        try:
            ctx.upscaled = await translator._run_upscaling(config, ctx)
            ctx.upscaled_ran = True
        except Exception as e:
            logger.error(f"Error during upscaling:\n{traceback.format_exc()}")
            if not translator.ignore_errors:
                raise
            ctx.upscaled = ctx.img_colorized
            ctx.upscaled_ran = False
    else:
        ctx.upscaled = ctx.img_colorized
        ctx.upscaled_ran = False

    ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

    # -- Detection
    await translator._report_progress('detection')
    try:
        ctx.textlines, ctx.mask_raw, ctx.mask = await translator._run_detection(config, ctx)
    except Exception as e:
        logger.error(f"Error during detection:\n{traceback.format_exc()}")
        if not translator.ignore_errors:
            raise
        ctx.textlines = []
        ctx.mask_raw = None
        ctx.mask = None

    if ctx.textlines:
        detection_docs = serialize_regions(ctx.textlines)
        ctx.result_documents['detection.json'] = detection_docs
        if translator._pipeline_run is not None:
            translator._pipeline_run.write_json('detection.json', detection_docs)
        elif translator._current_image_context:
            await save_documents_fn(
                translator._current_image_context['subfolder'],
                {'detection.json': detection_docs},
                translator.result_root,
            )

    if (translator.verbose or translator._pipeline_run is not None) and ctx.mask_raw is not None:
        await translator._async_imwrite(translator._result_path('mask_raw.png'), ctx.mask_raw)

    if not ctx.textlines:
        await translator._report_progress('skip-no-regions', True)
        ctx.result = ctx.upscaled
        if translator._current_image_context:
            ctx.image_context = translator._current_image_context.copy()
        return await translator._revert_upscale(config, ctx)

    # -- OCR
    await translator._report_progress('ocr')
    try:
        ctx.textlines = await translator._run_ocr(config, ctx)
    except Exception as e:
        logger.error(f"Error during ocr:\n{traceback.format_exc()}")
        if not translator.ignore_errors:
            raise
        ctx.textlines = []

    if ctx.textlines:
        ocr_docs = serialize_regions(ctx.textlines)
        ctx.result_documents['ocr.json'] = ocr_docs
        if translator._pipeline_run is not None:
            translator._pipeline_run.write_json('ocr.json', ocr_docs)
        elif translator._current_image_context:
            await save_documents_fn(
                translator._current_image_context['subfolder'],
                {'ocr.json': ocr_docs},
                translator.result_root,
            )

    if not ctx.textlines:
        await translator._report_progress('skip-no-text', True)
        ctx.result = ctx.upscaled
        if translator._current_image_context:
            ctx.image_context = translator._current_image_context.copy()
        return await translator._revert_upscale(config, ctx)

    # -- Textline merge
    await translator._report_progress('textline_merge')
    try:
        ctx.text_regions = await translator._run_textline_merge(config, ctx)
    except Exception as e:
        logger.error(f"Error during textline_merge:\n{traceback.format_exc()}")
        if not translator.ignore_errors:
            raise
        ctx.text_regions = []

    if ctx.text_regions:
        merged_docs = serialize_regions(ctx.text_regions)
        ctx.result_documents['text_regions_merged.json'] = merged_docs
        if translator._pipeline_run is not None:
            translator._pipeline_run.write_json('text_regions_merged.json', merged_docs)
        elif translator._current_image_context:
            await save_documents_fn(
                translator._current_image_context['subfolder'],
                {'text_regions_merged.json': merged_docs},
                translator.result_root,
            )

    if not ctx.text_regions:
        await translator._report_progress('skip-no-regions', True)
        ctx.result = ctx.upscaled
        if translator._current_image_context:
            ctx.image_context = translator._current_image_context.copy()
        return await translator._revert_upscale(config, ctx)

    # Optional speech-bubble detection runs after OCR merging so every
    # assigned bubble is translated as one text flow.
    await translator._detect_speech_bubbles(config, ctx)
    ctx.page_geometry, ctx.bubble_mask = prepare_page_geometry(
        ctx.img_rgb,
        ctx.text_regions,
        padding=int(getattr(config.bubble_detection, "padding", 9)),
    )

    # Apply pre-dictionary after textline merge
    pre_dict = load_dictionary_fn(translator.pre_dict)
    pre_replacements = []
    for region in ctx.text_regions:
        original = region.text
        region.text = apply_dictionary_fn(region.text, pre_dict)
        if original != region.text:
            pre_replacements.append(f"{original} => {region.text}")

    if pre_replacements:
        logger.info("Pre-translation replacements:")
        for replacement in pre_replacements:
            logger.info(replacement)
    else:
        logger.info("No pre-translation replacements made.")

    # Keep the canvas and geometry ready for translation-dependent stages.
    if prepare_canvas and ctx.text_regions:
        merged = serialize_regions(ctx.text_regions)
        ctx.result_documents['text_regions_merged.json'] = merged
        bubble_documents = serialize_bubble_detections(
            getattr(ctx, 'bubble_detections', None) or []
        )
        ctx.result_documents['bubble_detections.json'] = bubble_documents
        if translator._current_image_context:
            await translator._async_imwrite(
                translator._result_path('original_canvas.png'),
                cv2.cvtColor(ctx.img_rgb, cv2.COLOR_RGB2BGR),
            )
            if ctx.bubble_mask is not None:
                await translator._async_imwrite(translator._result_path('bubble_mask.png'), ctx.bubble_mask)
            if getattr(ctx, 'bubble_detections', None):
                bd_path = translator._result_path('bubble_detections.json')
                with open(bd_path, 'w', encoding='utf-8') as f:
                    json.dump(serialize_bubble_detections(ctx.bubble_detections), f, indent=2)
        ctx.mask = None
        ctx.inpaint_mask = None
        saved_docs = {
            'text_regions_merged.json': merged,
            'bubble_detections.json': bubble_documents,
        }
        if translator._pipeline_run is not None:
            for name, document in saved_docs.items():
                translator._pipeline_run.write_json(name, document)
            await translator._pipeline_run.checkpoint()
        elif translator._current_image_context:
            await save_documents_fn(
                translator._current_image_context['subfolder'],
                saved_docs,
                translator.result_root,
            )
        await translator._report_progress('awaiting_translation')

    # 保存当前图片上下文到ctx中，用于并发翻译时的路径管理
    if translator._current_image_context:
        ctx.image_context = translator._current_image_context.copy()

    return ctx

