import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, call, Mock, patch

from server.main import MangaSummaryRequest, SummaryJobController
from server.manga_summary import load_summary, update_summary_job, save_summary
from server.summary_scheduler import SummaryScheduler
from server.summary_task import run_summary_task


class TestSummaryScheduler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="summary_scheduler_test_"))
        self.controller = SummaryJobController()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _create_page(self, folder: str, title: str, name: str, text: str = "some text"):
        page = self.root / folder
        page.mkdir(parents=True, exist_ok=True)
        (page / "final.png").write_bytes(b"png")
        (page / "input.png").write_bytes(b"png")
        (page / "meta.json").write_text(
            json.dumps({"mangaTitle": title, "originalName": name}), encoding="utf-8"
        )
        (page / "text_regions.json").write_text(
            json.dumps([{"original_text": text}]), encoding="utf-8"
        )

    async def test_summary_task_delegates_and_always_cleans_up(self):
        generate_summary = AsyncMock()
        reclaim_memory = AsyncMock()
        controller = Mock()
        current_task = asyncio.current_task()

        await run_summary_task(
            None,
            MangaSummaryRequest(mangaTitle="Series"),
            "store",
            "group-id",
            "Series",
            generate_summary=generate_summary,
            log=Mock(),
            update_summary_job=AsyncMock(),
            controller=controller,
            reclaim_memory=reclaim_memory,
        )

        generate_summary.assert_awaited_once()
        controller.unregister_task.assert_has_calls([
            call("group-id", current_task),
            call("Series", current_task),
        ])
        reclaim_memory.assert_awaited_once_with(None)

    async def test_reconcile_interrupted_generating_jobs(self):
        # Job 1: generating without summary -> should be reconciled to queued
        update_summary_job(
            self.root,
            "InterruptedSeries",
            "generating",
            stage="detecting",
            progress=25,
            message="Detecting text",
        )
        # Job 2: generating with summary -> should be reconciled to ready
        save_summary(
            self.root,
            "CompletedSeries",
            {
                "mangaTitle": "CompletedSeries",
                "jobStatus": "generating",
                "summary": "Full summary text",
                "provider": "deepseek",
                "model": "deepseek-flash",
            },
        )

        scheduler = SummaryScheduler(
            store_getter=lambda: None,
            controller=self.controller,
            run_task_fn=AsyncMock(),
            request_cls=MangaSummaryRequest,
            result_root=self.root,
        )

        await scheduler.reconcile()

        job1 = load_summary(self.root, "InterruptedSeries")
        self.assertEqual(job1["jobStatus"], "queued")
        self.assertEqual(job1["jobProgress"], 0)
        self.assertEqual(job1["jobMessage"], "Waiting for an available worker")

        job2 = load_summary(self.root, "CompletedSeries")
        self.assertEqual(job2["jobStatus"], "ready")
        self.assertEqual(job2["jobProgress"], 100)
        self.assertEqual(job2["jobStage"], "complete")

    async def test_scheduler_picks_up_and_executes_queued_jobs_on_start(self):
        self._create_page("page_1", "Series1", "1.png")
        self._create_page("page_2", "Series2", "2.png")

        update_summary_job(
            self.root,
            "Series1",
            "queued",
            provider="deepseek",
            model="deepseek-flash",
            message="Waiting for an available worker",
        )
        update_summary_job(
            self.root,
            "Series2",
            "queued",
            provider="gemini",
            model="gemini-3.1-flash-lite",
            message="Waiting for an available worker",
        )

        executed = []

        async def fake_run_task(
            request, data, store, group_value, clean_title, worker=None, pause_event=None
        ):
            executed.append((clean_title, data.summaryModel))
            update_summary_job(
                self.root, clean_title, "ready", stage="complete", progress=100
            )

        scheduler = SummaryScheduler(
            store_getter=lambda: None,
            controller=self.controller,
            run_task_fn=fake_run_task,
            request_cls=MangaSummaryRequest,
            result_root=self.root,
            max_concurrent=1,
        )

        await scheduler.start()

        # Allow scheduler loop to process jobs
        for _ in range(50):
            if len(executed) == 2:
                break
            await asyncio.sleep(0.05)

        await scheduler.stop()

        self.assertEqual(executed, [
            ("Series1", "deepseek:deepseek-flash"),
            ("Series2", "gemini:gemini-3.1-flash-lite"),
        ])
        job1 = load_summary(self.root, "Series1")
        job2 = load_summary(self.root, "Series2")
        self.assertEqual(job1["jobStatus"], "ready")
        self.assertEqual(job2["jobStatus"], "ready")

    async def test_scheduler_ignores_paused_jobs(self):
        self._create_page("page_1", "PausedSeries", "1.png")

        update_summary_job(
            self.root,
            "PausedSeries",
            "paused",
            provider="deepseek",
            model="deepseek-flash",
            message="Paused",
        )

        executed = []

        async def fake_run_task(*_args, **_kwargs):
            executed.append(True)

        scheduler = SummaryScheduler(
            store_getter=lambda: None,
            controller=self.controller,
            run_task_fn=fake_run_task,
            request_cls=MangaSummaryRequest,
            result_root=self.root,
        )

        await scheduler.start()
        await asyncio.sleep(0.1)
        await scheduler.stop()

        self.assertEqual(executed, [])

    async def test_scheduler_wakes_on_new_job(self):
        self._create_page("page_1", "NewSeries", "1.png")

        executed = []

        async def fake_run_task(
            request, data, store, group_value, clean_title, worker=None, pause_event=None
        ):
            executed.append(clean_title)
            update_summary_job(self.root, clean_title, "ready", stage="complete", progress=100)

        scheduler = SummaryScheduler(
            store_getter=lambda: None,
            controller=self.controller,
            run_task_fn=fake_run_task,
            request_cls=MangaSummaryRequest,
            result_root=self.root,
        )

        await scheduler.start()
        self.assertEqual(executed, [])

        # Queue a new job dynamically
        update_summary_job(
            self.root,
            "NewSeries",
            "queued",
            provider="deepseek",
            model="deepseek-flash",
        )
        scheduler.wake()

        for _ in range(50):
            if executed:
                break
            await asyncio.sleep(0.05)

        await scheduler.stop()
        self.assertEqual(executed, ["NewSeries"])

    async def test_reconcile_queued_jobs_with_existing_summary(self):
        save_summary(
            self.root,
            "AlreadySummarizedSeries",
            {
                "mangaTitle": "AlreadySummarizedSeries",
                "jobStatus": "queued",
                "summary": "Existing synopsis text",
                "jobMessage": "Waiting for an available worker",
            },
        )

        scheduler = SummaryScheduler(
            store_getter=lambda: None,
            controller=self.controller,
            run_task_fn=AsyncMock(),
            request_cls=MangaSummaryRequest,
            result_root=self.root,
        )

        await scheduler.reconcile()

        job = load_summary(self.root, "AlreadySummarizedSeries")
        self.assertEqual(job["jobStatus"], "ready")
        self.assertEqual(job["jobProgress"], 100)
        self.assertEqual(job["jobStage"], "complete")

    async def test_scheduler_does_not_loop_on_already_ready_jobs(self):
        self._create_page("page_1", "ReadySeries", "1.png")
        save_summary(
            self.root,
            "ReadySeries",
            {
                "mangaTitle": "ReadySeries",
                "jobStatus": "queued",
                "summary": "Existing synopsis text",
                "jobMessage": "Waiting for an available worker",
            },
        )

        executed_count = 0

        async def fake_run_task(*_args, **_kwargs):
            nonlocal executed_count
            executed_count += 1

        scheduler = SummaryScheduler(
            store_getter=lambda: None,
            controller=self.controller,
            run_task_fn=fake_run_task,
            request_cls=MangaSummaryRequest,
            result_root=self.root,
        )

        await scheduler.start()
        await asyncio.sleep(0.1)
        await scheduler.stop()

        # Because the job already had a summary and was not set to regenerate,
        # reconcile marks it ready and list_runnable filters it out. It must not execute repeatedly.
        self.assertEqual(executed_count, 0)
        job = load_summary(self.root, "ReadySeries")
        self.assertEqual(job["jobStatus"], "ready")


if __name__ == "__main__":
    unittest.main()
