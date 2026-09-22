import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from manga_translator.config import Config, PipelineLabConfig
from manga_translator.manga_translator import MangaTranslator
from manga_translator.pipeline_lab import PipelineLabRun, set_document_saver
from manga_translator.utils import Context, TextBlock, Quadrilateral
import server.main as sm
from starlette.testclient import TestClient


class TestPipelineTiming(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="manga_pipeline_timing_test_")
        self.root_path = Path(self.temp_dir).resolve()
        self.results_dir = self.root_path / "result"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.orig_result_root = sm.RESULT_ROOT
        sm.RESULT_ROOT = self.results_dir
        sm._invalidate_meta_cache()

    def tearDown(self):
        sm.RESULT_ROOT = self.orig_result_root
        set_document_saver(None)

    def test_pipeline_lab_run_stage_durations_and_detail(self):
        saved_docs = {}
        async def mock_save(folder, docs):
            saved_docs[folder] = docs

        set_document_saver(mock_save)

        img = Image.new("RGB", (100, 100))
        config = Config(original_name="test_page.png")
        run = PipelineLabRun(self.results_dir, "run_timing_1", img, config)
        
        # Simulate stages
        run.progress("detection")
        run.progress("ocr")
        run.progress("textline_merge")
        run.progress("translating")
        
        ctx = Context()
        ctx.translator_model = "deepseek-chat"
        ctx.translation_duration_ms = 1250
        ctx.translation_started_at = "2026-09-20T10:00:00.000Z"
        ctx.translation_finished_at = "2026-09-20T10:00:01.250Z"
        
        run.record_translation(
            config,
            [{"index": 0, "text": "こんにちは"}],
            [{"index": 0, "translation": "Hello"}],
            ctx,
        )
        
        run.progress("mask-generation")
        run.progress("inpainting")
        run.progress("rendering")
        run.progress("finished", True)
        
        asyncio.run(run.checkpoint())

        manifest = saved_docs["run_timing_1"]["pipeline_manifest.json"]
        self.assertEqual(manifest["status"], "completed")
        self.assertTrue(manifest.get("createdAt"))
        self.assertTrue(manifest.get("updatedAt"))

        # Verify stages
        stage_map = {s["id"]: s for s in manifest["stages"]}
        self.assertIn("detection", stage_map)
        self.assertIn("ocr", stage_map)
        self.assertIn("textline_merge", stage_map)
        self.assertIn("translation", stage_map)
        self.assertIn("rendering", stage_map)
        
        # Verify translation detail document
        detail = saved_docs["run_timing_1"]["translation_detail.json"]
        self.assertEqual(detail["translator"]["name"], "deepseek")
        self.assertEqual(detail["translator"]["model"], "deepseek-chat")
        self.assertEqual(detail["durationMs"], 1250)
        self.assertEqual(detail["startedAt"], "2026-09-20T10:00:00.000Z")
        self.assertEqual(detail["finishedAt"], "2026-09-20T10:00:01.250Z")

    def test_meta_manifest_fallback_api(self):
        # Create a result folder without pipeline_manifest.json, but with meta.json
        folder_dir = self.results_dir / "folder_legacy_1"
        folder_dir.mkdir(parents=True, exist_ok=True)
        
        meta = {
            "originalName": "legacy_page.png",
            "mangaTitle": "Test Manga",
            "startedAt": "2026-09-20T12:00:00.000Z",
            "finishedAt": "2026-09-20T12:00:05.500Z",
            "durationMs": 5500,
            "settings": {
                "translator": "gemini",
                "geminiModel": "gemini-1.5-flash",
            }
        }
        (folder_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (folder_dir / "final.png").write_bytes(b"final")

        client = TestClient(sm.app)
        res = client.get("/api/pipeline-lab/runs/folder_legacy_1/manifest")
        self.assertEqual(res.status_code, 200)
        
        data = res.json()
        self.assertEqual(data["folder"], "folder_legacy_1")
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["createdAt"], "2026-09-20T12:00:00.000Z")
        self.assertEqual(data["updatedAt"], "2026-09-20T12:00:05.500Z")
        self.assertEqual(data["source"]["filename"], "legacy_page.png")
        self.assertEqual(data["stages"][0]["durationMs"], 5500)

    def test_manga_translator_prepare_timestamps(self):
        translator = object.__new__(MangaTranslator)
        translator._progress_hooks = []
        translator.verbose = False
        translator.disable_memory_optimization = True
        translator.device = "cpu"
        translator.result_root = str(self.results_dir)
        translator._current_image_context = {'file_md5': 'mockmd5', 'subfolder': 'test_subfolder'}
        translator._get_image_subfolder = lambda: "test_subfolder"
        translator._set_image_context = lambda cfg, img: None
        translator._save_current_image_context = lambda md5: None

        async def fake_step_1(image, config, prepare_canvas=True):
            ctx = Context()
            ctx.text_regions = []
            return ctx

        translator._translate_until_translation = fake_step_1

        async def fake_report(state):
            pass

        translator._report_progress = fake_report

        img = Image.new("RGB", (50, 50))
        config = Config()
        ctx = asyncio.run(translator.prepare(img, config))
        self.assertIsNotNone(ctx.image_context)
        self.assertTrue(ctx.image_context.get("started_at"))

    def test_manga_translator_prepare_textless_releases_run_safely(self):
        translator = object.__new__(MangaTranslator)
        translator._progress_hooks = []
        translator.verbose = False
        translator.disable_memory_optimization = True
        translator.device = "cpu"
        translator.result_root = str(self.results_dir)
        translator._current_image_context = {'file_md5': 'mockmd5', 'subfolder': 'test_subfolder'}
        translator._get_image_subfolder = lambda: "test_subfolder"
        translator._set_image_context = lambda cfg, img: None
        translator._save_current_image_context = lambda md5: None

        async def fake_step_1_textless(image, config, prepare_canvas=True):
            # Simulate pipeline finishing and releasing _pipeline_lab_run in _revert_upscale
            if translator._pipeline_lab_run is not None:
                translator._pipeline_lab_run.release_runtime()
                translator._pipeline_lab_run = None
            ctx = Context()
            ctx.text_regions = []
            return ctx

        translator._translate_until_translation = fake_step_1_textless

        async def fake_report(state):
            pass

        translator._report_progress = fake_report

        img = Image.new("RGB", (50, 50))
        config = Config()
        ctx = asyncio.run(translator.prepare(img, config))
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.debug_folder, "test_subfolder")
        self.assertIsNone(translator._pipeline_lab_run)


    def test_manga_translator_revert_upscale_without_monotonic(self):
        translator = object.__new__(MangaTranslator)
        translator._progress_hooks = []
        translator.verbose = True
        translator.result_sub_folder = "test_subfolder"
        translator.result_root = str(self.results_dir)
        translator._pipeline_lab_run = None
        translator._current_image_context = {'file_md5': 'mockmd5', 'subfolder': 'test_subfolder'}
        translator._get_image_subfolder = lambda: "test_subfolder"
        translator._result_path = lambda filename: str(self.results_dir / filename)

        ctx = Context()
        ctx.input = Image.new("RGB", (50, 50))
        ctx.result = Image.new("RGB", (50, 50))
        # started_at_monotonic is NOT set on ctx (evaluates to None)
        ctx.started_at_iso = "2026-09-20T10:00:00.000Z"
        config = Config()

        res_ctx = asyncio.run(translator._revert_upscale(config, ctx))
        self.assertIsNotNone(res_ctx)


if __name__ == "__main__":
    unittest.main()
