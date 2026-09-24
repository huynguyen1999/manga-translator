import os
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from server.postgres_store import PostgresBatchStore, PostgresStore


class PostgresIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_page_deletes_in_one_group_do_not_deadlock(self):
        database_url = os.getenv("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
        with tempfile.TemporaryDirectory() as root:
            result_root = Path(root) / "results"
            result_root.mkdir()
            store = PostgresStore(database_url, result_root)
            await store.start(check_schema=False)
            folders = []
            try:
                await store.apply_migrations()
                prefix = Path(root).name
                title = f"Concurrent delete {prefix}"
                for index in range(4):
                    folder = result_root / f"{prefix}-concurrent-delete-{index}"
                    folder.mkdir()
                    Image.new("RGB", (2, 2), "white").save(folder / "final.png")
                    (folder / "meta.json").write_text(json.dumps({"mangaTitle": title}))
                    await store.sync_result_folder(folder, generate_variants=False)
                    folders.append(folder.name)

                self.assertEqual(
                    await asyncio.gather(*(store.delete_result(folder) for folder in folders)),
                    [True] * len(folders),
                )
                self.assertEqual(
                    await store.pool.fetchval(
                        "SELECT count(*) FROM pages WHERE folder=ANY($1::text[])", folders
                    ),
                    0,
                )
            finally:
                if store.pool is not None and folders:
                    await store.pool.execute("DELETE FROM pages WHERE folder=ANY($1::text[])", folders)
                    await store.pool.execute("DELETE FROM manga_groups WHERE title=$1", title)
                await store.close()

    async def test_sync_result_folder_migrates_manifest_stage_state_and_documents(self):
        database_url = os.getenv("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
        with tempfile.TemporaryDirectory() as root:
            store = PostgresStore(database_url, Path(root) / "results")
            await store.start(check_schema=False)
            folder = None
            try:
                await store.apply_migrations()
                folder = store.result_root / Path(root).name
                folder.mkdir(parents=True)
                Image.new("RGB", (2, 2), "white").save(folder / "final.png")
                manifest = {
                    "createdAt": "2026-09-22T10:00:00Z",
                    "config": {"font_size": 32},
                    "stages": [
                        {"id": "textline_merge", "status": "completed",
                         "startedAt": "2026-09-22T10:00:01Z",
                         "finishedAt": "2026-09-22T10:00:02Z", "durationMs": 1000},
                        {"id": "layout", "status": "failed",
                         "startedAt": "2026-09-22T10:00:03Z",
                         "finishedAt": "2026-09-22T10:00:04Z", "reason": "layout failed"},
                        {"id": "rendering", "status": "completed",
                         "startedAt": "2026-09-22T10:00:05Z",
                         "finishedAt": "2026-09-22T10:00:06Z", "artifacts": ["final.png"]},
                    ],
                }
                await store.save_documents(folder.name, {
                    "pipeline_manifest.json": manifest,
                    "ocr.json": [{"text": "日本語"}],
                })

                await store.sync_result_folder(folder, generate_variants=False)

                states = {row["stage"]: row for row in await store.get_pipeline_stage_state(folder.name)}
                self.assertEqual(states["text_grouping"]["status"], "completed")
                self.assertEqual(states["layout"]["status"], "failed")
                attempts = await store.pool.fetch(
                    "SELECT stage,status,error_message FROM page_stage_attempts WHERE page_id=(SELECT id FROM pages WHERE folder=$1)",
                    folder.name,
                )
                self.assertEqual({row["stage"] for row in attempts}, {"text_grouping", "layout", "rendering"})
                structured = await store.get_pipeline_document(folder.name, "ocr", "regions")
                self.assertEqual(structured["payload"], [{"text": "日本語"}])
                await store.save_documents(folder.name, {"ocr.json": [{"text": "updated"}]})
                structured = await store.get_pipeline_document(folder.name, "ocr", "regions")
                self.assertEqual(structured["revision"], 2)
                self.assertEqual(structured["payload"], [{"text": "updated"}])
                artifact = await store.get_pipeline_artifact(folder.name, "rendering", "final_png")
                self.assertEqual(artifact["relative_path"], f"{folder.name}/final.png")
                self.assertGreater(artifact["size_bytes"], 0)
                self.assertFalse((folder / "ocr.json").exists())

                updated_manifest = {
                    **manifest,
                    "stages": [
                        manifest["stages"][0],
                        {"id": "layout", "status": "completed",
                         "startedAt": "2026-09-22T10:01:03Z",
                         "finishedAt": "2026-09-22T10:01:04Z", "durationMs": 1000},
                        manifest["stages"][2],
                    ],
                }
                await store.save_documents(folder.name, {"pipeline_manifest.json": updated_manifest})
                await store.sync_result_folder(folder, generate_variants=False)
                states = {row["stage"]: row for row in await store.get_pipeline_stage_state(folder.name)}
                self.assertEqual(states["layout"]["status"], "completed")
                self.assertEqual(states["layout"]["attempt"], 2)
            finally:
                if folder is not None and store.pool is not None:
                    await store.pool.execute("DELETE FROM pages WHERE folder=$1", folder.name)
                await store.close()

    async def test_running_pipeline_stage_is_interrupted_after_restart(self):
        database_url = os.getenv("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
        with tempfile.TemporaryDirectory() as root:
            store = PostgresStore(database_url, Path(root) / "results")
            await store.start(check_schema=False)
            folder = store.result_root / f"crash-recovery-{Path(root).name}"
            try:
                await store.apply_migrations()
                folder.mkdir(parents=True)
                Image.new("RGB", (2, 2), "white").save(folder / "final.png")
                await store.sync_result_folder(folder, generate_variants=False)

                first_attempt = await store.start_pipeline_stage(folder.name, "detection")
                self.assertEqual(first_attempt, 1)
                self.assertGreaterEqual(await store.interrupt_running_pipeline_stages(), 1)
                state = await store.get_pipeline_stage_state(folder.name)
                self.assertEqual(state[0]["status"], "interrupted")
                self.assertEqual(
                    await store.start_pipeline_stage(folder.name, "detection"), 2
                )
            finally:
                if store.pool is not None:
                    await store.pool.execute("DELETE FROM pages WHERE folder=$1", folder.name)
                await store.close()

    async def test_id_relationship_migration_is_canonical(self):
        database_url = os.getenv("TEST_DATABASE_URL")
        if not database_url:
            self.skipTest("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
        store = PostgresStore(database_url, Path(tempfile.mkdtemp()) / "results")
        await store.start(check_schema=False)
        try:
            self.assertEqual(await store.apply_migrations(), "015_batch_item_stage_state")
            stage_constraint = await store.pool.fetchval(
                """SELECT pg_get_constraintdef(oid) FROM pg_constraint
                   WHERE conname='page_stage_state_stage_check'"""
            )
            self.assertIn("colorization", stage_constraint)
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
                    "batch_items",
                    "reading_progress",
                    "page_stage_state",
                    "page_stage_attempts",
                    "pipeline_documents",
                    "pipeline_artifacts",
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
                ("batch_items", "stage_started_at"),
                ("reading_progress", "group_id"),
                ("page_stage_state", "settings_fingerprint"),
                ("page_stage_attempts", "metrics"),
                ("pipeline_documents", "schema_version"),
                ("pipeline_artifacts", "checksum"),
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
                    folder = store.result_root / f"{title}-{name}"
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
                    batch["items"][1].update(status="completed", resultFolder=f"{title}-second")
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
            prefix = Path(root).name
            folder = result_root / f"{prefix}_page_2"
            manga_title = f"Series {prefix}"
            folder.mkdir(parents=True)
            Image.new("RGB", (1, 1), "white").save(folder / "final.png")
            (folder / "input.png").write_bytes(b"input")
            (folder / "meta.json").write_text(
                json.dumps({"id": "page-2", "originalName": "page_2.png", "mangaTitle": manga_title, "finishedAt": "2026-01-01T00:00:00Z"}),
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
                groups = await store.list_groups(search=manga_title)
                self.assertEqual(groups["totalImages"], 1)
                group_id = groups["groups"][0]["id"]
                self.assertTrue(group_id)
                self.assertEqual(len(await store.group_pages(group_id)), 1)
                page_list = await store.list_results(manga=manga_title, limit=1)
                page = page_list["items"][0]
                self.assertTrue(page["id"])
                self.assertNotEqual(page["id"], page["folder"])
                self.assertEqual(page["legacyId"], "page-2")
                self.assertEqual(page["folder"], folder.name)
                self.assertTrue(page["hasTextRegions"])
                self.assertEqual(
                    page["textRegionsUrl"],
                    f"/result/{page['id']}/text_regions.json",
                )
                self.assertEqual((await store.resolve_folder(page["id"])), folder.name)
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
                new_folder = result_root / f"{prefix}_page_3"
                new_folder.mkdir()
                Image.new("RGB", (1, 1), "white").save(new_folder / "final.png")
                (new_folder / "meta.json").write_text(
                    '{"originalName":"page_3.png","mangaTitle":"Series"}',
                    encoding="utf-8",
                )
                self.assertEqual(await store.index_untracked_results(), {"indexed": 1, "skipped": 0})
                self.assertIsNotNone(await store.resolve_folder(new_folder.name))
                await store.delete_result(new_folder.name)
                self.assertIsNone(page_list["nextOffset"])
                saved = await store.save_progress({
                    "installationId": "integration-test",
                    "mangaTitle": manga_title,
                    "page": 2,
                    "scrollTop": 120,
                    "updatedAt": "2026-01-02T00:00:00Z",
                })
                self.assertTrue(saved["id"])
                self.assertEqual(saved["page"], 2)
                stale = await store.save_progress({
                    "installationId": "integration-test",
                    "mangaTitle": manga_title,
                    "page": 1,
                    "scrollTop": 0,
                    "updatedAt": "2026-01-01T00:00:00Z",
                })
                self.assertEqual(stale["page"], 2)
                await store.pool.execute(
                    "DELETE FROM reading_progress WHERE installation_id=$1",
                    f"integration-test-{prefix}",
                )
                await store.delete_result(folder.name)
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
            series_title = f"Reader navigation {prefix}"
            titles = [f"{prefix} 10", f"{prefix} 2", f"{prefix} 1", f"{prefix} standalone"]
            renamed_title = titles[1]
            store = PostgresStore(database_url, result_root)
            await store.start(check_schema=False)
            try:
                await store.apply_migrations()
                for index, title in enumerate(titles):
                    folder = result_root / f"{prefix}_page_{index}"
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
                series = await store.create_series(series_title, [
                    group_ids[titles[0]], group_ids[titles[1]], group_ids[titles[2]],
                ])
                self.assertEqual(
                    [member["title"] for member in series["members"]],
                    [titles[2], titles[1], titles[0]],
                )
                listed = await store.list_series(search=series_title)
                self.assertEqual(listed["series"][0]["id"], series["id"])
                self.assertIsNone(await store.get_series_for_group(group_ids[titles[3]]))
                page = (await store.list_results(manga=group_ids[titles[1]], detail="reader"))["items"][0]
                self.assertEqual(page["seriesId"], series["id"])
                self.assertEqual(page["seriesTitle"], series_title)

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
