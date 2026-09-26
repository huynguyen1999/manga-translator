"""Post-translation mask, layout, inpainting, and rendering completion."""

import asyncio
import json
import os
import traceback

import cv2
import numpy as np
from PIL import Image

from manga_translator.config import Config
from manga_translator.detection.bubble import deserialize_bubble_detections, serialize_bubble_detections
from manga_translator.mask_builder import build_inpaint_masks
from manga_translator.pipeline.cpu import CPU_PRIORITY_BACKGROUND
from manga_translator.pipeline.run import deserialize_textlines
from manga_translator.rendering import get_default_eng_font
from manga_translator.rendering.bubble_layout import group_regions_by_bubbles
from manga_translator.rendering.layout import layout_page
from manga_translator.rendering.layout.frozen import serialize_frozen_layout
from manga_translator.translation_errors import TranslationFailure
from manga_translator.utils import Context, dump_image, is_preserved_region, load_image


async def complete_translation_pipeline(
    owner,
    ctx: Context,
    config: Config,
    *,
    logger,
    run_cpu_stage_fn,
    save_jpeg_fn,
) -> Context:
    """Finish post-translation processing and produce the rendered page."""
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

    # Rehydrate the lossless pre-translation canvas instead of retaining N
    # full image buffers per worker while the AI request is pending.
    if ctx.img_inpainted is None:
        inpainted_path = owner._result_path('inpainted.png')
        if not os.path.exists(inpainted_path):
            inpainted_path = owner._result_path('inpainted.jpg')
        if os.path.exists(inpainted_path):
            stored = cv2.imread(inpainted_path, cv2.IMREAD_COLOR)
            if stored is not None:
                ctx.img_inpainted = cv2.cvtColor(stored, cv2.COLOR_BGR2RGB)

    # Rehydrate the pre-inpaint working canvas for region-level restoration.
    if getattr(ctx, 'img_rgb', None) is None:
        original_canvas_path = owner._result_path('original_canvas.png')
        if os.path.exists(original_canvas_path):
            stored = cv2.imread(original_canvas_path, cv2.IMREAD_COLOR)
            if stored is not None:
                ctx.img_rgb = cv2.cvtColor(stored, cv2.COLOR_BGR2RGB)
    if getattr(ctx, 'upscaled', None) is None and getattr(ctx, 'img_rgb', None) is not None:
        ctx.upscaled = Image.fromarray(ctx.img_rgb)
    if getattr(ctx, 'upscaled', None) is None and getattr(ctx, 'input', None) is not None:
        ctx.upscaled = ctx.img_colorized if getattr(ctx, 'img_colorized', None) is not None else ctx.input
    if getattr(ctx, 'upscaled', None) is None and ctx.img_inpainted is not None:
        ctx.upscaled = Image.fromarray(ctx.img_inpainted)
    if getattr(ctx, 'img_rgb', None) is None and getattr(ctx, 'upscaled', None) is not None:
        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

    if not getattr(ctx, 'textlines', None):
        documents = getattr(ctx, 'result_documents', {}) or {}
        saved_textlines = (
            documents.get('ocr.json')
            or (owner._pipeline_run._document('ocr.json') if owner._pipeline_run else None)
            or documents.get('detection.json')
            or (owner._pipeline_run._document('detection.json') if owner._pipeline_run else None)
        )
        if saved_textlines is not None:
            ctx.textlines = deserialize_textlines(saved_textlines)
    if getattr(ctx, 'mask_raw', None) is None:
        mask_raw_path = owner._result_path('mask_raw.png')
        if os.path.isfile(mask_raw_path):
            ctx.mask_raw = cv2.imread(mask_raw_path, cv2.IMREAD_GRAYSCALE)

    if getattr(ctx, 'bubble_detections', None) is None:
        bd_path = owner._result_path('bubble_detections.json')
        documents = getattr(ctx, 'result_documents', {}) or {}
        bubble_document = (
            documents.get('bubble_detections.json')
            or (owner._pipeline_run._document('bubble_detections.json') if owner._pipeline_run else None)
        )
        if bubble_document is None and os.path.exists(bd_path):
            try:
                with open(bd_path, 'r', encoding='utf-8') as f:
                    bubble_document = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load existing bubble_detections.json: {e}")
        if bubble_document is not None and getattr(ctx, 'img_rgb', None) is not None:
            try:
                bds_raw = bubble_document
                ctx.bubble_detections = deserialize_bubble_detections(bds_raw, ctx.img_rgb.shape)
                ctx._bubble_detection_done = True
                group_regions = getattr(config.bubble_detection, 'group_regions', False)
                ctx.text_regions = group_regions_by_bubbles(
                    ctx.text_regions, ctx.bubble_detections, group=group_regions
                )
            except Exception as e:
                logger.warning(f"Failed to load existing bubble_detections.json: {e}")

    if (config.bubble_detection.enabled
            and not getattr(ctx, '_bubble_detection_done', False)):
        await owner._detect_speech_bubbles(config, ctx, report_progress=False)

    # A detector mask is input to refinement, not a completed inpainting mask.
    bundle = None
    if ctx.mask is None:
        mask_path = owner._result_path('mask_final.png')
        if os.path.exists(mask_path):
            try:
                ctx.mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            except Exception:
                pass
    if getattr(ctx, 'inpaint_mask', None) is None:
        inpaint_path = owner._result_path('inpaint_mask.png')
        if os.path.exists(inpaint_path):
            try:
                ctx.inpaint_mask = cv2.imread(inpaint_path, cv2.IMREAD_GRAYSCALE)
            except Exception:
                pass
        if ctx.inpaint_mask is None and ctx.mask is not None:
            ctx.inpaint_mask = ctx.mask

    if ctx.mask is None:
        if ctx.img_inpainted is not None:
            logger.info('Discarding cached inpainted image because its erasing mask is missing')
            ctx.img_inpainted = None
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
            if owner.verbose or owner._pipeline_run is not None:
                for name, mask in (
                    ('text_mask.png', ctx.text_mask),
                    ('bubble_mask.png', ctx.bubble_mask),
                    ('detector_rescue_mask.png', ctx.detector_rescue_mask),
                    ('bubble_residual_mask.png', ctx.bubble_residual_mask),
                    ('protected_bubble_edge.png', ctx.protected_edge_mask),
                    ('mask_final.png', ctx.mask),
                    ('inpaint_mask.png', ctx.inpaint_mask),
                ):
                    if mask is not None:
                        await owner._async_imwrite(owner._result_path(name), mask)
                if owner._pipeline_run is not None:
                    owner._pipeline_run.write_json('profiling.json', bundle.profile)
                    owner._pipeline_run.refresh()
        except Exception as e:
            logger.error(f"Error during mask-generation:\n{traceback.format_exc()}")
            raise


    ctx.cleanup_mask_diagnostics()
    bundle = None

    if getattr(ctx, 'text_regions', None) and getattr(ctx, 'img_rgb', None) is not None and not getattr(ctx, '_bubble_layout_ready', False):
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

    if owner.verbose and ctx.mask is not None:
        try:
            # 保存mask_final.png
            mask_final_path = owner._result_path('mask_final.png')
            success = await owner._async_imwrite(mask_final_path, ctx.mask)
            if not success:
                logger.warning(f"Failed to save debug image: {mask_final_path}")
        except Exception as e:
            logger.error(f"Error saving debug image (mask_final.png): {e}")
            logger.debug(f"Exception details: {traceback.format_exc()}")

    # -- Inpainting
    if ctx.img_inpainted is None:
        await owner._report_progress('inpainting')
        try:
            ctx.img_inpainted = await owner._run_inpainting(config, ctx)
        except Exception as e:
            logger.error(f"Error during inpainting:\n{traceback.format_exc()}")
            raise
        if ctx.img_inpainted is not None and (owner.verbose or owner._pipeline_run is not None):
            await asyncio.to_thread(
                save_jpeg_fn, ctx.img_inpainted, owner._result_path('inpainted.jpg')
            )
    if ctx.mask is not None and ctx.img_inpainted is not None:
        ctx.gimp_mask = np.dstack((cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), ctx.mask))

    if owner.verbose:
        try:
            inpainted_path = owner._result_path('inpainted.jpg')
            await asyncio.to_thread(save_jpeg_fn, ctx.img_inpainted, inpainted_path)
            try:
                os.unlink(owner._result_path('inpainted.png'))
            except FileNotFoundError:
                pass
        except Exception as e:
            logger.error(f"Error saving inpainted.jpg debug image: {e}")
            logger.debug(f"Exception details: {traceback.format_exc()}")

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

    await owner._report_progress('finished', True)
    ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)

    # 保存debug文件夹信息到Context中（用于Web模式的缓存访问）
    if owner.verbose:
        ctx.debug_folder = owner._get_image_subfolder()

    res_ctx = await owner._revert_upscale(config, ctx)
    if hasattr(res_ctx, 'cleanup_intermediate'):
        res_ctx.cleanup_intermediate(keep_input=True)
        res_ctx.cleanup_detection_workspace()
    owner._empty_device_cache()
    return res_ctx
