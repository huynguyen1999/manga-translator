import asyncio
import gc
import threading
import unittest

from manga_translator.pipeline.cpu import (
    CPU_PRIORITY_BACKGROUND,
    CPU_PRIORITY_INTERACTIVE,
    run_cpu_stage,
    shutdown_cpu_stage_executor,
)


class CpuStageExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_lane_workers_survive_collection_and_stop_with_the_loop(self):
        await run_cpu_stage(lambda: None)
        gc.collect()

        workers = [
            task for task in asyncio.all_tasks()
            if task.get_coro().__name__ == "_work"
        ]
        self.assertEqual(len(workers), 2)

        await shutdown_cpu_stage_executor()
        self.assertTrue(all(task.done() for task in workers))

    async def test_lane_is_bounded_responsive_and_prioritizes_interactive_work(self):
        started = threading.Event()
        release = threading.Event()
        active = 0
        maximum_active = 0
        order = []

        def work(name, wait=False):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            if wait:
                started.set()
                release.wait(timeout=1)
            order.append(name)
            active -= 1

        first = asyncio.create_task(
            run_cpu_stage(work, "first", True, priority=CPU_PRIORITY_BACKGROUND)
        )
        self.assertTrue(await asyncio.to_thread(started.wait, 1))

        background = asyncio.create_task(
            run_cpu_stage(work, "background", priority=CPU_PRIORITY_BACKGROUND)
        )
        interactive_started = threading.Event()

        def interactive_work():
            work("interactive")
            interactive_started.set()

        interactive = asyncio.create_task(
            run_cpu_stage(interactive_work, priority=CPU_PRIORITY_INTERACTIVE)
        )
        heartbeats = 0

        async def heartbeat():
            nonlocal heartbeats
            while not first.done():
                heartbeats += 1
                await asyncio.sleep(0.001)

        heartbeat_task = asyncio.create_task(heartbeat())
        self.assertTrue(await asyncio.to_thread(interactive_started.wait, 1))
        await asyncio.sleep(0.02)
        release.set()
        await asyncio.gather(first, background, interactive, heartbeat_task)

        self.assertLess(order.index("interactive"), order.index("first"))
        self.assertEqual(maximum_active, 2)
        self.assertGreater(heartbeats, 1)

    async def test_lane_runs_async_cpu_callables(self):
        async def work(value):
            return value + 1

        self.assertEqual(await run_cpu_stage(work, 4), 5)


if __name__ == "__main__":
    unittest.main()
