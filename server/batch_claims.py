"""Atomic batch-item claim and reservation operations."""

from __future__ import annotations

from typing import Any


async def claim_prepare_items(
    scheduler, batch_id: str, group: list[dict[str, Any]], stage_id: str = "ocr"
) -> list[dict[str, Any]]:
    item_ids = {item["id"] for item in group}
    claimed: list[dict[str, Any]] = []

    def mutate(manifest: dict[str, Any]):
        if manifest.get("status") == "paused" or batch_id in scheduler._stopping_batches:
            return False
        queued = [
            item for item in manifest.get("items", [])
            if item.get("id") in item_ids
            and item.get("status") == "queued"
            and item.get("pipelineStage") == stage_id
        ]
        if len(queued) != len(item_ids):
            return False
        for item in queued:
            item.update(status="processing", stage=stage_id, settings=dict(manifest.get("settings", {})))
            item.pop("stageStartedAt", None)
            claimed.append(dict(item))
        manifest["status"] = "processing"
        return True

    await scheduler.store.mutate(batch_id, mutate)
    return claimed


async def claim_prepare_item(scheduler, batch_id: str, item_id: str) -> dict[str, Any] | None:
    claimed: dict[str, Any] | None = None

    def mutate(manifest: dict[str, Any]):
        nonlocal claimed
        if manifest.get("status") == "paused" or batch_id in scheduler._stopping_batches:
            return False
        for item in manifest.get("items", []):
            if item.get("id") == item_id and item.get("status") == "queued":
                pipeline_stage = item.get("retryFromStage") or item.get("pipelineStage") or "initialize"
                item["pipelineStage"] = pipeline_stage
                item.update(status="processing", stage=pipeline_stage)
                item.pop("stageStartedAt", None)
                item["settings"] = dict(manifest.get("settings", {}))
                claimed = dict(item)
                manifest["status"] = "processing"
                return True
        return False

    await scheduler.store.mutate(batch_id, mutate)
    return claimed


async def claim_pipeline_rerun_item(scheduler, batch_id: str, item_id: str) -> dict[str, Any] | None:
    claimed: dict[str, Any] | None = None

    def mutate(manifest: dict[str, Any]):
        nonlocal claimed
        if manifest.get("status") == "paused" or batch_id in scheduler._stopping_batches:
            return False
        for item in manifest.get("items", []):
            if item.get("id") == item_id and item.get("status") == "queued":
                mode = item.get("rerunMode") or manifest.get("rerunMode") or "typesetting"
                initial_stage = "detection" if mode in {"full", "reprocess_text"} else "translating" if mode == "translation_typesetting" else "rendering"
                item.update(status="processing", stage=initial_stage)
                item.pop("stageStartedAt", None)
                claimed = dict(item)
                manifest["status"] = "processing"
                return True
        return False

    await scheduler.store.mutate(batch_id, mutate)
    return claimed


async def claim_translation_group(
    scheduler, batch_id: str, group_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    claimed: list[dict[str, Any]] = []
    group_ids = {item["id"] for item in group_items}

    def mutate(manifest: dict[str, Any]):
        if manifest.get("status") == "paused" or batch_id in scheduler._stopping_batches:
            return False
        first = True
        for item in manifest.get("items", []):
            if item.get("id") in group_ids and item.get("stage") == "awaiting_translation":
                item["settings"] = dict(manifest.get("settings", {}))
                if first:
                    item.update(status="processing", stage="translating")
                    first = False
                else:
                    item.update(status="processing", stage="reserved")
                item.pop("stageStartedAt", None)
                claimed.append(dict(item))
        if claimed:
            manifest["status"] = "processing"
        return bool(claimed)

    await scheduler.store.mutate(batch_id, mutate)
    return claimed


async def claim_items(
    scheduler, batch_id: str, limit: int, *, set_item_stage
) -> list[dict[str, Any]]:
    claimed: list[dict[str, Any]] = []

    def mutate(manifest: dict[str, Any]):
        if manifest.get("status") == "paused" or batch_id in scheduler._stopping_batches:
            return False
        for item in manifest.get("items", []):
            if item.get("status") != "queued":
                continue
            reservation = (batch_id, item["id"])
            if reservation in scheduler._reserved_items:
                continue
            scheduler._reserved_items.add(reservation)
            set_item_stage(item, "reserved")
            item["settings"] = dict(manifest.get("settings", {}))
            claimed.append(dict(item))
            if len(claimed) == limit:
                break
        if claimed:
            claimed_id = claimed[0]["id"]
            for item in manifest.get("items", []):
                if item.get("id") == claimed_id:
                    item.update(status="processing")
                    set_item_stage(item, "starting")
            manifest["status"] = "processing"
        return bool(claimed)

    await scheduler.store.mutate(batch_id, mutate)
    return claimed


async def claim_item(scheduler, batch_id: str, *, set_item_stage) -> dict[str, Any] | None:
    claimed: dict[str, Any] | None = None

    def mutate(manifest: dict[str, Any]):
        nonlocal claimed
        if manifest.get("status") == "paused" or batch_id in scheduler._stopping_batches:
            return False
        for item in manifest.get("items", []):
            if item.get("status") == "queued":
                item["status"] = "processing"
                set_item_stage(item, "starting")
                item["settings"] = dict(manifest.get("settings", {}))
                claimed = dict(item)
                manifest["status"] = "processing"
                return True
        return False

    await scheduler.store.mutate(batch_id, mutate)
    return claimed
