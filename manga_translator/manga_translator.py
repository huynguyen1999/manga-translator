import asyncio
import cv2
from datetime import datetime, timezone
import json
try:
    import langcodes
except ImportError:
    langcodes = None
import os
import regex as re
import time
import torch
import logging
import sys
import traceback
import uuid
import numpy as np
from PIL import Image
from typing import Optional, Any, List, Callable, Awaitable
try:
    import py3langid as langid
except ImportError:
    langid = None

from .config import Config, Colorizer, Detector, Translator, Renderer, Inpainter, Ocr
from .utils import (
    BASE_PATH,
    LANGUAGE_ORIENTATION_PRESETS,
    ModelWrapper,
    Context,
    load_image,
    dump_image,
    contains_linguistic_ocr_text,
    is_meaningful_ocr_text,
    is_preserved_region,
    classify_numeric_ocr_region,
    NumericClassification,
    rect_distance,
    sort_regions,
)

from .detection import (
    dispatch as dispatch_detection,
    dispatch_batch as dispatch_detection_batch,
    prepare as prepare_detection,
    unload as unload_detection,
)
from .detection.bubble import (
    BubbleDetection,
    dispatch as dispatch_bubble_detection,
    dispatch_batch as dispatch_bubble_detection_batch,
    detect as detect_bubbles,
    prepare as prepare_bubble_detection,
    unload as unload_bubble_detection,
)
from .upscaling import dispatch as dispatch_upscaling, prepare as prepare_upscaling, unload as unload_upscaling
from .ocr import dispatch as dispatch_ocr, dispatch_batch as dispatch_ocr_batch, prepare as prepare_ocr, unload as unload_ocr
from .textline_merge import dispatch as dispatch_textline_merge
from .typography import analyze_source_typography
from .mask_refinement import dispatch as dispatch_mask_refinement
from .inpainting import dispatch_batch as dispatch_inpainting_batch, prepare as prepare_inpainting, unload as unload_inpainting
from .translators import (
    GPT_TRANSLATORS,
    dispatch as dispatch_translation,
    dispatch_structured as dispatch_structured_translation,
    prepare as prepare_translation,
    unload as unload_translation,
)
from .translators.common import ISO_639_1_TO_VALID_LANGUAGES
from .translators.structured import StructuredTranslationError
from .translation_validation import (
    check_repetition_hallucination,
    check_target_language_ratio,
    retry_translation_with_validation,
    validate_translation,
)
from .translation_context import build_previous_page_context
from .translation_dispatch import dispatch_with_context
from .translation_errors import TranslationFailure
from .translation_retry import translate_page_with_retries
from .translation_stage import run_text_translation
from .text_grouping_stage import merge_textlines
from .translation_postprocessing import postprocess_translation
from .detection_stage import run_detection, run_detection_batch
from .ocr_stage import finish_ocr_textlines, run_ocr, run_ocr_batch
from .bubble_detection_stage import run_bubble_detection, run_bubble_detection_batch
from .inpainting_stage import run_inpainting_batch
from .image_preprocessing_stage import run_colorizer, run_upscaling, run_upscaling_batch
from .rendering_stage import run_text_rendering
from .translators.gemini_keys import GeminiRetryExhausted
from .colorization import (
    dispatch as dispatch_colorization,
    prepare as prepare_colorization,
    unload as unload_colorization,
    is_image_colored,
)
from .rendering import dispatch as dispatch_rendering, dispatch_eng_render, dispatch_eng_render_pillow, get_default_eng_font, _composite_box_to_image, render_page
from .rendering.bubble_layout import group_regions_by_bubbles, prepare_bubbles, restore_original, encode_safe_shape, encode_rendered_box
from .rendering.layout import layout_page, PlacementMode
from .mask_builder import build_inpaint_masks, create_mask_sources_overlay
from .geometry.bubbles import prepare_page_geometry
from .detection.bubble import deserialize_bubble_detections
from .pipeline.context import (
    build_result_metadata, get_image_subfolder, restore_image_context,
    result_path, save_current_image_context, set_image_context,
)
from .pipeline.orchestrator import translate_until_translation
from .pipeline.batch.coordinator import batch_translate_contexts
from .pipeline.batch.runner import translate_prepared_contexts
from .pipeline.batch.workflow import (
    translate_batch as translate_batch_impl,
    translate_and_render_batch as translate_and_render_batch_impl,
)
from .pipeline.batch.translation import (
    batch_translate_texts, concurrent_translate_contexts,
)
from .pipeline.batch.text_extraction import extract_text_batch as extract_text_batch_impl
from .pipeline.single_page import translate_page
from .pipeline.preparation import prepare_page
from .pipeline.page_render import render_prepared_page
from .pipeline.entrypoint import translate_image
from .pipeline.completion import complete_translation_pipeline
from .pipeline.finalization import revert_upscale
from .pipeline.progress import (
    add_logger_hook as add_logger_hook_impl,
    add_progress_hook as add_progress_hook_impl,
    emit_progress as emit_progress_impl,
    report_progress as report_progress_impl,
)
from .pipeline.lifecycle import (
    empty_device_cache as empty_device_cache_impl,
    model_cleanup_job as model_cleanup_job_impl,
    unload_model as unload_model_impl,
)
from .pipeline.run import (
    PipelineRun,
    deserialize_textlines,
    save_result_documents,
    serialize_regions,
)
from .pipeline.cpu import (
    CPU_PRIORITY_BACKGROUND,
    CPU_PRIORITY_NORMAL,
    run_cpu_stage,
)
from .utils.model_cache import get_model_executor, model_operation
from .utils.device_memory import empty_device_cache, configure_device_memory_limits, log_memory_stats
from .utils.image_storage import save_jpeg

