"""Bounded CPU lanes keep interactive work independent of batch work."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import itertools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from typing import Any

CPU_PRIORITY_INTERACTIVE = 0
CPU_PRIORITY_NORMAL = 1
CPU_PRIORITY_BACKGROUND = 2
logger = logging.getLogger(__name__)

_CPU_EXECUTOR_LOCK = threading.Lock()
_CPU_STAGE_WORKERS = 1
_CPU_STAGE_EXECUTORS: tuple[ThreadPoolExecutor, ThreadPoolExecutor] | None = None
_CPU_ACTIVE_LOCK = threading.Lock()
_CPU_ACTIVE = {"interactive": 0, "background": 0}


def configure_cpu_stage_workers(background_workers: int) -> None:
    """Set the process-wide background CPU capacity before pipeline work starts."""
    global _CPU_STAGE_WORKERS, _CPU_STAGE_EXECUTORS
    if background_workers < 1:
        raise ValueError("background_workers must be at least 1")
    with _CPU_EXECUTOR_LOCK:
        if _CPU_STAGE_WORKERS == background_workers:
            return
        executors = _CPU_STAGE_EXECUTORS
        _CPU_STAGE_EXECUTORS = None
        _CPU_STAGE_WORKERS = background_workers
    if executors is not None:
        for executor in executors:
            executor.shutdown(wait=True)


def cpu_stage_workers() -> int:
    with _CPU_EXECUTOR_LOCK:
        return _CPU_STAGE_WORKERS


def _cpu_stage_executors() -> tuple[ThreadPoolExecutor, ThreadPoolExecutor]:
    global _CPU_STAGE_EXECUTORS
    with _CPU_EXECUTOR_LOCK:
        if _CPU_STAGE_EXECUTORS is None:
            _CPU_STAGE_EXECUTORS = (
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="cpu-interactive"),
                ThreadPoolExecutor(max_workers=_CPU_STAGE_WORKERS, thread_name_prefix="cpu-background"),
            )
        return _CPU_STAGE_EXECUTORS


class _CpuLane:
    def __init__(self, name: str, executor: ThreadPoolExecutor, concurrency: int) -> None:
        self.name = name
        self.executor = executor
        self.concurrency = concurrency
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.sequence = itertools.count()
        self.workers = [asyncio.create_task(self._work()) for _ in range(concurrency)]

    async def run(self, function: Callable[..., Any], args: tuple, kwargs: dict, priority: int):
        future = asyncio.get_running_loop().create_future()
        logger.debug(
            "cpu_stage event=waiting lane=%s function=%s queued=%d",
            self.name,
            getattr(function, "__name__", type(function).__name__),
            self.queue.qsize(),
        )
        self.queue.put_nowait((priority, next(self.sequence), function, args, kwargs, future))
        return await future

    async def _work(self) -> None:
        while True:
            _, _, function, args, kwargs, future = await self.queue.get()
            try:
                if future.cancelled():
                    continue
                context = contextvars.copy_context()
                result = await asyncio.get_running_loop().run_in_executor(
                    self.executor, context.run, _call, function, args, kwargs,
                    self.name, self.concurrency,
                )
                if not future.done():
                    future.set_result(result)
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except Exception as error:
                if not future.done():
                    future.set_exception(error)
            finally:
                self.queue.task_done()

    async def close(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        while not self.queue.empty():
            try:
                _, _, _, _, _, future = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not future.done():
                future.cancel()
            self.queue.task_done()


class _CpuStageExecutor:
    def __init__(self) -> None:
        interactive_executor, background_executor = _cpu_stage_executors()
        self.interactive = _CpuLane("interactive", interactive_executor, 1)
        self.background = _CpuLane("background", background_executor, cpu_stage_workers())

    async def run(self, function: Callable[..., Any], args: tuple, kwargs: dict, priority: int):
        lane = self.background if priority >= CPU_PRIORITY_BACKGROUND else self.interactive
        return await lane.run(function, args, kwargs, priority)

    async def close(self) -> None:
        await asyncio.gather(self.interactive.close(), self.background.close())


_CV2_THREAD_LOCK = threading.Lock()
_CV2_THREADS_LIMITED = False


def _call(
    function: Callable[..., Any], args: tuple, kwargs: dict, lane: str, capacity: int,
) -> Any:
    global _CV2_THREADS_LIMITED
    started = time.monotonic()
    with _CPU_ACTIVE_LOCK:
        _CPU_ACTIVE[lane] += 1
        active = _CPU_ACTIVE[lane]
    logger.debug(
        "cpu_stage event=started lane=%s function=%s active=%d/%d",
        lane, getattr(function, "__name__", type(function).__name__), active, capacity,
    )
    try:
        try:
            import cv2

            if not _CV2_THREADS_LIMITED:
                with _CV2_THREAD_LOCK:
                    if not _CV2_THREADS_LIMITED:
                        cv2.setNumThreads(1)
                        _CV2_THREADS_LIMITED = True
        except ImportError:
            pass
        result = function(*args, **kwargs)
        return asyncio.run(result) if inspect.isawaitable(result) else result
    finally:
        with _CPU_ACTIVE_LOCK:
            _CPU_ACTIVE[lane] -= 1
            active = _CPU_ACTIVE[lane]
        logger.debug(
            "cpu_stage event=completed lane=%s function=%s elapsed_ms=%d active=%d/%d",
            lane, getattr(function, "__name__", type(function).__name__),
            int((time.monotonic() - started) * 1000), active, capacity,
        )


_EXECUTOR_ATTRIBUTE = "_manga_cpu_stage_executor"


async def shutdown_cpu_stage_executor() -> None:
    """Stop the CPU queue workers owned by the current event loop."""
    loop = asyncio.get_running_loop()
    executor = getattr(loop, _EXECUTOR_ATTRIBUTE, None)
    if executor is not None:
        delattr(loop, _EXECUTOR_ATTRIBUTE)
        await executor.close()


def shutdown_shared_cpu_stage_executor() -> None:
    """Stop the process-wide CPU pools after all event-loop lanes have closed."""
    global _CPU_STAGE_EXECUTORS
    with _CPU_EXECUTOR_LOCK:
        executors = _CPU_STAGE_EXECUTORS
        _CPU_STAGE_EXECUTORS = None
    if executors is not None:
        for executor in executors:
            executor.shutdown(wait=True)


async def run_cpu_stage(
    function: Callable[..., Any],
    *args: Any,
    priority: int = CPU_PRIORITY_NORMAL,
    **kwargs: Any,
) -> Any:
    """Run work on a bounded interactive or background lane."""
    loop = asyncio.get_running_loop()
    executor = getattr(loop, _EXECUTOR_ATTRIBUTE, None)
    if executor is None:
        executor = _CpuStageExecutor()
        setattr(loop, _EXECUTOR_ATTRIBUTE, executor)
    return await executor.run(function, args, kwargs, priority)
