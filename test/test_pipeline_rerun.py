import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from server.batch_scheduler import BatchScheduler
from server.batch_store import BatchStore
from server.pipeline_rerun import (
    PipelineRerunMode,
    _copy_file_atomic,
    commit_rerun_artifacts,
    execute_rerun_plan,
    load_rerun_context,
    resolve_rerun_plan,
    validate_rerun_prerequisites,
)
from manga_translator.config import Config
from manga_translator.utils import TextBlock


class _MockTranslator:
    def __init__(self):
        self.stages_called = []

    async def _run_detection(self, config: Config, ctx):
        self.stages_called.append("detection")
        return [
            TextBlock([[[1, 1], [7, 1], [7, 7], [1, 7]]], texts=["re-detected hello"])
        ], np.zeros((8, 8), dtype=np.uint8), np.zeros((8, 8), dtype=np.uint8)

    async def _run_ocr(self, config: Config, ctx):
        self.stages_called.append("ocr")
        return [
            TextBlock([[[1, 1], [7, 1], [7, 7], [1, 7]]], texts=["ocr hello"])
        ]

    async def _run_textline_merge(self, config: Config, ctx):
        self.stages_called.append("textline_merge")
        return [
            TextBlock([[[1, 1], [7, 1], [7, 7], [1, 7]]], texts=["ocr hello"])
        ]

    async def _detect_speech_bubbles(self, config: Config, ctx):
        self.stages_called.append("speech_bubble_detection")
        ctx.bubble_detections = []
        return []

    async def _run_inpainting(self, config: Config, ctx):
        self.stages_called.append("inpainting")
        return np.ones((8, 8, 3), dtype=np.uint8) * 255

    async def _run_text_translation(self, config: Config, ctx):
        self.stages_called.append("text_translation")
        for region in ctx.text_regions:
            region.translation = f"translated {region.text}"
        return ctx.text_regions

    async def _run_text_rendering(self, config: Config, ctx):
        self.stages_called.append("text_rendering")
        rendered = np.zeros((8, 8, 3), dtype=np.uint8)
        rendered[:, :, 2] = 254  # Blue
        return rendered