# Will be overwritten by __main__.py if module is being run directly (with python -m)
logger = logging.getLogger('manga_translator')

# 全局console实例，用于日志重定向
_global_console = None
_log_console = None

def set_main_logger(l):
    global logger
    logger = l



class TranslationInterrupt(Exception):
    """
    Can be raised from within a progress hook to prematurely terminate
    the translation.
    """


from .pipeline.model_execution import _GLOBAL_MPS_LOCK, _MPSLock, mps_call

_dictionary_cache = {}

def load_dictionary(file_path):
    if not file_path or not os.path.exists(file_path):
        return []
    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        mtime = 0
    cache_key = (file_path, mtime)
    if cache_key in _dictionary_cache:
        return _dictionary_cache[cache_key]

    dictionary = []
    with open(file_path, 'r', encoding='utf-8') as file:
        for line_number, line in enumerate(file, start=1):
            # Ignore empty lines and lines starting with '#' or '//'
            if not line.strip() or line.strip().startswith('#') or line.strip().startswith('//'):
                continue
            # Remove comment parts
            line = line.split('#')[0].strip()
            line = line.split('//')[0].strip()
            parts = line.split()
            if len(parts) == 1:
                # If there is only the left part, the right part defaults to an empty string, meaning delete the left part
                pattern = re.compile(parts[0])
                dictionary.append((pattern, '', line_number))
            elif len(parts) == 2:
                # If both left and right parts are present, perform the replacement
                pattern = re.compile(parts[0])
                dictionary.append((pattern, parts[1], line_number))
            else:
                logger.error(f'Invalid dictionary entry at line {line_number}: {line.strip()}')
    _dictionary_cache[cache_key] = dictionary
    return dictionary

def apply_dictionary(text, dictionary):
    for pattern, value, line_number in dictionary:
        original_text = text  
        text = pattern.sub(value, text)
        if text != original_text:  
            logger.info(f'Line {line_number}: Replaced "{original_text}" with "{text}" using pattern "{pattern.pattern}" and value "{value}"')
    return text

