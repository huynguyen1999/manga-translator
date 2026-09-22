import asyncio
import glob
import io
import logging
import os
import re
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from manga_translator.utils.log import (
    UTC7,
    UTC7Formatter,
    CorrelationIdFilter,
    correlation_id_ctx,
    get_correlation_id,
    set_correlation_id,
)
from server.logger import (
    DailyRotatingFileHandler,
    setup_server_logging,
    get_logger,
)
from server.main import app


class TestUTC7Formatter(unittest.TestCase):
    def test_utc7_timestamp_conversion(self):
        formatter = UTC7Formatter(use_color=False)
        # Create a record at known UTC timestamp: 2026-09-13 10:00:00 UTC -> 2026-09-13 17:00:00 +07:00
        utc_dt = datetime(2026, 9, 13, 10, 0, 0, 123000, tzinfo=timezone.utc)
        record = logging.LogRecord(
            name="manga-translator.server",
            level=logging.INFO,
            pathname="server/main.py",
            lineno=100,
            msg="Server started",
            args=(),
            exc_info=None,
        )
        record.created = utc_dt.timestamp()
        record.msecs = 123.0
        set_correlation_id("test-id-123")
        try:
            formatted = formatter.format(record)
            self.assertIn("2026-09-13 17:00:00.123 +07:00", formatted)
            self.assertIn("[INFO]", formatted)
            self.assertIn("[test-id-123]", formatted)
            self.assertIn("[server]", formatted)
            self.assertIn("Server started", formatted)
            # Ensure no ANSI escapes in non-color mode
            self.assertNotIn("\033[", formatted)
        finally:
            set_correlation_id("-")

    def test_color_formatter_has_ansi(self):
        formatter = UTC7Formatter(use_color=True)
        record = logging.LogRecord(
            name="server",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Failure occurred",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        self.assertIn("\033[", formatted)
        self.assertIn("ERROR", formatted)
        self.assertIn("Failure occurred", formatted)


class TestCorrelationId(unittest.TestCase):
    def test_correlation_id_context(self):
        self.assertEqual(get_correlation_id(), "-")
        set_correlation_id("req-abc12345")
        self.assertEqual(get_correlation_id(), "req-abc12345")
        set_correlation_id("-")
        self.assertEqual(get_correlation_id(), "-")

    def test_filter_injects_correlation_id(self):
        filt = CorrelationIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello",
            args=(),
            exc_info=None,
        )
        set_correlation_id("req-xyz999")
        try:
            filt.filter(record)
            self.assertEqual(record.correlation_id, "req-xyz999")
        finally:
            set_correlation_id("-")


class TestDailyRotatingFileHandler(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_daily_file_created(self):
        handler = DailyRotatingFileHandler(log_dir=self.test_dir, prefix="test_server", backup_count=3)
        formatter = UTC7Formatter(use_color=False)
        handler.setFormatter(formatter)

        today_str = datetime.now(UTC7).strftime("%Y-%m-%d")
        expected_file = os.path.join(self.test_dir, f"test_server_{today_str}.log")

        record = logging.LogRecord(
            name="test_mod",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Testing log file creation",
            args=(),
            exc_info=None,
        )
        record.correlation_id = "req-111"
        handler.emit(record)
        handler.close()

        self.assertTrue(os.path.exists(expected_file), f"Expected {expected_file} to exist")
        with open(expected_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Testing log file creation", content)
        self.assertIn("+07:00", content)
        self.assertIn("[req-111]", content)

    def test_cleanup_old_logs(self):
        # Create an old log file from 30 days ago
        old_date = (datetime.now(UTC7).date() - timedelta(days=30)).strftime("%Y-%m-%d")
        old_file = os.path.join(self.test_dir, f"test_server_{old_date}.log")
        with open(old_file, "w", encoding="utf-8") as f:
            f.write("old log\n")

        self.assertTrue(os.path.exists(old_file))

        # Initializing handler with backup_count=14 should trigger cleanup of 30-day-old file
        handler = DailyRotatingFileHandler(log_dir=self.test_dir, prefix="test_server", backup_count=14)
        handler.close()

        self.assertFalse(os.path.exists(old_file), "Old log file should have been cleaned up")

    def test_emit_after_close_reopens_stream(self):
        handler = DailyRotatingFileHandler(log_dir=self.test_dir, prefix="test_server", backup_count=3)
        formatter = UTC7Formatter(use_color=False)
        handler.setFormatter(formatter)

        # Simulate handler close (e.g. from shutdown or dictConfig)
        handler.close()
        self.assertIsNone(handler._stream)

        # Emit should safely reopen the file and NOT throw AttributeError
        record = logging.LogRecord(
            name="test_reopen",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="Cancelled batch item 1234",
            args=(),
            exc_info=None,
        )
        record.correlation_id = "batch-1/item-2"
        handler.emit(record)
        handler.close()

        today_str = datetime.now(UTC7).strftime("%Y-%m-%d")
        expected_file = os.path.join(self.test_dir, f"test_server_{today_str}.log")
        with open(expected_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Cancelled batch item 1234", content)
        self.assertIn("[batch-1/item-2]", content)


class TestMultiLevelRouting(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_file_captures_debug_while_console_is_info(self):
        setup_server_logging(
            log_dir=self.test_dir,
            console_level=logging.INFO,
            file_level=logging.DEBUG,
            retention_days=7,
            force_reconfigure=True,
        )
        logger = get_logger("test_routing")

        # Capture stdout
        captured_stdout = io.StringIO()
        console_handler = logging.getLogger().handlers[0]
        original_stream = console_handler.stream
        console_handler.stream = captured_stdout

        try:
            set_correlation_id("req-routing")
            logger.debug("This is a DEBUG message")
            logger.info("This is an INFO message")
        finally:
            console_handler.stream = original_stream
            set_correlation_id("-")

        # Check console output (should NOT have DEBUG, but SHOULD have INFO)
        console_text = captured_stdout.getvalue()
        self.assertNotIn("This is a DEBUG message", console_text)
        self.assertIn("This is an INFO message", console_text)

        # Check file output (SHOULD have both DEBUG and INFO)
        today_str = datetime.now(UTC7).strftime("%Y-%m-%d")
        log_file = os.path.join(self.test_dir, f"server_{today_str}.log")
        self.assertTrue(os.path.exists(log_file))
        with open(log_file, "r", encoding="utf-8") as f:
            file_text = f.read()
        self.assertIn("This is a DEBUG message", file_text)
        self.assertIn("This is an INFO message", file_text)
        self.assertIn("[DEBUG]", file_text)
        self.assertIn("[INFO]", file_text)
        self.assertIn("[req-routing]", file_text)


class TestFastAPIMiddleware(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_auto_generated_request_id(self):
        response = self.client.get("/queue-size")
        self.assertEqual(response.status_code, 200)
        self.assertIn("x-request-id", response.headers)
        req_id = response.headers["x-request-id"]
        self.assertTrue(req_id.startswith("req-"), f"Expected req- prefix, got {req_id}")

    def test_passed_request_id_preserved(self):
        custom_id = "test-custom-trace-id-456"
        response = self.client.get("/queue-size", headers={"X-Request-ID": custom_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("x-request-id"), custom_id)


class TestContextPropagation(unittest.TestCase):
    def test_in_process_executor_thread_propagation(self):
        from server.in_process_executor import InProcessExecutorInstance
        executor = InProcessExecutorInstance(worker_id=99, translator_params={'verbose': False})
        try:
            set_correlation_id("req-worker-propagate-99")
            async def check_cid():
                return get_correlation_id()
            result = asyncio.run(executor._run_translation(check_cid))
            self.assertEqual(result, "req-worker-propagate-99")
        finally:
            executor.close()
            set_correlation_id("-")


if __name__ == "__main__":
    unittest.main()
