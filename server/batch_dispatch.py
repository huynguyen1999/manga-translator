"""Choose and start the next runnable batch unit."""

from __future__ import annotations

import asyncio


def log_token(value: str) -> str:
    return value[-8:]


async def launch_available(scheduler) -> bool:
    if scheduler.executors.free_executors() <= 0:
        return False
    fetch = getattr(scheduler.store, "list_runnable_batches", scheduler.store.list_batches)
    batches = await fetch()
    for batch in batches:
        batch_id = batch["id"]
        if batch_id in scheduler._stopping_batches:
            continue
        if batch.get("status") == "paused":
            continue
        if batch.get("status") not in {"waiting", "processing"}:
            continue
        items = batch.get("items", [])
        if not items:
            continue

        if not hasattr(scheduler.store, "mutate"):
            claimed_item = await scheduler._claim_item(batch_id)
            if not claimed_item:
                continue
            instance = await scheduler.executors.find_executor()
            task = asyncio.create_task(scheduler._process_item(batch_id, claimed_item["id"], instance))
            running_key = (batch_id, claimed_item["id"])
            scheduler._running[running_key] = task
            task.add_done_callback(lambda _, key=running_key: scheduler._running.pop(key, None))
            return True

        if batch.get("kind") in {"rerender", "pipeline-rerun"}:
            next_queued = scheduler._find_next_queued_item(batch_id, items)
            if next_queued:
                instance = await scheduler.executors.find_executor()
                claimed_item = await scheduler._claim_pipeline_rerun_item(batch_id, next_queued["id"])
                if not claimed_item:
                    await scheduler.executors.free_executor(instance)
                    continue
                item_id = claimed_item["id"]
                scheduler._running_items.add((batch_id, item_id))
                task = asyncio.create_task(
                    scheduler._process_pipeline_rerun_item(batch_id, item_id, instance),
                    name=f"batch-{log_token(batch_id)}-rerun-{log_token(item_id)}",
                )
                running_key = (batch_id, f"rerun:{item_id}")
                scheduler._running[running_key] = task
                def on_rerun_done(_, key=running_key, b_id=batch_id, i_id=item_id):
                    scheduler._running.pop(key, None)
                    scheduler._running_items.discard((b_id, i_id))
                task.add_done_callback(on_rerun_done)
                return True
            continue

        settings = batch.get("settings", {})
        current_stage = scheduler._current_batch_stage(items)
        if current_stage is None:
            continue
        if settings.get("translationQuality") == "professional":
            size = len([
                item for item in items
                if item.get("status") not in {"completed", "error"}
            ])
        else:
            size = max(1, min(100, int(settings.get("translationBatchSize", 20))))

        ready_group = (
            scheduler._find_ready_translation_group(batch_id, items, size, settings)
            if current_stage == "translation"
            else None
        )
        if ready_group:
            instance = await scheduler._find_stage_executor()
            if instance is None:
                continue
            claimed = await scheduler._claim_translation_group(batch_id, ready_group)
            if not claimed:
                await scheduler.executors.free_executor(instance)
                continue
            item_ids = [item["id"] for item in claimed]
            for item_id in item_ids:
                scheduler._running_items.add((batch_id, item_id))
            task = asyncio.create_task(
                scheduler._process_translation_group(batch_id, claimed, instance),
                name=f"batch-{log_token(batch_id)}-trans-{log_token(item_ids[0])}",
            )
            running_key = (batch_id, f"trans:{item_ids[0]}")
            scheduler._running[running_key] = task
            def on_trans_done(_, key=running_key, b_id=batch_id, ids=item_ids):
                scheduler._running.pop(key, None)
                for i_id in ids:
                    scheduler._running_items.discard((b_id, i_id))
            task.add_done_callback(on_trans_done)
            return True

        inference_group = scheduler._find_page_inference_group(batch, items)
        if inference_group and inference_group[0] == current_stage:
            stage_id, group = inference_group
            instance = await scheduler._find_stage_executor()
            if instance is not None:
                claimed = await scheduler._claim_prepare_items(batch_id, group, stage_id)
                if claimed:
                    item_ids = [item["id"] for item in claimed]
                    scheduler._running_items.update((batch_id, item_id) for item_id in item_ids)
                    task = asyncio.create_task(
                        scheduler._process_checkpointed_model_group(batch_id, claimed, instance, stage_id),
                        name=f"batch-{log_token(batch_id)}-{stage_id}-{log_token(item_ids[0])}",
                    )
                    running_key = (batch_id, f"{stage_id}:{item_ids[0]}")
                    scheduler._running[running_key] = task

                    def on_model_batch_done(_, key=running_key, b_id=batch_id, ids=item_ids):
                        scheduler._running.pop(key, None)
                        scheduler._running_items.difference_update((b_id, item_id) for item_id in ids)

                    task.add_done_callback(on_model_batch_done)
                    return True
                await scheduler.executors.free_executor(instance)
                continue

        ocr_group = scheduler._find_ocr_group(batch, items) if current_stage == "ocr" else []
        if ocr_group:
            instance = await scheduler._find_stage_executor()
            if instance is not None:
                claimed = await scheduler._claim_prepare_items(batch_id, ocr_group)
                if claimed:
                    item_ids = [item["id"] for item in claimed]
                    scheduler._running_items.update((batch_id, item_id) for item_id in item_ids)
                    task = asyncio.create_task(
                        scheduler._process_checkpointed_ocr_group(batch_id, claimed, instance),
                        name=f"batch-{log_token(batch_id)}-ocr-{log_token(item_ids[0])}",
                    )
                    running_key = (batch_id, f"ocr:{item_ids[0]}")
                    scheduler._running[running_key] = task

                    def on_ocr_done(_, key=running_key, b_id=batch_id, ids=item_ids):
                        scheduler._running.pop(key, None)
                        scheduler._running_items.difference_update((b_id, item_id) for item_id in ids)

                    task.add_done_callback(on_ocr_done)
                    return True
                await scheduler.executors.free_executor(instance)
                continue

        next_queued = scheduler._find_next_queued_item(batch_id, items, current_stage)
        if next_queued:
            instance = await scheduler._find_stage_executor()
            if instance is None:
                continue
            claimed_item = await scheduler._claim_prepare_item(batch_id, next_queued["id"])
            if not claimed_item:
                await scheduler.executors.free_executor(instance)
                continue
            item_id = claimed_item["id"]
            scheduler._running_items.add((batch_id, item_id))
            process = scheduler._process_prepare_item
            work = "stage"
            task = asyncio.create_task(
                process(batch_id, item_id, instance),
                name=f"batch-{log_token(batch_id)}-{work}-{log_token(item_id)}",
            )
            running_key = (batch_id, f"{work}:{item_id}")
            scheduler._running[running_key] = task
            def on_prep_done(_, key=running_key, b_id=batch_id, i_id=item_id):
                scheduler._running.pop(key, None)
                scheduler._running_items.discard((b_id, i_id))
            task.add_done_callback(on_prep_done)
            return True

    return False

