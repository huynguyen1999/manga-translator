import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server.api.routes.batches import create_batch_router


class BatchEventsIdleTest(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_batches_do_not_reload_summaries(self):
        store = SimpleNamespace(
            summary_revision=AsyncMock(side_effect=["first", "first", "second"]),
            list_batch_summaries=AsyncMock(side_effect=[
                [{"id": "batch", "status": "processing"}],
                [{"id": "batch", "status": "completed"}],
            ]),
            get_batch=AsyncMock(return_value={"id": "batch", "status": "completed"}),
        )
        _, handlers = create_batch_router(
            lambda: store, lambda: None,
            max_items=1, max_item_bytes=1, max_upload_bytes=1,
        )
        events = handlers[1]()
        try:
            with patch("server.api.routes.batches.asyncio.sleep", new=AsyncMock()):
                self.assertIn('"status":"processing"', await anext(events))
                self.assertEqual(await anext(events), ": keep-alive\n\n")
                self.assertIn('"status":"completed"', await anext(events))
                self.assertIn("event: batch_details", await anext(events))
        finally:
            await events.aclose()
        self.assertEqual(store.list_batch_summaries.await_count, 2)
