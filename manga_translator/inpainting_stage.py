"""Inpainting stage calls used by MangaTranslator."""

import time

import cv2
import numpy as np

from .config import Config, Inpainter
from .translation_errors import TranslationFailure
from .utils import Context


async def run_inpainting_batch(owner, configs: list[Config], contexts: list[Context], *, dispatch_batch, logger):
    if len(configs) != len(contexts):
        raise ValueError('Inpainting config/context count mismatch')
    if not configs: return []
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
                translated = str(region.translation or '').strip()
                source = str(region.text or '').strip()
                unchanged = region.review_reason == 'Translation identical to original' and source.casefold() == translated.casefold()
                if getattr(region, '_render_suppressed', False) or unchanged or (region.review_required and not translated):
                    continue
                for line in region.lines:
                    x, y, w, h = cv2.boundingRect(np.asarray(line, dtype=np.int32))
                    x1, y1, x2, y2 = max(0, x), max(0, y), min(ctx.img_rgb.shape[1], x + w), min(ctx.img_rgb.shape[0], y + h)
                    if ctx.mask is None or x2 <= x1 or y2 <= y1: raise TranslationFailure('No erasing mask for detected text; page needs retry')
                    polygon = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
                    cv2.fillPoly(polygon, [np.asarray(line, dtype=np.int32) - (x1, y1)], 255)
                    if not np.any(ctx.mask[y1:y2, x1:x2][polygon > 0]):
                        raise TranslationFailure('No erasing mask for detected text; page needs retry')
        active.append((index, ctx))

    current_time = time.time()
    if not hasattr(owner, '_model_usage_timestamps'):
        owner._model_usage_timestamps = {}
    owner._model_usage_timestamps[("inpainting", inpainter_config.inpainter)] = current_time
    device = getattr(owner, 'device', None)
    inpainting_size = inpainter_config.inpainting_size
    if device == 'mps' and inpainting_size > 1024:
        logger.info(
            'Capping MPS inpainting size from %d to 1024 to limit peak memory',
            inpainting_size,
        )
        inpainting_size = 1024
    if active:
        results = await owner._mps_call(
            dispatch_batch,
            inpainter_config.inpainter,
            [ctx.img_rgb for _, ctx in active],
            [ctx.mask for _, ctx in active],
            inpainter_config, inpainting_size, device, owner.verbose,
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