class MangaTranslator:
    verbose: bool
    ignore_errors: bool
    _gpu_limited_memory: bool
    device: Optional[str]
    kernel_size: Optional[int]
    models_ttl: int
    _progress_hooks: list[Any]
    result_sub_folder: str
    batch_size: int

    def __init__(self, params: dict = None):
        params = params or {}
        self.pre_dict = params.get('pre_dict', None)
        self.post_dict = params.get('post_dict', None)
        self.font_path = None
        self.use_mtpe = False
        self.kernel_size = None
        self.device = None
        self._gpu_limited_memory = False
        self.ignore_errors = False
        self.verbose = False
        self._pipeline_run = None
        self.models_ttl = 0
        self.batch_size = 20

        self._progress_hooks = []
        self._add_logger_hook()

        params = params or {}
        
        self._batch_contexts = []  # 存储批量处理的上下文
        self._batch_configs = []   # 存储批量处理的配置
        self.disable_memory_optimization = params.get('disable_memory_optimization', False)
        # batch_concurrent 会在 parse_init_params 中验证并设置
        self.batch_concurrent = params.get('batch_concurrent', False)
        
        self.parse_init_params(params)
        self.result_sub_folder = ''
        self.result_root = os.path.abspath(
            params.get('result_root') or os.getenv('MANGA_RESULT_ROOT') or os.path.join(BASE_PATH, 'result')
        )

        # The flag below controls whether to allow TF32 on matmul. This flag defaults to False
        # in PyTorch 1.12 and later.
        torch.backends.cuda.matmul.allow_tf32 = True

        # The flag below controls whether to allow TF32 on cuDNN. This flag defaults to True.
        torch.backends.cudnn.allow_tf32 = True

        self._model_usage_timestamps = {}
        self._model_cleanup_task = None
        self.prep_manual = params.get('prep_manual', None)
        self.context_size = params.get('context_size', 0)
        self.all_page_translations = []
        self._original_page_texts = []  # 存储原文页面数据，用于并发模式下的上下文

        # 调试图片管理相关属性
        self._current_image_context = None  # 存储当前处理图片的上下文信息
        self._saved_image_contexts = {}     # 存储批量处理中每个图片的上下文信息
        

    def parse_init_params(self, params: dict):
        self.verbose = params.get('verbose', False)
        self.use_mtpe = params.get('use_mtpe', False)
        self.font_path = params.get('font_path', None)
        self.models_ttl = params.get('models_ttl', 0)
        self.batch_size = params.get('batch_size', 20)
        
        # 验证batch_concurrent参数
        if self.batch_concurrent and self.batch_size < 2:
            logger.warning('--batch-concurrent requires --batch-size to be at least 2. When batch_size is 1, concurrent mode has no effect.')
            logger.info('Suggestion: Use --batch-size 2 (or higher) with --batch-concurrent, or remove --batch-concurrent flag.')
            # 自动禁用并发模式
            self.batch_concurrent = False
            
        self.ignore_errors = params.get('ignore_errors', False)
        # check xpu for intel arc, mps for apple silicon, or cuda for nvidia
        use_gpu = params.get('use_gpu', False)
        use_gpu_limited = params.get('use_gpu_limited', False)
        # Select a GPU only when the caller explicitly requests one.
        mps_available = torch.backends.mps.is_available()
        xpu_available = torch.xpu.is_available()
        cuda_available = torch.cuda.is_available()
        if xpu_available:
            best_device = 'xpu'
        elif mps_available:
            best_device = 'mps'
        elif cuda_available:
            best_device = 'cuda'
        else:
            best_device = 'cpu'

        if use_gpu:
            self.device = best_device
        elif use_gpu_limited:
            self.device = best_device
        else:
            self.device = 'cpu'

        self._gpu_limited_memory = use_gpu_limited
        if self._gpu_limited_memory and not self.using_gpu:
            self.device = best_device
        if self.device == 'mps':
            configure_device_memory_limits(self.device)
        logger.info(
            f'Compute device selected: device={self.device} requested_gpu={use_gpu} '
            f'mps_available={mps_available} cuda_available={cuda_available} xpu_available={xpu_available}'
        )
        if use_gpu and (not cuda_available and not mps_available and not xpu_available):
            raise Exception(
                'CUDA or Metal compatible device could not be found in torch whilst --use-gpu args was set.\n'
                'Is the correct pytorch version installed? (See https://pytorch.org/)')
        if params.get('model_dir'):
            ModelWrapper._MODEL_DIR = params.get('model_dir')
        #todo: fix why is kernel size loaded in the constructor
        self.kernel_size = int(params.get('kernel_size') or 3)
        # Set input files
        self.input_files = params.get('input', [])
        # Set save_text
        self.save_text = params.get('save_text', False)
        # Set load_text
        self.load_text = params.get('load_text', False)
        
        # batch_concurrent 已在初始化时设置并验证
        

        
    def _set_image_context(self, config: Config, image=None):
        """设置当前处理图片的上下文信息，用于生成调试图片子文件夹"""
        return set_image_context(self, config, image)

    def _build_result_metadata(self, config: Config, ctx: Context) -> dict:
        return build_result_metadata(self, config, ctx)

    def _get_image_subfolder(self) -> str:
        """获取当前图片的调试子文件夹名"""
        return get_image_subfolder(self)

    def _save_current_image_context(self, image_md5: str):
        """保存当前图片上下文，用于批量处理中保持一致性"""
        return save_current_image_context(self, image_md5)

    def _restore_image_context(self, image_md5: str):
        """恢复保存的图片上下文"""
        return restore_image_context(self, image_md5)

    async def _async_imwrite(self, path: str, img: np.ndarray):
        if not path:
            return False
        return await asyncio.to_thread(cv2.imwrite, path, img)

    @property
    def using_gpu(self):
        return self.device.startswith('cuda') or self.device == 'mps' or self.device == 'xpu'

    @property
    def result_root(self) -> str:
        return getattr(
            self,
            '_result_root',
            os.path.abspath(os.getenv('MANGA_RESULT_ROOT') or os.path.join(BASE_PATH, 'result')),
        )

    @result_root.setter
    def result_root(self, value: str):
        self._result_root = os.path.abspath(value) if value else os.path.abspath(os.path.join(BASE_PATH, 'result'))

    def _empty_device_cache(self):
        return empty_device_cache_impl(self, get_model_executor, empty_device_cache)

    def _log_memory_boundary(self, stage: str, ctx: Context | None = None):
        run = getattr(self, '_pipeline_run', None)
        if run is not None:
            batch_id, page_id = run._memory_identity()
        else:
            image_context = getattr(ctx, 'image_context', None) or self._current_image_context or {}
            batch_id = getattr(self, '_memory_batch_id', None)
            page_id = image_context.get('file_md5') or getattr(ctx, 'debug_folder', None)
        return log_memory_stats(
            stage,
            device=getattr(self, 'device', None),
            batch_id=batch_id,
            page_id=page_id,
        )

    def clear_batch_state(self, batch_id: str | None = None):
        """Drop translation context and image metadata when a manga batch ends."""
        self.all_page_translations.clear()
        self._original_page_texts.clear()
        self._saved_image_contexts.clear()
        self._current_image_context = None
        self._memory_batch_id = batch_id

    async def translate(self, image: Image.Image, config: Config, image_name: str = None, skip_context_save: bool = False) -> Context:
        return await translate_image(
            self, image, config, image_name, skip_context_save,
            logger=logger, save_jpeg_fn=save_jpeg,
        )

    async def extract_text(self, image: Image.Image, config: Config) -> Context:
        """Run the preprocessing pipeline through OCR without translation or rendering."""
        self._set_image_context(config, image)
        ctx = await self._translate_until_translation(image, config, prepare_canvas=False)
        ctx.img_rgb = None
        ctx.img_alpha = None
        ctx.mask_raw = None
        ctx.mask = None
        ctx.upscaled = None
        ctx.input = None
        ctx.result = None
        return ctx

    async def extract_text_batch(
        self,
        images_with_configs: List[tuple[Image.Image, Config]],
        batch_size: int = 3,
        on_progress: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> List[Context]:
        return await extract_text_batch_impl(
            self,
            images_with_configs,
            batch_size,
            on_progress,
            load_dictionary_fn=load_dictionary,
            apply_dictionary_fn=apply_dictionary,
        )

    async def _translate(self, config: Config, ctx: Context) -> Context:
        return await translate_page(
            self,
            config,
            ctx,
            logger=logger,
            run_cpu_stage_fn=run_cpu_stage,
            save_jpeg_fn=save_jpeg,
            save_documents_fn=save_result_documents,
            load_dictionary_fn=load_dictionary,
            apply_dictionary_fn=apply_dictionary,
        )

    async def _revert_upscale(self, config: Config, ctx: Context):
        return await revert_upscale(
            self, config, ctx, logger=logger, save_jpeg=save_jpeg,
            save_result_documents=save_result_documents,
        )

    # ------------------------------------------------------------------ #
    # MPS fallback & concurrency helper                                   #
    # ------------------------------------------------------------------ #
    async def _mps_call(self, coro_fn, *args, **kwargs):
        """
        Call an async model-dispatch function, automatically falling back to CPU
        if the MPS backend reports an unsupported operation. Standalone MPS calls
        are serialized; in-process calls use the shared executor's limit.

        Usage:
            result = await self._mps_call(dispatch_detection, arg1, arg2, ..., self.device, verbose)

        The last positional argument whose value equals ``self.device`` is
        replaced with 'cpu' on retry, so callers don't need to change anything.
        """
        return await mps_call(
            self, coro_fn, args, kwargs, logger=logger,
            get_model_executor_fn=get_model_executor, global_mps_lock=_GLOBAL_MPS_LOCK,
        )

    async def _run_colorizer(self, config: Config, ctx: Context):
        return await run_colorizer(
            self,
            config,
            ctx,
            dispatch=dispatch_colorization,
        )

    async def _run_upscaling(self, config: Config, ctx: Context):
        return await run_upscaling(self, config, ctx, dispatch=dispatch_upscaling)

    async def _run_upscaling_batch(self, configs: list[Config], contexts: list[Context]):
        return await run_upscaling_batch(
            self,
            configs,
            contexts,
            dispatch=dispatch_upscaling,
        )

    async def _run_detection(self, config: Config, ctx: Context):
        return await run_detection(self, config, ctx, dispatch_detection=dispatch_detection)

    async def _run_detection_batch(self, configs: list[Config], contexts: list[Context]):
        return await run_detection_batch(
            self,
            configs,
            contexts,
            dispatch_detection_batch=dispatch_detection_batch,
            logger=logger,
        )

    async def _unload_model(self, tool: str, model: str):
        await unload_model_impl(
            self,
            tool,
            model,
            logger=logger,
            get_model_executor=get_model_executor,
            unloaders={
                'colorization': unload_colorization,
                'detection': unload_detection,
                'inpainting': unload_inpainting,
                'ocr': unload_ocr,
                'upscaling': unload_upscaling,
                'translation': unload_translation,
                'bubble_detection': unload_bubble_detection,
            },
        )

    # Background models cleanup job.
    async def _model_cleanup_job(self):
        await model_cleanup_job_impl(
            self,
            sleep=asyncio.sleep,
            time=time.time,
            get_model_executor=get_model_executor,
        )

    @model_operation
    async def _run_ocr(self, config: Config, ctx: Context):
        return await run_ocr(
            self,
            config,
            ctx,
            dispatch_ocr=dispatch_ocr,
            finish_textlines=self._finish_ocr_textlines,
        )

    @model_operation
    async def _run_ocr_batch(self, pages: list[tuple[Context, Config]]):
        return await run_ocr_batch(
            self,
            pages,
            dispatch_ocr_batch=dispatch_ocr_batch,
            finish_textlines=self._finish_ocr_textlines,
            logger=logger,
        )

    @staticmethod
    def _finish_ocr_textlines(textlines, config: Config, ctx: Context):
        return finish_ocr_textlines(
            textlines,
            config,
            ctx,
            analyze_typography=analyze_source_typography,
        )

    async def _run_textline_merge(self, config: Config, ctx: Context):
        return await merge_textlines(
            self,
            config,
            ctx,
            logger=logger,
            langid=langid,
            langcodes=langcodes,
            dispatch_textline_merge=dispatch_textline_merge,
            save_result_documents=save_result_documents,
        )

    def _build_prev_context(self, use_original_text=False, current_page_index=None, batch_index=None, batch_original_texts=None):
        """Keep the existing context-builder call surface for translation paths."""
        return build_previous_page_context(
            self.context_size,
            self.all_page_translations,
            getattr(self, '_original_page_texts', None),
            hasattr(self, '_original_page_texts'),
            use_original_text,
            current_page_index,
            batch_index,
            batch_original_texts,
        )

    async def _dispatch_with_context(self, config: Config, texts: list[str], ctx: Context):
        """Keep the existing translator dispatch surface for pipeline callers."""
        return await dispatch_with_context(self, config, texts, ctx, logger=logger)

    @staticmethod
    def _uses_gemini(config: Config) -> bool:
        return any(
            key == Translator.gemini
            for key, _ in config.translator.translator_gen.chain
        )

    async def _translate_page_with_retries(self, config, ctx, translations=None):
        """Require one usable translation per region before any source text is erased."""
        return await translate_page_with_retries(self, config, ctx, translations, logger=logger)

    async def _run_text_translation(self, config: Config, ctx: Context):
        return await run_text_translation(
            self,
            config,
            ctx,
            load_dictionary,
            apply_dictionary,
            logger=logger,
        )

    def _prepare_bubble_layout(self, config: Config, ctx: Context):
        """Calculate translated-text placement from prepared bubble geometry."""
        if not getattr(ctx, 'text_regions', None) or getattr(ctx, 'img_rgb', None) is None:
            return False
        transform_text_case = getattr(config.render, "transform_text_case", None)
        if transform_text_case:
            for region in (ctx.text_regions or []):
                if is_preserved_region(region):
                    region.translation = region.text
                elif getattr(region, "translation", None) and isinstance(region.translation, str):
                    region.translation = transform_text_case(region.translation)
        if getattr(ctx, 'inpaint_mask', None) is None and getattr(ctx, 'mask', None) is not None:
            ctx.inpaint_mask = ctx.mask
        active_font = self.font_path or getattr(config.render, 'font_path', None) or get_default_eng_font()
        layout_page(ctx, config, active_font)
        return True

    async def _run_bubble_detection_batch(self, configs: list[Config], contexts: list[Context]):
        return await run_bubble_detection_batch(
            self,
            configs,
            contexts,
            dispatch_batch=dispatch_bubble_detection_batch,
        )

    async def _detect_speech_bubbles(
        self,
        config: Config,
        ctx: Context,
        report_progress: bool = True,
        precomputed_detections: list[BubbleDetection] | None = None,
    ):
        return await run_bubble_detection(
            self,
            config,
            ctx,
            report_progress,
            precomputed_detections,
            detect_bubbles=detect_bubbles,
            dispatch_detection=dispatch_bubble_detection,
            group_regions_by_bubbles=group_regions_by_bubbles,
            logger=logger,
        )

    def _install_prepared_bubbles(self, ctx, prepared):
        if not any(getattr(region, '_bubble_mask', None) is not None for region in ctx.text_regions):
            ctx.text_regions = prepared
            return
        prepared_by_id = {
            str(getattr(region, 'region_id', '')): region
            for region in prepared
            if getattr(region, 'region_id', None)
        }
        prepared_by_mask = {id(getattr(region, '_bubble_mask', None)): region for region in prepared}
        ctx.text_regions = [
            prepared_by_id.get(str(getattr(region, 'region_id', '')))
            or prepared_by_mask.get(id(getattr(region, '_bubble_mask', None)), region)
            if getattr(region, '_bubble_mask', None) is not None else region
            for region in ctx.text_regions
        ]

    async def _run_mask_refinement(self, config: Config, ctx: Context):
        return await dispatch_mask_refinement(
            ctx.text_regions, ctx.img_rgb, ctx.mask_raw, 'fit_text',
            config.mask_dilation_offset, config.ocr.ignore_bubble, self.verbose, self.kernel_size,
        )

    async def _run_inpainting(self, config: Config, ctx: Context):
        return (await self._run_inpainting_batch([config], [ctx]))[0]

    async def _run_inpainting_batch(self, configs: list[Config], contexts: list[Context]):
        return await run_inpainting_batch(
            self,
            configs,
            contexts,
            dispatch_batch=dispatch_inpainting_batch,
            logger=logger,
        )

    async def _run_text_rendering(self, config: Config, ctx: Context):
        return await run_text_rendering(
            self,
            config,
            ctx,
            logger=logger,
            run_cpu_stage_fn=run_cpu_stage,
            render_page_fn=render_page,
        )

    def _result_path(self, path: str) -> str:
        """
        Returns path to result folder where intermediate images are saved when using verbose flag
        or web mode input/result images are cached.
        """
        return result_path(self, path)

    def add_progress_hook(self, ph):
        add_progress_hook_impl(self, ph)

    async def _emit_progress(self, state: str, finished: bool = False):
        await emit_progress_impl(self, state, finished)

    async def _report_progress(self, state: str, finished: bool = False):
        await report_progress_impl(self, state, finished)

    def _add_logger_hook(self):
        add_logger_hook_impl(self, logger)

    async def prepare(self, image: Image.Image, config: Config) -> Context:
        return await prepare_page(self, image, config, logger=logger)

    async def translate_batch_contexts(
        self, contexts_with_configs: List[tuple], batch_size: int = None
    ) -> List[tuple]:
        """
        Translate an aggregate batch of prepared contexts and return translated (ctx, config) pairs.
        """
        return await translate_prepared_contexts(
            self,
            contexts_with_configs,
            batch_size,
            logger=logger,
            gpt_translators=GPT_TRANSLATORS,
            serialize_regions_fn=serialize_regions,
        )

    async def render(self, ctx: Context, config: Config) -> Context:
        return await render_prepared_page(self, ctx, config, logger=logger)

    async def render_saved(self, ctx: Context, config: Config) -> Context:
        """Render persisted translations without preparing or translating again."""
        if getattr(ctx, 'image_context', None):
            self._current_image_context = ctx.image_context.copy()
        try:
            ctx.result = Image.fromarray(await self._run_text_rendering(config, ctx))
            return ctx
        except Exception as error:
            logger.error(f'Saved render error: {error}')
            ctx.translation_error = str(error)
            ctx.result = None
            return ctx

    async def translate_and_render_batch(
        self, contexts_with_configs: List[tuple], batch_size: int = None
    ) -> List[Context]:
        """Translate an aggregate batch of prepared contexts and render the final images."""
        return await translate_and_render_batch_impl(
            self, contexts_with_configs, batch_size, logger=logger
        )

    async def translate_batch(self, images_with_configs: List[tuple], batch_size: int = None, image_names: List[str] = None) -> List[Context]:
        """
        批量翻译多张图片，在翻译阶段进行批量处理以提高效率
        Args:
            images_with_configs: List of (image, config) tuples
            batch_size: 批量大小，如果为None则使用实例的batch_size
            image_names: 已弃用的参数，保留用于兼容性
        Returns:
            List of Context objects with translation results
        """
        return await translate_batch_impl(
            self, images_with_configs, batch_size, image_names, logger=logger
        )

    async def _translate_until_translation(
        self, image: Image.Image, config: Config, prepare_canvas: bool = True
    ) -> Context:
        """
        执行翻译之前的所有步骤（彩色化、上采样、检测、OCR、文本行合并）
        """
        return await translate_until_translation(
            self,
            image,
            config,
            prepare_canvas,
            logger=logger,
            save_jpeg_fn=save_jpeg,
            save_documents_fn=save_result_documents,
            load_dictionary_fn=load_dictionary,
            apply_dictionary_fn=apply_dictionary,
        )

    async def _batch_translate_contexts(self, contexts_with_configs: List[tuple], batch_size: int) -> List[tuple]:
        """
        批量处理翻译步骤，防止内存溢出
        """
        return await batch_translate_contexts(
            self, contexts_with_configs, batch_size, logger=logger
        )

    async def _concurrent_translate_contexts(self, contexts_with_configs: List[tuple]) -> List[tuple]:
        '\n        并发处理翻译步骤，为每个图片单独发送翻译请求，避免合并大批次\n        '
        return await concurrent_translate_contexts(
            self, contexts_with_configs, logger=logger
        )
    async def _batch_translate_texts(self, texts: List[str], config: Config, ctx: Context, batch_contexts: List[Context] = None, page_index: int = None, batch_index: int = None, batch_original_texts: List[dict] = None, text_ids: List[str] = None) -> List[str]:
        '\n        批量翻译文本列表，使用现有的翻译器接口\n\n        Args:\n            texts: 要翻译的文本列表\n            config: 配置对象\n            ctx: 上下文对象\n            batch_contexts: 批处理上下文列表\n            page_index: 当前页面索引，用于并发模式下的上下文计算\n            batch_index: 当前页面在批次中的索引\n            batch_original_texts: 当前批次的原文数据\n        '
        return await batch_translate_texts(
            self, texts, config, ctx, batch_contexts, page_index, batch_index,
            batch_original_texts, text_ids, logger=logger
        )
    async def _apply_post_translation_processing(self, ctx: Context, config: Config) -> List:
        """
        应用翻译后处理逻辑（括号修正、过滤等）
        """
        return await postprocess_translation(
            self,
            ctx,
            config,
            load_dictionary,
            apply_dictionary,
            logger=logger,
        )

    async def _complete_translation_pipeline(self, ctx: Context, config: Config) -> Context:
        '\n    完成翻译后的处理步骤（掩码细化、修复、渲染）\n    '
        return await complete_translation_pipeline(
            self,
            ctx,
            config,
            logger=logger,
            run_cpu_stage_fn=run_cpu_stage,
            save_jpeg_fn=save_jpeg,
        )

    async def _check_repetition_hallucination(self, text: str, threshold: int = 5, silent: bool = False) -> bool:
        """Keep the existing async method surface for translation callers."""
        return check_repetition_hallucination(text, threshold, silent, logger=logger)

    async def _check_target_language_ratio(self, text_regions: List, target_lang: str, min_ratio: float = 0.5) -> bool:
        """Keep the existing async method surface for translation callers."""
        return check_target_language_ratio(text_regions, target_lang, min_ratio, logger=logger)

    async def _validate_translation(self, original_text: str, translation: str, target_lang: str, config, ctx: Context = None, silent: bool = False, page_lang_check_result: bool = None) -> bool:
        return await validate_translation(
            self,
            original_text,
            translation,
            target_lang,
            config,
            ctx,
            silent,
            page_lang_check_result,
            logger=logger,
        )

    async def _retry_translation_with_validation(self, region, config: Config, ctx: Context) -> str:
        return await retry_translation_with_validation(self, region, config, ctx, logger=logger)
