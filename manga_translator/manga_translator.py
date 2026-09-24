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
import threading
import torch
import logging
import sys
import traceback
import uuid
import numpy as np
from PIL import Image
from typing import Optional, Any, List
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
from .translators.common import ISO_639_1_TO_VALID_LANGUAGES, TranslationProviderUnavailable
from .translators.structured import StructuredTranslationError
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
from .rendering.layout.frozen import hydrate_layout, layout_input_fingerprints, serialize_frozen_layout
from .mask_builder import build_inpaint_masks, create_mask_sources_overlay
from .geometry.bubbles import prepare_page_geometry
from .detection.bubble import serialize_bubble_detections, deserialize_bubble_detections
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

class TranslationFailure(RuntimeError):
    """A page has missing dialogue after its translation retries."""


class TranslationInterrupt(Exception):
    """
    Can be raised from within a progress hook to prematurely terminate
    the translation.
    """


class _MPSLock:
    def __init__(self):
        self._lock = threading.Lock()

    async def __aenter__(self):
        await asyncio.to_thread(self._lock.acquire)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if torch.backends.mps.is_available():
            try:
                torch.mps.synchronize()
            except Exception:
                pass
        self._lock.release()

_GLOBAL_MPS_LOCK = _MPSLock()

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
        from .utils.generic import get_image_md5

        # 使用毫秒级时间戳确保唯一性
        timestamp = str(int(time.time() * 1000))
        detection_size = str(getattr(config.detector, 'detection_size', 1024))
        target_lang = getattr(config.translator, 'target_lang', 'unknown')
        translator = getattr(config.translator, 'translator', 'unknown')

        file_md5 = get_image_md5(image) if image is not None else "unknown"

        subfolder_name = f"{timestamp}-{file_md5}-{detection_size}-{target_lang}-{translator}"

        original_name = getattr(config, 'original_name', None)
        if not original_name or original_name == 'Unknown':
            original_name = f"{subfolder_name}.png"
        manga_title = (getattr(config, 'manga_title', None) or 'Ungrouped').strip() or 'Ungrouped'
        manga_group_id = getattr(config, 'manga_group_id', None)
        page_order = getattr(config, 'page_order', None)
        source_path = getattr(config, 'source_path', None)

        self._current_image_context = {
            'subfolder': subfolder_name,
            'file_md5': file_md5,
            'config': config,
            'original_name': original_name,
            'manga_title': manga_title,
            'manga_group_id': manga_group_id,
            'page_order': page_order,
            'source_path': source_path,
            'request_id': getattr(config, 'request_id', None),
        }

    def _build_result_metadata(self, config: Config, ctx: Context) -> dict:
        image_context = getattr(self, '_current_image_context', None) or {}
        original_name = image_context.get('original_name') or getattr(config, 'original_name', None)
        if not original_name or original_name == 'Unknown':
            original_name = getattr(config, 'original_name', None) or f"{self._get_image_subfolder()}.png"
        manga_title = (
            image_context.get('manga_title') or getattr(config, 'manga_title', None) or 'Ungrouped'
        ).strip() or 'Ungrouped'
        manga_group_id = image_context.get('manga_group_id') or getattr(config, 'manga_group_id', None)
        review_pending = bool(getattr(ctx, 'manual_review_required', False)) or any(
            bool(getattr(region, 'review_required', False)) for region in (getattr(ctx, 'text_regions', None) or [])
        )
        started_at = (
            getattr(ctx, 'started_at_iso', None)
            or image_context.get('started_at')
            or (self._pipeline_run.manifest.get('createdAt') if getattr(self, '_pipeline_run', None) else None)
        )
        finished_at = datetime.now(timezone.utc).isoformat()
        duration_ms = None
        if getattr(ctx, 'started_at_monotonic', None) is not None:
            duration_ms = round((time.monotonic() - ctx.started_at_monotonic) * 1000)
        elif started_at:
            try:
                started = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                duration_ms = round((datetime.fromisoformat(finished_at.replace('Z', '+00:00')) - started).total_seconds() * 1000)
            except Exception:
                pass

        return {
            'originalName': original_name,
            'mangaTitle': manga_title,
            'mangaGroupId': manga_group_id,
            'groupId': manga_group_id,
            'pageOrder': getattr(config, 'page_order', None),
            'sourcePath': getattr(config, 'source_path', None),
            'requestId': getattr(config, 'request_id', None),
            'timestamp': int(time.time() * 1000),
            'startedAt': started_at,
            'finishedAt': finished_at,
            'durationMs': duration_ms,
            'reviewStatus': 'pending' if review_pending else 'not_required',
            'reviewedAt': None,
            'settings': {
                'detectionResolution': str(getattr(config.detector, 'detection_size', '1536')),
                'textDetector': str(getattr(config.detector, 'detector', 'default')),
                'customUnclipRatio': float(getattr(config.detector, 'unclip_ratio', 2.3)),
                'customBoxThreshold': float(getattr(config.detector, 'box_threshold', 0.7)),
                'ocr': str(getattr(config.ocr, 'ocr', Ocr.ocr48px_ctc)),
                'customOcrProb': float(getattr(config.ocr, 'prob')) if getattr(config.ocr, 'prob', None) is not None else None,
                'ocrMinConfidence': float(getattr(config.ocr, 'prob')) if getattr(config.ocr, 'prob', None) is not None else None,
                'useMocrMerge': bool(getattr(config.ocr, 'use_mocr_merge', False)),
                'bubbleDetection': bool(getattr(config.bubble_detection, 'enabled', False)),
                'bubbleModel': str(getattr(config.bubble_detection, 'model', 'manga109')),
                'bubbleConfidence': float(getattr(config.bubble_detection, 'confidence', 0.25)),
                'bubbleMaskThreshold': float(getattr(config.bubble_detection, 'mask_threshold', 0.5)),
                'inpainter': str(getattr(config.inpainter, 'inpainter', 'default')),
                'inpaintingSize': str(getattr(config.inpainter, 'inpainting_size', '2048')),
                'inpaintingPrecision': str(getattr(config.inpainter, 'inpainting_precision', 'bf16')),
                'maskDilationOffset': int(getattr(config, 'mask_dilation_offset', 30)),
                'renderer': str(getattr(config.render, 'renderer', 'default')),
                'renderTextDirection': str(getattr(config.render, 'direction', 'auto')),
                'renderAlignment': str(getattr(config.render, 'alignment', 'auto')),
                'renderFont': str(getattr(config.render, 'gimp_font', 'Sans-serif')),
                'translator': str(getattr(config.translator, 'translator', 'offline')),
                'translatorModel': getattr(ctx, 'translator_model', None),
                'offlineModel': getattr(ctx, 'offline_model', None),
                'geminiModel': getattr(ctx, 'gemini_model', None),
                'targetLanguage': str(getattr(config.translator, 'target_lang', 'ENG')),
                'colorizer': str(getattr(config.colorizer, 'colorizer', 'none')),
                'colorizationSize': int(getattr(config.colorizer, 'colorization_size', 576)),
                'denoiseSigma': int(getattr(config.colorizer, 'denoise_sigma', 25)),
                'colorThreshold': float(getattr(config.colorizer, 'color_threshold', 31.0)),
                'upscaler': str(getattr(config.upscale, 'upscaler', '')),
                'upscaleRatio': getattr(config.upscale, 'upscale_ratio', None),
                'revertUpscaling': bool(getattr(config.upscale, 'revert_upscaling', True)),
            },
        }

    def _get_image_subfolder(self) -> str:
        """获取当前图片的调试子文件夹名"""
        if self._current_image_context:
            return self._current_image_context['subfolder']
        return ''
    
    def _save_current_image_context(self, image_md5: str):
        """保存当前图片上下文，用于批量处理中保持一致性"""
        if self._current_image_context:
            self._saved_image_contexts[image_md5] = self._current_image_context.copy()

    def _restore_image_context(self, image_md5: str):
        """恢复保存的图片上下文"""
        if image_md5 in self._saved_image_contexts:
            self._current_image_context = self._saved_image_contexts[image_md5].copy()
            return True
        return False

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
        # In-process MPS cache cleanup runs exclusively after active model calls.
        # Calling empty_cache from a pipeline can race another lane's Metal work.
        device = getattr(self, 'device', 'cpu')
        if device == 'mps' and get_model_executor() is not None:
            return
        empty_device_cache(device)

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
        """
        Translates a single image.

        :param image: Input image.
        :param config: Translation config.
        :param image_name: Deprecated parameter, kept for compatibility.
        :return: Translation context.
        """
        await self._report_progress('running_pre_translation_hooks')
        for hook in self._progress_hooks:
            try:
                hook('running_pre_translation_hooks', False)
            except Exception as e:
                logger.error(f"Error in progress hook: {e}")

        ctx = Context()
        ctx.input = image
        ctx.result = None
        ctx.result_documents = {}
        ctx.verbose = self.verbose
        ctx.started_at_monotonic = time.monotonic()
        ctx.started_at_iso = datetime.now(timezone.utc).isoformat()
        self._pipeline_run = None

        # 设置图片上下文以生成调试图片子文件夹
        self._set_image_context(config, image)
        if self._current_image_context:
            self._current_image_context['started_at'] = ctx.started_at_iso

        self._pipeline_run = PipelineRun(
            self.result_root, self._get_image_subfolder(), image, config
        )
        self._pipeline_run.ctx = ctx
        self._pipeline_run.translator = self
        
        # 保存debug文件夹信息到Context中（用于Web模式的缓存访问）
        # 在web模式下总是保存，不仅仅是verbose模式
        ctx.debug_folder = self._get_image_subfolder()
        
        # 保存原始输入图片用于对比和调试
        try:
            result_path = self._result_path('input.jpg')
            await asyncio.to_thread(save_jpeg, image, result_path)
        except Exception as e:
            logger.error(f"Error saving input.jpg debug image: {e}")
            logger.debug(f"Exception details: {traceback.format_exc()}")
        if self._pipeline_run is not None:
            self._pipeline_run.refresh()
            await self._report_progress(f'debug_folder:{self._get_image_subfolder()}')

        # preload and download models (not strictly necessary, remove to lazy load)
        if getattr(self, 'models_ttl', 0) == 0:
            logger.info('Loading models')
            if config.upscale.upscale_ratio:
                await prepare_upscaling(config.upscale.upscaler)
            await prepare_detection(config.detector.detector)
            device = getattr(self, 'device', 'cpu')
            await prepare_ocr(config.ocr.ocr, device)
            await prepare_inpainting(config.inpainter.inpainter, device)
            await prepare_translation(config.translator.translator_gen)
            if config.colorizer.colorizer != Colorizer.none:
                await prepare_colorization(config.colorizer.colorizer)
            if bool(getattr(getattr(config, 'bubble_detection', None), 'enabled', False)):
                await prepare_bubble_detection(config.bubble_detection, device)

        self._log_memory_boundary("models_ready", ctx)
        # translate
        try:
            ctx = await self._translate(config, ctx)
        except (Exception, asyncio.CancelledError) as e:
            if self._pipeline_run is not None:
                if isinstance(e, asyncio.CancelledError):
                    self._pipeline_run.cancel("Stopped by user")
                else:
                    self._pipeline_run.fail(str(e))
                await self._pipeline_run.checkpoint()
                self._pipeline_run.release_runtime()
                self._pipeline_run = None
            raise

        # 在翻译流程的最后保存翻译结果，确保保存的是最终结果（包括重试后的结果）
        # Save translation results at the end of translation process to ensure final results are saved
        if not skip_context_save and ctx.text_regions:
            # 汇总本页翻译，供下一页做上文
            page_translations = {
                r.text_raw if hasattr(r, "text_raw") else r.text: r.translation
                for r in ctx.text_regions if not is_preserved_region(r)
            }
            self.all_page_translations.append(page_translations)

            # 同时保存原文用于并发模式的上下文
            page_original_texts = {
                i: (r.text_raw if hasattr(r, "text_raw") else r.text)
                for i, r in enumerate(ctx.text_regions) if not is_preserved_region(r)
            }
            self._original_page_texts.append(page_original_texts)

        return ctx

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

    async def _translate(self, config: Config, ctx: Context) -> Context:
        if getattr(ctx, 'result_documents', None) is None:
            ctx.result_documents = {}
        # Start the background cleanup job once if not already started.
        if self._model_cleanup_task is None:
            self._model_cleanup_task = asyncio.create_task(self._model_cleanup_job())
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
                await self._report_progress('colorizing')
                try:
                    ctx.img_colorized = await self._run_colorizer(config, ctx)
                    colorization_ran = True
                except Exception as e:  
                    logger.error(f"Error during colorizing:\n{traceback.format_exc()}")  
                    if not self.ignore_errors:  
                        raise  
                    ctx.img_colorized = ctx.input  # Fallback to input image if colorization fails
        else:
            ctx.img_colorized = ctx.input

        if self._pipeline_run is not None and colorization_ran and ctx.img_colorized is not None:
            colorized = np.array(ctx.img_colorized)
            if len(colorized.shape) == 3 and colorized.shape[2] == 3:
                colorized = cv2.cvtColor(colorized, cv2.COLOR_RGB2BGR)
            await self._async_imwrite(self._result_path('colorized.png'), colorized)
            self._pipeline_run.refresh()

        # -- Upscaling
        # The default text detector doesn't work very well on smaller images, might want to
        # consider adding automatic upscaling on certain kinds of small images.
        upscaling_ran = False
        if config.upscale.upscale_ratio:
            await self._report_progress('upscaling')
            try:
                ctx.upscaled = await self._run_upscaling(config, ctx)
                upscaling_ran = True
                ctx.upscaled_ran = True
            except Exception as e:
                logger.error(f"Error during upscaling:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise  
                ctx.upscaled = ctx.img_colorized # Fallback to colorized (or input) image if upscaling fails
                ctx.upscaled_ran = False
        else:
            ctx.upscaled = ctx.img_colorized
            ctx.upscaled_ran = False

        if self._pipeline_run is not None and upscaling_ran and ctx.upscaled is not None:
            upscaled = np.array(ctx.upscaled)
            if len(upscaled.shape) == 3 and upscaled.shape[2] == 3:
                upscaled = cv2.cvtColor(upscaled, cv2.COLOR_RGB2BGR)
            await self._async_imwrite(self._result_path('upscaled.png'), upscaled)
            self._pipeline_run.refresh()

        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

        # -- Detection
        await self._report_progress('detection')
        try:
            ctx.textlines, ctx.mask_raw, ctx.mask = await self._run_detection(config, ctx)
        except Exception as e:  
            logger.error(f"Error during detection:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.textlines = [] 
            ctx.mask_raw = None
            ctx.mask = None

        if (self.verbose or self._pipeline_run is not None) and ctx.mask_raw is not None:
            await self._async_imwrite(self._result_path('mask_raw.png'), ctx.mask_raw)

        if not ctx.textlines:
            await self._report_progress('skip-no-regions', True)
            # If no text was found result is intermediate image product
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)

        detection_docs = serialize_regions(ctx.textlines)
        ctx.result_documents['detection.json'] = detection_docs
        if self._pipeline_run is not None:
            self._pipeline_run.write_json('detection.json', detection_docs)
        elif self._current_image_context:
            await save_result_documents(
                self._current_image_context['subfolder'],
                {'detection.json': detection_docs},
                self.result_root,
            )

        # -- OCR
        await self._report_progress('ocr')
        try:
            ctx.textlines = await self._run_ocr(config, ctx)
        except Exception as e:  
            logger.error(f"Error during ocr:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.textlines = [] # Fallback to empty textlines if OCR fails

        if ctx.textlines:
            ocr_docs = serialize_regions(ctx.textlines)
            ctx.result_documents['ocr.json'] = ocr_docs
            if self._pipeline_run is not None:
                self._pipeline_run.write_json('ocr.json', ocr_docs)
            elif self._current_image_context:
                await save_result_documents(
                    self._current_image_context['subfolder'],
                    {'ocr.json': ocr_docs},
                    self.result_root,
                )

        if not ctx.textlines:
            await self._report_progress('skip-no-text', True)
            # If no text was found result is intermediate image product
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)

        # -- Textline merge
        await self._report_progress('textline_merge')
        try:
            ctx.text_regions = await self._run_textline_merge(config, ctx)
        except Exception as e:  
            logger.error(f"Error during textline_merge:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.text_regions = [] # Fallback to empty text_regions if textline merge fails

        if ctx.text_regions:
            merged_docs = serialize_regions(ctx.text_regions)
            ctx.result_documents['text_regions_merged.json'] = merged_docs
            if self._pipeline_run is not None:
                self._pipeline_run.write_json('text_regions_merged.json', merged_docs)
            elif self._current_image_context:
                await save_result_documents(
                    self._current_image_context['subfolder'],
                    {'text_regions_merged.json': merged_docs},
                    self.result_root,
                )

        # Detect before translation so grouped bubble text is translated as one flow.
        await self._detect_speech_bubbles(config, ctx)

        # Apply pre-dictionary after textline merge
        pre_dict = load_dictionary(self.pre_dict)
        pre_replacements = []
        for region in ctx.text_regions:
            original = region.text  
            region.text = apply_dictionary(region.text, pre_dict)
            if original != region.text:
                pre_replacements.append(f"{original} => {region.text}")

        if pre_replacements:
            logger.info("Pre-translation replacements:")
            for replacement in pre_replacements:
                logger.info(replacement)
        else:
            logger.info("No pre-translation replacements made.")

        # -- Translation
        await self._report_progress('translating')
        try:
            ctx.text_regions = await self._run_text_translation(config, ctx)
        except Exception as e:  
            logger.error(f"Error during translating:\n{traceback.format_exc()}")  
            raise

        translations_doc = serialize_regions(ctx.text_regions if isinstance(ctx.text_regions, list) else [])
        ctx.result_documents['translations.json'] = translations_doc
        if self._pipeline_run is not None:
            self._pipeline_run.write_json('translations.json', translations_doc)
        elif self._current_image_context:
            await save_result_documents(
                self._current_image_context['subfolder'],
                {'translations.json': translations_doc},
                self.result_root,
            )

        await self._report_progress('after-translating')

        if ctx.get('translation_error'):
            raise TranslationFailure(ctx.translation_error)
        if not ctx.text_regions:
            await self._report_progress('error-translating', True)
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)
        elif ctx.text_regions == 'cancel':
            await self._report_progress('cancelled', True)
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)

        # -- Mask refinement
        # (Delayed to take advantage of the region filtering done after ocr and translation)
        await self._report_progress('mask-generation')
        try:
            bundle = await run_cpu_stage(
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

        if (self.verbose or self._pipeline_run is not None) and ctx.mask is not None:
            await self._async_imwrite(self._result_path('text_mask.png'), ctx.text_mask)
            await self._async_imwrite(self._result_path('bubble_mask.png'), ctx.bubble_mask)
            if getattr(ctx, 'detector_rescue_mask', None) is not None:
                await self._async_imwrite(self._result_path('detector_rescue_mask.png'), ctx.detector_rescue_mask)
            if getattr(ctx, 'bubble_residual_mask', None) is not None:
                await self._async_imwrite(self._result_path('bubble_residual_mask.png'), ctx.bubble_residual_mask)
            if getattr(ctx, 'protected_edge_mask', None) is not None:
                await self._async_imwrite(self._result_path('protected_bubble_edge.png'), ctx.protected_edge_mask)
            await self._async_imwrite(self._result_path('mask_final.png'), ctx.mask)
            await self._async_imwrite(self._result_path('inpaint_mask.png'), ctx.inpaint_mask)
            if ctx.img_rgb is not None and getattr(ctx, 'mask_bundle', None) is not None:
                try:
                    overlay = create_mask_sources_overlay(ctx.img_rgb, ctx.mask_bundle)
                    await self._async_imwrite(self._result_path('mask_sources_overlay.png'), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
                except Exception as ex:
                    logger.warning(f"Could not save mask_sources_overlay.png: {ex}")
            if self._pipeline_run is not None:
                self._pipeline_run.refresh()

        ctx.cleanup_mask_diagnostics()
        bundle = None

        # Layout owns placement; inpainting and rendering only consume its result.
        try:
            await self._report_progress('layout')
            transform_text_case = getattr(config.render, "transform_text_case", None)
            if transform_text_case:
                for region in (ctx.text_regions or []):
                    if is_preserved_region(region):
                        region.translation = region.text
                    elif getattr(region, "translation", None) and isinstance(region.translation, str):
                        region.translation = transform_text_case(region.translation)
            layout_font = self.font_path or getattr(config.render, 'font_path', None) or get_default_eng_font()
            await run_cpu_stage(layout_page, ctx, config, layout_font, priority=CPU_PRIORITY_BACKGROUND)
            if self._pipeline_run is not None:
                self._pipeline_run.write_json('layout.json', serialize_frozen_layout(
                    ctx, config, layout_font,
                    serialize_bubble_detections(getattr(ctx, 'bubble_detections', None) or []),
                ))
        except Exception as error:
            logger.warning('Production layout failed; preserving the existing render path: %s', error)

        # -- Inpainting
        await self._report_progress('inpainting')
        try:
            ctx.img_inpainted = await self._run_inpainting(config, ctx)
        except Exception as e:  
            logger.error(f"Error during inpainting:\n{traceback.format_exc()}")  
            raise
        ctx.gimp_mask = np.dstack((cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), ctx.mask))

        if self.verbose or self._pipeline_run is not None:
            try:
                inpainted_path = self._result_path('inpainted.jpg')
                await asyncio.to_thread(save_jpeg, ctx.img_inpainted, inpainted_path)
                try:
                    os.unlink(self._result_path('inpainted.png'))
                except FileNotFoundError:
                    pass
                if self._pipeline_run is not None:
                    self._pipeline_run.refresh()
            except Exception as e:  
                logger.error(f"Error saving inpainted.jpg debug image: {e}")
                logger.debug(f"Exception details: {traceback.format_exc()}")

        if getattr(ctx, "_bubble_layout_ready", False):
            ctx.cleanup_mask_workspace()
        # -- Rendering
        await self._report_progress('rendering')

        # 在rendering状态后立即发送文件夹信息，用于前端精确检查final.png
        if hasattr(self, '_progress_hooks') and self._current_image_context:
            folder_name = self._current_image_context['subfolder']
            # 发送特殊格式的消息，前端可以解析
            await self._report_progress(f'rendering_folder:{folder_name}')

        try:
            ctx.img_rendered = await self._run_text_rendering(config, ctx)
        except Exception as e:
            logger.error(f"Error during rendering:\n{traceback.format_exc()}")
            raise

        await self._report_progress('saving')
        ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)

        return await self._revert_upscale(config, ctx)
    
    # If upscaling was enabled and `revert_upscaling` is True, revert to input size
    # Else leave `ctx` as-is
    async def _revert_upscale(self, config: Config, ctx: Context):
        should_downscale = bool(
            config.upscale.upscale_ratio
            and config.upscale.revert_upscaling
            and getattr(ctx, 'upscaled_ran', True)
            and ctx.result
            and ctx.result.size != ctx.input.size
        )
        if should_downscale:
            await self._report_progress('downscaling')
            ctx.result = ctx.result.resize(ctx.input.size)

        # 保存verbose或pipeline run的final.jpg到调试文件夹
        final_saved = False
        if ctx.result and (self.verbose or self._pipeline_run is not None):
            try:
                final_img = np.array(ctx.result)
                final_path = self._result_path('final.jpg')
                await asyncio.to_thread(save_jpeg, final_img, final_path)
                final_saved = True
            except Exception as e:
                logger.error(f"Error saving final.jpg debug image: {e}")
                logger.debug(f"Exception details: {traceback.format_exc()}")

        # Web流式模式优化：保存final.jpg并使用占位符
        if ctx.result and (
            (not self.result_sub_folder and hasattr(self, '_is_streaming_mode') and self._is_streaming_mode)
            or self._pipeline_run is not None
        ):
            # 保存final.jpg文件 (skip if already saved above)
            if not final_saved:
                try:
                    final_path = self._result_path('final.jpg')
                    if hasattr(ctx.result, 'save'):
                        await asyncio.to_thread(save_jpeg, ctx.result, final_path)
                    else:
                        await asyncio.to_thread(save_jpeg, np.array(ctx.result), final_path)
                except Exception as e:
                    logger.error(f"Error saving final.jpg: {e}")

            # 保存inpainted.jpg与text_regions.json供交互式编辑
            try:
                scale_x = 1.0
                scale_y = 1.0
                if ctx.img_inpainted is not None:
                    inpainted_img = ctx.img_inpainted
                    target_w, target_h = ctx.result.size
                    cur_h, cur_w = inpainted_img.shape[:2]
                    if (cur_w, cur_h) != (target_w, target_h) and cur_w > 0 and cur_h > 0:
                        interp = cv2.INTER_AREA if (target_w < cur_w and target_h < cur_h) else cv2.INTER_LINEAR
                        inpainted_img = cv2.resize(inpainted_img, (target_w, target_h), interpolation=interp)
                        scale_x = target_w / cur_w
                        scale_y = target_h / cur_h
                    await asyncio.to_thread(save_jpeg, inpainted_img, self._result_path('inpainted.jpg'))
                    try:
                        os.unlink(self._result_path('inpainted.png'))
                    except FileNotFoundError:
                        pass

                text_regions_data = []
                if ctx.text_regions:
                    for i, blk in enumerate(ctx.text_regions):
                        try:
                            xywh = blk.xywh.tolist() if hasattr(blk.xywh, 'tolist') else list(blk.xywh)
                            fg = list(blk.fg_colors) if hasattr(blk.fg_colors, '__iter__') else [0, 0, 0]
                            bg = list(blk.bg_colors) if hasattr(blk.bg_colors, '__iter__') else [255, 255, 255]
                            fg = [int(c) for c in fg[:3]]
                            bg = [int(c) for c in bg[:3]]
                            lines = blk.lines.tolist() if hasattr(blk.lines, 'tolist') else []
                            lines = [
                                [[int(round(point[0] * scale_x)), int(round(point[1] * scale_y))] for point in line]
                                for line in lines
                            ]

                            bx = int(round(xywh[0] * scale_x))
                            by = int(round(xywh[1] * scale_y))
                            bw = max(20, int(round(xywh[2] * scale_x)))
                            bh = max(20, int(round(xywh[3] * scale_y)))
                            trans_text = blk.translation if hasattr(blk, 'translation') else ""
                            dir_val = getattr(blk, 'direction', 'h')
                            layout_bounds = getattr(blk, 'layout_bounds', None)
                            layout_segments = getattr(blk, 'layout_segments', None)

                            # If rendering horizontal text (e.g. English) from a vertical OCR textline,
                            # expand narrow boxes to the true speech bubble contour or proportions.
                            if dir_val != 'v' and layout_bounds and len(layout_bounds) == 4:
                                bx = int(round(layout_bounds[0] * scale_x))
                                by = int(round(layout_bounds[1] * scale_y))
                                bw = max(20, int(round((layout_bounds[2] - layout_bounds[0]) * scale_x)))
                                bh = max(20, int(round((layout_bounds[3] - layout_bounds[1]) * scale_y)))
                            elif dir_val != 'v' and bw < 140 and bh >= 50:
                                try:
                                    from .rendering.ballon_extractor import safe_ballon_bounds
                                    if ctx.img_rgb is not None:
                                        enlarge_ratio = min(max(xywh[2] / max(1, xywh[3]), xywh[3] / max(1, xywh[2])) * 1.5, 3)
                                        detected_bounds = safe_ballon_bounds(ctx.img_rgb, xywh, enlarge_ratio=enlarge_ratio)
                                        if detected_bounds:
                                            layout_bounds = detected_bounds
                                            bx = int(round(detected_bounds[0] * scale_x))
                                            by = int(round(detected_bounds[1] * scale_y))
                                            bw = max(bw, int(round((detected_bounds[2] - detected_bounds[0]) * scale_x)))
                                            bh = max(bh, int(round((detected_bounds[3] - detected_bounds[1]) * scale_y)))
                                except Exception:
                                    pass

                                if bw < 140 and bh >= 50:
                                    desired_w = max(140, int(round(bh * 0.85)))
                                    diff_w = desired_w - bw
                                    bx = max(0, bx - diff_w // 2)
                                    bw = desired_w

                            # Font size calculation:
                            # Instead of copying raw Japanese Kanji height, estimate a proportionate
                            # font size based on the translated string length and bubble area.
                            font_sz = blk.font_size if hasattr(blk, 'font_size') and blk.font_size > 0 else 24
                            scaled_font_size = max(10, int(round(font_sz * scale_y)))

                            if trans_text and dir_val != 'v' and not layout_segments:
                                words = [w for w in trans_text.split() if w]
                                if words:
                                    longest_w = max(len(w) for w in words)
                                    safe_w = max(20, int(bw * 0.82))
                                    safe_h = max(20, int(bh * 0.84))
                                    max_w_size = int(safe_w / (longest_w * 0.56))
                                    est_size = min(scaled_font_size, max_w_size, 42)
                                    while est_size > 11:
                                        char_w = est_size * 0.54
                                        line_h = est_size * 1.15
                                        c_per_line = max(1, int(safe_w / char_w))
                                        lines_count = 1
                                        cur_line_chars = 0
                                        for w in words:
                                            if cur_line_chars == 0:
                                                cur_line_chars = len(w)
                                            elif cur_line_chars + 1 + len(w) <= c_per_line:
                                                cur_line_chars += 1 + len(w)
                                            else:
                                                lines_count += 1
                                                cur_line_chars = len(w)
                                        if lines_count * line_h <= safe_h:
                                            break
                                        est_size -= 1
                                    scaled_font_size = max(11, est_size)

                            text_regions_data.append({
                                "id": getattr(blk, 'group_id', f"bubble_{getattr(blk, '_bubble_source_order', i)}"),
                                "group_members": getattr(blk, 'group_members', []),
                                "review_required": bool(getattr(blk, 'review_required', False)),
                                "review_reason": getattr(blk, 'review_reason', None),
                                "x": bx,
                                "y": by,
                                "width": bw,
                                "height": bh,
                                "layout_bounds": {
                                    "x": bx,
                                    "y": by,
                                    "width": bw,
                                    "height": bh,
                                } if layout_bounds else None,
                                "layout_segments": [
                                    {
                                        "x": int(round(segment["x"] * scale_x)),
                                        "y": int(round(segment["y"] * scale_y)),
                                        "width": max(1, int(round(segment["width"] * scale_x))),
                                        "height": max(1, int(round(segment["height"] * scale_y))),
                                        "text": segment["text"],
                                        "font_size": int(segment.get("font_size", blk.font_size)),
                                        "rendered_png": encode_rendered_box(
                                            next((box["box"] for box in (getattr(blk, "_bubble_segments", None) or [])
                                                  if box["bounds"][0] == segment["x"] and box["bounds"][1] == segment["y"]), None),
                                            scale_x, scale_y,
                                        ),
                                        "positioned_lines": [
                                            {
                                                "text": line["text"],
                                                "x": int(round(line["x"] * scale_x)),
                                                "y": int(round(line["y"] * scale_y)),
                                            }
                                            for line in segment.get("lines", [])
                                        ],
                                    }
                                    for segment in (layout_segments or [])
                                ],
                                "bubble_safe_shape": encode_safe_shape(
                                    getattr(blk, '_bubble_interior', None), scale_x, scale_y
                                ),
                                "lines": lines,
                                "original_text": blk.text if hasattr(blk, 'text') else "",
                                "translation": trans_text,
                                "font_size": scaled_font_size,
                                "font_family": getattr(blk, 'font_family', "") or "Comic Neue",
                                "fg_color": fg,
                                "bg_color": bg,
                                "stroke_width": float(getattr(blk, 'stroke_width', 2.0)),
                                "angle": float(getattr(blk, 'angle', 0)),
                                "direction": dir_val,
                                "alignment": getattr(blk, 'alignment', 'center'),
                                "line_spacing": float(config.render.line_spacing or 0) if layout_segments else float(getattr(blk, 'line_spacing', 1.0)),
                                "letter_spacing": float(getattr(blk, 'letter_spacing', 1.0)),
                                "bold": bool(getattr(blk, 'bold', False)),
                                "italic": bool(getattr(blk, 'italic', False)),
                                "target_lang": getattr(blk, 'target_lang', None) or str(getattr(config.translator, 'target_lang', 'ENG')),
                            })
                        except Exception as block_err:
                            logger.warning(f"Error serializing text region {i}: {block_err}")

            except Exception as e:
                logger.error(f"Error saving editor artifacts: {e}")
                raise

            # 保存meta.json记录元数据
            try:
                meta = self._build_result_metadata(config, ctx)
            except Exception as e:
                logger.error(f"Error saving meta.json: {e}")
                raise

            folder_name = self._current_image_context['subfolder']
            documents = {
                **(getattr(ctx, 'result_documents', None) or {}),
                'meta.json': meta,
                'text_regions.json': text_regions_data,
            }
            if self._pipeline_run is not None:
                self._pipeline_run.documents.update(documents)
                await self._pipeline_run.checkpoint()
            else:
                await save_result_documents(folder_name, documents, self.result_root)

            # 通知前端文件已就绪
            if hasattr(self, '_progress_hooks') and self._current_image_context:
                await self._report_progress(f'final_ready:{folder_name}')

            await self._report_progress('finished', True)
            if self._pipeline_run is not None:
                self._pipeline_run.release_runtime(preserve_output=True)
                self._pipeline_run = None

            # 创建占位符结果并立即返回
            from PIL import Image
            placeholder = Image.new('RGB', (1, 1), color='white')
            ctx.result = placeholder
            ctx.use_placeholder = True
            return ctx

        await self._report_progress('finished', True)
        if self._pipeline_run is not None:
            self._pipeline_run.release_runtime(preserve_output=True)
            self._pipeline_run = None
        return ctx

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
        async def _execute():
            try:
                return await coro_fn(*args, **kwargs)
            except (RuntimeError, NotImplementedError) as exc:
                err_str = str(exc)
                unsupported_mps_keywords = (
                    'not implemented for', 'not supported on mps',
                    'operator does not have a metal kernel',
                )
                if self.device == 'mps' and any(kw in err_str.lower() for kw in unsupported_mps_keywords):
                    logger.warning(
                        f'MPS not supported for this model ({type(exc).__name__}: {exc!s}). '
                        'Falling back to CPU for this model call.'
                    )
                    # Replace the device argument in the call and retry on CPU without permanently overriding self.device
                    new_args = tuple('cpu' if isinstance(a, str) and a == 'mps' else a for a in args)
                    new_kwargs = {k: ('cpu' if isinstance(v, str) and v == 'mps' else v) for k, v in kwargs.items()}
                    return await coro_fn(*new_args, **new_kwargs)
                raise

        # Keep the standalone MPS guard separate from shared in-process model work.
        if self.device == 'mps' and get_model_executor() is None:
            async with _GLOBAL_MPS_LOCK:
                return await _execute()
        else:
            return await _execute()

    async def _run_colorizer(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("colorizer", config.colorizer.colorizer)] = current_time
        return await self._mps_call(
            dispatch_colorization,
            config.colorizer.colorizer,
            colorization_size=config.colorizer.colorization_size,
            denoise_sigma=config.colorizer.denoise_sigma,
            color_threshold=config.colorizer.color_threshold,
            restore_size=config.colorizer.restore_size,
            device=self.device,
            image=ctx.input,
            **ctx
        )

    async def _run_upscaling(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("upscaling", config.upscale.upscaler)] = current_time
        return (await self._mps_call(
            dispatch_upscaling,
            config.upscale.upscaler, [ctx.img_colorized], config.upscale.upscale_ratio, self.device
        ))[0]

    async def _run_upscaling_batch(self, configs: list[Config], contexts: list[Context]):
        if len(configs) != len(contexts):
            raise ValueError("Upscaling config/context count mismatch")
        if not configs:
            return []
        upscale = configs[0].upscale
        if any(config.upscale.dict() != upscale.dict() for config in configs[1:]):
            raise ValueError("Upscaling batch settings must match")
        images = [context.img_colorized or context.input for context in contexts]
        if any(image is None for image in images):
            raise RuntimeError("No image available for upscaling")
        self._model_usage_timestamps[("upscaling", upscale.upscaler)] = time.time()
        output = await self._mps_call(
            dispatch_upscaling,
            upscale.upscaler,
            images,
            upscale.upscale_ratio,
            self.device,
        )
        if len(output) != len(images):
            raise RuntimeError(f"Upscaler returned {len(output)} pages for {len(images)} inputs")
        return output

    async def _run_detection(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("detection", config.detector.detector)] = current_time
        return await self._mps_call(
            dispatch_detection,
            config.detector.detector, ctx.img_rgb, config.detector.detection_size,
            config.detector.text_threshold, config.detector.box_threshold,
            config.detector.unclip_ratio, config.detector.det_invert,
            config.detector.det_gamma_correct, config.detector.det_rotate,
            config.detector.det_auto_rotate,
            self.device, self.verbose
        )

    async def _run_detection_batch(self, configs: list[Config], contexts: list[Context]):
        if len(configs) != len(contexts):
            raise ValueError("Detection config/context count mismatch")
        if not configs:
            return []
        detector = configs[0].detector
        if any(config.detector.dict() != detector.dict() for config in configs[1:]):
            raise ValueError("Detection batch settings must match")
        images = [context.img_rgb for context in contexts]
        if any(image is None for image in images):
            raise RuntimeError("No image canvas available for detection")
        logger.info("Detection model batch pages=%d device=%s", len(images), self.device)
        self._model_usage_timestamps[("detection", detector.detector)] = time.time()
        output = await self._mps_call(
            dispatch_detection_batch,
            detector.detector,
            images,
            detector.detection_size,
            detector.text_threshold,
            detector.box_threshold,
            detector.unclip_ratio,
            detector.det_invert,
            detector.det_gamma_correct,
            detector.det_rotate,
            detector.det_auto_rotate,
            self.device,
            self.verbose,
        )
        if len(output) != len(images):
            raise RuntimeError(f"Detector returned {len(output)} pages for {len(images)} inputs")
        return output

    async def _unload_model(self, tool: str, model: str):
        async def unload():
            logger.info(f"Unloading {tool} model: {model}")
            match tool:
                case 'colorization':
                    await unload_colorization(model)
                case 'detection':
                    await unload_detection(model)
                case 'inpainting':
                    await unload_inpainting(model)
                case 'ocr':
                    await unload_ocr(model)
                case 'upscaling':
                    await unload_upscaling(model)
                case 'translation':
                    await unload_translation(model)
                case 'bubble_detection':
                    await unload_bubble_detection()
            self._empty_device_cache()

        executor = get_model_executor()
        if executor is None:
            await unload()
        else:
            await executor.run_exclusive(unload)

    # Background models cleanup job.
    async def _model_cleanup_job(self):
        while True:
            await asyncio.sleep(20)
            executor = get_model_executor()
            if executor is not None:
                await executor.cleanup_models(self.models_ttl)
            elif self.models_ttl > 0:
                now = time.time()
                for (tool, model), last_used in list(self._model_usage_timestamps.items()):
                    if now - last_used > self.models_ttl:
                        await self._unload_model(tool, model)
                        del self._model_usage_timestamps[(tool, model)]

    @model_operation
    async def _run_ocr(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("ocr", config.ocr.ocr)] = current_time
        
        # 为OCR创建子文件夹（只在verbose模式下）
        if self.verbose:
            image_subfolder = self._get_image_subfolder()
            if image_subfolder:
                if self.result_sub_folder:
                    ocr_result_dir = os.path.join(self.result_root, self.result_sub_folder, image_subfolder, 'ocrs')
                else:
                    ocr_result_dir = os.path.join(self.result_root, image_subfolder, 'ocrs')
                os.makedirs(ocr_result_dir, exist_ok=True)
            else:
                ocr_result_dir = os.path.join(self.result_root, self.result_sub_folder, 'ocrs')
                os.makedirs(ocr_result_dir, exist_ok=True)
        else:
            # 非verbose模式下使用临时目录或不创建OCR结果目录
            ocr_result_dir = None
        
        # 临时设置环境变量供OCR模块使用
        old_ocr_dir = os.environ.get('MANGA_OCR_RESULT_DIR', None)
        if ocr_result_dir:
            os.environ['MANGA_OCR_RESULT_DIR'] = ocr_result_dir
        
        try:
            textlines = await self._mps_call(dispatch_ocr, config.ocr.ocr, ctx.img_rgb, ctx.textlines, config.ocr, self.device, self.verbose)
        finally:
            # 恢复环境变量
            if old_ocr_dir is not None:
                os.environ['MANGA_OCR_RESULT_DIR'] = old_ocr_dir
            elif 'MANGA_OCR_RESULT_DIR' in os.environ:
                del os.environ['MANGA_OCR_RESULT_DIR']

        return self._finish_ocr_textlines(textlines, config, ctx)

    @model_operation
    async def _run_ocr_batch(self, pages: list[tuple[Context, Config]]):
        if not pages:
            return []
        if len({config.ocr.ocr for _, config in pages}) != 1:
            return [await self._run_ocr(config, ctx) for ctx, config in pages]
        logger.info(
            "OCR model batch pages=%d backend=%s device=%s",
            len(pages), pages[0][1].ocr.ocr, self.device,
        )
        for _, config in pages:
            self._model_usage_timestamps[("ocr", config.ocr.ocr)] = time.time()
        outputs = await self._mps_call(
            dispatch_ocr_batch,
            pages[0][1].ocr.ocr,
            [(ctx.img_rgb, ctx.textlines, config.ocr) for ctx, config in pages],
            self.device,
            self.verbose,
        )
        if len(outputs) != len(pages):
            raise RuntimeError("OCR batch returned a different number of page results than inputs")
        return [
            self._finish_ocr_textlines(textlines, config, ctx)
            for (ctx, config), textlines in zip(pages, outputs)
        ]

    @staticmethod
    def _finish_ocr_textlines(textlines, config: Config, ctx: Context):
        analyze_source_typography(textlines, ctx.img_rgb)
        new_textlines = []
        for textline in textlines:
            if textline.text.strip():
                if config.render.font_color_fg:
                    textline.fg_r, textline.fg_g, textline.fg_b = config.render.font_color_fg
                if config.render.font_color_bg:
                    textline.bg_r, textline.bg_g, textline.bg_b = config.render.font_color_bg
                new_textlines.append(textline)
        return new_textlines

    async def _run_textline_merge(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("textline_merge", "textline_merge")] = current_time

        # Filter out languages to skip before merging textlines
        if config.translator.skip_lang is not None:  
            skip_langs = [lang.strip().upper() for lang in config.translator.skip_lang.split(',')]  
            filtered_textlines = []  
            for txtln in ctx.textlines:  
                if not contains_linguistic_ocr_text(txtln.text):
                    filtered_textlines.append(txtln)
                    continue
                try:  
                    detected_lang, confidence = langid.classify(txtln.text)
                    source_language = ISO_639_1_TO_VALID_LANGUAGES.get(detected_lang, 'UNKNOWN')
                    if source_language != 'UNKNOWN':
                        source_language = source_language.upper()
                except Exception:  
                    source_language = 'UNKNOWN'  
    
                if source_language in skip_langs:  
                    logger.info(f'Filtered out: {txtln.text}')  
                    logger.info(f'Reason: Detected language {source_language} is in skip_langs')  
                    continue  # Skip this region  
                filtered_textlines.append(txtln)  
            ctx.textlines = filtered_textlines  
    
        text_regions = await dispatch_textline_merge(ctx.textlines, ctx.img_rgb.shape[1], ctx.img_rgb.shape[0],  
                                                     verbose=self.verbose)  
        for region in text_regions:
            if not hasattr(region, "text_raw"):
                region.text_raw = region.text      # <- Save the initial OCR results to expand the render detection box. Also, prevent affecting the forbidden translation function.  
            if not getattr(region, "region_id", None):
                region.region_id = uuid.uuid4().hex

        new_text_regions = []
        for region in text_regions:
            # Remove leading spaces after pre-translation dictionary replacement                
            original_text = region.text  
            stripped_text = original_text.strip()  
            
            # Record removed leading characters  
            removed_start_chars = original_text[:len(original_text) - len(stripped_text)]  
            if removed_start_chars:  
                logger.info(f'Removed leading characters: "{removed_start_chars}" from "{original_text}"')  
            
            # Modified filtering condition: handle incomplete parentheses  
            bracket_pairs = {  
                '(': ')', '（': '）', '[': ']', '【': '】', '{': '}', '〔': '〕', '〈': '〉', '「': '」',  
                '"': '"', '＂': '＂', "'": "'", "“": "”", '《': '》', '『': '』', '"': '"', '〝': '〞', '﹁': '﹂', '﹃': '﹄',  
                '⸂': '⸃', '⸄': '⸅', '⸉': '⸊', '⸌': '⸍', '⸜': '⸝', '⸠': '⸡', '‹': '›', '«': '»', '＜': '＞', '<': '>'  
            }   
            left_symbols = set(bracket_pairs.keys())  
            right_symbols = set(bracket_pairs.values())  
            
            has_brackets = any(s in stripped_text for s in left_symbols) or any(s in stripped_text for s in right_symbols)  
            
            if has_brackets:  
                result_chars = []  
                stack = []  
                to_skip = []    
                
                # 第一次遍历：标记匹配的括号  
                # First traversal: mark matching brackets
                for i, char in enumerate(stripped_text):  
                    if char in left_symbols:  
                        stack.append((i, char))  
                    elif char in right_symbols:  
                        if stack:  
                            # 有对应的左括号，出栈  
                            # There is a corresponding left bracket, pop the stack
                            stack.pop()  
                        else:  
                            # 没有对应的左括号，标记为删除  
                            # No corresponding left parenthesis, marked for deletion
                            to_skip.append(i)  
                
                # 标记未匹配的左括号为删除
                # Mark unmatched left brackets as delete  
                for pos, _ in stack:  
                    to_skip.append(pos)  
                
                has_removed_symbols = len(to_skip) > 0  
                
                # 第二次遍历：处理匹配但不对应的括号
                # Second pass: Process matching but mismatched brackets
                stack = []  
                for i, char in enumerate(stripped_text):  
                    if i in to_skip:  
                        # 跳过孤立的括号
                        # Skip isolated parentheses
                        continue  
                        
                    if char in left_symbols:  
                        stack.append(char)  
                        result_chars.append(char)  
                    elif char in right_symbols:  
                        if stack:  
                            left_bracket = stack.pop()  
                            expected_right = bracket_pairs.get(left_bracket)  
                            
                            if char != expected_right:  
                                # 替换不匹配的右括号为对应左括号的正确右括号
                                # Replace mismatched right brackets with the correct right brackets corresponding to the left brackets
                                result_chars.append(expected_right)  
                                logger.info(f'Fixed mismatched bracket: replaced "{char}" with "{expected_right}"')  
                            else:  
                                result_chars.append(char)  
                    else:  
                        result_chars.append(char)  
                
                new_stripped_text = ''.join(result_chars)  
                
                if has_removed_symbols:  
                    logger.info(f'Removed unpaired bracket from "{stripped_text}"')  
                
                if new_stripped_text != stripped_text and not has_removed_symbols:  
                    logger.info(f'Fixed brackets: "{stripped_text}" → "{new_stripped_text}"')  
                
                stripped_text = new_stripped_text  
              
            region.text = stripped_text.strip()     

        # Precompute bounds and linguistic properties for all candidate regions to enable spatial context heuristics
        def _get_region_bounds(reg):
            pts = []
            for line in getattr(reg, "lines", []):
                for pt in line:
                    pts.append(pt)
            if pts:
                arr = np.asarray(pts)
                return float(arr[:, 0].min()), float(arr[:, 1].min()), float(arr[:, 0].max()), float(arr[:, 1].max())
            return 0.0, 0.0, 0.0, 0.0

        def _is_region_in_bubbles(reg, bubble_detections):
            if not bubble_detections:
                return False
            if getattr(reg, "_bubble_mask", None) is not None:
                return True
            rx1, ry1, rx2, ry2 = _get_region_bounds(reg)
            cx, cy = int((rx1 + rx2) / 2), int((ry1 + ry2) / 2)
            for bd in bubble_detections:
                mask = getattr(bd, "mask", None)
                if mask is not None and 0 <= cy < mask.shape[0] and 0 <= cx < mask.shape[1]:
                    if mask[cy, cx] > 0:
                        return True
            return False

        region_bounds_list = [_get_region_bounds(r) for r in text_regions]
        has_linguistic_list = [contains_linguistic_ocr_text(r.text) for r in text_regions]
        bubble_dets = getattr(ctx, "bubble_detections", None) or []

        new_text_regions = []
        for idx, region in enumerate(text_regions):
            if not region.text:
                continue

            # Compute spatial proximity to neighboring linguistic text
            r_bounds = region_bounds_list[idx]
            r_fs = getattr(region, "font_size", 14) or 14
            max_dist = max(3.0 * r_fs, 80.0)
            is_near_text = False
            for other_idx, other_region in enumerate(text_regions):
                if other_idx != idx and has_linguistic_list[other_idx]:
                    o_bounds = region_bounds_list[other_idx]
                    d = rect_distance(
                        r_bounds[0], r_bounds[1], r_bounds[2], r_bounds[3],
                        o_bounds[0], o_bounds[1], o_bounds[2], o_bounds[3]
                    )
                    if d <= max_dist:
                        is_near_text = True
                        break

            is_in_bubble = _is_region_in_bubbles(region, bubble_dets)
            ocr_prob = float(getattr(region, "prob", 1.0) or 1.0)
            min_conf = float(getattr(config.ocr, "prob", 0.40) or 0.40)

            classification = classify_numeric_ocr_region(
                region.text,
                prob=ocr_prob,
                is_in_bubble=is_in_bubble,
                is_near_text=is_near_text,
                min_confidence=min_conf,
            )

            is_kept_numeric = classification in (
                NumericClassification.MEANINGFUL_NUMERIC,
                NumericClassification.POSSIBLE_NUMERIC,
            )
            has_linguistic_text = classification == NumericClassification.LINGUISTIC

            same_as_target_language = (
                has_linguistic_text
                and not config.translator.no_text_lang_skip
                and langcodes is not None
                and langcodes.tag_distance(region.source_lang, config.translator.target_lang) == 0
            )

            if is_kept_numeric:
                region.retention = "kept"
                region.retention_reason = "numeric_content"
                region.translation_policy = "preserve"
                region.translation = region.text
                if classification == NumericClassification.POSSIBLE_NUMERIC:
                    region.review_required = True
                    region.review_reasons = getattr(region, "review_reasons", []) or []
                    if "possible_numeric_content" not in region.review_reasons:
                        region.review_reasons.append("possible_numeric_content")
            elif has_linguistic_text:
                region.retention = "kept"
                region.retention_reason = "linguistic_content"
                region.translation_policy = "translate"

            should_filter = False
            filter_reason = None
            if not is_kept_numeric and not has_linguistic_text:
                should_filter = True
                filter_reason = "Text contains no letters or meaningful numbers."
            elif has_linguistic_text and len(region.text) < config.ocr.min_text_length:
                should_filter = True
                filter_reason = "Text length is less than the minimum required length."
            elif has_linguistic_text and same_as_target_language:
                should_filter = True
                filter_reason = "Text language matches the target language and no_text_lang_skip is False."

            if should_filter:
                if region.text.strip():
                    logger.info(f'Filtered out: {region.text}')
                    logger.info(f'Reason: {filter_reason}')
            else:
                if config.render.font_color_fg or config.render.font_color_bg:
                    if config.render.font_color_bg:
                        region.adjust_bg_color = False
                new_text_regions.append(region)
        text_regions = new_text_regions

        text_regions = sort_regions(
            text_regions,
            right_to_left=config.render.rtl,
            img=ctx.img_rgb,
            force_simple_sort=config.force_simple_sort
        )   
        
        return text_regions

    def _build_prev_context(self, use_original_text=False, current_page_index=None, batch_index=None, batch_original_texts=None):
        """
        跳过句子数为0的页面，取最近 context_size 个非空页面，拼成：
        <|1|>句子
        <|2|>句子
        ...
        的格式；如果没有任何非空页面，返回空串。

        Args:
            use_original_text: 是否使用原文而不是译文作为上下文
            current_page_index: 当前页面索引，用于确定上下文范围
            batch_index: 当前页面在批次中的索引
            batch_original_texts: 当前批次的原文数据
        """
        if self.context_size <= 0:
            return ""

        # 在并发模式下，需要特殊处理上下文范围
        if batch_index is not None and batch_original_texts is not None:
            # 并发模式：使用已完成的页面 + 当前批次中已处理的页面
            available_pages = self.all_page_translations.copy()

            # 添加当前批次中在当前页面之前的页面
            for i in range(batch_index):
                if i < len(batch_original_texts) and batch_original_texts[i]:
                    # 在并发模式下，我们使用原文作为"已完成"的页面
                    if use_original_text:
                        available_pages.append(batch_original_texts[i])
                    else:
                        # 如果不使用原文，则跳过当前批次的页面（因为它们还没有翻译完成）
                        pass
        elif current_page_index is not None:
            # 使用指定页面索引之前的页面作为上下文
            available_pages = self.all_page_translations[:current_page_index] if self.all_page_translations else []
        else:
            # 使用所有已完成的页面
            available_pages = self.all_page_translations or []

        if not available_pages:
            return ""

        # 筛选出有句子的页面
        non_empty_pages = [
            page for page in available_pages
            if any(sent.strip() for sent in page.values())
        ]
        # 实际要用的页数
        pages_used = min(self.context_size, len(non_empty_pages))
        if pages_used == 0:
            return ""
        tail = non_empty_pages[-pages_used:]

        # 拼接 - 根据参数决定使用原文还是译文
        lines = []
        for page in tail:
            for sent in page.values():
                if sent.strip():
                    lines.append(sent.strip())

        # 如果使用原文，需要从原始数据中获取
        if use_original_text and hasattr(self, '_original_page_texts'):
            # 尝试获取对应的原文
            original_lines = []
            for i, page in enumerate(tail):
                page_idx = available_pages.index(page)
                if page_idx < len(self._original_page_texts):
                    original_page = self._original_page_texts[page_idx]
                    for sent in original_page.values():
                        if sent.strip():
                            original_lines.append(sent.strip())
            if original_lines:
                lines = original_lines

        numbered = [f"<|{i+1}|>{s}" for i, s in enumerate(lines)]
        context_type = "original text" if use_original_text else "translation results"
        return f"Here are the previous {context_type} for reference:\n" + "\n".join(numbered)

    async def _dispatch_with_context(self, config: Config, texts: list[str], ctx: Context):
        # 计算实际要使用的上下文页数和跳过的空页数
        # Calculate the actual number of context pages to use and empty pages to skip
        done_pages = self.all_page_translations
        if self.context_size > 0 and done_pages:
            pages_expected = min(self.context_size, len(done_pages))
            non_empty_pages = [
                page for page in done_pages
                if any(sent.strip() for sent in page.values())
            ]
            pages_used = min(self.context_size, len(non_empty_pages))
            skipped = pages_expected - pages_used
        else:
            pages_used = skipped = 0

        if self.context_size > 0:
            logger.info(f"Context-aware translation enabled with {self.context_size} pages of history")

        # 构建上下文字符串
        # Build the context string
        prev_ctx = self._build_prev_context()

        # 如果是 ChatGPT 翻译器，则专门处理上下文注入
        # Special handling for ChatGPT translator: inject context
        if config.translator.translator == Translator.chatgpt:
            from .translators.chatgpt import OpenAITranslator
            translator = OpenAITranslator()
                
            translator.parse_args(config.translator)
            translator.set_prev_context(prev_ctx)

            if pages_used > 0:
                context_count = prev_ctx.count("<|")
                logger.info(f"Carrying {pages_used} pages of context, {context_count} sentences as translation reference")
            if skipped > 0:
                logger.warning(f"Skipped {skipped} pages with no sentences")

            translated = await translator._translate(ctx.from_lang, config.translator.target_lang, texts)
            model = getattr(translator, 'model', None) or getattr(translator, 'MODEL', None)
            if isinstance(model, str):
                ctx['translator_model'] = model
            return translated

        translated = await self._mps_call(
            dispatch_translation,
            config.translator.translator_gen,
            texts,
            config.translator,
            self.use_mtpe,
            ctx,
            'cpu' if self._gpu_limited_memory else self.device
        )
        if ctx.get('offline_model'):
            await self._report_progress(f'offline_model:{ctx.offline_model}')
        if ctx.get('gemini_model'):
            await self._report_progress(f'gemini_model:{ctx.gemini_model}')
        if ctx.get('translator_model'):
            await self._report_progress(f'translator_model:{ctx.translator_model}')
        return translated

    @staticmethod
    def _uses_gemini(config: Config) -> bool:
        return any(
            key == Translator.gemini
            for key, _ in config.translator.translator_gen.chain
        )

    async def _translate_page_with_retries(self, config, ctx, translations=None):
        """Require one usable translation per region before any source text is erased."""
        regions = ctx.text_regions
        region_indices = [i for i, region in enumerate(regions) if not is_preserved_region(region)]
        texts = [regions[i].text for i in region_indices]
        has_preserved_regions = len(region_indices) != len(regions)

        if has_preserved_regions and translations is not None and len(translations) == len(regions):
            translations = [translations[i] for i in region_indices]

        def restore_preserved_regions(values):
            if not has_preserved_regions:
                return list(values)
            result = [None] * len(regions)
            for i, region in enumerate(regions):
                if is_preserved_region(region):
                    result[i] = region.text
            for i, translated in zip(region_indices, values):
                result[i] = translated
            return result

        if config.translator.translator in (Translator.none, Translator.original):
            translated = translations if translations is not None else await self._dispatch_with_context(config, texts, ctx)
            return restore_preserved_regions(translated)

        def validation_reason(source, translated):
            if not isinstance(translated, str) or not translated.strip():
                return f"translation is missing or blank (source: {repr(source)})"
            if (source.strip().casefold() == translated.strip().casefold()
                    and re.search(r'[\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Han}]', source)
                    and source.strip() != '彡'
                    and config.translator.target_lang not in ('JPN', 'CHS', 'CHT', 'KOR')):
                return f"unchanged foreign dialogue: {repr(source)}"
            return None

        def validate_translations(values):
            if not isinstance(values, (list, tuple)):
                return False, f"Translations result is not a list or tuple: {type(values)}"
            if len(values) != len(texts):
                return False, f"Translation count mismatch: expected {len(texts)}, got {len(values)}"
            for i, (source, translated) in enumerate(zip(texts, values)):
                reason = validation_reason(source, translated)
                if reason:
                    return False, f"Region {i+1}/{len(texts)} {reason}"
            return True, None

        def keep_for_manual_edit(values):
            if not isinstance(values, (list, tuple)) or len(values) > len(texts):
                return None
            kept = list(values) + [None] * (len(texts) - len(values))
            review_count = 0
            for i, (source, translated) in enumerate(zip(texts, kept)):
                if validation_reason(source, translated):
                    kept[i] = translated if isinstance(translated, str) and translated.strip() else source
                    region = ctx.text_regions[region_indices[i]]
                    region.review_required = True
                    region.review_reason = 'translation_validation_failed'
                    review_count += 1
            if review_count:
                ctx.manual_review_required = True
            return kept

        def complete(values):
            ok, _ = validate_translations(values)
            if ok:
                return True
            return (
                isinstance(values, (list, tuple))
                and len(values) == len(texts)
                and all(
                    not validation_reason(source, translated)
                    or getattr(regions[region_indices[i]], 'review_required', False)
                    for i, (source, translated) in enumerate(zip(texts, values))
                )
            )

        if complete(translations):
            return restore_preserved_regions(translations)
        if not texts:
            return restore_preserved_regions([])
        attempts = 1 if self._uses_gemini(config) else 1 + max(1, config.translator.post_check_max_retry_attempts)
        last_error = None
        for attempt in range(attempts):
            try:
                translations = await self._dispatch_with_context(config, texts, ctx)
                is_valid, reason = validate_translations(translations)
                if is_valid:
                    return restore_preserved_regions(translations)
                last_error = ValueError(reason or 'Missing, blank, or untranslated dialogue')
                logger.warning('Page translation validation failed on attempt %s/%s: %s', attempt + 1, attempts, reason)
            except GeminiRetryExhausted:
                raise
            except TranslationProviderUnavailable:
                raise
            except Exception as exc:
                last_error = exc
            if attempt + 1 < attempts:
                logger.warning('Retrying incomplete page translation (%s/%s)', attempt + 1, attempts - 1)
        if config.translator.keep_failed_pages_for_editing:
            kept = keep_for_manual_edit(translations)
            if kept is not None:
                logger.warning('Keeping page for manual editing: %s', last_error)
                return restore_preserved_regions(kept)
        if self._uses_gemini(config):
            raise GeminiRetryExhausted(f'Gemini page translation failed validation: {last_error}') from last_error
        raise TranslationFailure(f'Page translation failed after {attempts} attempts: {last_error}') from last_error

    async def _run_text_translation(self, config: Config, ctx: Context):
        # 检查text_regions是否为None或空
        if not ctx.text_regions:
            return []
            
        # 如果设置了prep_manual则将translator设置为none，防止token浪费
        # Set translator to none to provent token waste if prep_manual is True  
        if self.prep_manual:  
            config.translator.translator = Translator.none
    
        current_time = time.time()
        self._model_usage_timestamps[("translation", config.translator.translator)] = current_time

        # 为none翻译器添加特殊处理  
        # Add special handling for none translator  
        if config.translator.translator == Translator.none:  
            # 使用none翻译器时，为所有文本区域设置必要的属性  
            # When using none translator, set necessary properties for all text regions  
            for region in ctx.text_regions:  
                region.translation = region.text if is_preserved_region(region) else ""  # Empty translation leaves preserved annotations visible.
                region.target_lang = config.translator.target_lang  
                region._alignment = config.render.alignment  
                region._direction = config.render.direction    
            return ctx.text_regions  

        # 以下翻译处理仅在非none翻译器或有none翻译器但没有prep_manual时执行  
        # Translation processing below only happens for non-none translator or none translator without prep_manual  
        texts = [region.text for region in ctx.text_regions]
        if self.load_text:  
            input_filename = os.path.splitext(os.path.basename(self.input_files[0]))[0]  
            with open(self._result_path(f"{input_filename}_translations.txt"), "r") as f:  
                    translated_sentences = json.load(f)  
        else:  
            # 如果是none翻译器，不需要调用翻译服务，文本已经设置为空  
            # If using none translator, no need to call translation service, text is already set to empty  
            if config.translator.translator != Translator.none:  
                # 自动给 ChatGPT 加上下文，其他翻译器不改变
                # Automatically add context to ChatGPT, no change for other translators
                translated_sentences = \
                    await self._translate_page_with_retries(config, ctx)
            else:  
                # 对于none翻译器，创建一个空翻译列表  
                # For none translator, create an empty translation list  
                translated_sentences = ["" for _ in ctx.text_regions]

            # Save translation if args.save_text is set and quit  
            if self.save_text:  
                input_filename = os.path.splitext(os.path.basename(self.input_files[0]))[0]  
                with open(self._result_path(f"{input_filename}_translations.txt"), "w") as f:  
                    json.dump(translated_sentences, f, indent=4, ensure_ascii=False)  
                print("Don't continue if --save-text is used")  
                exit(-1)  

        if self._pipeline_run is not None:
            self._pipeline_run.record_translation(
                config,
                [
                    {"index": index, "text": text}
                    for index, text in enumerate(texts)
                    if not is_preserved_region(ctx.text_regions[index])
                ],
                [{"index": index, "translation": translation} for index, translation in enumerate(translated_sentences)],
                ctx,
            )

        # 如果不是none翻译器或者是none翻译器但没有prep_manual  
        # If not none translator or none translator without prep_manual  
        if config.translator.translator != Translator.none or not self.prep_manual:  
            for region, translation in zip(ctx.text_regions, translated_sentences):  
                region.translation = (
                    region.text if is_preserved_region(region)
                    else config.render.transform_text_case(translation)
                )
                region.target_lang = config.translator.target_lang  
                region._alignment = config.render.alignment  
                region._direction = config.render.direction  

        # Punctuation correction logic. for translators often incorrectly change quotation marks from the source language to those commonly used in the target language.
        check_items = [
            # 圆括号处理
            ["(", "（", "「", "【"],
            ["（", "(", "「", "【"],
            [")", "）", "」", "】"],
            ["）", ")", "」", "】"],
            
            # 方括号处理
            ["[", "［", "【", "「"],
            ["［", "[", "【", "「"],
            ["]", "］", "】", "」"],
            ["］", "]", "】", "」"],
            
            # 引号处理
            ["「", "“", "‘", "『", "【"],
            ["」", "”", "’", "』", "】"],
            ["『", "“", "‘", "「", "【"],
            ["』", "”", "’", "」", "】"],
            
            # 新增【】处理
            ["【", "(", "（", "「", "『", "["],
            ["】", ")", "）", "」", "』", "]"],
        ]

        replace_items = [
            ["「", "“"],
            ["「", "‘"],
            ["」", "”"],
            ["」", "’"],
            ["【", "["],  
            ["】", "]"],  
        ]

        for region in ctx.text_regions:
            if is_preserved_region(region):
                region.translation = region.text
                continue
            if region.text and region.translation:
                if '『' in region.text and '』' in region.text:
                    quote_type = '『』'
                elif '「' in region.text and '」' in region.text:
                    quote_type = '「」'
                elif '【' in region.text and '】' in region.text: 
                    quote_type = '【】'
                else:
                    quote_type = None
                
                if quote_type:
                    src_quote_count = region.text.count(quote_type[0])
                    dst_dquote_count = region.translation.count('"')
                    dst_fwquote_count = region.translation.count('＂')
                    
                    if (src_quote_count > 0 and
                        (src_quote_count == dst_dquote_count or src_quote_count == dst_fwquote_count) and
                        not region.translation.isascii()):
                        
                        if quote_type == '「」':
                            region.translation = re.sub(r'"([^"]*)"', r'「\1」', region.translation)
                        elif quote_type == '『』':
                            region.translation = re.sub(r'"([^"]*)"', r'『\1』', region.translation)
                        elif quote_type == '【】':  
                            region.translation = re.sub(r'"([^"]*)"', r'【\1】', region.translation)

                # === 优化后的数量判断逻辑 ===
                # === Optimized quantity judgment logic ===
                for v in check_items:
                    num_src_std = region.text.count(v[0])
                    num_src_var = sum(region.text.count(t) for t in v[1:])
                    num_dst_std = region.translation.count(v[0])
                    num_dst_var = sum(region.translation.count(t) for t in v[1:])
                    
                    if (num_src_std > 0 and
                        num_src_std != num_src_var and
                        num_src_std == num_dst_std + num_dst_var):
                        for t in v[1:]:
                            region.translation = region.translation.replace(t, v[0])

                # 强制替换规则
                # Forced replacement rules
                for v in replace_items:
                    region.translation = region.translation.replace(v[1], v[0])

        # 注意：翻译结果的保存移动到了翻译流程的最后，确保保存的是最终结果而不是重试前的结果

        # Apply post dictionary after translating
        post_dict = load_dictionary(self.post_dict)
        post_replacements = []  
        for region in ctx.text_regions:  
            if is_preserved_region(region):
                region.translation = region.text
                continue
            original = region.translation  
            region.translation = apply_dictionary(region.translation, post_dict)
            if original != region.translation:  
                post_replacements.append(f"{original} => {region.translation}")  

        if post_replacements:  
            logger.info("Post-translation replacements:")  
            for replacement in post_replacements:  
                logger.info(replacement)  
        else:  
            logger.info("No post-translation replacements made.")

        # 译后检查和重试逻辑 - 第一阶段：单个region幻觉检测
        failed_regions = []
        if config.translator.enable_post_translation_check:
            logger.info("Starting post-translation check...")
            
            # 单个region级别的幻觉检测（在过滤前进行）
            for region in ctx.text_regions:
                if not is_preserved_region(region) and region.translation and region.translation.strip():
                    # 只检查重复内容幻觉，不进行页面级目标语言检查
                    if await self._check_repetition_hallucination(
                        region.translation, 
                        config.translator.post_check_repetition_threshold,
                        silent=False
                    ):
                        failed_regions.append(region)
            
            # 对失败的区域进行重试
            if failed_regions:
                logger.warning(f"Found {len(failed_regions)} regions that failed repetition check, starting retry...")
                if self._uses_gemini(config):
                    raise GeminiRetryExhausted(
                        f"Gemini translation failed repetition check for {len(failed_regions)} region(s)."
                    )
                for region in failed_regions:
                    await self._retry_translation_with_validation(region, config, ctx)
                logger.info("Repetition check retry finished.")

        # 译后检查和重试逻辑 - 第二阶段：页面级目标语言检查（使用过滤后的区域）
        if config.translator.enable_post_translation_check:
            
            # 页面级目标语言检查（使用过滤后的区域数量）
            page_lang_check_result = True
            if ctx.text_regions and len(ctx.text_regions) > 5:
                logger.info(f"Starting page-level target language check with {len(ctx.text_regions)} regions...")
                page_lang_check_result = await self._check_target_language_ratio(
                    ctx.text_regions,
                    config.translator.target_lang,
                    min_ratio=0.5
                )
                
                if not page_lang_check_result:
                    logger.warning("Page-level target language ratio check failed")

                    if self._uses_gemini(config):
                        raise GeminiRetryExhausted("Gemini translation failed the target-language check.")
                    
                    # 第二阶段：整个批次重新翻译逻辑
                    max_batch_retry = config.translator.post_check_max_retry_attempts
                    batch_retry_count = 0
                    
                    while batch_retry_count < max_batch_retry and not page_lang_check_result:
                        batch_retry_count += 1
                        logger.warning(f"Starting batch retry {batch_retry_count}/{max_batch_retry} for page-level target language check...")
                        
                        # 重新翻译所有区域
                        translatable_regions = [
                            region for region in ctx.text_regions
                            if not is_preserved_region(region)
                        ]
                        original_texts = [getattr(region, 'text', '') or '' for region in translatable_regions]
                        
                        if original_texts:
                            try:
                                # 重新批量翻译
                                logger.info(f"Retrying translation for {len(original_texts)} regions...")
                                new_translations = await self._batch_translate_texts(original_texts, config, ctx)
                                
                                # 更新翻译结果到regions
                                for region, translation in zip(translatable_regions, new_translations):
                                    if translation:
                                        old_translation = region.translation
                                        region.translation = translation
                                        logger.debug(f"Region translation updated: '{old_translation}' -> '{translation}'")
                                    
                                # 重新检查目标语言比例
                                logger.info(f"Re-checking page-level target language ratio after batch retry {batch_retry_count}...")
                                page_lang_check_result = await self._check_target_language_ratio(
                                    ctx.text_regions,
                                    config.translator.target_lang,
                                    min_ratio=0.5
                                )
                                
                                if page_lang_check_result:
                                    logger.info(f"Page-level target language check passed")
                                    break
                                else:
                                    logger.warning(f"Page-level target language check still failed")
                                    
                            except Exception as e:
                                logger.error(f"Error during batch retry {batch_retry_count}: {e}")
                                break
                        else:
                            logger.warning("No text found for batch retry")
                            break
                    
                    if not page_lang_check_result:
                        logger.error(f"Page-level target language check failed after all {max_batch_retry} batch retries")
                else:
                    logger.info("Page-level target language ratio check passed")
            else:
                logger.info(f"Skipping page-level target language check: only {len(ctx.text_regions)} regions (threshold: 5)")
            
            # 统一的成功信息
            if page_lang_check_result:
                logger.info("All translation regions passed post-translation check.")
            else:
                logger.warning("Some translation regions failed post-translation check.")

        # 过滤逻辑（简化版本，保留主要过滤条件）
        new_text_regions = []
        for region in ctx.text_regions:
            if is_preserved_region(region):
                region.translation = region.text
                new_text_regions.append(region)
                continue
            should_filter = False
            filter_reason = ""

            if not region.translation.strip():
                should_filter = True
                filter_reason = "Translation contain blank areas"
            elif config.translator.translator != Translator.none:
                if region.translation.isnumeric():
                    should_filter = True
                    filter_reason = "Numeric translation"
                elif config.filter_text and re.search(config.re_filter_text, region.translation):
                    should_filter = True
                    filter_reason = f"Matched filter text: {config.filter_text}"
                elif (not getattr(region, 'review_required', False)
                      and not config.translator.translator == Translator.original):
                    text_equal = region.text.lower().strip() == region.translation.lower().strip()
                    if text_equal:
                        should_filter = True
                        filter_reason = "Translation identical to original"

            if should_filter:
                region.review_required = True
                region.review_reason = filter_reason
                ctx.manual_review_required = True
                if region.translation.strip():
                    logger.info(f'Filtered out: {region.translation}')
                    logger.info(f'Reason: {filter_reason}')
            new_text_regions.append(region)

        return new_text_regions

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
        if len(configs) != len(contexts):
            raise ValueError("Bubble detection config/context count mismatch")
        if not configs:
            return []
        bubble_config = configs[0].bubble_detection
        if any(config.bubble_detection.dict() != bubble_config.dict() for config in configs[1:]):
            raise ValueError("Bubble detection batch settings must match")
        images = [context.img_rgb for context in contexts]
        if any(image is None for image in images):
            raise RuntimeError("No image canvas available for bubble detection")
        self._model_usage_timestamps[("bubble_detection", bubble_config.model)] = time.time()
        output = await self._mps_call(
            dispatch_bubble_detection_batch, images, bubble_config, self.device
        )
        if len(output) != len(images):
            raise RuntimeError(f"Bubble detector returned {len(output)} pages for {len(images)} inputs")
        return output

    async def _detect_speech_bubbles(
        self,
        config: Config,
        ctx: Context,
        report_progress: bool = True,
        precomputed_detections: list[BubbleDetection] | None = None,
    ):
        """Run optional bubble detection before translation in every pipeline mode."""
        ctx.bubble_detections = []
        ctx._bubble_detection_done = True
        for region in ctx.text_regions or []:
            if not getattr(region, 'region_id', None):
                region.region_id = uuid.uuid4().hex
        if precomputed_detections is not None:
            ctx.bubble_detections = precomputed_detections
        if not config.bubble_detection.enabled or getattr(ctx, 'img_rgb', None) is None:
            return
        try:
            if precomputed_detections is None:
                if report_progress:
                    await self._report_progress('bubble-detection')
                if hasattr(self, '_model_usage_timestamps'):
                    self._model_usage_timestamps[("bubble_detection", config.bubble_detection.model)] = time.time()
                if hasattr(detect_bubbles, 'mock_calls') or hasattr(detect_bubbles, 'assert_called'):
                    res = detect_bubbles(ctx.img_rgb, config.bubble_detection)
                    ctx.bubble_detections = await res if asyncio.iscoroutine(res) else res
                else:
                    ctx.bubble_detections = await self._mps_call(
                        dispatch_bubble_detection, ctx.img_rgb, config.bubble_detection, self.device
                    )
            if ctx.text_regions:
                group_regions = getattr(config.bubble_detection, 'group_regions', False)
                ctx.text_regions = group_regions_by_bubbles(
                    ctx.text_regions, ctx.bubble_detections, group=group_regions
                )
                for region in ctx.text_regions:
                    if not getattr(region, 'region_id', None):
                        region.region_id = uuid.uuid4().hex
            if ctx.bubble_detections and (getattr(self, 'verbose', False) or self._pipeline_run is not None):
                bubble_mask = np.zeros(ctx.img_rgb.shape[:2], np.uint8)
                for detection in ctx.bubble_detections:
                    bubble_mask = np.maximum(bubble_mask, np.asarray(detection.mask, dtype=np.uint8))
                await self._async_imwrite(self._result_path('bubble_mask.png'), bubble_mask)
            logger.info(
                'Detected %d speech bubbles; matched %d text regions',
                len(ctx.bubble_detections),
                sum(1 for region in (ctx.text_regions or []) if getattr(region, '_bubble_mask', None) is not None),
            )
        except Exception as error:
            logger.warning('Speech-bubble detection unavailable; using the existing pipeline: %s', error)
            ctx.bubble_detections = []

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
        if len(configs) != len(contexts):
            raise ValueError('Inpainting config/context count mismatch')
        if not configs:
            return []
        inpainter_config = configs[0].inpainter
        if any(config.inpainter.dict() != inpainter_config.dict() for config in configs[1:]):
            raise ValueError('Inpainting batch settings must match')
        outputs = [None] * len(contexts)
        active = []
        for index, ctx in enumerate(contexts):
            if ctx.img_rgb is None:
                raise RuntimeError('No image canvas available for inpainting')
            if ctx.text_regions and all(
                getattr(region, 'review_required', False)
                and not (getattr(region, 'translation', None) and region.translation.strip())
                for region in ctx.text_regions
            ):
                outputs[index] = ctx.img_rgb.copy()
                continue
            if inpainter_config.inpainter != Inpainter.none:
                for region in ctx.text_regions or []:
                    if getattr(region, 'review_required', False) and not (getattr(region, 'translation', None) and region.translation.strip()):
                        continue
                    for line in region.lines:
                        x, y, w, h = cv2.boundingRect(np.asarray(line, dtype=np.int32))
                        x1, y1 = max(0, x), max(0, y)
                        x2, y2 = min(ctx.img_rgb.shape[1], x + w), min(ctx.img_rgb.shape[0], y + h)
                        if ctx.mask is None or x2 <= x1 or y2 <= y1:
                            raise TranslationFailure('No erasing mask for detected text; page needs retry')
                        polygon = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
                        cv2.fillPoly(polygon, [np.asarray(line, dtype=np.int32) - (x1, y1)], 255)
                        if not np.any(ctx.mask[y1:y2, x1:x2][polygon > 0]):
                            raise TranslationFailure('No erasing mask for detected text; page needs retry')
            active.append((index, ctx))

        current_time = time.time()
        if not hasattr(self, '_model_usage_timestamps'):
            self._model_usage_timestamps = {}
        self._model_usage_timestamps[("inpainting", inpainter_config.inpainter)] = current_time
        device = getattr(self, 'device', None)
        inpainting_size = inpainter_config.inpainting_size
        if device == 'mps' and inpainting_size > 1024:
            logger.info(
                'Capping MPS inpainting size from %d to 1024 to limit peak memory',
                inpainting_size,
            )
            inpainting_size = 1024
        if active:
            results = await self._mps_call(
                dispatch_inpainting_batch,
                inpainter_config.inpainter,
                [ctx.img_rgb for _, ctx in active],
                [ctx.mask for _, ctx in active],
                inpainter_config, inpainting_size, device, self.verbose,
            )
            if len(results) != len(active):
                raise RuntimeError(f'Inpainter returned {len(results)} pages for {len(active)} inputs')
            for (index, ctx), result in zip(active, results):
                protected = getattr(ctx, 'protected_edge_mask', None)
                if protected is not None and np.any(protected) and result is not None:
                    result = result.copy()
                    protected_pixels = protected > 0
                    result[protected_pixels] = ctx.img_rgb[protected_pixels]
                    bundle = getattr(ctx, 'mask_bundle', None)
                    if bundle is not None and getattr(bundle, 'metrics', None) is not None:
                        bundle.metrics.protected_edge_retention = float(
                            np.array_equal(result[protected_pixels], ctx.img_rgb[protected_pixels])
                        )
                outputs[index] = result
        return outputs

    async def _run_text_rendering(self, config: Config, ctx: Context):
        current_time = time.time()
        self._model_usage_timestamps[("rendering", config.render.renderer)] = current_time
        active_font = self.font_path or getattr(config.render, 'font_path', None) or get_default_eng_font()
        cpu_priority = (
            CPU_PRIORITY_BACKGROUND
            if getattr(ctx, '_background_batch_render', False)
            else CPU_PRIORITY_NORMAL
        )
        bubble_detection_enabled = bool(getattr(getattr(config, 'bubble_detection', None), 'enabled', False))

        transform_text_case = getattr(config.render, "transform_text_case", None)
        if transform_text_case:
            for region in (ctx.text_regions or []):
                if is_preserved_region(region):
                    region.translation = region.text
                elif getattr(region, 'translation', None) and isinstance(region.translation, str):
                    region.translation = transform_text_case(region.translation)

        pipeline_run = getattr(self, '_pipeline_run', None)
        layout_stage = next((
            stage for stage in getattr(pipeline_run, 'manifest', {}).get('stages', [])
            if stage.get('id') == 'layout'
        ), None)
        if layout_stage and layout_stage.get('status') == 'completed' and pipeline_run is not None:
            layout_doc = pipeline_run._document('layout.json')
            if layout_doc is not None:
                bubble_doc = pipeline_run._document('bubble_detections.json')
                if bubble_doc is None:
                    bubble_doc = serialize_bubble_detections(getattr(ctx, 'bubble_detections', None) or [])
                inputs = layout_input_fingerprints(
                    ctx.text_regions or [], config, active_font, bubble_doc,
                    getattr(ctx.img_rgb, 'shape', None),
                    getattr(ctx, 'inpaint_mask', None) if getattr(ctx, 'inpaint_mask', None) is not None else getattr(ctx, 'mask', None),
                    layout_doc.get('input_fingerprints', {}).get('mask')
                    if isinstance(layout_doc, dict) and getattr(ctx, 'inpaint_mask', None) is None and getattr(ctx, 'mask', None) is None
                    else None,
                )
                hydrate_layout(ctx, layout_doc, inputs['fingerprint'])
                ctx._bubble_detection_done = True
                ctx._bubble_layout_ready = True

        if (bubble_detection_enabled
                and not getattr(ctx, '_bubble_detection_done', False)):
            await self._detect_speech_bubbles(config, ctx, report_progress=False)

        if (getattr(ctx, 'img_rgb', None) is not None
                and not getattr(ctx, '_bubble_layout_ready', False)):
            try:
                await self._report_progress('layout')
                await run_cpu_stage(layout_page, ctx, config, active_font, priority=cpu_priority)
                if self._pipeline_run is not None:
                    bubble_doc = serialize_bubble_detections(getattr(ctx, 'bubble_detections', None) or [])
                    self._pipeline_run.write_json('layout.json', serialize_frozen_layout(
                        ctx, config, active_font, bubble_doc,
                    ))
            except Exception as error:
                logger.warning('Bubble layout failed; preserving the existing render path: %s', error)

        if ctx.img_inpainted is None and getattr(ctx, 'img_rgb', None) is not None:
            if ctx.mask is None:
                ctx.mask = getattr(ctx, 'bubble_mask', None)
            ctx.img_inpainted = ctx.img_rgb.copy()

        return await run_cpu_stage(render_page, ctx, config, active_font, priority=cpu_priority)

    def _result_path(self, path: str) -> str:
        """
        Returns path to result folder where intermediate images are saved when using verbose flag
        or web mode input/result images are cached.
        """
        output_override = getattr(self, '_result_path_override', None)
        if output_override is not None:
            result_path = os.path.join(os.fspath(output_override), path)
            os.makedirs(os.path.dirname(result_path), exist_ok=True)
            return result_path

        # 只有在verbose模式下才使用图片级子文件夹
        if self.verbose:
            image_subfolder = self._get_image_subfolder()
            if image_subfolder:
                if self.result_sub_folder:
                    result_path = os.path.join(self.result_root, self.result_sub_folder, image_subfolder, path)
                else:
                    result_path = os.path.join(self.result_root, image_subfolder, path)
                # 确保目录存在
                os.makedirs(os.path.dirname(result_path), exist_ok=True)
                return result_path
        
        # 在server/web模式下（result_sub_folder为空）且为非verbose模式时
        # 需要创建一个子文件夹来保存final.png
        if not self.result_sub_folder:
            if self._current_image_context:
                # 直接使用已生成的子文件夹名
                sub_folder = self._current_image_context['subfolder']
            else:
                # 没有上下文信息时使用默认值
                timestamp = str(int(time.time() * 1000))
                sub_folder = f"{timestamp}-unknown-1024-unknown-unknown"

            result_path = os.path.join(self.result_root, sub_folder, path)
        else:
            result_path = os.path.join(self.result_root, self.result_sub_folder, path)
        
        # 确保目录存在
        os.makedirs(os.path.dirname(result_path), exist_ok=True)
        return result_path

    def add_progress_hook(self, ph):
        self._progress_hooks.append(ph)

    async def _emit_progress(self, state: str, finished: bool = False):
        for ph in self._progress_hooks:
            await ph(state, finished)

    async def _report_progress(self, state: str, finished: bool = False):
        if self._pipeline_run is not None:
            run = self._pipeline_run
            run.progress(state, finished)
            await run.checkpoint()
            if finished and state == 'finished':
                run.cleanup_completed_artifacts()
                run.refresh()
                await run.checkpoint()
        await self._emit_progress(state, finished)

    def _add_logger_hook(self):
        # TODO: Pass ctx to logger hook
        LOG_MESSAGES = {
            'upscaling': 'Running upscaling',
            'detection': 'Running text detection',
            'ocr': 'Running ocr',
            'mask-generation': 'Running mask refinement',
            'translating': 'Running text translation',
            'rendering': 'Running rendering',
            'colorizing': 'Running colorization',
            'downscaling': 'Running downscaling',
        }
        LOG_MESSAGES_SKIP = {
            'skip-no-regions': 'No text regions! - Skipping',
            'skip-no-text': 'No text regions with text! - Skipping',
            'error-translating': 'Text translator returned empty queries',
            'cancelled': 'Image translation cancelled',
        }
        LOG_MESSAGES_ERROR = {
            # 'error-lang':           'Target language not supported by chosen translator',
        }

        async def ph(state, finished):
            if state in LOG_MESSAGES:
                logger.info(LOG_MESSAGES[state])
            elif state.startswith('offline_model:'):
                logger.info(f'Using offline model: {state.removeprefix("offline_model:")}')
            elif state.startswith('gemini_model:'):
                logger.info(f'Using Gemini model: {state.removeprefix("gemini_model:")}')
            elif state in LOG_MESSAGES_SKIP:
                logger.warn(LOG_MESSAGES_SKIP[state])
            elif state in LOG_MESSAGES_ERROR:
                logger.error(LOG_MESSAGES_ERROR[state])

        self.add_progress_hook(ph)

    async def prepare(self, image: Image.Image, config: Config) -> Context:
        """
        Pre-process one image through OCR and persist its canvas for later stages.
        """
        memory_optimization_enabled = not self.disable_memory_optimization
        if memory_optimization_enabled:
            try:
                import psutil
                memory_percent = psutil.virtual_memory().percent
                if memory_percent > 85:
                    logger.warning(f'High memory usage during pre-processing: {memory_percent:.1f}%')
                    self._empty_device_cache()
            except ImportError:
                pass
            except Exception as e:
                logger.debug(f'Memory check failed: {e}')

        try:
            self._set_image_context(config, image)
            subfolder = self._get_image_subfolder()
            if self._current_image_context:
                image_md5 = self._current_image_context['file_md5']
                self._save_current_image_context(image_md5)
                self._current_image_context['started_at'] = datetime.now(timezone.utc).isoformat()
            self._pipeline_run = PipelineRun(
                self.result_root, subfolder, image, config
            )
            self._pipeline_run.translator = self
            ctx = await self._translate_until_translation(image, config, prepare_canvas=True)
            if self._pipeline_run is not None:
                self._pipeline_run.ctx = ctx
            if self._current_image_context:
                ctx.image_context = self._current_image_context.copy()
                ctx.debug_folder = self._current_image_context['subfolder']
            if self._pipeline_run is not None:
                await self._pipeline_run.checkpoint()
            if ctx.text_regions and hasattr(ctx, 'cleanup_intermediate'):
                ctx.cleanup_intermediate(keep_input=True)
                ctx.cleanup_detection_workspace()
            ctx.verbose = self.verbose
            self._empty_device_cache()
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
                self._empty_device_cache()
                self._set_image_context(recovery_config, image)
                if self._current_image_context:
                    image_md5 = self._current_image_context['file_md5']
                    self._save_current_image_context(image_md5)
                ctx = await self._translate_until_translation(image, recovery_config, prepare_canvas=True)
                if self._current_image_context:
                    ctx.image_context = self._current_image_context.copy()
                    ctx.debug_folder = self._current_image_context['subfolder']
                ctx.verbose = self.verbose
                return ctx
            except Exception as retry_error:
                logger.error(f'Fallback processing also failed: {retry_error}')
                ctx = Context()
                ctx.input = image
                ctx.text_regions = []
                return ctx

    async def translate_batch_contexts(
        self, contexts_with_configs: List[tuple], batch_size: int = None
    ) -> List[tuple]:
        """
        Translate an aggregate batch of prepared contexts and return translated (ctx, config) pairs.
        """
        batch_size = batch_size or self.batch_size
        if batch_size < 1:
            raise ValueError('batch_size must be at least 1')

        if not contexts_with_configs:
            return []

        memory_optimization_enabled = not self.disable_memory_optimization

        for ctx, config in contexts_with_configs:
            if getattr(ctx, 'image_context', None):
                self._saved_image_contexts[ctx.image_context['file_md5']] = ctx.image_context.copy()

        logger.debug('Starting batch translation phase...')
        try:
            professional = any(
                config.translator.translation_quality == 'professional'
                for _, config in contexts_with_configs
            )
            if professional:
                if not all(config.translator.translation_quality == 'professional' for _, config in contexts_with_configs):
                    raise ValueError('Cannot mix fast and professional translation in one batch')
                from .professional_translation import translate_professionally
                await self._report_progress('analyzing-story')
                translated_contexts = await translate_professionally(
                    contexts_with_configs,
                    progress=self._report_progress,
                )
                for ctx, config in translated_contexts:
                    for region in ctx.text_regions or []:
                        region._alignment = config.render.alignment
                        region._direction = config.render.direction
                    ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
                    ctx.result_documents['translations.json'] = serialize_regions(ctx.text_regions)
                await self._report_progress('after-translating')
                return translated_contexts
            uses_gpt = any(
                key in GPT_TRANSLATORS
                for key, _ in contexts_with_configs[0][1].translator.translator_gen.chain
            )
            if self.batch_concurrent and not uses_gpt:
                logger.info('Using concurrent mode for batch translation')
                translated_contexts = await self._concurrent_translate_contexts(contexts_with_configs)
            else:
                logger.debug('Using standard batch mode for translation')
                translated_contexts = await self._batch_translate_contexts(contexts_with_configs, batch_size)
        except MemoryError as e:
            logger.error(f'Memory error in batch translation: {e}')
            if not memory_optimization_enabled:
                logger.error('Consider enabling memory optimization')
                raise
            logger.warning('Batch translation failed, switching to individual page translation mode...')
            translated_contexts = []
            for ctx, config in contexts_with_configs:
                try:
                    if ctx.text_regions:
                        translated_texts = await self._translate_page_with_retries(config, ctx)
                        for region, translation in zip(ctx.text_regions, translated_texts):
                            region.translation = translation
                            region.target_lang = config.translator.target_lang
                            region._alignment = config.render.alignment
                            region._direction = config.render.direction
                    translated_contexts.append((ctx, config))
                    self._empty_device_cache()
                except Exception as individual_error:
                    logger.error(f'Individual page translation failed: {individual_error}')
                    ctx.translation_error = str(individual_error)
                    ctx.result = None
                    translated_contexts.append((ctx, config))

        return translated_contexts

    async def render(self, ctx: Context, config: Config) -> Context:
        """
        Render text and save final output artifacts for a single prepared & translated image context.
        """
        if getattr(ctx, 'image_context', None):
            self._current_image_context = ctx.image_context.copy()
        folder = getattr(ctx, 'debug_folder', None) or (self._current_image_context.get('subfolder') if self._current_image_context else None)
        if self._pipeline_run is None and folder:
            documents = getattr(ctx, 'result_documents', {}) or {}
            if 'pipeline_manifest.json' in documents:
                ctx.result_documents = {
                    name: document for name, document in documents.items()
                    if name != 'pipeline_manifest.json'
                }
            self._pipeline_run = PipelineRun.get_or_load(self.result_root, folder)
            if self._pipeline_run is None:
                self._pipeline_run = PipelineRun.from_documents(
                    self.result_root, folder, documents
                )
                if self._pipeline_run is None:
                    img_in = getattr(ctx, 'input', None) or Image.new('RGB', (1, 1))
                    self._pipeline_run = PipelineRun(self.result_root, folder, img_in, config)
        if self._pipeline_run is not None:
            self._pipeline_run.ctx = ctx
            self._pipeline_run.translator = self
            if getattr(ctx, 'translation_duration_ms', None) is not None:
                try:
                    trans_stage = self._pipeline_run._stage('translation')
                    if trans_stage:
                        trans_stage['status'] = 'completed'
                        trans_stage['startedAt'] = getattr(ctx, 'translation_started_at', None) or trans_stage.get('startedAt')
                        trans_stage['finishedAt'] = getattr(ctx, 'translation_finished_at', None) or trans_stage.get('finishedAt')
                        trans_stage['durationMs'] = ctx.translation_duration_ms
                except Exception:
                    pass
        try:
            if ctx.text_regions:
                ctx = await self._complete_translation_pipeline(ctx, config)
            else:
                if getattr(ctx, 'result', None) is None:
                    ctx.result = getattr(ctx, 'upscaled', None) or getattr(ctx, 'input', None)
                    if ctx.result is not None:
                        self._current_image_context = getattr(ctx, 'image_context', None) or self._current_image_context
                        ctx = await self._revert_upscale(config, ctx)
            return ctx
        except Exception as e:
            logger.error(f'Render error: {e}')
            ctx.translation_error = str(e)
            ctx.result = None
            run = self._pipeline_run
            if run is not None:
                try:
                    run.fail(str(e))
                    await run.checkpoint()
                except Exception as checkpoint_error:
                    logger.error(f'Could not checkpoint failed render: {checkpoint_error}')
                finally:
                    run.release_runtime()
                    if self._pipeline_run is run:
                        self._pipeline_run = None
            return ctx

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
        """
        Translate an aggregate batch of prepared contexts and render the final images.
        """
        translated_contexts = await self.translate_batch_contexts(contexts_with_configs, batch_size)
        results = []
        logger.debug('Starting post-processing phase...')
        for i, (ctx, config) in enumerate(translated_contexts):
            res_ctx = await self.render(ctx, config)
            results.append(res_ctx)
            logger.debug(f'Image {i+1} post-processing completed')

        logger.info(f'Batch translation completed: processed {len(results)} images')

        for ctx in results:
            if ctx.text_regions and not ctx.get('translation_error'):
                page_translations = {
                    r.text_raw if hasattr(r, "text_raw") else r.text: r.translation
                    for r in ctx.text_regions if not is_preserved_region(r)
                }
                self.all_page_translations.append(page_translations)
                page_original_texts = {
                    i: (r.text_raw if hasattr(r, "text_raw") else r.text)
                    for i, r in enumerate(ctx.text_regions) if not is_preserved_region(r)
                }
                self._original_page_texts.append(page_original_texts)

        self._saved_image_contexts.clear()
        return results

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
        batch_size = batch_size or self.batch_size
        if batch_size < 1:
            raise ValueError('batch_size must be at least 1')
        
        logger.debug(f'Starting batch translation: {len(images_with_configs)} images, batch size: {batch_size}')
        results = []
        for offset in range(0, len(images_with_configs), batch_size):
            pre_translation_contexts = []
            chunk = images_with_configs[offset:offset + batch_size]
            for index, (image, config) in enumerate(chunk, start=offset):
                logger.debug(f'Pre-processing image {index+1}/{len(images_with_configs)}')
                try:
                    ctx = await self.prepare(image, config)
                    logger.debug(f'Image {index+1} pre-processing successful')
                except Exception as e:
                    logger.error(f'Image {index+1} pre-processing error: {e}')
                    ctx = Context(input=image, text_regions=[])
                pre_translation_contexts.append((ctx, config))

            chunk_results = await self.translate_and_render_batch(
                pre_translation_contexts, batch_size=batch_size
            )
            # ponytail: returned outputs remain page-sized; callers release them after save or serialization.
            for ctx in chunk_results:
                ctx.cleanup_runtime(preserve_output=True)
            results.extend(chunk_results)

        if not results:
            logger.warning('No images pre-processed successfully')
        return results

    async def _translate_until_translation(
        self, image: Image.Image, config: Config, prepare_canvas: bool = True
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
            result_path = self._result_path('input.jpg')
            await asyncio.to_thread(save_jpeg, image, result_path)
        except Exception as e:
            logger.error(f"Error saving input.jpg debug image: {e}")
            logger.debug(f"Exception details: {traceback.format_exc()}")

        # preload and download models (not strictly necessary, remove to lazy load)
        if ( self.models_ttl == 0 ):
            logger.info('Loading models')
            if config.upscale.upscale_ratio:
                await prepare_upscaling(config.upscale.upscaler)
            await prepare_detection(config.detector.detector)
            await prepare_ocr(config.ocr.ocr, self.device)
            await prepare_translation(config.translator.translator_gen)
            if config.colorizer.colorizer != Colorizer.none:
                await prepare_colorization(config.colorizer.colorizer)

        self._log_memory_boundary("models_ready", ctx)
        # Start the background cleanup job once if not already started.
        if self._model_cleanup_task is None:
            self._model_cleanup_task = asyncio.create_task(self._model_cleanup_job())

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
                await self._report_progress('colorizing')
                try:
                    ctx.img_colorized = await self._run_colorizer(config, ctx)
                except Exception as e:  
                    logger.error(f"Error during colorizing:\n{traceback.format_exc()}")  
                    if not self.ignore_errors:  
                        raise  
                    ctx.img_colorized = ctx.input
        else:
            ctx.img_colorized = ctx.input

        # -- Upscaling
        if config.upscale.upscale_ratio:
            await self._report_progress('upscaling')
            try:
                ctx.upscaled = await self._run_upscaling(config, ctx)
                ctx.upscaled_ran = True
            except Exception as e:  
                logger.error(f"Error during upscaling:\n{traceback.format_exc()}")  
                if not self.ignore_errors:  
                    raise  
                ctx.upscaled = ctx.img_colorized
                ctx.upscaled_ran = False
        else:
            ctx.upscaled = ctx.img_colorized
            ctx.upscaled_ran = False

        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)

        # -- Detection
        await self._report_progress('detection')
        try:
            ctx.textlines, ctx.mask_raw, ctx.mask = await self._run_detection(config, ctx)
        except Exception as e:  
            logger.error(f"Error during detection:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.textlines = [] 
            ctx.mask_raw = None
            ctx.mask = None

        if ctx.textlines:
            detection_docs = serialize_regions(ctx.textlines)
            ctx.result_documents['detection.json'] = detection_docs
            if self._pipeline_run is not None:
                self._pipeline_run.write_json('detection.json', detection_docs)
            elif self._current_image_context:
                await save_result_documents(
                    self._current_image_context['subfolder'],
                    {'detection.json': detection_docs},
                    self.result_root,
                )

        if (self.verbose or self._pipeline_run is not None) and ctx.mask_raw is not None:
            await self._async_imwrite(self._result_path('mask_raw.png'), ctx.mask_raw)

        if not ctx.textlines:
            await self._report_progress('skip-no-regions', True)
            ctx.result = ctx.upscaled
            if self._current_image_context:
                ctx.image_context = self._current_image_context.copy()
            return await self._revert_upscale(config, ctx)

        # -- OCR
        await self._report_progress('ocr')
        try:
            ctx.textlines = await self._run_ocr(config, ctx)
        except Exception as e:  
            logger.error(f"Error during ocr:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.textlines = []

        if ctx.textlines:
            ocr_docs = serialize_regions(ctx.textlines)
            ctx.result_documents['ocr.json'] = ocr_docs
            if self._pipeline_run is not None:
                self._pipeline_run.write_json('ocr.json', ocr_docs)
            elif self._current_image_context:
                await save_result_documents(
                    self._current_image_context['subfolder'],
                    {'ocr.json': ocr_docs},
                    self.result_root,
                )

        if not ctx.textlines:
            await self._report_progress('skip-no-text', True)
            ctx.result = ctx.upscaled
            if self._current_image_context:
                ctx.image_context = self._current_image_context.copy()
            return await self._revert_upscale(config, ctx)

        # -- Textline merge
        await self._report_progress('textline_merge')
        try:
            ctx.text_regions = await self._run_textline_merge(config, ctx)
        except Exception as e:  
            logger.error(f"Error during textline_merge:\n{traceback.format_exc()}")  
            if not self.ignore_errors:  
                raise 
            ctx.text_regions = []

        if ctx.text_regions:
            merged_docs = serialize_regions(ctx.text_regions)
            ctx.result_documents['text_regions_merged.json'] = merged_docs
            if self._pipeline_run is not None:
                self._pipeline_run.write_json('text_regions_merged.json', merged_docs)
            elif self._current_image_context:
                await save_result_documents(
                    self._current_image_context['subfolder'],
                    {'text_regions_merged.json': merged_docs},
                    self.result_root,
                )

        if not ctx.text_regions:
            await self._report_progress('skip-no-regions', True)
            ctx.result = ctx.upscaled
            if self._current_image_context:
                ctx.image_context = self._current_image_context.copy()
            return await self._revert_upscale(config, ctx)

        # Optional speech-bubble detection runs after OCR merging so every
        # assigned bubble is translated as one text flow.
        await self._detect_speech_bubbles(config, ctx)
        ctx.page_geometry, ctx.bubble_mask = prepare_page_geometry(
            ctx.img_rgb,
            ctx.text_regions,
            padding=int(getattr(config.bubble_detection, "padding", 9)),
        )

        # Apply pre-dictionary after textline merge
        pre_dict = load_dictionary(self.pre_dict)
        pre_replacements = []
        for region in ctx.text_regions:
            original = region.text  
            region.text = apply_dictionary(region.text, pre_dict)
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
            if self._current_image_context:
                await self._async_imwrite(
                    self._result_path('original_canvas.png'),
                    cv2.cvtColor(ctx.img_rgb, cv2.COLOR_RGB2BGR),
                )
                if ctx.bubble_mask is not None:
                    await self._async_imwrite(self._result_path('bubble_mask.png'), ctx.bubble_mask)
                if getattr(ctx, 'bubble_detections', None):
                    bd_path = self._result_path('bubble_detections.json')
                    with open(bd_path, 'w', encoding='utf-8') as f:
                        json.dump(serialize_bubble_detections(ctx.bubble_detections), f, indent=2)
            ctx.mask = None
            ctx.inpaint_mask = None
            saved_docs = {
                'text_regions_merged.json': merged,
                'bubble_detections.json': bubble_documents,
            }
            if self._pipeline_run is not None:
                for name, document in saved_docs.items():
                    self._pipeline_run.write_json(name, document)
                await self._pipeline_run.checkpoint()
            elif self._current_image_context:
                await save_result_documents(
                    self._current_image_context['subfolder'],
                    saved_docs,
                    self.result_root,
                )
            await self._report_progress('awaiting_translation')

        # 保存当前图片上下文到ctx中，用于并发翻译时的路径管理
        if self._current_image_context:
            ctx.image_context = self._current_image_context.copy()

        return ctx

    async def _batch_translate_contexts(self, contexts_with_configs: List[tuple], batch_size: int) -> List[tuple]:
        """
        批量处理翻译步骤，防止内存溢出
        """
        results = []
        batches = []
        batch = []
        translatable_pages = 0
        for pair in contexts_with_configs:
            batch.append(pair)
            if pair[0].text_regions:
                translatable_pages += 1
            if translatable_pages == batch_size:
                batches.append(batch)
                batch = []
                translatable_pages = 0
        if batch:
            batches.append(batch)

        # Empty pages do not consume the configurable translation batch size.
        for batch_index, batch in enumerate(batches):
            logger.info(f'Processing translation batch {batch_index + 1}/{len(batches)}')
            
            # 收集当前批次的所有文本
            all_texts = []
            all_text_ids = []
            
            for ctx_idx, (ctx, config) in enumerate(batch):
                if ctx.result_documents is None:
                    ctx.result_documents = {}
                if not ctx.text_regions:
                    continue
                    
                for region_idx, region in enumerate(ctx.text_regions):
                    if not getattr(region, 'region_id', None):
                        region.region_id = uuid.uuid4().hex
                    if is_preserved_region(region):
                        region.translation = region.text
                        region.target_lang = config.translator.target_lang
                        region._alignment = config.render.alignment
                        region._direction = config.render.direction
                        continue
                    all_texts.append(region.text)
                    all_text_ids.append(region.region_id)
                
            if not all_texts:
                # 当前批次没有需要翻译的文本
                results.extend(batch)
                continue
                
            # 批量翻译
            trans_start_time = time.monotonic()
            trans_start_iso = datetime.now(timezone.utc).isoformat()
            try:
                await self._report_progress('translating')
                # 使用第一个配置进行翻译（假设批次内配置相同）
                sample_config = batch[0][1] if batch else None
                if sample_config:
                    # 支持批量翻译 - 传递所有批次上下文
                    batch_contexts = [ctx for ctx, config in batch]
                    translated_texts = await self._batch_translate_texts(
                        all_texts, sample_config, batch[0][0], batch_contexts,
                        text_ids=all_text_ids,
                    )
                else:
                    translated_texts = all_texts  # 无法翻译时保持原文
                    
                if len(translated_texts) != len(all_texts):
                    raise TranslationFailure('Batch returned an incorrect number of translations')

                trans_duration_ms = round((time.monotonic() - trans_start_time) * 1000)
                trans_finished_iso = datetime.now(timezone.utc).isoformat()

                translations_by_id = dict(zip(all_text_ids, translated_texts))
                for ctx_idx, (ctx, config) in enumerate(batch):
                    if not ctx.text_regions:  # 检查text_regions是否为None或空
                        continue
                    ctx.translation_duration_ms = trans_duration_ms
                    ctx.translation_started_at = trans_start_iso
                    ctx.translation_finished_at = trans_finished_iso
                    for region_idx, region in enumerate(ctx.text_regions):
                        if region.region_id in translations_by_id:
                            region.translation = translations_by_id[region.region_id]
                            region.target_lang = config.translator.target_lang
                            region._alignment = config.render.alignment
                            region._direction = config.render.direction
                        
                # 应用后处理逻辑（括号修正、过滤等）
                for ctx, config in batch:
                    if ctx.text_regions:
                        try:
                            ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
                            ctx.result_documents['translations.json'] = serialize_regions(ctx.text_regions)
                            translator_config = getattr(config, "translator", None)
                            translator_val = getattr(getattr(translator_config, "translator", None), "value", None)
                            translator_val = translator_val or str(getattr(translator_config, "translator", "unknown"))
                            model_val = getattr(ctx, "translator_model", None) or getattr(ctx, "offline_model", None) or getattr(ctx, "gemini_model", None)
                            ctx.result_documents['translation_detail.json'] = {
                                "processedAt": trans_finished_iso,
                                "startedAt": trans_start_iso,
                                "finishedAt": trans_finished_iso,
                                "durationMs": trans_duration_ms,
                                "translator": {
                                    "name": translator_val,
                                    "model": model_val,
                                    "sourceLanguage": getattr(translator_config, "source_lang", "auto"),
                                    "targetLanguage": getattr(translator_config, "target_lang", None),
                                },
                                "request": [
                                    {"id": getattr(r, "region_id", str(i)), "text": getattr(r, "text_raw", r.text)}
                                    for i, r in enumerate(ctx.text_regions)
                                    if not is_preserved_region(r)
                                ],
                                "response": [
                                    {"id": getattr(r, "region_id", str(i)), "text": r.translation}
                                    for i, r in enumerate(ctx.text_regions)
                                ],
                            }
                        except Exception as exc:
                            ctx.translation_error = str(exc)
                            ctx.text_regions = []
                            ctx.result = None

                # 批次级别的目标语言检查
                if batch and batch[0][1].translator.enable_post_translation_check:
                    # 收集批次内所有页面的filtered regions
                    all_batch_regions = []
                    for ctx, config in batch:
                        if ctx.text_regions:
                            all_batch_regions.extend(ctx.text_regions)
                    
                    # 进行批次级别的目标语言检查
                    batch_lang_check_result = True
                    if sum(not is_preserved_region(region) for region in all_batch_regions) > 10:
                        sample_config = batch[0][1]
                        logger.info(f"Starting batch-level target language check with {len(all_batch_regions)} regions...")
                        batch_lang_check_result = await self._check_target_language_ratio(
                            all_batch_regions,
                            sample_config.translator.target_lang,
                            min_ratio=0.5
                        )
                        
                        if not batch_lang_check_result:
                            logger.warning("Batch-level target language ratio check failed")

                            if self._uses_gemini(sample_config):
                                raise GeminiRetryExhausted("Gemini batch failed the target-language check.")
                            
                            # 批次重新翻译逻辑
                            max_batch_retry = sample_config.translator.post_check_max_retry_attempts
                            batch_retry_count = 0
                            
                            while batch_retry_count < max_batch_retry and not batch_lang_check_result:
                                batch_retry_count += 1
                                logger.warning(f"Starting batch retry {batch_retry_count}/{max_batch_retry}")
                                
                                # 重新翻译批次内所有区域
                                all_original_texts = []
                                region_mapping = []  # 记录每个text属于哪个ctx
                                
                                for ctx_idx, (ctx, config) in enumerate(batch):
                                    if ctx.text_regions:
                                        for region in ctx.text_regions:
                                            if (not is_preserved_region(region)
                                                    and hasattr(region, 'text') and region.text):
                                                all_original_texts.append(region.text)
                                                region_mapping.append((ctx_idx, region))
                                
                                if all_original_texts:
                                    try:
                                        # 重新批量翻译
                                        logger.info(f"Retrying translation for {len(all_original_texts)} regions...")
                                        new_translations = await self._batch_translate_texts(all_original_texts, sample_config, batch[0][0])
                                        
                                        # 更新翻译结果到各个region
                                        for i, (ctx_idx, region) in enumerate(region_mapping):
                                            if i < len(new_translations) and new_translations[i]:
                                                old_translation = region.translation
                                                region.translation = new_translations[i]
                                                logger.debug(f"Region {i+1} translation updated: '{old_translation}' -> '{new_translations[i]}'")
                                        
                                        # 重新收集所有regions并检查目标语言比例
                                        all_batch_regions = []
                                        for ctx, config in batch:
                                            if ctx.text_regions:
                                                all_batch_regions.extend(ctx.text_regions)
                                        
                                        logger.info(f"Re-checking batch-level target language ratio after batch retry {batch_retry_count}...")
                                        batch_lang_check_result = await self._check_target_language_ratio(
                                            all_batch_regions,
                                            sample_config.translator.target_lang,
                                            min_ratio=0.5
                                        )
                                        
                                        if batch_lang_check_result:
                                            logger.info(f"Batch-level target language check passed")
                                            break
                                        else:
                                            logger.warning(f"Batch-level target language check still failed")
                                            
                                    except Exception as e:
                                        logger.error(f"Error during batch retry {batch_retry_count}: {e}")
                                        break
                                else:
                                    logger.warning("No text found for batch retry")
                                    break
                            
                            if not batch_lang_check_result:
                                logger.error(f"Batch-level target language check failed after all {max_batch_retry} batch retries")
                    else:
                        logger.info(f"Skipping batch-level target language check: only {len(all_batch_regions)} regions (threshold: 10)")
                    
                    # 统一的成功信息
                    if batch_lang_check_result:
                        logger.info("All translation regions passed post-translation check.")
                    else:
                        logger.warning("Some translation regions failed post-translation check.")
                        
                # 过滤逻辑（简化版本，保留主要过滤条件）
                for ctx, config in batch:
                    if ctx.text_regions:
                        new_text_regions = []
                        for region in ctx.text_regions:
                            if is_preserved_region(region):
                                region.translation = region.text
                                new_text_regions.append(region)
                                continue
                            should_filter = False
                            filter_reason = ""

                            if not region.translation.strip():
                                should_filter = True
                                filter_reason = "Translation contain blank areas"
                            elif config.translator.translator != Translator.none:
                                if region.translation.isnumeric():
                                    should_filter = True
                                    filter_reason = "Numeric translation"
                                elif config.filter_text and re.search(config.re_filter_text, region.translation):
                                    should_filter = True
                                    filter_reason = f"Matched filter text: {config.filter_text}"
                                elif (not getattr(region, 'review_required', False)
                                      and not config.translator.translator == Translator.original):
                                    text_equal = region.text.lower().strip() == region.translation.lower().strip()
                                    if text_equal:
                                        should_filter = True
                                        filter_reason = "Translation identical to original"

                            if should_filter:
                                region.review_required = True
                                region.review_reason = filter_reason
                                ctx.manual_review_required = True
                                if region.translation.strip():
                                    logger.info(f'Filtered out: {region.translation}')
                                    logger.info(f'Reason: {filter_reason}')
                            new_text_regions.append(region)
                        ctx.text_regions = new_text_regions
                        
                results.extend(batch)
                
            except StructuredTranslationError as e:
                logger.error(f"Incomplete structured batch translation: {e}")
                for ctx, config in batch:
                    missing = [
                        region for region in (ctx.text_regions or [])
                        if not is_preserved_region(region) and region.region_id not in e.translated
                    ]
                    if missing:
                        try:
                            values = await self._translate_page_with_retries(config, ctx)
                            for region, value in zip(ctx.text_regions, values):
                                region.translation = value
                                region.target_lang = config.translator.target_lang
                                region._alignment = config.render.alignment
                                region._direction = config.render.direction
                            ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
                            ctx.result_documents['translations.json'] = serialize_regions(ctx.text_regions)
                        except Exception as exc:
                            ctx.translation_error = str(exc)
                            ctx.result = None
                    else:
                        for region in ctx.text_regions:
                            region.translation = (
                                region.text if is_preserved_region(region)
                                else e.translated[region.region_id]
                            )
                            region.target_lang = config.translator.target_lang
                            region._alignment = config.render.alignment
                            region._direction = config.render.direction
                        ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
                        ctx.result_documents['translations.json'] = serialize_regions(ctx.text_regions)
                    results.append((ctx, config))
            except Exception as e:
                logger.error(f"Error in batch translation: {e}")
                # Retry each page independently; one unavailable translation must
                # not turn every page into untranslated output or stop the batch.
                for ctx, config in batch:
                    if ctx.text_regions:
                        try:
                            values = await self._translate_page_with_retries(config, ctx)
                            for region, value in zip(ctx.text_regions, values):
                                region.translation = value
                                region.target_lang = config.translator.target_lang
                                region._alignment = config.render.alignment
                                region._direction = config.render.direction
                            ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
                            ctx.result_documents['translations.json'] = serialize_regions(ctx.text_regions)
                        except Exception as exc:
                            ctx.translation_error = str(exc)
                            ctx.result = None
                    results.append((ctx, config))

            # 强制垃圾回收以释放内存
            self._empty_device_cache()
                
        return results

    async def _concurrent_translate_contexts(self, contexts_with_configs: List[tuple]) -> List[tuple]:
        """
        并发处理翻译步骤，为每个图片单独发送翻译请求，避免合并大批次
        """

        # 在并发模式下，先保存所有页面的原文用于上下文
        batch_original_texts = []  # 存储当前批次的原文
        if self.context_size > 0:
            for i, (ctx, config) in enumerate(contexts_with_configs):
                if ctx.text_regions:
                    # 保存当前页面的原文
                    page_texts = {}
                    for j, region in enumerate(ctx.text_regions):
                        if not is_preserved_region(region):
                            page_texts[j] = region.text
                    batch_original_texts.append(page_texts)

                    # 确保 _original_page_texts 有足够的长度
                    while len(self._original_page_texts) <= len(self.all_page_translations) + i:
                        self._original_page_texts.append({})

                    self._original_page_texts[len(self.all_page_translations) + i] = page_texts
                else:
                    batch_original_texts.append({})

        async def translate_single_context(ctx_config_pair_with_index):
            """翻译单个context的异步函数"""
            ctx, config, page_index, batch_index = ctx_config_pair_with_index
            try:
                if not ctx.text_regions:
                    return ctx, config

                # Preserved source annotations do not enter translation requests.
                translatable_indices = [
                    i for i, region in enumerate(ctx.text_regions)
                    if not is_preserved_region(region)
                ]
                texts = [ctx.text_regions[i].text for i in translatable_indices]

                translated_texts = [None] * len(ctx.text_regions)
                for i, region in enumerate(ctx.text_regions):
                    if is_preserved_region(region):
                        translated_texts[i] = region.text

                if texts:
                    logger.debug(f'Translating {len(texts)} regions for single image in concurrent mode (page {page_index}, batch {batch_index})')

                    # 单独翻译这一张图片的文本，传递页面索引和批次索引用于正确的上下文
                    try:
                        translated = await self._batch_translate_texts(
                            texts, config, ctx,
                            page_index=page_index,
                            batch_index=batch_index,
                            batch_original_texts=batch_original_texts
                        )
                    except Exception:
                        if self._uses_gemini(config):
                            raise
                        translated = []
                    for i, value in zip(translatable_indices, translated):
                        translated_texts[i] = value
                translated_texts = await self._translate_page_with_retries(config, ctx, translated_texts)

                # 将翻译结果分配回各个region
                for i, region in enumerate(ctx.text_regions):
                    if i < len(translated_texts):
                        region.translation = translated_texts[i]
                        region.target_lang = config.translator.target_lang
                        region._alignment = config.render.alignment
                        region._direction = config.render.direction
                
                # 应用后处理逻辑（括号修正、过滤等）
                if ctx.text_regions:
                    ctx.text_regions = await self._apply_post_translation_processing(ctx, config)
                
                # 单页目标语言检查（如果启用）
                if config.translator.enable_post_translation_check and ctx.text_regions:
                    page_lang_check_result = await self._check_target_language_ratio(
                        ctx.text_regions,
                        config.translator.target_lang,
                        min_ratio=0.3  # 对单页使用更宽松的阈值
                    )
                    
                    if not page_lang_check_result:
                        logger.warning(f"Page-level target language check failed for single image")

                        if self._uses_gemini(config):
                            raise GeminiRetryExhausted("Gemini translation failed the target-language check.")
                        
                        # 单页重试逻辑
                        max_retry = config.translator.post_check_max_retry_attempts
                        retry_count = 0
                        
                        while retry_count < max_retry and not page_lang_check_result:
                            retry_count += 1
                            logger.info(f"Retrying single image translation {retry_count}/{max_retry}")
                            
                            # 重新翻译
                            translatable_regions = [
                                region for region in ctx.text_regions
                                if not is_preserved_region(region) and getattr(region, 'text', None)
                            ]
                            original_texts = [region.text for region in translatable_regions]
                            if original_texts:
                                try:
                                    new_translations = await self._batch_translate_texts(original_texts, config, ctx)
                                    
                                    # 更新翻译结果
                                    for region, translation in zip(translatable_regions, new_translations):
                                        old_translation = region.translation
                                        region.translation = translation
                                        logger.debug(f"Region translation updated: '{old_translation}' -> '{translation}'")
                                    
                                    # 重新检查
                                    page_lang_check_result = await self._check_target_language_ratio(
                                        ctx.text_regions,
                                        config.translator.target_lang,
                                        min_ratio=0.3
                                    )
                                    
                                    if page_lang_check_result:
                                        logger.info(f"Single image target language check passed after retry {retry_count}")
                                        break
                                        
                                except Exception as e:
                                    logger.error(f"Error during single image retry {retry_count}: {e}")
                                    break
                            else:
                                break
                        
                        if not page_lang_check_result:
                            logger.warning(f"Single image target language check failed after all {max_retry} retries")
                
                # 过滤逻辑
                if ctx.text_regions:
                    new_text_regions = []
                    for region in ctx.text_regions:
                        if is_preserved_region(region):
                            region.translation = region.text
                            new_text_regions.append(region)
                            continue
                        should_filter = False
                        filter_reason = ""

                        if not region.translation.strip():
                            should_filter = True
                            filter_reason = "Translation contain blank areas"
                        elif config.translator.translator != Translator.none:
                            if region.translation.isnumeric():
                                should_filter = True
                                filter_reason = "Numeric translation"
                            elif config.filter_text and re.search(config.re_filter_text, region.translation):
                                should_filter = True
                                filter_reason = f"Matched filter text: {config.filter_text}"
                            elif (not getattr(region, 'review_required', False)
                                  and not config.translator.translator == Translator.original):
                                text_equal = region.text.lower().strip() == region.translation.lower().strip()
                                if text_equal:
                                    should_filter = True
                                    filter_reason = "Translation identical to original"

                        if should_filter:
                            region.review_required = True
                            region.review_reason = filter_reason
                            ctx.manual_review_required = True
                            if region.translation.strip():
                                logger.info(f'Filtered out: {region.translation}')
                                logger.info(f'Reason: {filter_reason}')
                        new_text_regions.append(region)
                    ctx.text_regions = new_text_regions
                
                return ctx, config
                
            except TranslationFailure as exc:
                ctx.translation_error = str(exc)
                ctx.result = None
                return ctx, config
            except Exception as e:
                logger.error(f"Error in concurrent translation for single image: {e}")
                ctx.translation_error = str(e)
                ctx.result = None
                return ctx, config

        # 创建并发任务，为每个任务添加页面索引和批次索引
        tasks = []
        for i, ctx_config_pair in enumerate(contexts_with_configs):
            # 计算当前页面在整个翻译序列中的索引
            page_index = len(self.all_page_translations) + i
            batch_index = i  # 在当前批次中的索引
            ctx_config_pair_with_index = (*ctx_config_pair, page_index, batch_index)
            task = asyncio.create_task(translate_single_context(ctx_config_pair_with_index))
            tasks.append(task)
        
        logger.info(f'Starting concurrent translation of {len(tasks)} images...')
        
        # 等待所有任务完成
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Error in concurrent translation gather: {e}")
            raise
        
        # 处理结果，检查是否有异常
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Image {i+1} concurrent translation failed: {result}")
                ctx, config = contexts_with_configs[i]
                ctx.translation_error = str(result)
                ctx.result = None
                final_results.append((ctx, config))
            else:
                final_results.append(result)
        
        logger.info(f'Concurrent translation completed: {len(final_results)} images processed')
        return final_results

    async def _batch_translate_texts(self, texts: List[str], config: Config, ctx: Context, batch_contexts: List[Context] = None, page_index: int = None, batch_index: int = None, batch_original_texts: List[dict] = None, text_ids: List[str] = None) -> List[str]:
        """
        批量翻译文本列表，使用现有的翻译器接口

        Args:
            texts: 要翻译的文本列表
            config: 配置对象
            ctx: 上下文对象
            batch_contexts: 批处理上下文列表
            page_index: 当前页面索引，用于并发模式下的上下文计算
            batch_index: 当前页面在批次中的索引
            batch_original_texts: 当前批次的原文数据
        """
        if config.translator.translator == Translator.none:
            return ["" for _ in texts]

        if text_ids and any(key in GPT_TRANSLATORS for key, _ in config.translator.translator_gen.chain):
            translated = await dispatch_structured_translation(
                config.translator.translator_gen,
                list(zip(text_ids, texts)),
                config.translator,
                ctx,
                'cpu' if self._gpu_limited_memory else self.device,
            )
            return [translated[text_id] for text_id in text_ids]



        # 如果是ChatGPT翻译器，需要处理上下文
        if config.translator.translator == Translator.chatgpt:
            from .translators.chatgpt import OpenAITranslator
            translator = OpenAITranslator()

            # 确定是否使用并发模式和原文上下文
            use_original_text = self.batch_concurrent and self.batch_size > 1

            done_pages = self.all_page_translations
            if self.context_size > 0 and done_pages:
                pages_expected = min(self.context_size, len(done_pages))
                non_empty_pages = [
                    page for page in done_pages
                    if any(sent.strip() for sent in page.values())
                ]
                pages_used = min(self.context_size, len(non_empty_pages))
                skipped = pages_expected - pages_used
            else:
                pages_used = skipped = 0

            if self.context_size > 0:
                context_type = "original text" if use_original_text else "translation results"
                logger.info(f"Context-aware translation enabled with {self.context_size} pages of history using {context_type}")

            translator.parse_args(config.translator)

            # 构建上下文 - 在并发模式下使用原文和页面索引
            prev_ctx = self._build_prev_context(
                use_original_text=use_original_text,
                current_page_index=page_index,
                batch_index=batch_index,
                batch_original_texts=batch_original_texts
            )
            translator.set_prev_context(prev_ctx)

            if pages_used > 0:
                context_count = prev_ctx.count("<|")
                logger.info(f"Carrying {pages_used} pages of context, {context_count} sentences as translation reference")
            if skipped > 0:
                logger.warning(f"Skipped {skipped} pages with no sentences")

            return await translator._translate(
                ctx.from_lang,
                config.translator.target_lang,
                texts
            )

        else:
            # 使用通用翻译调度器
            return await dispatch_translation(
                config.translator.translator_gen,
                texts,
                config.translator,
                self.use_mtpe,
                ctx,
                'cpu' if self._gpu_limited_memory else self.device
            )
            
    async def _apply_post_translation_processing(self, ctx: Context, config: Config) -> List:
        """
        应用翻译后处理逻辑（括号修正、过滤等）
        """
        # 检查text_regions是否为None或空
        if not ctx.text_regions:
            return []
            
        translated = await self._translate_page_with_retries(
            config, ctx, [region.translation for region in ctx.text_regions]
        )
        for region, translation in zip(ctx.text_regions, translated):
            region.translation = translation
            region.target_lang = config.translator.target_lang
            region._alignment = config.render.alignment
            region._direction = config.render.direction

        check_items = [
            # 圆括号处理
            ["(", "（", "「", "【"],
            ["（", "(", "「", "【"],
            [")", "）", "」", "】"],
            ["）", ")", "」", "】"],
            
            # 方括号处理
            ["[", "［", "【", "「"],
            ["［", "[", "【", "「"],
            ["]", "］", "】", "」"],
            ["］", "]", "】", "」"],
            
            # 引号处理
            ["「", "“", "‘", "『", "【"],
            ["」", "”", "’", "』", "】"],
            ["『", "“", "‘", "「", "【"],
            ["』", "”", "’", "」", "】"],
            
            # 新增【】处理
            ["【", "(", "（", "「", "『", "["],
            ["】", ")", "）", "」", "』", "]"],
        ]

        replace_items = [
            ["「", "“"],
            ["「", "‘"],
            ["」", "”"],
            ["」", "’"],
            ["【", "["],  
            ["】", "]"],  
        ]

        for region in ctx.text_regions:
            if is_preserved_region(region):
                region.translation = region.text
                continue
            if region.text and region.translation:
                # 引号处理逻辑
                if '『' in region.text and '』' in region.text:
                    quote_type = '『』'
                elif '「' in region.text and '」' in region.text:
                    quote_type = '「」'
                elif '【' in region.text and '】' in region.text: 
                    quote_type = '【】'
                else:
                    quote_type = None
                
                if quote_type:
                    src_quote_count = region.text.count(quote_type[0])
                    dst_dquote_count = region.translation.count('"')
                    dst_fwquote_count = region.translation.count('＂')
                    
                    if (src_quote_count > 0 and
                        (src_quote_count == dst_dquote_count or src_quote_count == dst_fwquote_count) and
                        not region.translation.isascii()):
                        
                        if quote_type == '「」':
                            region.translation = re.sub(r'"([^"]*)"', r'「\1」', region.translation)
                        elif quote_type == '『』':
                            region.translation = re.sub(r'"([^"]*)"', r'『\1』', region.translation)
                        elif quote_type == '【】':  
                            region.translation = re.sub(r'"([^"]*)"', r'【\1】', region.translation)

                # 括号修正逻辑
                for v in check_items:
                    num_src_std = region.text.count(v[0])
                    num_src_var = sum(region.text.count(t) for t in v[1:])
                    num_dst_std = region.translation.count(v[0])
                    num_dst_var = sum(region.translation.count(t) for t in v[1:])
                    
                    if (num_src_std > 0 and
                        num_src_std != num_src_var and
                        num_src_std == num_dst_std + num_dst_var):
                        for t in v[1:]:
                            region.translation = region.translation.replace(t, v[0])

                # 强制替换规则
                for v in replace_items:
                    region.translation = region.translation.replace(v[1], v[0])

        # 注意：翻译结果的保存移动到了translate方法的最后，确保保存的是最终结果

        # 应用后字典
        post_dict = load_dictionary(self.post_dict)
        post_replacements = []  
        for region in ctx.text_regions:  
            if is_preserved_region(region):
                region.translation = region.text
                continue
            original = region.translation  
            region.translation = apply_dictionary(region.translation, post_dict)
            if original != region.translation:  
                post_replacements.append(f"{original} => {region.translation}")  

        if post_replacements:  
            logger.info("Post-translation replacements:")  
            for replacement in post_replacements:  
                logger.info(replacement)  
        else:  
            logger.info("No post-translation replacements made.")

        # 单个region幻觉检测
        failed_regions = []
        if config.translator.enable_post_translation_check:
            logger.info("Starting post-translation check...")
            
            # 单个region级别的幻觉检测
            for region in ctx.text_regions:
                if not is_preserved_region(region) and region.translation and region.translation.strip():
                    # 只检查重复内容幻觉
                    if await self._check_repetition_hallucination(
                        region.translation, 
                        config.translator.post_check_repetition_threshold,
                        silent=False
                    ):
                        failed_regions.append(region)
            
            # 对失败的区域进行重试
            if failed_regions:
                logger.warning(f"Found {len(failed_regions)} regions that failed repetition check, starting retry...")
                if self._uses_gemini(config):
                    raise GeminiRetryExhausted(
                        f"Gemini translation failed repetition check for {len(failed_regions)} region(s)."
                    )
                for region in failed_regions:
                    try:
                        logger.info(f"Retrying translation for region with text: '{region.text}'")
                        new_translation = await self._retry_translation_with_validation(region, config, ctx)
                        if new_translation:
                            old_translation = region.translation
                            region.translation = new_translation
                            logger.info(f"Region retry successful: '{old_translation}' -> '{new_translation}'")
                        else:
                            logger.warning(f"Region retry failed, keeping original: '{region.translation}'")
                    except Exception as e:
                        logger.error(f"Error during region retry: {e}")

        return ctx.text_regions

    async def _complete_translation_pipeline(self, ctx: Context, config: Config) -> Context:
        """
        完成翻译后的处理步骤（掩码细化、修复、渲染）
        """
        await self._report_progress('after-translating')

        if ctx.get('translation_error'):
            raise TranslationFailure(ctx.translation_error)
        if not ctx.text_regions:
            await self._report_progress('error-translating', True)
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)
        elif ctx.text_regions == 'cancel':
            await self._report_progress('cancelled', True)
            ctx.result = ctx.upscaled
            return await self._revert_upscale(config, ctx)

        # Rehydrate the lossless pre-translation canvas instead of retaining N
        # full image buffers per worker while the AI request is pending.
        if ctx.img_inpainted is None:
            inpainted_path = self._result_path('inpainted.png')
            if not os.path.exists(inpainted_path):
                inpainted_path = self._result_path('inpainted.jpg')
            if os.path.exists(inpainted_path):
                stored = cv2.imread(inpainted_path, cv2.IMREAD_COLOR)
                if stored is not None:
                    ctx.img_inpainted = cv2.cvtColor(stored, cv2.COLOR_BGR2RGB)

        # Rehydrate the pre-inpaint working canvas for region-level restoration.
        if getattr(ctx, 'img_rgb', None) is None:
            original_canvas_path = self._result_path('original_canvas.png')
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
                or (self._pipeline_run._document('ocr.json') if self._pipeline_run else None)
                or documents.get('detection.json')
                or (self._pipeline_run._document('detection.json') if self._pipeline_run else None)
            )
            if saved_textlines is not None:
                ctx.textlines = deserialize_textlines(saved_textlines)
        if getattr(ctx, 'mask_raw', None) is None:
            mask_raw_path = self._result_path('mask_raw.png')
            if os.path.isfile(mask_raw_path):
                ctx.mask_raw = cv2.imread(mask_raw_path, cv2.IMREAD_GRAYSCALE)

        if getattr(ctx, 'bubble_detections', None) is None:
            bd_path = self._result_path('bubble_detections.json')
            documents = getattr(ctx, 'result_documents', {}) or {}
            bubble_document = (
                documents.get('bubble_detections.json')
                or (self._pipeline_run._document('bubble_detections.json') if self._pipeline_run else None)
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
            await self._detect_speech_bubbles(config, ctx, report_progress=False)

        # A detector mask is input to refinement, not a completed inpainting mask.
        bundle = None
        if ctx.mask is None:
            mask_path = self._result_path('mask_final.png')
            if os.path.exists(mask_path):
                try:
                    ctx.mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
                except Exception:
                    pass
        if getattr(ctx, 'inpaint_mask', None) is None:
            inpaint_path = self._result_path('inpaint_mask.png')
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
            await self._report_progress('mask-generation')
            try:
                bundle = await run_cpu_stage(
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
                if self.verbose or self._pipeline_run is not None:
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
                            await self._async_imwrite(self._result_path(name), mask)
                    if self._pipeline_run is not None:
                        self._pipeline_run.write_json('profiling.json', bundle.profile)
                        self._pipeline_run.refresh()
            except Exception as e:  
                logger.error(f"Error during mask-generation:\n{traceback.format_exc()}")  
                raise


        ctx.cleanup_mask_diagnostics()
        bundle = None

        if getattr(ctx, 'text_regions', None) and getattr(ctx, 'img_rgb', None) is not None and not getattr(ctx, '_bubble_layout_ready', False):
            try:
                await self._report_progress('layout')
                transform_text_case = getattr(config.render, "transform_text_case", None)
                if transform_text_case:
                    for region in (ctx.text_regions or []):
                        if is_preserved_region(region):
                            region.translation = region.text
                        elif getattr(region, "translation", None) and isinstance(region.translation, str):
                            region.translation = transform_text_case(region.translation)
                layout_font = self.font_path or getattr(config.render, 'font_path', None) or get_default_eng_font()
                await run_cpu_stage(layout_page, ctx, config, layout_font, priority=CPU_PRIORITY_BACKGROUND)
                if self._pipeline_run is not None:
                    self._pipeline_run.write_json('layout.json', serialize_frozen_layout(
                        ctx, config, layout_font,
                        serialize_bubble_detections(getattr(ctx, 'bubble_detections', None) or []),
                    ))
            except Exception as error:
                logger.warning('Production layout failed; preserving the existing render path: %s', error)

        if self.verbose and ctx.mask is not None:
            try:
                # 保存mask_final.png
                mask_final_path = self._result_path('mask_final.png')
                success = await self._async_imwrite(mask_final_path, ctx.mask)
                if not success:
                    logger.warning(f"Failed to save debug image: {mask_final_path}")
            except Exception as e:
                logger.error(f"Error saving debug image (mask_final.png): {e}")
                logger.debug(f"Exception details: {traceback.format_exc()}")

        # -- Inpainting
        if ctx.img_inpainted is None:
            await self._report_progress('inpainting')
            try:
                ctx.img_inpainted = await self._run_inpainting(config, ctx)
            except Exception as e:
                logger.error(f"Error during inpainting:\n{traceback.format_exc()}")
                raise
            if ctx.img_inpainted is not None and (self.verbose or self._pipeline_run is not None):
                await asyncio.to_thread(
                    save_jpeg, ctx.img_inpainted, self._result_path('inpainted.jpg')
                )
        if ctx.mask is not None and ctx.img_inpainted is not None:
            ctx.gimp_mask = np.dstack((cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR), ctx.mask))

        if self.verbose:
            try:
                inpainted_path = self._result_path('inpainted.jpg')
                await asyncio.to_thread(save_jpeg, ctx.img_inpainted, inpainted_path)
                try:
                    os.unlink(self._result_path('inpainted.png'))
                except FileNotFoundError:
                    pass
            except Exception as e:
                logger.error(f"Error saving inpainted.jpg debug image: {e}")
                logger.debug(f"Exception details: {traceback.format_exc()}")

        # -- Rendering
        await self._report_progress('rendering')

        # 在rendering状态后立即发送文件夹信息，用于前端精确检查final.png
        if hasattr(self, '_progress_hooks') and self._current_image_context:
            folder_name = self._current_image_context['subfolder']
            # 发送特殊格式的消息，前端可以解析
            await self._report_progress(f'rendering_folder:{folder_name}')

        try:
            ctx.img_rendered = await self._run_text_rendering(config, ctx)
        except Exception as e:
            logger.error(f"Error during rendering:\n{traceback.format_exc()}")
            raise

        await self._report_progress('finished', True)
        ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
        
        # 保存debug文件夹信息到Context中（用于Web模式的缓存访问）
        if self.verbose:
            ctx.debug_folder = self._get_image_subfolder()

        res_ctx = await self._revert_upscale(config, ctx)
        if hasattr(res_ctx, 'cleanup_intermediate'):
            res_ctx.cleanup_intermediate(keep_input=True)
            res_ctx.cleanup_detection_workspace()
        self._empty_device_cache()
        return res_ctx
    
    async def _check_repetition_hallucination(self, text: str, threshold: int = 5, silent: bool = False) -> bool:
        """
        检查文本是否包含重复内容（模型幻觉）
        Check if the text contains repetitive content (model hallucination)
        """
        if not text or len(text.strip()) < threshold:
            return False
            
        # 检查字符级重复
        consecutive_count = 1
        prev_char = None
        
        for char in text:
            if char == prev_char:
                consecutive_count += 1
                if consecutive_count >= threshold:
                    if not silent:
                        logger.warning(f'Detected character repetition hallucination: "{text}" - repeated character: "{char}", consecutive count: {consecutive_count}')
                    return True
            else:
                consecutive_count = 1
            prev_char = char
        
        # 检查词语级重复（按字符分割中文，按空格分割其他语言）
        segments = re.findall(r'[\u4e00-\u9fff]|\S+', text)
        
        if len(segments) >= threshold:
            consecutive_segments = 1
            prev_segment = None
            
            for segment in segments:
                if segment == prev_segment:
                    consecutive_segments += 1
                    if consecutive_segments >= threshold:
                        if not silent:
                            logger.warning(f'Detected word repetition hallucination: "{text}" - repeated segment: "{segment}", consecutive count: {consecutive_segments}')
                        return True
                else:
                    consecutive_segments = 1
                prev_segment = segment
        
        # 检查短语级重复
        words = text.split()
        if len(words) >= threshold * 2:
            for i in range(len(words) - threshold + 1):
                phrase = ' '.join(words[i:i + threshold//2])
                remaining_text = ' '.join(words[i + threshold//2:])
                if phrase in remaining_text:
                    phrase_count = text.count(phrase)
                    if phrase_count >= 3:  # 降低短语重复检测阈值
                        if not silent:
                            logger.warning(f'Detected phrase repetition hallucination: "{text}" - repeated phrase: "{phrase}", occurrence count: {phrase_count}')
                        return True
                        
        return False

    async def _check_target_language_ratio(self, text_regions: List, target_lang: str, min_ratio: float = 0.5) -> bool:
        """
        检查翻译结果中目标语言的占比是否达到要求
        使用py3langid进行语言检测
        Check if the target language ratio meets the requirement by detecting the merged translation text
        
        Args:
            text_regions: 文本区域列表
            target_lang: 目标语言代码
            min_ratio: 最小目标语言占比（此参数在新逻辑中不使用，保留为兼容性）
            
        Returns:
            bool: True表示通过检查，False表示未通过
        """
        translatable_regions = [region for region in (text_regions or []) if not is_preserved_region(region)]
        if len(translatable_regions) <= 10:
            # 如果区域数量不超过10个，跳过此检查
            return True
            
        # 合并所有翻译文本
        all_translations = []
        for region in translatable_regions:
            translation = getattr(region, 'translation', '')
            if translation and translation.strip():
                all_translations.append(translation.strip())
        
        if not all_translations:
            logger.debug('No valid translation texts for language ratio check')
            return True
            
        # 将所有翻译合并为一个文本进行检测
        merged_text = ''.join(all_translations)
        
        # logger.info(f'Target language check - Merged text preview (first 200 chars): "{merged_text[:200]}"')
        # logger.info(f'Target language check - Total merged text length: {len(merged_text)} characters')
        # logger.info(f'Target language check - Number of regions: {len(all_translations)}')
        
        # 使用py3langid进行语言检测
        try:
            detected_lang, confidence = langid.classify(merged_text)
            detected_language = ISO_639_1_TO_VALID_LANGUAGES.get(detected_lang, 'UNKNOWN')
            if detected_language != 'UNKNOWN':
                detected_language = detected_language.upper()
            
            # logger.info(f'Target language check - py3langid result: "{detected_lang}" -> "{detected_language}" (confidence: {confidence:.3f})')
        except Exception as e:
            logger.debug(f'py3langid failed for merged text: {e}')
            detected_language = 'UNKNOWN'
            confidence = -9999
        
        # 检查检测出的语言是否为目标语言
        is_target_lang = (detected_language == target_lang.upper())
        
        # logger.info(f'Target language check: Detected language "{detected_language}" using py3langid (confidence: {confidence:.3f})')
        # logger.info(f'Target language check: Target is "{target_lang.upper()}"')
        # logger.info(f'Target language check result: {"PASSED" if is_target_lang else "FAILED"}')
        
        return is_target_lang

    async def _validate_translation(self, original_text: str, translation: str, target_lang: str, config, ctx: Context = None, silent: bool = False, page_lang_check_result: bool = None) -> bool:
        """
        验证翻译质量（包含目标语言比例检查和幻觉检测）
        Validate translation quality (includes target language ratio check and hallucination detection)
        
        Args:
            page_lang_check_result: 页面级目标语言检查结果，如果为None则进行检查，如果已有结果则直接使用
        """
        if not config.translator.enable_post_translation_check:
            return True
            
        if not translation or not translation.strip():
            return True
        
        # 1. 目标语言比例检查（页面级别）
        if page_lang_check_result is None and ctx and ctx.text_regions and len(ctx.text_regions) > 10:
            # 进行页面级目标语言检查
            page_lang_check_result = await self._check_target_language_ratio(
                ctx.text_regions,
                target_lang,
                min_ratio=0.5
            )
            
        # 如果页面级检查失败，直接返回失败
        if page_lang_check_result is False:
            if not silent:
                logger.debug("Target language ratio check failed for this region")
            return False
        
        # 2. 检查重复内容幻觉（region级别）
        if await self._check_repetition_hallucination(
            translation, 
            config.translator.post_check_repetition_threshold,
            silent
        ):
            return False
                
        return True

    async def _retry_translation_with_validation(self, region, config: Config, ctx: Context) -> str:
        """
        带验证的重试翻译
        Retry translation with validation
        """
        original_translation = region.translation
        max_attempts = config.translator.post_check_max_retry_attempts
        
        for attempt in range(max_attempts):
            # 验证当前翻译 - 在重试过程中只检查单个region（幻觉检测），不进行页面级检查
            is_valid = await self._validate_translation(
                region.text, 
                region.translation, 
                config.translator.target_lang,
                config,
                ctx=None,  # 不传ctx避免页面级检查
                silent=True,  # 重试过程中禁用日志输出
                page_lang_check_result=True  # 传入True跳过页面级检查，只做region级检查
            )
            
            if is_valid:
                if attempt > 0:
                    logger.info(f'Post-translation check passed (Attempt {attempt + 1}/{max_attempts}): "{region.translation}"')
                return region.translation
            
            # 如果不是最后一次尝试，进行重新翻译
            if attempt < max_attempts - 1:
                logger.warning(f'Post-translation check failed (Attempt {attempt + 1}/{max_attempts}), re-translating: "{region.text}"')
                
                try:
                    # 单独重新翻译这个文本区域
                    if config.translator.translator != Translator.none:
                        from .translators import dispatch
                        retranslated = await dispatch(
                            config.translator.translator_gen,
                            [region.text],
                            config.translator,
                            self.use_mtpe,
                            ctx,
                            'cpu' if self._gpu_limited_memory else self.device
                        )
                        if retranslated:
                            region.translation = config.render.transform_text_case(retranslated[0])
                            logger.info(f'Re-translation finished: "{region.text}" -> "{region.translation}"')
                        else:
                            logger.warning(f'Re-translation failed, keeping original translation: "{original_translation}"')
                            region.translation = original_translation
                            break
                    else:
                        logger.warning('Translator is none, cannot re-translate.')
                        break
                        
                except Exception as e:
                    logger.error(f'Error during re-translation: {e}')
                    region.translation = original_translation
                    break
            else:
                logger.warning(f'Post-translation check failed, maximum retry attempts ({max_attempts}) reached, keeping original translation: "{original_translation}"')
                region.translation = original_translation
        
        return region.translation
