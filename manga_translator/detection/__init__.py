import numpy as np

from .default import DefaultDetector
from .dbnet_convnext import DBConvNextDetector
from .ctd import ComicTextDetector
from .craft import CRAFTDetector
from .paddle_rust import PaddleDetector
from .none import NoneDetector
from .common import CommonDetector, OfflineDetector
from .common_rust import RustDetector
from ..config import Detector
from ..utils.model_cache import (
    get_cached_model, model_operation, unload_cached_model,
)

DETECTORS = {
    Detector.default: DefaultDetector,
    Detector.dbconvnext: DBConvNextDetector,
    Detector.ctd: ComicTextDetector,
    Detector.craft: CRAFTDetector,
    Detector.paddle: PaddleDetector,
    Detector.none: NoneDetector,
}
detector_cache = {}

def get_detector(key: Detector | str, *args, **kwargs) -> CommonDetector:
    if isinstance(key, str) and not isinstance(key, Detector):
        try:
            key = Detector(key)
        except ValueError:
            pass
    if key not in DETECTORS:
        raise ValueError(f'Could not find detector for: "{key}". Choose from the following: %s' % ','.join(DETECTORS))
    return get_cached_model('detector', detector_cache, key, lambda: DETECTORS[key](*args, **kwargs))

@model_operation
async def prepare(detector_key: Detector | str):
    detector = get_detector(detector_key)
    if isinstance(detector, OfflineDetector):
        await detector.download()

@model_operation
async def dispatch(detector_key: Detector | str, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float, unclip_ratio: float,
                   invert: bool, gamma_correct: bool, rotate: bool, auto_rotate: bool = False, device: str = 'cpu', verbose: bool = False):
    detector = get_detector(detector_key)
    if isinstance(detector, OfflineDetector):
        await detector.load(device)
    elif isinstance(detector, RustDetector):
        await detector.load(device)
    return await detector.detect(image, detect_size, text_threshold, box_threshold, unclip_ratio, invert, gamma_correct, rotate, auto_rotate, verbose)

@model_operation
async def dispatch_batch(detector_key: Detector | str, images: list[np.ndarray], detect_size: int, text_threshold: float, box_threshold: float, unclip_ratio: float,
                         invert: bool, gamma_correct: bool, rotate: bool, auto_rotate: bool = False, device: str = 'cpu', verbose: bool = False):
    detector = get_detector(detector_key)
    if isinstance(detector, OfflineDetector):
        await detector.load(device)
    elif isinstance(detector, RustDetector):
        await detector.load(device)
    return await detector.detect_batch(
        images, detect_size, text_threshold, box_threshold, unclip_ratio,
        invert, gamma_correct, rotate, auto_rotate, verbose,
    )

@model_operation
async def unload(detector_key: Detector | str):
    if isinstance(detector_key, str) and not isinstance(detector_key, Detector):
        try:
            detector_key = Detector(detector_key)
        except ValueError:
            pass
    await unload_cached_model('detector', detector_cache, detector_key)
