"""Single-page translation pipeline orchestration."""

import asyncio
import os
import traceback

import cv2
import numpy as np

from manga_translator.config import Colorizer, Config
from manga_translator.mask_builder import build_inpaint_masks, create_mask_sources_overlay
from manga_translator.pipeline.cpu import CPU_PRIORITY_BACKGROUND
from manga_translator.pipeline.run import serialize_regions
from manga_translator.rendering import get_default_eng_font
from manga_translator.rendering.layout import layout_page
from manga_translator.rendering.layout.frozen import serialize_frozen_layout
from manga_translator.detection.bubble import serialize_bubble_detections
from manga_translator.translation_errors import TranslationFailure
from manga_translator.utils import Context, dump_image, is_preserved_region, load_image
from manga_translator.colorization import is_image_colored


async def translate_page(
    owner,
    config: Config,
    ctx: Context,
    *,
    logger,
    run_cpu_stage_fn,
    save_jpeg_fn,
    save_documents_fn,
    load_dictionary_fn,
    apply_dictionary_fn,
) -> Context:
    if getattr(ctx, 'result_documents', None) is None:
        ctx.result_documents = {}
    # Start the background cleanup job once if not already started.
    if owner._model_cleanup_task is None:
        owner._model_cleanup_task = asyncio.create_task(owner._model_cleanup_job())
    # -- Colorization
    colorization_ran = False
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
            await owner._report_progress('colorizing')
            try:
                ctx.img_colorized = await owner._run_colorizer(config, ctx)
                colorization_ran = True
            except Exception as e:
                logger.error(f"Error during colorizing:\n{traceback.format_exc()}")
                if not owner.ignore_errors:
                    raise
                ctx.img_colorized = ctx.input  # Fallback to input image if colorization fails
    else:
        ctx.img_colorized = ctx.input

    if owner._pipeline_run is not None and colorization_ran and ctx.img_colorized is not None:
        colorized = np.array(ctx.img_colorized)
        if len(colorized.shape) == 3 and colorized.shape[2] == 3:
            colorized = cv2.cvtColor(colorized, cv2.COLOR_RGB2BGR)
        await owner._async_imwrite(owner._result_path('colorized.png'), colorized)
        owner._pipeline_run.refresh()

    # -- Upscaling
    # The default text detector doesn't work very well on smaller images, might want to
    # consider adding automatic upscaling on certain kinds of small images.
    upscaling_ran = False
    if config.upscale.upscale_ratio:
        await owner._report_progress('upscaling')
        try:
            ctx.upscaled = await owner._run_upscaling(config, ctx)
            upscaling_ran = True
            ctx.upscaled_ran = True
        except Exception as e:
            logger.error(f"Error during upscaling:\n{traceback.format_exc()}")
            if not owner.ignore_errors:
                raise
            ctx.upscaled = ctx.img_colorized # Fallback to colorized (or input) image if upscaling fails
            ctx.upscaled_ran = False
    else:
        ctx.upscaled = ctx.img_colorized
        ctx.upscaled_ran = False

    if owner._pipeline_run is not None and upscaling_ran and ctx.upscaled is not None:
        upscaled = np.array(ctx.upscaled)
        if len(upscaled.shape) == 3 and upscaled.shape[2] == 3:
            upscaled = cv2.cvtColor(upscaled, cv2.COLOR_RGB2BGR)
        await owner._async_imwrite(owner._result_path('upscaled.png'), upscaled)
        owner._pipeline_run.refresh()

    ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

    # -- Detection
    await owner._report_progress('detection')
    try:
        ctx.textlines, ctx.mask_raw, ctx.mask = await owner._run_detection(config, ctx)
    except Exception as e:
        logger.error(f"Error during detection:\n{traceback.format_exc()}")
        if not owner.ignore_errors:
            raise
        ctx.textlines = []
        ctx.mask_raw = None
        ctx.mask = None

    if (owner.verbose or owner._pipeline_run is not None) and ctx.mask_raw is not None:
        await owner._async_imwrite(owner._result_path('mask_raw.png'), ctx.mask_raw)

    if not ctx.textlines:
        await owner._report_progress('skip-no-regions', True)
        # If no text was found result is intermediate image product
        ctx.result = ctx.upscaled
        return await owner._revert_upscale(config, ctx)

    detection_docs = serialize_regions(ctx.textlines)
    ctx.result_documents['detection.json'] = detection_docs
    if owner._pipeline_run is not None:
        owner._pipeline_run.write_json('detection.json', detection_docs)
    elif owner._current_image_context:
        await save_documents_fn(
            owner._current_image_context['subfolder'],
            {'detection.json': detection_docs},
            owner.result_root,
        )

    # -- OCR
    await owner._report_progress('ocr')
    try:
        ctx.textlines = await owner._run_ocr(config, ctx)
    except Exception as e:
        logger.error(f"Error during ocr:\n{traceback.format_exc()}")
        if not owner.ignore_errors:
            raise
        ctx.textlines = [] # Fallback to empty textlines if OCR fails

    if ctx.textlines:
        ocr_docs = serialize_regions(ctx.textlines)
        ctx.result_documents['ocr.json'] = ocr_docs
        if owner._pipeline_run is not None:
            owner._pipeline_run.write_json('ocr.json', ocr_docs)
        elif owner._current_image_context:
            await save_documents_fn(
                owner._current_image_context['subfolder'],
                {'ocr.json': ocr_docs},
                owner.result_root,
            )

    if not ctx.textlines:
        await owner._report_progress('skip-no-text', True)
        # If no text was found result is intermediate image product
        ctx.result = ctx.upscaled
        return await owner._revert_upscale(config, ctx)

    # -- Textline merge
    await owner._report_progress('textline_merge')
    try:
        ctx.text_regions = await owner._run_textline_merge(config, ctx)
    except Exception as e:
        logger.error(f"Error during textline_merge:\n{traceback.format_exc()}")
        if not owner.ignore_errors:
            raise
        ctx.text_regions = [] # Fallback to empty text_regions if textline merge fails

    if ctx.text_regions:
        merged_docs = serialize_regions(ctx.text_regions)
        ctx.result_documents['text_regions_merged.json'] = merged_docs
        if owner._pipeline_run is not None:
            owner._pipeline_run.write_json('text_regions_merged.json', merged_docs)
        elif owner._current_image_context:
            await save_documents_fn(
                owner._current_image_context['subfolder'],
                {'text_regions_merged.json': merged_docs},
                owner.result_root,
            )

    # Detect before translation so grouped bubble text is translated as one flow.
    await owner._detect_speech_bubbles(config, ctx)

    # Apply pre-dictionary after textline merge
    pre_dict = load_dictionary_fn(owner.pre_dict)
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

    # -- Translation
    await owner._report_progress('translating')
    try:
        ctx.text_regions = await owner._run_text_translation(config, ctx)
    except Exception as e:
        logger.error(f"Error during translating:\n{traceback.format_exc()}")
        raise

    translations_doc = serialize_regions(ctx.text_regions if isinstance(ctx.text_regions, list) else [])
    ctx.result_documents['translations.json'] = translations_doc
    if owner._pipeline_run is not None:
        owner._pipeline_run.write_json('translations.json', translations_doc)
    elif owner._current_image_context:
        await save_documents_fn(
            owner._current_image_context['subfolder'],
            {'translations.json': translations_doc},
            owner.result_root,
        )

    await owner._report_progress('after-translating')

    if ctx.get('translation_error'):
        raise TranslationFailure(ctx.translation_error)
    if not ctx.text_regions:
        await owner._report_progress('error-translating', True)
        ctx.result = ctx.upscaled
        return await owner._revert_upscale(config, ctx)
    elif ctx.text_regions == 'cancel':
        await owner._report_progress('cancelled', True)
        ctx.result = ctx.upscaled
        return await owner._revert_upscale(config, ctx)

    # -- Mask refinement
    # (Delayed to take advantage of the region filtering done after ocr and translation)
    await owner._report_progress('mask-generation')
    try:
        bundle = await run_cpu_stage_fn(
            build_inpaint_masks,
            image=ctx.img_rgb,
            detector_textlines=getattr(ctx, 'textlines', None),
            detector_mask=getattr(ctx, 'mask_raw', None),
            text_regions=ctx.text_regions or [],
            bubble_detections=getattr(ctx, 'bubble_detections', None),
            config=config,
            page_geometry=getattr(ctx, 'page_geometry', None),
            priority=CPU_PRIORITY_BACKGROUND,
        )
        ctx.text_mask = bundle.text_mask
        ctx.bubble_mask = bundle.bubble_cleanup_mask
        ctx.detector_rescue_mask = bundle.detector_rescue_mask
        ctx.bubble_residual_mask = bundle.bubble_residual_mask
        ctx.protected_edge_mask = bundle.protected_edge_mask
        ctx.mask_bundle = bundle
        ctx.mask_profile = bundle.profile
        ctx.page_geometry = bundle.page_geometry
        if getattr(ctx, "result_documents", None) is not None:
            ctx.result_documents["profiling.json"] = bundle.profile
        ctx.mask = bundle.final_inpaint_mask
        ctx.inpaint_mask = bundle.final_inpaint_mask
    except Exception as e:
        logger.error(f"Error during mask-generation:\n{traceback.format_exc()}")
        raise

    if (owner.verbose or owner._pipeline_run is not None) and ctx.mask is not None:
        await owner._async_imwrite(owner._result_path('text_mask.png'), ctx.text_mask)
        await owner._async_imwrite(owner._result_path('bubble_mask.png'), ctx.bubble_mask)
        if getattr(ctx, 'detector_rescue_mask', None) is not None:
            await owner._async_imwrite(owner._result_path('detector_rescue_mask.png'), ctx.detector_rescue_mask)
        if getattr(ctx, 'bubble_residual_mask', None) is not None:
            await owner._async_imwrite(owner._result_path('bubble_residual_mask.png'), ctx.bubble_residual_mask)
        if getattr(ctx, 'protected_edge_mask', None) is not None:
            await owner._async_imwrite(owner._result_path('protected_bubble_edge.png'), ctx.protected_edge_mask)
        await owner._async_imwrite(owner._result_path('mask_final.png'), ctx.mask)
        await owner._async_imwrite(owner._result_path('inpaint_mask.png'), ctx.inpaint_mask)
        if ctx.img_rgb is not None and getattr(ctx, 'mask_bundle', None) is not None:
            try:
                overlay = create_mask_sources_overlay(ctx.img_rgb, ctx.mask_bundle)
                await owner._async_imwrite(owner._result_path('mask_sources_overlay.png'), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
            except Exception as ex:
                logger.warning(f"Could not save mask_sources_overlay.png: {ex}")
        if owner._pipeline_run is not None:
            owner._pipeline_run.refresh()

    ctx.cleanup_mask_diagnostics()
    bundle = None

    # Layout owns placement; inpainting and rendering only consume its result.
    try:
        await owner._report_progress('layout')
        transform_text_case = getattr(config.render, "transform_text_case", None)
        if transform_text_case:
            for region in (ctx.text_regions or []):
                if is_preserved_region(region):
                    region.translation = region.text
                elif getattr(region, "translation", None) and isinstance(region.translation, str):
                    region.translation = transform_text_case(region.translation)
        layout_font = owner.font_path or getattr(config.render, 'font_path', None) or get_default_eng_font()
        await run_cpu_stage_fn(layout_page, ctx, config, layout_font, priority=CPU_PRIORITY_BACKGROUND)
        if owner._pipeline_run is not None:
            owner._pipeline_run.write_json('layout.json', serialize_frozen_layout(
                ctx, config, layout_font,
                serialize_bubble_detections(getattr(ctx, 'bubble_detections', None) or []),
            ))
    except Exception as error:
        logger.warning('Production layout failed; preserving the existing render path: %s', error)

    # -- Inpainting
    await owner._report_progress('inpainting')
    try:
        ctx.img_inpainted = await owner._run_inpainting(config, ctx)
    except Exception as e:
        logger.error(f"Error during inpainting:\n{traceback.format_exc()}")
        raise
    ctx.gimp_mask = np.dstack((cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), ctx.mask))

    if owner.verbose or owner._pipeline_run is not None:
        try:
            inpainted_path = owner._result_path('inpainted.jpg')
            await asyncio.to_thread(save_jpeg_fn, ctx.img_inpainted, inpainted_path)
            try:
                os.unlink(owner._result_path('inpainted.png'))
            except FileNotFoundError:
                pass
            if owner._pipeline_run is not None:
                owner._pipeline_run.refresh()
        except Exception as e:
            logger.error(f"Error saving inpainted.jpg debug image: {e}")
            logger.debug(f"Exception details: {traceback.format_exc()}")

    if getattr(ctx, "_bubble_layout_ready", False):
        ctx.cleanup_mask_workspace()
    # -- Rendering
    await owner._report_progress('rendering')

    # 在rendering状态后立即发送文件夹信息，用于前端精确检查final.png
    if hasattr(owner, '_progress_hooks') and owner._current_image_context:
        folder_name = owner._current_image_context['subfolder']
        # 发送特殊格式的消息，前端可以解析
        await owner._report_progress(f'rendering_folder:{folder_name}')

    try:
        ctx.img_rendered = await owner._run_text_rendering(config, ctx)
    except Exception as e:
        logger.error(f"Error during rendering:\n{traceback.format_exc()}")
        raise

    await owner._report_progress('saving')
    ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)

    return await owner._revert_upscale(config, ctx)

# If upscaling was enabled and `revert_upscaling` is True, revert to input size
# Else leave `ctx` as-is
