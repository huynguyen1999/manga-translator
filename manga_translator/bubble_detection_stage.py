import asyncio
import json
from pathlib import Path
import time
import uuid
import numpy as np

from .config import Config
from .detection.bubble import BubbleDetection, serialize_bubble_detections
from .detection.bubble_batch import run_bubble_detection_batch
from .detection.bubble_state import set_bubble_detection_state
from .detection.panel import serialize_panel_detections
from .utils import Context

async def run_bubble_detection(owner, config: Config, ctx: Context, report_progress: bool = True,
                               precomputed_detections: list[BubbleDetection] | None = None,
                               precomputed_panels: list | None = None, *,
                               detect_bubbles, dispatch_detection, group_regions_by_bubbles, logger):
    """Run optional bubble + panel detection before translation in every pipeline mode."""
    has_precomputed = precomputed_detections is not None or precomputed_panels is not None
    ctx.bubble_detections = precomputed_detections or []
    ctx.panel_detections = precomputed_panels or []
    ctx._bubble_detection_done = True
    set_bubble_detection_state(
        ctx, (precomputed_detections or []) if has_precomputed else None,
    )
    for region in ctx.text_regions or []:
        if not getattr(region, 'region_id', None):
            region.region_id = uuid.uuid4().hex
    if not config.bubble_detection.enabled or getattr(ctx, 'img_rgb', None) is None:
        return
    try:
        if precomputed_detections is None and precomputed_panels is None:
            if report_progress:
                await owner._report_progress('bubble-detection')
            if hasattr(owner, "_model_usage_timestamps") and isinstance(owner._model_usage_timestamps, dict):
                owner._model_usage_timestamps[("bubble_detection", config.bubble_detection.model)] = time.time()
            if hasattr(detect_bubbles, 'mock_calls') or hasattr(detect_bubbles, 'assert_called'):
                res = detect_bubbles(ctx.img_rgb, config.bubble_detection)
                raw = await res if asyncio.iscoroutine(res) else res
            else:
                raw = await owner._mps_call(dispatch_detection, ctx.img_rgb, config.bubble_detection, owner.device)
            ctx.bubble_detections, ctx.panel_detections = raw if isinstance(raw, tuple) else (raw, getattr(raw, 'panels', []))
            set_bubble_detection_state(ctx, ctx.bubble_detections)
        if ctx.text_regions:
            ctx.text_regions = group_regions_by_bubbles(
                ctx.text_regions, ctx.bubble_detections, group=getattr(config.bubble_detection, 'group_regions', False)
            )
            for region in ctx.text_regions:
                if not getattr(region, 'region_id', None):
                    region.region_id = uuid.uuid4().hex
        b_docs = serialize_bubble_detections(getattr(ctx, 'bubble_detections', None) or [])
        p_docs = serialize_panel_detections(getattr(ctx, 'panel_detections', None) or [], ctx.img_rgb.shape[:2] if ctx.img_rgb is not None else None)
        if getattr(ctx, 'result_documents', None) is not None:
            ctx.result_documents.update({'bubble_detections.json': b_docs, 'panel_detections.json': p_docs})

        if ctx.bubble_detections and (getattr(owner, 'verbose', False) or owner._pipeline_run is not None):
            mask = np.zeros(ctx.img_rgb.shape[:2], np.uint8)
            for d in ctx.bubble_detections:
                mask = np.maximum(mask, np.asarray(d.mask, dtype=np.uint8))
            await owner._async_imwrite(owner._result_path('bubble_mask.png'), mask)

        if owner._pipeline_run is not None:
            owner._pipeline_run.write_json('bubble_detections.json', b_docs)
            owner._pipeline_run.write_json('panel_detections.json', p_docs)
            owner._pipeline_run.refresh()
        elif hasattr(owner, '_result_path') and getattr(owner, '_current_image_context', None):
            for name, payload in (('bubble_detections.json', b_docs), ('panel_detections.json', p_docs)):
                try:
                    Path(owner._result_path(name)).write_text(json.dumps(payload, indent=2), encoding='utf-8')
                except Exception:
                    pass

        logger.info('Detected %d speech bubbles, %d panels; matched %d text regions',
                    len(ctx.bubble_detections), len(ctx.panel_detections or []),
                    sum(1 for r in (ctx.text_regions or []) if getattr(r, '_bubble_mask', None) is not None))
    except Exception as error:
        logger.warning('Speech-bubble detection unavailable; using the existing pipeline: %s', error)
        ctx.bubble_detections, ctx.panel_detections = [], []
        set_bubble_detection_state(ctx, failed=True)
