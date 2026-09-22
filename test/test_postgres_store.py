import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server.postgres_store import (
    PostgresBatchStore,
    PostgresStore,
    _manga_id,
    _natural_sort_key,
    _parse_finished_at,
    _safe_folder,
)


class _GroupsPool:
    async def fetchrow(self, *_args):
        return {"total_groups": 1, "total_images": 1}

    async def fetch(self, *_args):
        return [{
            "id": "page-id",
            "folder": "page-folder",
            "original_name": "001.png",
            "manga_title": "Series",
            "metadata": {},
            "asset_version": 1,
            "input_name": None,
            "source_type": "translated",
            "finished_at": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            "has_inpainted": False,
            "has_regions": False,
            "page_count": 1,
            "latest_finished_at": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            "group_id": "group-id",
        }]


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _ManifestConnection:
    def __init__(self):
        self.executed = []
        self.items = []
        self.current_max = 0

    def transaction(self):
        return _AsyncContext(self)

    async def execute(self, query, *args):
        self.executed.append((query, args))

    async def fetchval(self, query, *_args):
        if "WHERE id=$1" in query:
            return "group-a"
        if "SELECT GREATEST" in query:
            return self.current_max
        raise AssertionError(query)

    async def fetch(self, *_args):
        return self.items

    async def fetchrow(self, *_args):
        return {
            "id": "page-a",
            "manga_group_id": "group-b",
            "manga_title": "Canonical title",
        }

    async def executemany(self, query, args):
        self.executed.append((query, args))
        if "INSERT INTO batch_items" in query:
            self.items = [
                {"id": row[1], "manga_group_id": row[3], "page_order": row[9]}
                for row in args
            ]


class _ManifestPool:
    def __init__(self):
        self.connection = _ManifestConnection()

    def acquire(self):
        return _AsyncContext(self.connection)


class _ReconcilePool(_ManifestPool):
    def __init__(self, manifest):
        super().__init__()
        self.manifest = manifest

    async def fetch(self, query, *_args):
        if "FROM pages" in query:
            return []
        return [{"manifest": self.manifest}]


