"""Optional per-engine resource callbacks used by persistent batch jobs."""

from contextlib import asynccontextmanager


@asynccontextmanager
async def translation_resource_lease(args, is_offline: bool, device: str):
    acquire = args.get("_batch_resource_acquire") if args is not None else None
    release = args.get("_batch_resource_release") if args is not None else None
    if acquire is None or release is None:
        yield
        return
    lease = await acquire(is_offline, device)
    try:
        yield
    finally:
        await release(*lease)
