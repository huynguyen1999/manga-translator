import asyncio
import pickle
import threading
from typing import List, Optional
from PIL import Image

from manga_translator import Config, Context
from manga_translator.pipeline.cpu import shutdown_cpu_stage_executor
from manga_translator.utils.model_cache import (
    SharedModelExecutor, finish_before_cancelling,
    reset_model_cache, set_model_cache, reset_model_executor, set_model_executor,
)
from manga_translator.utils.device_memory import empty_device_cache, log_memory_stats
from manga_translator.utils.log import get_correlation_id, correlation_id_ctx
from server.sent_data_internal import NotifyType

def _serialize_result(result):
    if hasattr(result, 'use_placeholder') and result.use_placeholder:
        minimal_result = Context()
        minimal_result.result = Image.new('RGB', (1, 1), color='white')
        minimal_result.use_placeholder = True
        return pickle.dumps(minimal_result)
    return pickle.dumps(result)

class InProcessExecutorInstance:
    """
    An in-process translation executor that shares global model weights
    in RAM/VRAM while providing isolated request hooks, image contexts,
    and progress reporting.
    """
    def __init__(self, worker_id: int = 0, translator_params: Optional[dict] = None,
                 model_executor: Optional[SharedModelExecutor] = None):
        from manga_translator import MangaTranslator
        self.worker_id = worker_id
        self.busy = False
        self.translator_params = translator_params or {}
        self.translator = MangaTranslator(self.translator_params)
        self._owns_model_executor = model_executor is None
        self._model_executor = model_executor or SharedModelExecutor()
        # Only remote translation clients live here. Local models belong to the shared executor.
        self._model_cache = {}
        self._translation_loop = None
        self._translation_loop_ready = threading.Event()
        self._translation_thread = threading.Thread(
            target=self._run_translation_loop,
            name=f"translator-{worker_id}",
            daemon=True,
        )
        self._translation_thread.start()
        self._translation_loop_ready.wait()

    def _run_translation_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._translation_loop = loop
        self._translation_loop_ready.set()
        loop.run_forever()

    async def _run_translation(self, operation):
        cid = get_correlation_id()
        async def run():
            cid_token = correlation_id_ctx.set(cid)
            cache_token = set_model_cache(self._model_cache)
            executor_token = set_model_executor(self._model_executor)
            try:
                return await operation()
            finally:
                reset_model_executor(executor_token)
                reset_model_cache(cache_token)
                correlation_id_ctx.reset(cid_token)

        future = asyncio.run_coroutine_threadsafe(run(), self._translation_loop)
        return await finish_before_cancelling(asyncio.wrap_future(future))

    def close(self):
        async def cleanup():
            await shutdown_cpu_stage_executor()
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._model_cache.clear()
            await self._translation_loop.shutdown_asyncgens()
            await self._translation_loop.shutdown_default_executor()

        asyncio.run_coroutine_threadsafe(cleanup(), self._translation_loop).result()
        self._translation_loop.call_soon_threadsafe(self._translation_loop.stop)
        self._translation_thread.join()
        self._translation_loop.close()
        if self._owns_model_executor:
            self._model_executor.close()

    def free_executor(self):
        self.busy = False

    async def reclaim_memory(self):
        async def cleanup():
            batch_id = getattr(self.translator, "_memory_batch_id", None)
            before = log_memory_stats(
                "batch_cleanup:start",
                device=self.translator.device,
                batch_id=batch_id,
            )
            empty_device_cache(
                self.translator.device,
                collect_twice=True,
                memory_label="batch_cleanup",
                batch_id=batch_id,
            )
            log_memory_stats(
                "batch_end",
                device=self.translator.device,
                batch_id=batch_id,
                before=before,
            )
            self.translator._memory_batch_id = None

        await self._model_executor.run_exclusive(cleanup)

    async def sent(self, image: Image.Image, config: Config) -> Context:
        self.translator._is_streaming_mode = getattr(config, '_web_frontend_optimized', False)
        return await self._run_translation(lambda: self.translator.translate(image, config))

    async def extract_text(self, image: Image.Image, config: Config) -> Context:
        self.translator._is_streaming_mode = getattr(config, '_web_frontend_optimized', False)
        return await self._run_translation(
            lambda: self.translator.extract_text(image, config)
        )

    async def sent_stream(self, image: Image.Image, config: Config, sender: NotifyType):
        loop = asyncio.get_running_loop()

        def send(code, data):
            if sender:
                loop.call_soon_threadsafe(sender, code, data)

        async def progress_hook(state: str, finished: bool):
            send(1, state.encode("utf-8"))
            await asyncio.sleep(0)

        self.translator.add_progress_hook(progress_hook)
        try:
            self.translator._is_streaming_mode = getattr(config, '_web_frontend_optimized', False)
            result = await self._run_translation(lambda: self.translator.translate(image, config))
            send(0, await asyncio.to_thread(_serialize_result, result))
        except Exception as e:
            send(2, str(e).encode("utf-8"))
            raise
        finally:
            if progress_hook in self.translator._progress_hooks:
                self.translator._progress_hooks.remove(progress_hook)

    async def prepare(self, image: Image.Image, config: Config) -> Context:
        self.translator._is_streaming_mode = getattr(config, '_web_frontend_optimized', False)
        return await self._run_translation(
            lambda: self.translator.prepare(image, config)
        )

    async def translate_batch_contexts(
        self, contexts_with_configs: List[tuple], batch_size: int = None
    ) -> List[tuple]:
        configs = [cfg for _, cfg in contexts_with_configs]
        self.translator._is_streaming_mode = any(getattr(item, '_web_frontend_optimized', False) for item in configs)
        return await self._run_translation(
            lambda: self.translator.translate_batch_contexts(contexts_with_configs, batch_size=batch_size)
        )

    async def render(self, ctx: Context, config: Config) -> Context:
        self.translator._is_streaming_mode = getattr(config, '_web_frontend_optimized', False)
        return await self._run_translation(
            lambda: self.translator.render(ctx, config)
        )

    async def render_saved(self, ctx: Context, config: Config) -> Context:
        self.translator._is_streaming_mode = getattr(config, '_web_frontend_optimized', False)
        return await self._run_translation(
            lambda: self.translator.render_saved(ctx, config)
        )

    async def translate_and_render_batch(
        self, contexts_with_configs: List[tuple], batch_size: int = None
    ) -> List[Context]:
        configs = [cfg for _, cfg in contexts_with_configs]
        self.translator._is_streaming_mode = any(getattr(item, '_web_frontend_optimized', False) for item in configs)
        return await self._run_translation(
            lambda: self.translator.translate_and_render_batch(contexts_with_configs, batch_size=batch_size)
        )

    async def sent_batch(self, images: List[Image.Image], config: Config | List[Config], batch_size: int) -> List[Context]:
        configs = config if isinstance(config, list) else [config] * len(images)
        images_with_configs = list(zip(images, configs))
        self.translator._is_streaming_mode = any(getattr(item, '_web_frontend_optimized', False) for item in configs)
        return await self._run_translation(
            lambda: self.translator.translate_batch(images_with_configs, batch_size=batch_size)
        )

    async def sent_batch_stream(self, images: List[Image.Image], config: Config, batch_size: int, sender: NotifyType):
        loop = asyncio.get_running_loop()

        def send(code, data):
            if sender:
                loop.call_soon_threadsafe(sender, code, data)

        async def progress_hook(state: str, finished: bool):
            send(1, state.encode("utf-8"))
            await asyncio.sleep(0)

        self.translator.add_progress_hook(progress_hook)
        try:
            images_with_configs = [(img, config) for img in images]
            results = await self._run_translation(
                lambda: self.translator.translate_batch(images_with_configs, batch_size=batch_size)
            )
            send(0, await asyncio.to_thread(pickle.dumps, results))
        except Exception as e:
            send(2, str(e).encode("utf-8"))
            raise
        finally:
            if progress_hook in self.translator._progress_hooks:
                self.translator._progress_hooks.remove(progress_hook)