class PostgresStoreGroupsTest(unittest.IsolatedAsyncioTestCase):
    async def test_list_groups_does_not_generate_images(self):
        with tempfile.TemporaryDirectory() as root:
            store = PostgresStore("unused", Path(root))
            store.pool = _GroupsPool()
            with patch("server.postgres_store.generate_image_variants") as generate:
                result = await store.list_groups()

            self.assertEqual(result["groups"][0]["title"], "Series")
            generate.assert_not_called()

    async def test_save_manifest_repairs_stale_page_group_relation(self):
        database = PostgresStore("unused", "/tmp/results")
        database.pool = _ManifestPool()
        store = PostgresBatchStore(database, "/tmp/batches", "/tmp/results")
        manifest = {
            "id": "batch-a",
            "title": "Original title",
            "mangaTitle": "Original title",
            "status": "waiting",
            "items": [{
                "id": "item-a",
                "name": "page.png",
                "mangaTitle": "Original title",
                "mangaGroupId": "group-a",
                "resultFolder": "page-folder",
                "status": "queued",
            }],
        }

        await store._save_db_manifest(manifest)

        item = manifest["items"][0]
        self.assertEqual(item["mangaGroupId"], "group-b")
        self.assertEqual(item["pageId"], "page-a")
        self.assertEqual(item["mangaTitle"], "Canonical title")

    async def test_save_manifest_handles_new_item_without_existing_page(self):
        database = PostgresStore("unused", "/tmp/results")
        database.pool = _ManifestPool()
        store = PostgresBatchStore(database, "/tmp/batches", "/tmp/results")
        manifest = {
            "id": "batch-new",
            "title": "New title",
            "mangaTitle": "New title",
            "status": "waiting",
            "items": [{
                "id": "item-new",
                "name": "page.png",
                "mangaTitle": "New title",
                "status": "queued",
            }],
        }

        await store._save_db_manifest(manifest)

        item = manifest["items"][0]
        self.assertIsNone(item["pageId"])
        self.assertEqual(item["pageOrder"], 1)

    async def test_progress_updates_preserve_database_page_reservations(self):
        database = PostgresStore("unused", "/tmp/results")
        database.pool = _ManifestPool()
        store = PostgresBatchStore(database, "/tmp/batches", "/tmp/results")
        manifest = {
            "id": "batch-a", "title": "Series", "mangaTitle": "Series",
            "status": "processing",
            "items": [
                {"id": "a", "name": "1.png", "status": "processing", "pageOrder": 99},
                {"id": "b", "name": "2.png", "status": "queued", "pageOrder": 99},
            ],
        }
        await store._save_db_manifest(manifest)
        self.assertEqual([item["pageOrder"] for item in manifest["items"]], [1, 2])
        # Another batch reserves later positions while this translation is running.
        database.pool.connection.current_max = 12
        for stage in ("detection", "ocr", "translation", "rendering"):
            manifest["items"][0]["stage"] = stage
            manifest["items"][0]["pageOrder"] = 99  # Ignore stale client state.
            await store._save_db_manifest(manifest)
            self.assertEqual([item["pageOrder"] for item in manifest["items"]], [1, 2])

    async def test_new_result_recovers_an_occupied_page_order(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "new-page"
            folder.mkdir()
            Image.new("RGB", (2, 2), "white").save(folder / "final.png")
            (folder / "meta.json").write_text(json.dumps({
                "mangaTitle": "Series", "originalName": "10.webp", "pageOrder": 12,
            }))
            connection = SimpleNamespace(
                transaction=lambda: _AsyncContext(None),
                fetchrow=AsyncMock(return_value={"id": "group-id"}),
                fetchval=AsyncMock(side_effect=lambda query, *args:
                    True if "SELECT EXISTS" in query else
                    20 if "SELECT GREATEST" in query else None),
                execute=AsyncMock(), executemany=AsyncMock(),
            )
            store = PostgresStore("unused", root)
            store.pool = SimpleNamespace(
                fetchrow=AsyncMock(return_value=None), fetch=AsyncMock(return_value=[]),
                acquire=lambda: _AsyncContext(connection),
            )
            with patch.object(store, "resolve_group_id", AsyncMock(return_value="group-id")):
                result = await store.sync_result_folder(folder, generate_variants=False)
            insert = next(call for call in connection.execute.await_args_list
                          if "INSERT INTO pages" in call.args[0])
            self.assertEqual(insert.args[7], 20)
            self.assertEqual(result["metadata"]["pageOrder"], 20)

    async def test_register_result_replaces_the_existing_page(self):
        database = AsyncMock()
        store = PostgresBatchStore(database, "/tmp/batches", "/tmp/results")

        await store.register_result("new-folder", page_order=19, page_id="page-id")

        database.sync_result_folder.assert_awaited_once_with(
            "new-folder",
            page_order=19,
            replace_page_id="page-id",
        )

    async def test_sync_retry_reuses_page_id_and_removes_old_folder(self):
        with tempfile.TemporaryDirectory() as root:
            result_root = Path(root)
            old_folder = result_root / "old-folder"
            new_folder = result_root / "new-folder"
            old_folder.mkdir()
            new_folder.mkdir()
            connection = SimpleNamespace(
                transaction=lambda: _AsyncContext(None),
                fetchrow=AsyncMock(return_value={"id": "group-id"}),
                fetchval=AsyncMock(return_value=None),
                execute=AsyncMock(),
                executemany=AsyncMock(),
            )
            pool = SimpleNamespace(
                fetchrow=AsyncMock(return_value={
                    "id": "page-id",
                    "manga_group_id": "group-id",
                    "manga_title": "Series",
                    "folder": "old-folder",
                    "original_name": "019.png",
                    "original_sort_key": "019.png",
                    "source_type": "translated",
                    "finished_at": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                    "request_id": "request-id",
                    "metadata": {},
                    "text_regions": [],
                    "page_order": 19,
                }),
                fetch=AsyncMock(return_value=[]),
                acquire=lambda: _AsyncContext(connection),
            )
            store = PostgresStore("unused", result_root)
            store.pool = pool
            snapshot = {
                "folder": "new-folder",
                "manga_title": "Series",
                "manga_group_id": "group-id",
                "original_name": "019.png",
                "original_sort_key": "019.png",
                "page_order": 19,
                "source_type": "translated",
                "finished_at": dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc),
                "request_id": "request-id",
                "input_name": "input.png",
                "final_name": "final.png",
                "has_inpainted": False,
                "has_regions": False,
                "has_thumbnail": False,
                "asset_version": "version",
                "metadata": {},
                "text_regions": [],
                "documents": {},
                "artifacts": [],
            }

            with patch.object(store, "_page_snapshot", return_value=snapshot):
                await store.sync_result_folder(new_folder, replace_page_id="page-id")

            insert = next(call for call in connection.execute.await_args_list if "INSERT INTO pages" in call.args[0])
            self.assertIn("ON CONFLICT(id)", insert.args[0])
            self.assertEqual(insert.args[1:4], ("page-id", "new-folder", "group-id"))
            self.assertEqual(insert.args[7], 19)
            self.assertFalse(old_folder.exists())

    async def test_reconcile_preserves_paused_batch_with_queued_items(self):
        with tempfile.TemporaryDirectory() as root:
            result_folder = Path(root) / "page-folder"
            result_folder.mkdir()
            (result_folder / "final.png").write_bytes(b"final")
            manifest = {
                "id": "batch-a",
                "title": "Original title",
                "mangaTitle": "Original title",
                "status": "paused",
                "items": [
                    {
                        "id": "item-a",
                        "name": "page.png",
                        "mangaTitle": "Original title",
                        "mangaGroupId": "group-a",
                        "resultFolder": "page-folder",
                        "requestId": "request-a",
                        "status": "processing",
                    },
                    {
                        "id": "item-b",
                        "name": "page.png",
                        "mangaTitle": "Original title",
                        "mangaGroupId": "group-a",
                        "requestId": "request-b",
                        "status": "processing",
                    },
                    {
                        "id": "item-c",
                        "name": "page.png",
                        "mangaTitle": "Original title",
                        "mangaGroupId": "group-a",
                        "status": "error",
                    },
                ],
            }
            database = PostgresStore("unused", result_folder.parent)
            database.pool = _ReconcilePool(manifest)
            store = PostgresBatchStore(database, Path(root) / "batches", result_folder.parent)

            await store.reconcile()

            self.assertEqual(manifest["status"], "paused")
            self.assertEqual(manifest["items"][0]["status"], "completed")
            self.assertEqual(manifest["items"][1]["status"], "queued")

    async def test_list_runnable_batches_queries_waiting_and_processing_only(self):
        database = PostgresStore("unused", "/tmp/results")
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                {"manifest": json.dumps({"id": "b1", "status": "waiting", "items": []})},
            ]
        )
        database.pool = pool
        store = PostgresBatchStore(database, "/tmp/batches", "/tmp/results")
        batches = await store.list_runnable_batches()
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["id"], "b1")
        query = pool.fetch.call_args[0][0]
        self.assertIn("status IN ('waiting', 'processing')", query)

    async def test_list_groups_with_status(self):
        database = PostgresStore("unused", "/tmp/results")
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"total_groups": 10, "total_images": 50})
        pool.fetch = AsyncMock(return_value=[])
        database.pool = pool

        res = await database.list_groups(limit=25, offset=0, status="translated")
        self.assertEqual(res["totalGroups"], 10)
        self.assertEqual(res["totalImages"], 50)
        # Verify effective_status parameter is passed as $3 in fetchrow and $5 in fetch
        fetchrow_args = pool.fetchrow.call_args[0]
        self.assertEqual(fetchrow_args[3], "translated")
        fetch_args = pool.fetch.call_args[0]
        self.assertEqual(fetch_args[5], "translated")
        self.assertIn("tp.source_type = 'translated'", fetch_args[0])

    async def test_resolve_group_id_with_manga_hash(self):
        database = PostgresStore("unused", "/tmp/results")
        pool = AsyncMock()
        title = "Sakura Garden"
        hashed_id = _manga_id(title)
        # First 2 fetchval calls (by id, by title) return None
        pool.fetchval = AsyncMock(return_value=None)
        pool.fetch = AsyncMock(return_value=[{"id": "uuid-1234", "title": title}])
        database.pool = pool

        resolved = await database.resolve_group_id(hashed_id)
        self.assertEqual(resolved, "uuid-1234")

    async def test_list_batch_summaries_includes_manga_group_id(self):
        database = PostgresStore("unused", "/tmp/results")
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{
            "id": "batch-1",
            "title": "Sakura Garden",
            "manga_group_id": "uuid-1234",
            "kind": "translation",
            "status": "completed",
            "dismissed": False,
            "added_at": 1000,
            "updated_at": 2000,
            "settings": "{}",
            "priority": False,
            "total_items": 32,
            "completed_count": 32,
            "queued_count": 0,
            "processing_count": 0,
            "failed_count": 0,
            "needs_review_count": 0,
        }])
        database.pool = pool
        store = PostgresBatchStore(database, "/tmp/batches", "/tmp/results")
        summaries = await store.list_batch_summaries()
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["mangaGroupId"], "uuid-1234")

    async def test_review_update_targets_only_the_matching_batch_item(self):
        manifest = {
            "id": "batch-1",
            "items": [
                {"id": "target", "resultFolder": "page-folder", "needsReview": True},
                {"id": "other", "resultFolder": "other-folder", "needsReview": True},
            ],
        }
        connection = SimpleNamespace(
            transaction=lambda: _AsyncContext(None),
            fetch=AsyncMock(return_value=[{
                "batch_id": "batch-1",
                "manifest": manifest,
                "item_id": "target",
            }]),
            execute=AsyncMock(),
        )
        database = PostgresStore("unused", "/tmp/results")
        database.pool = SimpleNamespace(acquire=lambda: _AsyncContext(connection))
        with tempfile.TemporaryDirectory() as root:
            store = PostgresBatchStore(database, Path(root) / "batches", "/tmp/results")
            updated = await store.update_review_for_result("page-folder", False)
            snapshot = json.loads((Path(root) / "batches" / "batch-1" / "manifest.json").read_text())

        self.assertEqual(updated, 1)
        self.assertFalse(snapshot["items"][0]["needsReview"])
        self.assertTrue(snapshot["items"][1]["needsReview"])
        self.assertEqual(connection.execute.await_count, 2)
        self.assertIn("UPDATE batch_items", connection.execute.await_args_list[0].args[0])


class PostgresStoreHelpersTest(unittest.TestCase):
    def test_natural_sort_key_keeps_page_order(self):
        self.assertLess(_natural_sort_key("page_2.png"), _natural_sort_key("page_10.png"))

    def test_parse_finished_at_normalizes_naive_iso_values(self):
        parsed = _parse_finished_at("2026-01-01T00:00:00", dt.datetime.now(dt.timezone.utc))
        self.assertEqual(parsed.tzinfo, dt.timezone.utc)

    def test_safe_folder_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            _safe_folder("../outside")


if __name__ == "__main__":
    unittest.main()
