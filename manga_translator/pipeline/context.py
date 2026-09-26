"""Image context, result metadata, and output path helpers."""

import os
import time
from datetime import datetime, timezone
from ..config import Config, Ocr
from ..utils import Context


def set_image_context(translator, config: Config, image=None):
    """设置当前处理图片的上下文信息，用于生成调试图片子文件夹"""
    from ..utils.generic import get_image_md5

    # 使用毫秒级时间戳确保唯一性
    timestamp = str(int(time.time() * 1000))
    detection_size = str(getattr(config.detector, 'detection_size', 1024))
    target_lang = getattr(config.translator, 'target_lang', 'unknown')
    translator_name = getattr(config.translator, 'translator', 'unknown')

    file_md5 = get_image_md5(image) if image is not None else "unknown"

    subfolder_name = f"{timestamp}-{file_md5}-{detection_size}-{target_lang}-{translator_name}"

    original_name = getattr(config, 'original_name', None)
    if not original_name or original_name == 'Unknown':
        original_name = f"{subfolder_name}.png"
    manga_title = (getattr(config, 'manga_title', None) or 'Ungrouped').strip() or 'Ungrouped'
    manga_group_id = getattr(config, 'manga_group_id', None)
    page_order = getattr(config, 'page_order', None)
    source_path = getattr(config, 'source_path', None)

    translator._current_image_context = {
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

def build_result_metadata(translator, config: Config, ctx: Context) -> dict:
    image_context = getattr(translator, '_current_image_context', None) or {}
    original_name = image_context.get('original_name') or getattr(config, 'original_name', None)
    if not original_name or original_name == 'Unknown':
        original_name = getattr(config, 'original_name', None) or f"{translator._get_image_subfolder()}.png"
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
        or (translator._pipeline_run.manifest.get('createdAt') if getattr(translator, '_pipeline_run', None) else None)
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

def get_image_subfolder(translator) -> str:
    """获取当前图片的调试子文件夹名"""
    if translator._current_image_context:
        return translator._current_image_context['subfolder']
    return ''

def save_current_image_context(translator, image_md5: str):
    """保存当前图片上下文，用于批量处理中保持一致性"""
    if translator._current_image_context:
        translator._saved_image_contexts[image_md5] = translator._current_image_context.copy()

def restore_image_context(translator, image_md5: str):
    """恢复保存的图片上下文"""
    if image_md5 in translator._saved_image_contexts:
        translator._current_image_context = translator._saved_image_contexts[image_md5].copy()
        return True
    return False

def result_path(translator, path: str) -> str:
    """
    Returns path to result folder where intermediate images are saved when using verbose flag
    or web mode input/result images are cached.
    """
    output_override = getattr(translator, '_result_path_override', None)
    if output_override is not None:
        result_path = os.path.join(os.fspath(output_override), path)
        os.makedirs(os.path.dirname(result_path), exist_ok=True)
        return result_path

    # 只有在verbose模式下才使用图片级子文件夹
    if translator.verbose:
        image_subfolder = translator._get_image_subfolder()
        if image_subfolder:
            if translator.result_sub_folder:
                result_path = os.path.join(translator.result_root, translator.result_sub_folder, image_subfolder, path)
            else:
                result_path = os.path.join(translator.result_root, image_subfolder, path)
            # 确保目录存在
            os.makedirs(os.path.dirname(result_path), exist_ok=True)
            return result_path

    # 在server/web模式下（result_sub_folder为空）且为非verbose模式时
    # 需要创建一个子文件夹来保存final.png
    if not translator.result_sub_folder:
        if translator._current_image_context:
            # 直接使用已生成的子文件夹名
            sub_folder = translator._current_image_context['subfolder']
        else:
            # 没有上下文信息时使用默认值
            timestamp = str(int(time.time() * 1000))
            sub_folder = f"{timestamp}-unknown-1024-unknown-unknown"

        result_path = os.path.join(translator.result_root, sub_folder, path)
    else:
        result_path = os.path.join(translator.result_root, translator.result_sub_folder, path)

    # 确保目录存在
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    return result_path
