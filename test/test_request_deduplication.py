import asyncio
import json
import pickle
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from manga_translator import Config
from server.request_extraction import _completed_web_request, _frame, while_streaming


class TestRequestDeduplication(unittest.TestCase):
    def test_completed_request_reuses_saved_result(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "saved-result"
            folder.mkdir()
            (folder / "final.png").write_bytes(b"result")
            (folder / "meta.json").write_text(json.dumps({"requestId": "request-1"}), encoding="utf-8")

            with patch("server.request_extraction.RESULT_DIR", Path(root)):
                self.assertEqual(_completed_web_request("request-1"), "saved-result")

    def test_frame_has_expected_stream_header(self):
        frame = _frame(1, b"final_ready:folder")
        self.assertEqual(frame[0], 1)
        self.assertEqual(int.from_bytes(frame[1:5], "big"), len(frame) - 5)

    def test_new_request_forwards_progress_and_closes_stream(self):
        async def run():
            async def execute(_task, sender):
                sender(1, b"upscaling")
                sender(0, pickle.dumps(object()))

            with (
                patch("server.request_extraction.to_pil_image", AsyncMock(return_value=MagicMock())),
                patch("server.request_extraction.wait_in_queue", execute),
            ):
                response = await while_streaming(MagicMock(), lambda _ctx: b"done", Config(), b"image")
                return [chunk async for chunk in response.body_iterator]

        chunks = asyncio.run(run())
        self.assertEqual([chunk[0] for chunk in chunks], [1, 0])


if __name__ == "__main__":
    unittest.main()
