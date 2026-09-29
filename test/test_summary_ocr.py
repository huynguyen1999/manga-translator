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
        persist.assert_awaited_once_with({"path": Path(temporary), "name": "page.png"}, repaired, panels=None, bubbles=None)
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

    async def test_summary_ocr_job_advances_stage_after_batch_phase_completes(self):
        import asyncio
        from server.summary_ocr_execution import SummaryOCRJob, run_summary_ocr_job

        updates = []

        async def update_summary_job_for(
            _store, _group, _title, status, _error, stage, progress, message,
            current_page, page_count, _pages_with_text, _extraction_required, *, stage_passed_count=None,
        ):
            updates.append((status, stage, progress, message, current_page, page_count, stage_passed_count))

        async def run_batch(pages, _lang, _worker, _batch_size, on_progress):
            for stage in ("detection", "ocr", "textline_merge"):
                for pos in range(len(pages)):
                    await on_progress(stage, pos)
            return [([{"original_text": f"text-{i}"}], None) for i in range(len(pages))]

        pages = [{"name": "1.png", "textRegions": []}, {"name": "2.png", "textRegions": []}]
        job = SummaryOCRJob(
            request=None,
            pages=pages,
            target_language="ENG",
            worker=SimpleNamespace(extract_text_batch=AsyncMock()),
            pause_event=None,
            store=object(),
            group_value="Series",
            clean_title="Series",
            has_group_text=False,
            refresh_text=False,
            extraction_required=True,
            pages_with_text=0,
        )
        runtime = SimpleNamespace(
            summary_log=Mock(),
            update_summary_job_for=update_summary_job_for,
            run_summary_ocr=AsyncMock(),
            run_summary_ocr_batch=run_batch,
            summary_ocr_semaphore=asyncio.Semaphore(1),
            summary_controller=SimpleNamespace(),
            is_page_text_extracted=lambda *_a, **_kw: False,
            summary_queue_element=Mock(),
            task_queue=SimpleNamespace(),
            wait_in_queue=AsyncMock(),
            empty_device_cache=Mock(),
            get_inference_page_batch_size=lambda: 2,
        )

        pages_with_text, failed, _ = await run_summary_ocr_job(job, runtime)
        self.assertEqual(pages_with_text, 2)
        self.assertEqual(failed, [])
        stage_snapshots = [(stage, passed, message) for _, stage, _, message, _, _, passed in updates]
        self.assertEqual(
            stage_snapshots[:6],
            [
                ("detecting", 1, "Detecting text · 1/2 pages"),
                ("ocr", 0, "Reading OCR · 0/2 pages"),
                ("ocr", 1, "Reading OCR · 1/2 pages"),
                ("textline_merge", 0, "Merging text lines · 0/2 pages"),
                ("textline_merge", 1, "Merging text lines · 1/2 pages"),
                ("textline_merge", 2, "Merging text lines · 2/2 pages"),
            ],
        )


if __name__ == "__main__":
    unittest.main()

