import json
import asyncio
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

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
    run_temporary_pipeline_case,
    validate_rerun_prerequisites,
)
from manga_translator.config import Config
from manga_translator.pipeline.serialization import serialize_editor_regions
from manga_translator.utils import TextBlock


class TemporaryPipelineCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_temporary_case_returns_render_and_removes_copied_files(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "page"
            source.mkdir()
            (source / "input.jpg").write_bytes(b"source image")
            (source / "text_regions.json").write_text("[]", encoding="utf-8")
            (source / "unneeded.bin").write_bytes(b"large unrelated data")
            created_dirs = []
            real_temporary_directory = tempfile.TemporaryDirectory
            copied_names = []

            def track_tempdir(*args, **kwargs):
                tempdir = real_temporary_directory(*args, **kwargs)
                created_dirs.append(Path(tempdir.name))
                return tempdir

            async def write_render(**kwargs):
                output = kwargs["staging_dir"]
                (output / "final.jpg").write_bytes(b"render")
                (output / "layout.json").write_text('{"regions": []}', encoding="utf-8")
                return None

            async def load_case(result_dir, *_args, **_kwargs):
                copied_names.extend(path.name for path in result_dir.iterdir())
                return object(), {}

            class Instance:
                translator = object()

                async def _run_translation(self, operation):
                    return await operation()

            with patch("server.pipeline_rerun.tempfile.TemporaryDirectory", side_effect=track_tempdir), \
                    patch("server.pipeline_rerun.load_rerun_context", new=AsyncMock(side_effect=load_case)), \
                    patch("server.pipeline_rerun.execute_rerun_plan", new=AsyncMock(side_effect=write_render)):
                result = await run_temporary_pipeline_case(
                    source_dir=source,
                    documents={},
                    replacements={},
                    mode="typesetting",
                    settings={},
                    page={"id": "source-page", "originalName": "page.jpg"},
                    instance=Instance(),
                )

            self.assertEqual(result["image"], b"render")
            self.assertEqual(result["artifacts"], {"layout.json": {"regions": []}})
            self.assertEqual(len(created_dirs), 1)
            self.assertFalse(created_dirs[0].exists())
            self.assertCountEqual(copied_names, ["input.jpg", "text_regions.json"])
            self.assertEqual((source / "text_regions.json").read_text(), "[]")


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

    async def test_typesetting_restores_source_font_and_region_id_from_legacy_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "page-1"
            folder.mkdir()
            Image.new("RGB", (20, 20), "white").save(folder / "input.png")
            region_id = "c202044208fb45d992475e2269c413b8"
            lines = [[[2, 2], [12, 2], [12, 12], [2, 12]]]
            (folder / "text_regions.json").write_text(json.dumps([{
                "id": region_id,
                "x": 2, "y": 2, "width": 10, "height": 10,
                "lines": lines,
                "original_text": "source",
                "translation": "translated",
                "font_size": 44,
            }]), encoding="utf-8")
            (folder / "translations.json").write_text(json.dumps([{
                "region_id": region_id,
                "group_id": region_id,
                "lines": lines,
                "text": "source",
                "translation": "translated",
                "font_size": 25,
            }]), encoding="utf-8")

            ctx, _ = await load_rerun_context(
                folder, resolve_rerun_plan(PipelineRerunMode.TYPESETTING), self.config
            )

            assert ctx.text_regions[0].region_id == region_id
            assert ctx.text_regions[0].source_font_size == 25

    async def test_typesetting_restores_saved_bubble_masks_without_associating_unmatched_text(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "page-1"
            folder.mkdir()
            Image.new("RGB", (40, 40), "white").save(folder / "input.png")
            regions = [
                {
                    "id": "inside",
                    "lines": [[[5, 5], [12, 5], [12, 12], [5, 12]]],
                    "original_text": "bubble text",
                    "translation": "Inside bubble",
                    "font_size": 12,
                },
                {
                    "id": "outside",
                    "lines": [[[25, 25], [33, 25], [33, 33], [25, 33]]],
                    "original_text": "free text",
                    "translation": "Outside bubble",
                    "font_size": 12,
                },
            ]
            (folder / "text_regions.json").write_text(json.dumps(regions), encoding="utf-8")
            (folder / "bubble_detections.json").write_text(json.dumps([{
                "confidence": 0.9,
                "polygon": [[2, 2], [20, 2], [20, 20], [2, 20]],
            }]), encoding="utf-8")

            ctx, _ = await load_rerun_context(
                folder, resolve_rerun_plan(PipelineRerunMode.TYPESETTING), self.config
            )

            inside, outside = ctx.text_regions
            self.assertEqual(inside.bubble_id, "bubble_0")
            self.assertTrue(np.any(inside._bubble_mask))
            self.assertIsNone(getattr(outside, "_bubble_mask", None))
            self.assertIsNone(getattr(outside, "bubble_id", None))

    def test_editor_artifact_keeps_source_font_and_region_identity(self):
        region = TextBlock(
            [[[1, 1], [8, 1], [8, 8], [1, 8]]],
            texts=["source"], translation="translated", font_size=44,
            region_id="stable-region",
        )
        region.group_id = "stable-group"
        region.source_font_size = 25

        saved = serialize_editor_regions([region])[0]

        assert saved["region_id"] == "stable-region"
        assert saved["source_font_size"] == 25

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

    async def test_isolated_case_rerun_skips_source_database_and_gallery_index(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            results = root / "results"
            source = results / "page-1"
            case = results / "ai-case-1"
            self._create_sample_folder(source, with_all=True)
            shutil.copytree(source, case)
            (case / ".ai-case").touch()

            store = BatchStore(root / "batches", results)
            database_calls = []

            class Database:
                async def invalidate_pipeline_stages(self, *args):
                    database_calls.append("invalidate")

                async def start_pipeline_stage(self, *args, **kwargs):
                    database_calls.append("start")

                async def finish_pipeline_stage(self, *args, **kwargs):
                    database_calls.append("finish")

            store.database = Database()

            async def register_result(*args, **kwargs):
                database_calls.append("index")

            store.register_result = register_result
            await store.put_batch(
                "isolated-case-batch",
                {
                    "id": "isolated-case-batch",
                    "kind": "pipeline-rerun",
                    "rerunMode": "typesetting",
                    "items": [{
                        "id": "case-item",
                        "name": "page.png",
                        "resultFolder": "ai-case-1",
                        "settings": {},
                        "isolatedRerun": True,
                        "status": "queued",
                    }],
                },
                {},
            )

            class _ExecutorsWrapper:
                worker = self.translator

                def free_executors(self):
                    return 1

                async def free_executor(self, _worker):
                    return None

            scheduler = BatchScheduler(store, _ExecutorsWrapper(), results)
            await scheduler._claim_pipeline_rerun_item("isolated-case-batch", "case-item")
            await scheduler._process_pipeline_rerun_item(
                "isolated-case-batch", "case-item", self.translator
            )

            self.assertEqual(database_calls, [])
            with Image.open(source / "final.jpg") as original:
                self.assertEqual(original.getpixel((0, 0)), (254, 0, 0))
            with Image.open(case / "final.jpg") as rerun:
                self.assertEqual(rerun.getpixel((0, 0)), (0, 0, 254))

    async def test_rerun_progress_mutates_postgres_store_on_scheduler_loop(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            results = root / "results"
            case = results / "ai-case-loop"
            self._create_sample_folder(case, with_all=True)
            (case / ".ai-case").touch()
            store = BatchStore(root / "batches", results)
            scheduler_loop = asyncio.get_running_loop()
            mutate = store.mutate

            async def loop_bound_mutate(*args, **kwargs):
                if asyncio.get_running_loop() is not scheduler_loop:
                    raise RuntimeError("batch store used from the wrong event loop")
                return await mutate(*args, **kwargs)

            store.mutate = loop_bound_mutate
            await store.put_batch(
                "loop-bound-case",
                {
                    "id": "loop-bound-case", "kind": "pipeline-rerun", "rerunMode": "typesetting",
                    "items": [{
                        "id": "case-item", "name": "page.png", "resultFolder": case.name,
                        "settings": {}, "isolatedRerun": True, "status": "queued",
                    }],
                },
                {},
            )

            class Worker:
                def __init__(self, translator):
                    self.translator = translator
                    self.loop = asyncio.new_event_loop()
                    self.thread = threading.Thread(target=self._run_loop, daemon=True)
                    self.thread.start()

                def _run_loop(self):
                    asyncio.set_event_loop(self.loop)
                    self.loop.run_forever()

                async def _run_translation(self, operation):
                    future = asyncio.run_coroutine_threadsafe(operation(), self.loop)
                    return await asyncio.wrap_future(future)

                async def free_executor(self, _worker):
                    return None

                def free_executors(self):
                    return 1

                def close(self):
                    self.loop.call_soon_threadsafe(self.loop.stop)
                    self.thread.join()
                    self.loop.close()

            worker = Worker(self.translator)
            scheduler = BatchScheduler(store, worker, results)
            try:
                await scheduler._claim_pipeline_rerun_item("loop-bound-case", "case-item")
                await scheduler._process_pipeline_rerun_item("loop-bound-case", "case-item", worker)
                batch = await store.get_batch("loop-bound-case")
                self.assertEqual(batch["status"], "completed", batch["items"][0].get("error"))
            finally:
                worker.close()

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
