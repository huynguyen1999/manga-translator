import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import server.main as sm
from server.batch_store import BatchStore
from server.batch_scheduler import BatchScheduler
from server.services.manga_review_mutation import MangaReviewMutationService
from starlette.testclient import TestClient


class TestMangaReviewApproval(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="manga_review_test_")
        self.root_path = Path(self.temp_dir).resolve()
        self.results_dir = self.root_path / "result"
        self.batches_dir = self.root_path / "batches"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.batches_dir.mkdir(parents=True, exist_ok=True)

        self.orig_result_root = sm.RESULT_ROOT
        self.orig_batch_store = sm.batch_store
        self.orig_batch_scheduler = sm.batch_scheduler

        sm.RESULT_ROOT = self.results_dir
        sm.batch_store = BatchStore(self.batches_dir, self.results_dir)
        sm.batch_scheduler = BatchScheduler(sm.batch_store, sm.executor_instances, self.results_dir)
        sm._invalidate_meta_cache()

        img = Image.new("RGB", (64, 64), color="red")
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        self.sample_png = img_bytes.getvalue()

        self.client = TestClient(sm.app)

    def tearDown(self):
        sm.RESULT_ROOT = self.orig_result_root
        sm.batch_store = self.orig_batch_store
        sm.batch_scheduler = self.orig_batch_scheduler
        sm._invalidate_meta_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_page(self, folder_name: str, manga_title: str, review_required: bool = True):
        folder = self.results_dir / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "final.png").write_bytes(self.sample_png)

        regions = [
            {
                "id": "b1",
                "x": 10,
                "y": 10,
                "width": 40,
                "height": 40,
                "translation": "Text",
                "review_required": review_required,
                "review_reason": "uncertain_boundary" if review_required else None,
            }
        ]
        (folder / "text_regions.json").write_text(json.dumps(regions), encoding="utf-8")

        meta = {
            "mangaTitle": manga_title,
            "reviewStatus": "pending" if review_required else "approved",
            "pageOrder": 1,
        }
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return folder

    def test_approve_all_reviews_for_manga_success(self):
        self._create_page("page_1", "Test Manga", review_required=True)
        self._create_page("page_2", "Test Manga", review_required=True)
        self._create_page("page_3", "Other Manga", review_required=True)

        # Call the endpoint to approve all pages in Test Manga
        response = self.client.post("/api/manga/Test%20Manga/review/approve-all")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["mangaTitle"], "Test Manga")
        self.assertEqual(data["approvedCount"], 2)
        self.assertEqual(data["status"], "approved")

        # Verify page 1 and page 2 text regions and meta are approved
        meta1 = json.loads((self.results_dir / "page_1" / "meta.json").read_text(encoding="utf-8"))
        regions1 = json.loads((self.results_dir / "page_1" / "text_regions.json").read_text(encoding="utf-8"))
        self.assertEqual(meta1["reviewStatus"], "approved")
        self.assertIsNotNone(meta1.get("reviewedAt"))
        self.assertFalse(regions1[0]["review_required"])
        self.assertIsNone(regions1[0]["review_reason"])

        meta2 = json.loads((self.results_dir / "page_2" / "meta.json").read_text(encoding="utf-8"))
        regions2 = json.loads((self.results_dir / "page_2" / "text_regions.json").read_text(encoding="utf-8"))
        self.assertEqual(meta2["reviewStatus"], "approved")
        self.assertFalse(regions2[0]["review_required"])

        # Other manga page should still be pending
        meta3 = json.loads((self.results_dir / "page_3" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta3["reviewStatus"], "pending")

    def test_approve_all_reviews_nonexistent_manga_returns_404(self):
        response = self.client.post("/api/manga/Nonexistent/review/approve-all")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
