import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np

from PIL import Image

from manga_translator.manga_translator import MangaTranslator
from manga_translator.config import Config, TranslatorConfig, OcrConfig
from manga_translator.utils import Context, Quadrilateral, TextBlock


class TestSaveDetectionImmediately(unittest.IsolatedAsyncioTestCase):
    @patch("manga_translator.manga_translator.save_result_documents", new_callable=AsyncMock)
    async def test_detection_saved_immediately_after_detection_step(self, mock_save_docs):
        translator = MangaTranslator({"no_gpu": True})
        translator._current_image_context = {"subfolder": "test_folder_123"}

        config = Config()
        config.translator = TranslatorConfig()
        config.ocr = OcrConfig()

        ctx = Context()
        ctx.input = Image.new("RGB", (100, 100), (255, 255, 255))
        ctx.upscaled = ctx.input
        ctx.img_rgb = ctx.input
        ctx.img_alpha = None

        dummy_box = Quadrilateral(np.array([[10, 10], [50, 10], [50, 30], [10, 30]]), "test", 0.95)
        dummy_textlines = [dummy_box]

        async def fake_run_detection(*args, **kwargs):
            return dummy_textlines, None, None

        async def fake_run_ocr(*args, **kwargs):
            return dummy_textlines

        async def fake_run_textline_merge(*args, **kwargs):
            return [TextBlock([[[10, 10], [50, 10], [50, 30], [10, 30]]], ["test"], 0)]

        translator._run_detection = fake_run_detection
        translator._run_ocr = fake_run_ocr
        translator._run_textline_merge = fake_run_textline_merge
        translator._detect_speech_bubbles = AsyncMock()

        res_ctx = await translator._translate(config, ctx)

        self.assertIn("detection.json", res_ctx.result_documents)
        self.assertIn("ocr.json", res_ctx.result_documents)
        self.assertIn("text_regions_merged.json", res_ctx.result_documents)

        # Verify save_result_documents was called for detection.json
        saved_calls = [call.args for call in mock_save_docs.call_args_list]
        saved_keys = [list(call[1].keys())[0] for call in saved_calls]
        self.assertIn("detection.json", saved_keys)
        self.assertIn("ocr.json", saved_keys)
        self.assertIn("text_regions_merged.json", saved_keys)


if __name__ == "__main__":
    unittest.main()
