import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from functools import wraps


_active_cache: ContextVar[dict | None] = ContextVar('active_model_cache', default=None)
_active_executor = ContextVar('active_model_executor', default=None)
MODEL_EXECUTOR_CONCURRENCY = 2
_model_cache_lock = threading.RLock()


def set_model_executor(executor):
    return _active_executor.set(executor)


def reset_model_executor(token):
    _active_executor.reset(token)


def get_model_executor():
    return _active_executor.get()


async def finish_before_cancelling(future):
    """Do not release an image/worker while its thread still uses request state."""
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not future.cancelled():
            future.exception()  # Retrieve an error even when the caller disconnected.
        raise asyncio.CancelledError
    return future.result()


class SharedModelExecutor:
    """Own shared models while bounding concurrent local model operations."""

    def __init__(self, max_concurrent_calls: int = MODEL_EXECUTOR_CONCURRENCY):
        self.max_concurrent_calls = max_concurrent_calls
        self._pool = ThreadPoolExecutor(
            max_workers=max_concurrent_calls, thread_name_prefix='shared-models'
        )
        self._cache = {}
        self._worker = threading.local()
        self._loops = {}
        self._loops_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_concurrent_calls)
        self._exclusive_lock = threading.Lock()

    def _call(self, operation, args, kwargs, exclusive=False):
        import torch
        from .device_memory import DEVICE_MEMORY_LOCK

        acquired_slots = 0
        exclusive_acquired = False
        try:
            if exclusive:
                self._exclusive_lock.acquire()
                exclusive_acquired = True
                for _ in range(self.max_concurrent_calls):
                    self._slots.acquire()
                    acquired_slots += 1
            else:
                self._slots.acquire()
                acquired_slots = 1

            loop = getattr(self._worker, 'loop', None)
            if loop is None:
                loop = asyncio.new_event_loop()
                self._worker.loop = loop
                with self._loops_lock:
                    self._loops[threading.get_ident()] = loop
                asyncio.set_event_loop(loop)
            self._worker.active = True
            token = set_model_cache(self._cache)
            try:
                return loop.run_until_complete(operation(*args, **kwargs))
            finally:
                try:
                    with DEVICE_MEMORY_LOCK:
                        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                            torch.mps.synchronize()
                except Exception:
                    pass
                finally:
                    reset_model_cache(token)
                    self._worker.active = False
        finally:
            for _ in range(acquired_slots):
                self._slots.release()
            if exclusive_acquired:
                self._exclusive_lock.release()

    async def run(self, operation, *args, **kwargs):
        if getattr(self._worker, 'active', False):
            return await operation(*args, **kwargs)
        context = copy_context()
        future = self._pool.submit(context.run, self._call, operation, args, kwargs)
        return await finish_before_cancelling(asyncio.wrap_future(future))

    async def run_exclusive(self, operation, *args, **kwargs):
        if getattr(self._worker, 'active', False):
            raise RuntimeError('Exclusive model cleanup cannot run inside a model operation')
        context = copy_context()
        future = self._pool.submit(context.run, self._call, operation, args, kwargs, True)
        return await finish_before_cancelling(asyncio.wrap_future(future))

    def close(self):
        self._pool.shutdown(wait=True)

        def close_loops():
            for loop in self._loops.values():
                asyncio.set_event_loop(loop)
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()

        closer = threading.Thread(target=close_loops, name='shared-model-cleanup')
        closer.start()
        closer.join()
        self._cache.clear()


def model_operation(operation):
    """Route local model work through the shared executor when running in-process."""
    @wraps(operation)
    async def run(*args, **kwargs):
        executor = get_model_executor()
        if executor is None:
            return await operation(*args, **kwargs)
        return await executor.run(operation, *args, **kwargs)
    return run


def set_model_cache(cache: dict):
    return _active_cache.set(cache)


def reset_model_cache(token):
    _active_cache.reset(token)


def get_model_cache(name: str, default: dict) -> dict:
    cache = _active_cache.get()
    if cache is None:
        return default
    return cache.setdefault(name, {})


def get_cached_model(name: str, default: dict, key, factory):
    """Return one shared model instance, creating it once across executor threads."""
    with _model_cache_lock:
        cache = get_model_cache(name, default)
        if key not in cache:
            cache[key] = factory()
        return cache[key]


def remove_cached_model(name: str, default: dict, key):
    with _model_cache_lock:
        return get_model_cache(name, default).pop(key, None)


def clear_model_cache(name: str, default: dict):
    with _model_cache_lock:
        get_model_cache(name, default).clear()
