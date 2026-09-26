"""Batch settings and lifecycle mutations used by the scheduler facade."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Callable

from manga_translator.pipeline.run import CHECKPOINT_STAGE_IDS, PipelineRun
from manga_translator.utils.image_storage import find_asset
from server.batch_item_skip import cancel_active_page_stage
from server.batch_store import BatchNotFound, InvalidBatch


def set_batch_status(manifest: dict[str, Any], status: str) -> bool:
    current = manifest.get("status")
    if status == "paused" and current != "processing":
        return False
    if status == "stopping" and current not in {"waiting", "processing", "paused"}:
        return False
    if status == "waiting" and current not in {"waiting", "processing", "paused"}:
        return False
    manifest["status"] = status
    return True


async def pause_batch(store, wake: Callable[[], None], batch_id: str, set_status: Callable) -> dict[str, Any]:
    result = await store.mutate(
        batch_id,
        lambda manifest: set_status(manifest, "paused"),
    )
    wake()
    return result


async def resume_batch(store, wake: Callable[[], None], batch_id: str, set_status: Callable) -> dict[str, Any]:
    result = await store.mutate(
        batch_id,
        lambda manifest: set_status(manifest, "waiting"),
    )
    wake()
    return result


async def dismiss_batch(store, batch_id: str, compact_groups: Callable) -> dict[str, Any]:
    result = await store.mutate(
        batch_id, lambda manifest: manifest.update(dismissed=True) is None
    )
    if not any(item.get("status") in {"queued", "processing"} for item in result.get("items", [])):
        await compact_groups(result)
    return result


async def update_translator(
    store, wake: Callable[[], None], batch_id: str, translator: str
) -> dict[str, Any]:
    def mutate(manifest: dict[str, Any]):
        manifest.setdefault("settings", {})["translator"] = translator
        for item in manifest.get("items", []):
            if item.get("status") == "queued":
                item.pop("config", None)
        return True

    result = await store.mutate(batch_id, mutate)
    wake()
    return result


async def update_title(
    store, wake: Callable[[], None], batch_id: str, title: str
) -> dict[str, Any]:
    clean_title = title.strip() or "Ungrouped"

    def mutate(manifest: dict[str, Any]):
        manifest["title"] = clean_title
        manifest["mangaTitle"] = clean_title
        for item in manifest.get("items", []):
            if item.get("status") == "queued":
                item["mangaTitle"] = clean_title
        return True

    result = await store.mutate(batch_id, mutate)
    wake()
    return result


async def update_priority(
    store, wake: Callable[[], None], batch_id: str, priority: bool
) -> dict[str, Any]:
    result = await store.mutate(
        batch_id,
        lambda manifest: manifest.update(priority=priority) is None,
    )
    wake()
    return result


async def update_manual_review(
    store, wake: Callable[[], None], batch_id: str, enabled: bool
) -> dict[str, Any]:
    def mutate(manifest: dict[str, Any]):
        manifest.setdefault("settings", {})["keepFailedPagesForEditing"] = enabled
        for item in manifest.get("items", []):
            if item.get("status") in {"queued", "error"}:
                item.setdefault("settings", {})["keepFailedPagesForEditing"] = enabled
                item.pop("config", None)
        return True

    result = await store.mutate(batch_id, mutate)
    wake()
    return result


async def retry_item(
    scheduler,
    batch_id: str,
    item_id: str,
    keep_failed_pages_for_editing: bool | None = None,
    from_stage: str | None = None,
) -> dict[str, Any]:
    batch = await scheduler.store.get_batch(batch_id)
    target = next((item for item in batch["items"] if item["id"] == item_id), None)
    is_completed_retry = bool(target and target["status"] == "completed")
    if from_stage is not None:
        from_stage = CHECKPOINT_STAGE_IDS.get(from_stage, from_stage)
        folder = target.get("resultFolder") if target else None
        if not isinstance(folder, str) or Path(folder).name != folder:
            raise InvalidBatch("Cannot retry from a stage: saved result is unavailable")
        run = PipelineRun.get_or_load(scheduler.result_root, folder)
        if run is None:
            database = getattr(scheduler.store, "database", None)
            documents = await database.get_documents(folder) if database is not None else {}
            manifest_path = scheduler.result_root / folder / "pipeline_manifest.json"
            if "pipeline_manifest.json" not in documents and manifest_path.is_file():
                try:
                    documents["pipeline_manifest.json"] = await asyncio.to_thread(
                        lambda: json.loads(manifest_path.read_text(encoding="utf-8"))
                    )
                except (OSError, json.JSONDecodeError):
                    pass
            run = PipelineRun.from_documents(scheduler.result_root, folder, documents)
        valid_stages = {
            stage.get("id")
            for stage in (getattr(run, "manifest", {}) or {}).get("stages", [])
            if stage.get("id") != "input"
        }
        if from_stage not in valid_stages:
            raise InvalidBatch("Cannot retry from the requested stage: checkpoint unavailable")
    if is_completed_retry and from_stage is None:
        folder = target.get("resultFolder")
        source = find_asset(scheduler.result_root / folder, "input") if isinstance(folder, str) else None
        if source is None or not source.is_file():
            raise InvalidBatch("Cannot retry page: saved input is unavailable")
        destination = scheduler.store._input_path(batch_id, target)
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, source, destination)

    def mutate(manifest: dict[str, Any]):
        was_dismissed = bool(manifest.get("dismissed"))
        for item in manifest.get("items", []):
            retryable = item.get("status") == "error" or (
                item.get("status") == "completed"
            )
            if item.get("id") == item_id and retryable:
                if keep_failed_pages_for_editing is not None:
                    manifest.setdefault("settings", {})[
                        "keepFailedPagesForEditing"
                    ] = keep_failed_pages_for_editing
                    item.setdefault("settings", {})[
                        "keepFailedPagesForEditing"
                    ] = keep_failed_pages_for_editing
                    item.pop("config", None)
                item.update(status="queued", stage=None, error=None, needsReview=False)
                if from_stage is None:
                    item.pop("retryFromStage", None)
                else:
                    item["retryFromStage"] = from_stage
                if was_dismissed:
                    # Dismissal compacts the group; retrying is a new append.
                    item["pageOrder"] = None
                    manifest["dismissed"] = False
                if item.get("resultFolder") and is_completed_retry and from_stage is None:
                    item["resultFolder"] = None
                if is_completed_retry:
                    manifest["completedCount"] = sum(
                        entry.get("status") == "completed"
                        for entry in manifest.get("items", [])
                    )
                if manifest.get("status") in {"error", "completed"}:
                    manifest["status"] = "waiting"
                return True
        return False

    result = await scheduler.store.mutate(batch_id, mutate)
    scheduler._wake.set()
    return result


async def update_item(scheduler, batch_id: str, item_id: str, exclude_color: bool) -> dict[str, Any]:
    def mutate(manifest: dict[str, Any]):
        for item in manifest.get("items", []):
            if item.get("id") == item_id and item.get("status") == "queued":
                item["excludeColor"] = exclude_color
                return True
        return False

    return await scheduler.store.mutate(batch_id, mutate)


async def remove_item(scheduler, batch_id: str, item_id: str) -> dict[str, Any]:
    cancel_active_stage = await cancel_active_page_stage(scheduler, batch_id, item_id)
    input_path = None
    removed = False
    removed_group_id = None
    try:
        input_path = await scheduler.store.input_path(batch_id, item_id)
    except BatchNotFound:
        pass

    def mutate(manifest: dict[str, Any]):
        nonlocal removed, removed_group_id
        items = manifest.get("items", [])
        target = next((item for item in items if item.get("id") == item_id), None)
        if not target:
            return False
        can_skip = target.get("status") == "queued" or (target.get("status") == "processing" and (target.get("stage") == "awaiting_translation" or cancel_active_stage))
        if target.get("status") != "error" and not can_skip:
            raise InvalidBatch("Only waiting, independent processing, or failed pages can be skipped or removed")
        removed_group_id = target.get("mangaGroupId")
        items.remove(target)
        manifest["totalItems"] = max(
            int(manifest.get("completedCount", 0)), int(manifest.get("totalItems", len(items))) - 1
        )
        statuses = {item.get("status") for item in items}
        if manifest.get("status") == "paused" and statuses & {"queued", "processing"}:
            pass
        elif "processing" in statuses:
            manifest["status"] = "processing"
        elif "queued" in statuses:
            manifest["status"] = "waiting"
        elif "error" in statuses:
            manifest["status"] = "error"
        else:
            manifest["status"] = "completed"
        removed = True
        return True

    result = await scheduler.store.mutate(batch_id, mutate)
    if removed and input_path:
        input_path.unlink(missing_ok=True)
    if removed and removed_group_id and getattr(scheduler.store, "database", None) is not None:
        await scheduler.store.database.compact_page_order(removed_group_id)
    scheduler._wake.set()
    return result


async def remove_batch(scheduler, batch_id: str) -> None:
    existing = scheduler._remove_tasks.get(batch_id)
    if existing:
        await existing
        return

    async def drain_and_delete():
        scheduler._stopping_batches.add(batch_id)
        try:
            try:
                await scheduler.store.mutate(batch_id, lambda manifest: scheduler._set_batch_status(manifest, "stopping"))
            except BatchNotFound:
                return
            tasks = [task for (running_batch_id, _), task in scheduler._running.items() if running_batch_id == batch_id]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await scheduler.store.delete_batch(batch_id)
        finally:
            scheduler._stopping_batches.discard(batch_id)
            scheduler._wake.set()

    task = asyncio.create_task(drain_and_delete(), name=f"remove-batch-{batch_id}")
    scheduler._remove_tasks[batch_id] = task
    try:
        await task
    finally:
        scheduler._remove_tasks.pop(batch_id, None)