class PipelineRerunTest(unittest.IsolatedAsyncioTestCase):
    def test_atomic_copy_keeps_last_good_target_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "new.bin"
            target = Path(root) / "result.bin"
            source.write_bytes(b"replacement")
            target.write_bytes(b"last good")

            with patch("server.pipeline_rerun.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    _copy_file_atomic(source, target)

            self.assertEqual(target.read_bytes(), b"last good")
            self.assertEqual([path.name for path in Path(root).glob(".*.tmp")], [])

    async def test_rerun_activates_versioned_image_and_document_together(self):
        with tempfile.TemporaryDirectory() as root:
            result_dir = Path(root) / "page-1"
            staging_dir = Path(root) / "staging"
            result_dir.mkdir()
            staging_dir.mkdir()
            Image.new("RGB", (4, 3), "red").save(result_dir / "final.jpg")
            Image.new("RGB", (4, 3), "blue").save(staging_dir / "final.jpg", format="JPEG")
            (staging_dir / "text_regions.json").write_text("[]", encoding="utf-8")

            class Database:
                committed = None

                async def get_page_id(self, _folder):
                    return "page-id"

                async def commit_pipeline_outputs(self, _folder, documents, artifacts):
                    self.committed = (documents, artifacts)
                    return True

            database = Database()
            await commit_rerun_artifacts(
                result_dir,
                staging_dir,
                resolve_rerun_plan(PipelineRerunMode.TYPESETTING),
                database=database,
            )

            documents, artifacts = database.committed
            self.assertIn("text_regions.json", documents)
            self.assertEqual(artifacts[0]["artifact_type"], "final_image")
            versioned = Path(root) / artifacts[0]["relative_path"]
            self.assertTrue(versioned.is_file())
            with Image.open(versioned) as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 254))
            with Image.open(result_dir / "final.jpg") as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 254))

    def setUp(self):
        self.translator = _MockTranslator()
        self.config = Config()

    def _create_sample_folder(self, folder: Path, with_all: bool = True):
        folder.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "gray").save(folder / "input.png")
        Image.new("RGB", (8, 8), "red").save(folder / "final.jpg")
        if with_all:
            Image.new("RGB", (8, 8), "white").save(folder / "inpainted.jpg")
            Image.new("RGB", (8, 8), "black").save(folder / "mask.png")
            (folder / "text_regions.json").write_text(json.dumps([{
                "id": "bubble-1",
                "lines": [[[1, 1], [7, 1], [7, 7], [1, 7]]],
                "original_text": "sample text",
                "translation": "sample translation",
                "font_size": 14,
                "font_family": "Comic Neue",
                "fg_color": [0, 0, 0],
                "bg_color": [255, 255, 255],
                "alignment": "center",
            }]), encoding="utf-8")
            (folder / "bubbles.json").write_text(json.dumps([
                {"id": "b1", "box": [0, 0, 8, 8]}
            ]), encoding="utf-8")

    async def test_validate_prerequisites(self):
        with tempfile.TemporaryDirectory() as root:
            results_dir = Path(root)
            folder = results_dir / "page-1"
            
            # Nonexistent folder
            ok, err = validate_rerun_prerequisites(folder, PipelineRerunMode.TYPESETTING)
            self.assertFalse(ok)
            self.assertIn("missing", err)

            # Folder with only original
            self._create_sample_folder(folder, with_all=False)
            # Typesetting requires text regions
            ok, err = validate_rerun_prerequisites(folder, PipelineRerunMode.TYPESETTING)
            self.assertFalse(ok)
            self.assertIn("text regions", err)

            # Full only requires original
            ok, err = validate_rerun_prerequisites(folder, PipelineRerunMode.FULL)
            self.assertTrue(ok)
            self.assertIsNone(err)

            # Add missing files
            self._create_sample_folder(folder, with_all=True)
            ok, _ = validate_rerun_prerequisites(folder, PipelineRerunMode.TYPESETTING)
            self.assertTrue(ok)
            ok, _ = validate_rerun_prerequisites(folder, PipelineRerunMode.TRANSLATION_TYPESETTING)
            self.assertTrue(ok)
            ok, _ = validate_rerun_prerequisites(folder, PipelineRerunMode.REPROCESS_TEXT)
            self.assertTrue(ok)

    async def test_typesetting_rerun(self):
        with tempfile.TemporaryDirectory() as root:
            results_dir = Path(root)
            folder = results_dir / "page-1"
            self._create_sample_folder(folder, with_all=True)

            plan = resolve_rerun_plan(PipelineRerunMode.TYPESETTING)
            self.assertTrue(plan.run_rendering)
            self.assertFalse(plan.run_translation)
            self.assertFalse(plan.run_inpainting)

            staging_dir = results_dir / ".rerun" / "job-1"
            staging_dir.mkdir(parents=True, exist_ok=True)
            ctx, state = await load_rerun_context(folder, plan, self.config)
            executed_ctx, remap_result = await execute_rerun_plan(
                translator=self.translator,
                ctx=ctx,
                config=self.config,
                plan=plan,
                state=state,
                staging_dir=staging_dir,
            )
            self.assertIn("text_rendering", self.translator.stages_called)
            self.assertNotIn("text_translation", self.translator.stages_called)
            self.assertNotIn("inpainting", self.translator.stages_called)

            await commit_rerun_artifacts(folder, staging_dir, plan, remap_result)
            with Image.open(folder / "final.jpg") as final:
                self.assertEqual(final.getpixel((0, 0)), (0, 0, 254))

    async def test_full_rerun_stages_output_before_replacing_live_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            results_dir = Path(root)
            folder = results_dir / "page-1"
            self._create_sample_folder(folder, with_all=True)
            staging_dir = folder / ".rerun-full"
            staging_dir.mkdir()
            plan = resolve_rerun_plan(PipelineRerunMode.FULL)
            ctx, state = await load_rerun_context(folder, plan, self.config)

            class FullTranslator:
                def __init__(self):
                    self._pipeline_run = object()
                    self._current_image_context = {"subfolder": "page-1"}
                    self._result_path_override = None
                    self.verbose = False

                def _result_path(self, name):
                    return str(Path(self._result_path_override) / name)

                async def _translate(self, _config, context):
                    Image.new("RGB", (8, 8), "blue").save(self._result_path("final.jpg"))
                    context.result = Image.new("RGB", (8, 8), "blue")
                    context.text_regions = [TextBlock(
                        [[[1, 1], [7, 1], [7, 7], [1, 7]]], texts=["fresh text"]
                    )]
                    context.result_documents = {"ocr.json": [{"text": "fresh text"}]}
                    return context

            translator = FullTranslator()
            previous_run = translator._pipeline_run
            executed_ctx, _ = await execute_rerun_plan(
                translator, ctx, self.config, plan, state, staging_dir
            )

            self.assertEqual(translator._pipeline_run, previous_run)
            self.assertFalse(translator.verbose)
            with Image.open(folder / "final.jpg") as live:
                self.assertGreater(live.getpixel((0, 0))[0], live.getpixel((0, 0))[2])
            with Image.open(staging_dir / "final.jpg") as staged:
                self.assertGreater(staged.getpixel((0, 0))[2], staged.getpixel((0, 0))[0])
            self.assertTrue((staging_dir / "translations.json").is_file())
            self.assertEqual(executed_ctx.debug_folder, "page-1")

            await commit_rerun_artifacts(folder, staging_dir, plan)
            with Image.open(folder / "final.jpg") as final:
                self.assertGreater(final.getpixel((0, 0))[2], final.getpixel((0, 0))[0])

    async def test_translation_typesetting_rerun(self):
        with tempfile.TemporaryDirectory() as root:
            results_dir = Path(root)
            folder = results_dir / "page-1"
            self._create_sample_folder(folder, with_all=True)

            plan = resolve_rerun_plan(PipelineRerunMode.TRANSLATION_TYPESETTING)
            staging_dir = results_dir / ".rerun" / "job-2"
            staging_dir.mkdir(parents=True, exist_ok=True)
            ctx, state = await load_rerun_context(folder, plan, self.config)
            executed_ctx, remap_result = await execute_rerun_plan(
                translator=self.translator,
                ctx=ctx,
                config=self.config,
                plan=plan,
                state=state,
                staging_dir=staging_dir,
            )
            self.assertIn("text_translation", self.translator.stages_called)
            self.assertIn("text_rendering", self.translator.stages_called)
            self.assertNotIn("inpainting", self.translator.stages_called)

            await commit_rerun_artifacts(folder, staging_dir, plan, remap_result)
            regions = json.loads((folder / "text_regions.json").read_text(encoding="utf-8"))
            self.assertEqual(regions[0]["translation"], "translated sample text")

    async def test_reprocess_text_rerun_with_remapping(self):
        with tempfile.TemporaryDirectory() as root:
            results_dir = Path(root)
            folder = results_dir / "page-1"
            self._create_sample_folder(folder, with_all=True)

            plan = resolve_rerun_plan(PipelineRerunMode.REPROCESS_TEXT)
            staging_dir = results_dir / ".rerun" / "job-3"
            staging_dir.mkdir(parents=True, exist_ok=True)
            ctx, state = await load_rerun_context(folder, plan, self.config)
            executed_ctx, remap_result = await execute_rerun_plan(
                translator=self.translator,
                ctx=ctx,
                config=self.config,
                plan=plan,
                state=state,
                staging_dir=staging_dir,
            )
            self.assertIn("detection", self.translator.stages_called)
            self.assertIn("ocr", self.translator.stages_called)
            self.assertIn("inpainting", self.translator.stages_called)
            self.assertIn("text_rendering", self.translator.stages_called)
            self.assertIsNotNone(remap_result)

            await commit_rerun_artifacts(folder, staging_dir, plan, remap_result)
            regions = json.loads((folder / "text_regions.json").read_text(encoding="utf-8"))
            self.assertEqual(regions[0]["translation"], "sample translation")
            self.assertEqual(regions[0]["translation_source"], "remapped")
            self.assertIsNotNone(regions[0]["translation_remap"])

    async def test_batch_scheduler_pipeline_rerun(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            results = root / "results"
            folder = results / "page-1"
            self._create_sample_folder(folder, with_all=True)

            store = BatchStore(root / "batches", results)
            batch = await store.put_batch(
                "rerun-batch-1",
                {
                    "id": "rerun-batch-1",
                    "kind": "pipeline-rerun",
                    "title": "Pipeline Rerun Test",
                    "settings": {"rerun_mode": "typesetting"},
                    "items": [{
                        "id": "item-1",
                        "name": "page.png",
                        "pageId": "page-1",
                        "resultFolder": "page-1",
                        "settings": {"renderer": "default"},
                        "status": "queued",
                    }],
                },
                {},
            )
            self.assertEqual(batch["kind"], "pipeline-rerun")

            class _ExecutorsWrapper:
                def __init__(self, translator):
                    class Worker:
                        def __init__(self, wrapped_translator):
                            self.translator = wrapped_translator

                        async def _run_translation(self, operation):
                            return await operation()

                    self.worker = Worker(translator)

                def free_executors(self):
                    return 1

                async def find_executor(self):
                    return self.worker

                async def free_executor(self, _worker):
                    return None

            executors = _ExecutorsWrapper(self.translator)
            scheduler = BatchScheduler(store, executors, results)
            claimed = await scheduler._claim_pipeline_rerun_item("rerun-batch-1", "item-1")
            self.assertIsNotNone(claimed)
            await scheduler._process_pipeline_rerun_item("rerun-batch-1", "item-1", executors.worker)

            batch_state = await store.get_batch("rerun-batch-1")
            self.assertEqual(batch_state["status"], "completed")
            with Image.open(folder / "final.jpg") as final:
                self.assertEqual(final.getpixel((0, 0)), (0, 0, 254))

    async def test_failure_isolation_rolls_back_without_mutating_live_folder(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            results = root / "results"
            folder = results / "page-1"
            self._create_sample_folder(folder, with_all=True)

            store = BatchStore(root / "batches", results)
            await store.put_batch(
                "rerun-fail-batch",
                {
                    "id": "rerun-fail-batch",
                    "kind": "pipeline-rerun",
                    "title": "Failing Test",
                    "settings": {"rerun_mode": "typesetting"},
                    "items": [{
                        "id": "item-2",
                        "name": "page.png",
                        "pageId": "page-1",
                        "resultFolder": "page-1",
                        "settings": {},
                        "status": "queued",
                    }],
                },
                {},
            )

            class _FailingTranslator:
                async def _run_text_rendering(self, config, ctx):
                    raise RuntimeError("simulated rendering explosion")

            class _ExecutorsWrapper:
                def __init__(self, translator):
                    self.worker = translator

                def free_executors(self):
                    return 1

                async def find_executor(self):
                    return self.worker

                async def free_executor(self, _worker):
                    return None

            failing_scheduler = BatchScheduler(store, _ExecutorsWrapper(_FailingTranslator()), results)
            await failing_scheduler._claim_pipeline_rerun_item("rerun-fail-batch", "item-2")
            await failing_scheduler._process_pipeline_rerun_item("rerun-fail-batch", "item-2", failing_scheduler.executors.worker)

            # Live final image must be untouched (red)
            with Image.open(folder / "final.jpg") as final:
                self.assertEqual(final.getpixel((0, 0)), (254, 0, 0))

            # Batch must be marked error
            batch_state = await store.get_batch("rerun-fail-batch")
            self.assertEqual(batch_state["status"], "error")
            self.assertIn("simulated rendering explosion", batch_state["items"][0]["error"])

            # Staging folders must have been cleaned up
            staging_dirs = list(folder.glob(".rerun-*"))
            self.assertEqual(len(staging_dirs), 0)


if __name__ == "__main__":
    unittest.main()
