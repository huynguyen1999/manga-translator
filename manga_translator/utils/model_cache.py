import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from functools import wraps


_active_cache: ContextVar[dict | None] = ContextVar('active_model_cache', default=None)
_active_executor = ContextVar('active_model_executor', default=None)


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
    """Own shared models and their entire load/infer/unload lifecycle on one thread."""

    def __init__(self):
        # ponytail: serialize local models; add device-specific lanes only if profiling warrants it.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='shared-models')
        self._cache = {}
        self._loop = None
        self._thread_id = None

    def _call(self, operation, args, kwargs):
        import torch
        from .device_memory import DEVICE_MEMORY_LOCK

        if self._loop is None:
            self._thread_id = threading.get_ident()
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        with DEVICE_MEMORY_LOCK:
            token = set_model_cache(self._cache)
            try:
                return self._loop.run_until_complete(operation(*args, **kwargs))
            finally:
                try:
                    if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                        torch.mps.synchronize()
                except Exception:
                    pass
                finally:
                    reset_model_cache(token)

    async def run(self, operation, *args, **kwargs):
        if threading.get_ident() == self._thread_id:
            return await operation(*args, **kwargs)
        context = copy_context()
        future = self._pool.submit(context.run, self._call, operation, args, kwargs)
        return await finish_before_cancelling(asyncio.wrap_future(future))

    def close(self):
        def cleanup():
            self._cache.clear()
            if self._loop is not None:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                self._loop.run_until_complete(self._loop.shutdown_default_executor())
                self._loop.close()
        self._pool.submit(cleanup).result()
        self._pool.shutdown()


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
