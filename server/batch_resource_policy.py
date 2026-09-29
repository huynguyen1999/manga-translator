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


def _resource_family(resource: ResourceClass) -> ResourceClass:
    return ResourceClass.CPU_HEAVY if resource in {
        ResourceClass.CPU_HEAVY, ResourceClass.CPU_LIGHT
    } else resource


def _device_resource(device: str | None) -> ResourceClass:
    value = str(device or "cpu").lower()
    return (
        ResourceClass.GPU
        if value.startswith(("cuda", "mps", "xpu", "coreml", "vulkan"))
        else ResourceClass.CPU_HEAVY
    )


def _value(value) -> str:
    return str(getattr(value, "value", value) or "").lower()


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
        ResourceClass.GPU: min(workers, max(1, gpu_concurrency)),
        ResourceClass.CPU_HEAVY: cpu_heavy,
        ResourceClass.CPU_LIGHT: cpu_heavy,
        ResourceClass.NETWORK: workers,
    }


def batch_stage_executor_count(resource_limits: dict[ResourceClass, int]) -> int:
    return sum(
        resource_limits[resource]
        for resource in (
            ResourceClass.IO,
            ResourceClass.GPU,
            ResourceClass.CPU_HEAVY,
            ResourceClass.NETWORK,
        )
    )


def _stage_resource(
    stage_id: str,
    config=None,
    device: str | None = None,
) -> ResourceClass:
    stage_id = {"upscaling": "upscale", "textline_merge": "text_grouping"}.get(stage_id, stage_id)
    try:
        stage = PipelineStage(stage_id)
    except ValueError:
        return ResourceClass.IO

    if config is None:
        return STAGE_RESOURCES[stage]

    if stage is PipelineStage.COLORIZATION:
        return (
            ResourceClass.CPU_LIGHT
            if _value(config.colorizer.colorizer) == "none"
            else _device_resource(device)
        )
    if stage is PipelineStage.UPSCALE:
        if not config.upscale.upscale_ratio or config.upscale.upscale_ratio == 1:
            return ResourceClass.CPU_LIGHT
        if _value(config.upscale.upscaler) in {"esrgan", "waifu2x"}:
            return ResourceClass.GPU  # NCNN-Vulkan
        return _device_resource(device)
    if stage is PipelineStage.DETECTION:
        detector = _value(config.detector.detector)
        if detector == "paddle":  # Rust/ONNX does not expose its provider.
            return ResourceClass.CPU_HEAVY
        if detector == "none":
            return ResourceClass.CPU_LIGHT
        return _device_resource(device)  # CTD selects Torch or OpenCV ONNX by device.
    if stage in {PipelineStage.OCR, PipelineStage.BUBBLE_DETECTION}:
        if stage is PipelineStage.BUBBLE_DETECTION and not config.bubble_detection.enabled:
            return ResourceClass.CPU_LIGHT
        return _device_resource(device)
    if stage is PipelineStage.INPAINTING:
        if _value(config.inpainter.inpainter) in {"original", "none"}:
            return ResourceClass.CPU_LIGHT
        return _device_resource(device)
    if stage is PipelineStage.TRANSLATION:
        chain = config.translator.translator_gen.chain
        first_engine = _value(chain[0][0]) if chain else ""
        if first_engine == "sugoi":
            # CTranslate2's local model uses CUDA or CPU; MPS/XPU requests fall back to CPU.
            return (
                ResourceClass.GPU
                if str(device or "cpu").lower().startswith("cuda")
                else ResourceClass.CPU_HEAVY
            )
        if first_engine in {"none", "original"}:
            return ResourceClass.CPU_LIGHT
        return ResourceClass.NETWORK
    return STAGE_RESOURCES[stage]


class BatchResourceManager:
    def __init__(
        self,
        resource_limits: dict[ResourceClass, int] | None,
        *,
        logger,
        correlation_id,
    ):
        self.resource_limits = {**_STAGE_RESOURCE_LIMITS, **(resource_limits or {})}
        cpu_limit = min(
            self.resource_limits[ResourceClass.CPU_HEAVY],
            self.resource_limits[ResourceClass.CPU_LIGHT],
        )
        self.resource_limits[ResourceClass.CPU_HEAVY] = cpu_limit
        self.resource_limits[ResourceClass.CPU_LIGHT] = cpu_limit
        self.resource_limits[ResourceClass.GPU] = min(
            self.resource_limits[ResourceClass.GPU], MODEL_EXECUTOR_CONCURRENCY
        )
        self.slots = {
            resource: asyncio.Semaphore(limit)
            for resource, limit in self.resource_limits.items()
            if _resource_family(resource) == resource
        }
        self.slots[ResourceClass.CPU_LIGHT] = self.slots[ResourceClass.CPU_HEAVY]
        self._slot_resources = {id(slot): resource for resource, slot in self.slots.items()}
        self.active: dict[ResourceClass, dict[str, int]] = {
            resource: {} for resource in self.resource_limits
        }
        self._logger = logger
        self._correlation_id = correlation_id

    async def acquire(self, stage_id: str, resource: ResourceClass) -> asyncio.Semaphore:
        resource = _resource_family(resource)
        slot = self.slots[resource]
        if slot.locked():
            self._logger.debug(
                "stage_resource event=waiting stage=%s resource=%s capacity=%d request=%s",
                stage_id, resource.value, self.resource_limits[resource], self._correlation_id.get(),
            )
        await slot.acquire()
        self.active[resource][stage_id] = self.active[resource].get(stage_id, 0) + 1
        self._logger.debug(
            "stage_resource event=acquired stage=%s resource=%s capacity=%d request=%s",
            stage_id, resource.value, self.resource_limits[resource], self._correlation_id.get(),
        )
        return slot

    async def acquire_stage(
        self, stage_id: str, resource: ResourceClass | None = None
    ) -> asyncio.Semaphore:
        return await self.acquire(stage_id, resource or _stage_resource(stage_id))

    def release_stage(
        self, stage_id: str, slot: asyncio.Semaphore, resource: ResourceClass | None = None
    ) -> None:
        resource = _resource_family(resource or self._slot_resources[id(slot)])
        stages = self.active[resource]
        if stage_id in stages:
            stages[stage_id] -= 1
            if not stages[stage_id]:
                del stages[stage_id]
        slot.release()
        self._logger.debug(
            "stage_resource event=released stage=%s resource=%s capacity=%d request=%s",
            stage_id, resource.value, self.resource_limits[resource], self._correlation_id.get(),
        )

    def active_resources(self, stage_id: str) -> set[ResourceClass]:
        return {
            resource
            for resource, stages in self.active.items()
            if stages.get(stage_id, 0)
        }

    def has_capacity(self, resource: ResourceClass) -> bool:
        resource = _resource_family(resource)
        return sum(self.active[resource].values()) < self.resource_limits[resource]
