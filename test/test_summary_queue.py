import asyncio
import unittest
from unittest.mock import patch

from server.myqueue import SummaryQueueElement, task_queue, wait_in_queue


class TestSummaryQueue(unittest.IsolatedAsyncioTestCase):
    async def test_summary_jobs_share_available_workers(self):
        active = 0
        peak = 0
        release = asyncio.Event()
        slots = asyncio.Semaphore(2)

        class Workers:
            async def find_executor(self):
                await slots.acquire()
                return object()

            async def free_executor(self, _worker):
                slots.release()

        async def run(_worker):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await release.wait()
            active -= 1

        jobs = [SummaryQueueElement(run) for _ in range(3)]
        for job in jobs:
            task_queue.add_task(job)

        with patch("server.myqueue.executor_instances", Workers()):
            running = asyncio.gather(*(wait_in_queue(job, None) for job in jobs))
            await asyncio.sleep(0)
            self.assertEqual(peak, 2)
            release.set()
            await running

    async def test_wait_in_queue_reclaims_memory_on_finish(self):
        reclaimed = False

        class WorkerWithReclaim:
            async def reclaim_memory(self):
                nonlocal reclaimed
                reclaimed = True

            def free_executor(self):
                pass

        class Workers:
            async def find_executor(self):
                return WorkerWithReclaim()

            async def free_executor(self, _worker):
                pass

        async def run(_worker):
            pass

        job = SummaryQueueElement(run)
        task_queue.add_task(job)

        with patch("server.myqueue.executor_instances", Workers()):
            await wait_in_queue(job, None)

        self.assertTrue(reclaimed)

    async def test_summary_job_releases_worker_before_synopsis_generation(self):
        import json
        import tempfile
        import shutil
        from pathlib import Path
        from unittest.mock import AsyncMock
        from server import main

        root = Path(tempfile.mkdtemp(prefix="summary_worker_release_"))
        original_root = main.RESULT_ROOT
        main.RESULT_ROOT = root
        worker_held_during_synopsis = None

        try:
            page = root / "page-1"
            page.mkdir()
            (page / "final.png").write_bytes(b"png")
            (page / "input.png").write_bytes(b"png")
            (page / "meta.json").write_text(
                json.dumps({"mangaTitle": "Series", "originalName": "page.png"}),
                encoding="utf-8",
            )

            leased = 0

            class TrackedWorkers:
                async def find_executor(self):
                    nonlocal leased
                    leased += 1
                    return object()

                async def free_executor(self, _worker):
                    nonlocal leased
                    leased -= 1

            async def fake_synopsis(*_args, **_kwargs):
                nonlocal worker_held_during_synopsis, leased
                worker_held_during_synopsis = (leased > 0)
                return "Generated Synopsis"

            async def fake_ocr(*_args, **_kwargs):
                return [{"original_text": "text"}]

            with patch.object(main, "_postgres", return_value=None), \
                 patch.object(main, "_run_summary_ocr", fake_ocr), \
                 patch.object(main, "generate_synopsis", fake_synopsis), \
                 patch("server.main.executor_instances", TrackedWorkers()), \
                 patch("server.myqueue.executor_instances", TrackedWorkers()):
                await main._generate_manga_summary(
                    None,
                    main.MangaSummaryRequest(mangaTitle="Series", refreshText=True),
                )

            self.assertIsNotNone(worker_held_during_synopsis)
            self.assertFalse(worker_held_during_synopsis)
            self.assertEqual(leased, 0)
        finally:
            main.RESULT_ROOT = original_root
            shutil.rmtree(root, ignore_errors=True)

    async def test_summary_cached_pages_do_not_acquire_worker(self):
        import json
        import tempfile
        import shutil
        from pathlib import Path
        from unittest.mock import AsyncMock
        from server import main

        root = Path(tempfile.mkdtemp(prefix="summary_cached_no_worker_"))
        original_root = main.RESULT_ROOT
        main.RESULT_ROOT = root
        acquired_worker = False

        try:
            page = root / "page-1"
            page.mkdir()
            (page / "final.png").write_bytes(b"png")
            (page / "input.png").write_bytes(b"png")
            (page / "meta.json").write_text(
                json.dumps({"mangaTitle": "Series", "originalName": "page.png"}),
                encoding="utf-8",
            )
            (page / "text_regions.json").write_text(
                json.dumps([{"original_text": "cached text"}]), encoding="utf-8"
            )

            class FailingWorkers:
                async def find_executor(self):
                    nonlocal acquired_worker
                    acquired_worker = True
                    raise AssertionError("Worker should not be acquired for fully cached summary")

                async def free_executor(self, _worker):
                    pass

            with patch.object(main, "_postgres", return_value=None), \
                 patch.object(main, "generate_synopsis", AsyncMock(return_value="Synopsis")), \
                 patch("server.main.executor_instances", FailingWorkers()), \
                 patch("server.myqueue.executor_instances", FailingWorkers()):
                await main._generate_manga_summary(
                    None,
                    main.MangaSummaryRequest(mangaTitle="Series", regenerate=True),
                )

            self.assertFalse(acquired_worker)
        finally:
            main.RESULT_ROOT = original_root
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
