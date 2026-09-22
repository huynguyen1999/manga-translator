from PIL import Image

from .common import CommonColorizer, OfflineColorizer
from .manga_colorization_v2 import MangaColorizationV2
from .detector import is_image_colored, distance_from_grayscale
from ..config import Colorizer
from ..utils.model_cache import get_model_cache, model_operation

COLORIZERS = {
    Colorizer.mc2: MangaColorizationV2,
}
colorizer_cache = {}

def get_colorizer(key: Colorizer, *args, **kwargs) -> CommonColorizer:
    if key not in COLORIZERS:
        raise ValueError(f'Could not find colorizer for: "{key}". Choose from the following: %s' % ','.join(COLORIZERS))
    cache = get_model_cache('colorizer', colorizer_cache)
    if not cache.get(key):
        colorizer_cls = COLORIZERS[key]
        cache[key] = colorizer_cls(*args, **kwargs)
    return cache[key]

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
    get_model_cache('colorizer', colorizer_cache).pop(key, None)
