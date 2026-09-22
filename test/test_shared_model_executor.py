import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from manga_translator.utils.model_cache import (
    SharedModelExecutor, get_model_cache, model_operation,
    reset_model_executor, set_model_executor,
    reset_model_cache, set_model_cache,
)


class SharedModelExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.executor = SharedModelExecutor()
        self.token = set_model_executor(self.executor)

    async def asyncTearDown(self):
        reset_model_executor(self.token)
        self.executor.close()

    async def test_models_share_one_cache_and_thread_while_pipelines_overlap(self):
        active = peak = 0
        pipelines = pipeline_peak = 0
        threads = set()

        @model_operation
        async def stage(key):
            nonlocal active, peak
            threads.add(threading.get_ident())
            cache = get_model_cache('detector', {})
            model = cache.setdefault(key, object())
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return model

        async def pipeline(key):
            nonlocal pipelines, pipeline_peak
            pipelines += 1
            pipeline_peak = max(pipeline_peak, pipelines)
            model = await stage(key)
            await asyncio.sleep(0.01)  # A remote translation request.
            self.assertIs(await stage(key), model)
            pipelines -= 1
            return model

        models = await asyncio.gather(*(pipeline(key) for key in ('a', 'a', 'b', 'b')))
        self.assertEqual(peak, 1)
        self.assertEqual(pipeline_peak, 4)
        self.assertEqual(len(threads), 1)
        self.assertNotIn(threading.get_ident(), threads)
        self.assertIs(models[0], models[1])
        self.assertIs(models[2], models[3])
        self.assertIsNot(models[0], models[2])

    async def test_cancel_waits_for_running_model_before_releasing_image(self):
        started, release = threading.Event(), threading.Event()
        finished = []

        @model_operation
        async def stage():
            started.set()
            await asyncio.to_thread(release.wait)
            finished.append(True)

        task = asyncio.create_task(stage())
        await asyncio.to_thread(started.wait)
        task.cancel()
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(finished, [True])

    async def test_synchronize_on_failure_then_next_operation_succeeds(self):
        @model_operation
        async def stage(fail=False):
            if fail:
                raise ValueError('model failed')
            return 42

        with patch('torch.backends.mps.is_available', return_value=True), patch('torch.mps.synchronize') as sync:
            with self.assertRaisesRegex(ValueError, 'model failed'):
                await stage(True)
            self.assertEqual(await stage(), 42)
            self.assertEqual(sync.call_count, 2)

    async def test_nested_operations_do_not_deadlock(self):
        @model_operation
        async def inner():
            return threading.get_ident()

        @model_operation
        async def outer():
            return await inner()

        self.assertNotEqual(await asyncio.wait_for(outer(), 2), threading.get_ident())

    async def test_remote_requests_overlap_and_keep_their_own_settings(self):
        import manga_translator.translators as translators
        from manga_translator.config import Translator
        from manga_translator.manga_translator import MangaTranslator

        started = asyncio.Event()
        release = asyncio.Event()
        clients = []

        class Client:
            def __init__(self):
                clients.append(self)

            def parse_args(self, config):
                self.config = config

            async def translate(self, *args):
                started.set()
                await release.wait()
                return [self.config]

        owner = MangaTranslator.__new__(MangaTranslator)
        owner.device = 'mps'
        chain = SimpleNamespace(target_lang=None, chain=[(Translator.none, 'ENG')])

        async def request(label):
            token = set_model_cache({})
            try:
                return await owner._mps_call(translators.dispatch, chain, ['text'], label)
            finally:
                reset_model_cache(token)

        @model_operation
        async def gpu_stage():
            return 'GPU was available during remote requests'

        with patch.dict(translators.TRANSLATORS, {Translator.none: Client}):
            tasks = [asyncio.create_task(request(label)) for label in ('first', 'second')]
            await asyncio.wait_for(started.wait(), 2)
            self.assertEqual(await asyncio.wait_for(gpu_stage(), 2), 'GPU was available during remote requests')
            release.set()
            self.assertEqual(await asyncio.gather(*tasks), [['first'], ['second']])
        self.assertEqual(len(clients), 2)

    async def test_preparation_inference_and_unload_use_same_shared_model(self):
        from manga_translator import ocr
        from manga_translator.config import Ocr

        calls = []

        class Model(ocr.OfflineOCR):
            _MODEL_MAPPING = {}

            async def download(self):
                calls.append(('prepare', id(self), threading.get_ident()))

            async def load(self, device):
                calls.append(('load', id(self), threading.get_ident()))

            async def _load(self, device):
                pass

            async def _unload(self):
                pass

            async def _infer(self, *args):
                pass

            async def recognize(self, *args):
                calls.append(('infer', id(self), threading.get_ident()))
                return []

        with patch.dict(ocr.OCRS, {Ocr.ocr48px: Model}):
            await ocr.prepare(Ocr.ocr48px, 'mps')
            await ocr.dispatch(Ocr.ocr48px, None, [], device='mps')
            self.assertEqual(len({call[1] for call in calls}), 1)
            self.assertEqual(len({call[2] for call in calls}), 1)
            self.assertNotEqual(calls[0][2], threading.get_ident())
            await ocr.unload(Ocr.ocr48px)
            self.assertNotIn(Ocr.ocr48px, self.executor._cache['ocr'])

    async def test_offline_translation_shares_model_without_mixing_presets(self):
        import manga_translator.translators as translators
        from manga_translator.config import Translator

        instances = []

        class LocalModel:
            def __init__(self):
                instances.append(self)

            async def load(self, *args):
                pass

            def parse_args(self, config):
                self.config = config

            async def translate(self, *args):
                await asyncio.sleep(0.01)
                return [self.config]

        chain = SimpleNamespace(target_lang=None, chain=[(Translator.sugoi, 'ENG')])

        async def request(label):
            token = set_model_cache({})
            try:
                return await translators.dispatch(chain, ['text'], label, device='mps')
            finally:
                reset_model_cache(token)

        with patch.object(translators, 'OfflineTranslator', LocalModel), \
                patch.dict(translators.TRANSLATORS, {Translator.sugoi: LocalModel}):
            self.assertEqual(await asyncio.gather(request('first'), request('second')),
                             [['first'], ['second']])
        self.assertEqual(len(instances), 1)

    async def test_mps_cpu_fallback_handles_image_arrays(self):
        import numpy as np
        from manga_translator.manga_translator import MangaTranslator

        owner = MangaTranslator.__new__(MangaTranslator)
        owner.device = 'mps'
        image = np.zeros((2, 2, 3))
        devices = []

        @model_operation
        async def stage(image_arg, device):
            self.assertIs(image_arg, image)
            devices.append(device)
            if device == 'mps':
                raise NotImplementedError('not supported on mps')
            return 'ok'

        self.assertEqual(await owner._mps_call(stage, image, 'mps'), 'ok')
        self.assertEqual(devices, ['mps', 'cpu'])

    async def test_bubble_detection_shares_model_and_thread(self):
        from manga_translator.detection import bubble
        from manga_translator.config import BubbleDetectionConfig
        import numpy as np

        calls = []

        class MockBubbleDetector:
            def __init__(self, model, confidence, mask_threshold, image_size, device):
                calls.append(('init', id(self), threading.get_ident(), device))
                self.device = device

            def __call__(self, image):
                calls.append(('infer', id(self), threading.get_ident(), self.device))
                return [bubble.BubbleDetection(np.zeros((10, 10), dtype=np.uint8), 0.9)]

        cfg = BubbleDetectionConfig(model="manga109", confidence=0.5, mask_threshold=0.5, image_size=512)
        with patch.object(bubble, 'BubbleDetector', MockBubbleDetector):
            await bubble.prepare(cfg, device='mps')
            results = await bubble.dispatch(np.zeros((10, 10, 3), dtype=np.uint8), cfg, device='mps')
            self.assertEqual(len(results), 1)
            self.assertEqual(len({call[1] for call in calls}), 1)
            self.assertEqual(len({call[2] for call in calls}), 1)
            self.assertNotEqual(calls[0][2], threading.get_ident())
            self.assertEqual(calls[0][3], 'mps')
            self.assertIn(('manga109', 'mps', 0.5, 0.5, 512), self.executor._cache['bubble_detector'])
            await bubble.unload()
            self.assertEqual(len(self.executor._cache['bubble_detector']), 0)

    def test_macos_multiworker_does_not_switch_to_subprocesses(self):
        from server import main
        args = SimpleNamespace(start_instance=True, workers=4, executor_mode='inprocess', use_gpu=True)
        with patch.object(main, '_init_server_environment'), patch.object(main.sys, 'platform', 'darwin'), \
                patch.object(main, '_setup_inprocess_workers', return_value=[]) as inprocess, \
                patch.object(main, '_setup_subprocess_workers') as subprocess:
            self.assertEqual(main.prepare(args), [])
            inprocess.assert_called_once_with(args, 4)
            subprocess.assert_not_called()


if __name__ == '__main__':
    unittest.main()
