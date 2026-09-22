import os
import sys
import json
import time
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure project root is in path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from PIL import Image

from server.main import (
    _scan_results,
    _scan_manga_groups,
    _get_cached_meta,
    _invalidate_meta_cache,
    _META_CACHE,
    _ensure_bbox_artifact,
    _ensure_thumbnail_artifact,
)


class TestServerResults(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="manga_test_results_"))
        _invalidate_meta_cache()

        # Create dummy result folders
        # Folder 1: Manga A, Page 1
        f1 = self.test_dir / "folder_001"
        f1.mkdir(parents=True, exist_ok=True)
        (f1 / "final.png").write_bytes(b"dummy final image content")
        (f1 / "input.png").write_bytes(b"dummy input image content")
        (f1 / "inpainted.jpg").write_bytes(b"dummy inpainted content")
        (f1 / "text_regions.json").write_text("[]", encoding="utf-8")
        meta1 = {
            "id": "item-1",
            "originalName": "01_cover.jpg",
            "mangaTitle": "Test Manga Series",
            "finishedAt": "2026-01-01T00:00:00Z",
            "settings": {"translator": "deepseek", "targetLanguage": "ENG", "largeConfigField": "x" * 1000},
        }
        (f1 / "meta.json").write_text(json.dumps(meta1), encoding="utf-8")

        # Folder 2: Manga A, Page 2
        f2 = self.test_dir / "folder_002"
        f2.mkdir(parents=True, exist_ok=True)
        (f2 / "final.png").write_bytes(b"dummy final image content")
        meta2 = {
            "id": "item-2",
            "originalName": "02_page.jpg",
            "mangaTitle": "Test Manga Series",
            "finishedAt": "2026-01-01T00:01:00Z",
            "settings": {"translator": "deepseek", "targetLanguage": "ENG"},
        }
        (f2 / "meta.json").write_text(json.dumps(meta2), encoding="utf-8")

        # Folder 3: Other Manga
        f3 = self.test_dir / "folder_003"
        f3.mkdir(parents=True, exist_ok=True)
        (f3 / "final.png").write_bytes(b"dummy final image content")
        meta3 = {
            "id": "item-3",
            "originalName": "other.jpg",
            "mangaTitle": "Other Title",
            "finishedAt": "2026-01-01T00:02:00Z",
            "settings": {"translator": "sugoi"},
        }
        (f3 / "meta.json").write_text(json.dumps(meta3), encoding="utf-8")

    def tearDown(self):
        _invalidate_meta_cache()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_slim_vs_full_result_responses(self):
        # 1. Full response (default detail=None)
        full_res = _scan_results(self.test_dir, sort="alpha", manga="Test Manga Series", detail=None)
        self.assertEqual(len(full_res["items"]), 2)
        item1 = next(item for item in full_res["items"] if item["folder"] == "folder_001")
        self.assertIn("settings", item1)
        self.assertEqual(item1["settings"]["translator"], "deepseek")
        self.assertIn("inpaintedUrl", item1)
        self.assertIn("textRegionsUrl", item1)
        self.assertTrue(item1["hasTextRegions"])
        self.assertIn("largeConfigField", item1["settings"])

        # 2. Slim reader response (detail="reader")
        slim_res = _scan_results(self.test_dir, sort="alpha", manga="Test Manga Series", detail="reader")
        self.assertEqual(len(slim_res["items"]), 2)
        slim_item1 = next(item for item in slim_res["items"] if item["folder"] == "folder_001")
        self.assertEqual(slim_item1["id"], "item-1")
        self.assertEqual(slim_item1["originalName"], "01_cover.jpg")
        self.assertEqual(slim_item1["mangaTitle"], "Test Manga Series")
        self.assertEqual(slim_item1["resultUrl"], "/result/folder_001/final.png")
        self.assertEqual(slim_item1["inputUrl"], "/result/folder_001/input.png")
        # Ensure heavy/editor fields are omitted in slim mode
        self.assertNotIn("settings", slim_item1)
        self.assertNotIn("inpaintedUrl", slim_item1)
        self.assertNotIn("textRegionsUrl", slim_item1)
        self.assertNotIn("hasTextRegions", slim_item1)

        # Confirm slim payload size is significantly smaller
        full_json_len = len(json.dumps(full_res))
        slim_json_len = len(json.dumps(slim_res))
        self.assertLess(slim_json_len, full_json_len)

    def test_list_results_does_not_generate_image_variants(self):
        with patch("server.main.generate_image_variants") as generate:
            _scan_results(self.test_dir, sort="alpha", manga="Test Manga Series")

        generate.assert_not_called()

    def test_scan_results_uses_persistent_page_order(self):
        for folder, order in (("folder_001", 2), ("folder_002", 1)):
            meta_path = self.test_dir / folder / "meta.json"
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            metadata["pageOrder"] = order
            meta_path.write_text(json.dumps(metadata), encoding="utf-8")
        _invalidate_meta_cache()

        result = _scan_results(self.test_dir, sort="alpha", manga="Test Manga Series")
        self.assertEqual([item["id"] for item in result["items"]], ["item-2", "item-1"])

    def test_scan_results_paginates_with_offset(self):
        first_page = _scan_results(self.test_dir, sort="alpha", limit=1, offset=0)
        second_page = _scan_results(self.test_dir, sort="alpha", limit=1, offset=1)

        self.assertEqual(first_page["total"], 3)
        self.assertEqual(first_page["nextOffset"], 1)
        self.assertEqual(len(first_page["items"]), 1)
        self.assertEqual(second_page["nextOffset"], 2)
        self.assertEqual(len(second_page["items"]), 1)
        self.assertNotEqual(first_page["items"][0]["id"], second_page["items"][0]["id"])

    def test_metadata_cache_and_invalidation(self):
        folder1 = self.test_dir / "folder_001"
        meta_file = folder1 / "meta.json"

        # 1. Initial read should populate cache
        cached1 = _get_cached_meta(folder1)
        self.assertEqual(cached1.get("mangaTitle"), "Test Manga Series")
        self.assertIn("folder_001", _META_CACHE)

        # 2. Reading again should return cached instance
        cached2 = _get_cached_meta(folder1)
        self.assertIs(cached1, cached2)

        # 3. Update file and modify mtime -> cache should detect mtime mismatch and reload
        updated_meta = {**cached1, "mangaTitle": "Updated Title After Edit"}
        time.sleep(0.05)
        meta_file.write_text(json.dumps(updated_meta), encoding="utf-8")
        new_mtime = time.time() + 10
        os.utime(str(meta_file), (new_mtime, new_mtime))

        cached3 = _get_cached_meta(folder1)
        self.assertEqual(cached3.get("mangaTitle"), "Updated Title After Edit")
        self.assertIsNot(cached1, cached3)

        # 4. Explicit invalidation
        _invalidate_meta_cache("folder_001")
        self.assertNotIn("folder_001", _META_CACHE)

        # 5. Global invalidation
        _get_cached_meta(folder1)
        self.assertIn("folder_001", _META_CACHE)
        _invalidate_meta_cache()
        self.assertEqual(len(_META_CACHE), 0)

    def test_scan_manga_groups_uses_cache(self):
        groups_res = _scan_manga_groups(self.test_dir)
        self.assertEqual(len(groups_res["groups"]), 2)
        # Verify cache was populated for all scanned folders
        self.assertIn("folder_001", _META_CACHE)
        self.assertIn("folder_002", _META_CACHE)
        self.assertIn("folder_003", _META_CACHE)

    def test_scan_manga_groups_paginates(self):
        first_page = _scan_manga_groups(self.test_dir, limit=1, offset=0)
        second_page = _scan_manga_groups(self.test_dir, limit=1, offset=1)

        self.assertEqual(first_page["totalGroups"], 2)
        self.assertEqual(first_page["nextOffset"], 1)
        self.assertEqual(len(first_page["groups"]), 1)
        self.assertEqual(second_page["totalGroups"], 2)
        self.assertIsNone(second_page["nextOffset"])
        self.assertEqual(len(second_page["groups"]), 1)
        self.assertNotEqual(first_page["groups"][0]["id"], second_page["groups"][0]["id"])

    def test_scan_manga_groups_sorts_before_pagination(self):
        newest = _scan_manga_groups(self.test_dir, limit=1, sort="date-desc")
        oldest = _scan_manga_groups(self.test_dir, limit=1, sort="date-asc")

        self.assertEqual(newest["groups"][0]["title"], "Other Title")
        self.assertEqual(oldest["groups"][0]["title"], "Test Manga Series")

    def test_scan_manga_groups_search_filters_image_totals(self):
        result = _scan_manga_groups(self.test_dir, search="Test Manga")

        self.assertEqual(result["totalGroups"], 1)
        self.assertEqual(result["totalImages"], 2)
        self.assertEqual(result["groups"][0]["title"], "Test Manga Series")

    def test_ensure_bbox_artifact_already_exists(self):
        folder = self.test_dir / "folder_001"
        existing_file = folder / "bboxes_unfiltered.png"
        existing_file.write_bytes(b"already-exists")

        result = _ensure_bbox_artifact(folder, "bboxes_unfiltered.png")
        self.assertFalse(result)
        self.assertEqual(existing_file.read_bytes(), b"already-exists")

    def test_ensure_bbox_artifact_synthesis(self):
        folder = self.test_dir / "folder_bbox_test"
        folder.mkdir(parents=True, exist_ok=True)

        # Create valid input PNG image
        img = Image.new("RGB", (120, 120), color="white")
        img.save(folder / "input.png", format="PNG")

        # Create valid text_regions.json
        regions = [
            {
                "x": 10,
                "y": 15,
                "width": 60,
                "height": 40,
                "lines": [[[10, 15], [70, 15], [70, 55], [10, 55]]],
            },
            {
                "x": 80,
                "y": 20,
                "width": 30,
                "height": 50,
            },
        ]
        (folder / "text_regions.json").write_text(json.dumps(regions), encoding="utf-8")

        # 1. Synthesize detection.json
        res_json = _ensure_bbox_artifact(folder, "detection.json")
        self.assertTrue(res_json)
        self.assertTrue((folder / "detection.json").is_file())
        detection_data = json.loads((folder / "detection.json").read_text(encoding="utf-8"))
        self.assertEqual(len(detection_data), 2)
        self.assertEqual(detection_data[0]["pts"], [[[10, 15], [70, 15], [70, 55], [10, 55]]][0])
        self.assertEqual(detection_data[1]["pts"], [[80, 20], [110, 20], [110, 70], [80, 70]])

        # Bounding boxes are rendered by the frontend overlay, never persisted.
        self.assertFalse(_ensure_bbox_artifact(folder, "bboxes_unfiltered.png"))
        self.assertFalse(_ensure_bbox_artifact(folder, "bboxes.png"))

    def test_ensure_bbox_artifact_missing_inputs(self):
        folder = self.test_dir / "folder_missing"
        folder.mkdir(parents=True, exist_ok=True)

        # Missing text_regions.json
        self.assertFalse(_ensure_bbox_artifact(folder, "bboxes_unfiltered.png"))
        self.assertFalse(_ensure_bbox_artifact(folder, "detection.json"))

        # Invalid text_regions.json
        (folder / "text_regions.json").write_text("invalid json", encoding="utf-8")
        self.assertFalse(_ensure_bbox_artifact(folder, "detection.json"))

        # Valid text_regions.json but no base image
        (folder / "text_regions.json").write_text("[]", encoding="utf-8")
        self.assertFalse(_ensure_bbox_artifact(folder, "bboxes_unfiltered.png"))

    def test_ensure_thumbnail_artifact(self):
        folder = self.test_dir / "folder_thumb_test"
        folder.mkdir(parents=True, exist_ok=True)

        # Create valid 800x1200 image
        img = Image.new("RGB", (800, 1200), color="blue")
        img.save(folder / "final.png", format="PNG")

        # 1. Synthesize thumbnail.webp
        res_webp = _ensure_thumbnail_artifact(folder, "thumbnail.webp")
        self.assertTrue(res_webp)
        thumb_webp = folder / "thumbnail.webp"
        self.assertTrue(thumb_webp.is_file())
        self.assertGreater(thumb_webp.stat().st_size, 0)
        with Image.open(thumb_webp) as loaded_img:
            self.assertLessEqual(loaded_img.width, 360)
            self.assertLessEqual(loaded_img.height, 500)

        # 2. Re-synthesize should return True immediately from cache
        self.assertTrue(_ensure_thumbnail_artifact(folder, "thumbnail.webp"))

        # 3. Fallback to input.png when final.png is absent
        folder2 = self.test_dir / "folder_thumb_input_fallback"
        folder2.mkdir(parents=True, exist_ok=True)
        img2 = Image.new("RGB", (600, 900), color="green")
        img2.save(folder2 / "input.png", format="PNG")
        self.assertTrue(_ensure_thumbnail_artifact(folder2, "thumbnail.webp"))
        self.assertTrue((folder2 / "thumbnail.webp").is_file())

    def test_thumbnail_urls_in_scan_results(self):
        full_res = _scan_results(self.test_dir, sort="alpha", manga="Test Manga Series", detail=None)
        self.assertEqual(len(full_res["items"]), 2)
        for item in full_res["items"]:
            self.assertIn("thumbnailUrl", item)
            self.assertTrue(item["thumbnailUrl"].endswith("thumbnail.webp"))

        groups_res = _scan_manga_groups(self.test_dir)
        group_ids = [grp["id"] for grp in groups_res["groups"]]
        self.assertTrue(all(group_id.startswith("manga-") for group_id in group_ids))
        self.assertEqual(len(group_ids), len(set(group_ids)))
        for grp in groups_res["groups"]:
            if grp.get("cover"):
                self.assertIn("thumbnailUrl", grp["cover"])
                self.assertTrue(grp["cover"]["thumbnailUrl"].endswith("thumbnail.webp"))


if __name__ == "__main__":
    unittest.main()
