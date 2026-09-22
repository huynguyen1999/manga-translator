import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from PIL import Image

from server.batch_scheduler import BatchScheduler
from server.batch_store import BatchStore


class BatchSchedulerMemoryTest(unittest.IsolatedAsyncioTestCase):
    def test_group_progress_only_updates_current_page_outside_translation(self):
        translator = SimpleNamespace(_current_image_context={"request_id": "manga-a:page-2"})
        item_ids = ["page-1", "page-2", "page-3"]

        self.assertEqual(
            BatchScheduler._progress_item_ids("detection", item_ids, translator),
            ["page-2"],
        )
        self.assertEqual(
            BatchScheduler._progress_item_ids("translating", item_ids, translator),
            ["page-1"],
        )

    async def test_group_reserves_n_but_marks_only_one_item_processing(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            items = [{"id": f"page-{index}", "name": f"{index}.png"} for index in range(1, 6)]
            files = {item["id"]: (item["name"], image.getvalue()) for item in items}
            await store.put_batch("manga-a", {"id": "manga-a", "items": items}, files)
            scheduler = BatchScheduler(store, None, workspace / "results")

            claimed = await scheduler._claim_items("manga-a", 5)
            batch = await store.get_batch("manga-a")

            self.assertEqual(len(claimed), 5)
            self.assertEqual(sum(item["status"] == "processing" for item in batch["items"]), 1)
            self.assertEqual(sum(item["stage"] == "reserved" for item in batch["items"]), 4)

    async def test_active_item_preserves_awaiting_translation_stage(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            items = [{"id": f"page-{index}", "name": f"{index}.png"} for index in range(1, 4)]
            files = {item["id"]: (item["name"], image.getvalue()) for item in items}
            await store.put_batch("manga-a", {"id": "manga-a", "items": items}, files)
            scheduler = BatchScheduler(store, None, workspace / "results")
            item_ids = [item["id"] for item in items]

            # Page 1 finishes inpainting and reaches awaiting_translation
            await scheduler._set_active_group_item("manga-a", item_ids, "page-1", "awaiting_translation")
            batch = await store.get_batch("manga-a")
            self.assertEqual(batch["items"][0]["stage"], "awaiting_translation")

            # Page 2 starts detection
            await scheduler._set_active_group_item("manga-a", item_ids, "page-2", "detection")
            batch = await store.get_batch("manga-a")
            self.assertEqual(batch["items"][0]["stage"], "awaiting_translation")
            self.assertEqual(batch["items"][0]["status"], "processing")
            self.assertEqual(batch["items"][1]["stage"], "detection")
            self.assertEqual(batch["items"][1]["status"], "processing")

            # Group enters translating
            await scheduler._set_group_stage("manga-a", item_ids, "translating")
            batch = await store.get_batch("manga-a")
            self.assertEqual(batch["items"][0]["stage"], "translating")
            self.assertEqual(batch["items"][1]["stage"], "translating")
            self.assertEqual(batch["items"][2]["stage"], "translating")

    async def test_paused_batch_does_not_block_later_waiting_batch(self):
        store = SimpleNamespace(
            list_batches=AsyncMock(
                return_value=[
                    {"id": "paused", "status": "paused", "items": [{"status": "queued"}]},
                    {"id": "waiting", "status": "waiting", "items": [{"status": "queued"}]},
                ]
            )
        )

        class Executors:
            def free_executors(self):
                return 1

            async def find_executor(self):
                return object()

            async def free_executor(self, _instance):
                pass

        scheduler = BatchScheduler(store, Executors(), tempfile.gettempdir())
        scheduler._claim_item = AsyncMock(return_value={"id": "item-1"})
        scheduler._process_item = AsyncMock()

        self.assertTrue(await scheduler._launch_available())
        await asyncio.sleep(0)
        scheduler._process_item.assert_awaited_once_with("waiting", "item-1", ANY)

        await asyncio.gather(*scheduler._running.values(), return_exceptions=True)

    async def test_scheduler_does_not_launch_second_group_for_same_batch_concurrently(self):
        store = SimpleNamespace(
            list_batches=AsyncMock(
                return_value=[
                    {"id": "active-batch", "status": "processing", "items": [{"status": "queued"}], "settings": {}},
                ]
            ),
            mutate=AsyncMock(),
        )

        class Executors:
            def free_executors(self):
                return 2

            async def find_executor(self):
                return object()

            async def free_executor(self, _instance):
                pass

        scheduler = BatchScheduler(store, Executors(), tempfile.gettempdir())
        # Simulate active task already running for "active-batch"
        scheduler._running[("active-batch", "group-1")] = asyncio.create_task(asyncio.sleep(10))

        self.assertFalse(await scheduler._launch_available())

        # Cleanup running dummy task
        for task in scheduler._running.values():
            task.cancel()
        await asyncio.gather(*scheduler._running.values(), return_exceptions=True)

    async def test_scheduler_prefers_list_runnable_batches_over_list_batches(self):
        store = SimpleNamespace(
            list_runnable_batches=AsyncMock(
                return_value=[
                    {"id": "waiting", "status": "waiting", "items": [{"status": "queued"}]},
                ]
            ),
            list_batches=AsyncMock(return_value=[]),
        )

        class Executors:
            def free_executors(self):
                return 1

            async def find_executor(self):
                return object()

            async def free_executor(self, _instance):
                pass

        scheduler = BatchScheduler(store, Executors(), tempfile.gettempdir())
        scheduler._claim_item = AsyncMock(return_value={"id": "item-1"})
        scheduler._process_item = AsyncMock()

        self.assertTrue(await scheduler._launch_available())
        store.list_runnable_batches.assert_awaited_once()
        store.list_batches.assert_not_called()
        await asyncio.gather(*scheduler._running.values(), return_exceptions=True)

    def test_batch_jobs_keep_failed_regions_for_review_by_default(self):
        config = BatchScheduler._config_for(
            {"settings": {}},
            {"id": "page-1", "name": "1.png"},
        )

        self.assertTrue(config.translator.keep_failed_pages_for_editing)

        explicit_opt_out = BatchScheduler._config_for(
            {"settings": {"keepFailedPagesForEditing": False}},
            {"id": "page-1", "name": "1.png"},
        )
        self.assertFalse(explicit_opt_out.translator.keep_failed_pages_for_editing)

    def test_web_upscaling_settings_are_forwarded(self):
        config = BatchScheduler._config_for(
            {"settings": {"upscaler": "4xultrasharp", "upscaleRatio": 2, "revertUpscaling": False}},
            {"id": "page-1", "name": "1.png"},
        )

        self.assertEqual(config.upscale.upscaler.value, "4xultrasharp")
        self.assertEqual(config.upscale.upscale_ratio, 2)
        self.assertFalse(config.upscale.revert_upscaling)

        config_revert = BatchScheduler._config_for(
            {"settings": {"upscaler": "4xultrasharp", "upscaleRatio": 2, "revertUpscaling": True}},
            {"id": "page-1", "name": "1.png"},
        )
        self.assertTrue(config_revert.upscale.revert_upscaling)

    def test_deactivate_upscaling_deactivates_downscaling(self):
        config = BatchScheduler._config_for(
            {"settings": {"revertUpscaling": True}},
            {"id": "page-1", "name": "1.png"},
        )
        self.assertIsNone(config.upscale.upscale_ratio)
        self.assertFalse(config.upscale.revert_upscaling)

        config_empty_ratio = BatchScheduler._config_for(
            {"settings": {"upscaleRatio": None, "revertUpscaling": True}},
            {"id": "page-1", "name": "1.png"},
        )
        self.assertIsNone(config_empty_ratio.upscale.upscale_ratio)
        self.assertFalse(config_empty_ratio.upscale.revert_upscaling)

    async def test_reclaims_once_when_manga_finishes_while_another_is_active(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            await store.put_batch(
                "manga-a",
                {"id": "manga-a", "items": [
                    {"id": "page-1", "name": "1.png"},
                    {"id": "page-2", "name": "2.png"},
                ]},
                {"page-1": ("1.png", image.getvalue()), "page-2": ("2.png", image.getvalue())},
            )
            await store.put_batch(
                "manga-b",
                {"id": "manga-b", "items": [{"id": "page-1", "name": "1.png"}]},
                {"page-1": ("1.png", image.getvalue())},
            )
            await store.mutate("manga-b", lambda batch: batch.update(status="processing") is None)

            class Translator:
                _progress_hooks = []

                def add_progress_hook(self, hook):
                    self._progress_hooks.append(hook)

            class Instance:
                translator = Translator()
                reclaim_memory = AsyncMock()

                async def sent(self, _image, config):
                    folder = config.request_id.replace(":", "-")
                    output = results / folder
                    output.mkdir(parents=True)
                    (output / "final.png").write_bytes(b"result")
                    return SimpleNamespace(debug_folder=folder, offline_model=None, gemini_model=None)

            class Executors:
                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(store, Executors(), results)
            instance = Instance()

            await scheduler._process_item("manga-a", "page-1", instance)
            instance.reclaim_memory.assert_not_awaited()

            await scheduler._process_item("manga-a", "page-2", instance)
            instance.reclaim_memory.assert_awaited_once_with()
            self.assertEqual((await store.get_batch("manga-b"))["status"], "processing")

    async def test_failed_item_retries_from_saved_translation_checkpoint(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            await store.put_batch(
                "manga-a",
                {
                    "id": "manga-a",
                    "status": "error",
                    "items": [{"id": "page-1", "name": "1.png", "status": "error", "resultFolder": "checkpoint"}],
                },
                {"page-1": ("1.png", image.getvalue())},
            )
            checkpoint = results / "checkpoint"
            checkpoint.mkdir(parents=True)

            class Run:
                path = checkpoint
                manifest = {
                    "stages": [
                        {"id": "textline_merge", "status": "completed"},
                        {"id": "translation", "status": "failed"},
                    ]
                }

                async def retry_from_stage(self, stage, config, translator):
                    calls.append((stage, config, translator))
                    (self.path / "final.png").write_bytes(b"translated")

            calls = []

            class Translator:
                _progress_hooks = []

                def add_progress_hook(self, hook):
                    self._progress_hooks.append(hook)

            class Instance:
                translator = Translator()

                async def sent(self, _image, _config):
                    raise AssertionError("retry must not rerun the whole pipeline")

                async def _run_translation(self, operation):
                    return await operation()

            class Executors:
                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(store, Executors(), results)
            with patch("server.batch_scheduler.PipelineLabRun.get_or_load", return_value=Run()):
                await scheduler.retry_item("manga-a", "page-1")
                await scheduler._process_item("manga-a", "page-1", Instance())

            batch = await store.get_batch("manga-a")
            self.assertEqual(batch["items"][0]["status"], "completed")
            self.assertEqual(batch["items"][0]["stage"], "finished")
            self.assertEqual(calls[0][0], "translation")
            self.assertEqual(batch["items"][0]["resultFolder"], "checkpoint")
            self.assertFalse((workspace / "batches" / "manga-a" / "inputs" / "page-1.png").exists())

    async def test_non_translation_failure_retries_from_original_input(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            await store.put_batch(
                "manga-a",
                {
                    "id": "manga-a",
                    "status": "error",
                    "items": [{"id": "page-1", "name": "1.png", "status": "error", "resultFolder": "checkpoint"}],
                },
                {"page-1": ("1.png", image.getvalue())},
            )
            checkpoint = results / "checkpoint"
            checkpoint.mkdir(parents=True)
            calls = []

            class Run:
                path = checkpoint
                manifest = {"stages": [{"id": "inpainting", "status": "failed"}]}

                async def retry_from_stage(self, *_args):
                    raise AssertionError("non-translation failures must restart from the input")

            class Translator:
                _progress_hooks = []

                def add_progress_hook(self, hook):
                    self._progress_hooks.append(hook)

            class Instance:
                translator = Translator()

                async def sent(self, _image, _config):
                    calls.append("sent")
                    fresh = results / "fresh"
                    fresh.mkdir(parents=True)
                    (fresh / "final.png").write_bytes(b"result")
                    return SimpleNamespace(debug_folder="fresh")

                async def _run_translation(self, operation):
                    return await operation()

            class Executors:
                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(store, Executors(), results)
            with patch("server.batch_scheduler.PipelineLabRun.get_or_load", return_value=Run()):
                await scheduler.retry_item("manga-a", "page-1")
                await scheduler._process_item("manga-a", "page-1", Instance())

            batch = await store.get_batch("manga-a")
            self.assertEqual(calls, ["sent"])
            self.assertEqual(batch["items"][0]["status"], "completed")
            self.assertEqual(batch["items"][0]["resultFolder"], "fresh")

    async def test_completed_checkpoint_retries_from_original_input(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            await store.put_batch(
                "manga-a",
                {
                    "id": "manga-a",
                    "status": "error",
                    "items": [{"id": "page-1", "name": "1.png", "status": "error", "resultFolder": "checkpoint"}],
                },
                {"page-1": ("1.png", image.getvalue())},
            )
            checkpoint = results / "checkpoint"
            checkpoint.mkdir(parents=True)
            (checkpoint / "final.png").write_bytes(b"stale result")

            class Run:
                path = checkpoint
                manifest = {"stages": [{"id": "translation", "status": "completed"}]}

            calls = []

            class Translator:
                _progress_hooks = []

                def add_progress_hook(self, hook):
                    self._progress_hooks.append(hook)

            class Instance:
                translator = Translator()

                async def sent(self, _image, _config):
                    calls.append("sent")
                    fresh = results / "fresh"
                    fresh.mkdir(parents=True)
                    (fresh / "final.png").write_bytes(b"result")
                    return SimpleNamespace(debug_folder="fresh")

            class Executors:
                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(store, Executors(), results)
            with patch("server.batch_scheduler.PipelineLabRun.get_or_load", return_value=Run()):
                await scheduler.retry_item("manga-a", "page-1")
                await scheduler._process_item("manga-a", "page-1", Instance())

            batch = await store.get_batch("manga-a")
            self.assertEqual(calls, ["sent"])
            self.assertEqual(batch["items"][0]["status"], "completed")
            self.assertEqual(batch["items"][0]["resultFolder"], "fresh")

    async def test_review_item_retries_from_saved_input_and_restarts_pipeline(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            await store.put_batch(
                "manga-a",
                {
                    "id": "manga-a",
                    "items": [{"id": "page-1", "name": "1.png"}],
                },
                {"page-1": ("1.png", b"source")},
            )
            await store.mutate(
                "manga-a",
                lambda manifest: manifest.update(
                    status="completed",
                    completedCount=1,
                    items=[{
                        **manifest["items"][0],
                        "status": "completed",
                        "resultFolder": "review-page",
                        "needsReview": True,
                    }],
                ),
            )
            input_path = workspace / "batches" / "manga-a" / "inputs" / "page-1.png"
            input_path.unlink()
            result = results / "review-page"
            result.mkdir(parents=True)
            (result / "input.png").write_bytes(b"saved source")
            (result / "final.png").write_bytes(b"translated")

            batch = await BatchScheduler(store, None, results).retry_item("manga-a", "page-1")

            self.assertEqual(batch["status"], "waiting")
            self.assertEqual(batch["completedCount"], 0)
            self.assertEqual(batch["items"][0]["status"], "queued")
            self.assertIsNone(batch["items"][0]["resultFolder"])
            self.assertFalse(batch["items"][0]["needsReview"])
            self.assertEqual(input_path.read_bytes(), b"saved source")

    async def test_failed_item_keeps_checkpoint_folder_and_stage(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            await store.put_batch(
                "manga-a",
                {"id": "manga-a", "items": [{"id": "page-1", "name": "1.png"}]},
                {"page-1": ("1.png", image.getvalue())},
            )
            checkpoint = results / "checkpoint"
            checkpoint.mkdir(parents=True)

            class Run:
                path = checkpoint
                manifest = {"stages": [{"id": "translation", "status": "failed"}]}

            class Translator:
                _progress_hooks = []
                _pipeline_lab_run = Run()

                def add_progress_hook(self, hook):
                    self._progress_hooks.append(hook)

            class Instance:
                translator = Translator()

                async def sent(self, _image, _config):
                    await self.translator._progress_hooks[0]("debug_folder:checkpoint", False)
                    raise RuntimeError("translation failed")

            class Executors:
                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(store, Executors(), results)
            await scheduler._process_item("manga-a", "page-1", Instance())

            batch = await store.get_batch("manga-a")
            self.assertEqual(batch["items"][0]["status"], "error")
            self.assertEqual(batch["items"][0]["stage"], "translation")
            self.assertEqual(batch["items"][0]["resultFolder"], "checkpoint")
            self.assertTrue((workspace / "batches" / "manga-a" / "inputs" / "page-1.png").exists())

    async def test_process_group_handles_textless_page_successfully(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            items = [
                {"id": "page-1", "name": "1.png"},
                {"id": "page-2", "name": "2.png"},
            ]
            files = {item["id"]: (item["name"], image.getvalue()) for item in items}
            await store.put_batch("manga-a", {"id": "manga-a", "items": items}, files)

            class Translator:
                _progress_hooks = []

                def add_progress_hook(self, hook):
                    self._progress_hooks.append(hook)

            class Instance:
                translator = Translator()

                async def sent_batch(self, images, configs, batch_size):
                    contexts = []
                    for idx, (img, cfg) in enumerate(zip(images, configs)):
                        folder = f"res-page-{idx + 1}"
                        folder_dir = results / folder
                        folder_dir.mkdir(parents=True, exist_ok=True)
                        (folder_dir / "final.jpg").write_bytes(b"final")
                        ctx = SimpleNamespace(
                            debug_folder=folder,
                            translation_error=None,
                            text_regions=[] if idx == 1 else ["dummy_region"],
                        )
                        contexts.append(ctx)
                    return contexts

            class Executors:
                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(store, Executors(), results)
            claimed = await scheduler._claim_items("manga-a", 2)
            await scheduler._process_group("manga-a", claimed, Instance())

            batch = await store.get_batch("manga-a")
            self.assertEqual(batch["status"], "completed")
            self.assertEqual(batch["items"][0]["status"], "completed")
            self.assertEqual(batch["items"][1]["status"], "completed")
            self.assertEqual(batch["items"][1]["resultFolder"], "res-page-2")

    async def test_scheduler_launches_parallel_preparation_across_multiple_workers(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            items = [
                {"id": "page-1", "name": "1.png"},
                {"id": "page-2", "name": "2.png"},
                {"id": "page-3", "name": "3.png"},
            ]
            files = {item["id"]: (item["name"], image.getvalue()) for item in items}
            await store.put_batch("manga-a", {"id": "manga-a", "items": items, "settings": {"translationBatchSize": 2}}, files)

            class MockExecutors:
                def __init__(self):
                    self._free = 2

                def free_executors(self):
                    return self._free

                async def find_executor(self):
                    self._free -= 1
                    return SimpleNamespace(prepare=AsyncMock(), free_executor=AsyncMock())

                async def free_executor(self, _instance):
                    self._free += 1

            executors = MockExecutors()
            scheduler = BatchScheduler(store, executors, results)

            # First launch should claim page-1 on worker 1
            launched1 = await scheduler._launch_available()
            self.assertTrue(launched1)
            self.assertIn(("manga-a", "prep:page-1"), scheduler._running)
            self.assertIn(("manga-a", "page-1"), scheduler._running_items)

            # Second launch should claim page-2 on worker 2 concurrently
            launched2 = await scheduler._launch_available()
            self.assertTrue(launched2)
            self.assertIn(("manga-a", "prep:page-2"), scheduler._running)
            self.assertIn(("manga-a", "page-2"), scheduler._running_items)

            # No more free executors
            self.assertEqual(executors.free_executors(), 0)
            self.assertFalse(await scheduler._launch_available())

            # Cleanup
            for task in scheduler._running.values():
                task.cancel()
            await asyncio.gather(*scheduler._running.values(), return_exceptions=True)

    async def test_scheduler_aggregates_ready_translation_group_once_batch_size_reached(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            items = [
                {"id": "page-1", "name": "1.png", "stage": "awaiting_translation", "status": "processing", "resultFolder": "res-1"},
                {"id": "page-2", "name": "2.png", "stage": "awaiting_translation", "status": "processing", "resultFolder": "res-2"},
                {"id": "page-3", "name": "3.png", "status": "queued"},
            ]
            files = {item["id"]: (item["name"], image.getvalue()) for item in items}
            await store.put_batch("manga-a", {"id": "manga-a", "items": items, "settings": {"translationBatchSize": 2}}, files)

            # Create mock text_regions_merged.json for res-1 and res-2
            (results / "res-1").mkdir(parents=True, exist_ok=True)
            (results / "res-2").mkdir(parents=True, exist_ok=True)
            (results / "res-1" / "text_regions_merged.json").write_text('[{"text": "Hello"}]', encoding="utf-8")
            (results / "res-2" / "text_regions_merged.json").write_text('[{"text": "World"}]', encoding="utf-8")

            class MockExecutors:
                def __init__(self):
                    self._free = 1

                def free_executors(self):
                    return self._free

                async def find_executor(self):
                    self._free -= 1
                    return SimpleNamespace(
                        translate_and_render_batch=AsyncMock(return_value=[
                            SimpleNamespace(debug_folder="res-1", translation_error=None, text_regions=["r1"]),
                            SimpleNamespace(debug_folder="res-2", translation_error=None, text_regions=["r2"]),
                        ]),
                    )

                async def free_executor(self, _instance):
                    self._free += 1

            executors = MockExecutors()
            scheduler = BatchScheduler(store, executors, results)

            # Ready group of size 2 (page-1 and page-2) should be launched for translation
            launched = await scheduler._launch_available()
            self.assertTrue(launched)
            self.assertIn(("manga-a", "trans:page-1"), scheduler._running)
            self.assertIn(("manga-a", "page-1"), scheduler._running_items)
            self.assertIn(("manga-a", "page-2"), scheduler._running_items)

            # Cleanup
            for task in scheduler._running.values():
                task.cancel()
            await asyncio.gather(*scheduler._running.values(), return_exceptions=True)

    async def test_scheduler_translates_tail_group_when_all_remaining_items_ready(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            items = [
                {"id": "page-1", "name": "1.png", "status": "completed", "stage": "finished"},
                {"id": "page-2", "name": "2.png", "stage": "awaiting_translation", "status": "processing", "resultFolder": "res-2"},
            ]
            files = {"page-2": ("2.png", image.getvalue())}
            # Batch size is 5, but only 1 uncompleted item (page-2) remains and is awaiting_translation
            await store.put_batch("manga-a", {"id": "manga-a", "items": items, "settings": {"translationBatchSize": 5}}, files)

            (results / "res-2").mkdir(parents=True, exist_ok=True)
            (results / "res-2" / "text_regions_merged.json").write_text('[{"text": "Tail"}]', encoding="utf-8")

            class MockExecutors:
                def __init__(self):
                    self._free = 1

                def free_executors(self):
                    return self._free

                async def find_executor(self):
                    self._free -= 1
                    return SimpleNamespace(
                        translate_and_render_batch=AsyncMock(return_value=[
                            SimpleNamespace(debug_folder="res-2", translation_error=None, text_regions=["r2"]),
                        ]),
                    )

                async def free_executor(self, _instance):
                    self._free += 1

            executors = MockExecutors()
            scheduler = BatchScheduler(store, executors, results)

            # Tail group (page-2) should launch translation even though len(ready_group) < translationBatchSize
            launched = await scheduler._launch_available()
            self.assertTrue(launched)
            self.assertIn(("manga-a", "trans:page-2"), scheduler._running)

            # Cleanup
            for task in scheduler._running.values():
                task.cancel()
            await asyncio.gather(*scheduler._running.values(), return_exceptions=True)

    async def test_scheduler_renders_translated_items_in_parallel_across_workers(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            items = [
                {"id": "page-1", "name": "1.png", "stage": "awaiting_translation", "status": "processing", "resultFolder": "res-1"},
                {"id": "page-2", "name": "2.png", "stage": "awaiting_translation", "status": "processing", "resultFolder": "res-2"},
            ]
            files = {item["id"]: (item["name"], image.getvalue()) for item in items}
            await store.put_batch("manga-a", {"id": "manga-a", "items": items, "settings": {"translationBatchSize": 2}}, files)

            (results / "res-1").mkdir(parents=True, exist_ok=True)
            (results / "res-2").mkdir(parents=True, exist_ok=True)
            (results / "res-1" / "text_regions_merged.json").write_text('[{"text": "Hello"}]', encoding="utf-8")
            (results / "res-2" / "text_regions_merged.json").write_text('[{"text": "World"}]', encoding="utf-8")
            (results / "res-1" / "final.jpg").write_bytes(image.getvalue())
            (results / "res-2" / "final.jpg").write_bytes(image.getvalue())

            concurrent_renders = 0
            max_concurrent_renders = 0

            class MockWorker:
                def __init__(self, worker_id):
                    self.worker_id = worker_id
                    self.translator = SimpleNamespace(_progress_hooks=[])

                async def translate_batch_contexts(self, contexts_with_configs, batch_size=None):
                    return [(ctx, cfg) for ctx, cfg in contexts_with_configs]

                async def render(self, ctx, config):
                    nonlocal concurrent_renders, max_concurrent_renders
                    concurrent_renders += 1
                    max_concurrent_renders = max(max_concurrent_renders, concurrent_renders)
                    await asyncio.sleep(0.05)
                    concurrent_renders -= 1
                    ctx.debug_folder = ctx.image_context["subfolder"] if getattr(ctx, "image_context", None) else "res-1"
                    return ctx

                def free_executor(self):
                    pass

            workers = [MockWorker(1), MockWorker(2)]
            queue = asyncio.Queue()
            for w in workers:
                queue.put_nowait(w)

            class MockExecutors:
                def free_executors(self):
                    return queue.qsize()

                async def find_executor(self):
                    return await queue.get()

                async def free_executor(self, worker):
                    queue.put_nowait(worker)

            executors = MockExecutors()
            scheduler = BatchScheduler(store, executors, results)

            launched = await scheduler._launch_available()
            self.assertTrue(launched)

            # Wait for all tasks to complete
            await asyncio.gather(*scheduler._running.values())

            # Verify that both workers rendered in parallel!
            self.assertEqual(max_concurrent_renders, 2)

            batch = await store.get_batch("manga-a")
            self.assertEqual(batch["status"], "completed")
            self.assertEqual(batch["items"][0]["status"], "completed")
            self.assertEqual(batch["items"][1]["status"], "completed")

    def test_config_for_maps_custom_ocr_prob(self):
        config1 = BatchScheduler._config_for(
            {"settings": {"customOcrProb": 0.85}},
            {"id": "page-1", "name": "1.png"},
        )
        self.assertEqual(config1.ocr.prob, 0.85)

        config2 = BatchScheduler._config_for(
            {"settings": {"ocrMinConfidence": "0.7"}},
            {"id": "page-1", "name": "1.png"},
        )
        self.assertEqual(config2.ocr.prob, 0.7)

        config3 = BatchScheduler._config_for(
            {"settings": {"customOcrProb": ""}},
            {"id": "page-1", "name": "1.png"},
        )
        self.assertIsNone(config3.ocr.prob)
        self.assertEqual(BatchScheduler._config_for({}, {"id": "page-1", "name": "1.png"}).ocr.ocr, "48px_ctc")


if __name__ == "__main__":
    unittest.main()
