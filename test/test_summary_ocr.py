import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PIL import Image

from server.summary_ocr import (
    ocr_config,
    repaired_regions,
    run_summary_ocr,
    run_summary_ocr_batch,
    safe_setting,
    summary_target_language,
)


class SummaryOcrTests(unittest.IsolatedAsyncioTestCase):
    def test_target_language_and_ocr_config_keep_saved_page_settings(self):
        self.assertEqual(
            summary_target_language([
                {"meta": {"settings": {"targetLanguage": "JPN"}}},
            ]),
            "JPN",
        )
        self.assertEqual(
            summary_target_language([{"meta": {"sourceType": "original"}}]),
            "ENG",
        )

        config = ocr_config(
            {
                "name": "page.png",
                "meta": {
                    "sourceType": "original",
                    "settings": {"detectionResolution": 4096, "customBoxThreshold": 0.6},
                },
            },
            "JPN",
            safe_setting,
        )
        self.assertEqual(config.detector.detection_size, 4096)
        self.assertEqual(config.detector.box_threshold, 0.6)
        self.assertEqual(config.translator.target_lang, "ENG")
        self.assertFalse(config._web_frontend_optimized)
        self.assertEqual(
            repaired_regions(SimpleNamespace(text_regions=[SimpleNamespace(
                xywh=[1, 2, 3, 4], lines=[], text="source", font_size=12,
            )]))[0]["original_text"],
            "source",
        )

    async def test_single_ocr_uses_worker_and_persists_repaired_regions(self):
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "page.png"
            Image.new("RGB", (3, 2), "white").save(image_path)
            context = SimpleNamespace(text_regions=[object()], cleanup_all_images=Mock())
            worker = SimpleNamespace(extract_text=AsyncMock(return_value=context))
            repaired = [{"original_text": "hello"}]
            persist = AsyncMock()
            config = object()

            result = await run_summary_ocr(
                None,
                {"path": Path(temporary), "name": "page.png"},
                "ENG",
                worker=worker,
                get_context=AsyncMock(),
                ocr_config_fn=Mock(return_value=config),
                input_file_fn=Mock(return_value=image_path),
                repaired_regions_fn=Mock(return_value=repaired),
                persist_regions=persist,
            )

        self.assertEqual(result, repaired)
        worker.extract_text.assert_awaited_once()
        self.assertIs(worker.extract_text.await_args.args[1], config)
        persist.assert_awaited_once_with({"path": Path(temporary), "name": "page.png"}, repaired)
        context.cleanup_all_images.assert_called_once_with()

    async def test_batch_ocr_keeps_per_page_results_and_reclaims_contexts(self):
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "page.png"
            Image.new("RGB", (3, 2), "white").save(image_path)
            pages = [{"path": Path(temporary), "name": f"page-{index}.png"} for index in range(2)]
            contexts = [
                SimpleNamespace(text_regions=[index], cleanup_all_images=Mock())
                for index in range(2)
            ]
            worker = SimpleNamespace(extract_text_batch=AsyncMock(return_value=contexts))
            persist = AsyncMock()

            result = await run_summary_ocr_batch(
                pages,
                "ENG",
                worker,
                2,
                ocr_config_fn=Mock(side_effect=["config-1", "config-2"]),
                input_file_fn=Mock(return_value=image_path),
                repaired_regions_fn=lambda ctx: [{"text": ctx.text_regions[0]}],
                persist_regions=persist,
            )

        self.assertEqual(result, [([{"text": 0}], None), ([{"text": 1}], None)])
        self.assertEqual(worker.extract_text_batch.await_args.kwargs["batch_size"], 2)
        self.assertEqual(persist.await_count, 2)
        for context in contexts:
            context.cleanup_all_images.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
