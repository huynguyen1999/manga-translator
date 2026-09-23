import os
import sys
import json
import base64
import io
import zipfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from PIL import Image

# Ensure project root is in python path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import server.main as sm
from server.batch_store import BatchStore
from server.batch_scheduler import BatchScheduler
from starlette.testclient import TestClient


class TestFrontendBackendApiSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="manga_api_sync_test_")
        self.root_path = Path(self.temp_dir).resolve()
        self.results_dir = self.root_path / "result"
        self.batches_dir = self.root_path / "batches"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.batches_dir.mkdir(parents=True, exist_ok=True)

        # Save originals to restore in tearDown
        self.orig_result_root = sm.RESULT_ROOT
        self.orig_batch_store = sm.batch_store
        self.orig_batch_scheduler = sm.batch_scheduler

        # Monkeypatch server state
        sm.RESULT_ROOT = self.results_dir
        sm.batch_store = BatchStore(self.batches_dir, self.results_dir)
        sm.batch_scheduler = BatchScheduler(sm.batch_store, sm.executor_instances, self.results_dir)
        sm._invalidate_meta_cache()

        from manga_translator.pipeline.run import set_document_saver
        async def _save_docs(folder, docs):
            folder_dir = self.results_dir / folder
            if folder_dir.is_dir():
                for name, content in docs.items():
                    if isinstance(content, (dict, list)):
                        (folder_dir / name).write_text(json.dumps(content, indent=2), encoding="utf-8")
                    elif isinstance(content, bytes):
                        (folder_dir / name).write_bytes(content)
        set_document_saver(_save_docs)

        # Seed test image
        img = Image.new("RGB", (64, 64), color="blue")
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        self.sample_png = img_bytes.getvalue()
        jpg_bytes = io.BytesIO()
        img.save(jpg_bytes, format="JPEG")
        self.sample_jpg = jpg_bytes.getvalue()

        # Create 3 test result folders
        # 1) folder_alpha_1 (Series Alpha, Page 1)
        f1 = self.results_dir / "folder_alpha_1"
        f1.mkdir()
        (f1 / "final.png").write_bytes(self.sample_png)
        (f1 / "input.png").write_bytes(self.sample_png)
        (f1 / "inpainted.jpg").write_bytes(self.sample_jpg)
        (f1 / "text_regions.json").write_text(
            json.dumps([{"id": "bubble_0", "text": "Original Text", "x": 10, "y": 10, "width": 40, "height": 20}]),
            encoding="utf-8"
        )
        (f1 / "meta.json").write_text(json.dumps({
            "id": "item-alpha-1",
            "originalName": "01.png",
            "mangaTitle": "Series Alpha",
            "finishedAt": "2026-01-01T00:00:00Z",
            "settings": {"translator": "deepseek", "targetLanguage": "ENG"}
        }), encoding="utf-8")

        # 2) folder_alpha_2 (Series Alpha, Page 2)
        f2 = self.results_dir / "folder_alpha_2"
        f2.mkdir()
        (f2 / "final.png").write_bytes(self.sample_png)
        (f2 / "input.png").write_bytes(self.sample_png)
        (f2 / "meta.json").write_text(json.dumps({
            "id": "item-alpha-2",
            "originalName": "02.png",
            "mangaTitle": "Series Alpha",
            "finishedAt": "2026-01-01T00:01:00Z",
            "settings": {"translator": "deepseek", "targetLanguage": "ENG"}
        }), encoding="utf-8")

        # 3) folder_ungrouped (Ungrouped)
        f3 = self.results_dir / "folder_ungrouped"
        f3.mkdir()
        (f3 / "final.png").write_bytes(self.sample_png)
        (f3 / "meta.json").write_text(json.dumps({
            "id": "item-ungrouped-1",
            "originalName": "standalone.png",
            "mangaTitle": "Ungrouped",
            "finishedAt": "2026-01-01T00:02:00Z",
            "settings": {"translator": "sugoi", "targetLanguage": "ENG"}
        }), encoding="utf-8")

        # 4) folder_orig_1 (Original Raw Series)
        f4 = self.results_dir / "folder_orig_1"
        f4.mkdir()
        (f4 / "final.png").write_bytes(self.sample_png)
        (f4 / "meta.json").write_text(json.dumps({
            "id": "item-orig-1",
            "originalName": "raw_01.png",
            "mangaTitle": "Original Raw Series",
            "finishedAt": "2026-01-01T00:03:00Z",
            "sourceType": "original",
            "settings": {"translator": "none", "inpainter": "original"}
        }), encoding="utf-8")

        self.client = TestClient(sm.app)

    def tearDown(self):
        sm.RESULT_ROOT = self.orig_result_root
        sm.batch_store = self.orig_batch_store
        sm.batch_scheduler = self.orig_batch_scheduler
        sm._invalidate_meta_cache()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ==========================================
    # 1. /results/groups & /api/results/groups
    # ==========================================
    def test_results_groups_endpoint(self):
        for prefix in ["", "/api"]:
            res = self.client.get(f"{prefix}/results/groups")
            self.assertEqual(res.status_code, 200, f"Failed on prefix: {prefix}")
            data = res.json()
            self.assertIn("groups", data)
            self.assertIn("totalImages", data)
            self.assertEqual(data["totalImages"], 4)
            titles = {g["title"]: g["count"] for g in data["groups"]}
            self.assertEqual(titles.get("Series Alpha"), 2)
            self.assertEqual(titles.get("Ungrouped"), 1)
            self.assertEqual(titles.get("Original Raw Series"), 1)
            ids = [group["id"] for group in data["groups"]]
            self.assertTrue(all(item_id.startswith("manga-") for item_id in ids))
            self.assertEqual(len(ids), len(set(ids)))

            paged = self.client.get(f"{prefix}/results/groups?limit=1&offset=0")
            self.assertEqual(paged.status_code, 200)
            paged_data = paged.json()
            self.assertEqual(len(paged_data["groups"]), 1)
            self.assertEqual(paged_data["totalGroups"], 3)
            self.assertEqual(paged_data["nextOffset"], 1)

            by_id = self.client.get(
                f"{prefix}/results/groups?mangaId={paged_data['groups'][0]['id']}"
            )
            self.assertEqual(by_id.status_code, 200)
            self.assertEqual(len(by_id.json()["groups"]), 1)
            self.assertEqual(by_id.json()["groups"][0]["id"], paged_data["groups"][0]["id"])

            searched = self.client.get(f"{prefix}/results/groups?search=Series Alpha")
            self.assertEqual(searched.status_code, 200)
            self.assertEqual([group["title"] for group in searched.json()["groups"]], ["Series Alpha"])
            self.assertEqual(searched.json()["totalGroups"], 1)

            status_translated = self.client.get(f"{prefix}/results/groups?status=translated")
            self.assertEqual(status_translated.status_code, 200)
            trans_titles = [g["title"] for g in status_translated.json()["groups"]]
            self.assertIn("Series Alpha", trans_titles)
            self.assertIn("Ungrouped", trans_titles)
            self.assertNotIn("Original Raw Series", trans_titles)
            self.assertEqual(status_translated.json()["totalGroups"], 2)

            status_orig = self.client.get(f"{prefix}/results/groups?status=original")
            self.assertEqual(status_orig.status_code, 200)
            orig_titles = [g["title"] for g in status_orig.json()["groups"]]
            self.assertEqual(orig_titles, ["Original Raw Series"])
            self.assertEqual(status_orig.json()["totalGroups"], 1)

            next_page = self.client.get(f"{prefix}/results/groups?limit=1&offset=1")
            self.assertEqual(next_page.status_code, 200)
            self.assertEqual(len(next_page.json()["groups"]), 1)
            self.assertEqual(next_page.json()["nextOffset"], 2)

    # ==========================================
    # 2. /results/list & /api/results/list
    # ==========================================
    def test_results_list_endpoint(self):
        for prefix in ["", "/api"]:
            # All items
            res = self.client.get(f"{prefix}/results/list")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("items", data)
            self.assertEqual(len(data["items"]), 4)

            # Filtered by manga
            res = self.client.get(f"{prefix}/results/list?manga=Series Alpha")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(len(data["items"]), 2)
            self.assertEqual(data["items"][0]["originalName"], "01.png")
            self.assertEqual(data["items"][1]["originalName"], "02.png")

            # Sorting: alpha_desc
            res = self.client.get(f"{prefix}/results/list?manga=Series Alpha&sort=alpha_desc")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["items"][0]["originalName"], "02.png")

            # Reader detail mode (slim payload)
            res = self.client.get(f"{prefix}/results/list?manga=Series Alpha&detail=reader")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            first = data["items"][0]
            self.assertIn("id", first)
            self.assertIn("folder", first)
            self.assertIn("originalName", first)
            self.assertIn("mangaTitle", first)
            self.assertIn("resultUrl", first)
            self.assertIn("inputUrl", first)
            self.assertIn("finishedAt", first)
            # Ensure bulky metadata like settings and textRegionsUrl are omitted in slim mode
            self.assertNotIn("settings", first)
            self.assertNotIn("textRegionsUrl", first)

            # Offset pagination
            res = self.client.get(f"{prefix}/results/list?limit=1&offset=1")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(len(res.json()["items"]), 1)
            self.assertEqual(res.json()["total"], 4)
            self.assertEqual(res.json()["nextOffset"], 2)

    def test_reorder_pages_endpoint_persists_and_validates(self):
        group = next(
            group for group in self.client.get("/api/results/groups?search=Series Alpha").json()["groups"]
        )
        listed = self.client.get(f"/api/results/list?groupId={group['id']}").json()["items"]
        page_ids = [item["id"] for item in listed]

        invalid = self.client.put(
            f"/api/manga/{group['id']}/pages/order",
            json={"pageIds": [page_ids[0], page_ids[0]]},
        )
        self.assertEqual(invalid.status_code, 400)

        reordered = self.client.put(
            f"/api/manga/{group['id']}/pages/order",
            json={"pageIds": list(reversed(page_ids))},
        )
        self.assertEqual(reordered.status_code, 200)
        self.assertEqual(
            [page["id"] for page in reordered.json()["pages"]],
            list(reversed(page_ids)),
        )
        persisted = self.client.get(f"/api/results/list?groupId={group['id']}").json()["items"]
        self.assertEqual([item["id"] for item in persisted], list(reversed(page_ids)))
        self.assertEqual([item["pageOrder"] for item in persisted], [1, 2])

    def test_original_manga_import_endpoint(self):
        jpg_buffer = io.BytesIO()
        Image.new("RGB", (32, 32), color="red").save(jpg_buffer, format="JPEG")
        sample_jpg = jpg_buffer.getvalue()

        response = self.client.post(
            "/api/results/import",
            data={"mangaTitle": "Imported Series"},
            files=[
                ("files", ("02.jpg", sample_jpg, "image/jpeg")),
                ("files", ("01.png", self.sample_png, "image/png")),
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["group"]["title"], "Imported Series")
        self.assertEqual(response.json()["group"]["count"], 2)

        listed = self.client.get("/api/results/list?manga=Imported Series&detail=reader").json()["items"]
        self.assertEqual([item["originalName"] for item in listed], ["02.jpg", "01.png"])
        self.assertTrue(all(item["sourceType"] == "original" for item in listed))
        red_pixel = Image.open(io.BytesIO(self.client.get(listed[0]["inputUrl"]).content)).getpixel((0, 0))
        blue_pixel = Image.open(io.BytesIO(self.client.get(listed[1]["inputUrl"]).content)).getpixel((0, 0))
        self.assertGreaterEqual(red_pixel[0], 250)
        self.assertLess(red_pixel[1], 10)
        self.assertLess(red_pixel[2], 10)
        self.assertLess(blue_pixel[0], 10)
        self.assertLess(blue_pixel[1], 10)
        self.assertGreaterEqual(blue_pixel[2], 250)

        archive = self.client.get("/api/results/export/cbz?manga=Imported Series")
        self.assertEqual(archive.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(archive.content), "r") as zf:
            pages = [name for name in zf.namelist() if name != "ComicInfo.xml"]
            self.assertEqual(len(pages), 2)
            exported_red = Image.open(io.BytesIO(zf.read("001_02.jpg"))).getpixel((0, 0))
            exported_blue = Image.open(io.BytesIO(zf.read("002_01.png"))).getpixel((0, 0))
            self.assertGreaterEqual(exported_red[0], 250)
            self.assertLess(exported_red[1], 10)
            self.assertLess(exported_red[2], 10)
            self.assertLess(exported_blue[0], 10)
            self.assertLess(exported_blue[1], 10)
            self.assertGreaterEqual(exported_blue[2], 250)

        group = next(
            group for group in self.client.get("/api/results/groups?search=Imported Series").json()["groups"]
        )
        appended = self.client.post(
            "/api/results/import",
            data={"mangaTitle": "Imported Series", "groupId": group["id"], "isNewGroup": "false"},
            files=[("files", ("02.jpg", sample_jpg, "image/jpeg"))],
        )
        self.assertEqual(appended.status_code, 200)
        appended_items = self.client.get(
            "/api/results/list?manga=Imported Series&detail=reader"
        ).json()["items"]
        self.assertEqual([item["originalName"] for item in appended_items], ["02.jpg", "01.png", "02.jpg"])
        self.assertEqual([item["pageOrder"] for item in appended_items], [1, 2, 3])

        duplicate = self.client.post(
            "/results/import",
            data={"mangaTitle": "Imported Series"},
            files=[("files", ("03.png", self.sample_png, "image/png"))],
        )
        self.assertEqual(duplicate.status_code, 409)

        before_invalid = {path.name for path in self.results_dir.iterdir() if path.is_dir()}
        invalid = self.client.post(
            "/api/results/import",
            data={"mangaTitle": "Rejected Series"},
            files=[
                ("files", ("01.png", self.sample_png, "image/png")),
                ("files", ("02.png", b"not an image", "image/png")),
            ],
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(
            {path.name for path in self.results_dir.iterdir() if path.is_dir()},
            before_invalid,
        )

    def test_original_manga_import_streams_past_batch_upload_limit(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("01.png", self.sample_png)

        store = AsyncMock()
        store.group_exists.return_value = False
        store.resolve_group_id.return_value = None
        store.list_groups.return_value = {
            "groups": [{"title": "Large Imported Series", "count": 1}],
            "totalImages": 1,
        }
        store.list_results.return_value = {"items": []}

        with (
            patch.object(sm, "MAX_BATCH_UPLOAD_BYTES", 1),
            patch.object(sm, "_postgres", return_value=store),
            patch.object(sm, "generate_image_variants", side_effect=RuntimeError("preview warmup failed")) as generate,
        ):
            response = self.client.post(
                "/api/results/import",
                data={"mangaTitle": "Large Imported Series"},
                files=[("files", ("large.cbz", archive.getvalue(), "application/vnd.comicbook+zip"))],
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["group"]["title"], "Large Imported Series")
        store.sync_result_folder.assert_awaited_once_with(
            store.sync_result_folder.await_args.args[0], generate_variants=False
        )
        generate.assert_called_once_with(
            sm.RESULT_ROOT / store.sync_result_folder.await_args.args[0],
            only="preview",
        )

    def test_original_manga_import_cbz_and_zip_archives(self):
        jpg_buffer = io.BytesIO()
        Image.new("RGB", (32, 32), color="blue").save(jpg_buffer, format="JPEG")
        sample_jpg = jpg_buffer.getvalue()

        # Build a sample .cbz archive with metadata and images in subfolders
        cbz_buffer = io.BytesIO()
        with zipfile.ZipFile(cbz_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ComicInfo.xml", "<ComicInfo><Title>CBZ Title</Title></ComicInfo>")
            zf.writestr("__MACOSX/._01.png", b"fake apple double")
            zf.writestr(".DS_Store", b"fake ds_store")
            zf.writestr("ch1/page_02.jpg", sample_jpg)
            zf.writestr("ch1/page_01.png", self.sample_png)
            zf.writestr("ch1/notes.txt", b"translator notes")
            zf.writestr("ch2/page_01.png", self.sample_png)

        cbz_bytes = cbz_buffer.getvalue()

        response = self.client.post(
            "/api/results/import",
            data={"mangaTitle": "CBZ Series"},
            files=[
                ("files", ("volume1.cbz", cbz_bytes, "application/vnd.comicbook+zip")),
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["group"]["title"], "CBZ Series")
        self.assertEqual(response.json()["group"]["count"], 3)

        items = self.client.get("/api/results/list?manga=CBZ Series&detail=reader").json()["items"]
        self.assertEqual(len(items), 3)
        self.assertTrue(all(item["sourceType"] == "original" for item in items))

        # Test .zip archive import
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("p1.png", self.sample_png)
            zf.writestr("p2.jpg", sample_jpg)

        zip_response = self.client.post(
            "/api/results/import",
            data={"mangaTitle": "ZIP Series"},
            files=[
                ("files", ("chapter.zip", zip_buffer.getvalue(), "application/zip")),
            ],
        )
        self.assertEqual(zip_response.status_code, 200)
        self.assertEqual(zip_response.json()["group"]["title"], "ZIP Series")
        self.assertEqual(zip_response.json()["group"]["count"], 2)

        # Test archive with no images
        empty_zip_buffer = io.BytesIO()
        with zipfile.ZipFile(empty_zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("readme.txt", "no images here")

        no_img_response = self.client.post(
            "/api/results/import",
            data={"mangaTitle": "Empty Series"},
            files=[
                ("files", ("empty.cbz", empty_zip_buffer.getvalue(), "application/vnd.comicbook+zip")),
            ],
        )
        self.assertEqual(no_img_response.status_code, 400)
        self.assertIn("No supported images", no_img_response.json()["detail"])

        # Test corrupted archive
        corrupt_response = self.client.post(
            "/api/results/import",
            data={"mangaTitle": "Corrupt Series"},
            files=[
                ("files", ("broken.cbz", b"corrupted bytes", "application/vnd.comicbook+zip")),
            ],
        )
        self.assertEqual(corrupt_response.status_code, 400)
        self.assertIn("Invalid or corrupted archive", corrupt_response.json()["detail"])

    # ==========================================
    # 3. /results/{folder_name} & /api/results/{folder_name}
    # ==========================================
    def test_result_detail_endpoint(self):
        for prefix in ["", "/api"]:
            res = self.client.get(f"{prefix}/results/folder_alpha_1")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["folder"], "folder_alpha_1")
            self.assertEqual(data["originalName"], "01.png")
            self.assertEqual(data["mangaTitle"], "Series Alpha")
            self.assertEqual(data["resultUrl"], "/result/folder_alpha_1/final.png")
            self.assertEqual(data["inputUrl"], "/result/folder_alpha_1/input.png")
            self.assertEqual(data["inpaintedUrl"], "/result/folder_alpha_1/inpainted.jpg")
            self.assertEqual(data["textRegionsUrl"], "/result/folder_alpha_1/text_regions.json")
            self.assertTrue(data["hasTextRegions"])

            # Nonexistent folder
            res_404 = self.client.get(f"{prefix}/results/nonexistent_folder")
            self.assertEqual(res_404.status_code, 404)

    # ==========================================
    # 4. Result file routes: /result/... & /api/result/...
    # ==========================================
    def test_result_files_endpoint(self):
        for prefix in ["", "/api"]:
            # GET final.png
            res = self.client.get(f"{prefix}/result/folder_alpha_1/final.png")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers["Content-Type"], "image/png")
            self.assertEqual(res.content, self.sample_png)

            # GET inpainted.jpg
            inpainted_res = self.client.get(f"{prefix}/result/folder_alpha_1/inpainted.jpg")
            self.assertEqual(inpainted_res.status_code, 200)
            self.assertEqual(inpainted_res.headers["Content-Type"], "image/jpeg")
            self.assertEqual(inpainted_res.content, self.sample_jpg)

            # Legacy PNG URL remains an alias for existing clients.
            legacy_inpainted_res = self.client.get(f"{prefix}/result/folder_alpha_1/inpainted.png")
            self.assertEqual(legacy_inpainted_res.status_code, 200)
            self.assertEqual(legacy_inpainted_res.headers["Content-Type"], "image/jpeg")
            self.assertEqual(legacy_inpainted_res.content, self.sample_jpg)

            # HEAD final.png
            head_res = self.client.head(f"{prefix}/result/folder_alpha_1/final.png")
            self.assertEqual(head_res.status_code, 200)

            # GET text_regions.json
            json_res = self.client.get(f"{prefix}/result/folder_alpha_1/text_regions.json")
            self.assertEqual(json_res.status_code, 200)
            self.assertIn("application/json", json_res.headers["Content-Type"])
            regions = json_res.json()
            self.assertEqual(len(regions), 1)
            self.assertEqual(regions[0]["text"], "Original Text")

        # Bbox images are rendered by the frontend overlay and are not stored.
        bbox_res = self.client.get(f"{prefix}/result/folder_alpha_1/bboxes_unfiltered.png")
        self.assertEqual(bbox_res.status_code, 404)

            # GET bboxes.png (dynamically synthesized)
        bboxes_res = self.client.get(f"{prefix}/result/folder_alpha_1/bboxes.png")
        self.assertEqual(bboxes_res.status_code, 404)

    # ==========================================
    # 5. /result/{folder}/save_edits & /api/result/...
    # ==========================================
    def test_save_edits_endpoint(self):
        new_img = Image.new("RGB", (32, 32), color="red")
        new_bytes = io.BytesIO()
        new_img.save(new_bytes, format="PNG")
        b64_str = f"data:image/png;base64,{base64.b64encode(new_bytes.getvalue()).decode('ascii')}"

        payload = {
            "text_regions": [
                {"id": "bubble_0", "text": "Updated bubble text", "x": 15, "y": 15, "width": 50, "height": 25}
            ],
            "final_image_base64": b64_str
        }

        res = self.client.post("/api/result/folder_alpha_1/save_edits", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "success")

        # Verify on disk
        regions = json.loads((self.results_dir / "folder_alpha_1" / "text_regions.json").read_text(encoding="utf-8"))
        self.assertEqual(regions[0]["text"], "Updated bubble text")

    def test_layout_preview_flows_text_across_linked_segments(self):
        payload = {
            "translation": "One day, we met again.",
            "font_size": 16,
            "minimum_font_size": 8,
            "segments": [
                {"x": 10, "y": 10, "width": 100, "height": 80},
                {"x": 130, "y": 10, "width": 100, "height": 80},
            ],
        }

        res = self.client.post("/api/result/folder_alpha_1/layout-preview", json=payload)

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["fits"])
        self.assertTrue(body["needs_review"])
        self.assertEqual(
            " ".join(segment["text"] for segment in body["layout_segments"]).split(),
            payload["translation"].split(),
        )

    def test_layout_preview_uses_saved_group_shape(self):
        import base64
        import cv2
        import numpy as np
        from manga_translator.rendering.bubble_layout import encode_safe_shape

        folder = self.results_dir / "folder_alpha_1"
        Image.new("RGB", (360, 280), color="white").save(folder / "final.png")
        mask = np.zeros((280, 360), np.uint8)
        cv2.ellipse(mask, (105, 135), (72, 92), 0, 0, 360, 1, -1)
        cv2.ellipse(mask, (255, 135), (72, 92), 0, 0, 360, 1, -1)
        cv2.rectangle(mask, (172, 126), (188, 144), 1, -1)
        interior = cv2.erode(mask, np.ones((7, 7), np.uint8))
        (folder / "text_regions.json").write_text(json.dumps([{
            "id": "bubble_0", "bubble_safe_shape": encode_safe_shape(interior),
        }]), encoding="utf-8")
        payload = {
            "group_id": "bubble_0", "translation": "Today, we are going for a drive.",
            "font_size": 20, "segments": [
                {"x": 55, "y": 76, "width": 101, "height": 118},
                {"x": 205, "y": 76, "width": 101, "height": 118},
            ],
        }

        body = self.client.post("/api/result/folder_alpha_1/layout-preview", json=payload).json()

        self.assertTrue(body["fits"], body)
        self.assertFalse(body["needs_review"])
        self.assertEqual(" ".join(s["text"] for s in body["layout_segments"]), payload["translation"])
        for segment in body["layout_segments"]:
            self.assertTrue(segment["positioned_lines"])
            self.assertEqual(" ".join(line["text"] for line in segment["positioned_lines"]), segment["text"])
            self.assertTrue(segment["rendered_png"])
            raw = np.frombuffer(base64.b64decode(segment["rendered_png"]), np.uint8)
            rendered = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
            x, y = segment["x"], segment["y"]
            safe = interior[y:y + rendered.shape[0], x:x + rendered.shape[1]] > 0
            self.assertFalse(np.any((rendered[:, :, 3] > 0) & ~safe))

    # ==========================================
    # 6. /results/update-meta & /api/results/update-meta
    # ==========================================
    def test_update_meta_endpoint(self):
        # Update by folder list
        res = self.client.post("/api/results/update-meta", json={
            "folders": ["folder_ungrouped"],
            "mangaTitle": "Newly Grouped Series"
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["updated"], 1)

        # Verify folder has new title
        detail = self.client.get("/api/results/folder_ungrouped").json()
        self.assertEqual(detail["mangaTitle"], "Newly Grouped Series")

        # Rename the whole group through the stable group ID in filesystem mode.
        group = next(
            group for group in self.client.get("/api/results/groups").json()["groups"]
            if group["title"] == "Newly Grouped Series"
        )
        group_res = self.client.post("/api/results/update-meta", json={
            "groupId": group["id"],
            "mangaTitle": "Renamed Group",
        })
        self.assertEqual(group_res.status_code, 200)
        self.assertEqual(group_res.json()["updated"], 1)
        self.assertEqual(
            self.client.get("/api/results/folder_ungrouped").json()["mangaTitle"],
            "Renamed Group",
        )

        # Update by old title
        res2 = self.client.post("/results/update-meta", json={
            "oldMangaTitle": "Series Alpha",
            "mangaTitle": "Series Gamma"
        })
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["updated"], 2)

        groups = self.client.get("/api/results/groups").json()
        titles = {g["title"] for g in groups["groups"]}
        self.assertIn("Series Gamma", titles)
        self.assertNotIn("Series Alpha", titles)

    # ==========================================
    # 7. /results/export/cbz & /api/results/export/cbz
    # ==========================================
    def test_export_cbz_endpoints(self):
        # 1. GET export with ?manga=Series Alpha
        res = self.client.get("/api/results/export/cbz?manga=Series Alpha")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("Content-Type"), "application/vnd.comicbook+zip")
        self.assertTrue(res.headers.get("Content-Disposition", "").endswith('.cbz"'))

        with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
            namelist = zf.namelist()
            self.assertIn("ComicInfo.xml", namelist)
            # Should have 2 page images + 1 ComicInfo
            self.assertEqual(len(namelist), 3)

        # 2. POST export with explicit folders
        res_post = self.client.post("/results/export/cbz", json={
            "mangaTitle": "Selected Pages",
            "folders": ["folder_alpha_1"]
        })
        self.assertEqual(res_post.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(res_post.content), "r") as zf:
            namelist = zf.namelist()
            self.assertIn("ComicInfo.xml", namelist)
            self.assertEqual(len(namelist), 2)

    # ==========================================
    # 8. /results/{folder_name} DELETE
    # ==========================================
    def test_delete_result_endpoint(self):
        res = self.client.delete("/api/results/folder_alpha_2")
        self.assertEqual(res.status_code, 200)
        self.assertFalse((self.results_dir / "folder_alpha_2").exists())

        # Repeat delete returns 404
        res404 = self.client.delete("/results/folder_alpha_2")
        self.assertEqual(res404.status_code, 404)

    # ==========================================
    # 9. /results/group DELETE (by ?title=...)
    # ==========================================
    def test_delete_group_endpoint(self):
        res = self.client.delete("/api/results/group?title=Series Alpha")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["deleted"], 2)
        self.assertFalse((self.results_dir / "folder_alpha_1").exists())
        self.assertFalse((self.results_dir / "folder_alpha_2").exists())
        # Ungrouped should remain
        self.assertTrue((self.results_dir / "folder_ungrouped").exists())

    # ==========================================
    # 10. /batches endpoints
    # ==========================================
    def test_batches_endpoints(self):
        # 1. GET /batches
        res = self.client.get("/api/batches")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

        # 2. PUT /batches/{batch_id} (multipart upload)
        manifest = {
            "id": "batch-sync-1",
            "title": "Batch Sync Title",
            "mangaTitle": "Batch Manga",
            "addedAt": 1780000000000,
            "settings": {"translator": "deepseek"},
            "status": "waiting",
            "totalItems": 1,
            "completedCount": 0,
            "items": [
                {
                    "id": "page-1",
                    "name": "page-1.png",
                    "addedAt": 1780000000000,
                    "status": "queued",
                    "requestId": "batch-sync-1:page-1",
                }
            ],
        }

        files = [
            ("manifest", ("manifest.json", json.dumps(manifest), "application/json")),
            ("page-1", ("page-1.png", self.sample_png, "image/png")),
        ]

        put_res = self.client.put("/api/batches/batch-sync-1", files=files)
        self.assertEqual(put_res.status_code, 200)
        batch_data = put_res.json()
        self.assertEqual(batch_data["id"], "batch-sync-1")
        self.assertEqual(batch_data["totalItems"], 1)

        summary_res = self.client.get("/api/batches")
        self.assertEqual(summary_res.status_code, 200)
        summary_data = summary_res.json()[0]
        self.assertNotIn("items", summary_data)
        self.assertEqual(summary_data["queuedCount"], 1)

        detail_res = self.client.get("/api/batches/batch-sync-1")
        self.assertEqual(detail_res.status_code, 200)
        self.assertEqual(len(detail_res.json()["items"]), 1)

        # 3. GET /batches/{batch_id}/items/{item_id}/input
        input_res = self.client.get("/api/batches/batch-sync-1/items/page-1/input")
        self.assertEqual(input_res.status_code, 200)
        self.assertEqual(input_res.content, self.sample_png)

        # 4. PATCH /batches/{batch_id} (update translator)
        patch_res = self.client.patch("/api/batches/batch-sync-1", json={"translator": "sugoi"})
        self.assertEqual(patch_res.status_code, 200)

        priority_res = self.client.patch("/api/batches/batch-sync-1", json={"priority": True})
        self.assertEqual(priority_res.status_code, 200)
        self.assertTrue(priority_res.json()["priority"])

        # 5. POST /batches/{batch_id}/pause
        pause_res = self.client.post("/api/batches/batch-sync-1/pause")
        self.assertEqual(pause_res.status_code, 200)

        # 6. POST /batches/{batch_id}/resume
        resume_res = self.client.post("/api/batches/batch-sync-1/resume")
        self.assertEqual(resume_res.status_code, 200)

        # 7. POST /batches/{batch_id}/items/{item_id}/retry
        retry_res = self.client.post("/api/batches/batch-sync-1/items/page-1/retry")
        self.assertEqual(retry_res.status_code, 200)

        # 8. DELETE /batches/{batch_id}/items/{item_id}
        del_item_res = self.client.delete("/api/batches/batch-sync-1/items/page-1")
        self.assertEqual(del_item_res.status_code, 200)

        # 9. POST /batches/{batch_id}/dismiss
        dismiss_res = self.client.post("/api/batches/batch-sync-1/dismiss")
        self.assertEqual(dismiss_res.status_code, 200)

        # 10. DELETE /batches/{batch_id}
        del_batch_res = self.client.delete("/api/batches/batch-sync-1")
        self.assertEqual(del_batch_res.status_code, 200)

    # ==========================================
    # 12. Server status endpoints
    # ==========================================
    def test_status_endpoints(self):
        for prefix in ["", "/api"]:
            res_status = self.client.get(f"{prefix}/status")
            self.assertEqual(res_status.status_code, 200)

            res_workers = self.client.get(f"{prefix}/workers")
            self.assertEqual(res_workers.status_code, 200)

        # queue-size
        res_q_get = self.client.get("/queue-size")
        self.assertEqual(res_q_get.status_code, 200)
        res_q_post = self.client.post("/queue-size")
        self.assertEqual(res_q_post.status_code, 200)

    # ==========================================
    # 13. /results/clear & /api/results/clear
    # ==========================================
    def test_clear_results_endpoint(self):
        res = self.client.delete("/api/results/clear")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["message"], "Deleted 4 result directories")

        # Verify all directories with final.png were deleted
        remaining = [d for d in self.results_dir.iterdir() if d.is_dir() and (d / "final.png").exists()]
        self.assertEqual(len(remaining), 0)


if __name__ == "__main__":
    unittest.main()
