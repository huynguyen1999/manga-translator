import asyncio
import os
from typing import Awaitable, Callable, List, Optional

from PIL import Image
from fastapi import HTTPException
from fastapi.requests import Request

from manga_translator import Config
from server.instance import executor_instances
from server.sent_data_internal import NotifyType

class QueueElement:
    req: Request
    image: Image.Image | str
    config: Config

    def __init__(self, req: Request, image: Image.Image, config: Config, length):
        self.req = req
        if length > 10:
            #todo: store image in "upload-cache" folder
            self.image = image
        else:
            self.image = image
        self.config = config

    def get_image(self)-> Image:
        if isinstance(self.image, str):
            return Image.open(self.image)
        else:
            return self.image

    def __del__(self):
        if isinstance(self.image, str):
            os.remove(self.image)

    async def is_client_disconnected(self) -> bool:
        if await self.req.is_disconnected():
            return True
        return False


class BatchQueueElement:
    """Batch translation queue element"""
    req: Request
    images: List[Image.Image]
    config: Config
    batch_size: int

    def __init__(self, req: Request, images: List[Image.Image], config: Config, batch_size: int):
        self.req = req
        self.images = images
        self.config = config
        self.batch_size = batch_size

    async def is_client_disconnected(self) -> bool:
        if await self.req.is_disconnected():
            return True
        return False


class SummaryQueueElement:
    """A queued synopsis job that uses one translation worker."""

    # ponytail: one worker lease covers OCR and the model call; split leases if API wait starves translation.

    def __init__(self, run: Callable[[object], Awaitable[object]]):
        self.run = run

    async def is_client_disconnected(self) -> bool:
        return False


class TaskQueue:
    def __init__(self):
        self.queue: List[QueueElement | BatchQueueElement | SummaryQueueElement] = []
        self.queue_event: asyncio.Event = asyncio.Event()

    def add_task(self, task: QueueElement | BatchQueueElement | SummaryQueueElement):
        self.queue.append(task)

    def get_pos(self, task: QueueElement | BatchQueueElement | SummaryQueueElement) -> Optional[int]:
        try:
            return self.queue.index(task)
        except ValueError:
            return None
    async def update_event(self):
        self.queue_event.set()
        self.queue_event.clear()

    async def remove(self, task: QueueElement | BatchQueueElement | SummaryQueueElement):
        if task in self.queue:
            self.queue.remove(task)
        await self.update_event()

    async def wait_for_event(self):
        await self.queue_event.wait()

task_queue = TaskQueue()

async def wait_in_queue(task: QueueElement | BatchQueueElement | SummaryQueueElement, notify: NotifyType):
    """Wait in queue until an executor instance is available, then execute the task."""
    try:
        queue_pos = task_queue.get_pos(task)
        if notify and queue_pos is not None:
            notify(3, str(queue_pos).encode('utf-8'))

        # Acquire a free executor instance (suspends asynchronously until available)
        instance = await executor_instances.find_executor()
    except (asyncio.CancelledError, GeneratorExit):
        await task_queue.remove(task)
        raise

    try:
        await task_queue.remove(task)
        if notify:
            notify(4, b"")

        if await task.is_client_disconnected():
            if not notify:
                raise HTTPException(499, detail="Client disconnected")
            return

        if isinstance(task, SummaryQueueElement):
            return await task.run(instance)
        if isinstance(task, BatchQueueElement):
            if notify:
                await instance.sent_batch_stream(task.images, task.config, task.batch_size, notify)
            else:
                return await instance.sent_batch(task.images, task.config, task.batch_size)
        else:
            if notify:
                await instance.sent_stream(task.image, task.config, notify)
            else:
                return await instance.sent(task.image, task.config)

    except Exception as e:
        if "Cannot connect to host" in str(e) or "Connection refused" in str(e):
            error_msg = "Translation service is starting up, please wait a moment and try again."
        else:
            error_msg = f"Translation failed: {str(e)}"

        if notify:
            notify(2, error_msg.encode('utf-8'))
            return
        else:
            raise HTTPException(500, detail=error_msg)
    finally:
        reclaim_memory = getattr(instance, "reclaim_memory", None)
        if reclaim_memory is not None:
            try:
                await reclaim_memory()
            except Exception:
                pass
        # Guarantee executor is returned to the pool under all circumstances
        await executor_instances.free_executor(instance)
