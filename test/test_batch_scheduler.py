import asyncio
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch

from PIL import Image

from manga_translator import Config
from manga_translator.pipeline.stages import ResourceClass
from server.batch_scheduler import BatchScheduler, stage_resource_limits
from server.batch_resources import MODEL_EXECUTOR_CONCURRENCY, _stage_resource
from server.batch_stage_eligibility import (
    acquire_stage_resource,
    can_schedule_stage,
    eligible_queued_items,
    stage_resource_for,
)
from server.batch_store import BatchStore


class BatchSchedulerMemoryTest(unittest.IsolatedAsyncioTestCase):
    def test_inference_page_limit_uses_memory_pressure_policy(self):
        scheduler = BatchScheduler(None, SimpleNamespace(list=[]), tempfile.gettempdir())
        psutil = ModuleType("psutil")
        psutil.Process = lambda *_: SimpleNamespace(
            memory_info=lambda: SimpleNamespace(rss=60)
        )
        psutil.virtual_memory = lambda: SimpleNamespace(total=100)
        with patch.dict(sys.modules, {"psutil": psutil}):
            self.assertEqual(scheduler._inference_page_limit("ocr"), 1)

    def test_repeated_stage_progress_is_a_noop_but_stage_change_restarts_elapsed_time(self):
        manifest = {"items": [{"id": "p1", "status": "processing", "stage": "detection", "stageStartedAt": 123}]}

        self.assertFalse(BatchScheduler._mutate_stage(manifest, "p1", "detection"))
        self.assertEqual(manifest["items"][0]["stageStartedAt"], 123)

        with patch("server.batch_scheduler.time.time", return_value=2):
            self.assertTrue(BatchScheduler._mutate_stage(manifest, "p1", "ocr"))
        self.assertEqual(manifest["items"][0]["stageStartedAt"], 2000)

    def test_ocr_group_batches_only_compatible_page_settings(self):
        scheduler = BatchScheduler(
            None, None, tempfile.gettempdir(),
            resource_limits=stage_resource_limits(2, 2, gpu_concurrency=2),
        )
        batch = {"id": "manga-a", "settings": {"ocr": "48px_ctc"}}
        items = [
            {"id": "p1", "status": "queued", "pipelineStage": "ocr"},
            {"id": "p2", "status": "queued", "pipelineStage": "ocr"},
            {
                "id": "p3", "status": "queued", "pipelineStage": "ocr",
                "settings": {"ocr": "mocr"},
            },
        ]

        group = scheduler._find_ocr_group(batch, items)

        self.assertEqual([item["id"] for item in group], ["p1", "p2"])
        mocr_group = scheduler._find_ocr_group(
            batch,
            [{**item, "settings": {"ocr": "mocr"}} for item in items[:2]],
        )
        self.assertEqual([item["id"] for item in mocr_group], ["p1", "p2"])
        ppocrv6_cfg = Config.model_validate({"ocr": {"ocr": "ppocrv6"}})
        self.assertEqual(_stage_resource("ocr", ppocrv6_cfg, "mps"), ResourceClass.CPU_HEAVY)
        self.assertEqual(_stage_resource("ocr", ppocrv6_cfg, "cuda"), ResourceClass.CPU_HEAVY)
        self.assertEqual(
            scheduler._find_ocr_group(
                batch,
                [{**item, "settings": {"ocr": "ppocrv6"}} for item in items[:2]],
            ),
            [],
        )

    def test_page_inference_group_batches_only_matching_model_settings(self):
        scheduler = BatchScheduler(
            None, None, tempfile.gettempdir(),
            resource_limits=stage_resource_limits(2, 2, gpu_concurrency=2),
        )
        batch = {"id": "manga-a", "settings": {}}
        items = [
            {"id": "p1", "status": "queued", "pipelineStage": "detection"},
            {"id": "p2", "status": "queued", "pipelineStage": "detection"},
            {
                "id": "p3", "status": "queued", "pipelineStage": "detection",
                "settings": {"textDetector": "ctd"},
            },
        ]

        stage, group = scheduler._find_page_inference_group(batch, items)

        self.assertEqual(stage, "detection")
        self.assertEqual([item["id"] for item in group], ["p1", "p2"])
        self.assertIsNone(
            scheduler._find_page_inference_group(
                batch,
                [{**items[0], "settings": {"textDetector": "ctd"}}, items[2]],
            )
        )

    def test_single_gpu_slot_allows_page_inference_batching(self):
        scheduler = BatchScheduler(None, None, tempfile.gettempdir())
        batch = {"id": "manga-a", "settings": {}}
        items = [
            {"id": "p1", "status": "queued", "pipelineStage": "detection"},
            {"id": "p2", "status": "queued", "pipelineStage": "detection"},
        ]

        stage, group = scheduler._find_page_inference_group(batch, items)
        self.assertEqual(stage, "detection")
        self.assertEqual([item["id"] for item in group], ["p1", "p2"])

        stage, group = scheduler._find_page_inference_group(
            batch,
            [
                {"id": "p1", "status": "queued", "pipelineStage": "upscaling"},
                {"id": "p2", "status": "queued", "pipelineStage": "upscaling"},
            ],
        )
        self.assertEqual(stage, "upscaling")
        self.assertEqual([item["id"] for item in group], ["p1", "p2"])

    async def test_ocr_group_claim_is_atomic_and_keeps_stage_checkpoint(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            items = [
                {"id": f"p{index}", "name": f"{index}.png", "status": "queued", "pipelineStage": "ocr"}
                for index in (1, 2)
            ]
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            files = {item["id"]: (item["name"], image.getvalue()) for item in items}
            await store.put_batch("manga-a", {"id": "manga-a", "items": items}, files)
            scheduler = BatchScheduler(store, None, workspace / "results")

            claimed = await scheduler._claim_prepare_items("manga-a", items)
            saved = await store.get_batch("manga-a")

            self.assertEqual(len(claimed), 2)
            self.assertTrue(all(item["pipelineStage"] == "ocr" for item in claimed))
            self.assertTrue(all(item["status"] == "processing" for item in saved["items"]))

    async def test_checkpointed_ocr_group_skips_pages_without_textlines(self):
        class Store:
            async def get_batch(self, _batch_id):
                return {"id": "manga-a", "items": []}

            async def mutate(self, _batch_id, mutator):
                mutator({"items": []})

        run = SimpleNamespace(
            manifest={"createdAt": "now"},
            ctx=None,
            _ensure_context=lambda: SimpleNamespace(input=None, textlines=[]),
            _document=lambda _name: [],
            release_runtime=lambda: None,
        )
        ocr_batch = AsyncMock(return_value=[])

        translator = SimpleNamespace(_pipeline_run=None, _run_ocr_batch=ocr_batch)
        instance = SimpleNamespace(
            translator=translator,
            _run_translation=AsyncMock(),
        )
        scheduler = BatchScheduler(Store(), SimpleNamespace(free_executor=AsyncMock()), tempfile.gettempdir())
        scheduler._complete_checkpointed_textless = AsyncMock()

        with (
            patch.object(scheduler, "_checkpoint_run", AsyncMock(return_value=run)),
            patch.object(BatchScheduler, "_config_for", return_value=Config()),
        ):
            await scheduler._process_checkpointed_ocr_group(
                "manga-a", [{"id": "p1", "resultFolder": "page-1"}], instance
            )

        scheduler._complete_checkpointed_textless.assert_awaited_once()
        self.assertEqual(scheduler._complete_checkpointed_textless.await_args.args[3], "ocr")
        instance._run_translation.assert_not_awaited()
        ocr_batch.assert_not_awaited()

    async def test_model_group_claim_reports_the_claimed_stage(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            items = [
                {"id": f"p{index}", "name": f"{index}.png", "status": "queued", "pipelineStage": "detection"}
                for index in (1, 2)
            ]
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            files = {item["id"]: (item["name"], image.getvalue()) for item in items}
            await store.put_batch("manga-a", {"id": "manga-a", "items": items}, files)
            scheduler = BatchScheduler(store, None, workspace / "results")

            claimed = await scheduler._claim_prepare_items("manga-a", items, "detection")

            self.assertEqual(len(claimed), 2)
            self.assertTrue(all(item["pipelineStage"] == "detection" for item in claimed))
            self.assertTrue(all(item["stage"] == "detection" for item in claimed))

    async def test_checkpointed_initialize_updates_resource_before_waiting_for_next_stage(self):
        class Store:
            def __init__(self, input_path):
                self.input_file = input_path
                self.manifest = {
                    "id": "manga-a", "status": "waiting", "items": [{
                        "id": "p1", "name": "1.webp", "status": "queued",
                        "stage": "initialize", "pipelineStage": "initialize",
                        "resultFolder": "page-1",
                    }],
                }

            async def get_batch(self, _batch_id):
                return {**self.manifest, "items": [dict(self.manifest["items"][0])]}

            async def input_path(self, _batch_id, _item_id):
                return self.input_file

            async def mutate(self, _batch_id, mutator):
                mutator(self.manifest)

        class Run:
            manifest = {
                "createdAt": "now",
                "stages": [
                    {"id": "input", "status": "completed"},
                    {"id": "colorization", "status": "skipped"},
                    {"id": "upscaling", "status": "skipped"},
                    {"id": "detection", "status": "pending"},
                    {"id": "ocr", "status": "pending"},
                    {"id": "textline_merge", "status": "pending"},
                    {"id": "bubble_detection", "status": "pending"},
                    {"id": "translation", "status": "pending"},
                    {"id": "mask_generation", "status": "pending"},
                    {"id": "layout", "status": "pending"},
                    {"id": "inpainting", "status": "pending"},
                    {"id": "rendering", "status": "pending"},
                ],
            }
            ctx = None
            translator = None
            called_on_executor = False
            called_stage = None
            started_at = None

            def _document(self, name):
                return [{"text": "text"}] if name == "detection.json" else None

            def _ensure_context(self):
                return SimpleNamespace(input=None)

            async def retry_stage(self, stage_id, *_args, **_kwargs):
                self.called_on_executor = instance.in_executor_loop
                self.called_stage = stage_id
                self.started_at = store.manifest["items"][0].get("stageStartedAt")
                self._stage(stage_id)["status"] = "completed"

            def _stage(self, stage_id):
                return next(stage for stage in self.manifest["stages"] if stage["id"] == stage_id)

            def release_runtime(self):
                self.ctx = None
                self.translator = None

        class Executors:
            async def free_executor(self, _instance):
                pass

        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "1.webp"
            Image.new("RGB", (2, 2)).save(input_path, "WEBP")
            store = Store(input_path)
            scheduler = BatchScheduler(
                store, Executors(), root, resource_limits={ResourceClass.CPU_HEAVY: 1}
            )
            run = Run()

            class Translator:
                _current_image_context = None

                def _set_image_context(self, *_args):
                    self._current_image_context = {"request_id": "p1"}

            class Instance:
                def __init__(self):
                    self.translator = Translator()
                    self.in_executor_loop = False

                async def _run_translation(self, operation):
                    self.in_executor_loop = True
                    try:
                        return await operation()
                    finally:
                        self.in_executor_loop = False

            instance = Instance()
            claimed = await scheduler._claim_prepare_item("manga-a", "p1")
            self.assertIsNotNone(claimed)
            self.assertIsNone(claimed.get("stageStartedAt"))
            scheduler._running_items.add(("manga-a", "p1"))
            scheduler._running_stage_resources[("manga-a", "p1")] = (
                "initialize", ResourceClass.IO
            )

            slot = await scheduler._acquire_stage_resource(
                "detection", ResourceClass.CPU_HEAVY
            )
            waiting_for_slot = asyncio.Event()
            acquire = scheduler._acquire_stage_resource

            async def tracked_acquire(stage_id, resource=None):
                if stage_id == "detection":
                    self.assertEqual(
                        scheduler._running_stage_resources[("manga-a", "p1")],
                        ("detection", ResourceClass.CPU_HEAVY),
                    )
                waiting_for_slot.set()
                return await acquire(stage_id, resource)

            scheduler._acquire_stage_resource = tracked_acquire
            with (
                patch.object(scheduler, "_checkpoint_run", AsyncMock(return_value=run)),
                patch.object(BatchScheduler, "_config_for", return_value=Config()),
            ):
                process = asyncio.create_task(
                    scheduler._process_checkpointed_prepare_item("manga-a", "p1", instance)
                )
                await waiting_for_slot.wait()
                try:
                    self.assertNotIn("stageStartedAt", store.manifest["items"][0])
                finally:
                    scheduler._release_stage_resource(
                        "detection", slot, ResourceClass.CPU_HEAVY
                    )
                await process

            self.assertTrue(run.called_on_executor)
            self.assertEqual(run.called_stage, "detection")
            self.assertIsNotNone(run.started_at)
            self.assertEqual(store.manifest["items"][0]["stage"], "ocr")
            self.assertEqual(store.manifest["items"][0]["pipelineStage"], "ocr")

    async def test_checkpointed_prepare_sets_needs_review_from_text_regions(self):
        class Store:
            def __init__(self):
                self.manifest = {
                    "id": "manga-a",
                    "items": [{
                        "id": "p1",
                        "status": "processing",
                        "pipelineStage": "rendering",
                        "resultFolder": "folder-1",
                    }],
                }

            async def get_batch(self, _batch_id):
                return self.manifest

            async def mutate(self, _batch_id, mutator):
                mutator(self.manifest)

            async def input_path(self, _batch_id, _item_id):
                return Path("/tmp/fake-input.png")

        class Run:
            manifest = {
                "createdAt": "now",
                "stages": [{"id": stage, "status": "completed"} for stage in [
                    "input", "colorization", "upscaling", "detection", "ocr",
                    "textline_merge", "bubble_detection", "translation",
                    "mask_generation", "layout", "inpainting", "rendering"
                ]],
            }
            ctx = None
            translator = None

            def _document(self, name):
                if name == "translations.json":
                    return [{"review_required": False}]
                if name == "text_regions.json":
                    return [{"review_required": True}]
                if name == "meta.json":
                    return {"reviewStatus": "pending"}
                return None

            def _ensure_context(self):
                return SimpleNamespace(input=None)

            async def retry_stage(self, stage_id, *_args, **_kwargs):
                self._stage(stage_id)["status"] = "completed"

            def _stage(self, stage_id):
                return next(stage for stage in self.manifest["stages"] if stage["id"] == stage_id)

            async def checkpoint(self):
                pass

            def release_runtime(self):
                pass

        class Executors:
            async def free_executor(self, _instance):
                pass

        class Instance:
            def __init__(self):
                self.translator = SimpleNamespace(
                    _set_image_context=lambda *args: None,
                    _current_image_context={},
                    _pipeline_run=None,
                )

            async def _run_translation(self, operation):
                return await operation()

        with tempfile.TemporaryDirectory() as root:
            result_dir = Path(root) / "folder-1"
            result_dir.mkdir(parents=True)
            (result_dir / "final.png").write_bytes(b"final image")
            store = Store()
            scheduler = BatchScheduler(store, Executors(), root)
            run = Run()
            instance = Instance()
            with (
                patch.object(scheduler, "_checkpoint_run", AsyncMock(return_value=run)),
                patch.object(BatchScheduler, "_config_for", return_value=Config()),
            ):
                await scheduler._process_checkpointed_prepare_item("manga-a", "p1", instance)

            self.assertTrue(store.manifest["items"][0]["needsReview"])
            self.assertEqual(store.manifest["items"][0]["status"], "completed")
            self.assertEqual(store.manifest["items"][0]["stage"], "finished")

    async def test_stage_resource_slots_cap_gpu_at_two_and_allow_cpu_work(self):
        limits = stage_resource_limits(4, 3)
        limits[ResourceClass.GPU] = 4
        scheduler = BatchScheduler(None, None, tempfile.gettempdir(), resource_limits=limits)
        self.assertEqual(scheduler.resource_limits[ResourceClass.CPU_HEAVY], 3)
        self.assertEqual(scheduler.resource_limits[ResourceClass.CPU_LIGHT], 3)
        self.assertEqual(scheduler.resource_limits[ResourceClass.GPU], 2)
        gpu_slot = await scheduler._acquire_stage_resource("ocr")
        second_gpu_slot = await scheduler._acquire_stage_resource("detection")
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(scheduler._acquire_stage_resource("bubble_detection"), 0.01)
        finally:
            scheduler._release_stage_resource("ocr", gpu_slot)
            scheduler._release_stage_resource("detection", second_gpu_slot)

        cpu_slots = [
            await scheduler._acquire_stage_resource("mask_generation")
            for _ in range(3)
        ]
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    scheduler._acquire_stage_resource("layout"), 0.01
                )
        finally:
            for cpu_slot in cpu_slots:
                scheduler._release_stage_resource("mask_generation", cpu_slot)

    async def test_checkpointed_model_group_keeps_stage_retry_and_manifest_transition(self):
        config = Config()
        image = Image.new("RGB", (2, 2))
        context = SimpleNamespace(input=image, img_rgb=object(), img_colorized=None, upscaled=None)

        class Run:
            manifest = {"createdAt": "now"}

            def _ensure_context(self):
                self.ctx = context
                return context

            begin_stage = AsyncMock()
            retry_stage = AsyncMock()
            release_runtime = Mock()

        run = Run()
        item = {"id": "page-1", "resultFolder": "folder-1", "pipelineStage": "detection"}
        manifest = {"status": "processing", "items": [item]}

        class Store:
            async def get_batch(self, _batch_id):
                return {"id": "batch-1", "items": [item]}

            async def mutate(self, _batch_id, mutator):
                mutator(manifest)
                return manifest

        executor_pool = SimpleNamespace(free_executor=AsyncMock())
        scheduler = BatchScheduler(Store(), executor_pool, tempfile.gettempdir())
        translator = SimpleNamespace(
            _pipeline_run=None,
            _current_image_context={},
            _run_detection_batch=AsyncMock(return_value=["detections"]),
        )

        def set_image_context(_config, _image):
            translator._current_image_context = {}

        translator._set_image_context = set_image_context

        async def run_translation(operation):
            return await operation()

        instance = SimpleNamespace(translator=translator, _run_translation=run_translation)
        slot = object()
        scheduler._acquire_stage_resource = AsyncMock(return_value=slot)
        scheduler._release_stage_resource = Mock()
        scheduler._checkpoint_run = AsyncMock(return_value=run)
        scheduler._set_group_stage = AsyncMock()
        scheduler._next_batch_stage = lambda _run: "ocr"
        scheduler._reclaim_batch_memory = AsyncMock()

        with patch.object(BatchScheduler, "_config_for", return_value=config):
            await scheduler._process_checkpointed_model_group(
                "batch-1", [item], instance, "detection",
            )

        translator._run_detection_batch.assert_awaited_once_with([config], [context])
        run.retry_stage.assert_awaited_once_with(
            "detection", config, translator, stage_already_running=True,
            precomputed_detection="detections",
        )
        self.assertEqual(manifest["items"][0]["status"], "queued")
        self.assertEqual(manifest["items"][0]["stage"], "ocr")
        self.assertEqual(manifest["items"][0]["pipelineStage"], "ocr")
        run.release_runtime.assert_called_once_with()
        scheduler._release_stage_resource.assert_called_once_with("detection", slot)
        executor_pool.free_executor.assert_awaited_once_with(instance)

    def test_stage_resource_limits_accept_single_gpu_slot(self):
        limits = stage_resource_limits(2, 2, gpu_concurrency=1)
        self.assertEqual(limits[ResourceClass.GPU], 1)
        self.assertEqual(stage_resource_limits(1, 1, gpu_concurrency=3)[ResourceClass.GPU], 1)

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

            # Page 1 finishes text preparation and reaches the translation barrier.
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

    def test_professional_translation_waits_for_every_page_before_story_analysis(self):
        scheduler = BatchScheduler(None, None, tempfile.gettempdir())
        settings = {
            "translationQuality": "professional",
            "storyPlan": {
                "enabled": True,
                "segments": [
                    {"startPage": 1, "endPage": 2},
                    {"startPage": 3, "endPage": 4},
                ],
            },
        }
        items = [
            {"id": "p1", "status": "processing", "stage": "awaiting_translation"},
            {"id": "p2", "status": "processing", "stage": "awaiting_translation"},
            {"id": "p3", "status": "processing", "stage": "detection"},
            {"id": "p4", "status": "processing", "stage": "awaiting_translation"},
        ]

        ready = scheduler._find_ready_translation_group("batch", items, 4, settings)
        self.assertIsNone(ready)

        items[2]["stage"] = "awaiting_translation"
        ready = scheduler._find_ready_translation_group("batch", items, 4, settings)
        self.assertEqual([item["id"] for item in ready], ["p1", "p2", "p3", "p4"])
        items[1].update(status="error", stage="ocr")
        self.assertIsNone(scheduler._find_ready_translation_group("batch", items, 4, settings))

    def test_translation_group_is_ready_while_other_pages_finish_preparation(self):
        scheduler = BatchScheduler(None, None, tempfile.gettempdir())
        items = [
            {"id": "p1", "status": "queued", "stage": "detection", "pipelineStage": "detection"},
            {"id": "p2", "status": "processing", "stage": "awaiting_translation"},
            {"id": "p3", "status": "processing", "stage": "awaiting_translation"},
            {"id": "p4", "status": "queued", "stage": "ocr", "pipelineStage": "ocr"},
        ]

        self.assertEqual(scheduler._current_batch_stage(items), "detection")
        ready = scheduler._find_ready_translation_group("batch", items, 2, {})
        self.assertEqual([item["id"] for item in ready], ["p2", "p3"])

        for item in items:
            item.update(status="processing", stage="awaiting_translation")
            item.pop("pipelineStage", None)
        ready = scheduler._find_ready_translation_group("batch", items, 2, {})

        self.assertEqual([item["id"] for item in ready], ["p1", "p2"])

    def test_fast_translation_waits_for_batch_size_or_all_remaining_pages(self):
        scheduler = BatchScheduler(None, None, tempfile.gettempdir())
        items = [
            {"id": f"p{i}", "status": "queued", "stage": "queued", "pipelineStage": "mask_generation"}
            for i in range(60)
        ]
        items.append({"id": "p60", "status": "processing", "stage": "awaiting_translation"})

        ready = scheduler._find_ready_translation_group("batch", items, 12, {})

        self.assertEqual([item["id"] for item in ready], ["p60"])

        items.append({"id": "p61", "status": "queued", "stage": "queued", "pipelineStage": "translation"})
        self.assertIsNone(scheduler._find_ready_translation_group("batch", items, 12, {}))

        items[-1].update(status="processing", stage="awaiting_translation")
        ready = scheduler._find_ready_translation_group("batch", items, 12, {})
        self.assertEqual([item["id"] for item in ready], ["p60", "p61"])

    def test_prepare_stops_after_text_grouping_and_bubble_detection(self):
        scheduler = BatchScheduler(None, None, tempfile.gettempdir())
        completed = {
            "colorization", "upscaling", "detection", "ocr", "textline_merge", "bubble_detection"
        }
        stages = [
            {"id": stage, "status": "completed" if stage in completed else "pending"}
            for stage in (
                "colorization", "upscaling", "detection", "ocr", "textline_merge",
                "bubble_detection", "translation", "mask_generation", "layout", "inpainting", "rendering",
            )
        ]
        run = SimpleNamespace(manifest={"stages": stages})

        self.assertIsNone(scheduler._next_prepare_stage(run))
        self.assertEqual(scheduler._next_batch_stage(run), "translation")

        next_stage = SimpleNamespace(manifest={"stages": [
            {"id": "colorization", "status": "skipped"},
            {"id": "upscaling", "status": "skipped"},
            {"id": "detection", "status": "completed"},
            {"id": "ocr", "status": "completed"},
            {"id": "bubble_detection", "status": "completed"},
            {"id": "textline_merge", "status": "pending"},
        ]})
        self.assertEqual(scheduler._next_batch_stage(next_stage), "textline_merge")

    def test_professional_translation_waits_when_a_story_page_fails(self):
        scheduler = BatchScheduler(None, None, tempfile.gettempdir())
        items = [
            {"id": "p1", "status": "processing", "stage": "awaiting_translation"},
            {"id": "p2", "status": "error", "stage": "ocr"},
        ]

        self.assertIsNone(scheduler._find_ready_translation_group(
            "batch", items, 2, {"translationQuality": "professional"}
        ))

    def test_professional_story_plan_ranges_are_remapped_for_story_group(self):
        plan = {
            "enabled": True,
            "autoDetect": False,
            "segments": [
                {"id": "one", "startPage": 1, "endPage": 2},
                {"id": "two", "startPage": 3, "endPage": 4},
            ],
            "archives": [
                {"id": "archive", "startPage": 1, "endPage": 4, "pageCount": 4},
            ],
        }
        items = [{"id": f"p{index}"} for index in range(1, 5)]

        remapped = BatchScheduler._story_plan_for_group(plan, items, ["p3", "p4"])

        self.assertEqual(remapped["segments"][0]["startPage"], 1)
        self.assertEqual(remapped["segments"][0]["endPage"], 2)
        self.assertEqual(remapped["archives"][0]["pageCount"], 2)
        self.assertEqual(plan["segments"][1]["startPage"], 3)

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

    async def test_checkpoint_backed_retry_reenters_the_stage_scheduler(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            await store.put_batch(
                "manga-a",
                {
                    "id": "manga-a",
                    "status": "waiting",
                    "items": [{
                        "id": "page-1",
                        "name": "1.png",
                        "status": "queued",
                        "resultFolder": "checkpoint",
                        "retryFromStage": "ocr",
                    }],
                },
                {"page-1": ("1.png", image.getvalue())},
            )

            class Executors:
                def free_executors(self):
                    return 1

                async def find_executor(self):
                    async def run_translation(operation):
                        return await operation()
                    return SimpleNamespace(translator=SimpleNamespace(), _run_translation=run_translation)

                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(store, Executors(), workspace / "results")
            scheduler._process_item = AsyncMock()
            scheduler._process_prepare_item = AsyncMock()

            self.assertTrue(await scheduler._launch_available())
            await asyncio.sleep(0)
            scheduler._process_prepare_item.assert_awaited_once_with("manga-a", "page-1", ANY)
            scheduler._process_item.assert_not_awaited()
            batch = await store.get_batch("manga-a")
            self.assertEqual(batch["items"][0]["pipelineStage"], "ocr")
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

    async def test_scheduler_keeps_other_manga_queued_while_one_is_active(self):
        store = SimpleNamespace(
            list_batches=AsyncMock(
                return_value=[
                    {"id": "manga-a", "status": "processing", "items": [{"status": "processing"}], "settings": {}},
                    {"id": "manga-b", "status": "waiting", "items": [{"id": "page-1", "status": "queued"}]},
                ]
            ),
            mutate=AsyncMock(),
        )

        class Executors:
            def free_executors(self):
                return 1

            async def find_executor(self):
                return object()

            async def free_executor(self, _instance):
                pass

        scheduler = BatchScheduler(store, Executors(), tempfile.gettempdir())
        scheduler._running[("manga-a", "page-1")] = asyncio.create_task(asyncio.sleep(10))
        scheduler._claim_item = AsyncMock()

        self.assertFalse(await scheduler._launch_available())
        scheduler._claim_item.assert_not_awaited()

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
            with patch("server.batch_scheduler.PipelineRun.get_or_load", return_value=Run()):
                await scheduler.retry_item("manga-a", "page-1")
                await scheduler._process_item("manga-a", "page-1", Instance())

            batch = await store.get_batch("manga-a")
            self.assertEqual(batch["items"][0]["status"], "completed")
            self.assertEqual(batch["items"][0]["stage"], "finished")
            self.assertEqual(calls[0][0], "translation")
            self.assertEqual(batch["items"][0]["resultFolder"], "checkpoint")
            self.assertFalse((workspace / "batches" / "manga-a" / "inputs" / "page-1.png").exists())

    async def test_non_translation_failure_retries_from_saved_stage(self):
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
                ctx = None

                async def retry_from_stage(self, stage, _config, _translator):
                    calls.append(stage)
                    (self.path / "final.png").write_bytes(b"result")

            class Translator:
                _progress_hooks = []

                def add_progress_hook(self, hook):
                    self._progress_hooks.append(hook)

            class Instance:
                translator = Translator()

                async def sent(self, _image, _config):
                    raise AssertionError("a saved stage checkpoint must be resumed")

                async def _run_translation(self, operation):
                    return await operation()

            class Executors:
                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(store, Executors(), results)
            with patch("server.batch_scheduler.PipelineRun.get_or_load", return_value=Run()):
                await scheduler.retry_item("manga-a", "page-1", from_stage="inpainting")
                (workspace / "batches" / "manga-a" / "inputs" / "page-1.png").unlink()
                await scheduler._process_item("manga-a", "page-1", Instance())

            self.assertEqual(calls, ["inpainting"])
            batch = await store.get_batch("manga-a")
            self.assertEqual(batch["items"][0]["status"], "completed")
            self.assertEqual(batch["items"][0]["resultFolder"], "checkpoint")

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
            with patch("server.batch_scheduler.PipelineRun.get_or_load", return_value=Run()):
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
                _pipeline_run = Run()

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

    async def test_failed_retry_indexes_existing_checkpoint_for_stage_state_sync(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            store.register_result = AsyncMock()
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            await store.put_batch(
                "manga-a",
                {"id": "manga-a", "items": [{
                    "id": "page-1", "name": "1.png", "status": "error",
                    "pageId": "page-id", "resultFolder": "checkpoint",
                }]},
                {"page-1": ("1.png", image.getvalue())},
            )
            checkpoint = results / "checkpoint"
            checkpoint.mkdir(parents=True)
            Image.new("RGB", (2, 2)).save(checkpoint / "final.png")

            class Run:
                path = checkpoint
                manifest = {"stages": [{"id": "inpainting", "status": "failed"}]}
                ctx = None

                async def retry_from_stage(self, *_args):
                    raise RuntimeError("retry failed")

            class Translator:
                _progress_hooks = []

                def add_progress_hook(self, hook):
                    self._progress_hooks.append(hook)

            class Instance:
                translator = Translator()

                async def sent(self, *_args):
                    raise AssertionError("saved stage should be retried")

                async def _run_translation(self, operation):
                    return await operation()

            class Executors:
                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(store, Executors(), results)
            with patch("server.batch_scheduler.PipelineRun.get_or_load", return_value=Run()):
                await scheduler.retry_item("manga-a", "page-1", from_stage="inpainting")
                await scheduler._process_item("manga-a", "page-1", Instance())

            store.register_result.assert_awaited_once()
            self.assertEqual(store.register_result.await_args.args[0], "checkpoint")
            self.assertEqual(store.register_result.await_args.kwargs["page_id"], "page-id")

    async def test_translation_checkpoints_all_pages_before_mask_stage(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            results = workspace / "results"
            store = BatchStore(workspace / "batches", results)
            image = io.BytesIO()
            Image.new("RGB", (2, 2)).save(image, "PNG")
            items = [
                {"id": f"page-{index}", "name": f"{index}.png", "status": "processing",
                 "stage": "awaiting_translation", "resultFolder": f"res-{index}"}
                for index in (1, 2)
            ]
            files = {item["id"]: (item["name"], image.getvalue()) for item in items}
            await store.put_batch(
                "manga-a", {"id": "manga-a", "items": items, "settings": {}}, files
            )
            for index in (1, 2):
                folder = results / f"res-{index}"
                folder.mkdir(parents=True)
                (folder / "text_regions_merged.json").write_text("[]", encoding="utf-8")

            class Run:
                def __init__(self):
                    self.manifest = {"stages": [{"id": "translation", "status": "pending"}]}
                    self.documents = {}

                def write_json(self, name, payload):
                    self.documents[name] = payload

                def _stage(self, stage_id):
                    return next(stage for stage in self.manifest["stages"] if stage["id"] == stage_id)

                def _finish(self, stage_id):
                    self._stage(stage_id)["status"] = "completed"

                async def checkpoint(self):
                    pass

                def release_runtime(self):
                    pass

            runs = {f"res-{index}": Run() for index in (1, 2)}

            class Translator:
                def __init__(self):
                    self._progress_hooks = []

                def add_progress_hook(self, hook):
                    self._progress_hooks.append(hook)

            class Instance:
                def __init__(self):
                    self.translator = Translator()
                    self.render = AsyncMock(side_effect=AssertionError("render must wait for the mask barrier"))

                async def translate_batch_contexts(self, contexts_with_configs, batch_size=None):
                    return contexts_with_configs

            class Executors:
                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(store, Executors(), results)
            config = SimpleNamespace(translator=SimpleNamespace(story_plan=None))
            claimed = [{**item, "settings": {}} for item in items]

            async def checkpoint(folder):
                return runs[folder]

            with (
                patch.object(scheduler, "_checkpoint_run", side_effect=checkpoint),
                patch.object(BatchScheduler, "_config_for", return_value=config),
            ):
                await scheduler._process_translation_group("manga-a", claimed, Instance())

            batch = await store.get_batch("manga-a")
            self.assertTrue(all(item["status"] == "queued" for item in batch["items"]))
            self.assertTrue(all(item["pipelineStage"] == "mask_generation" for item in batch["items"]))
            self.assertTrue(all(run._stage("translation")["status"] == "completed" for run in runs.values()))
            self.assertTrue(all("translations.json" in run.documents for run in runs.values()))

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
                    async def run_translation(operation):
                        return await operation()
                    return SimpleNamespace(
                        prepare=AsyncMock(),
                        translator=SimpleNamespace(),
                        _run_translation=run_translation,
                    )

                async def free_executor(self, _instance):
                    self._free += 1

            executors = MockExecutors()
            scheduler = BatchScheduler(store, executors, results)

            async def hold_stage(*_args):
                await asyncio.Future()

            with patch.object(scheduler, "_process_prepare_item", side_effect=hold_stage):
                # Pages in the active stage can occupy both workers together.
                self.assertTrue(await scheduler._launch_available())
                self.assertIn(("manga-a", "stage:page-1"), scheduler._running)
                self.assertTrue(await scheduler._launch_available())
                self.assertIn(("manga-a", "stage:page-2"), scheduler._running)
                self.assertEqual(executors.free_executors(), 0)
                self.assertFalse(await scheduler._launch_available())

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
                {"id": "page-3", "name": "3.png", "status": "completed", "stage": "finished"},
            ]
            files = {item["id"]: (item["name"], image.getvalue()) for item in items[:2]}
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
                    async def run_translation(operation):
                        return await operation()
                    return SimpleNamespace(
                        translate_batch_contexts=AsyncMock(),
                        translator=SimpleNamespace(_progress_hooks=[]),
                        _run_translation=run_translation,
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
                    async def run_translation(operation):
                        return await operation()
                    return SimpleNamespace(
                        translate_batch_contexts=AsyncMock(),
                        translator=SimpleNamespace(_progress_hooks=[]),
                        _run_translation=run_translation,
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

    def test_failed_page_keeps_batch_stage_selection_blocked(self):
        scheduler = BatchScheduler(None, None, tempfile.gettempdir())
        items = [
            {"id": "p1", "status": "queued", "pipelineStage": "translation"},
            {"id": "p2", "status": "queued", "pipelineStage": "layout"},
        ]

        self.assertEqual(scheduler._current_batch_stage(items), "translation")
        items[0].update(status="queued", pipelineStage="mask_generation")
        self.assertEqual(scheduler._current_batch_stage(items), "mask_generation")
        items[1]["status"] = "error"
        self.assertIsNone(scheduler._current_batch_stage(items))

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


class BatchResourceSchedulingTest(unittest.IsolatedAsyncioTestCase):
    def test_resource_mapping_follows_effective_model_settings(self):
        self.assertEqual(
            _stage_resource("translation", Config(), "cuda"),
            ResourceClass.NETWORK,
        )
        sugoi = Config(translator={"translator_chain": "sugoi:ENG"})
        self.assertEqual(_stage_resource("translation", sugoi, "cuda"), ResourceClass.GPU)
        self.assertEqual(_stage_resource("translation", sugoi, "cpu"), ResourceClass.CPU_HEAVY)

        ctd = Config(detector={"detector": "ctd"})
        self.assertEqual(_stage_resource("detection", ctd, "cuda"), ResourceClass.GPU)
        self.assertEqual(_stage_resource("detection", ctd, "cpu"), ResourceClass.CPU_HEAVY)
        paddle = Config(detector={"detector": "paddle"})
        self.assertEqual(_stage_resource("detection", paddle, "cuda"), ResourceClass.CPU_HEAVY)

        vulkan = Config(upscale={"upscaler": "esrgan", "upscale_ratio": 4})
        ultrasharp = Config(upscale={"upscaler": "4xultrasharp", "upscale_ratio": 4})
        self.assertEqual(_stage_resource("upscaling", vulkan, "cpu"), ResourceClass.GPU)
        self.assertEqual(_stage_resource("upscaling", ultrasharp, "cuda"), ResourceClass.GPU)
        self.assertEqual(
            _stage_resource("upscaling", Config(upscale={"upscale_ratio": 1}), "cuda"),
            ResourceClass.CPU_LIGHT,
        )
        self.assertEqual(
            _stage_resource("upscaling", Config(upscale={"upscale_ratio": 0}), "cuda"),
            ResourceClass.CPU_LIGHT,
        )
        self.assertEqual(
            _stage_resource("bubble_detection", Config(bubble_detection={"enabled": False}), "cuda"),
            ResourceClass.CPU_LIGHT,
        )

    async def test_cpu_stages_share_one_resource_lane_and_gpu_cap(self):
        limits = stage_resource_limits(3, 2, gpu_concurrency=5)
        self.assertEqual(limits[ResourceClass.NETWORK], 3)
        self.assertEqual(limits[ResourceClass.IO], 3)

        scheduler = BatchScheduler(
            None,
            None,
            tempfile.gettempdir(),
            resource_limits=stage_resource_limits(4, 4, gpu_concurrency=8),
        )
        manager = scheduler._resource_manager

        self.assertIs(manager.slots[ResourceClass.CPU_LIGHT], manager.slots[ResourceClass.CPU_HEAVY])
        self.assertEqual(manager.resource_limits[ResourceClass.GPU], MODEL_EXECUTOR_CONCURRENCY)
        slot = await manager.acquire("text_grouping", ResourceClass.CPU_LIGHT)
        self.assertEqual(manager.active_resources("text_grouping"), {ResourceClass.CPU_HEAVY})
        manager.release_stage("text_grouping", slot, ResourceClass.CPU_LIGHT)
        self.assertFalse(manager.active_resources("text_grouping"))

    def test_different_resource_stage_can_overlap_but_cpu_family_stays_serial(self):
        instance = SimpleNamespace(
            translator=SimpleNamespace(device="cuda", _gpu_limited_memory=False)
        )
        scheduler = BatchScheduler(None, SimpleNamespace(list=[instance]), tempfile.gettempdir())
        with patch.object(scheduler, "_config_for", return_value=Config()):
            items = [
                {"id": "api", "status": "processing", "pipelineStage": "translation"},
                {"id": "layout", "status": "queued", "pipelineStage": "layout"},
            ]
            scheduler._running_items.add(("batch", "api"))
            layout_resource = stage_resource_for(scheduler, {"settings": {}}, items[1], "layout")
            self.assertTrue(
                can_schedule_stage(scheduler, "batch", {"settings": {}}, items, "layout", layout_resource)
            )

            items[1].update(status="processing")
            scheduler._running_items.add(("batch", "layout"))
            merge_resource = stage_resource_for(
                scheduler, {"settings": {}}, {"id": "merge", "settings": {}, "pipelineStage": "textline_merge"},
                "textline_merge",
            )
            self.assertFalse(
                can_schedule_stage(
                    scheduler,
                    "batch", {"settings": {}}, items, "textline_merge", merge_resource
                )
            )

    def test_earlier_ready_stage_wins_within_a_resource_lane(self):
        instance = SimpleNamespace(
            translator=SimpleNamespace(device="cuda", _gpu_limited_memory=False)
        )
        scheduler = BatchScheduler(None, SimpleNamespace(list=[instance]), tempfile.gettempdir())
        items = [
            {"id": "gpu", "status": "processing", "stage": "detection", "pipelineStage": "detection"},
            {"id": "first-cpu", "status": "queued", "stage": "mask_generation", "pipelineStage": "mask_generation"},
            {"id": "later-cpu", "status": "queued", "stage": "layout", "pipelineStage": "layout"},
        ]
        scheduler._running_items.add(("batch", "gpu"))

        eligible = eligible_queued_items(
            scheduler, "batch", {"settings": {}}, items, instance
        )

        self.assertEqual(eligible[1]["status"], "queued")
        self.assertEqual(eligible[2]["status"], "blocked")
        self.assertEqual(
            scheduler._find_next_queued_item("batch", eligible)["id"], "first-cpu"
        )

    async def test_stale_initialize_tracking_uses_runtime_gpu_stage_and_launches_network(self):
        batch = {
            "id": "batch",
            "status": "processing",
            "settings": {},
            "items": [
                {"id": "active", "status": "processing", "pipelineStage": "initialize", "stage": "detection"},
                {"id": "gpu", "status": "queued", "pipelineStage": "ocr"},
                {"id": "network", "status": "queued", "pipelineStage": "translation"},
            ],
        }

        class Store:
            async def list_batches(self):
                return [batch]

            async def mutate(self, _batch_id, mutator):
                mutator(batch)
                return batch

        instance = SimpleNamespace(
            translator=SimpleNamespace(device="cuda", _gpu_limited_memory=False),
            _run_translation=AsyncMock(),
        )

        class Executors:
            list = [instance]

            def free_executors(self):
                return 1

            async def find_executor(self):
                return instance

            async def free_executor(self, _instance):
                pass

        started = asyncio.Event()
        launched = []

        async def process(_batch_id, item_id, _instance):
            launched.append(item_id)
            started.set()
            await asyncio.Event().wait()

        with patch("server.batch_resource_policy.MODEL_EXECUTOR_CONCURRENCY", 2):
            scheduler = BatchScheduler(
                Store(),
                Executors(),
                tempfile.gettempdir(),
                resource_limits=stage_resource_limits(2, 2, gpu_concurrency=2),
            )
            gpu_permit = await scheduler._acquire_stage_resource("detection", ResourceClass.GPU)
            scheduler._running_items.add(("batch", "active"))
            scheduler._running_stage_resources[("batch", "active")] = (
                "initialize",
                ResourceClass.IO,
            )
            scheduler._process_prepare_item = process

            try:
                with patch.object(scheduler, "_config_for", return_value=Config()):
                    gpu_resource = stage_resource_for(scheduler, batch, batch["items"][1], "ocr", instance)
                    network_resource = stage_resource_for(
                        scheduler, batch, batch["items"][2], "translation", instance
                    )
                    self.assertEqual(gpu_resource, ResourceClass.GPU)
                    self.assertEqual(network_resource, ResourceClass.NETWORK)
                    self.assertFalse(
                        can_schedule_stage(scheduler, "batch", batch, batch["items"], "ocr", gpu_resource)
                    )
                    self.assertTrue(
                        can_schedule_stage(
                            scheduler, "batch", batch, batch["items"], "translation", network_resource
                        )
                    )

                    self.assertTrue(await scheduler._launch_available())
                    await asyncio.wait_for(started.wait(), timeout=1)
                    self.assertEqual(launched, ["network"])
                    self.assertEqual(batch["items"][1]["status"], "queued")
            finally:
                tasks = list(scheduler._running.values())
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                scheduler._release_stage_resource("detection", gpu_permit, ResourceClass.GPU)

    def test_failed_page_keeps_batch_stage_selection_blocked(self):
        scheduler = BatchScheduler(None, None, tempfile.gettempdir())
        items = [
            {"id": "failed", "status": "error", "pipelineStage": "translation"},
            {"id": "ready", "status": "queued", "pipelineStage": "layout"},
        ]

        self.assertIsNone(scheduler._current_batch_stage(items))

    async def test_scheduler_launches_ready_next_stage_across_resource_boundary(self):
        batch = {
            "id": "batch",
            "status": "processing",
            "settings": {},
            "items": [
                {"id": "api", "status": "processing", "pipelineStage": "translation", "stage": "translating"},
                {"id": "layout", "status": "queued", "pipelineStage": "layout"},
            ],
        }

        class Store:
            async def list_batches(self):
                return [batch]

            async def mutate(self, _batch_id, _mutator):
                return batch

        class Executors:
            def __init__(self):
                self.instance = SimpleNamespace(
                    translator=SimpleNamespace(device="cuda", _gpu_limited_memory=False),
                    _run_translation=AsyncMock(),
                )
                self.list = [self.instance]

            def free_executors(self):
                return 1

            async def find_executor(self):
                return self.instance

            async def free_executor(self, _instance):
                pass

        stage_executors = Executors()
        direct_executors = Executors()
        scheduler = BatchScheduler(
            Store(),
            direct_executors,
            tempfile.gettempdir(),
            stage_executors=stage_executors,
        )
        direct_executors.free = 0
        direct_executors.free_executors = lambda: direct_executors.free
        direct_executors.find_executor = AsyncMock(side_effect=AssertionError("direct pool is busy"))
        scheduler._running_items.add(("batch", "api"))
        scheduler._claim_prepare_item = AsyncMock(return_value=batch["items"][1])
        scheduler._process_prepare_item = AsyncMock()

        self.assertTrue(await scheduler._launch_available())
        await asyncio.sleep(0)
        self.assertEqual(direct_executors.free_executors(), 0)
        scheduler._process_prepare_item.assert_awaited_once_with("batch", "layout", ANY)
        for task in scheduler._running.values():
            task.cancel()
        await asyncio.gather(*scheduler._running.values(), return_exceptions=True)

    async def _assert_launch_uses_claimed_worker(self, executor_order, active_device, queued_device):
        workers = {
            device: SimpleNamespace(
                translator=SimpleNamespace(device=device, _gpu_limited_memory=False),
                _run_translation=AsyncMock(),
            )
            for device in {active_device, queued_device}
        }
        batch = {
            "id": "batch",
            "status": "processing",
            "settings": {},
            "items": [
                {"id": "active", "status": "processing", "pipelineStage": "detection"},
                {"id": "queued", "status": "queued", "pipelineStage": "ocr"},
            ],
        }

        class Store:
            async def list_batches(self):
                return [batch]

            async def mutate(self, _batch_id, mutator):
                mutator(batch)
                return batch

        class Executors:
            def __init__(self):
                self.list = [workers[device] for device in executor_order]
                self.available = [workers[queued_device]]

            def free_executors(self):
                return len(self.available)

            async def find_executor(self):
                return self.available.pop(0)

            async def free_executor(self, instance):
                self.available.append(instance)

        executors = Executors()
        scheduler = BatchScheduler(Store(), executors, tempfile.gettempdir())
        scheduler._running_items.add(("batch", "active"))
        config = Config(detector={"detector": "ctd"})
        active_resource = (
            ResourceClass.GPU if active_device == "cuda" else ResourceClass.CPU_HEAVY
        )

        called = asyncio.Event()

        async def process(_batch_id, _item_id, instance):
            scheduler.selected_instance = instance
            called.set()
            await executors.free_executor(instance)

        scheduler._process_prepare_item = process
        with patch.object(scheduler, "_config_for", return_value=config):
            permit = await acquire_stage_resource(
                scheduler, batch, batch["items"][0], "detection", workers[active_device]
            )
            self.assertEqual(
                scheduler._resource_manager.active_resources("detection"), {active_resource}
            )
            try:
                self.assertTrue(await scheduler._launch_available())
                await called.wait()
                self.assertIs(scheduler.selected_instance, workers[queued_device])
                queued_resource = stage_resource_for(
                    scheduler, batch, batch["items"][1], "ocr", workers[queued_device]
                )
                self.assertEqual(
                    queued_resource,
                    ResourceClass.GPU if queued_device == "cuda" else ResourceClass.CPU_HEAVY,
                )
            finally:
                scheduler._release_stage_resource("detection", permit)
                await asyncio.gather(*scheduler._running.values(), return_exceptions=True)

    async def test_cpu_first_pool_uses_claimed_gpu_for_permit_and_overlap(self):
        await self._assert_launch_uses_claimed_worker(["cpu", "cuda"], "cpu", "cuda")

    async def test_gpu_first_pool_uses_claimed_cpu_for_permit_and_overlap(self):
        await self._assert_launch_uses_claimed_worker(["cuda", "cpu"], "cuda", "cpu")


if __name__ == "__main__":
    unittest.main()
