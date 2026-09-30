import asyncio
import threading
from types import SimpleNamespace

from manga_translator.pipeline.cpu import (
    CPU_PRIORITY_BACKGROUND, configure_cpu_stage_workers, run_cpu_stage,
    shutdown_cpu_stage_executor, shutdown_shared_cpu_stage_executor,
)


def test_layout_waits_before_cpu_worker_and_other_stages_continue():
    async def run():
        configure_cpu_stage_workers(2)
        started, release, other_started = threading.Event(), threading.Event(), threading.Event()
        active, peak = 0, 0
        lock = threading.Lock()

        def layout_page(ctx):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            started.set()
            release.wait(2)
            with lock:
                active -= 1
            return ctx._layout_queue_wait_ms

        layout_page.__module__ = "manga_translator.rendering.layout.engine"
        first = asyncio.create_task(run_cpu_stage(layout_page, SimpleNamespace(), priority=CPU_PRIORITY_BACKGROUND))
        assert await asyncio.to_thread(started.wait, 1)
        second = asyncio.create_task(run_cpu_stage(layout_page, SimpleNamespace(), priority=CPU_PRIORITY_BACKGROUND))
        other = asyncio.create_task(run_cpu_stage(other_started.set, priority=CPU_PRIORITY_BACKGROUND))
        try:
            assert await asyncio.to_thread(other_started.wait, 1)
        finally:
            release.set()
            results = await asyncio.gather(first, second, other)
            await shutdown_cpu_stage_executor()
            await asyncio.to_thread(shutdown_shared_cpu_stage_executor)
        assert peak == 1
        assert results[1] > 0

    asyncio.run(run())


def test_batch_layout_admission_leaves_other_cpu_slot_available():
    from contextvars import ContextVar
    from server.batch_resource_policy import BatchResourceManager
    from manga_translator.pipeline.stages import ResourceClass

    async def run():
        manager = BatchResourceManager({ResourceClass.CPU_HEAVY: 2, ResourceClass.CPU_LIGHT: 2},
            logger=SimpleNamespace(debug=lambda *args: None), correlation_id=ContextVar("test_request", default=None))
        first = await manager.acquire_stage("layout")
        second = asyncio.create_task(manager.acquire_stage("layout"))
        await asyncio.sleep(0)
        assert not second.done()
        other = await asyncio.wait_for(manager.acquire_stage("rendering"), 1)
        manager.release_stage("rendering", other)
        manager.release_stage("layout", first)
        slot = await asyncio.wait_for(second, 1)
        manager.release_stage("layout", slot)
        assert manager.has_capacity(ResourceClass.CPU_HEAVY)

    asyncio.run(run())
