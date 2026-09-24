import asyncio
import inspect
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from functools import wraps


_active_cache: ContextVar[dict | None] = ContextVar('active_model_cache', default=None)
_active_executor = ContextVar('active_model_executor', default=None)
MODEL_EXECUTOR_CONCURRENCY = 2
_model_cache_lock = threading.RLock()
logger = logging.getLogger(__name__)


class CachedModelEntry:
    def __init__(self, model_key, stage, model):
        self.model_key = model_key
        self.stage = stage
        self.model = model
        self.last_used_at = time.monotonic()
        self.loaded_at = time.monotonic() if _model_is_loaded(model) else None
        self.active_users = 0


def _model_is_loaded(model):
    is_loaded = getattr(model, 'is_loaded', None)
    return is_loaded() if callable(is_loaded) else True


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
        self._registry_lock = threading.RLock()
        self._model_entries = {}
        self._last_cleanup_at = 0.0

    def acquire_model(self, name, key, model):
        identity = (name, key)
        with self._registry_lock:
            entry = self._model_entries.get(identity)
            if entry is None or entry.model is not model:
                entry = self._model_entries[identity] = CachedModelEntry(key, name, model)
            entry.last_used_at = time.monotonic()
            if entry.loaded_at is None and _model_is_loaded(model):
                entry.loaded_at = time.monotonic()
            active = getattr(self._worker, 'active_model_keys', None)
            if active is not None and identity not in active:
                active.add(identity)
                entry.active_users += 1

    def release_active_models(self):
        active = getattr(self._worker, 'active_model_keys', None)
        if active is None:
            return
        with self._registry_lock:
            for identity in active:
                entry = self._model_entries.get(identity)
                if entry is not None:
                    entry.active_users = max(0, entry.active_users - 1)
                    entry.last_used_at = time.monotonic()
                    if entry.loaded_at is None and _model_is_loaded(entry.model):
                        entry.loaded_at = entry.last_used_at
            active.clear()

    async def cleanup_models(self, ttl: int):
        if ttl <= 0:
            return
        now = time.monotonic()
        with self._registry_lock:
            if now - self._last_cleanup_at < 15:
                return
            self._last_cleanup_at = now
        await self.run_exclusive(self._cleanup_models, ttl)

    async def _cleanup_models(self, ttl: int):
        from .device_memory import empty_device_cache

        now = time.monotonic()
        with self._registry_lock:
            candidates = [
                (identity, entry, now - entry.last_used_at)
                for identity, entry in self._model_entries.items()
                if entry.active_users == 0
            ]
        candidates.sort(key=lambda item: item[1].last_used_at)

        # TTL cleanup is always safe to do first. Memory thresholds use process RSS
        # because MPS and CPU weights share the machine's unified memory budget.
        expired = [item for item in candidates if item[2] >= ttl]
        for identity, entry, idle_seconds in expired:
            await self._evict_model(identity, entry, f'ttl idle={idle_seconds:.0f}s')
        if expired:
            empty_device_cache(memory_label='model_cache_ttl')

        try:
            import psutil
            memory = psutil.virtual_memory()
            rss = psutil.Process(os.getpid()).memory_info().rss
        except Exception:
            return

        soft_limit = memory.total * 0.60
        hard_limit = memory.total * 0.72
        if rss < soft_limit:
            return
        aggressive = rss >= hard_limit

        with self._registry_lock:
            candidates = sorted(
                ((identity, entry) for identity, entry in self._model_entries.items()
                 if entry.active_users == 0),
                key=lambda item: item[1].last_used_at,
            )
        logger.info(
            'Model cache memory pressure rss=%.0fMB soft=%.0fMB hard=%.0fMB idle_models=%d',
            rss / (1024 * 1024), soft_limit / (1024 * 1024),
            hard_limit / (1024 * 1024), len(candidates),
        )
        for identity, entry in candidates:
            if not aggressive and rss < soft_limit:
                break
            await self._evict_model(identity, entry, f'memory pressure rss={rss / (1024 * 1024):.0f}MB')
            empty_device_cache(memory_label='model_cache_pressure')
            try:
                rss = psutil.Process(os.getpid()).memory_info().rss
            except Exception:
                break

        if aggressive or rss >= hard_limit:
            empty_device_cache(collect_twice=True, memory_label='model_cache_hard_pressure')

    async def _evict_model(self, identity, entry, reason):
        with self._registry_lock:
            current = self._model_entries.get(identity)
            if current is not entry or entry.active_users:
                return
        name, key = identity
        try:
            await _unload_model_cache_entry(name, key)
        except Exception:
            logger.exception('Failed to unload cached model stage=%s key=%s', name, key)
            return
        with self._registry_lock:
            if self._model_entries.get(identity) is entry:
                self._model_entries.pop(identity, None)
        logger.info(
            'Unloaded cached model stage=%s key=%s reason=%s idle=%.1fs active_users=%d',
            name, key, reason, time.monotonic() - entry.last_used_at, entry.active_users,
        )

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
            self._worker.active_model_keys = set()
            token = set_model_cache(self._cache)
            try:
                return loop.run_until_complete(operation(*args, **kwargs))
            finally:
                self.release_active_models()
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
        async def unload_all():
            with self._registry_lock:
                entries = list(self._model_entries.items())
            for identity, entry in entries:
                await self._evict_model(identity, entry, 'executor shutdown')

        if self._model_entries:
            asyncio.run(self.run_exclusive(unload_all))
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
        with self._registry_lock:
            self._model_entries.clear()


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
            logger.info('Created model cache entry stage=%s key=%s', name, key)
        model = cache[key]
        executor = get_model_executor()
        if executor is not None and getattr(executor._worker, 'active', False):
            executor.acquire_model(name, key, model)
        return model


def remove_cached_model(name: str, default: dict, key):
    with _model_cache_lock:
        return get_model_cache(name, default).pop(key, None)


def clear_model_cache(name: str, default: dict):
    with _model_cache_lock:
        get_model_cache(name, default).clear()


async def unload_cached_model(name: str, default: dict, key):
    with _model_cache_lock:
        cache = get_model_cache(name, default)
        model = cache.get(key)
    if model is None:
        return
    unload = getattr(model, 'unload', None)
    if unload is not None:
        result = unload()
        if inspect.isawaitable(result):
            await result
    with _model_cache_lock:
        if cache.get(key) is model:
            cache.pop(key, None)


async def _unload_model_cache_entry(name, key):
    if name == 'bubble_detector':
        from ..detection.bubble import unload
        await unload(key)
        return
    unloaders = {
        'colorizer': ('..colorization', 'unload'),
        'detector': ('..detection', 'unload'),
        'inpainter': ('..inpainting', 'unload'),
        'ocr': ('..ocr', 'unload'),
        'translator': ('..translators', 'unload'),
        'upscaler': ('..upscaling', 'unload'),
    }
    module_name, function_name = unloaders[name]
    from importlib import import_module
    module = import_module(module_name, __package__)
    await getattr(module, function_name)(key)
