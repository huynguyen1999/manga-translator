import asyncio
from typing import List, Union, Any

from PIL import Image
from pydantic import BaseModel

from manga_translator import Config
from server.sent_data_internal import fetch_data_stream, NotifyType, fetch_data
from server.in_process_executor import InProcessExecutorInstance

AnyExecutor = Union['ExecutorInstance', InProcessExecutorInstance]

class ExecutorInstance(BaseModel):
    ip: str
    port: int
    worker_id: int = 0
    busy: bool = False

    model_config = {"arbitrary_types_allowed": True}

    def free_executor(self):
        self.busy = False

    async def sent(self, image: Image.Image, config: Config):
        return await fetch_data("http://"+self.ip+":"+str(self.port)+"/simple_execute/translate", image, config)

    async def extract_text(self, image: Image.Image, config: Config):
        return await fetch_data(
            "http://" + self.ip + ":" + str(self.port) + "/simple_execute/extract_text",
            image,
            config,
        )

    async def sent_stream(self, image: Image.Image, config: Config, sender: NotifyType):
        await fetch_data_stream("http://"+self.ip+":"+str(self.port)+"/execute/translate", image, config, sender)

    async def sent_batch(self, images: List[Image.Image], config: Config | List[Config], batch_size: int):
        """发送批量翻译请求"""
        configs = config if isinstance(config, list) else [config] * len(images)
        images_with_configs = list(zip(images, configs))
        payload = {"images_with_configs": images_with_configs, "batch_size": batch_size}
        return await fetch_data("http://"+self.ip+":"+str(self.port)+"/simple_execute/translate_batch", payload)

    async def sent_batch_stream(self, images: List[Image.Image], config: Config, batch_size: int, sender: NotifyType):
        """发送批量翻译流式请求"""
        images_with_configs = [(img, config) for img in images]
        payload = {"images_with_configs": images_with_configs, "batch_size": batch_size}
        await fetch_data_stream("http://"+self.ip+":"+str(self.port)+"/execute/translate_batch",
                               payload, sender=sender)

class Executors:
    def __init__(self):
        self.list: List[AnyExecutor] = []
        self.queue: asyncio.Queue = asyncio.Queue()

    def register(self, instance: AnyExecutor):
        if hasattr(instance, 'ip') and hasattr(instance, 'port'):
            # Avoid duplicate registration for same ip:port
            existing = next((x for x in self.list if getattr(x, 'ip', None) == instance.ip and getattr(x, 'port', None) == instance.port), None)
            if existing:
                existing.worker_id = instance.worker_id
                if existing.busy:
                    existing.busy = False
                    self.queue.put_nowait(existing)
                return
        self.list.append(instance)
        self.queue.put_nowait(instance)

    def unregister(self, instance: AnyExecutor):
        if instance in self.list:
            self.list.remove(instance)
            temp = []
            while not self.queue.empty():
                try:
                    item = self.queue.get_nowait()
                    if item != instance:
                        temp.append(item)
                except asyncio.QueueEmpty:
                    break
            for item in temp:
                self.queue.put_nowait(item)

    def free_executors(self) -> int:
        return self.queue.qsize()

    def get_status(self) -> dict:
        total = len(self.list)
        free = self.queue.qsize()
        return {
            "total_workers": total,
            "free_workers": free,
            "busy_workers": total - free
        }

    async def find_executor(self) -> AnyExecutor:
        instance = await self.queue.get()
        instance.busy = True
        return instance

    async def free_executor(self, instance: AnyExecutor):
        instance.free_executor()
        self.queue.put_nowait(instance)

executor_instances: Executors = Executors()
