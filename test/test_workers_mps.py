"""Opt-in hardware check: MANGA_TEST_MPS=1 python -m unittest discover -s test -p test_workers_mps.py"""
import asyncio
import os
import unittest

import numpy as np
import torch

from manga_translator.config import Detector, Inpainter, InpainterConfig, Ocr
from manga_translator import detection, inpainting, ocr
from manga_translator.utils import Quadrilateral
from manga_translator.utils.model_cache import SharedModelExecutor
from server.in_process_executor import InProcessExecutorInstance


@unittest.skipUnless(os.environ.get('MANGA_TEST_MPS') == '1', 'Set MANGA_TEST_MPS=1 for real GPU inference')
class MPSWorkersTest(unittest.IsolatedAsyncioTestCase):
    async def test_four_pipelines_share_models_on_metal(self):
        self.assertTrue(torch.backends.mps.is_available(), 'MPS must be available for this check')
        shared = SharedModelExecutor()
        workers = [InProcessExecutorInstance(i, {'use_gpu': True}, shared) for i in range(4)]
        image = np.full((2048, 1536, 3), 255, dtype=np.uint8)
        image[100:140, 100:300] = 0

        async def pipeline():
            # Preparation used to bypass the GPU lock.
            await ocr.prepare(Ocr.ocr48px, 'mps')
            await inpainting.prepare(Inpainter.lama_large, 'mps')
            from manga_translator.detection import bubble
            from manga_translator.config import BubbleDetectionConfig
            bubble_config = BubbleDetectionConfig(enabled=True)
            await bubble.prepare(bubble_config, 'mps')
            _, mask, _ = await detection.dispatch(
                Detector.default, image, 2048, 0.5, 0.7, 2.3,
                False, False, False, device='mps',
            )
            self.assertEqual(mask.shape, image.shape[:2])
            region = Quadrilateral(np.array([[100, 100], [300, 100], [300, 140], [100, 140]]), '', 1.0)
            await ocr.dispatch(Ocr.ocr48px, image, [region], device='mps')
            await asyncio.sleep(0.01)  # Remote translation wait; no API call or credentials.
            small = image[:512, :512].copy()
            bubble_dets = await bubble.dispatch(small, bubble_config, device='mps')
            self.assertIsInstance(bubble_dets, list)
            mask = np.zeros((512, 512), dtype=np.uint8)
            mask[90:150, 90:310] = 255
            result = await inpainting.dispatch(
                Inpainter.lama_large, small, mask, InpainterConfig(), 512, 'mps'
            )
            self.assertEqual(result.shape, small.shape)

        try:
            # Warm once, then confirm four simultaneous pipelines retain the same instances.
            await workers[0]._run_translation(pipeline)
            initial = {name: {key: id(value) for key, value in cache.items()}
                       for name, cache in shared._cache.items()}
            await asyncio.gather(*(worker._run_translation(pipeline) for worker in workers))
            after = {name: {key: id(value) for key, value in cache.items()}
                     for name, cache in shared._cache.items()}
            self.assertEqual(initial, after)
            self.assertEqual({key: len(cache) for key, cache in shared._cache.items()},
                             {'ocr': 1, 'inpainter': 1, 'detector': 1, 'bubble_detector': 1})
        finally:
            for worker in workers:
                worker.close()
            shared.close()


if __name__ == '__main__':
    unittest.main()
