import numpy as np
try:
    from rusty_manga_image_translator import Session, PyPreprocessorOptions, PyDefaultOptions, PyImage
except ImportError:
    Session = PyPreprocessorOptions = PyDefaultOptions = PyImage = None

from manga_translator.config import TranslatorConfig
from manga_translator.utils import Quadrilateral
from manga_translator.utils.log import get_logger

def get_session():
    """Gets onnx session"""
    if not hasattr(get_session, "_instance"):
        get_session._instance = Session(None)
    return get_session._instance


class RustDetector:
    def __init__(self, det):
        self.det = det
        self.logger = get_logger(self.__class__.__name__)
        self._loaded_device = None

    def parse_args(self, args: TranslatorConfig):
        # parse_args does noting
        pass

    def is_downloaded(self) -> bool:
        #do not use python downloader
        return True

    async def download(self, force=False):
        # download does nothing
        pass

    @property
    def model_dir(self):
        # should not be used
        raise NotImplementedError

    async def infer(self, *args, **kwargs):
        # should not be used
        raise NotImplementedError

    async def detect(self, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float,
                     unclip_ratio: float,
                     invert: bool, gamma_correct: bool, rotate: bool, auto_rotate: bool = False, verbose: bool = False):
        '''
        Returns textblock list and text mask.
        '''
        self.logger.info(
            f'Model inference: model={self.__class__.__name__} '
            f'requested_device={getattr(self, "requested_device", "cpu")} '
            'runtime_device=external backend=external:rust-onnx'
        )
        o1 = PyPreprocessorOptions(invert, gamma_correct, rotate, auto_rotate)
        o2 = PyDefaultOptions(detect_size, unclip_ratio, text_threshold, box_threshold)
        img = PyImage.from_numpy(image)
        areas, raw_mask = self.det.detect(img, o1, o2)
        textlines = [Quadrilateral(np.array(x.pts(), dtype=np.int64), '', x.score()) for x in areas]
        return textlines, raw_mask, None

    async def reload(self, device: str, *args, **kwargs):
        await self.unload()
        await self.load(device)

    async def load(self, device: str, *args, **kwargs):
        requested_device = str(device)
        self.requested_device = requested_device
        if self.det.loaded():
            if self._loaded_device != requested_device:
                self.logger.warning(
                    f'External detector device request changed from {self._loaded_device} to {requested_device}; '
                    'the Rust/ONNX backend does not expose device reconfiguration, keeping its existing placement'
                )
            return
        self._loaded_device = requested_device
        self.logger.info(
            f'Model loaded: model={self.__class__.__name__} requested_device={self.requested_device} '
            'runtime_device=external backend=external:rust-onnx '
            '(device request delegated to external Rust/ONNX backend; not a PyTorch MPS device)'
        )
        self.det.load()

    async def unload(self):
        self.det.unload()
        self._loaded_device = None

    def is_loaded(self) -> bool:
        return self.det.loaded()
