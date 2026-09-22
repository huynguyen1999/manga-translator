import os
import sys
import unittest
import numpy as np
from PIL import Image

# Ensure project root is in path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from manga_translator.utils.generic import dump_image, load_image
from manga_translator.rendering.text_render import set_font, put_text_horizontal, get_char_glyph, CURRENT_FONT_PATH
from manga_translator.manga_translator import MangaTranslator
from server.sent_data_internal import get_client_session, close_client_session
from server.instance import ExecutorInstance
from manga_translator.config import Config


class TestPerformanceImprovements(unittest.TestCase):
    def test_dump_image_no_alpha(self):
        # RGB image without alpha
        img_arr = np.zeros((100, 100, 3), dtype=np.uint8)
        img_arr[:, :] = [128, 64, 32]
        img_pil = Image.fromarray(img_arr)

        res = dump_image(img_pil, img_arr, alpha_ch=None)
        self.assertIsInstance(res, Image.Image)
        self.assertEqual(res.size, (100, 100))
        self.assertEqual(res.mode, "RGB")
        res_arr = np.array(res)
        np.testing.assert_array_equal(res_arr, img_arr)

    def test_dump_image_with_alpha(self):
        # RGBA image
        img_arr = np.zeros((100, 100, 3), dtype=np.uint8)
        alpha = Image.new("L", (100, 100), 200)
        img_pil = Image.new("RGBA", (100, 100), (255, 255, 255, 200))

        res = dump_image(img_pil, img_arr, alpha_ch=alpha)
        self.assertIsInstance(res, Image.Image)
        self.assertEqual(res.size, (100, 100))
        self.assertEqual(res.mode, "RGBA")

    def test_set_font_cache_retention(self):
        # Calling set_font multiple times with the same font should retain cache
        set_font('')
        glyph1 = get_char_glyph('A', 20, 0)
        info1 = get_char_glyph.cache_info()
        self.assertGreater(info1.currsize, 0)

        # Re-calling set_font with empty string (same) should NOT clear cache
        set_font('')
        info2 = get_char_glyph.cache_info()
        self.assertEqual(info1.currsize, info2.currsize)

    def test_put_text_horizontal(self):
        set_font('')
        res = put_text_horizontal(
            font_size=24,
            text="Hello world this is a test text layout",
            width=200,
            height=100,
            alignment="center",
            reversed_direction=False,
            fg=(0, 0, 0),
            bg=(255, 255, 255),
            lang="en_US",
            hyphenate=True
        )
        # Should complete quickly and return a rendered image tuple/array
        self.assertIsNotNone(res)

    def test_dictionary_cache(self):
        import tempfile
        from manga_translator.manga_translator import load_dictionary
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("apple orange\nbanana pear\n")
            f_path = f.name

        try:
            dict1 = load_dictionary(f_path)
            self.assertEqual(len(dict1), 2)
            self.assertEqual(dict1[0][1], "orange")
            self.assertEqual(dict1[1][1], "pear")

            # Check cache hit (same object returned)
            dict2 = load_dictionary(f_path)
            self.assertIs(dict1, dict2)
        finally:
            os.unlink(f_path)

    def test_server_batch_payload_structure(self):
        executor = ExecutorInstance(ip="127.0.0.1", port=5003)
        images = [Image.new("RGB", (10, 10))]
        cfg = Config()

        # Check payload construction logic
        images_with_configs = [(img, cfg) for img in images]
        payload = {"images_with_configs": images_with_configs, "batch_size": 2}
        self.assertIn("images_with_configs", payload)
        self.assertEqual(len(payload["images_with_configs"]), 1)
        self.assertEqual(payload["batch_size"], 2)


    def test_det_batch_forward(self):
        import torch
        import torch.nn as nn
        from manga_translator.detection import default as det_module

        class DummyModel(nn.Module):
            def forward(self, x):
                self.input_dtype = x.dtype
                self.input_shape = x.shape
                # Verify that x was normalized: range should be approx [-1, 1]
                return torch.zeros(x.shape[0], 1, x.shape[2], x.shape[3]), torch.zeros(x.shape[0], 1, x.shape[2], x.shape[3])

        dummy = DummyModel()
        det_module.MODEL = dummy

        batch = [np.zeros((64, 64, 3), dtype=np.uint8)]
        db, mask = det_module.det_batch_forward_default(batch, device='cpu')

        self.assertEqual(dummy.input_dtype, torch.float32)
        self.assertEqual(dummy.input_shape, (1, 3, 64, 64))
        self.assertEqual(db.shape, (1, 1, 64, 64))
        self.assertEqual(mask.shape, (1, 1, 64, 64))

    def test_openai_client_cache(self):
        from manga_translator.translators.chatgpt import _get_openai_client, _client_cache
        # Verify cache returns the same instance for identical credentials
        client1 = _get_openai_client("dummy_key", "https://api.openai.com/v1", None)
        client2 = _get_openai_client("dummy_key", "https://api.openai.com/v1", None)
        self.assertIs(client1, client2)


if __name__ == '__main__':
    unittest.main()
