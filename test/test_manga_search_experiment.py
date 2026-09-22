import os
import json
import math
import tempfile
import uuid
import unittest
from importlib.util import find_spec
from pathlib import Path

from PIL import Image

from experiments.manga_search.chunking import QueryTooLong, chunk_text, validate_query_length
from experiments.manga_search.config import EMBEDDING_DIMENSION
from experiments.manga_search.db import DatabaseError, MangaSearchDB
from experiments.manga_search.manifest import ManifestError, PageRecord, load_manifest, resolve_selection
from experiments.manga_search.metrics import benchmark_metrics, validate_vector
from experiments.manga_search.preprocessing import prepare_image
from experiments.manga_search.retrieval import (
    PageScore,
    page_scores,
    rank_manga,
    reciprocal_rank_fusion,
)


class ToyTokenizer:
    def __call__(self, text, add_special_tokens=True, truncation=False, return_offsets_mapping=False):
        words = list(__import__("re").finditer(r"\S+", text))
        result = {"input_ids": list(range(len(words) + (2 if add_special_tokens else 0)))}
        if return_offsets_mapping:
            result["offset_mapping"] = [(match.start(), match.end()) for match in words]
        return result

    def num_special_tokens_to_add(self, pair=False):
        return 2

    def decode(self, ids, **_kwargs):
        return "token " * len(ids)


class MangaSearchCoreTest(unittest.TestCase):
    def test_manifest_selection_paths_sources_and_repeated_filters(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            image = root / "page.png"
            Image.new("RGB", (2, 4), "red").save(image)
            source = root / "text_regions.json"
            source.write_text(
                json.dumps(
                    [
                        {"translation": "First"},
                        {"translation": "Second"},
                    ]
                ),
                encoding="utf-8",
            )
            manifest = root / "pages.jsonl"
            manifest.write_text(
                json.dumps(
                    {
                        "manga_id": "m1",
                        "manga_title": "Title",
                        "chapter_id": "c1",
                        "page_id": "p1",
                        "page_number": 1,
                        "image_path": "page.png",
                        "text_source": "text_regions.json",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            records = load_manifest(manifest)
            selected = resolve_selection(records, ["m1", "m1"], ["p1", "p1"])
            self.assertEqual(selected[0].text, "First\nSecond")
            self.assertEqual(Path(selected[0].image_path), image.resolve())

    def test_manifest_rejects_conflicting_sources(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            Image.new("RGB", (1, 1), "white").save(root / "p.png")
            (root / "pages.jsonl").write_text(
                json.dumps(
                    {
                        "manga_id": "m1",
                        "manga_title": "Title",
                        "chapter_id": "c1",
                        "page_id": "p1",
                        "page_number": 1,
                        "image_path": "p.png",
                        "text": "inline",
                        "text_source": "missing.txt",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ManifestError):
                load_manifest(root / "pages.jsonl")

    def test_chunking_has_overlap_and_explicit_query_rejection(self):
        tokenizer = ToyTokenizer()
        text = " ".join(f"word{i}" for i in range(20))
        chunks = chunk_text(tokenizer, text, max_tokens=8, overlap=2)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(validate_query_length(tokenizer, chunk, 8) <= 8 for chunk in chunks))
        self.assertEqual(set(text.split()), set(" ".join(chunks).split()))
        with self.assertRaises(QueryTooLong):
            validate_query_length(tokenizer, "one two three four", 3)

    def test_whole_page_padding_preserves_edges_and_vector_contract(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "page.png"
            Image.new("RGB", (2, 4), "red").save(path)
            prepared = prepare_image(path)
            self.assertEqual(prepared.size, (4, 4))
            self.assertEqual(prepared.getpixel((1, 0)), (255, 0, 0))
            self.assertEqual(prepared.getpixel((2, 3)), (255, 0, 0))
        vector = validate_vector([1.0] + [0.0] * (EMBEDDING_DIMENSION - 1))
        self.assertTrue(math.isclose(sum(item * item for item in vector), 1.0))
        with self.assertRaises(ValueError):
            validate_vector([1.0, 0.0])

    def test_page_ranking_rrf_and_metrics(self):
        rows = [
            {"manga_id": "m1", "manga_title": "One", "page_id": "p1", "page_number": 1, "image_path": "p1", "score": 0.4, "excerpt": "weak"},
            {"manga_id": "m1", "manga_title": "One", "page_id": "p1", "page_number": 1, "image_path": "p1", "score": 0.9, "excerpt": "best"},
            {"manga_id": "m2", "manga_title": "Two", "page_id": "p2", "page_number": 2, "image_path": "p2", "score": 0.8},
        ]
        text = rank_manga(page_scores(rows, "text"))
        image = rank_manga(
            [
                PageScore("m1", "One", "p1", 1, "p1", 0.7, "image"),
                PageScore("m2", "Two", "p2", 2, "p2", 0.9, "image"),
            ]
        )
        self.assertEqual(text[0].pages[0].excerpt, "best")
        self.assertEqual(reciprocal_rank_fusion(text, image)[0].manga_id, "m1")
        metrics = benchmark_metrics(
            [["m1", "m2"], ["m2", "m1"]],
            ["m1", "m1"],
            [["p1"], ["p2"]],
            [["p1"], ["p1"]],
        )
        self.assertEqual(metrics["manga_top1_accuracy"], 0.5)
        self.assertEqual(metrics["manga_mrr"], 0.75)
        self.assertEqual(metrics["relevant_page_recall"], 0.5)


@unittest.skipUnless(
    os.getenv("TEST_DATABASE_URL") and find_spec("psycopg"),
    "Set TEST_DATABASE_URL and install experiment dependencies for PostgreSQL checks",
)
class MangaSearchPostgresTest(unittest.TestCase):
    def test_exact_vector_retrieval_and_run_isolation(self):
        corpus = f"test-{uuid.uuid4().hex}"
        db = MangaSearchDB(os.environ["TEST_DATABASE_URL"])
        db.ensure_schema()
        page = PageRecord("m1", "One", "c1", "p1", 1, "/tmp/p1.png", "dialogue")
        vector = [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)
        try:
            run_id = db.start_run(corpus, [page], "test-model", "test-revision", "cpu")
            db.store_page(
                corpus,
                page,
                "test-model",
                "test-revision",
                [
                    {
                        "modality": "image",
                        "chunk_number": 0,
                        "vector": vector,
                        "content_fingerprint": "image-fp",
                    }
                ],
                {"image"},
            )
            db.finish_run(run_id, "complete")
            rows = db.search_pages(corpus, "test-model", "test-revision", "image", vector)
            self.assertEqual(rows[0]["page_id"], "p1")
            self.assertAlmostEqual(rows[0]["score"], 1.0, places=6)
            db.require_complete_run(corpus, "test-model", "test-revision")
            other_corpus = f"test-{uuid.uuid4().hex}"
            with self.assertRaises(DatabaseError):
                db.require_complete_run(other_corpus, "test-model", "test-revision")
        finally:
            with db.conn.transaction():
                db.conn.execute("DELETE FROM manga_search_experiment.embeddings WHERE corpus_fingerprint=%s", (corpus,))
                db.conn.execute("DELETE FROM manga_search_experiment.runs WHERE corpus_fingerprint=%s", (corpus,))
                db.conn.execute("DELETE FROM manga_search_experiment.pages WHERE corpus_fingerprint=%s", (corpus,))
            db.close()


if __name__ == "__main__":
    unittest.main()
