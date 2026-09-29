"""Translation engine leases bridged to the batch scheduler loop."""

import asyncio

from manga_translator.pipeline.stages import ResourceClass


def resource_callbacks(scheduler, main_loop):
    async def release_on_scheduler(resource, slot):
        scheduler._release_stage_resource("translation", slot, resource)

    async def release(resource, slot):
        future = asyncio.run_coroutine_threadsafe(
            release_on_scheduler(resource, slot), main_loop
        )
        wrapped = asyncio.wrap_future(future)
        try:
            await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(wrapped)
            finally:
                raise

    async def acquire(is_offline: bool, device: str):
        if not is_offline:
            resource = ResourceClass.NETWORK
        elif str(device).lower().startswith("cuda"):
            resource = ResourceClass.GPU
        else:
            resource = ResourceClass.CPU_HEAVY
        future = asyncio.run_coroutine_threadsafe(
            scheduler._acquire_resource("translation", resource), main_loop
        )
        wrapped = asyncio.wrap_future(future)
        try:
            slot = await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            future.cancel()
            try:
                slot = await asyncio.shield(wrapped)
            except BaseException:
                pass
            else:
                await release(resource, slot)
            raise
        return resource, slot

    return acquire, release
