"""End-to-end tests validating the 3 distinct manga summary flows."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from server.api.schemas.summary import MangaSummaryRequest
from server.summary_generation import SummaryGenerationRuntime, generate_manga_summary
from server.summary_ocr_execution import SummaryOCRJob, run_summary_ocr_job


class MangaSummaryFlowsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.result_root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_flow_1_cached_db_text_and_panels(self):
        """Flow 1: Pages with existing text & panels skip OCR and structure by panel for LLM."""
        pages = [
            {
                "id": "page-1",
                "folder": "0001",
                "name": "01.png",
                "path": self.result_root / "0001",
                "sourceType": "translated",
                "textRegions": [
                    {"id": "b1", "original_text": "First panel text", "panel_index": 0},
                    {"id": "b2", "original_text": "Second panel text", "panel_index": 1},
                ],
                "panel_detections": [
                    {"order": 1, "xyxy": [0, 0, 500, 500]},
                    {"order": 2, "xyxy": [0, 500, 500, 1000]},
                ],
            }
        ]

        ocr_mock = AsyncMock()
        synopsis_mock = AsyncMock(return_value="Synopsis from cached text")

        runtime = SummaryGenerationRuntime(
            result_root=self.result_root,
            get_store=lambda: None,
            summary_queue_element=Mock(),
            run_summary_ocr=ocr_mock,
            run_summary_ocr_batch=ocr_mock,
            summary_controller=SimpleNamespace(get_task=lambda *_: None),
            summary_log=Mock(),
            summary_ocr_semaphore=asyncio.Semaphore(1),
            summary_status_for=AsyncMock(return_value={"jobStatus": None, "summary": None, "stale": False}),
            summary_target_language=lambda *_: "ENG",
            update_summary_job_for=AsyncMock(),
            get_inference_page_batch_size=lambda: 2,
            task_queue=SimpleNamespace(),
            wait_in_queue=AsyncMock(),
            empty_device_cache=Mock(),
            deepseek_token_count_factory=lambda: (lambda text: len(text.split())),
            generate_synopsis=synopsis_mock,
            group_pages=lambda *_: pages,
            is_page_text_extracted=lambda p, **_: bool(p.get("textRegions")),
            read_page_text=lambda p: [r["original_text"] for r in p.get("textRegions", [])],
            resolve_summary_model=lambda *_: ("deepseek", "deepseek-flash", "key", "url"),
            save_summary=Mock(),
            source_snapshot=lambda p_list: {
                "entries": [{
                    "folder": p["folder"],
                    "name": p["name"],
                    "texts": [r["original_text"] for r in p["textRegions"]],
                    "panel_groups": [
                        {"panel_id": 1, "texts": ["First panel text"]},
                        {"panel_id": 2, "texts": ["Second panel text"]},
                    ],
                    "missing": False,
                } for p in p_list],
                "fingerprint": "abc",
                "texts": ["First panel text", "Second panel text"],
                "missing": [],
            },
            summary_error_details=lambda e: str(e),
            transcript_pages=lambda snap: [
                "[Page 01.png]\n[PANEL 1]\nFirst panel text\n\n[PANEL 2]\nSecond panel text"
            ],
        )

        request_data = MangaSummaryRequest(mangaTitle="CachedManga", summaryModel="deepseek-flash")
        await generate_manga_summary(
            None, request_data, runtime=runtime
        )

        # Flow 1 skips OCR completely
        ocr_mock.assert_not_awaited()
        synopsis_mock.assert_awaited_once()
        transcript = synopsis_mock.await_args.args[0]
        self.assertIn("[PANEL 1]\nFirst panel text", transcript[0])
        self.assertIn("[PANEL 2]\nSecond panel text", transcript[0])

    async def test_flow_2_translated_manga_missing_text_runs_neural_pipeline(self):
        """Flow 2: Translated manga without text runs neural detection, OCR, YOLO26, and textline merge."""
        img_path = self.result_root / "0001" / "input.png"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 100), "white").save(img_path)

        pages = [
            {
                "id": "p1",
                "name": "01.png",
                "path": self.result_root / "0001",
                "sourceType": "translated",
                "textRegions": [],
            }
        ]

        extracted_regions = [{"original_text": "Translated dialogue", "x": 10, "y": 10, "width": 50, "height": 20}]

        job = SummaryOCRJob(
            request=None,
            pages=pages,
            target_language="ENG",
            worker=SimpleNamespace(),
            pause_event=None,
            store=None,
            group_value="TranslatedSeries",
            clean_title="TranslatedSeries",
            has_group_text=False,
            refresh_text=False,
            extraction_required=True,
            pages_with_text=0,
        )

        runtime = SimpleNamespace(
            summary_log=Mock(),
            update_summary_job_for=AsyncMock(),
            run_summary_ocr=AsyncMock(return_value=extracted_regions),
            run_summary_ocr_batch=AsyncMock(),
            summary_ocr_semaphore=asyncio.Semaphore(1),
            summary_controller=SimpleNamespace(register_ocr_task=Mock(), unregister_ocr_task=Mock()),
            is_page_text_extracted=lambda p, **_: bool(p.get("textRegions")),
            summary_queue_element=Mock(),
            task_queue=SimpleNamespace(add_task=Mock()),
            wait_in_queue=AsyncMock(),
            empty_device_cache=Mock(),
            get_inference_page_batch_size=lambda: 1,
        )

        pages_with_text, failed, ocr_errors = await run_summary_ocr_job(job, runtime)

        self.assertEqual(pages_with_text, 1)
        self.assertEqual(len(failed), 0)
        self.assertEqual(pages[0]["textRegions"], extracted_regions)
        runtime.run_summary_ocr.assert_awaited_once()

    async def test_flow_3_original_manga_missing_text_routes_to_chrome_lens(self):
        """Flow 3: Original manga (sourceType='original') routes directly to Chrome Lens extraction."""
        img_path = self.result_root / "0001" / "01.png"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 100), "white").save(img_path)

        pages = [
            {
                "id": "p1",
                "name": "01.png",
                "path": self.result_root / "0001",
                "sourceType": "original",
                "textRegions": [],
            }
        ]

        job = SummaryOCRJob(
            request=None,
            pages=pages,
            target_language="ENG",
            worker=None,
            pause_event=None,
            store=None,
            group_value="RawOriginalManga",
            clean_title="RawOriginalManga",
            has_group_text=False,
            refresh_text=False,
            extraction_required=True,
            pages_with_text=0,
        )

        runtime = SimpleNamespace(
            summary_log=Mock(),
            update_summary_job_for=AsyncMock(),
            run_summary_ocr=AsyncMock(),
            run_summary_ocr_batch=AsyncMock(),
            summary_ocr_semaphore=asyncio.Semaphore(1),
            summary_controller=SimpleNamespace(),
            is_page_text_extracted=lambda p, **_: bool(p.get("textRegions")),
            summary_queue_element=Mock(),
            task_queue=SimpleNamespace(),
            wait_in_queue=AsyncMock(),
            empty_device_cache=Mock(),
            get_store=lambda: None,
        )

        mock_lens_regions = [{"original_text": "Chrome lens English speech", "x": 10, "y": 10, "width": 80, "height": 30}]
        mock_lens_panels = [{"order": 1, "xyxy": [0, 0, 100, 100]}]

        with patch("server.summary_chrome_lens.extract_page_chrome_lens", AsyncMock(return_value=(mock_lens_regions, mock_lens_panels, []))):
            with patch("server.summary_ocr.persist_summary_ocr", AsyncMock()):
                pages_with_text, failed, ocr_errors = await run_summary_ocr_job(job, runtime)

        self.assertEqual(pages_with_text, 1)
        self.assertEqual(len(failed), 0)
        self.assertEqual(pages[0]["textRegions"], mock_lens_regions)
        self.assertEqual(pages[0]["panel_detections"], mock_lens_panels)
        # Flow 3 did not run local neural OCR
        runtime.run_summary_ocr.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
