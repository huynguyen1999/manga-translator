"""Choose and start the next runnable batch unit."""

from __future__ import annotations

import asyncio
from server.batch_stage_eligibility import (
    eligible_queued_items,
    stage_resource_for,
    translation_group_is_schedulable,
)


def log_token(value: str) -> str:
    return value[-8:]


async def _find_executor_for(scheduler, select):
    for _ in range(scheduler.stage_executors.free_executors()):
        instance = await scheduler._find_stage_executor()
        if instance is None:
            return None, None
        selection = select(instance)
        if selection:
            return instance, selection
        await scheduler.stage_executors.free_executor(instance)
    return None, None


def _track_stage_resources(scheduler, batch_id, batch, items, stage_id, instance):
    for item in items:
        scheduler._running_stage_resources[(batch_id, item["id"])] = (
            stage_id,
            stage_resource_for(scheduler, batch, item, stage_id, instance),
        )


def _clear_stage_resources(scheduler, batch_id, item_ids):
    for item_id in item_ids:
        scheduler._running_stage_resources.pop((batch_id, item_id), None)


async def launch_available(scheduler) -> bool:
    if scheduler.executors.free_executors() <= 0 and scheduler.stage_executors.free_executors() <= 0:
        return False
    fetch = getattr(scheduler.store, "list_runnable_batches", scheduler.store.list_batches)
    batches = await fetch()
    for batch in batches:
        batch_id = batch["id"]
        if any(active_id != batch_id for active_id, _ in scheduler._running) or batch_id in scheduler._stopping_batches:
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
            size = len([item for item in items if item.get("status") != "completed"])
        else:
            size = max(1, min(100, int(settings.get("translationBatchSize", 20))))

        ready_group = scheduler._find_ready_translation_group(batch_id, items, size, settings)
        if ready_group:
            instance, selected_group = await _find_executor_for(
                scheduler,
                lambda candidate: ready_group
                if translation_group_is_schedulable(
                    scheduler, batch_id, batch, items, ready_group, candidate
                )
                else None,
            )
            if instance is None:
                selected_group = None
            claimed = (
                await scheduler._claim_translation_group(batch_id, selected_group)
                if selected_group
                else []
            )
            if not claimed:
                if instance is not None:
                    await scheduler.stage_executors.free_executor(instance)
                    continue
            else:
                item_ids = [item["id"] for item in claimed]
                _track_stage_resources(scheduler, batch_id, batch, claimed, "translation", instance)
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
                    _clear_stage_resources(scheduler, b_id, ids)
                task.add_done_callback(on_trans_done)
                return True

        instance, inference_group = await _find_executor_for(
            scheduler,
            lambda candidate: scheduler._find_page_inference_group(
                batch,
                eligible_queued_items(scheduler, batch_id, batch, items, candidate),
            ),
        )
        if inference_group:
            stage_id, group = inference_group
            claimed = await scheduler._claim_prepare_items(batch_id, group, stage_id)
            if claimed:
                item_ids = [item["id"] for item in claimed]
                _track_stage_resources(scheduler, batch_id, batch, claimed, stage_id, instance)
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
                    _clear_stage_resources(scheduler, b_id, ids)

                task.add_done_callback(on_model_batch_done)
                return True
            await scheduler.stage_executors.free_executor(instance)
            continue

        instance, ocr_group = await _find_executor_for(
            scheduler,
            lambda candidate: scheduler._find_ocr_group(
                batch,
                eligible_queued_items(scheduler, batch_id, batch, items, candidate),
            ),
        )
        if ocr_group:
            claimed = await scheduler._claim_prepare_items(batch_id, ocr_group)
            if claimed:
                item_ids = [item["id"] for item in claimed]
                _track_stage_resources(scheduler, batch_id, batch, claimed, "ocr", instance)
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
                    _clear_stage_resources(scheduler, b_id, ids)

                task.add_done_callback(on_ocr_done)
                return True
            await scheduler.stage_executors.free_executor(instance)
            continue

        instance, next_queued = await _find_executor_for(
            scheduler,
            lambda candidate: scheduler._find_next_queued_item(
                batch_id,
                eligible_queued_items(scheduler, batch_id, batch, items, candidate),
            ),
        )
        if next_queued:
            claimed_item = await scheduler._claim_prepare_item(batch_id, next_queued["id"])
            if not claimed_item:
                await scheduler.stage_executors.free_executor(instance)
                continue
            item_id = claimed_item["id"]
            stage_id = scheduler._item_batch_stage(claimed_item)
            _track_stage_resources(scheduler, batch_id, batch, [claimed_item], stage_id, instance)
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
                _clear_stage_resources(scheduler, b_id, [i_id])
            task.add_done_callback(on_prep_done)
            return True

    return False
