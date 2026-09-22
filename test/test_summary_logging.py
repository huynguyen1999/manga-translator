import logging
import unittest

from server import main


class TestSummaryLogging(unittest.TestCase):
    def test_summary_log_includes_job_context(self):
        with self.assertLogs("manga-translator.summary", level=logging.INFO) as captured:
            main._summary_log("started", "Series", "group-1", pages=2, model="deepseek-reasoner")

        self.assertIn(
            "summary_job event=started title='Series' group='group-1' pages=2 model='deepseek-reasoner'",
            captured.output[0],
        )


if __name__ == "__main__":
    unittest.main()
