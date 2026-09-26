"""Prepared-page lifecycle entry point."""

from datetime import datetime, timezone

from PIL import Image

from manga_translator.config import Config
from manga_translator.pipeline.run import PipelineRun
from manga_translator.utils import Context


async def prepare_page(owner, image: Image.Image, config: Config, *, logger):
    """
    Pre-process one image through OCR and persist its canvas for later stages.
    """
    memory_optimization_enabled = not owner.disable_memory_optimization
    if memory_optimization_enabled:
        try:
            import psutil
            memory_percent = psutil.virtual_memory().percent
            if memory_percent > 85:
                logger.warning(f'High memory usage during pre-processing: {memory_percent:.1f}%')
                owner._empty_device_cache()
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f'Memory check failed: {e}')

    try:
        owner._set_image_context(config, image)
        subfolder = owner._get_image_subfolder()
        if owner._current_image_context:
            image_md5 = owner._current_image_context['file_md5']
            owner._save_current_image_context(image_md5)
            owner._current_image_context['started_at'] = datetime.now(timezone.utc).isoformat()
        owner._pipeline_run = PipelineRun(
            owner.result_root, subfolder, image, config
        )
        owner._pipeline_run.translator = owner
        ctx = await owner._translate_until_translation(image, config, prepare_canvas=True)
        if owner._pipeline_run is not None:
            owner._pipeline_run.ctx = ctx
        if owner._current_image_context:
            ctx.image_context = owner._current_image_context.copy()
            ctx.debug_folder = owner._current_image_context['subfolder']
        if owner._pipeline_run is not None:
            await owner._pipeline_run.checkpoint()
        if ctx.text_regions and hasattr(ctx, 'cleanup_intermediate'):
            ctx.cleanup_intermediate(keep_input=True)
            ctx.cleanup_detection_workspace()
        ctx.verbose = owner.verbose
        owner._empty_device_cache()
        return ctx
    except MemoryError as e:
        logger.error(f'Memory error in pre-processing image: {e}')
        if not memory_optimization_enabled:
            logger.error('Consider enabling memory optimization')
            raise
        try:
            logger.warning('Attempting fallback processing...')
            import copy
            recovery_config = copy.deepcopy(config)
            owner._empty_device_cache()
            owner._set_image_context(recovery_config, image)
            if owner._current_image_context:
                image_md5 = owner._current_image_context['file_md5']
                owner._save_current_image_context(image_md5)
            ctx = await owner._translate_until_translation(image, recovery_config, prepare_canvas=True)
            if owner._current_image_context:
                ctx.image_context = owner._current_image_context.copy()
                ctx.debug_folder = owner._current_image_context['subfolder']
            ctx.verbose = owner.verbose
            return ctx
        except Exception as retry_error:
            logger.error(f'Fallback processing also failed: {retry_error}')
            ctx = Context()
            ctx.input = image
            ctx.text_regions = []
            return ctx

