import os
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from server.postgres_store import PostgresBatchStore, PostgresStore


class PostgresIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_id_relationship_migration_is_canonical(self):
        database_url = os.getenv("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
        store = PostgresStore(database_url, Path(tempfile.mkdtemp()) / "results")
        await store.start(check_schema=False)
        try:
            self.assertEqual(await store.apply_migrations(), "012_semantic_search")
            columns = await store.pool.fetch(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=ANY($1::text[])
                """,
                [
                    "pages",
                    "page_artifacts",
                    "result_documents",
                    "manga_summaries",
                    "batch_items",
                    "reading_progress",
                ],
            )
            present = {(row["table_name"], row["column_name"]) for row in columns}
            for table, column in [
                ("pages", "manga_group_id"),
                ("page_artifacts", "page_id"),
                ("result_documents", "page_id"),
                ("result_documents", "pipeline_run_id"),
                ("manga_summaries", "group_id"),
                ("batch_items", "manga_group_id"),
                ("batch_items", "page_id"),
                ("reading_progress", "group_id"),
            ]:
                self.assertIn((table, column), present)
            for table, column in [
                ("pages", "manga_title"),
                ("page_artifacts", "folder"),
                ("result_documents", "folder"),
                ("manga_summaries", "title"),
                ("batch_items", "manga_title"),
                ("batch_items", "result_folder"),
                ("reading_progress", "manga_title"),
            ]:
                self.assertNotIn((table, column), present)
            constraints = {
                row["conname"]
                for row in await store.pool.fetch(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE conname=ANY($1::text[])
                    """,
                    [
                        "pages_manga_group_id_fkey",
                        "page_artifacts_page_id_fkey",
                        "result_documents_owner_check",
                        "manga_summaries_group_id_fkey",
                        "reading_progress_group_id_fkey",
                        "reading_progress_page_id_fkey",
                    ],
                )
            }
            self.assertEqual(
                constraints,
                {
                    "pages_manga_group_id_fkey",
                    "page_artifacts_page_id_fkey",
                    "result_documents_owner_check",
                    "manga_summaries_group_id_fkey",
                    "reading_progress_group_id_fkey",
                    "reading_progress_page_id_fkey",
                },
            )
        finally:
            await store.close()

    async def test_page_reservations_survive_progress_and_out_of_order_results(self):
        database_url = os.getenv("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
        with tempfile.TemporaryDirectory() as root:
            store = PostgresStore(database_url, Path(root) / "results")
            await store.start(check_schema=False)
            group_id = None
            batches = []
            try:
                await store.apply_migrations()
                title = Path(root).name
                group_id = await store.resolve_group_id(title, create=True)
                batch_store = PostgresBatchStore(store, Path(root) / "batches", store.result_root)
                for index in range(2):
                    batch = {
                        "id": f"{title}-{index}", "title": title, "mangaTitle": title,
                        "status": "processing", "items": [
                            {"id": f"item-{n}", "name": f"{n}.png", "status": "processing",
                             "mangaGroupId": group_id, "pageOrder": 99}
                            for n in range(2)
                        ],
                    }
                    batches.append(batch)
                    await batch_store._save_db_manifest(batch)
                self.assertEqual(
                    [item["pageOrder"] for batch in batches for item in batch["items"]],
                    [1, 2, 3, 4],
                )

                async def save_page(name, order):
                    folder = store.result_root / name
                    folder.mkdir(parents=True)
                    Image.new("RGB", (2, 2), "white").save(folder / "final.png")
                    (folder / "meta.json").write_text(json.dumps({
                        "mangaTitle": title, "mangaGroupId": group_id,
                        "originalName": name + ".png", "pageOrder": order,
                    }))
                    return await store.sync_result_folder(folder, generate_variants=False)

                # The worker retains order 2 while progress and another batch are saved.
                await batch_store.mutate(batches[0]["id"], lambda batch: batch.update(status="processing"))
                self.assertEqual((await batch_store.get_batch(batches[0]["id"]))["items"][1]["pageOrder"], 2)
                await save_page("second", 2)
                def finish_second(batch):
                    batch["items"][1].update(status="completed", resultFolder="second")
                await batch_store.mutate(batches[0]["id"], finish_second)
                self.assertEqual((await batch_store.get_batch(batches[0]["id"]))["items"][0]["pageOrder"], 1)
                await save_page("first", 1)
                # Old jobs can still arrive with the same stale position concurrently.
                recovered = await asyncio.gather(save_page("stale-a", 2), save_page("stale-b", 2))
                self.assertEqual(sorted(page["page_order"] for page in recovered), [5, 6])
                rows = await store.pool.fetch(
                    "SELECT folder, page_order, metadata FROM pages WHERE manga_group_id=$1", group_id,
                )
                self.assertEqual(len(rows), 4)
                self.assertEqual(len({row["page_order"] for row in rows}), 4)
                for row in rows:
                    self.assertEqual(json.loads(row["metadata"])["pageOrder"], row["page_order"])
            finally:
                for batch in batches:
                    await store.pool.execute("DELETE FROM batches WHERE id=$1", batch["id"])
                if group_id:
                    await store.delete_group(group_id)
                await store.close()

    async def test_query_indexes_cover_unfiltered_lists(self):
        database_url = os.getenv("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
        store = PostgresStore(database_url, Path(tempfile.mkdtemp()) / "results")
        await store.start(check_schema=False)
        try:
            await store.apply_migrations()
            indexes = await store.pool.fetch(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname='public' AND indexname = ANY($1::text[])
                """,
                [
                    "pages_active_original_sort_idx",
                    "pages_active_finished_idx",
                    "batches_active_added_idx",
                ],
            )
            self.assertEqual(
                {row["indexname"] for row in indexes},
                {
                    "pages_active_original_sort_idx",
                    "pages_active_finished_idx",
                    "batches_active_added_idx",
                },
            )
        finally:
            await store.close()

    async def test_result_index_and_paginated_queries(self):
        database_url = os.getenv("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
        with tempfile.TemporaryDirectory() as root:
            result_root = Path(root) / "results"
            folder = result_root / "page_2"
            folder.mkdir(parents=True)
            Image.new("RGB", (1, 1), "white").save(folder / "final.png")
            (folder / "input.png").write_bytes(b"input")
            (folder / "meta.json").write_text(
                '{"id":"page-2","originalName":"page_2.png","mangaTitle":"Series","finishedAt":"2026-01-01T00:00:00Z"}',
                encoding="utf-8",
            )
            regions = [{"id": "bubble-1", "original_text": "Hello", "translation": "你好"}]
            (folder / "text_regions.json").write_text(
                json.dumps(regions),
                encoding="utf-8",
            )
            detection = [{"pts": [[0, 0], [1, 0], [1, 1], [0, 1]]}]
            (folder / "detection.json").write_text(json.dumps(detection), encoding="utf-8")
            store = PostgresStore(database_url, result_root)
            await store.start(check_schema=False)
            try:
                await store.apply_migrations()
                await store.sync_result_folder(folder)
                groups = await store.list_groups()
                self.assertEqual(groups["totalImages"], 1)
                group_id = groups["groups"][0]["id"]
                self.assertTrue(group_id)
                self.assertEqual(len(await store.group_pages(group_id)), 1)
                page_list = await store.list_results(manga="Series", limit=1)
                page = page_list["items"][0]
                self.assertTrue(page["id"])
                self.assertNotEqual(page["id"], page["folder"])
                self.assertEqual(page["legacyId"], "page-2")
                self.assertEqual(page["folder"], "page_2")
                self.assertTrue(page["hasTextRegions"])
                self.assertEqual(
                    page["textRegionsUrl"],
                    f"/result/{page['id']}/text_regions.json",
                )
                self.assertEqual((await store.resolve_folder(page["id"])), "page_2")
                self.assertEqual((await store.page_detail(page["id"]))["id"], page["id"])
                self.assertEqual(await store.get_text_regions(page["id"]), regions)
                self.assertEqual(await store.get_document(page["id"], "detection.json"), detection)
                self.assertFalse((folder / "meta.json").exists())
                self.assertFalse((folder / "text_regions.json").exists())
                self.assertFalse((folder / "detection.json").exists())
                (folder / "text_regions.json").write_text(
                    json.dumps([{"id": "stale", "original_text": "stale"}]),
                    encoding="utf-8",
                )
                await store.sync_result_folder(folder)
                self.assertEqual(await store.get_text_regions(page["id"]), regions)
                self.assertFalse((folder / "text_regions.json").exists())
                new_folder = result_root / "page_3"
                new_folder.mkdir()
                Image.new("RGB", (1, 1), "white").save(new_folder / "final.png")
                (new_folder / "meta.json").write_text(
                    '{"originalName":"page_3.png","mangaTitle":"Series"}',
                    encoding="utf-8",
                )
                self.assertEqual(await store.index_untracked_results(), {"indexed": 1, "skipped": 0})
                self.assertIsNotNone(await store.resolve_folder("page_3"))
                await store.delete_result("page_3")
                self.assertIsNone(page_list["nextOffset"])
                saved = await store.save_progress({
                    "installationId": "integration-test",
                    "mangaTitle": "Series",
                    "page": 2,
                    "scrollTop": 120,
                    "updatedAt": "2026-01-02T00:00:00Z",
                })
                self.assertTrue(saved["id"])
                self.assertEqual(saved["page"], 2)
                stale = await store.save_progress({
                    "installationId": "integration-test",
                    "mangaTitle": "Series",
                    "page": 1,
                    "scrollTop": 0,
                    "updatedAt": "2026-01-01T00:00:00Z",
                })
                self.assertEqual(stale["page"], 2)
                await store.pool.execute(
                    "DELETE FROM reading_progress WHERE installation_id=$1",
                    "integration-test",
                )
                await store.delete_result("page_2")
            finally:
                await store.close()

    async def test_series_membership_navigation_and_unlinking(self):
        database_url = os.getenv("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
        with tempfile.TemporaryDirectory() as root:
            result_root = Path(root) / "results"
            result_root.mkdir()
            prefix = Path(root).name
            titles = [f"{prefix} 10", f"{prefix} 2", f"{prefix} 1", f"{prefix} standalone"]
            renamed_title = titles[1]
            store = PostgresStore(database_url, result_root)
            await store.start(check_schema=False)
            try:
                await store.apply_migrations()
                for index, title in enumerate(titles):
                    folder = result_root / f"page_{index}"
                    folder.mkdir()
                    Image.new("RGB", (1, 1), "white").save(folder / "final.png")
                    (folder / "input.png").write_bytes(b"input")
                    (folder / "meta.json").write_text(
                        json.dumps({"originalName": "page.png", "mangaTitle": title}),
                        encoding="utf-8",
                    )
                    await store.sync_result_folder(folder)

                groups = await store.list_groups(limit=20, search=prefix)
                group_ids = {group["title"]: group["id"] for group in groups["groups"]}
                series = await store.create_series("Reader navigation", [
                    group_ids[titles[0]], group_ids[titles[1]], group_ids[titles[2]],
                ])
                self.assertEqual(
                    [member["title"] for member in series["members"]],
                    [titles[2], titles[1], titles[0]],
                )
                listed = await store.list_series(search="Reader navigation")
                self.assertEqual(listed["series"][0]["id"], series["id"])
                self.assertIsNone(await store.get_series_for_group(group_ids[titles[3]]))
                page = (await store.list_results(manga=group_ids[titles[1]], detail="reader"))["items"][0]
                self.assertEqual(page["seriesId"], series["id"])
                self.assertEqual(page["seriesTitle"], "Reader navigation")

                reordered = await store.replace_series_members(series["id"], [
                    group_ids[titles[0]], group_ids[titles[2]], group_ids[titles[1]],
                ])
                self.assertEqual(
                    [member["id"] for member in reordered["members"]],
                    [group_ids[titles[0]], group_ids[titles[2]], group_ids[titles[1]]],
                )
                renamed_title = f"{prefix} renamed"
                await store.rename_group(group_ids[titles[1]], renamed_title)
                self.assertEqual((await store.get_series_for_group(group_ids[titles[1]]))["id"], series["id"])
                await store.delete_series(series["id"])
                self.assertIsNone(await store.get_series_for_group(group_ids[titles[1]]))
                self.assertEqual((await store.list_groups(limit=20, search=renamed_title))["totalGroups"], 1)
            finally:
                for title in [titles[0], renamed_title, titles[2], titles[3]]:
                    await store.delete_group(title)
                await store.close()


if __name__ == "__main__":
    unittest.main()
