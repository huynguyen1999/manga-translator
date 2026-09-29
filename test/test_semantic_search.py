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

from server.search_api import search_router
from server.search_embeddings import (
    COLLECTION, DIMENSIONS, PROFILE, chunk_summary, fingerprint, point_id,
    rank_results, validate_query, validate_vector,
)
from server.search_typesense import SearchTypesense


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

    def test_vector_normalization(self):
        self.assertEqual(validate_vector([3, 4], 2), [0.6, 0.8])
        for vector in ([0, 0], [float("nan"), 1], [1]):
            with self.assertRaises(ValueError):
                validate_vector(vector, 2)

    def test_ranking_and_point_ids(self):
        summary = {"a": [{"score": 0.9}], "b": [{"score": 0.8}], "c": [{"score": 0.7}]}
        ranked = rank_results(summary)
        self.assertEqual([item[0] for item in ranked], ["a", "b", "c"])
        self.assertAlmostEqual(ranked[0][1], 0.9)

        self.assertEqual(point_id("a", "v", 0), point_id("a", "v", 0))
        self.assertNotEqual(point_id("a", "v", 0), point_id("a", "new", 0))

    def test_api_validation_and_feature_gates(self):
        service = AsyncMock()
        service.query.return_value = {"results": [], "initialResults": []}
        service.db.manga.return_value = {"items": [], "total": 0}
        service.status.return_value = {"available": True}
        app = FastAPI()
        app.include_router(search_router(lambda: service), prefix="/api")
        client = TestClient(app)

        for body in (
            {"query": " "},
            {"query": "x", "groupIds": []},
            {"query": "x", "limit": 51},
            {"query": "x", "typoTolerance": 3},
            {"query": "x", "alpha": 1.5},
        ):
            self.assertEqual(client.post("/api/search/query", json=body).status_code, 422)

        self.assertEqual(client.post("/api/search/query", json={"query": " story "}).status_code, 200)
        service.query.assert_awaited_once_with(
            "story", group_ids=None, limit=20,
            typo_tolerance=2, alpha=0.7, hybrid_rerank=True,
        )

        service.query.reset_mock()
        self.assertEqual(client.post("/api/search/query", json={
            "query": "story", "limit": 5,
            "typoTolerance": 1, "alpha": 0.5, "hybridRerank": False,
        }).status_code, 200)
        service.query.assert_awaited_once_with(
            "story", group_ids=None, limit=5,
            typo_tolerance=1, alpha=0.5, hybrid_rerank=False,
        )

        self.assertEqual(client.get("/api/search/manga?status=invalid").status_code, 422)
        self.assertEqual(client.get("/api/search/manga?status=summarized").status_code, 200)
        service.db.manga.assert_awaited_with("", 0, 25, status="summarized", summary_status="all", index_status="all")
        self.assertEqual(client.get("/api/search/manga?summarized=true").status_code, 200)
        service.db.manga.assert_awaited_with("", 0, 25, status="summarized", summary_status="all", index_status="all")
        self.assertEqual(client.get("/api/search/manga?summarized=false").status_code, 200)
        service.db.manga.assert_awaited_with("", 0, 25, status="not-summarized", summary_status="all", index_status="all")
        self.assertEqual(client.get("/api/search/manga?summary_status=summarized&index_status=not-indexed").status_code, 200)
        service.db.manga.assert_awaited_with("", 0, 25, status="all", summary_status="summarized", index_status="not-indexed")
        self.assertEqual(client.get("/api/search/manga?search=manga&status=all&offset=10&limit=5").status_code, 200)
        service.db.manga.assert_awaited_with("manga", 10, 5, status="all", summary_status="all", index_status="all")

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


