import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from server import main
from server.manga_summary import load_summary, update_summary_job


class TestSummaryJobsApi(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="summary_jobs_api_"))
        self.original_root = main.RESULT_ROOT
        self.original_store = main.postgres_store
        main.RESULT_ROOT = self.root
        main.postgres_store = None
        self.client = TestClient(main.app)

    def tearDown(self):
        main.RESULT_ROOT = self.original_root
        main.postgres_store = self.original_store
        shutil.rmtree(self.root, ignore_errors=True)

    def test_filesystem_listing_and_non_destructive_dismissal(self):
        update_summary_job(self.root, "Series", "ready", stage="complete", progress=100)
        listed = self.client.get("/api/results/group/summary/jobs")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["title"], "Series")

        dismissed = self.client.post(
            "/api/results/group/summary/dismiss",
            json={"mangaTitle": "Series"},
        )
        self.assertEqual(dismissed.status_code, 200)
        self.assertEqual(self.client.get("/api/results/group/summary/jobs").json(), [])
        saved = load_summary(self.root, "Series")
        self.assertTrue(saved and saved["jobStatus"] == "ready")
        self.assertTrue(saved["jobDismissed"])

    def test_summary_request_is_queued_before_worker_execution(self):
        page = self.root / "page-1"
        page.mkdir()
        (page / "final.png").write_bytes(b"result")
        (page / "input.png").write_bytes(b"source")
        (page / "meta.json").write_text('{"mangaTitle": "Series"}', encoding="utf-8")
        (page / "text_regions.json").write_text('[{"original_text": "text"}]', encoding="utf-8")

        class Workers:
            async def find_executor(self):
                return object()

            async def free_executor(self, _worker):
                pass

        with patch("server.main._generate_manga_summary", new=AsyncMock()), \
             patch("server.myqueue.executor_instances", Workers()):
            response = self.client.post(
                "/api/results/group/summary",
                json={"mangaTitle": "Series", "summaryModel": "deepseek-reasoner"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["jobStatus"], "queued")
        saved = load_summary(self.root, "Series")
        self.assertEqual(saved["provider"], "deepseek")
        self.assertEqual(saved["model"], "deepseek-flash")

    def test_summary_pause_resume_and_stop(self):
        page = self.root / "page-1"
        page.mkdir()
        (page / "final.png").write_bytes(b"result")
        (page / "input.png").write_bytes(b"source")
        (page / "meta.json").write_text('{"mangaTitle": "Series"}', encoding="utf-8")
        (page / "text_regions.json").write_text('[{"original_text": "text"}]', encoding="utf-8")

        update_summary_job(self.root, "Series", "generating", stage="detecting", progress=25)
        
        # Test Pause
        pause_resp = self.client.post(
            "/api/results/group/summary/pause",
            json={"mangaTitle": "Series"},
        )
        self.assertEqual(pause_resp.status_code, 200)
        self.assertEqual(pause_resp.json()["jobStatus"], "paused")
        saved = load_summary(self.root, "Series")
        self.assertEqual(saved["jobStatus"], "paused")
        self.assertFalse(saved.get("jobDismissed", False))

        # Test Resume
        with patch("server.main._generate_manga_summary", new=AsyncMock()):
            resume_resp = self.client.post(
                "/api/results/group/summary/resume",
                json={"mangaTitle": "Series"},
            )
        self.assertEqual(resume_resp.status_code, 200)
        self.assertEqual(resume_resp.json()["jobStatus"], "generating")
        saved = load_summary(self.root, "Series")
        self.assertEqual(saved["jobStatus"], "generating")

        # Test Stop
        stop_resp = self.client.post(
            "/api/results/group/summary/stop",
            json={"mangaTitle": "Series"},
        )
        self.assertEqual(stop_resp.status_code, 200)
        self.assertEqual(stop_resp.json()["status"], "stopped")
        saved = load_summary(self.root, "Series")
        self.assertTrue(saved is None or saved.get("jobDismissed", False))


if __name__ == "__main__":
    unittest.main()
