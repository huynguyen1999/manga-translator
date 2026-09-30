"""Batch execution and failure fallback for bubble detection."""

import logging
import time

from ..config import Config
from ..utils import Context
from .bubble_state import set_bubble_detection_state

logger = logging.getLogger(__name__)


async def run_bubble_detection_batch(owner, configs: list[Config], contexts: list[Context], *, dispatch_batch):
    if len(configs) != len(contexts) or not configs:
        raise ValueError("Bubble detection config/context count mismatch") if len(configs) != len(contexts) else None
        return []
    b_cfg = configs[0].bubble_detection
    if any(cfg.bubble_detection.dict() != b_cfg.dict() for cfg in configs[1:]):
        raise ValueError("Bubble detection batch settings must match")
    images = [ctx.img_rgb for ctx in contexts]
    if any(img is None for img in images):
        raise RuntimeError("No image canvas available for bubble detection")
    for ctx in contexts:
        ctx._bubble_detection_done = True
    if hasattr(owner, "_model_usage_timestamps") and isinstance(owner._model_usage_timestamps, dict):
        owner._model_usage_timestamps[("bubble_detection", b_cfg.model)] = time.time()
    try:
        out = await owner._mps_call(dispatch_batch, images, b_cfg, owner.device)
        if len(out) != len(images):
            raise RuntimeError(f"Bubble detector returned {len(out)} pages for {len(images)} inputs")
    except Exception as error:
        logger.warning("Batch speech-bubble detection failed; using geometry recovery: %s", error)
        for ctx in contexts:
            ctx.bubble_detections, ctx.panel_detections = [], []
            set_bubble_detection_state(ctx, failed=True)
        return [(ctx.bubble_detections, ctx.panel_detections) for ctx in contexts]
    for ctx, item in zip(contexts, out):
        ctx.bubble_detections, ctx.panel_detections = item if isinstance(item, tuple) else (item, [])
        set_bubble_detection_state(ctx, ctx.bubble_detections)
    return [(ctx.bubble_detections, ctx.panel_detections) for ctx in contexts]
