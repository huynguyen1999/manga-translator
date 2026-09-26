"""Colorization and upscaling stage calls used by MangaTranslator."""

import time

from .config import Config
from .utils import Context


async def run_colorizer(owner, config: Config, ctx: Context, *, dispatch):
    current_time = time.time()
    owner._model_usage_timestamps[("colorizer", config.colorizer.colorizer)] = current_time
    return await owner._mps_call(
        dispatch,
        config.colorizer.colorizer,
        colorization_size=config.colorizer.colorization_size,
        denoise_sigma=config.colorizer.denoise_sigma,
        color_threshold=config.colorizer.color_threshold,
        restore_size=config.colorizer.restore_size,
        device=owner.device,
        image=ctx.input,
        **ctx
    )


async def run_upscaling(owner, config: Config, ctx: Context, *, dispatch):
    current_time = time.time()
    owner._model_usage_timestamps[("upscaling", config.upscale.upscaler)] = current_time
    return (await owner._mps_call(
        dispatch,
        config.upscale.upscaler, [ctx.img_colorized], config.upscale.upscale_ratio, owner.device
    ))[0]


async def run_upscaling_batch(owner, configs: list[Config], contexts: list[Context], *, dispatch):
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
    owner._model_usage_timestamps[("upscaling", upscale.upscaler)] = time.time()
    output = await owner._mps_call(
        dispatch,
        upscale.upscaler,
        images,
        upscale.upscale_ratio,
        owner.device,
    )
    if len(output) != len(images):
        raise RuntimeError(f"Upscaler returned {len(output)} pages for {len(images)} inputs")
    return output
