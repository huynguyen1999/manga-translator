import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from server.batch_scheduler import BatchScheduler
from server.batch_store import BatchStore


class _Worker:
    def __init__(self):
        self.region = None

    async def render_saved(self, ctx, _config):
        self.region = ctx.text_regions[0]
        ctx.result = Image.new("RGB", (8, 8), "blue")
        return ctx


class _FailingWorker:
    async def render_saved(self, _ctx, _config):
        raise RuntimeError("render failed")


class _Executors:
    def __init__(self, worker=None):
        self.worker = worker or _Worker()

    def free_executors(self):
        return 1

    async def find_executor(self):
        return self.worker

    async def free_executor(self, _worker):
        return None


class RerenderBatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_rerender_uses_saved_folder_without_upload_and_replaces_final(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            results = root / "results"
            folder = results / "page-1"
            folder.mkdir(parents=True)
            Image.new("RGB", (8, 8), "red").save(folder / "final.jpg")
            Image.new("RGB", (8, 8), "white").save(folder / "inpainted.jpg")
            (folder / "text_regions.json").write_text(json.dumps([{
                "id": "bubble-1",
                "lines": [[[1, 1], [7, 1], [7, 7], [1, 7]]],
                "original_text": "hello",
                "translation": "bonjour",
                "font_size": 16,
                "font_family": "Comic Neue",
                "fg_color": [0, 0, 0],
                "bg_color": [255, 255, 255],
                "alignment": "center",
            }]), encoding="utf-8")

            store = BatchStore(root / "batches", results)
            batch = await store.put_batch(
                "rerender-test",
                {
                    "id": "rerender-test",
                    "kind": "rerender",
                    "title": "Test",
                    "settings": {},
                    "items": [{
                        "id": "item-1",
                        "name": "page.png",
                        "pageId": "page-1",
                        "resultFolder": "page-1",
                        "settings": {"renderer": "default"},
                        "status": "queued",
                    }],
                },
                {},
            )
            self.assertEqual(batch["kind"], "rerender")

            executors = _Executors()
            scheduler = BatchScheduler(store, executors, results)
            claimed = await scheduler._claim_rerender_item("rerender-test", "item-1")
            self.assertIsNotNone(claimed)
            await scheduler._process_rerender_item("rerender-test", "item-1", scheduler.executors.worker)
            self.assertEqual(executors.worker.region.font_family, "Comic Neue")
            self.assertEqual(executors.worker.region.alignment, "center")

            with Image.open(folder / "final.jpg") as final:
                self.assertEqual(final.getpixel((0, 0)), (0, 0, 254))
            updated_regions = json.loads((folder / "text_regions.json").read_text(encoding="utf-8"))
            self.assertEqual(updated_regions[0]["translation"], "bonjour")
            self.assertEqual((await store.get_batch("rerender-test"))["status"], "completed")

            Image.new("RGB", (8, 8), "green").save(folder / "final.jpg")
            await store.put_batch(
                "rerender-failure",
                {
                    "id": "rerender-failure",
                    "kind": "rerender",
                    "title": "Test",
                    "settings": {},
                    "items": [{
                        "id": "item-2",
                        "name": "page.png",
                        "pageId": "page-1",
                        "resultFolder": "page-1",
                        "settings": {},
                        "status": "queued",
                    }],
                },
                {},
            )
            failing_scheduler = BatchScheduler(store, _Executors(_FailingWorker()), results)
            await failing_scheduler._claim_rerender_item("rerender-failure", "item-2")
            await failing_scheduler._process_rerender_item(
                "rerender-failure", "item-2", failing_scheduler.executors.worker
            )
            with Image.open(folder / "final.jpg") as final:
                self.assertGreater(final.getpixel((0, 0))[1], 100)
            self.assertEqual((await store.get_batch("rerender-failure"))["status"], "error")


if __name__ == "__main__":
    unittest.main()
