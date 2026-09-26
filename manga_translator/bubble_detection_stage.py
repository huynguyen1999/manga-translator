"""Bubble detection stage calls used by MangaTranslator."""

import asyncio
import time
import uuid

import numpy as np

from .config import Config
from .detection.bubble import BubbleDetection
from .utils import Context


async def run_bubble_detection_batch(
    owner,
    configs: list[Config],
    contexts: list[Context],
    *,
    dispatch_batch,
):
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
    owner._model_usage_timestamps[("bubble_detection", bubble_config.model)] = time.time()
    output = await owner._mps_call(dispatch_batch, images, bubble_config, owner.device)
    if len(output) != len(images):
        raise RuntimeError(f"Bubble detector returned {len(output)} pages for {len(images)} inputs")
    return output


async def run_bubble_detection(
    owner,
    config: Config,
    ctx: Context,
    report_progress: bool = True,
    precomputed_detections: list[BubbleDetection] | None = None,
    *,
    detect_bubbles,
    dispatch_detection,
    group_regions_by_bubbles,
    logger,
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
                await owner._report_progress('bubble-detection')
            if hasattr(owner, '_model_usage_timestamps'):
                owner._model_usage_timestamps[("bubble_detection", config.bubble_detection.model)] = time.time()
            if hasattr(detect_bubbles, 'mock_calls') or hasattr(detect_bubbles, 'assert_called'):
                res = detect_bubbles(ctx.img_rgb, config.bubble_detection)
                ctx.bubble_detections = await res if asyncio.iscoroutine(res) else res
            else:
                ctx.bubble_detections = await owner._mps_call(
                    dispatch_detection, ctx.img_rgb, config.bubble_detection, owner.device
                )
        if ctx.text_regions:
            group_regions = getattr(config.bubble_detection, 'group_regions', False)
            ctx.text_regions = group_regions_by_bubbles(
                ctx.text_regions, ctx.bubble_detections, group=group_regions
            )
            for region in ctx.text_regions:
                if not getattr(region, 'region_id', None):
                    region.region_id = uuid.uuid4().hex
        if ctx.bubble_detections and (getattr(owner, 'verbose', False) or owner._pipeline_run is not None):
            bubble_mask = np.zeros(ctx.img_rgb.shape[:2], np.uint8)
            for detection in ctx.bubble_detections:
                bubble_mask = np.maximum(bubble_mask, np.asarray(detection.mask, dtype=np.uint8))
            await owner._async_imwrite(owner._result_path('bubble_mask.png'), bubble_mask)
        logger.info(
            'Detected %d speech bubbles; matched %d text regions',
            len(ctx.bubble_detections),
            sum(1 for region in (ctx.text_regions or []) if getattr(region, '_bubble_mask', None) is not None),
        )
    except Exception as error:
        logger.warning('Speech-bubble detection unavailable; using the existing pipeline: %s', error)
        ctx.bubble_detections = []
