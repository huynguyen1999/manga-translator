import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.batch_store import BatchConflict, BatchStore, InvalidBatch


class BatchStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_upload_is_atomic_and_duplicate_put_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            manifest = {
                "id": "batch-1",
                "title": "Chapter",
                "kind": "translation",
                "settings": {"translator": "deepseek"},
                "items": [
                    {"id": "page-1", "name": "page-1.png"},
                    {"id": "page-2", "name": "page-2.png"},
                ],
            }
            result = await store.put_batch(
                "batch-1",
                manifest,
                {"page-1": ("page-1.png", b"one"), "page-2": ("page-2.png", b"two")},
            )
            self.assertEqual([item["name"] for item in result["items"]], ["page-1.png", "page-2.png"])
            self.assertEqual(result["kind"], "translation")
            self.assertTrue(result["items"][0]["inputUrl"].startswith("/api/batches/"))
            self.assertTrue((workspace / "batches" / "batch-1" / "manifest.json").is_file())
            self.assertEqual(
                (await store.put_batch("batch-1", manifest, {}))["id"],
                "batch-1",
            )
            with self.assertRaises(BatchConflict):
                await store.put_batch(
                    "batch-1",
                    {**manifest, "items": [{"id": "different", "name": "page.png"}]},
                    {"different": ("page.png", b"data")},
                )

    async def test_page_order_is_reserved_when_batch_is_created(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            manifest = {
                "id": "batch-order",
                "title": "Series",
                "mangaTitle": "Series",
                "items": [
                    {"id": "page-2", "name": "page-2.png"},
                    {"id": "page-1", "name": "page-1.png"},
                ],
            }
            files = {
                "page-2": ("page-2.png", b"two"),
                "page-1": ("page-1.png", b"one"),
            }

            created = await store.put_batch("batch-order", manifest, files)
            self.assertEqual([item["pageOrder"] for item in created["items"]], [1, 2])

            failed = await store.mutate(
                "batch-order",
                lambda batch: batch["items"][0].update(status="error"),
            )
            self.assertEqual([item["pageOrder"] for item in failed["items"]], [1, 2])

            later = await store.put_batch(
                "batch-order-later",
                {
                    "id": "batch-order-later",
                    "mangaTitle": "Series",
                    "items": [{"id": "page-3", "name": "page-3.png"}],
                },
                {"page-3": ("page-3.png", b"three")},
            )
            self.assertEqual(later["items"][0]["pageOrder"], 3)

            duplicate = await store.put_batch("batch-order", manifest, {})
            self.assertEqual([item["pageOrder"] for item in duplicate["items"]], [1, 2])

    async def test_rejects_incomplete_upload_without_exposing_batch(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            with self.assertRaises(InvalidBatch):
                await store.put_batch(
                    "batch-2",
                    {"id": "batch-2", "items": [{"id": "page", "name": "page.png"}]},
                    {},
                )
            self.assertFalse((workspace / "batches" / "batch-2").exists())

    async def test_reconcile_returns_interrupted_item_to_queue(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            await store.put_batch(
                "batch-3",
                {
                    "id": "batch-3",
                    "status": "waiting",
                    "items": [{"id": "page", "name": "page.png"}],
                },
                {"page": ("page.png", b"data")},
            )
            manifest_path = workspace / "batches" / "batch-3" / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["status"] = "processing"
            manifest["items"][0]["status"] = "processing"
            manifest_path.write_text(json.dumps(manifest))
            await store.reconcile()
            batch = await store.get_batch("batch-3")
            self.assertEqual(batch["status"], "waiting")
            self.assertEqual(batch["items"][0]["status"], "queued")

    async def test_reconcile_adopts_result_written_before_manifest_update(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            await store.put_batch(
                "batch-4",
                {"id": "batch-4", "items": [{"id": "page", "name": "page.png"}]},
                {"page": ("page.png", b"data")},
            )
            result = workspace / "results" / "result-folder"
            result.mkdir(parents=True)
            (result / "final.png").write_bytes(b"translated")
            (result / "input.png").write_bytes(b"source")
            (result / "meta.json").write_text(json.dumps({"requestId": "batch-4:page"}))
            manifest_path = workspace / "batches" / "batch-4" / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["status"] = "processing"
            manifest["items"][0]["status"] = "processing"
            manifest_path.write_text(json.dumps(manifest))

            await store.reconcile()
            batch = await store.get_batch("batch-4")
            self.assertEqual(batch["status"], "completed")
            self.assertEqual(batch["items"][0]["resultFolder"], "result-folder")
            self.assertEqual(batch["items"][0]["inputUrl"], "/result/result-folder/input.png")

    async def test_processing_submission_is_requeued(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            batch = await store.put_batch(
                "batch-5",
                {
                    "id": "batch-5",
                    "status": "processing",
                    "items": [{"id": "page", "name": "page.png", "status": "processing"}],
                },
                {"page": ("page.png", b"data")},
            )
            self.assertEqual(batch["status"], "waiting")
            self.assertEqual(batch["items"][0]["status"], "queued")

    async def test_prioritized_batches_are_listed_first_in_fifo_order(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            for batch_id, added_at, priority in (
                ("batch-old", 1, None),
                ("batch-priority-new", 3, True),
                ("batch-priority-old", 2, True),
            ):
                await store.put_batch(
                    batch_id,
                    {
                        "id": batch_id,
                        "addedAt": added_at,
                        **({"priority": priority} if priority is not None else {}),
                        "items": [{"id": "page", "name": "page.png"}],
                    },
                    {"page": ("page.png", b"data")},
                )

            batches = await store.list_batches()
            self.assertEqual(
                [batch["id"] for batch in batches],
                ["batch-priority-old", "batch-priority-new", "batch-old"],
            )
            self.assertTrue(batches[0]["priority"])

    async def test_summary_list_omits_item_urls_and_counts_items(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            await store.put_batch(
                "batch-summary",
                {
                    "id": "batch-summary",
                    "items": [
                        {"id": "queued", "name": "queued.png", "status": "queued"},
                        {"id": "failed", "name": "failed.png", "status": "error"},
                        {"id": "done", "name": "done.png", "status": "completed", "needsReview": True},
                    ],
                    "totalItems": 3,
                    "completedCount": 1,
                },
                {"queued": ("queued.png", b"queued"), "failed": ("failed.png", b"failed")},
            )

            with patch("server.batch_store.final_file") as final_file, patch("server.batch_store.find_asset") as find_asset:
                summaries = await store.list_batch_summaries()

            self.assertEqual(len(summaries), 1)
            self.assertNotIn("items", summaries[0])
            self.assertEqual(summaries[0]["queuedCount"], 1)
            self.assertEqual(summaries[0]["failedCount"], 1)
            self.assertEqual(summaries[0]["needsReviewCount"], 1)
            final_file.assert_not_called()
            find_asset.assert_not_called()

    async def test_review_update_does_not_hydrate_every_batch_item(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            await store.put_batch(
                "batch-review",
                {
                    "id": "batch-review",
                    "items": [
                        {
                            "id": "page",
                            "name": "page.png",
                            "status": "completed",
                            "resultFolder": "result-folder",
                            "needsReview": True,
                        }
                    ],
                },
                {},
            )

            with patch("server.batch_store.final_file") as final_file, patch("server.batch_store.find_asset") as find_asset:
                updated = await store.update_review_for_result("result-folder", False)

            self.assertEqual(updated, 1)
            self.assertFalse((await store.get_batch("batch-review"))["items"][0]["needsReview"])
            final_file.assert_not_called()
            find_asset.assert_not_called()

    async def test_list_runnable_batches_only_returns_waiting_or_processing(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            items = [{"id": "item-1", "name": "1.png"}]
            files = {"item-1": ("1.png", b"test")}
            await store.put_batch("batch-waiting", {"id": "batch-waiting", "status": "waiting", "items": items}, files)
            await store.put_batch("batch-processing", {"id": "batch-processing", "status": "processing", "items": items}, files)
            await store.put_batch("batch-completed", {"id": "batch-completed", "status": "completed", "items": items}, files)
            await store.put_batch("batch-paused", {"id": "batch-paused", "status": "paused", "items": items}, files)

    async def test_manga_group_id_and_is_new_group_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            manifest = {
                "id": "batch-group-test",
                "title": "Existing Series",
                "mangaGroupId": "group-uuid-123",
                "isNewGroup": False,
                "items": [
                    {"id": "page-1", "name": "page-1.png"},
                ],
            }
            result = await store.put_batch(
                "batch-group-test",
                manifest,
                {"page-1": ("page-1.png", b"one")},
            )
            self.assertEqual(result["mangaGroupId"], "group-uuid-123")
            self.assertFalse(result["isNewGroup"])
            self.assertEqual(result["items"][0]["mangaGroupId"], "group-uuid-123")

            summaries = await store.list_batch_summaries()
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["mangaGroupId"], "group-uuid-123")
            self.assertFalse(summaries[0]["isNewGroup"])

    async def test_processing_count_excludes_awaiting_translation_and_reserved(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            manifest = {
                "id": "batch-active-test",
                "title": "Active Test",
                "items": [
                    {"id": "p1", "name": "p1.png"},
                    {"id": "p2", "name": "p2.png"},
                    {"id": "p3", "name": "p3.png"},
                ],
            }
            await store.put_batch(
                "batch-active-test",
                manifest,
                {"p1": ("p1.png", b"1"), "p2": ("p2.png", b"2"), "p3": ("p3.png", b"3")},
            )
            batch = await store.mutate(
                "batch-active-test",
                lambda m: [
                    m["items"][0].update(status="processing", stage="awaiting_translation"),
                    m["items"][1].update(status="processing", stage="ocr"),
                    m["items"][2].update(status="queued", stage="reserved"),
                ] and True,
            )
            # p2 is actively running ocr -> processingCount = 1
            # p1 (awaiting_translation) + p3 (queued/reserved) -> queuedCount = 2
            self.assertEqual(batch["processingCount"], 1)
            self.assertEqual(batch["queuedCount"], 2)

            summaries = await store.list_batch_summaries()
            self.assertEqual(summaries[0]["processingCount"], 1)
            self.assertEqual(summaries[0]["queuedCount"], 2)

    async def test_input_path_fallback_to_result_folder(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            result_dir = workspace / "results" / "folder-res"
            result_dir.mkdir(parents=True)
            (result_dir / "inpainted.jpg").write_bytes(b"inpainted image data")

            manifest = {
                "id": "batch-fb",
                "title": "Fallback Test",
                "items": [
                    {"id": "page-1", "name": "page-1.png", "resultFolder": "folder-res"},
                ],
            }
            await store.put_batch("batch-fb", manifest, {"page-1": ("page-1.png", b"raw")})
            # Unlink raw input to test fallback
            (workspace / "batches" / "batch-fb" / "inputs" / "page-1.png").unlink()
            found_path = await store.input_path("batch-fb", "page-1")
            self.assertEqual(found_path.resolve(), (result_dir / "inpainted.jpg").resolve())


if __name__ == "__main__":
    unittest.main()
