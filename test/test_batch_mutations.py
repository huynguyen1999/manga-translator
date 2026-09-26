import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from server.batch_scheduler import BatchScheduler


class MutatingStore:
    def __init__(self, manifest):
        self.manifest = manifest
        self.database = SimpleNamespace(compacted=[])

        async def compact_page_order(group_id):
            self.database.compacted.append(group_id)

        self.database.compact_page_order = compact_page_order

    async def mutate(self, batch_id, mutator):
        mutator(self.manifest)
        return self.manifest


class BatchMutationTest(unittest.IsolatedAsyncioTestCase):
    def make_scheduler(self):
        store = MutatingStore({
            "id": "batch-a",
            "status": "processing",
            "items": [
                {
                    "id": "queued",
                    "status": "queued",
                    "mangaGroupId": "group-b",
                    "config": {"stale": True},
                },
                {
                    "id": "error",
                    "status": "error",
                    "mangaGroupId": "group-a",
                    "config": {"stale": True},
                },
                {
                    "id": "processing",
                    "status": "processing",
                    "mangaGroupId": "group-c",
                    "mangaTitle": "Old title",
                    "config": {"keep": True},
                },
            ],
        })
        return BatchScheduler(store, None, Path(tempfile.gettempdir())), store

    async def test_scheduler_facade_preserves_mutation_scope_and_wake_behavior(self):
        scheduler, store = self.make_scheduler()

        self.assertEqual((await scheduler.pause("batch-a"))["status"], "paused")
        self.assertTrue(scheduler._wake.is_set())
        await scheduler.resume("batch-a")
        self.assertEqual(store.manifest["status"], "waiting")

        await scheduler.update_translator("batch-a", "provider")
        await scheduler.update_title("batch-a", "  New title  ")
        await scheduler.update_priority("batch-a", True)
        await scheduler.update_manual_review("batch-a", True)

        queued, failed, processing = store.manifest["items"]
        self.assertEqual(store.manifest["settings"], {
            "translator": "provider",
            "keepFailedPagesForEditing": True,
        })
        self.assertEqual(store.manifest["title"], "New title")
        self.assertEqual(store.manifest["mangaTitle"], "New title")
        self.assertTrue(store.manifest["priority"])
        self.assertEqual(queued["mangaTitle"], "New title")
        self.assertNotIn("config", queued)
        self.assertTrue(queued["settings"]["keepFailedPagesForEditing"])
        self.assertNotIn("config", failed)
        self.assertTrue(failed["settings"]["keepFailedPagesForEditing"])
        self.assertEqual(processing["mangaTitle"], "Old title")
        self.assertEqual(processing["config"], {"keep": True})

    async def test_dismiss_compacts_group_order_only_after_active_items_finish(self):
        scheduler, store = self.make_scheduler()

        await scheduler.dismiss("batch-a")
        self.assertTrue(store.manifest["dismissed"])
        self.assertEqual(store.database.compacted, [])

        for item in store.manifest["items"]:
            item["status"] = "completed"
        await scheduler.dismiss("batch-a")
        self.assertEqual(store.database.compacted, ["group-a", "group-b", "group-c"])


if __name__ == "__main__":
    unittest.main()
