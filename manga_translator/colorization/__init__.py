from PIL import Image

from .common import CommonColorizer, OfflineColorizer
from .manga_colorization_v2 import MangaColorizationV2
from .detector import is_image_colored, distance_from_grayscale
from ..config import Colorizer
from ..utils.model_cache import (
    get_cached_model, model_operation, remove_cached_model,
)

COLORIZERS = {
    Colorizer.mc2: MangaColorizationV2,
}
colorizer_cache = {}

def get_colorizer(key: Colorizer, *args, **kwargs) -> CommonColorizer:
    if key not in COLORIZERS:
        raise ValueError(f'Could not find colorizer for: "{key}". Choose from the following: %s' % ','.join(COLORIZERS))
    return get_cached_model('colorizer', colorizer_cache, key, lambda: COLORIZERS[key](*args, **kwargs))

@model_operation
async def prepare(key: Colorizer):
    colorizer = get_colorizer(key)
    if isinstance(colorizer, OfflineColorizer):
        await colorizer.download()

@model_operation
async def dispatch(key: Colorizer, image: Image.Image, device: str = 'cpu', **kwargs) -> Image.Image:
    colorizer = get_colorizer(key)
    if isinstance(colorizer, OfflineColorizer):
        await colorizer.load(device)
    return await colorizer.colorize(image, **kwargs)

@model_operation
async def unload(key: Colorizer):
    remove_cached_model('colorizer', colorizer_cache, key)
