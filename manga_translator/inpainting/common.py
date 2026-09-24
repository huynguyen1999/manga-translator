import numpy as np
from abc import abstractmethod

from ..config import InpainterConfig
from ..utils import InfererModule, ModelWrapper

class CommonInpainter(InfererModule):

    async def inpaint(self, image: np.ndarray, mask: np.ndarray, config: InpainterConfig, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        return await self._inpaint(image, mask, config, inpainting_size, verbose)

    @abstractmethod
    async def _inpaint(self, image: np.ndarray, mask: np.ndarray, config: InpainterConfig, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        pass

    async def inpaint_batch(self, images, masks, config, inpainting_size=1024, verbose=False):
        return [
            await self.inpaint(image, mask, config, inpainting_size, verbose)
            for image, mask in zip(images, masks)
        ]

class OfflineInpainter(CommonInpainter, ModelWrapper):
    _MODEL_SUB_DIR = 'inpainting'

    async def _inpaint(self, *args, **kwargs):
        return await self.infer(*args, **kwargs)

    async def _infer_batch(self, images, masks, config, inpainting_size=1024, verbose=False):
        return [
            await self._infer(image, mask, config, inpainting_size, verbose)
            for image, mask in zip(images, masks)
        ]

    async def inpaint_batch(self, images, masks, config, inpainting_size=1024, verbose=False):
        if not self.is_loaded():
            raise RuntimeError(f'{self._key}: Tried to forward pass without having loaded the model.')
        return await self._infer_batch(images, masks, config, inpainting_size, verbose)

    @abstractmethod
    async def _infer(self, image: np.ndarray, mask: np.ndarray, config: InpainterConfig, inpainting_size: int = 1024, verbose: bool = False) -> np.ndarray:
        pass
