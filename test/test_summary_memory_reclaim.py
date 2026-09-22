import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

from server import main
from server.main import MangaSummaryRequest, _run_summary_task, _reclaim_summary_memory


class TestSummaryMemoryReclaim(unittest.IsolatedAsyncioTestCase):
    async def test_reclaim_summary_memory_invokes_executors_and_device_cleanup(self):
        worker1 = MagicMock()
        worker1.reclaim_memory = AsyncMock()
        worker2 = MagicMock()
        worker2.reclaim_memory = AsyncMock()

        passed_worker = MagicMock()
        passed_worker.reclaim_memory = AsyncMock()

        class MockExecutors:
            list = [worker1, worker2]

        with patch("server.main.executor_instances", MockExecutors()), \
             patch("server.main.empty_device_cache") as mock_empty_cache, \
             patch("server.main.gc.collect") as mock_gc:
            await _reclaim_summary_memory(passed_worker)

            worker1.reclaim_memory.assert_awaited_once()
            worker2.reclaim_memory.assert_awaited_once()
            passed_worker.reclaim_memory.assert_awaited_once()
            mock_empty_cache.assert_called_once()
            mock_gc.assert_called_once()

    async def test_run_summary_task_reclaims_memory_on_success(self):
        req = MangaSummaryRequest(mangaTitle="Series")
        worker = MagicMock()
        worker.reclaim_memory = AsyncMock()

        with patch("server.main._generate_manga_summary", AsyncMock()), \
             patch("server.main._reclaim_summary_memory", AsyncMock()) as mock_reclaim:
            await _run_summary_task(None, req, None, "Series", "Series", worker=worker)
            mock_reclaim.assert_awaited_once_with(worker)

    async def test_run_summary_task_reclaims_memory_on_http_exception(self):
        req = MangaSummaryRequest(mangaTitle="Series")
        worker = MagicMock()
        worker.reclaim_memory = AsyncMock()

        with patch("server.main._generate_manga_summary", AsyncMock(side_effect=HTTPException(422, "No text"))), \
             patch("server.main._update_summary_job_for", AsyncMock()), \
             patch("server.main._reclaim_summary_memory", AsyncMock()) as mock_reclaim:
            await _run_summary_task(None, req, None, "Series", "Series", worker=worker)
            mock_reclaim.assert_awaited_once_with(worker)

    async def test_run_summary_task_reclaims_memory_on_generic_exception(self):
        req = MangaSummaryRequest(mangaTitle="Series")
        worker = MagicMock()
        worker.reclaim_memory = AsyncMock()

        with patch("server.main._generate_manga_summary", AsyncMock(side_effect=RuntimeError("Unexpected error"))), \
             patch("server.main._update_summary_job_for", AsyncMock()), \
             patch("server.main._reclaim_summary_memory", AsyncMock()) as mock_reclaim:
            await _run_summary_task(None, req, None, "Series", "Series", worker=worker)
            mock_reclaim.assert_awaited_once_with(worker)

    async def test_run_summary_task_reclaims_memory_on_cancellation(self):
        req = MangaSummaryRequest(mangaTitle="Series")
        worker = MagicMock()
        worker.reclaim_memory = AsyncMock()

        with patch("server.main._generate_manga_summary", AsyncMock(side_effect=asyncio.CancelledError())), \
             patch("server.main._reclaim_summary_memory", AsyncMock()) as mock_reclaim:
            with self.assertRaises(asyncio.CancelledError):
                await _run_summary_task(None, req, None, "Series", "Series", worker=worker)
            mock_reclaim.assert_awaited_once_with(worker)

    async def test_execute_ocr_reclaims_memory_in_finally_block_on_error(self):
        import json
        import tempfile
        import shutil
        from pathlib import Path

        root = Path(tempfile.mkdtemp(prefix="summary_ocr_reclaim_"))
        original_root = main.RESULT_ROOT
        main.RESULT_ROOT = root

        worker = MagicMock()
        worker.reclaim_memory = AsyncMock()
        worker.extract_text = AsyncMock(side_effect=RuntimeError("OCR Crash"))

        try:
            page = root / "page-1"
            page.mkdir()
            (page / "final.png").write_bytes(b"png")
            (page / "input.png").write_bytes(b"png")
            (page / "meta.json").write_text(
                json.dumps({"mangaTitle": "Series", "originalName": "page.png"}),
                encoding="utf-8",
            )

            with patch.object(main, "_postgres", return_value=None), \
                 patch.object(main, "generate_synopsis", AsyncMock(return_value="Synopsis")), \
                 patch("server.main.empty_device_cache") as mock_empty_cache, \
                 patch("server.main.gc.collect") as mock_gc:
                # OCR error on the page shouldn't prevent finally cleanup
                try:
                    await main._generate_manga_summary(
                        None,
                        main.MangaSummaryRequest(mangaTitle="Series", refreshText=True),
                        worker=worker,
                    )
                except HTTPException:
                    pass

                worker.reclaim_memory.assert_awaited_once()
                self.assertTrue(mock_empty_cache.called)
                self.assertTrue(mock_gc.called)
        finally:
            main.RESULT_ROOT = original_root
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
