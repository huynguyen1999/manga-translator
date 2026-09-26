"""Shared resource limits and semaphore handling for batch work."""

import asyncio

from manga_translator.pipeline.stages import PipelineStage, ResourceClass, STAGE_RESOURCES
from manga_translator.utils.model_cache import MODEL_EXECUTOR_CONCURRENCY


_STAGE_RESOURCE_LIMITS = {
    ResourceClass.IO: 4,
    ResourceClass.GPU: 1,
    ResourceClass.CPU_HEAVY: 2,
    ResourceClass.CPU_LIGHT: 2,
    ResourceClass.NETWORK: 4,
}


def stage_resource_limits(
    pipeline_workers: int,
    cpu_heavy_workers: int,
    gpu_concurrency: int = 1,
) -> dict[ResourceClass, int]:
    workers = max(1, pipeline_workers)
    cpu_heavy = min(workers, max(1, cpu_heavy_workers))
    return {
        **_STAGE_RESOURCE_LIMITS,
        ResourceClass.IO: workers,
        ResourceClass.GPU: gpu_concurrency,
        ResourceClass.CPU_HEAVY: cpu_heavy,
        ResourceClass.CPU_LIGHT: cpu_heavy,
    }


def _stage_resource(stage_id: str) -> ResourceClass:
    stage_id = {"upscaling": "upscale", "textline_merge": "text_grouping"}.get(stage_id, stage_id)
    try:
        return STAGE_RESOURCES[PipelineStage(stage_id)]
    except ValueError:
        return ResourceClass.IO


class BatchResourceManager:
    def __init__(
        self,
        resource_limits: dict[ResourceClass, int] | None,
        *,
        logger,
        correlation_id,
    ):
        self.resource_limits = {**_STAGE_RESOURCE_LIMITS, **(resource_limits or {})}
        self.resource_limits[ResourceClass.GPU] = min(
            self.resource_limits[ResourceClass.GPU], MODEL_EXECUTOR_CONCURRENCY
        )
        self.slots = {
            resource: asyncio.Semaphore(limit)
            for resource, limit in self.resource_limits.items()
        }
        self._logger = logger
        self._correlation_id = correlation_id

    async def acquire(self, stage_id: str, resource: ResourceClass) -> asyncio.Semaphore:
        slot = self.slots[resource]
        if slot.locked():
            self._logger.debug(
                "stage_resource event=waiting stage=%s resource=%s capacity=%d request=%s",
                stage_id, resource.value, self.resource_limits[resource], self._correlation_id.get(),
            )
        await slot.acquire()
        self._logger.debug(
            "stage_resource event=acquired stage=%s resource=%s capacity=%d request=%s",
            stage_id, resource.value, self.resource_limits[resource], self._correlation_id.get(),
        )
        return slot

    async def acquire_stage(self, stage_id: str) -> asyncio.Semaphore:
        return await self.acquire(stage_id, _stage_resource(stage_id))

    def release_stage(
        self, stage_id: str, slot: asyncio.Semaphore, resource: ResourceClass | None = None
    ) -> None:
        slot.release()
        resource = resource or _stage_resource(stage_id)
        self._logger.debug(
            "stage_resource event=released stage=%s resource=%s capacity=%d request=%s",
            stage_id, resource.value, self.resource_limits[resource], self._correlation_id.get(),
        )