class FakeTypesense:
    """In-memory Typesense backend for fast, hermetic unit & integration testing."""

    def __init__(self, collection_name: str = COLLECTION):
        self.collection_name = collection_name
        self.documents: dict[str, dict] = {}
        self.healthy = True

    async def health(self) -> bool:
        return self.healthy

    async def ensure_collection(self, reset: bool = False):
        if reset:
            self.documents.clear()
        else:
            self.documents = {k: v for k, v in self.documents.items() if v.get("published", True)}

    async def upsert_chunks(self, documents: list[dict]):
        for doc in documents:
            self.documents[doc["id"]] = dict(doc)

    async def publish_chunks(self, ids: list[str], group_id: str):
        for doc_id in ids:
            if doc_id in self.documents:
                self.documents[doc_id]["published"] = True
                self.documents[doc_id]["groupId"] = group_id

    async def delete_documents(self, ids: list[str]):
        for doc_id in ids:
            self.documents.pop(doc_id, None)

    async def delete_source(self, source_key: str):
        self.documents = {k: v for k, v in self.documents.items() if v.get("sourceKey") != source_key}

    async def delete_source_except(self, source_key: str, keep_ids: list[str]):
        keep_set = set(keep_ids)
        self.documents = {
            k: v for k, v in self.documents.items()
            if v.get("sourceKey") != source_key or k in keep_set
        }

    async def delete_group(self, group_id: str):
        self.documents = {k: v for k, v in self.documents.items() if v.get("groupId") != group_id}

    async def retrieve_documents(self, ids: list[str]) -> list[dict]:
        return [dict(self.documents[doc_id]) for doc_id in ids if doc_id in self.documents]

    async def hybrid_search(
        self,
        query: str,
        query_vector: list[float],
        group_ids: list[str] | None = None,
        limit: int = 50,
        num_typos: int = 2,
        alpha: float = 0.7,
        hybrid_rerank: bool = True,
        profile: str = PROFILE,
    ) -> dict[str, list[dict]]:
        group_set = set(group_ids) if group_ids is not None else None
        matching_groups: dict[str, list[dict]] = {}

        for doc_id, doc in self.documents.items():
            if not doc.get("published", False) or doc.get("profile") != profile:
                continue
            group_id = doc.get("groupId", "")
            if group_set is not None and group_id not in group_set:
                continue

            content = doc.get("content", "")
            text_match = 100 if query and query.lower() in content.lower() else 0
            doc_vec = doc.get("embedding", [])
            # Cosine similarity for unit vectors
            sim = sum(a * b for a, b in zip(query_vector, doc_vec)) if len(query_vector) == len(doc_vec) else 0.5
            dist = max(0.0, 1.0 - sim)
            score = (alpha * sim) + ((1 - alpha) * (text_match / 100.0))

            hit = {
                **doc,
                "excerpt": content,
                "score": score,
                "vectorDistance": dist,
                "textMatch": text_match,
                "rankFusionScore": score,
                "highlight": {},
            }
            matching_groups.setdefault(group_id, []).append(hit)

        result: dict[str, list[dict]] = {}
        for gid, hits in matching_groups.items():
            hits.sort(key=lambda h: -h["score"])
            result[gid] = hits[:1]

        sorted_groups = sorted(result.items(), key=lambda item: (-item[1][0]["score"], item[0]))
        return dict(sorted_groups[:limit])

    async def close(self):
        pass


