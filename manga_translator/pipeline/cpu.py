"""Bounded CPU lanes keep interactive work independent of batch work."""

from __future__ import annotations

import asyncio
import inspect
import itertools
import threading
from collections.abc import Callable
from typing import Any

CPU_PRIORITY_INTERACTIVE = 0
CPU_PRIORITY_NORMAL = 1
CPU_PRIORITY_BACKGROUND = 2


class _CpuLane:
    def __init__(self) -> None:
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.sequence = itertools.count()
        self.worker = asyncio.create_task(self._work())

    async def run(self, function: Callable[..., Any], args: tuple, kwargs: dict, priority: int):
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait((priority, next(self.sequence), function, args, kwargs, future))
        return await future

    async def _work(self) -> None:
        while True:
            _, _, function, args, kwargs, future = await self.queue.get()
            try:
                if future.cancelled():
                    continue
                result = await asyncio.to_thread(_call, function, args, kwargs)
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
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
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
        self.interactive = _CpuLane()
        self.background = _CpuLane()

    async def run(self, function: Callable[..., Any], args: tuple, kwargs: dict, priority: int):
        lane = self.background if priority >= CPU_PRIORITY_BACKGROUND else self.interactive
        return await lane.run(function, args, kwargs, priority)

    async def close(self) -> None:
        await asyncio.gather(self.interactive.close(), self.background.close())


_CV2_THREAD_LOCK = threading.Lock()
_CV2_THREADS_LIMITED = False


def _call(function: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
    global _CV2_THREADS_LIMITED
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


_EXECUTOR_ATTRIBUTE = "_manga_cpu_stage_executor"


async def shutdown_cpu_stage_executor() -> None:
    """Stop the CPU lanes owned by the current event loop."""
    loop = asyncio.get_running_loop()
    executor = getattr(loop, _EXECUTOR_ATTRIBUTE, None)
    if executor is not None:
        delattr(loop, _EXECUTOR_ATTRIBUTE)
        await executor.close()


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
