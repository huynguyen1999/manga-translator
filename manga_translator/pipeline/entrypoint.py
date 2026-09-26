"""Single-image translation entry point."""

import asyncio
import time
import traceback
from datetime import datetime, timezone

from PIL import Image

from manga_translator.colorization import prepare as prepare_colorization
from manga_translator.config import Colorizer, Config
from manga_translator.detection import prepare as prepare_detection
from manga_translator.detection.bubble import prepare as prepare_bubble_detection
from manga_translator.inpainting import prepare as prepare_inpainting
from manga_translator.ocr import prepare as prepare_ocr
from manga_translator.pipeline.run import PipelineRun
from manga_translator.translators import prepare as prepare_translation
from manga_translator.upscaling import prepare as prepare_upscaling
from manga_translator.utils import Context, is_preserved_region


async def translate_image(owner, image: Image.Image, config: Config, image_name: str = None, skip_context_save: bool = False, *, logger, save_jpeg_fn) -> Context:
    """
    Translates a single image.

    :param image: Input image.
    :param config: Translation config.
    :param image_name: Deprecated parameter, kept for compatibility.
    :return: Translation context.
    """
    await owner._report_progress('running_pre_translation_hooks')
    for hook in owner._progress_hooks:
        try:
            hook('running_pre_translation_hooks', False)
        except Exception as e:
            logger.error(f"Error in progress hook: {e}")

    ctx = Context()
    ctx.input = image
    ctx.result = None
    ctx.result_documents = {}
    ctx.verbose = owner.verbose
    ctx.started_at_monotonic = time.monotonic()
    ctx.started_at_iso = datetime.now(timezone.utc).isoformat()
    owner._pipeline_run = None

    # 设置图片上下文以生成调试图片子文件夹
    owner._set_image_context(config, image)
    if owner._current_image_context:
        owner._current_image_context['started_at'] = ctx.started_at_iso

    owner._pipeline_run = PipelineRun(
        owner.result_root, owner._get_image_subfolder(), image, config
    )
    owner._pipeline_run.ctx = ctx
    owner._pipeline_run.translator = owner
    
    # 保存debug文件夹信息到Context中（用于Web模式的缓存访问）
    # 在web模式下总是保存，不仅仅是verbose模式
    ctx.debug_folder = owner._get_image_subfolder()
    
    # 保存原始输入图片用于对比和调试
    try:
        result_path = owner._result_path('input.jpg')
        await asyncio.to_thread(save_jpeg_fn, image, result_path)
    except Exception as e:
        logger.error(f"Error saving input.jpg debug image: {e}")
        logger.debug(f"Exception details: {traceback.format_exc()}")
    if owner._pipeline_run is not None:
        owner._pipeline_run.refresh()
        await owner._report_progress(f'debug_folder:{owner._get_image_subfolder()}')

    # preload and download models (not strictly necessary, remove to lazy load)
    if getattr(owner, 'models_ttl', 0) == 0:
        logger.info('Loading models')
        if config.upscale.upscale_ratio:
            await prepare_upscaling(config.upscale.upscaler)
        await prepare_detection(config.detector.detector)
        device = getattr(owner, 'device', 'cpu')
        await prepare_ocr(config.ocr.ocr, device)
        await prepare_inpainting(config.inpainter.inpainter, device)
        await prepare_translation(config.translator.translator_gen)
        if config.colorizer.colorizer != Colorizer.none:
            await prepare_colorization(config.colorizer.colorizer)
        if bool(getattr(getattr(config, 'bubble_detection', None), 'enabled', False)):
            await prepare_bubble_detection(config.bubble_detection, device)

    owner._log_memory_boundary("models_ready", ctx)
    # translate
    try:
        ctx = await owner._translate(config, ctx)
    except (Exception, asyncio.CancelledError) as e:
        if owner._pipeline_run is not None:
            if isinstance(e, asyncio.CancelledError):
                owner._pipeline_run.cancel("Stopped by user")
            else:
                owner._pipeline_run.fail(str(e))
            await owner._pipeline_run.checkpoint()
            owner._pipeline_run.release_runtime()
            owner._pipeline_run = None
        raise

    # 在翻译流程的最后保存翻译结果，确保保存的是最终结果（包括重试后的结果）
    # Save translation results at the end of translation process to ensure final results are saved
    if not skip_context_save and ctx.text_regions:
        # 汇总本页翻译，供下一页做上文
        page_translations = {
            r.text_raw if hasattr(r, "text_raw") else r.text: r.translation
            for r in ctx.text_regions if not is_preserved_region(r)
        }
        owner.all_page_translations.append(page_translations)

        # 同时保存原文用于并发模式的上下文
        page_original_texts = {
            i: (r.text_raw if hasattr(r, "text_raw") else r.text)
            for i, r in enumerate(ctx.text_regions) if not is_preserved_region(r)
        }
        owner._original_page_texts.append(page_original_texts)

    return ctx

