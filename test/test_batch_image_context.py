import unittest
from unittest.mock import patch

from PIL import Image

from manga_translator.config import Config
from manga_translator.manga_translator import MangaTranslator


class BatchImageContextTest(unittest.TestCase):
    def test_worker_does_not_empty_mps_cache_outside_shared_model_lane(self):
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.device = "mps"

        with patch("manga_translator.manga_translator.get_model_executor", return_value=object()), patch(
            "manga_translator.manga_translator.empty_device_cache"
        ) as empty_cache:
            translator._empty_device_cache()

        empty_cache.assert_not_called()

    def test_context_uses_real_image_id_without_verbose_mode(self):
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.verbose = False
        translator._pipeline_lab_run = None
        config = Config()
        config.request_id = "batch:item-1"

        translator._set_image_context(config, Image.new("RGB", (2, 2), "white"))

        self.assertNotEqual(translator._current_image_context["file_md5"], "unknown")
        self.assertEqual(translator._current_image_context["request_id"], "batch:item-1")


if __name__ == "__main__":
    unittest.main()
