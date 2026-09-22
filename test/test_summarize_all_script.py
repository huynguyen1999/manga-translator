import io
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from devscripts.summarize_all import (
    _api_request,
    _normalize_base_url,
    fetch_manga_groups,
    fetch_summary_jobs,
    main,
    parse_args,
    trigger_summary,
    wait_for_summaries,
)


class TestSummarizeAllScript(unittest.TestCase):
    def test_normalize_base_url(self):
        self.assertEqual(_normalize_base_url("http://127.0.0.1:8000/"), "http://127.0.0.1:8000")
        self.assertEqual(_normalize_base_url("  http://localhost:5003/  "), "http://localhost:5003")
        self.assertEqual(_normalize_base_url(""), "http://127.0.0.1:8000")

    def test_parse_args_defaults(self):
        args = parse_args([])
        self.assertEqual(args.target, "all")
        self.assertEqual(args.url, "http://127.0.0.1:8000")
        self.assertIsNone(args.model)
        self.assertFalse(args.no_regenerate)
        self.assertFalse(args.refresh_text)
        self.assertFalse(args.wait)
        self.assertFalse(args.missing_only)

    def test_parse_args_custom(self):
        args = parse_args([
            "--target", "translated",
            "--url", "http://manga-server:5003",
            "--model", "deepseek-flash",
            "--missing-only",
            "--refresh-text",
            "--wait",
            "--dry-run",
            "--limit", "10",
            "--search", "One Piece",
        ])
        self.assertEqual(args.target, "translated")
        self.assertEqual(args.url, "http://manga-server:5003")
        self.assertEqual(args.model, "deepseek-flash")
        self.assertTrue(args.missing_only)
        self.assertTrue(args.refresh_text)
        self.assertTrue(args.wait)
        self.assertTrue(args.dry_run)
        self.assertEqual(args.limit, 10)
        self.assertEqual(args.search, "One Piece")

    @patch("urllib.request.urlopen")
    def test_fetch_manga_groups_pagination(self, mock_urlopen):
        page1 = {
            "groups": [{"id": "g1", "title": "Manga 1", "count": 10}],
            "nextOffset": 1,
        }
        page2 = {
            "groups": [{"id": "g2", "title": "Manga 2", "count": 20}],
            "nextOffset": None,
        }

        resp1 = MagicMock()
        resp1.getcode.return_value = 200
        resp1.read.return_value = json.dumps(page1).encode("utf-8")
        resp1.__enter__.return_value = resp1

        resp2 = MagicMock()
        resp2.getcode.return_value = 200
        resp2.read.return_value = json.dumps(page2).encode("utf-8")
        resp2.__enter__.return_value = resp2

        mock_urlopen.side_effect = [resp1, resp2]

        groups = fetch_manga_groups("http://127.0.0.1:5003", target="translated")
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["title"], "Manga 1")
        self.assertEqual(groups[1]["title"], "Manga 2")

        # Verify query parameters in requested URL
        first_call_req = mock_urlopen.call_args_list[0][0][0]
        self.assertIn("status=translated", first_call_req.full_url)
        self.assertIn("offset=0", first_call_req.full_url)

        second_call_req = mock_urlopen.call_args_list[1][0][0]
        self.assertIn("offset=1", second_call_req.full_url)

    @patch("urllib.request.urlopen")
    def test_fetch_manga_groups_missing_only(self, mock_urlopen):
        page = {
            "groups": [
                {"id": "g1", "title": "Manga 1", "hasSummary": True},
                {"id": "g2", "title": "Manga 2", "hasSummary": False},
                {"id": "g3", "title": "Manga 3", "hasSummary": None},
            ],
            "nextOffset": None,
        }
        resp = MagicMock()
        resp.getcode.return_value = 200
        resp.read.return_value = json.dumps(page).encode("utf-8")
        resp.__enter__.return_value = resp
        mock_urlopen.return_value = resp

        groups = fetch_manga_groups("http://127.0.0.1:5003", target="all", missing_only=True)
        self.assertEqual(len(groups), 2)
        self.assertEqual([g["id"] for g in groups], ["g2", "g3"])

    @patch("urllib.request.urlopen")
    def test_trigger_summary_payload(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = json.dumps({
            "jobStatus": "queued",
            "jobStage": "concatenating",
            "mangaTitle": "Berserk",
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = trigger_summary(
            base_url="http://127.0.0.1:5003",
            group_id="group-123",
            manga_title="Berserk",
            model="gemini",
            regenerate=True,
            refresh_text=False,
        )

        self.assertEqual(result["jobStatus"], "queued")
        self.assertEqual(result["mangaTitle"], "Berserk")

        call_req = mock_urlopen.call_args[0][0]
        self.assertEqual(call_req.get_method(), "POST")
        self.assertTrue(call_req.full_url.endswith("/api/results/group/summary"))

        sent_body = json.loads(call_req.data.decode("utf-8"))
        self.assertEqual(sent_body["groupId"], "group-123")
        self.assertEqual(sent_body["mangaTitle"], "Berserk")
        self.assertEqual(sent_body["summaryModel"], "gemini")
        self.assertTrue(sent_body["regenerate"])
        self.assertFalse(sent_body["refreshText"])

    @patch("devscripts.summarize_all.fetch_summary_jobs")
    @patch("time.sleep", return_value=None)
    def test_wait_for_summaries(self, _mock_sleep, mock_jobs):
        mock_jobs.side_effect = [
            [{"groupId": "g1", "title": "Manga 1", "status": "generating"}],
            [{"groupId": "g1", "title": "Manga 1", "status": "ready"}],
        ]

        targets = [{"id": "g1", "title": "Manga 1"}]
        counts = wait_for_summaries("http://127.0.0.1:5003", targets, poll_interval=0.01)
        self.assertEqual(counts["ready"], 1)
        self.assertEqual(counts["error"], 0)

    @patch("devscripts.summarize_all.fetch_manga_groups")
    @patch("devscripts.summarize_all.trigger_summary")
    def test_main_dry_run(self, mock_trigger, mock_fetch):
        mock_fetch.return_value = [
            {"id": "g1", "title": "Manga 1", "count": 10, "hasSummary": True},
            {"id": "g2", "title": "Manga 2", "count": 15, "hasSummary": False},
        ]

        stdout_capture = io.StringIO()
        with patch("sys.stdout", stdout_capture):
            exit_code = main(["--dry-run", "--target", "original"])

        self.assertEqual(exit_code, 0)
        mock_trigger.assert_not_called()
        output = stdout_capture.getvalue()
        self.assertIn("Found 2 manga group(s)", output)
        self.assertIn("Dry run enabled: 0 requests sent", output)

    @patch("devscripts.summarize_all.fetch_manga_groups")
    @patch("devscripts.summarize_all.trigger_summary")
    def test_main_execution(self, mock_trigger, mock_fetch):
        mock_fetch.return_value = [
            {"id": "g1", "title": "Manga 1", "count": 10, "hasSummary": True},
        ]
        mock_trigger.return_value = {"jobStatus": "queued", "jobStage": "concatenating"}

        stdout_capture = io.StringIO()
        with patch("sys.stdout", stdout_capture):
            exit_code = main(["--target", "translated", "--model", "deepseek-flash"])

        self.assertEqual(exit_code, 0)
        mock_trigger.assert_called_once_with(
            base_url="http://127.0.0.1:8000",
            group_id="g1",
            manga_title="Manga 1",
            model="deepseek-flash",
            regenerate=True,
            refresh_text=False,
        )

    @patch("devscripts.summarize_all.fetch_manga_groups")
    @patch("devscripts.summarize_all.trigger_summary")
    def test_main_execution_with_refresh_text_and_no_regenerate(self, mock_trigger, mock_fetch):
        mock_fetch.return_value = [
            {"id": "g1", "title": "Original Manga", "count": 5, "hasSummary": False},
        ]
        mock_trigger.return_value = {"jobStatus": "queued", "jobStage": "detecting"}

        stdout_capture = io.StringIO()
        with patch("sys.stdout", stdout_capture):
            exit_code = main(["--target", "original", "--refresh-text", "--no-regenerate"])

        self.assertEqual(exit_code, 0)
        mock_trigger.assert_called_once_with(
            base_url="http://127.0.0.1:8000",
            group_id="g1",
            manga_title="Original Manga",
            model=None,
            regenerate=False,
            refresh_text=True,
        )

    @patch("devscripts.summarize_all.fetch_manga_groups")
    def test_main_handles_fetch_error(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("Server unavailable")

        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            exit_code = main([])

        self.assertEqual(exit_code, 1)
        self.assertIn("Server unavailable", stderr_capture.getvalue())

    def test_root_entrypoint_import(self):
        import summarize_all
        self.assertTrue(hasattr(summarize_all, "main"))


if __name__ == "__main__":
    unittest.main()

