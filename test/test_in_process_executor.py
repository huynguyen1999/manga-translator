import asyncio
import threading
import unittest
from unittest.mock import patch

from server.in_process_executor import InProcessExecutorInstance
from server.main import _cpu_stage_worker_count, _cpu_threads_per_worker
from manga_translator.utils.inference import ModelWrapper
from manga_translator.utils.model_cache import SharedModelExecutor, get_model_cache, model_operation


class FakeModel(ModelWrapper):
    _MODEL_MAPPING = {}

    async def _load(self, device, *args, **kwargs):
        pass

    async def _unload(self):
        pass

    async def _infer(self, *args, **kwargs):
        pass


class FakeTranslator:
    def __init__(self, using_gpu=False):
        self._progress_hooks = []
        self.thread_id = None
        self.using_gpu = using_gpu

    def add_progress_hook(self, hook):
        self._progress_hooks.append(hook)

    async def translate(self, image, config):
        self.thread_id = threading.get_ident()
        for hook in list(self._progress_hooks):
            await hook("detecting", False)
        return {"image": image}


class InProcessExecutorTest(unittest.IsolatedAsyncioTestCase):
    def test_cpu_stage_budget_is_worker_and_cpu_bounded(self):
        self.assertEqual(_cpu_stage_worker_count(1, cpu_count=8), 1)
        self.assertEqual(_cpu_stage_worker_count(2, cpu_count=8), 2)
        self.assertEqual(_cpu_stage_worker_count(4, cpu_count=8), 3)
        self.assertEqual(_cpu_stage_worker_count(10, configured=4, cpu_count=8), 4)
        self.assertEqual(_cpu_stage_worker_count(2, configured=4, cpu_count=8), 2)

    def test_cpu_budget_reserves_capacity_for_api(self):
        self.assertEqual(_cpu_threads_per_worker(8, 1), 7)
        self.assertEqual(_cpu_threads_per_worker(8, 3), 2)
        self.assertEqual(_cpu_threads_per_worker(1, 1), 1)

    def test_shared_model_load_lock_is_thread_safe(self):
        model = FakeModel()

        self.assertIsInstance(model._load_lock, type(threading.Lock()))

    async def asyncSetUp(self):
        self.executor = object.__new__(InProcessExecutorInstance)
        self.executor.translator = FakeTranslator()
        self.executor._model_cache = {}
        self.executor._model_executor = SharedModelExecutor()
        self.executor._translation_loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.executor._translation_loop.run_forever, daemon=True)
        self.loop_thread.start()

    async def asyncTearDown(self):
        self.executor._translation_loop.call_soon_threadsafe(self.executor._translation_loop.stop)
        self.loop_thread.join(timeout=1)
        self.executor._translation_loop.close()
        self.executor._model_executor.close()

    async def test_translation_runs_off_api_event_loop(self):
        main_thread = threading.get_ident()

        result = await self.executor.sent("image", object())

        self.assertEqual(result, {"image": "image"})
        self.assertNotEqual(self.executor.translator.thread_id, main_thread)

    async def test_reclaim_memory_clears_device_cache_without_unloading_models(self):
        model = object()
        self.executor._model_executor._cache["ocr"] = {"default": model}
        self.executor.translator.device = "mps"
        self.executor.translator._memory_batch_id = "manga-1"

        with patch("server.in_process_executor.empty_device_cache") as cleanup:
            await self.executor.reclaim_memory()

        cleanup.assert_called_once_with(
            "mps", collect_twice=True, memory_label="batch_cleanup", batch_id="manga-1"
        )
        self.assertIsNone(self.executor.translator._memory_batch_id)
        self.assertIs(self.executor._model_executor._cache["ocr"]["default"], model)

    async def test_stream_callbacks_return_to_api_event_loop(self):
        callbacks = []
        main_thread = threading.get_ident()

        await self.executor.sent_stream(
            "image", object(), lambda code, data: callbacks.append((code, data, threading.get_ident()))
        )
        await asyncio.sleep(0)

        self.assertEqual([code for code, _, _ in callbacks], [1, 0])
        self.assertTrue(all(thread_id == main_thread for _, _, thread_id in callbacks))

    async def test_workers_overlap_with_shared_models_and_independent_client_caches(self):
        executors = []
        threads = []
        for _ in range(2):
            executor = object.__new__(InProcessExecutorInstance)
            executor.translator = FakeTranslator(using_gpu=True)
            executor._model_cache = {}
            executor._model_executor = self.executor._model_executor
            executor._translation_loop = asyncio.new_event_loop()
            thread = threading.Thread(target=executor._translation_loop.run_forever, daemon=True)
            thread.start()
            executors.append(executor)
            threads.append(thread)

        active = 0
        peak = 0

        @model_operation
        async def shared_model():
            return get_model_cache('test', {}).setdefault('model', object())

        async def operation():
            nonlocal active, peak
            cache = get_model_cache('test', {})
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return cache, await shared_model()

        try:
            caches = await asyncio.gather(*(executor._run_translation(operation) for executor in executors))
        finally:
            for executor, thread in zip(executors, threads):
                executor._translation_loop.call_soon_threadsafe(executor._translation_loop.stop)
                thread.join(timeout=1)
                executor._translation_loop.close()

        self.assertEqual(peak, 2)
        self.assertIsNot(caches[0][0], caches[1][0])
        self.assertIs(caches[0][1], caches[1][1])
