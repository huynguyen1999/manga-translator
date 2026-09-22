import unittest
import numpy as np
from PIL import Image
from manga_translator.utils import Context
from manga_translator import Config

class TestBatchMemoryDecoupling(unittest.TestCase):
    def test_context_cleanup_intermediate(self):
        ctx = Context()
        ctx.input = Image.new("RGB", (100, 100), color="white")
        ctx.img_rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        ctx.img_alpha = np.zeros((100, 100), dtype=np.uint8)
        ctx.upscaled = Image.new("RGB", (200, 200), color="white")
        ctx.mask_raw = np.zeros((100, 100), dtype=np.uint8)
        ctx.mask = np.zeros((100, 100), dtype=np.uint8)
        ctx.img_inpainted = np.zeros((100, 100, 3), dtype=np.uint8)
        ctx.text_regions = [{"text": "Hello"}]
        ctx.result = Image.new("RGB", (100, 100), color="white")

        # Cleanup intermediate keeping input
        ctx.cleanup_intermediate(keep_input=True)
        self.assertIsNotNone(ctx.input)
        self.assertIsNotNone(ctx.result)
        self.assertEqual(ctx.text_regions, [{"text": "Hello"}])
        self.assertIsNone(ctx.img_rgb)
        self.assertIsNone(ctx.img_alpha)
        self.assertIsNone(ctx.upscaled)
        self.assertIsNone(ctx.mask_raw)
        self.assertIsNone(ctx.mask)
        self.assertIsNone(ctx.img_inpainted)

        # Cleanup all images
        ctx.cleanup_all_images()
        self.assertIsNone(ctx.input)
        self.assertIsNone(ctx.result)
        self.assertEqual(ctx.text_regions, [{"text": "Hello"}])

if __name__ == "__main__":
    unittest.main()
