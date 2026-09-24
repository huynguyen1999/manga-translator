import asyncio
import threading
from typing import Optional

import numpy as np

from .common import CommonInpainter, OfflineInpainter
from .inpainting_aot import AotInpainter
from .inpainting_lama_mpe import LamaMPEInpainter, LamaLargeInpainter
from .inpainting_sd import StableDiffusionInpainter
from .none import NoneInpainter
from .original import OriginalInpainter
from ..config import Inpainter, InpainterConfig
from ..utils.model_cache import (
    get_cached_model, model_operation, remove_cached_model,
)

INPAINTERS = {
    Inpainter.default: AotInpainter,
    Inpainter.lama_large: LamaLargeInpainter,
    Inpainter.lama_mpe: LamaMPEInpainter,
    Inpainter.sd: StableDiffusionInpainter,
    Inpainter.none: NoneInpainter,
    Inpainter.original: OriginalInpainter,
}
inpainter_cache = {}

def get_inpainter(key: Inpainter, *args, **kwargs) -> CommonInpainter:
    if key not in INPAINTERS:
        raise ValueError(f'Could not find inpainter for: "{key}". Choose from the following: %s' % ','.join(INPAINTERS))
    return get_cached_model('inpainter', inpainter_cache, key, lambda: INPAINTERS[key](*args, **kwargs))

@model_operation
async def prepare(inpainter_key: Inpainter, device: str = 'cpu'):
    inpainter = get_inpainter(inpainter_key)
    if isinstance(inpainter, OfflineInpainter):
        await inpainter.download()
        await inpainter.load(device)

_inpainting_semaphore: Optional[threading.BoundedSemaphore] = None

def set_inpainting_concurrency(limit: Optional[int]):
    global _inpainting_semaphore
    if limit is not None and limit > 0:
        _inpainting_semaphore = threading.BoundedSemaphore(limit)
    else:
        _inpainting_semaphore = None

def get_inpainting_semaphore() -> Optional[threading.BoundedSemaphore]:
    return _inpainting_semaphore

@model_operation
async def dispatch(inpainter_key: Inpainter, image: np.ndarray, mask: np.ndarray, config: Optional[InpainterConfig], inpainting_size: int = 1024, device: str = 'cpu', verbose: bool = False) -> np.ndarray:
    inpainter = get_inpainter(inpainter_key)
    if isinstance(inpainter, OfflineInpainter):
        await inpainter.load(device)
    config = config or InpainterConfig()
    if _inpainting_semaphore is not None:
        await asyncio.to_thread(_inpainting_semaphore.acquire)
        try:
            return await inpainter.inpaint(image, mask, config, inpainting_size, verbose)
        finally:
            _inpainting_semaphore.release()
    return await inpainter.inpaint(image, mask, config, inpainting_size, verbose)

@model_operation
async def unload(inpainter_key: Inpainter):
    remove_cached_model('inpainter', inpainter_cache, inpainter_key)
