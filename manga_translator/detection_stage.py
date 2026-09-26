"""Detection stage calls used by MangaTranslator."""

import time

from .config import Config
from .utils import Context


async def run_detection(owner, config: Config, ctx: Context, *, dispatch_detection):
    current_time = time.time()
    owner._model_usage_timestamps[("detection", config.detector.detector)] = current_time
    return await owner._mps_call(
        dispatch_detection,
        config.detector.detector,
        ctx.img_rgb,
        config.detector.detection_size,
        config.detector.text_threshold,
        config.detector.box_threshold,
        config.detector.unclip_ratio,
        config.detector.det_invert,
        config.detector.det_gamma_correct,
        config.detector.det_rotate,
        config.detector.det_auto_rotate,
        owner.device,
        owner.verbose,
    )


async def run_detection_batch(
    owner,
    configs: list[Config],
    contexts: list[Context],
    *,
    dispatch_detection_batch,
    logger,
):
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
    logger.info("Detection model batch pages=%d device=%s", len(images), owner.device)
    owner._model_usage_timestamps[("detection", detector.detector)] = time.time()
    output = await owner._mps_call(
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
        owner.device,
        owner.verbose,
    )
    if len(output) != len(images):
        raise RuntimeError(f"Detector returned {len(output)} pages for {len(images)} inputs")
    return output
