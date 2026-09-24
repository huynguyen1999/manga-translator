from typing import List
from PIL import Image

from .common import CommonUpscaler, OfflineUpscaler
from .waifu2x import Waifu2xUpscaler
from .esrgan import ESRGANUpscaler
from .esrgan_pytorch import ESRGANUpscalerPytorch
from ..config import Upscaler
from ..utils.model_cache import (
    get_cached_model, model_operation, unload_cached_model,
)

UPSCALERS = {
    Upscaler.waifu2x: Waifu2xUpscaler,
    Upscaler.esrgan: ESRGANUpscaler,
    Upscaler.upscler4xultrasharp: ESRGANUpscalerPytorch,
}
upscaler_cache = {}

def get_upscaler(key: Upscaler, *args, **kwargs) -> CommonUpscaler:
    if key not in UPSCALERS:
        raise ValueError(f'Could not find upscaler for: "{key}". Choose from the following: %s' % ','.join(UPSCALERS))
    return get_cached_model('upscaler', upscaler_cache, key, lambda: UPSCALERS[key](*args, **kwargs))

@model_operation
async def prepare(upscaler_key: Upscaler):
    upscaler = get_upscaler(upscaler_key)
    if isinstance(upscaler, OfflineUpscaler):
        await upscaler.download()

@model_operation
async def dispatch(upscaler_key: Upscaler, image_batch: List[Image.Image], upscale_ratio: int, device: str = 'cpu') -> List[Image.Image]:
    if upscale_ratio == 1:
        return image_batch
    upscaler = get_upscaler(upscaler_key)
    if isinstance(upscaler, OfflineUpscaler):
        await upscaler.load(device)
    return await upscaler.upscale(image_batch, upscale_ratio)

@model_operation
async def unload(upscaler_key: Upscaler):
    await unload_cached_model('upscaler', upscaler_cache, upscaler_key)
