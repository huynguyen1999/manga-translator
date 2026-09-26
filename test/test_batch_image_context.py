import asyncio
import unittest
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from manga_translator.config import Config, Detector, Ocr, Translator
from manga_translator.manga_translator import MangaTranslator
from manga_translator.pipeline.batch.translation import batch_translate_texts


class BatchImageContextTest(unittest.TestCase):
    def test_offline_batch_translation_dispatches_with_region_ids(self):
        async def run():
            config = Config()
            config.translator.translator = Translator.sugoi
            config.translator.translator_gen.chain = []
            ctx = SimpleNamespace(from_lang="JPN")
            owner = SimpleNamespace(use_mtpe=False, _gpu_limited_memory=False, device="cpu")

            with patch(
                "manga_translator.pipeline.batch.translation.dispatch_translation",
                new=AsyncMock(return_value=["translated"]),
            ) as dispatch:
                result = await batch_translate_texts(
                    owner, ["日本語"], config, ctx, text_ids=["region-1"], logger=Mock()
                )

            self.assertEqual(result, ["translated"])
            dispatch.assert_awaited_once()

        asyncio.run(run())

    def test_batched_text_extraction_preserves_stage_and_progress_order(self):
        async def run():
            translator = MangaTranslator.__new__(MangaTranslator)
            translator.pre_dict = None
            events = []

            async def detection(configs, contexts):
                events.append("detection")
                return [([f"line-{index}"], None, None) for index in range(len(contexts))]

            async def ocr(pages):
                events.append("ocr")
                return [[f"ocr-{index}"] for index in range(len(pages))]

            async def merge(config, ctx):
                events.append("merge")
                return [SimpleNamespace(text=f"region-{len(events)}")]

            async def bubbles(config, ctx, report_progress=True):
                events.append("bubbles")

            translator._run_detection_batch = AsyncMock(side_effect=detection)
            translator._run_ocr_batch = AsyncMock(side_effect=ocr)
            translator._run_textline_merge = AsyncMock(side_effect=merge)
            translator._detect_speech_bubbles = AsyncMock(side_effect=bubbles)
            translator._set_image_context = Mock()
            config = Config()
            config.detector.detector = Detector.default
            config.ocr.ocr = Ocr.ocr48px
            progress = []

            async def on_progress(stage, index):
                progress.append((stage, index))

            contexts = await translator.extract_text_batch(
                [(Image.new("RGB", (2, 2)), config), (Image.new("RGB", (2, 2)), config)],
                batch_size=2,
                on_progress=on_progress,
            )

            self.assertEqual(events, ["detection", "ocr", "merge", "bubbles", "merge", "bubbles"])
            self.assertEqual(
                progress,
                [(stage, index) for stage in ("detection", "ocr", "textline_merge") for index in (0, 1)],
            )
            self.assertEqual([ctx.textlines for ctx in contexts], [["ocr-0"], ["ocr-1"]])
            self.assertEqual(len(contexts[0].text_regions), 1)
            translator._run_detection_batch.assert_awaited_once()
            translator._run_ocr_batch.assert_awaited_once()

        asyncio.run(run())

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
        translator._pipeline_run = None
        config = Config()
        config.request_id = "batch:item-1"

        translator._set_image_context(config, Image.new("RGB", (2, 2), "white"))

        self.assertNotEqual(translator._current_image_context["file_md5"], "unknown")
        self.assertEqual(translator._current_image_context["request_id"], "batch:item-1")

    def test_result_path_uses_current_page_folder_and_creates_it(self):
        with TemporaryDirectory() as root:
            translator = MangaTranslator.__new__(MangaTranslator)
            translator.result_root = root
            translator.result_sub_folder = ""
            translator.verbose = False
            translator._current_image_context = {"subfolder": "page-context"}
            translator._result_path_override = None

            result = Path(translator._result_path("input.jpg"))

            self.assertEqual(result, Path(root) / "page-context" / "input.jpg")
            self.assertTrue(result.parent.is_dir())

    def test_saved_image_context_can_be_restored(self):
        translator = MangaTranslator.__new__(MangaTranslator)
        translator._saved_image_contexts = {}
        translator._current_image_context = {"subfolder": "page-context"}

        translator._save_current_image_context("image-id")
        translator._current_image_context = None

        self.assertTrue(translator._restore_image_context("image-id"))
        self.assertEqual(translator._get_image_subfolder(), "page-context")


if __name__ == "__main__":
    unittest.main()
