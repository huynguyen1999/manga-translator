import asyncio
import gc
import threading
import unittest

from manga_translator.pipeline.cpu import (
    CPU_PRIORITY_BACKGROUND,
    CPU_PRIORITY_INTERACTIVE,
    configure_cpu_stage_workers,
    cpu_stage_workers,
    run_cpu_stage,
    shutdown_cpu_stage_executor,
    shutdown_shared_cpu_stage_executor,
)


class CpuStageExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await shutdown_cpu_stage_executor()
        await asyncio.to_thread(shutdown_shared_cpu_stage_executor)

    async def test_background_lane_runs_multiple_jobs_within_capacity(self):
        configure_cpu_stage_workers(3)
        release = threading.Event()
        three_started = threading.Event()
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def work():
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 3:
                    three_started.set()
            release.wait(timeout=2)
            with lock:
                active -= 1

        jobs = [
            asyncio.create_task(run_cpu_stage(work, priority=CPU_PRIORITY_BACKGROUND))
            for _ in range(3)
        ]
        try:
            self.assertTrue(await asyncio.to_thread(three_started.wait, 1))
        finally:
            release.set()
            await asyncio.gather(*jobs)
            await shutdown_cpu_stage_executor()

        self.assertEqual(maximum_active, 3)

    async def test_background_capacity_is_shared_across_event_loops(self):
        configure_cpu_stage_workers(2)
        release = threading.Event()
        two_started = threading.Event()
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def work():
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 2:
                    two_started.set()
            release.wait(timeout=2)
            with lock:
                active -= 1

        def run_on_own_loop():
            async def run():
                try:
                    await run_cpu_stage(work, priority=CPU_PRIORITY_BACKGROUND)
                finally:
                    await shutdown_cpu_stage_executor()

            asyncio.run(run())

        loop = asyncio.get_running_loop()
        jobs = [loop.run_in_executor(None, run_on_own_loop) for _ in range(3)]
        try:
            self.assertTrue(await asyncio.to_thread(two_started.wait, 1))
            await asyncio.sleep(0.02)
            self.assertEqual(maximum_active, 2)
        finally:
            release.set()
            await asyncio.gather(*jobs)

        self.assertEqual(maximum_active, cpu_stage_workers())

    async def test_lane_workers_survive_collection_and_stop_with_the_loop(self):
        configure_cpu_stage_workers(1)
        await run_cpu_stage(lambda: None)
        gc.collect()

        workers = [
            task for task in asyncio.all_tasks()
            if task.get_coro().__name__ == "_work"
        ]
        self.assertEqual(len(workers), cpu_stage_workers() + 1)

        await shutdown_cpu_stage_executor()
        self.assertTrue(all(task.done() for task in workers))

    async def test_lane_is_bounded_responsive_and_prioritizes_interactive_work(self):
        configure_cpu_stage_workers(1)
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
