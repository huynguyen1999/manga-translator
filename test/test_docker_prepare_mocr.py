import unittest
from unittest.mock import patch, MagicMock
import os
import tempfile

from docker_prepare import is_model_selected
from manga_translator.ocr.model_manga_ocr import ModelMangaOCR
from manga_translator.config import Ocr


class TestDockerPrepareMocr(unittest.IsolatedAsyncioTestCase):
    def test_is_model_selected(self):
        # Empty set or 'all' matches everything
        self.assertTrue(is_model_selected("ocr", "mocr", set()))
        self.assertTrue(is_model_selected("ocr", "mocr", {"all"}))
        self.assertTrue(is_model_selected("detector", "dbnet", set()))

        # Specific matches for mocr
        self.assertTrue(is_model_selected("ocr", "mocr", {"mocr"}))
        self.assertTrue(is_model_selected("ocr", "mocr", {"ocr.mocr"}))
        self.assertTrue(is_model_selected("ocr", "mocr", {"ocr.manga_ocr"}))
        self.assertTrue(is_model_selected("ocr", "mocr", {"mangaocr"}))

        # Non-matching
        self.assertFalse(is_model_selected("ocr", "mocr", {"ocr.48px"}))
        self.assertFalse(is_model_selected("ocr", "mocr", {"detector.dbnet"}))

        # Other detectors / inpainters
        self.assertTrue(is_model_selected("detector", "dbnet_convnext", {"dbnet_convnext"}))
        self.assertTrue(is_model_selected("detector", "dbnet_convnext", {"detector.dbnet_convnext"}))
        self.assertTrue(is_model_selected("detector", "dbnet_convnext", {"dbnet-convnext"}))

    def test_model_manga_ocr_required_files_constants(self):
        self.assertEqual(ModelMangaOCR.MOCR_REPO_ID, "kha-white/manga-ocr-base")
        for filename in [
            "config.json",
            "preprocessor_config.json",
            "pytorch_model.bin",
            "special_tokens_map.json",
            "tokenizer_config.json",
            "vocab.txt",
        ]:
            self.assertIn(filename, ModelMangaOCR.MOCR_REQUIRED_FILES)

    def test_check_mocr_downloaded_with_mock_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            inst = ModelMangaOCR()
            # Point model_dir to tmp_dir for test
            inst._MODEL_DIR = tmp_dir
            inst._MODEL_SUB_DIR = "ocr"
            os.makedirs(inst.model_dir, exist_ok=True)

            self.assertFalse(inst._check_mocr_downloaded())

            # Create all required files
            for f in ModelMangaOCR.MOCR_REQUIRED_FILES:
                with open(inst._get_file_path(f), "w") as fp:
                    fp.write("{}")

            self.assertTrue(inst._check_mocr_downloaded())

    @patch("manga_translator.utils.inference.ModelWrapper._download")
    @patch("huggingface_hub.snapshot_download")
    async def test_download_calls_snapshot_download_when_missing(
        self, mock_snapshot_download, mock_super_download
    ):
        with tempfile.TemporaryDirectory() as tmp_dir:
            inst = ModelMangaOCR()
            inst._MODEL_DIR = tmp_dir
            inst._MODEL_SUB_DIR = "ocr"
            os.makedirs(inst.model_dir, exist_ok=True)

            await inst._download()

            mock_super_download.assert_called_once()
            mock_snapshot_download.assert_called_once_with(
                repo_id=ModelMangaOCR.MOCR_REPO_ID,
                local_dir=inst.model_dir,
            )


if __name__ == "__main__":
    unittest.main()