@unittest.skipUnless(os.getenv("RUN_SEARCH_INTEGRATION") == "1", "Set RUN_SEARCH_INTEGRATION=1 for isolated PostgreSQL/Typesense integration checks")
class SearchIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
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
              original_name text,page_order int,original_sort_key text DEFAULT '',source_type text DEFAULT 'translated',
              has_regions boolean DEFAULT false,metadata jsonb DEFAULT '{}',text_regions jsonb DEFAULT '[]');
            CREATE TABLE manga_summaries(id text PRIMARY KEY,group_id text UNIQUE,payload jsonb,updated_at timestamptz DEFAULT now());
        """)
        await pool.execute((Path(__file__).parents[1] / "server/migrations/012_semantic_search.sql").read_text())
        self.encoder = FakeEncoders()
        self.collection = "search_test_" + uuid.uuid4().hex
        self.collection_patch = patch("server.search_service.COLLECTION", self.collection)
        self.collection_patch.start()

        if os.getenv("SEARCH_TEST_TYPESENSE_URL"):
            ts_client = SearchTypesense(collection_name=self.collection)
        else:
            ts_client = FakeTypesense(collection_name=self.collection)

        self.service = SearchService(self.store, typesense_client=ts_client, encoders=self.encoder)

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
                await pool.execute("INSERT INTO pages(id,folder,manga_group_id,original_name,page_order) VALUES($1,$1,$2,$1,$3)", page_id, group_id, number)
            snapshot = await self.store.summary_status(group_id)
            await self.store.save_summary_payload(group_id, {
                "summary": "A traveler makes an unexpected friend in ruined city. " * 6,
                "sourceFingerprint": snapshot["sourceFingerprint"],
            })

    async def asyncTearDown(self):
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
        self.assertEqual(job["completed"], 2)
        calls = self.encoder.calls
        again = await self.embed()
        self.assertEqual(again["unchanged"], 2)
        self.assertEqual(self.encoder.calls, calls)

        # Query Typesense hybrid search
        resp = await self.service.query("a traveler")
        self.assertEqual(len(resp["results"]), 2)
        self.assertEqual(len(resp["initialResults"]), 2)
        self.assertEqual(resp["results"][0]["rank"], 1)
        self.assertEqual(resp["results"][0]["initialRank"], 1)
        self.assertEqual(resp["results"][0]["rankDelta"], 0)
        self.assertGreater(resp["results"][0]["summarySimilarity"], 0)

        # Group filtering
        selected = await self.service.query("a traveler", ["b"])
        self.assertEqual([result["groupId"] for result in selected["results"]], ["b"])

        with self.assertRaisesRegex(ValueError, "no longer exist"):
            await self.service.query("story", ["missing"])

        # Stale summary detection
        old = await self.service.db.source("summary:a")
        saved = await self.store.get_summary_payload("a")
        saved["summary"] = "A new story."
        await self.store.save_summary_payload("a", saved)
        stale = await self.service.query("a traveler", ["a"])
        self.assertTrue(stale["results"][0]["coverage"]["outdated"])
        self.assertIn("traveler", stale["results"][0]["excerpt"])

        # Re-embed replaces old documents
        await self.embed(["a"])
        docs = await self.service.typesense.retrieve_documents(old["point_ids"])
        self.assertFalse(docs)

        # Deleting manga group reconciles
        await self.store.pool.execute("DELETE FROM manga_groups WHERE id='a'")
        deleted = await self.service.query("story")
        self.assertEqual([result["groupId"] for result in deleted["results"]], ["b"])

    async def test_missing_summary_cancel_resume_and_restart(self):
        await self.store.pool.execute("DELETE FROM manga_summaries WHERE group_id='a'")
        job = await self.embed(["a"])
        self.assertEqual(job["skipped"], 1)
        self.assertIsNone(await self.service.db.source("summary:a"))
        snapshot = await self.store.summary_status("a")
        # Save a summary with mismatched sourceFingerprint (stale=True)
        await self.store.save_summary_payload("a", {"summary": "Outdated summary text.", "sourceFingerprint": "mismatched_stale_fingerprint"})
        stale_status = await self.store.summary_status("a")
        self.assertTrue(stale_status["stale"])
        await self.service.submit(resume_id=job["id"])
        await self.service.task
        self.assertIsNotNone(await self.service.db.source("summary:a"))
        res = await self.service.query("Outdated", ["a"])
        self.assertEqual(len(res["results"]), 1)
        self.assertIn("Outdated summary text", res["results"][0]["excerpt"])

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
        result = await self.service.query("traveler", ["a"])
        self.assertIn("traveler", result["results"][0]["excerpt"])
        await self.service.submit(resume_id=job["id"])
        await self.service.task
        result = await self.service.query("story", ["a"])
        self.assertEqual(result["results"][0]["excerpt"], "Replacement story.")

    async def test_manga_summary_status_filter(self):
        await self.store.pool.execute("INSERT INTO manga_groups VALUES('c', 'Manga c')")
        folder = self.root / "c1"
        folder.mkdir()
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

        # Index 'a' so 'a' is indexed while 'b' is summarized but not indexed
        await self.embed(["a"])
        ready_to_embed = await self.service.db.manga(summary_status="summarized", index_status="not-indexed")
        self.assertEqual(ready_to_embed["total"], 1)
        self.assertEqual([item["id"] for item in ready_to_embed["items"]], ["b"])

        indexed = await self.service.db.manga(index_status="indexed")
        self.assertEqual(indexed["total"], 1)
        self.assertEqual([item["id"] for item in indexed["items"]], ["a"])

        alias_ready = await self.service.db.manga(status="ready-to-embed")
        self.assertEqual(alias_ready["total"], 1)
        self.assertEqual([item["id"] for item in alias_ready["items"]], ["b"])

    @unittest.skipUnless(os.getenv("RUN_SEARCH_MODELS") == "1", "Enable pinned real-model inference explicitly")
    async def test_real_models_index_and_query(self):
        from server.search_embeddings import SearchEncoders
        self.service.encoders = SearchEncoders()
        job = await self.embed(["a"])
        self.assertEqual(job["completed"], 1)
        result = await self.service.query("A traveler makes a friend", ["a"])
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["groupId"], "a")
        self.assertGreater(result["results"][0]["summarySimilarity"], 0)


if __name__ == "__main__":
    unittest.main()
