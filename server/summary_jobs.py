"""In-memory controls for active summary and OCR jobs."""

import asyncio

from server.api.schemas.summary import MangaSummaryRequest
from server.myqueue import SummaryQueueElement, task_queue


class SummaryJobController:
    def __init__(self):
        self._pause_events: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._requests: dict[str, MangaSummaryRequest] = {}
        self._ocr_tasks: dict[str, SummaryQueueElement] = {}

    def register_task(
        self,
        group_key: str,
        request: MangaSummaryRequest,
        task: asyncio.Task,
    ) -> asyncio.Event:
        pause_event = self._pause_events.setdefault(group_key, asyncio.Event())
        pause_event.set()
        self._tasks[group_key] = task
        self._requests[group_key] = request
        return pause_event

    def unregister_task(self, group_key: str, task: asyncio.Task | None = None) -> None:
        if task is None or self._tasks.get(group_key) == task:
            self._tasks.pop(group_key, None)
            self._pause_events.pop(group_key, None)
            self._requests.pop(group_key, None)
            self._ocr_tasks.pop(group_key, None)

    def register_ocr_task(self, group_key: str, ocr_task: SummaryQueueElement) -> None:
        self._ocr_tasks[group_key] = ocr_task

    def unregister_ocr_task(self, group_key: str) -> None:
        self._ocr_tasks.pop(group_key, None)

    def get_pause_event(self, group_key: str) -> asyncio.Event:
        event = self._pause_events.get(group_key)
        if event is None:
            event = asyncio.Event()
            event.set()
            self._pause_events[group_key] = event
        return event

    def pause(self, group_key: str) -> bool:
        event = self.get_pause_event(group_key)
        event.clear()
        return True

    def resume(self, group_key: str) -> bool:
        event = self.get_pause_event(group_key)
        event.set()
        return True

    def stop(self, group_key: str) -> bool:
        event = self._pause_events.pop(group_key, None)
        if event is not None:
            event.set()
        ocr_task = self._ocr_tasks.pop(group_key, None)
        if ocr_task is not None:
            asyncio.create_task(task_queue.remove(ocr_task))
        self._requests.pop(group_key, None)
        task = self._tasks.pop(group_key, None)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def is_paused(self, group_key: str) -> bool:
        event = self._pause_events.get(group_key)
        return event is not None and not event.is_set()

    def get_task(self, group_key: str) -> asyncio.Task | None:
        return self._tasks.get(group_key)

    def get_request(self, group_key: str) -> MangaSummaryRequest | None:
        return self._requests.get(group_key)
