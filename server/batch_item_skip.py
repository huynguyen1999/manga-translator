"""Cancel an isolated page runner when a user skips it mid-stage."""

_SKIPPABLE_ACTIVE_STAGES = frozenset({
    "initialize",
    "colorization",
    "textline_merge",
    "mask_generation",
    "layout",
    "rendering",
})


async def cancel_active_page_stage(scheduler, batch_id: str, item_id: str) -> bool:
    batch = await scheduler.store.get_batch(batch_id)
    item = next((item for item in batch.get("items", []) if item.get("id") == item_id), None)
    if not item or item.get("status") != "processing" or item.get("stage") not in _SKIPPABLE_ACTIVE_STAGES:
        return False
    task = scheduler._running.get((batch_id, f"stage:{item_id}")) or scheduler._running.get((batch_id, f"rerun:{item_id}"))
    if task is None:
        return False
    task.cancel()
    return True
