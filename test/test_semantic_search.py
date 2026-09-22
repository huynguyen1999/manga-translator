import asyncio
import json
import os
import re
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from server.search_api import search_router
from server.search_embeddings import (
    COLLECTION, DIMENSIONS, chunk_summary, fingerprint, point_id, prepare_image,
    rank_results, validate_query, validate_vector,
)


class Tokenizer:
    def __call__(self, text, add_special_tokens=True, **kwargs):
        words = list(re.finditer(r"\S+", text))
        return {"input_ids": list(range(len(words) + (2 if add_special_tokens else 0))),
                "offset_mapping": [(word.start(), word.end()) for word in words]}

    def num_special_tokens_to_add(self, **kwargs):
        return 2


class SearchCoreTest(unittest.TestCase):
    def test_chunks_preserve_complete_summary_and_offsets(self):
        text = " ".join(f"word{i}" for i in range(1200))
        chunks = chunk_summary(text, Tokenizer())
        self.assertGreater(len(chunks), 2)
        self.assertEqual(set(text.split()), set(" ".join(chunk["text"] for chunk in chunks).split()))
        for chunk in chunks:
            self.assertEqual(text[chunk["start"]:chunk["end"]], chunk["text"])
            self.assertLessEqual(validate_query(chunk["text"], Tokenizer(), 512), 512)
        with self.assertRaisesRegex(ValueError, "Shorten"):
            validate_query("word " * 64, Tokenizer(), 64)

    def test_normalization_and_original_page_preprocessing(self):
        self.assertEqual(validate_vector([3, 4], 2), [0.6, 0.8])
        for vector in ([0, 0], [float("nan"), 1], [1]):
            with self.assertRaises(ValueError):
                validate_vector(vector, 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.png"
            Image.new("RGB", (20, 80), "red").save(path)
            image = prepare_image(path)
            self.assertEqual(image.size, (256, 256))
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(image.getpixel((128, 0)), (255, 0, 0))

    def test_fusion_is_manga_level_and_deterministic(self):
        summary = {"a": [{"score": 0.9}], "b": [{"score": 0.8}]}
        images = {"a": [{"score": 0.5}, {"score": 0.4}], "c": [{"score": 0.9}]}
        ranked = rank_results(summary, images, "combined")
        self.assertEqual(ranked[0][0], "a")
        self.assertAlmostEqual(ranked[0][1], 1 / 61 + 1 / 62)
        self.assertEqual(rank_results(summary, images, "image")[0][0], "c")
        self.assertEqual(point_id("a", "v", 0), point_id("a", "v", 0))
        self.assertNotEqual(point_id("a", "v", 0), point_id("a", "new", 0))

    def test_api_validation_and_unavailable_service(self):
        service = AsyncMock()
        service.query.return_value = {"results": []}
        service.db.manga.return_value = {"items": [], "total": 0}
        app = FastAPI()
        app.include_router(search_router(lambda: service), prefix="/api")
        client = TestClient(app)
        for body in ({"query": " "}, {"query": "x", "groupIds": []}, {"query": "x", "mode": "unknown"}, {"query": "x", "limit": 51}):
            self.assertEqual(client.post("/api/search/query", json=body).status_code, 422)
        self.assertEqual(client.post("/api/search/query", json={"query": " story "}).status_code, 200)
        service.query.assert_awaited_once_with("story", "combined", None, 20)

        self.assertEqual(client.get("/api/search/manga?status=invalid").status_code, 422)
        self.assertEqual(client.get("/api/search/manga?status=summarized").status_code, 200)
        service.db.manga.assert_awaited_with("", 0, 25, status="summarized")
        self.assertEqual(client.get("/api/search/manga?summarized=true").status_code, 200)
        service.db.manga.assert_awaited_with("", 0, 25, status="summarized")
        self.assertEqual(client.get("/api/search/manga?summarized=false").status_code, 200)
        service.db.manga.assert_awaited_with("", 0, 25, status="not-summarized")
        self.assertEqual(client.get("/api/search/manga?search=manga&status=all&offset=10&limit=5").status_code, 200)
        service.db.manga.assert_awaited_with("manga", 10, 5, status="all")

        unavailable = FastAPI()
        unavailable.include_router(search_router(lambda: None))
        self.assertEqual(TestClient(unavailable).get("/search/status").status_code, 503)


class FakeEncoders:
    device = "test"

    def __init__(self):
        self.calls = 0

    def chunks(self, text):
        return chunk_summary(text, Tokenizer(), max_tokens=10, overlap=2)

    def encode(self, modality, values, query=False):
        self.calls += 1
        return [[1.0] + [0.0] * (DIMENSIONS[modality] - 1) for _ in values]

    def unload(self):
        pass


@unittest.skipUnless(os.getenv("RUN_SEARCH_INTEGRATION") == "1", "Set RUN_SEARCH_INTEGRATION=1 for isolated PostgreSQL/Qdrant integration checks")
class SearchIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        from qdrant_client import AsyncQdrantClient
        from server.constants import TEST_DATABASE_URL
        from server.postgres_store import PostgresStore
        from server.search_service import SearchService

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.schema = "search_test_" + uuid.uuid4().hex
        self.admin = await asyncpg.connect(TEST_DATABASE_URL)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=3, server_settings={"search_path": self.schema})
        self.store = PostgresStore(TEST_DATABASE_URL, self.root)
        self.store.pool = pool
        await pool.execute("""
            CREATE TABLE manga_groups(id text PRIMARY KEY,title text);
            CREATE TABLE pages(id text PRIMARY KEY,folder text,manga_group_id text,active boolean DEFAULT true,
              original_name text,page_order int,metadata jsonb DEFAULT '{}',text_regions jsonb DEFAULT '[]');
            CREATE TABLE manga_summaries(id text PRIMARY KEY,group_id text UNIQUE,payload jsonb,updated_at timestamptz DEFAULT now());
        """)
        await pool.execute((Path(__file__).parents[1] / "server/migrations/012_semantic_search.sql").read_text())
        self.encoder = FakeEncoders()
        self.collection = "search_test_" + uuid.uuid4().hex
        self.collection_patch = patch("server.search_service.COLLECTION", self.collection)
        self.collection_patch.start()
        client = AsyncQdrantClient(url=os.environ["SEARCH_TEST_QDRANT_URL"]) if os.getenv("SEARCH_TEST_QDRANT_URL") else AsyncQdrantClient(":memory:")
        self.service = SearchService(self.store, client=client, encoders=self.encoder)
        # Do not import translation models for synthetic-vector integration tests.
        async def infer(operation, *args, **kwargs):
            kwargs.pop("background", None)
            return operation(*args, **kwargs)
        self.service.infer = infer
        await self.service.start()
        for group_id in ("a", "b"):
            await pool.execute("INSERT INTO manga_groups VALUES($1,$2)", group_id, "Manga " + group_id)
            for number in (1, 2):
                page_id = group_id + str(number)
                folder = self.root / page_id
                folder.mkdir()
                Image.new("RGB", (20, 40), "red").save(folder / "input.png")
                await pool.execute("INSERT INTO pages(id,folder,manga_group_id,original_name,page_order) VALUES($1,$1,$2,$1,$3)", page_id, group_id, number)
            snapshot = await self.store.summary_status(group_id)
            await self.store.save_summary_payload(group_id, {"summary": "A traveler makes an unexpected friend. " * 6,
                "sourceFingerprint": snapshot["sourceFingerprint"]})

    async def asyncTearDown(self):
        if await self.service.client.collection_exists(self.collection):
            await self.service.client.delete_collection(self.collection)
        await self.service.close()
        self.service.encoders.unload()
        self.collection_patch.stop()
        await self.store.pool.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()
        self.temporary.cleanup()

    async def embed(self, ids=("a", "b")):
        job = await self.service.submit(list(ids))
        await self.service.task
        job = (await self.service.db.jobs(job["id"]))[0]
        self.assertIsNone(job["error"], job)
        return job

    async def test_index_search_reuse_replace_and_delete(self):
        job = await self.embed()
        self.assertEqual(job["completed"], 6)
        calls = self.encoder.calls
        again = await self.embed()
        self.assertEqual(again["unchanged"], 6)
        self.assertEqual(self.encoder.calls, calls)
        response = await self.service.query("a traveler", "combined")
        self.assertEqual(len(response["results"]), 2)
        self.assertEqual(response["results"][0]["rank"], 1)
        self.assertEqual(len(response["results"][0]["pages"]), 2)
        self.assertGreater(response["results"][0]["combinedScore"], 0)
        selected = await self.service.query("a traveler", "image", ["b"])
        self.assertEqual([result["groupId"] for result in selected["results"]], ["b"])
        with self.assertRaisesRegex(ValueError, "no longer exist"):
            await self.service.query("story", "combined", ["missing"])
        old = await self.service.db.source("summary:a")
        saved = await self.store.get_summary_payload("a")
        saved["summary"] = "A new story."
        await self.store.save_summary_payload("a", saved)
        stale = await self.service.query("a traveler", "summary", ["a"])
        self.assertTrue(stale["results"][0]["coverage"]["outdated"])
        self.assertIn("traveler", stale["results"][0]["excerpt"])
        await self.embed(["a"])
        self.assertFalse(await self.service.client.retrieve(self.collection, old["point_ids"]))
        await self.store.pool.execute("UPDATE pages SET active=false WHERE id='a1'")
        after_delete = await self.service.query("story", "image", ["a"])
        self.assertEqual([page["pageId"] for page in after_delete["results"][0]["pages"]], ["a2"])
        await self.store.pool.execute("DELETE FROM manga_groups WHERE id='a'")
        deleted = await self.service.query("story", "combined")
        self.assertEqual([result["groupId"] for result in deleted["results"]], ["b"])

    async def test_missing_original_cancel_resume_and_restart(self):
        (self.root / "a1/input.png").unlink()
        Image.new("RGB", (20, 40), "blue").save(self.root / "a1/final.png")
        job = await self.embed(["a"])
        self.assertEqual(job["skipped"], 1)
        self.assertIsNone(await self.service.db.source("image:a1"))
        self.assertEqual((await self.service.db.manga(group_ids=["a"]))["items"][0]["originalCount"], 1)
        Image.new("RGB", (20, 40), "red").save(self.root / "a1/input.png")
        await self.service.submit(resume_id=job["id"])
        await self.service.task
        self.assertIsNotNone(await self.service.db.source("image:a1"))
        cancelled_id = await self.service.db.create_job(["b"])
        await self.service.cancel(cancelled_id)
        await self.service.run_job(cancelled_id)
        self.assertEqual((await self.service.db.jobs(cancelled_id))[0]["status"], "cancelled")
        await self.service.submit(resume_id=cancelled_id)
        await self.service.task
        self.assertEqual((await self.service.db.jobs(cancelled_id))[0]["status"], "complete")
        interrupted_id = await self.service.db.create_job(["a"])
        await self.service.start()
        self.assertEqual((await self.service.db.jobs(interrupted_id))[0]["status"], "interrupted")

    async def test_uncommitted_write_is_not_visible_and_resume_repairs_it(self):
        await self.embed(["a"])
        saved = await self.store.get_summary_payload("a")
        saved["summary"] = "Replacement story."
        await self.store.save_summary_payload("a", saved)
        original_save = self.service.db.save_source
        self.service.db.save_source = AsyncMock(side_effect=RuntimeError("simulated database failure"))
        job = await self.service.submit(["a"])
        await self.service.task
        self.assertEqual((await self.service.db.jobs(job["id"]))[0]["status"], "error")
        self.service.db.save_source = original_save
        result = await self.service.query("traveler", "summary", ["a"])
        self.assertIn("traveler", result["results"][0]["excerpt"])
        await self.service.submit(resume_id=job["id"])
        await self.service.task
        result = await self.service.query("story", "summary", ["a"])
        self.assertEqual(result["results"][0]["excerpt"], "Replacement story.")

    async def test_cancellation_between_summary_batches_keeps_unfinished_source_hidden(self):
        original_upsert = self.service.client.upsert
        async def stop_after_batch(*args, **kwargs):
            result = await original_upsert(*args, **kwargs)
            job_id = await self.store.pool.fetchval("SELECT id FROM search_jobs WHERE status='running'")
            await self.service.cancel(job_id)
            return result
        self.service.client.upsert = stop_after_batch
        job = await self.service.submit(["a"])
        await self.service.task
        self.assertEqual((await self.service.db.jobs(job["id"]))[0]["status"], "cancelled")
        self.assertIsNone(await self.service.db.source("summary:a"))
        result = await self.service.query("story", "summary", ["a"])
        self.assertEqual(result["results"], [])
        self.service.client.upsert = original_upsert
        await self.service.submit(resume_id=job["id"])
        await self.service.task
        self.assertEqual((await self.service.db.jobs(job["id"]))[0]["status"], "complete")

    async def test_manga_summary_status_filter(self):
        # 'a' and 'b' have summaries from setUp; add 'c' without summary
        await self.store.pool.execute("INSERT INTO manga_groups VALUES('c', 'Manga c')")
        folder = self.root / "c1"
        folder.mkdir()
        Image.new("RGB", (20, 40), "red").save(folder / "input.png")
        await self.store.pool.execute("INSERT INTO pages(id,folder,manga_group_id,original_name,page_order) VALUES('c1','c1','c','c1',1)")

        all_manga = await self.service.db.manga(status="all")
        self.assertEqual(all_manga["total"], 3)
        self.assertEqual([item["id"] for item in all_manga["items"]], ["a", "b", "c"])

        summarized = await self.service.db.manga(status="summarized")
        self.assertEqual(summarized["total"], 2)
        self.assertEqual([item["id"] for item in summarized["items"]], ["a", "b"])

        not_summarized = await self.service.db.manga(status="not-summarized")
        self.assertEqual(not_summarized["total"], 1)
        self.assertEqual([item["id"] for item in not_summarized["items"]], ["c"])

    @unittest.skipUnless(os.getenv("RUN_SEARCH_MODELS") == "1", "Enable pinned real-model inference explicitly")
    async def test_real_models_index_and_query(self):
        from server.search_embeddings import SearchEncoders
        self.service.encoders = SearchEncoders()
        job = await self.embed(["a"])
        self.assertEqual(job["completed"], 3)
        for mode in ("summary", "image", "combined"):
            result = await self.service.query("A traveler makes a friend", mode, ["a"])
            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(result["results"][0]["groupId"], "a")
            print(f"real model {mode}: {result['elapsedMs']} ms, device={self.service.encoders.device}")
        with self.assertRaisesRegex(ValueError, "Shorten"):
            await self.service.query("traveler " * 80, "image")


if __name__ == "__main__":
    unittest.main()
