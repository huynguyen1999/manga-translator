"""Persistent manga synopsis scheduler."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from server.logger import get_logger

logger = get_logger("summary_scheduler")


class SummaryScheduler:
    def __init__(
        self,
        store_getter: Callable[[], Any],
        controller: Any,
        run_task_fn: Callable[..., Any],
        request_cls: Any,
        result_root: str | Path,
        max_concurrent: int = 1,
        max_concurrent_total: int = 10,
    ):
        self.store_getter = store_getter
        self.controller = controller
        self.run_task_fn = run_task_fn
        self.request_cls = request_cls
        self.result_root = Path(result_root).resolve()
        self.max_concurrent_extraction = max_concurrent
        self.max_concurrent_total = max_concurrent_total
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._running: dict[str, asyncio.Task] = {}
        self._extraction_running: set[str] = set()
        self._stopping_jobs: set[str] = set()
        self._closed = False

    def wake(self) -> None:
        self._wake.set()

    def release_extraction(self, *keys: str | None) -> None:
        to_remove = {k for k in keys if k and k in self._extraction_running}
        if to_remove:
            self._extraction_running.difference_update(to_remove)
            self.wake()

    async def reconcile(self) -> None:
        store = self.store_getter()
        if store is not None and hasattr(store, "reconcile_summary_jobs"):
            await store.reconcile_summary_jobs()
        elif store is None:
            from server.manga_summary import reconcile_summary_jobs
            await asyncio.to_thread(reconcile_summary_jobs, self.result_root)

    async def start(self) -> None:
        await self.reconcile()
        self._closed = False
        self._loop_task = asyncio.create_task(self._run(), name="summary-scheduler")
        self.wake()

    async def stop(self) -> None:
        self._closed = True
        self._wake.set()
        if self._loop_task:
            await self._loop_task
            self._loop_task = None
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_runnable(self) -> list[dict[str, Any]]:
        store = self.store_getter()
        if store is not None:
            list_fn = getattr(store, "list_runnable_summary_jobs", None)
            return await list_fn() if list_fn is not None else [j for j in await store.list_summary_jobs(100) if j.get("status") == "queued"]
        from server.manga_summary import list_runnable_summary_jobs
        return await asyncio.to_thread(list_runnable_summary_jobs, self.result_root)

    async def _run(self) -> None:
        while not self._closed:
            try:
                launched = await self._launch_available()
            except Exception as exc:
                logger.error(f"Error in summary scheduler launch loop: {exc}", exc_info=True)
                launched = False
            if launched:
                await asyncio.sleep(0)
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    async def _launch_available(self) -> bool:
        if len(self._running) >= self.max_concurrent_total or len(self._extraction_running) >= self.max_concurrent_extraction:
            return False
        runnable = await self._fetch_runnable()
        for job in runnable:
            group_id = job.get("groupId")
            clean_title = job.get("title") or "Ungrouped"
            group_key = group_id or clean_title
            if group_key in self._running or clean_title in self._running:
                continue
            if group_key in self._stopping_jobs or clean_title in self._stopping_jobs:
                continue
            if self.controller.is_paused(group_key) or self.controller.is_paused(clean_title):
                continue
            req_data = self.request_cls(
                groupId=group_id, mangaTitle=clean_title,
                summaryModel=(f"{job['provider']}:{job['model']}" if job.get("provider") and job.get("model") else job.get("model")),
                regenerate=bool(job.get("jobRegenerate", False)), refreshText=bool(job.get("jobRefreshText", False)),
            )
            pause_event = self.controller.get_pause_event(group_key)
            pause_event.set()
            store = self.store_getter()
            task = asyncio.create_task(
                self.run_task_fn(None, req_data, store, group_key, clean_title, worker=None, pause_event=pause_event),
                name=f"summary-{group_key}",
            )
            self.controller.register_task(group_key, req_data, task)
            self._running[group_key] = task
            self._extraction_running.add(group_key)

            def _on_done(_task, key=group_key, title=clean_title):
                self._running.pop(key, None)
                self._extraction_running.discard(key)
                if title:
                    self._extraction_running.discard(title)
                self.wake()

            task.add_done_callback(_on_done)
            return True
        return False
