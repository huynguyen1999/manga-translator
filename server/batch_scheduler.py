"""Persistent batch scheduler using the server's existing executor pool."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from manga_translator import Config, Context
from manga_translator.config import Detector, Ocr
from manga_translator.pipeline.run import (
    CHECKPOINT_STAGE_IDS,
    PipelineRun,
    deserialize_textblocks,
    deserialize_textlines,
    serialize_regions,
)
from manga_translator.pipeline.stages import (
    PipelineStage,
    ResourceClass,
    STAGE_RESOURCES,
    fingerprint,
    settings_for_stage,
    stage_from_progress,
)
from manga_translator.rendering import resolve_font_name_or_path
from manga_translator.utils.model_cache import MODEL_EXECUTOR_CONCURRENCY
from server.batch_store import BatchNotFound, BatchStore, InvalidBatch
from server.logger import correlation_id_ctx, get_logger
from server.image_variants import final_file
from manga_translator.utils.image_storage import find_asset, save_jpeg

logger = get_logger("batch_scheduler")

_PRE_TRANSLATION_STAGES = (
    "colorization",
    "upscaling",
    "detection",
    "ocr",
    "bubble_detection",
    "textline_merge",
)
_BATCH_STAGE_ORDER = (
    "initialize",
    *_PRE_TRANSLATION_STAGES,
    "translation",
    "mask_generation",
    "layout",
    "inpainting",
    "rendering",
)
_BATCH_STAGE_ALIASES = {
    "starting": "initialize",
    "queued": "initialize",
    "reserved": "translation",
    "awaiting_translation": "translation",
    "translating": "translation",
    "translation_remap": "translation",
    "analyzing-story": "translation",
    "after-translating": "translation",
    "colorizing": "colorization",
    "bubble-detection": "bubble_detection",
    "mask-generation": "mask_generation",
    "saving": "rendering",
    "downscaling": "rendering",
    "finished": "rendering",
}

_STAGE_RESOURCE_LIMITS = {
    ResourceClass.IO: 4,
    ResourceClass.GPU: MODEL_EXECUTOR_CONCURRENCY,
    ResourceClass.CPU_HEAVY: 2,
    ResourceClass.CPU_LIGHT: 2,
    ResourceClass.NETWORK: 4,
}
_INFERENCE_PAGE_BATCH_SIZE = 2


def stage_resource_limits(
    pipeline_workers: int,
    cpu_heavy_workers: int,
    gpu_concurrency: int = MODEL_EXECUTOR_CONCURRENCY,
) -> dict[ResourceClass, int]:
    workers = max(1, pipeline_workers)
    cpu_heavy = min(workers, max(1, cpu_heavy_workers))
    return {
        **_STAGE_RESOURCE_LIMITS,
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


def _set_item_stage(item: dict[str, Any], stage: str) -> bool:
    if item.get("stage") == stage and item.get("stageStartedAt") is not None:
        return False
    item["stage"] = stage
    item["stageStartedAt"] = int(time.time() * 1000)
    return True


def _log_token(value: str) -> str:
    return value[-8:]


class BatchScheduler:
    def __init__(
        self,
        store: BatchStore,
        executors: Any,
        result_root: str | Path,
        resource_limits: dict[ResourceClass, int] | None = None,
        mps_memory_mode: bool = False,
    ):
        self.store = store
        self.executors = executors
        self.result_root = Path(result_root).resolve()
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._running: dict[tuple[str, str], asyncio.Task] = {}
        self._running_items: set[tuple[str, str]] = set()
        self._remove_tasks: dict[str, asyncio.Task] = {}
        self._stopping_batches: set[str] = set()
        self._translation_locks: dict[str, asyncio.Lock] = {}
        self._reserved_items: set[tuple[str, str]] = set()
        self.mps_memory_mode = mps_memory_mode
        self.resource_limits = {**_STAGE_RESOURCE_LIMITS, **(resource_limits or {})}
        self.resource_limits[ResourceClass.GPU] = min(
            self.resource_limits[ResourceClass.GPU], MODEL_EXECUTOR_CONCURRENCY
        )
        self._resource_slots = {
            resource: asyncio.Semaphore(limit)
            for resource, limit in self.resource_limits.items()
        }
        self._closed = False

    def wake(self) -> None:
        self._wake.set()

    async def _acquire_stage_resource(self, stage_id: str) -> asyncio.Semaphore:
        resource = _stage_resource(stage_id)
        slot = self._resource_slots[resource]
        if slot.locked():
            logger.debug(
                "stage_resource event=waiting stage=%s resource=%s capacity=%d request=%s",
                stage_id, resource.value, self.resource_limits[resource], correlation_id_ctx.get(),
            )
        await slot.acquire()
        logger.debug(
            "stage_resource event=acquired stage=%s resource=%s capacity=%d request=%s",
            stage_id, resource.value, self.resource_limits[resource], correlation_id_ctx.get(),
        )
        return slot

    def _release_stage_resource(self, stage_id: str, slot: asyncio.Semaphore) -> None:
        slot.release()
        resource = _stage_resource(stage_id)
        logger.debug(
            "stage_resource event=released stage=%s resource=%s capacity=%d request=%s",
            stage_id, resource.value, self.resource_limits[resource], correlation_id_ctx.get(),
        )

    async def _find_stage_executor(self) -> Any | None:
        for _ in range(self.executors.free_executors()):
            instance = await self.executors.find_executor()
            if callable(getattr(instance, "_run_translation", None)) and getattr(instance, "translator", None) is not None:
                return instance
            await self.executors.free_executor(instance)
        return None

    async def start(self) -> None:
        database = getattr(self.store, "database", None)
        recover_stages = getattr(database, "interrupt_running_pipeline_stages", None)
        if recover_stages is not None:
            await recover_stages()
        await self.store.reconcile(self.result_root)
        self._closed = False
        self._loop_task = asyncio.create_task(self._run(), name="batch-scheduler")

    async def stop(self) -> None:
        self._closed = True
        self._wake.set()
        if self._loop_task:
            await self._loop_task
            self._loop_task = None
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self) -> None:
        while not self._closed:
            launched = await self._launch_available()
            if launched:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    def _find_ready_translation_group(
        self,
        batch_id: str,
        items: list[dict[str, Any]],
        size: int,
        settings: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        if self._current_batch_stage(items) != "translation":
            return None
        if (settings or {}).get("translationQuality") == "professional":
            story_plan = (settings or {}).get("storyPlan")
            segments = story_plan.get("segments") if isinstance(story_plan, dict) else None
            if not segments or not story_plan.get("enabled", True) or story_plan.get("mergeAllPages"):
                segments = [{"startPage": 1, "endPage": len(items)}]
            for segment in segments:
                start, end = segment.get("startPage"), segment.get("endPage")
                if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                    continue
                story_items = items[start - 1:end]
                if len(story_items) != end - start + 1:
                    continue
                if any(item.get("status") in {"completed", "error"} for item in story_items):
                    continue
                if all(
                    item.get("stage") == "awaiting_translation"
                    and (batch_id, item.get("id")) not in self._running_items
                    for item in story_items
                ):
                    return story_items
            return None

        uncompleted = [
            item for item in items
            if item.get("status") not in {"completed", "error"} and item.get("id")
        ]
        if not uncompleted:
            return None

        ready_group = [
            item for item in uncompleted
            if item.get("stage") == "awaiting_translation"
            and (batch_id, item["id"]) not in self._running_items
        ]

        if len(ready_group) >= size:
            return ready_group[:size]

        if len(ready_group) == len(uncompleted):
            return ready_group

        return None

    @staticmethod
    def _story_plan_for_group(
        story_plan: Any,
        items: list[dict[str, Any]],
        item_ids: list[str],
    ) -> Any:
        if not isinstance(story_plan, dict) or not story_plan.get("enabled", True) or story_plan.get("mergeAllPages"):
            return story_plan
        positions = {item.get("id"): index + 1 for index, item in enumerate(items)}
        selected = [positions.get(item_id) for item_id in item_ids]
        if not selected or any(position is None for position in selected):
            return story_plan
        start, end = min(selected), max(selected)
        if len(selected) != end - start + 1:
            return story_plan

        def local_ranges(ranges: Any) -> list[dict[str, Any]]:
            result = []
            for value in ranges if isinstance(ranges, list) else []:
                if not isinstance(value, dict):
                    continue
                range_start, range_end = value.get("startPage"), value.get("endPage")
                if not isinstance(range_start, int) or not isinstance(range_end, int):
                    continue
                overlap_start, overlap_end = max(start, range_start), min(end, range_end)
                if overlap_start <= overlap_end:
                    local_range = {
                        **value,
                        "startPage": overlap_start - start + 1,
                        "endPage": overlap_end - start + 1,
                    }
                    if "pageCount" in value:
                        local_range["pageCount"] = overlap_end - overlap_start + 1
                    result.append(local_range)
            return result

        return {
            **story_plan,
            "segments": local_ranges(story_plan.get("segments")),
            "archives": local_ranges(story_plan.get("archives")),
        }

    def _find_next_queued_item(
        self, batch_id: str, items: list[dict[str, Any]], stage_id: str | None = None
    ) -> dict[str, Any] | None:
        for item in items:
            item_id = item.get("id")
            if not item_id:
                continue
            if (batch_id, item_id) in self._running_items:
                continue
            if item.get("status") == "queued" and item.get("stage") not in {"awaiting_translation", "reserved"}:
                if stage_id is not None and self._item_batch_stage(item) != stage_id:
                    continue
                return item
        return None

    @staticmethod
    def _item_batch_stage(item: dict[str, Any]) -> str:
        stage = item.get("retryFromStage") or item.get("pipelineStage") or item.get("stage") or "initialize"
        stage = str(stage)
        if stage.startswith(("drafting:", "editing:")):
            return "translation"
        stage = _BATCH_STAGE_ALIASES.get(stage, stage)
        return stage if stage in _BATCH_STAGE_ORDER else "initialize"

    @classmethod
    def _current_batch_stage(cls, items: list[dict[str, Any]]) -> str | None:
        active = [item for item in items if item.get("status") != "completed"]
        if not active or any(item.get("status") == "error" for item in active):
            return None
        return min(
            (cls._item_batch_stage(item) for item in active),
            key=_BATCH_STAGE_ORDER.index,
        )

    def _find_ocr_group(self, batch: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compatible: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            item_id = item.get("id")
            if (
                item.get("status") != "queued"
                or item.get("pipelineStage") != "ocr"
                or not item_id
                or (batch["id"], item_id) in self._running_items
            ):
                continue
            config = self._config_for(batch, item)
            if config.ocr.ocr != Ocr.ocr48px_ctc:
                continue
            key = fingerprint(config.ocr.dict())
            compatible.setdefault(key, []).append(item)
        group = next((pages for pages in compatible.values() if len(pages) > 1), [])
        return group[:2]

    def _find_page_inference_group(
        self, batch: dict[str, Any], items: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]] | None:
        for stage_id in ("upscaling", "detection", "bubble_detection"):
            if stage_id == "detection" and self.mps_memory_mode:
                continue
            compatible: dict[str, list[dict[str, Any]]] = {}
            for item in items:
                item_id = item.get("id")
                if (
                    item.get("status") != "queued"
                    or item.get("pipelineStage") != stage_id
                    or not item_id
                    or (batch["id"], item_id) in self._running_items
                ):
                    continue
                config = self._config_for(batch, item)
                if stage_id == "bubble_detection":
                    if not config.bubble_detection.enabled:
                        continue
                    settings = config.bubble_detection.dict()
                elif stage_id == "detection":
                    if config.detector.detector not in {Detector.default, Detector.dbconvnext}:
                        continue
                    settings = config.detector.dict()
                else:
                    settings = config.upscale.dict()
                compatible.setdefault(fingerprint(settings), []).append(item)
            group = next((pages for pages in compatible.values() if len(pages) > 1), [])
            if group:
                return stage_id, group[:_INFERENCE_PAGE_BATCH_SIZE]
        return None

    async def _claim_prepare_items(
        self, batch_id: str, group: list[dict[str, Any]], stage_id: str = "ocr"
    ) -> list[dict[str, Any]]:
        item_ids = {item["id"] for item in group}
        claimed: list[dict[str, Any]] = []

        def mutate(manifest: dict[str, Any]):
            if manifest.get("status") == "paused" or batch_id in self._stopping_batches:
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

        await self.store.mutate(batch_id, mutate)
        return claimed

    async def _launch_available(self) -> bool:
        if self.executors.free_executors() <= 0:
            return False
        fetch = getattr(self.store, "list_runnable_batches", self.store.list_batches)
        batches = await fetch()
        for batch in batches:
            batch_id = batch["id"]
            if batch_id in self._stopping_batches:
                continue
            if batch.get("status") == "paused":
                continue
            if batch.get("status") not in {"waiting", "processing"}:
                continue
            items = batch.get("items", [])
            if not items:
                continue

            if not hasattr(self.store, "mutate"):
                claimed_item = await self._claim_item(batch_id)
                if not claimed_item:
                    continue
                instance = await self.executors.find_executor()
                task = asyncio.create_task(self._process_item(batch_id, claimed_item["id"], instance))
                running_key = (batch_id, claimed_item["id"])
                self._running[running_key] = task
                task.add_done_callback(lambda _, key=running_key: self._running.pop(key, None))
                return True

            if batch.get("kind") in {"rerender", "pipeline-rerun"}:
                next_queued = self._find_next_queued_item(batch_id, items)
                if next_queued:
                    instance = await self.executors.find_executor()
                    claimed_item = await self._claim_pipeline_rerun_item(batch_id, next_queued["id"])
                    if not claimed_item:
                        await self.executors.free_executor(instance)
                        continue
                    item_id = claimed_item["id"]
                    self._running_items.add((batch_id, item_id))
                    task = asyncio.create_task(
                        self._process_pipeline_rerun_item(batch_id, item_id, instance),
                        name=f"batch-{_log_token(batch_id)}-rerun-{_log_token(item_id)}",
                    )
                    running_key = (batch_id, f"rerun:{item_id}")
                    self._running[running_key] = task
                    def on_rerun_done(_, key=running_key, b_id=batch_id, i_id=item_id):
                        self._running.pop(key, None)
                        self._running_items.discard((b_id, i_id))
                    task.add_done_callback(on_rerun_done)
                    return True
                continue

            settings = batch.get("settings", {})
            current_stage = self._current_batch_stage(items)
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
                self._find_ready_translation_group(batch_id, items, size, settings)
                if current_stage == "translation"
                else None
            )
            if ready_group:
                instance = await self._find_stage_executor()
                if instance is None:
                    continue
                claimed = await self._claim_translation_group(batch_id, ready_group)
                if not claimed:
                    await self.executors.free_executor(instance)
                    continue
                item_ids = [item["id"] for item in claimed]
                for item_id in item_ids:
                    self._running_items.add((batch_id, item_id))
                task = asyncio.create_task(
                    self._process_translation_group(batch_id, claimed, instance),
                    name=f"batch-{_log_token(batch_id)}-trans-{_log_token(item_ids[0])}",
                )
                running_key = (batch_id, f"trans:{item_ids[0]}")
                self._running[running_key] = task
                def on_trans_done(_, key=running_key, b_id=batch_id, ids=item_ids):
                    self._running.pop(key, None)
                    for i_id in ids:
                        self._running_items.discard((b_id, i_id))
                task.add_done_callback(on_trans_done)
                return True

            inference_group = self._find_page_inference_group(batch, items)
            if inference_group and inference_group[0] == current_stage:
                stage_id, group = inference_group
                instance = await self._find_stage_executor()
                if instance is not None:
                    claimed = await self._claim_prepare_items(batch_id, group, stage_id)
                    if claimed:
                        item_ids = [item["id"] for item in claimed]
                        self._running_items.update((batch_id, item_id) for item_id in item_ids)
                        task = asyncio.create_task(
                            self._process_checkpointed_model_group(batch_id, claimed, instance, stage_id),
                            name=f"batch-{_log_token(batch_id)}-{stage_id}-{_log_token(item_ids[0])}",
                        )
                        running_key = (batch_id, f"{stage_id}:{item_ids[0]}")
                        self._running[running_key] = task

                        def on_model_batch_done(_, key=running_key, b_id=batch_id, ids=item_ids):
                            self._running.pop(key, None)
                            self._running_items.difference_update((b_id, item_id) for item_id in ids)

                        task.add_done_callback(on_model_batch_done)
                        return True
                    await self.executors.free_executor(instance)
                    continue

            ocr_group = self._find_ocr_group(batch, items) if current_stage == "ocr" else []
            if ocr_group:
                instance = await self._find_stage_executor()
                if instance is not None:
                    claimed = await self._claim_prepare_items(batch_id, ocr_group)
                    if claimed:
                        item_ids = [item["id"] for item in claimed]
                        self._running_items.update((batch_id, item_id) for item_id in item_ids)
                        task = asyncio.create_task(
                            self._process_checkpointed_ocr_group(batch_id, claimed, instance),
                            name=f"batch-{_log_token(batch_id)}-ocr-{_log_token(item_ids[0])}",
                        )
                        running_key = (batch_id, f"ocr:{item_ids[0]}")
                        self._running[running_key] = task

                        def on_ocr_done(_, key=running_key, b_id=batch_id, ids=item_ids):
                            self._running.pop(key, None)
                            self._running_items.difference_update((b_id, item_id) for item_id in ids)

                        task.add_done_callback(on_ocr_done)
                        return True
                    await self.executors.free_executor(instance)
                    continue

            next_queued = self._find_next_queued_item(batch_id, items, current_stage)
            if next_queued:
                instance = await self._find_stage_executor()
                if instance is None:
                    continue
                claimed_item = await self._claim_prepare_item(batch_id, next_queued["id"])
                if not claimed_item:
                    await self.executors.free_executor(instance)
                    continue
                item_id = claimed_item["id"]
                self._running_items.add((batch_id, item_id))
                process = self._process_prepare_item
                work = "stage"
                task = asyncio.create_task(
                    process(batch_id, item_id, instance),
                    name=f"batch-{_log_token(batch_id)}-{work}-{_log_token(item_id)}",
                )
                running_key = (batch_id, f"{work}:{item_id}")
                self._running[running_key] = task
                def on_prep_done(_, key=running_key, b_id=batch_id, i_id=item_id):
                    self._running.pop(key, None)
                    self._running_items.discard((b_id, i_id))
                task.add_done_callback(on_prep_done)
                return True

        return False

    async def _claim_prepare_item(self, batch_id: str, item_id: str) -> dict[str, Any] | None:
        claimed: dict[str, Any] | None = None

        def mutate(manifest: dict[str, Any]):
            nonlocal claimed
            if manifest.get("status") == "paused" or batch_id in self._stopping_batches:
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

        await self.store.mutate(batch_id, mutate)
        return claimed

    async def _claim_pipeline_rerun_item(self, batch_id: str, item_id: str) -> dict[str, Any] | None:
        claimed: dict[str, Any] | None = None

        def mutate(manifest: dict[str, Any]):
            nonlocal claimed
            if manifest.get("status") == "paused" or batch_id in self._stopping_batches:
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

        await self.store.mutate(batch_id, mutate)
        return claimed

    async def _claim_rerender_item(self, batch_id: str, item_id: str) -> dict[str, Any] | None:
        return await self._claim_pipeline_rerun_item(batch_id, item_id)


    async def _claim_translation_group(
        self, batch_id: str, group_items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        group_ids = {item["id"] for item in group_items}

        def mutate(manifest: dict[str, Any]):
            if manifest.get("status") == "paused" or batch_id in self._stopping_batches:
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

        await self.store.mutate(batch_id, mutate)
        return claimed

    async def _claim_items(self, batch_id: str, limit: int) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []

        def mutate(manifest: dict[str, Any]):
            if manifest.get("status") == "paused" or batch_id in self._stopping_batches:
                return False
            for item in manifest.get("items", []):
                if item.get("status") != "queued":
                    continue
                reservation = (batch_id, item["id"])
                if reservation in self._reserved_items:
                    continue
                self._reserved_items.add(reservation)
                _set_item_stage(item, "reserved")
                item["settings"] = dict(manifest.get("settings", {}))
                claimed.append(dict(item))
                if len(claimed) == limit:
                    break
            if claimed:
                claimed_id = claimed[0]["id"]
                for item in manifest.get("items", []):
                    if item.get("id") == claimed_id:
                        item.update(status="processing")
                        _set_item_stage(item, "starting")
                manifest["status"] = "processing"
            return bool(claimed)

        await self.store.mutate(batch_id, mutate)
        return claimed

    async def _claim_item(self, batch_id: str) -> dict[str, Any] | None:
        claimed: dict[str, Any] | None = None

        def mutate(manifest: dict[str, Any]):
            nonlocal claimed
            if manifest.get("status") == "paused" or batch_id in self._stopping_batches:
                return False
            for item in manifest.get("items", []):
                if item.get("status") == "queued":
                    item["status"] = "processing"
                    _set_item_stage(item, "starting")
                    item["settings"] = dict(manifest.get("settings", {}))
                    claimed = dict(item)
                    manifest["status"] = "processing"
                    return True
            return False

        await self.store.mutate(batch_id, mutate)
        return claimed

    async def _process_prepare_item(self, batch_id: str, item_id: str, instance: Any) -> None:
        if hasattr(instance, "_run_translation") and getattr(instance, "translator", None) is not None:
            await self._process_checkpointed_prepare_item(batch_id, item_id, instance)
            return

        hook = None
        image = None
        folder = None
        short_b = _log_token(batch_id)
        short_i = _log_token(item_id)
        token = correlation_id_ctx.set(f"batch-{short_b}/prep-{short_i}")
        logger.info(f"Starting preparation for batch item {item_id} (batch {batch_id})")
        try:
            batch = await self.store.get_batch(batch_id)
            item = next(item for item in batch["items"] if item["id"] == item_id)
            input_path = await self.store.input_path(batch_id, item_id)
            with Image.open(input_path) as opened:
                image = opened.convert("RGB")
            config = self._config_for(batch, item)

            translator = getattr(instance, "translator", None)
            if translator is not None and hasattr(translator, "add_progress_hook"):
                main_loop = asyncio.get_running_loop()

                async def progress(state: str, _finished: bool):
                    if state.startswith("debug_folder:"):
                        folder_name = state.split(":", 1)[1]
                        future = asyncio.run_coroutine_threadsafe(
                            self._set_result_folder(batch_id, item_id, folder_name), main_loop
                        )
                        await asyncio.wrap_future(future)
                        return
                    if state.startswith(("final_ready:", "rendering_folder:", "offline_model:", "gemini_model:")):
                        return
                    future = asyncio.run_coroutine_threadsafe(
                        self._set_stage(batch_id, item_id, state), main_loop
                    )
                    await asyncio.wrap_future(future)

                hook = progress
                translator.add_progress_hook(hook)

            if hasattr(instance, "prepare"):
                ctx = await instance.prepare(image, config)
            elif hasattr(instance, "sent"):
                ctx = await instance.sent(image, config)
            else:
                raise RuntimeError("Executor instance does not support prepare or sent")

            folder = getattr(ctx, "debug_folder", None)
            if not isinstance(folder, str) or Path(folder).name != folder:
                raise RuntimeError("Preparation returned an invalid result folder")

            if (not getattr(ctx, "text_regions", None)
                    and config.translator.translation_quality != "professional"):
                index_result = getattr(self.store, "register_result", None)
                if index_result is not None:
                    await index_result(folder, page_order=config.page_order, page_id=item.get("pageId"))

                def complete_textless(manifest: dict[str, Any]):
                    for entry in manifest.get("items", []):
                        if entry.get("id") == item_id:
                            entry.update(
                                status="completed",
                                stage="finished",
                                error=None,
                                resultFolder=folder,
                                needsReview=False,
                            )
                    pending = any(
                        entry.get("status") in {"queued", "processing"}
                        for entry in manifest.get("items", [])
                    )
                    if not pending:
                        manifest["status"] = "error" if any(
                            entry.get("status") == "error" for entry in manifest.get("items", [])
                        ) else "completed"
                    return True

                await self.store.mutate(batch_id, complete_textless)
                input_path.unlink(missing_ok=True)
                logger.info(f"Completed textless batch item {item_id} (folder: {folder})")
            else:
                def ready_for_translation(manifest: dict[str, Any]):
                    for entry in manifest.get("items", []):
                        if entry.get("id") == item_id:
                            entry.update(
                                status="processing",
                                stage="awaiting_translation",
                                error=None,
                                resultFolder=folder,
                            )
                    return True

                await self.store.mutate(batch_id, ready_for_translation)
                logger.info(f"Prepared batch item {item_id} (folder: {folder}), awaiting translation")
        except asyncio.CancelledError:
            logger.warning(f"Cancelled preparation of batch item {item_id}")
            raise
        except Exception as exc:
            logger.error(f"Error preparing batch item {item_id}: {exc}")
            def fail(manifest: dict[str, Any]):
                for entry in manifest.get("items", []):
                    if entry.get("id") == item_id:
                        entry.update(
                            status="error",
                            stage="error",
                            error=str(exc),
                            resultFolder=folder or entry.get("resultFolder"),
                        )
                if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                    manifest["status"] = "error"
                return True

            await self.store.mutate(batch_id, fail)
        finally:
            translator = getattr(instance, "translator", None)
            if hook is not None and translator is not None and hook in translator._progress_hooks:
                translator._progress_hooks.remove(hook)
            if image is not None:
                image.close()
            self._running_items.discard((batch_id, item_id))
            await self.executors.free_executor(instance)
            self._wake.set()
            correlation_id_ctx.reset(token)

    @staticmethod
    def _next_prepare_stage(run: PipelineRun) -> str | None:
        stages = {stage.get("id"): stage for stage in run.manifest.get("stages", [])}
        return next((stage for stage in _PRE_TRANSLATION_STAGES
                     if stages.get(stage, {}).get("status") == "pending"), None)

    @staticmethod
    def _next_batch_stage(run: PipelineRun) -> str | None:
        stages = {stage.get("id"): stage for stage in run.manifest.get("stages", [])}
        return next((stage for stage in _BATCH_STAGE_ORDER[1:]
                     if stages.get(stage, {}).get("status") in {
                         "pending", "failed", "running", "interrupted"
                     }), None)

    async def _checkpoint_run(self, folder: str) -> PipelineRun | None:
        run = PipelineRun.get_or_load(self.result_root, folder)
        if run is not None:
            return run
        database = getattr(self.store, "database", None)
        documents = await database.get_documents(folder) if database is not None else {}
        result_dir = self.result_root / folder
        if result_dir.is_dir():
            for path in result_dir.glob("*.json"):
                if path.name in documents:
                    continue
                try:
                    documents[path.name] = json.loads(path.read_text("utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
        return PipelineRun.from_documents(self.result_root, folder, documents)

    async def _complete_checkpointed_textless(
        self,
        batch_id: str,
        item: dict[str, Any],
        folder: str,
        stage_id: str,
        config: Config,
        run: PipelineRun,
        instance: Any,
    ) -> None:
        translator = instance.translator

        async def finalize():
            ctx = run._ensure_context()
            translator._set_image_context(config, ctx.input)
            if translator._current_image_context:
                translator._current_image_context["subfolder"] = folder
                translator._current_image_context["started_at"] = run.manifest.get("createdAt")
            translator._pipeline_run = run
            run.translator = translator
            ctx.text_regions = []
            ctx.result = ctx.upscaled
            await translator._revert_upscale(config, ctx)
            run.progress("skip-no-regions" if stage_id == "detection" else "skip-no-text", True)
            run.manifest["status"] = "completed"
            run.manifest.pop("error", None)
            await run.checkpoint()
            run.release_runtime()

        await instance._run_translation(finalize)
        index_result = getattr(self.store, "register_result", None)
        if index_result is not None:
            await index_result(folder, page_order=config.page_order, page_id=item.get("pageId"))

        def complete(manifest: dict[str, Any]):
            for entry in manifest.get("items", []):
                if entry.get("id") == item["id"]:
                    entry.update(status="completed", stage="finished", error=None, resultFolder=folder, needsReview=False)
                    entry.pop("pipelineStage", None)
            if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                manifest["status"] = "error" if any(entry.get("status") == "error" for entry in manifest.get("items", [])) else "completed"
            return True

        await self.store.mutate(batch_id, complete)
        (await self.store.input_path(batch_id, item["id"])).unlink(missing_ok=True)

    async def _process_checkpointed_ocr_group(
        self, batch_id: str, claimed: list[dict[str, Any]], instance: Any
    ) -> None:
        item_ids = [item["id"] for item in claimed]
        pages = []
        completed_textless_ids = set()
        slot = None
        token = correlation_id_ctx.set(f"batch-{_log_token(batch_id)}/ocr-{_log_token(item_ids[0])}")
        try:
            slot = await self._acquire_stage_resource("ocr")
            batch = await self.store.get_batch(batch_id)
            translator = instance.translator
            for item in claimed:
                folder = item.get("resultFolder")
                if not isinstance(folder, str):
                    raise RuntimeError("OCR batch page has no saved pipeline checkpoint")
                run = await self._checkpoint_run(folder)
                if run is None:
                    raise RuntimeError(f"Saved pipeline checkpoint is unavailable for {item['id']}")
                run.memory_batch_id = batch_id
                run.memory_page_id = item.get("pageId") or item["id"]
                run.translator = translator
                config = self._config_for(batch, item)
                ctx = run._ensure_context()
                if not getattr(ctx, "textlines", None):
                    detection = run._document("detection.json")
                    if detection is not None:
                        ctx.textlines = deserialize_textlines(detection)
                if not getattr(ctx, "textlines", None):
                    await self._complete_checkpointed_textless(
                        batch_id, item, folder, "ocr", config, run, instance
                    )
                    completed_textless_ids.add(item["id"])
                    continue
                run.ctx = ctx
                pages.append((item, folder, run, config, ctx))

            if pages:
                await self._set_group_stage(batch_id, item_ids, "ocr")

            async def recognize_and_checkpoint():
                requests = []
                for _, folder, run, config, ctx in pages:
                    translator._set_image_context(config, ctx.input)
                    if translator._current_image_context:
                        translator._current_image_context["subfolder"] = folder
                        translator._current_image_context["started_at"] = run.manifest.get("createdAt")
                    translator._pipeline_run = run
                    run.translator = translator
                    requests.append((ctx, config))

                outputs = await translator._run_ocr_batch(requests)
                for (_, folder, run, config, ctx), output in zip(pages, outputs):
                    translator._set_image_context(config, ctx.input)
                    if translator._current_image_context:
                        translator._current_image_context["subfolder"] = folder
                        translator._current_image_context["started_at"] = run.manifest.get("createdAt")
                    translator._pipeline_run = run
                    run.translator = translator
                    await run.retry_stage(
                        "ocr", config, translator, precomputed_ocr=output
                    )
                return outputs

            outputs = await instance._run_translation(recognize_and_checkpoint) if pages else []
            for (item, folder, run, config, _), output in zip(pages, outputs):
                if not output:
                    await self._complete_checkpointed_textless(
                        batch_id, item, folder, "ocr", config, run, instance
                    )
                    continue

                def continue_page(manifest: dict[str, Any], item_id=item["id"], folder=folder):
                    for entry in manifest.get("items", []):
                        if entry.get("id") == item_id:
                            entry.update(status="queued", stage="bubble_detection", pipelineStage="bubble_detection", error=None, resultFolder=folder)
                            entry.pop("stageStartedAt", None)
                    manifest["status"] = "processing"
                    return True

                await self.store.mutate(batch_id, continue_page)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Error running batched OCR for %s: %s", item_ids, exc)
            failed_item_ids = set(item_ids) - completed_textless_ids

            def fail(manifest: dict[str, Any]):
                for entry in manifest.get("items", []):
                    if entry.get("id") in failed_item_ids:
                        entry.update(status="error", stage="ocr", error=str(exc))
                if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                    manifest["status"] = "error"
                return True

            await self.store.mutate(batch_id, fail)
        finally:
            for _, _, run, _, ctx in pages:
                for image in (getattr(ctx, "input", None), getattr(ctx, "img_colorized", None), getattr(ctx, "upscaled", None)):
                    if isinstance(image, Image.Image):
                        image.close()
                run.release_runtime()
            translator = getattr(instance, "translator", None)
            if translator is not None:
                active_run = getattr(translator, "_pipeline_run", None)
                if active_run in [page[2] for page in pages]:
                    translator._pipeline_run = None
            if slot is not None:
                self._release_stage_resource("ocr", slot)
            await self.executors.free_executor(instance)
            self._wake.set()
            correlation_id_ctx.reset(token)

    async def _process_checkpointed_model_group(
        self,
        batch_id: str,
        claimed: list[dict[str, Any]],
        instance: Any,
        stage_id: str,
    ) -> None:
        if stage_id not in {"upscaling", "detection", "bubble_detection"}:
            raise ValueError(f"Unsupported batched model stage: {stage_id}")
        item_ids = [item["id"] for item in claimed]
        pages = []
        slot = None
        translator = getattr(instance, "translator", None)
        token = correlation_id_ctx.set(f"batch-{_log_token(batch_id)}/{stage_id}-{_log_token(item_ids[0])}")
        try:
            slot = await self._acquire_stage_resource(stage_id)
            batch = await self.store.get_batch(batch_id)
            for item in claimed:
                folder = item.get("resultFolder")
                if not isinstance(folder, str):
                    raise RuntimeError(f"{stage_id} page has no saved pipeline checkpoint")
                run = await self._checkpoint_run(folder)
                if run is None:
                    raise RuntimeError(f"Saved pipeline checkpoint is unavailable for {item['id']}")
                run.memory_batch_id = batch_id
                run.memory_page_id = item.get("pageId") or item["id"]
                config = self._config_for(batch, item)
                ctx = run._ensure_context()
                if stage_id == "upscaling":
                    if ctx.img_colorized is None:
                        ctx.img_colorized = ctx.input
                    if ctx.img_colorized is None:
                        raise RuntimeError(f"No image available for upscaling {item['id']}")
                elif ctx.img_rgb is None:
                    activity = "detection" if stage_id == "detection" else "bubble detection"
                    raise RuntimeError(f"No image canvas available for {activity} {item['id']}")
                run.translator = translator
                await run.begin_stage(stage_id, config)
                pages.append((item, folder, run, config, ctx))

            if pages:
                await self._set_group_stage(batch_id, item_ids, stage_id)

            async def infer_batch():
                configs = [page[3] for page in pages]
                contexts = [page[4] for page in pages]
                if stage_id == "upscaling":
                    return await translator._run_upscaling_batch(configs, contexts)
                if stage_id == "detection":
                    return await translator._run_detection_batch(configs, contexts)
                return await translator._run_bubble_detection_batch(configs, contexts)

            outputs = await instance._run_translation(infer_batch)
            if len(outputs) != len(pages):
                raise RuntimeError(f"{stage_id} returned {len(outputs)} pages for {len(pages)} inputs")

            for (item, folder, run, config, ctx), output in zip(pages, outputs):
                translator._set_image_context(config, ctx.input)
                if translator._current_image_context:
                    translator._current_image_context["subfolder"] = folder
                    translator._current_image_context["started_at"] = run.manifest.get("createdAt")
                translator._pipeline_run = run
                run.translator = translator
                kwargs = (
                    {"precomputed_upscale": output}
                    if stage_id == "upscaling"
                    else {"precomputed_detection": output}
                    if stage_id == "detection"
                    else {"precomputed_bubbles": output}
                )
                await run.retry_stage(
                    stage_id,
                    config,
                    translator,
                    stage_already_running=True,
                    **kwargs,
                )

            for item, folder, run, _, _ in pages:
                next_stage = self._next_batch_stage(run)

                def continue_page(manifest: dict[str, Any], item_id=item["id"], folder=folder, next_stage=next_stage):
                    for entry in manifest.get("items", []):
                        if entry.get("id") != item_id:
                            continue
                        if next_stage and next_stage != "translation":
                            entry.update(status="queued", stage=next_stage, pipelineStage=next_stage, error=None, resultFolder=folder)
                            entry.pop("stageStartedAt", None)
                        else:
                            entry.update(status="processing", error=None, resultFolder=folder)
                            _set_item_stage(entry, "awaiting_translation")
                            entry.pop("stageStartedAt", None)
                            entry.pop("pipelineStage", None)
                    manifest["status"] = "processing"
                    return True

                await self.store.mutate(batch_id, continue_page)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Error running batched %s for %s: %s", stage_id, item_ids, exc)
            for _, _, run, _, _ in pages:
                if run._stage(stage_id).get("status") == "running":
                    try:
                        await run.fail_stage(stage_id, str(exc))
                    except Exception:
                        logger.exception("Could not persist failed %s stage", stage_id)

            def fail(manifest: dict[str, Any]):
                for entry in manifest.get("items", []):
                    if entry.get("id") in item_ids:
                        entry.update(status="error", stage=stage_id, error=str(exc))
                if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                    manifest["status"] = "error"
                return True

            await self.store.mutate(batch_id, fail)
        finally:
            for _, _, run, _, _ in pages:
                ctx = run.ctx
                if ctx is not None:
                    seen = set()
                    for image in (
                        getattr(ctx, "input", None),
                        getattr(ctx, "img_colorized", None),
                        getattr(ctx, "upscaled", None),
                    ):
                        if isinstance(image, Image.Image) and id(image) not in seen:
                            seen.add(id(image))
                            image.close()
                run.release_runtime()
            if translator is not None:
                translator._pipeline_run = None
            if slot is not None:
                self._release_stage_resource(stage_id, slot)
            await self.executors.free_executor(instance)
            self._wake.set()
            correlation_id_ctx.reset(token)

    async def _process_checkpointed_prepare_item(
        self, batch_id: str, item_id: str, instance: Any
    ) -> None:
        image = None
        folder = None
        stage_id = None
        run = None
        batch_finished = False
        page_completed = False
        resource_slot = None
        resource_acquired = False
        token = correlation_id_ctx.set(f"batch-{_log_token(batch_id)}/stage-{_log_token(item_id)}")
        try:
            batch = await self.store.get_batch(batch_id)
            item = next(item for item in batch["items"] if item["id"] == item_id)
            pipeline_stage = item.get("pipelineStage") or "initialize"
            resource_slot = await self._acquire_stage_resource(pipeline_stage)
            resource_acquired = True
            if not item.get("resultFolder"):
                await self._set_stage(batch_id, item_id, "initialize")
            if not item.get("pipelineStage"):
                def mark_checkpointed(manifest: dict[str, Any]):
                    for entry in manifest.get("items", []):
                        if entry.get("id") == item_id:
                            entry["pipelineStage"] = "initialize"
                            return True
                    return False
                await self.store.mutate(batch_id, mark_checkpointed)
            config = self._config_for(batch, item)
            translator = instance.translator
            folder = item.get("resultFolder")
            if not isinstance(folder, str):
                folder = None
                input_path = await self.store.input_path(batch_id, item_id)
                with Image.open(input_path) as opened:
                    image = opened.convert("RGB")
            else:
                run = await self._checkpoint_run(folder)
                if run is None:
                    raise RuntimeError("Saved pipeline checkpoint is unavailable")
                run.memory_batch_id = batch_id
                run.memory_page_id = item.get("pageId") or item_id

            async def initialize():
                translator._set_image_context(config, image)
                folder_name = translator._get_image_subfolder()
                if translator._current_image_context:
                    translator._current_image_context["started_at"] = time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    )
                run = PipelineRun(self.result_root, folder_name, image, config)
                run.ctx = Context()
                run.ctx.input = image
                run.memory_batch_id = batch_id
                run.memory_page_id = item.get("pageId") or item_id
                run.translator = translator
                translator._pipeline_run = run
                await asyncio.to_thread(save_jpeg, image, run.path / "input.jpg")
                run.refresh()
                await run.checkpoint()
                run.ctx = None
                run.translator = None
                translator._pipeline_run = None
                return run

            if folder is None:
                run = await instance._run_translation(initialize)
                folder = run.path.name
                await self._set_result_folder(batch_id, item_id, folder)
                stage_id = "initialize"
            else:
                stage_id = (
                    self._next_batch_stage(run)
                    if pipeline_stage == "initialize"
                    else pipeline_stage
                )

                if stage_id is not None and stage_id != "translation":
                    await self._set_stage(batch_id, item_id, stage_id)

                    async def execute_stage():
                        ctx = run._ensure_context()
                        translator._set_image_context(config, ctx.input)
                        translator._current_image_context["subfolder"] = folder
                        translator._current_image_context["started_at"] = run.manifest.get("createdAt")
                        translator._pipeline_run = run
                        run.translator = translator
                        await run.retry_stage(
                            stage_id,
                            config,
                            translator,
                            defer_bubble_detection=True,
                        )
                        return run

                    run = await instance._run_translation(execute_stage)

                empty_after = {
                    "detection": "detection.json",
                    "ocr": "ocr.json",
                    "textline_merge": "text_regions_merged.json",
                }.get(stage_id)
                no_text = empty_after and not run._document(empty_after)
                if no_text:
                    await self._complete_checkpointed_textless(
                        batch_id, item, folder, stage_id, config, run, instance
                    )
                    return

            next_stage = self._next_batch_stage(run)
            if stage_id == "rendering" and next_stage is None:
                result_path = final_file(self.result_root / folder)
                if result_path is None:
                    raise RuntimeError("Rendering produced no final image")
                run.manifest["status"] = "completed"
                run.manifest.pop("error", None)
                await run.checkpoint()
                index_result = getattr(self.store, "register_result", None)
                if index_result is not None:
                    await index_result(folder, page_order=config.page_order, page_id=item.get("pageId"))
                translated_regions = run._document("translations.json") or []
                needs_review = any(
                    isinstance(region, dict) and region.get("review_required")
                    for region in translated_regions
                )

                def complete_page(manifest: dict[str, Any]):
                    nonlocal batch_finished, page_completed
                    for entry in manifest.get("items", []):
                        if entry.get("id") == item_id:
                            entry.update(
                                status="completed",
                                stage="finished",
                                error=None,
                                resultFolder=folder,
                                needsReview=needs_review,
                            )
                            entry.pop("pipelineStage", None)
                            entry.pop("retryFromStage", None)
                            page_completed = True
                    pending = any(
                        entry.get("status") in {"queued", "processing"}
                        for entry in manifest.get("items", [])
                    )
                    if not pending:
                        manifest["status"] = "error" if any(
                            entry.get("status") == "error" for entry in manifest.get("items", [])
                        ) else "completed"
                        batch_finished = manifest["status"] == "completed"
                    return True

                await self.store.mutate(batch_id, complete_page)
                (await self.store.input_path(batch_id, item_id)).unlink(missing_ok=True)
                next_stage = None

            stage_ctx = getattr(run, "ctx", None) if run is not None else None
            if stage_ctx is not None:
                seen_images = set()
                for name in ("input", "img_colorized", "upscaled", "result"):
                    value = getattr(stage_ctx, name, None)
                    if isinstance(value, Image.Image) and id(value) not in seen_images:
                        seen_images.add(id(value))
                        value.close()
            run.ctx = None
            run.translator = None
            if translator._pipeline_run is run:
                translator._pipeline_run = None

            def continue_or_translate(manifest: dict[str, Any]):
                for entry in manifest.get("items", []):
                    if entry.get("id") != item_id:
                        continue
                    if next_stage and next_stage != "translation":
                        entry.update(status="queued", stage=next_stage, pipelineStage=next_stage, error=None, resultFolder=folder)
                        entry.pop("stageStartedAt", None)
                    else:
                        entry.update(status="processing", error=None, resultFolder=folder)
                        _set_item_stage(entry, "awaiting_translation")
                        entry.pop("stageStartedAt", None)
                        entry.pop("pipelineStage", None)
                manifest["status"] = "processing"
                return True

            if not page_completed:
                await self.store.mutate(batch_id, continue_or_translate)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Error running checkpointed batch stage %s for %s: %s", stage_id, item_id, exc)
            def fail(manifest: dict[str, Any]):
                for entry in manifest.get("items", []):
                    if entry.get("id") == item_id:
                        entry.update(status="error", stage=stage_id or "error", error=str(exc), resultFolder=folder or entry.get("resultFolder"))
                if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                    manifest["status"] = "error"
                return True
            await self.store.mutate(batch_id, fail)
        finally:
            if run is not None:
                run.release_runtime()
                translator = getattr(instance, "translator", None)
                if translator is not None and translator._pipeline_run is run:
                    translator._pipeline_run = None
            if image is not None:
                image.close()
            if resource_acquired:
                self._release_stage_resource(pipeline_stage, resource_slot)
            if batch_finished and hasattr(instance, "reclaim_memory"):
                translator = getattr(instance, "translator", None)
                if translator is not None and hasattr(translator, "clear_batch_state"):
                    translator.clear_batch_state(batch_id)
                try:
                    await instance.reclaim_memory()
                except Exception as exc:
                    logger.warning("Failed to reclaim memory after batch %s: %s", batch_id, exc)
            await self.executors.free_executor(instance)
            self._wake.set()
            correlation_id_ctx.reset(token)

    async def _process_pipeline_rerun_item(self, batch_id: str, item_id: str, instance: Any) -> None:
        token = correlation_id_ctx.set(f"batch-{_log_token(batch_id)}/rerun-{_log_token(item_id)}")
        staging_dir: Path | None = None
        hook = None
        stage_runs: dict[PipelineStage, float] = {}
        database = getattr(self.store, "database", None)
        page_ref: str | None = None
        try:
            batch = await self.store.get_batch(batch_id)
            item = next(item for item in batch["items"] if item["id"] == item_id)
            folder = item.get("resultFolder")
            if not isinstance(folder, str) or Path(folder).name != folder:
                raise RuntimeError("Rerun item has no valid result folder")
            result_dir = (self.result_root / folder).resolve()
            if result_dir.parent != self.result_root or not result_dir.is_dir():
                raise RuntimeError("Result folder is unavailable")
            page_ref = item.get("pageId") or folder

            from server.pipeline_rerun import (
                commit_rerun_artifacts,
                execute_rerun_plan,
                load_rerun_context,
                resolve_rerun_plan,
                validate_rerun_prerequisites,
            )

            rerun_mode = item.get("rerunMode") or batch.get("rerunMode") or "typesetting"
            plan = resolve_rerun_plan(rerun_mode)

            valid, reason = validate_rerun_prerequisites(result_dir, plan.mode, database=database, record=item)
            if not valid:
                raise RuntimeError(reason or "Prerequisites check failed for pipeline rerun")
            if database is not None and page_ref:
                await database.invalidate_pipeline_stages(page_ref, list(plan.stages_to_invalidate))

            saved_settings = item.get("settings") if isinstance(item.get("settings"), dict) else {}
            config_item = {**item, "settings": saved_settings}
            config = self._config_for(batch, config_item)
            if plan.mode == "typesetting":
                config.bubble_detection.enabled = False
                config.translator.translator = "none"
                config.translator.translation_quality = "fast"
                config.upscale.upscale_ratio = None
                config.upscale.revert_upscaling = False

            ctx, state = await load_rerun_context(result_dir, plan, config, database=database, record_id=item.get("pageId"))

            async def progress_hook(stage: str, _finished: bool = False):
                canonical_stage = stage_from_progress(stage)
                if canonical_stage is not None and canonical_stage not in stage_runs:
                    config_settings = config.model_dump(exclude_none=True)
                    settings = settings_for_stage(config_settings, canonical_stage)
                    attempt = await database.start_pipeline_stage(
                        page_ref,
                        canonical_stage,
                        settings_fingerprint=fingerprint(settings),
                        settings=settings,
                    ) if database is not None and page_ref else None
                    if attempt is not None:
                        stage_runs[canonical_stage] = time.perf_counter()

                def set_stage(manifest: dict[str, Any]):
                    for entry in manifest.get("items", []):
                        if entry.get("id") == item_id:
                            return _set_item_stage(entry, stage)
                    return True
                await self.store.mutate(batch_id, set_stage)

            staging_dir = Path(tempfile.mkdtemp(prefix=f".rerun-{item_id[:8]}-", dir=result_dir))
            translator = getattr(instance, "translator", instance)

            async def translator_progress(stage: str, finished: bool = False):
                await progress_hook(stage, finished)

            hook = translator_progress
            if hasattr(translator, "_progress_hooks"):
                translator._progress_hooks.append(hook)

            if plan.mode.value == "full":
                await progress_hook("input")

            async def execute():
                return await execute_rerun_plan(
                    translator=translator,
                    ctx=ctx,
                    config=config,
                    plan=plan,
                    state=state,
                    staging_dir=staging_dir,
                    progress_hook=progress_hook,
                )

            if hasattr(instance, "_run_translation"):
                executed_ctx, remap_result = await instance._run_translation(execute)
            else:
                executed_ctx, remap_result = await execute()

            await progress_hook("finalize")
            await commit_rerun_artifacts(
                result_dir=result_dir,
                staging_dir=staging_dir,
                plan=plan,
                remap_result=remap_result,
                database=database,
                job_id=f"{batch_id}:{item_id}",
            )

            for stage, started in stage_runs.items():
                await database.finish_pipeline_stage(
                    page_ref,
                    stage,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )

            index_result = getattr(self.store, "register_result", None)
            if index_result is not None:
                await index_result(
                    folder,
                    page_order=item.get("pageOrder"),
                    page_id=item.get("pageId"),
                )

            needs_review = any(
                bool(getattr(region, "review_required", False))
                for region in (getattr(executed_ctx, "text_regions", None) or [])
            )

            def complete(manifest: dict[str, Any]):
                for entry in manifest.get("items", []):
                    if entry.get("id") == item_id:
                        entry.update(
                            status="completed",
                            stage="finished",
                            error=None,
                            resultFolder=folder,
                            needsReview=needs_review,
                        )
                if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                    manifest["status"] = "error" if any(
                        entry.get("status") == "error" for entry in manifest.get("items", [])
                    ) else "completed"
                return True

            await self.store.mutate(batch_id, complete)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Error running pipeline rerun for item %s: %s", item_id, exc)
            if database is not None and page_ref and stage_runs:
                last_stage = next(reversed(stage_runs))
                for stage, started in stage_runs.items():
                    try:
                        await database.finish_pipeline_stage(
                            page_ref,
                            stage,
                            "failed" if stage is last_stage else "interrupted",
                            duration_ms=int((time.perf_counter() - started) * 1000),
                            error_code=type(exc).__name__ if stage is last_stage else None,
                            error_message=str(exc) if stage is last_stage else None,
                        )
                    except Exception:
                        logger.exception("Failed to persist rerun stage status for %s", stage.value)

            def fail(manifest: dict[str, Any]):
                for entry in manifest.get("items", []):
                    if entry.get("id") == item_id:
                        entry.update(status="error", stage="error", error=str(exc))
                if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                    manifest["status"] = "error"
                return True

            await self.store.mutate(batch_id, fail)
        finally:
            if staging_dir is not None and staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            translator = getattr(instance, "translator", instance)
            if hook is not None and translator is not None and hook in getattr(translator, "_progress_hooks", []):
                translator._progress_hooks.remove(hook)
            self._running_items.discard((batch_id, item_id))
            await self.executors.free_executor(instance)
            self._wake.set()
            correlation_id_ctx.reset(token)

    async def _process_rerender_item(self, batch_id: str, item_id: str, instance: Any) -> None:
        return await self._process_pipeline_rerun_item(batch_id, item_id, instance)


    async def _process_translation_group(
        self, batch_id: str, claimed: list[dict[str, Any]], instance: Any
    ) -> None:
        item_ids = [item["id"] for item in claimed]
        hook = None
        owns_translation_lock = False
        owns_network_slot = False
        translation_runs = {}
        lock = self._translation_locks.setdefault(batch_id, asyncio.Lock())
        main_loop = asyncio.get_running_loop()
        token = correlation_id_ctx.set(f"batch-{_log_token(batch_id)}/trans-{_log_token(item_ids[0])}")
        logger.info(f"Starting batch translation for items {item_ids} (batch {batch_id})")
        try:
            network_slot = await self._acquire_stage_resource("translation")
            owns_network_slot = True
            batch = await self.store.get_batch(batch_id)
            current = {item["id"]: item for item in batch["items"]}
            configs = [self._config_for(batch, current[item_id]) for item_id in item_ids]

            story_plan = self._story_plan_for_group(
                configs[0].translator.story_plan,
                batch.get("items", []),
                item_ids,
            ) if configs else None
            if story_plan is not None:
                for config in configs:
                    config.translator.story_plan = story_plan

            translator = getattr(instance, "translator", None)
            for item_id in item_ids:
                folder = current[item_id].get("resultFolder")
                run = await self._checkpoint_run(folder) if isinstance(folder, str) else None
                if run is None:
                    raise RuntimeError(f"Saved pipeline checkpoint is unavailable for {item_id}")
                run.memory_batch_id = batch_id
                run.memory_page_id = current[item_id].get("pageId") or item_id
                run.translator = translator
                if hasattr(run, "_memory_begin"):
                    run._memory_begin("translation")
                translation_runs[item_id] = run

            contexts_with_configs = []
            for item_id, config in zip(item_ids, configs):
                item_entry = current[item_id]
                folder = item_entry.get("resultFolder")
                result_dir = self.result_root / folder if isinstance(folder, str) else None

                saved_documents = {}
                database = getattr(self.store, "database", None)
                if database is not None and folder:
                    saved_documents = await database.get_documents(folder) or {}
                if result_dir:
                    for name in (
                        "pipeline_manifest.json", "detection.json", "ocr.json",
                        "text_regions_merged.json", "bubble_detections.json",
                    ):
                        path = result_dir / name
                        if name not in saved_documents and path.is_file():
                            try:
                                saved_documents[name] = json.loads(path.read_text("utf-8"))
                            except (OSError, UnicodeError, json.JSONDecodeError):
                                pass
                merged_data = saved_documents.get("text_regions_merged.json")

                ctx = Context()
                if merged_data:
                    ctx.text_regions = deserialize_textblocks(merged_data)
                    ctx.result_documents = saved_documents
                else:
                    ctx.text_regions = []
                    ctx.result_documents = saved_documents

                if folder:
                    ctx.debug_folder = folder
                    ctx.image_context = {
                        "subfolder": folder,
                        "file_md5": folder.split("-")[-1] if "-" in folder else folder,
                        "request_id": item_entry.get("requestId"),
                    }
                contexts_with_configs.append((ctx, config))

            if translator is not None and hasattr(translator, "add_progress_hook"):
                async def progress(state: str, _finished: bool):
                    nonlocal owns_translation_lock
                    professional_stage = state == "analyzing-story" or state.startswith(("drafting:", "editing:"))
                    if (state == "translating" or professional_stage) and not owns_translation_lock:
                        future = asyncio.run_coroutine_threadsafe(lock.acquire(), main_loop)
                        await asyncio.wrap_future(future)
                        owns_translation_lock = True
                    elif state == "after-translating" and owns_translation_lock:
                        main_loop.call_soon_threadsafe(lock.release)
                        owns_translation_lock = False
                    if state.startswith(("debug_folder:", "final_ready:", "rendering_folder:", "offline_model:", "gemini_model:")):
                        return
                    if state in {"translating", "after-translating", "analyzing-story"} or professional_stage:
                        future = asyncio.run_coroutine_threadsafe(
                            self._set_group_stage(batch_id, item_ids, state),
                            main_loop,
                        )
                        await asyncio.wrap_future(future)
                        return
                    target_ids = self._progress_item_ids(state, item_ids, translator)
                    if target_ids:
                        future = asyncio.run_coroutine_threadsafe(
                            self._set_active_group_item(batch_id, item_ids, target_ids[0], state),
                            main_loop,
                        )
                        await asyncio.wrap_future(future)

                hook = progress
                translator.add_progress_hook(hook)

            if not hasattr(instance, "translate_batch_contexts"):
                raise RuntimeError("Stage-barrier batches require an executor with translate_batch_contexts")
            translated_pairs = await instance.translate_batch_contexts(
                contexts_with_configs, batch_size=len(contexts_with_configs)
            )
            if len(translated_pairs) != len(item_ids):
                raise RuntimeError(
                    f"Translation returned {len(translated_pairs)} pages for {len(item_ids)} inputs"
                )

            for item_id, (ctx, config) in zip(item_ids, translated_pairs):
                folder = current[item_id].get("resultFolder")
                error = getattr(ctx, "translation_error", None)
                if error:
                    run = translation_runs[item_id]
                    run._finish("translation", "failed", error)
                    await run.checkpoint()
                    run.release_runtime()
                    def fail_page(manifest: dict[str, Any], item_id=item_id, error=error):
                        for entry in manifest.get("items", []):
                            if entry.get("id") == item_id:
                                entry.update(status="error", stage="translation", error=error)
                        return True
                    await self.store.mutate(batch_id, fail_page)
                    continue
                if not isinstance(folder, str):
                    raise RuntimeError(f"Translated page {item_id} has no pipeline checkpoint")
                run = translation_runs[item_id]

                for name, payload in (getattr(ctx, "result_documents", None) or {}).items():
                    if name != "pipeline_manifest.json" and str(name).endswith(".json"):
                        run.write_json(name, payload)
                run.write_json("translations.json", serialize_regions(ctx.text_regions or []))
                run._finish("translation")
                translation_stage = run._stage("translation")
                if getattr(ctx, "translation_started_at", None):
                    translation_stage["startedAt"] = ctx.translation_started_at
                if getattr(ctx, "translation_finished_at", None):
                    translation_stage["finishedAt"] = ctx.translation_finished_at
                if getattr(ctx, "translation_duration_ms", None) is not None:
                    translation_stage["durationMs"] = ctx.translation_duration_ms
                await run.checkpoint()
                run.release_runtime()

                def queue_mask_stage(manifest: dict[str, Any], item_id=item_id, folder=folder):
                    for entry in manifest.get("items", []):
                        if entry.get("id") == item_id:
                            entry.update(
                                status="queued",
                                stage="mask_generation",
                                pipelineStage="mask_generation",
                                error=None,
                                resultFolder=folder,
                            )
                            entry.pop("stageStartedAt", None)
                    manifest["status"] = "processing"
                    return True
                await self.store.mutate(batch_id, queue_mask_stage)

            def update_batch_status(manifest: dict[str, Any]):
                pending = any(
                    item.get("status") in {"queued", "processing"}
                    for item in manifest.get("items", [])
                )
                if not pending:
                    manifest["status"] = "error" if any(
                        item.get("status") == "error" for item in manifest.get("items", [])
                    ) else "completed"
                elif manifest.get("status") != "paused":
                    manifest["status"] = "processing"
                return True
            await self.store.mutate(batch_id, update_batch_status)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Error processing batch group %s: %s", item_ids, exc)
            def fail_group(manifest: dict[str, Any]):
                for entry in manifest.get("items", []):
                    if entry.get("id") in item_ids:
                        entry.update(status="error", stage="translation", error=str(exc))
                if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                    manifest["status"] = "error"
                return True
            await self.store.mutate(batch_id, fail_group)
        finally:
            for run in translation_runs.values():
                if hasattr(run, "_memory_end"):
                    run._memory_end("translation")
                if getattr(run, "ctx", None) is not None or getattr(run, "translator", None) is not None:
                    release = getattr(run, "release_runtime", None)
                    if release is not None:
                        release()
            if instance is not None:
                translator = getattr(instance, "translator", None)
                if hook is not None and translator is not None and hook in translator._progress_hooks:
                    translator._progress_hooks.remove(hook)
                await self.executors.free_executor(instance)
            if owns_translation_lock:
                lock.release()
            if owns_network_slot:
                self._release_stage_resource("translation", network_slot)
            self._reserved_items.difference_update((batch_id, item_id) for item_id in item_ids)
            for item_id in item_ids:
                self._running_items.discard((batch_id, item_id))
            self._wake.set()
            correlation_id_ctx.reset(token)

    async def _process_group(self, batch_id: str, claimed: list[dict[str, Any]], instance: Any) -> None:
        await self._process_translation_group(batch_id, claimed, instance)

    @staticmethod
    def _progress_item_ids(state: str, item_ids: list[str], translator: Any) -> list[str]:
        if state in {"translating", "after-translating", "error-translating"}:
            return item_ids[:1]
        context = getattr(translator, "_current_image_context", None) or {}
        request_id = context.get("request_id")
        current_id = request_id.rsplit(":", 1)[-1] if isinstance(request_id, str) else None
        return [current_id] if current_id in item_ids else []

    async def _set_group_stage(
        self, batch_id: str, item_ids: list[str], stage: str
    ) -> None:
        def mutate(manifest: dict[str, Any]):
            for item in manifest.get("items", []):
                if item.get("id") in item_ids and item.get("status") in {"processing", "queued"}:
                    item.update(status="processing")
                    _set_item_stage(item, stage)
            manifest["status"] = "processing"
            return True

        await self.store.mutate(batch_id, mutate)

    async def _set_active_group_item(
        self, batch_id: str, item_ids: list[str], active_id: str, stage: str
    ) -> None:
        def mutate(manifest: dict[str, Any]):
            for item in manifest.get("items", []):
                if item.get("id") not in item_ids:
                    continue
                if item.get("id") == active_id:
                    item.update(status="processing")
                    _set_item_stage(item, stage)
                elif item.get("stage") == "awaiting_translation":
                    item.update(status="processing")
                    _set_item_stage(item, "awaiting_translation")
                    item.pop("stageStartedAt", None)
                elif item.get("status") == "processing":
                    item.update(status="queued", stage="reserved")
                    item.pop("stageStartedAt", None)
            manifest["status"] = "processing"
            return True

        await self.store.mutate(batch_id, mutate)

    @staticmethod
    def _config_for(batch: dict[str, Any], item: dict[str, Any]) -> Config:
        raw = item.get("config")
        if not isinstance(raw, dict):
            settings = item.get("settings", batch.get("settings", {}))
            ocr_prob_val = settings.get("customOcrProb") if settings.get("customOcrProb") not in (None, "") else settings.get("ocrMinConfidence")
            ocr_prob = None
            if ocr_prob_val not in (None, ""):
                try:
                    ocr_prob = float(ocr_prob_val)
                except (ValueError, TypeError):
                    ocr_prob = None
            letter_case = str(settings.get("letterCase") or settings.get("letter_case") or "").strip().lower()
            is_upper = bool(settings.get("uppercase", False)) or letter_case in ("upper", "uppercase", "all_caps", "caps")
            is_lower = (bool(settings.get("lowercase", False)) or letter_case in ("lower", "lowercase")) and not is_upper
            raw = {
                "detector": {
                    "detector": "none" if settings.get("colorizeOnly") else settings.get("textDetector", "default"),
                    "detection_size": settings.get("detectionResolution", "2048"),
                    "box_threshold": settings.get("customBoxThreshold", 0.5),
                    "unclip_ratio": settings.get("customUnclipRatio", 2.3),
                },
                "ocr": {
                    "ocr": settings.get("ocr", Ocr.ocr48px_ctc.value),
                    "prob": ocr_prob,
                    "min_text_length": int(settings.get("minTextLength", 1)),
                    "use_mocr_merge": bool(settings.get("useMocrMerge", False)),
                },
                "render": {
                    "direction": settings.get("renderTextDirection", "auto"),
                    "uppercase": is_upper,
                    "lowercase": is_lower,
                    "renderer": settings.get("renderer", "default"),
                    "alignment": settings.get("renderAlignment", "auto"),
                    "gimp_font": settings.get("renderFont", "wildwords"),
                    "font_path": resolve_font_name_or_path(settings.get("renderFont", "wildwords")),
                    "font_size": settings.get("customFontSize") if settings.get("customFontSize") not in (None, "") else None,
                    "font_size_offset": int(settings.get("fontSizeOffset", 0) or 0),
                    "font_size_minimum": int(settings.get("fontSizeMinimum", 0) if settings.get("fontSizeMinimum") not in (None, "") else 0),
                    "line_spacing": float(settings.get("lineSpacing")) if settings.get("lineSpacing") not in (None, "") else None,
                    "no_hyphenation": bool(settings.get("noHyphenation", False)),
                },
                "translator": {
                    "translator": "none" if settings.get("colorizeOnly") else settings.get("translator", "deepseek"),
                    "target_lang": settings.get("targetLanguage", "ENG"),
                    "translation_quality": settings.get("translationQuality", "fast"),
                    "translation_batch_size": settings.get("translationBatchSize", 20),
                    "story_page_ranges": settings.get("storyPageRanges") or None,
                    "story_plan": settings.get("storyPlan"),
                    "no_text_lang_skip": bool(settings.get("noTextLangSkip", True)),
                    "keep_failed_pages_for_editing": bool(settings.get("keepFailedPagesForEditing", True)),
                },
                "inpainter": {
                    "inpainter": "original" if settings.get("colorizeOnly") else settings.get("inpainter", "default"),
                    "inpainting_size": settings.get("inpaintingSize", "2048"),
                },
                "colorizer": {
                    "colorizer": "none" if item.get("excludeColor") else settings.get("colorizer", "none"),
                    "colorization_size": int(settings.get("colorizationSize", 576)),
                    "denoise_sigma": int(settings.get("denoiseSigma", 25)),
                    "color_threshold": float(settings.get("colorThreshold", 31)),
                    "restore_size": True,
                },
                "upscale": {
                    "upscaler": settings.get("upscaler", "esrgan"),
                    "upscale_ratio": settings.get("upscaleRatio"),
                    "revert_upscaling": bool(settings.get("upscaleRatio")) and bool(settings.get("revertUpscaling", True)),
                },
                "mask_dilation_offset": settings.get("maskDilationOffset", 20),
                "bubble_detection": {
                    "enabled": bool(settings.get("bubbleDetection", True)),
                    "model": settings.get("bubbleModel", "yolov8m"),
                    "confidence": float(settings.get("bubbleConfidence", 0.25)),
                    "mask_threshold": float(settings.get("bubbleMaskThreshold", 0.5)),
                    "padding": int(settings.get("bubblePadding", 9)),
                    "group_regions": bool(settings.get("bubbleGroupRegions", False)),
                },
            }
        config = Config.parse_raw(json.dumps(raw))
        config.original_name = item.get("name")
        config.manga_title = item.get("mangaTitle") or batch.get("title") or batch.get("mangaTitle") or "Ungrouped"
        config.manga_group_id = item.get("mangaGroupId") or batch.get("mangaGroupId")
        config.request_id = item.get("requestId")
        config.page_order = item.get("pageOrder")
        config.source_path = item.get("sourcePath")
        config._web_frontend_optimized = True
        return config

    async def _set_stage(self, batch_id: str, item_id: str, stage: str) -> None:
        try:
            await self.store.mutate(
                batch_id,
                lambda manifest: self._mutate_stage(manifest, item_id, stage),
            )
        except BatchNotFound:
            pass

    async def _set_result_folder(self, batch_id: str, item_id: str, folder: str) -> None:
        if not isinstance(folder, str) or Path(folder).name != folder:
            return
        try:
            await self.store.mutate(
                batch_id,
                lambda manifest: self._mutate_result_folder(manifest, item_id, folder),
            )
        except BatchNotFound:
            pass

    @staticmethod
    def _mutate_stage(manifest: dict[str, Any], item_id: str, stage: str) -> bool:
        for item in manifest.get("items", []):
            if item.get("id") == item_id and item.get("status") == "processing":
                return _set_item_stage(item, stage)
        return False

    @staticmethod
    def _mutate_result_folder(manifest: dict[str, Any], item_id: str, folder: str) -> bool:
        for item in manifest.get("items", []):
            if item.get("id") == item_id and item.get("status") == "processing":
                item["resultFolder"] = folder
                return True
        return False

    async def _process_item(self, batch_id: str, item_id: str, instance: Any) -> None:
        hook = None
        batch_finished = False
        image = context = config = None
        item = None
        input_path = None
        checkpoint_folder = None
        checkpoint_run = None
        short_b = _log_token(batch_id)
        short_i = _log_token(item_id)
        token = correlation_id_ctx.set(f"batch-{short_b}/item-{short_i}")
        logger.info(f"Starting batch item {item_id} (batch {batch_id})")
        try:
            batch = await self.store.get_batch(batch_id)
            item = next(item for item in batch["items"] if item["id"] == item_id)
            if not item.get("retryFromStage"):
                input_path = await self.store.input_path(batch_id, item_id)
                with Image.open(input_path) as opened:
                    image = opened.convert("RGB")
            config = self._config_for(batch, item)

            translator = getattr(instance, "translator", None)
            if translator is not None and hasattr(translator, "add_progress_hook"):
                main_loop = asyncio.get_running_loop()

                async def progress(state: str, _finished: bool):
                    if state.startswith("debug_folder:"):
                        folder = state.split(":", 1)[1]
                        current_loop = asyncio.get_running_loop()
                        if current_loop is main_loop:
                            await self._set_result_folder(batch_id, item_id, folder)
                        else:
                            future = asyncio.run_coroutine_threadsafe(
                                self._set_result_folder(batch_id, item_id, folder), main_loop
                            )
                            await asyncio.wrap_future(future)
                        return
                    if state.startswith(("final_ready:", "rendering_folder:", "offline_model:", "gemini_model:")):
                        return
                    current_loop = asyncio.get_running_loop()
                    if current_loop is main_loop:
                        await self._set_stage(batch_id, item_id, state)
                        return
                    future = asyncio.run_coroutine_threadsafe(
                        self._set_stage(batch_id, item_id, state), main_loop
                    )
                    await asyncio.wrap_future(future)

                hook = progress
                translator.add_progress_hook(hook)

            checkpoint_folder = item.get("resultFolder")
            run = None
            if isinstance(checkpoint_folder, str):
                run = PipelineRun.get_or_load(self.result_root, checkpoint_folder)
                if run is None and getattr(self.store, "database", None) is not None:
                    documents = await self.store.database.get_documents(checkpoint_folder)
                    run = PipelineRun.from_documents(
                        self.result_root, checkpoint_folder, documents
                    )
            checkpoint_run = run
            folder = checkpoint_folder
            failed_stage = item.get("retryFromStage") or next(
                (
                    stage["id"]
                    for stage in (getattr(run, "manifest", {}) or {}).get("stages", [])
                    if stage.get("status") in {"failed", "running"}
                ),
                None,
            )
            if run is not None and failed_stage not in {None, "input"} and hasattr(instance, "_run_translation"):
                translator = getattr(instance, "translator", None)
                if translator is None:
                    raise RuntimeError("No in-process translator available on worker")
                run.translator = translator
                run.ctx = None
                await instance._run_translation(
                    lambda: run.retry_from_stage(failed_stage, config, translator)
                )
                context = run.ctx
            else:
                # A completed or otherwise non-resumable checkpoint is stale for retry.
                checkpoint_folder = None
                context = await instance.sent(image, config)
                folder = getattr(context, "debug_folder", None)
            if not isinstance(folder, str) or Path(folder).name != folder:
                raise RuntimeError("Translation returned an invalid result folder")
            result_folder = self.result_root / folder if isinstance(folder, str) else None
            if not result_folder or not result_folder.is_dir() or final_file(result_folder) is None:
                raise RuntimeError("Translation completed without a verified result folder")

            index_result = getattr(self.store, "register_result", None)
            if index_result is not None:
                await index_result(
                    folder,
                    page_order=config.page_order,
                    page_id=item.get("pageId"),
                )

            model = {
                key: getattr(context, key, None)
                for key in ("translator_model", "offline_model", "gemini_model")
                if context is not None and getattr(context, key, None)
            }

            def complete(manifest: dict[str, Any]):
                nonlocal batch_finished
                for entry in manifest.get("items", []):
                    if entry.get("id") == item_id:
                        entry.update(
                            status="completed",
                            stage="finished",
                            error=None,
                            resultFolder=folder,
                            model=model,
                            needsReview=bool(getattr(context, "manual_review_required", False)),
                        )
                        entry.pop("retryFromStage", None)
                        entry.pop("pipelineStage", None)
                pending = any(
                    entry.get("status") in {"queued", "processing"}
                    for entry in manifest.get("items", [])
                )
                if not pending:
                    manifest["status"] = "error" if any(
                        entry.get("status") == "error" for entry in manifest.get("items", [])
                    ) else "completed"
                    batch_finished = True
                elif manifest.get("status") != "paused":
                    manifest["status"] = "processing"
                return True

            await self.store.mutate(batch_id, complete)
            if input_path is not None:
                input_path.unlink(missing_ok=True)
            logger.info(f"Completed batch item {item_id} (folder: {folder})")
        except asyncio.CancelledError:
            logger.warning(f"Cancelled batch item {item_id}")
            raise
        except Exception as exc:
            logger.error(f"Error processing batch item {item_id}: {exc}")
            try:
                if not checkpoint_folder:
                    active_run = getattr(getattr(instance, "translator", None), "_pipeline_run", None)
                    checkpoint_run = active_run
                    candidate = getattr(getattr(active_run, "path", None), "name", None)
                    if isinstance(candidate, str) and candidate:
                        checkpoint_folder = candidate

                failed_stage = next(
                    (
                        stage["id"]
                        for stage in (getattr(checkpoint_run, "manifest", {}) or {}).get("stages", [])
                        if stage.get("status") in {"failed", "running"}
                    ),
                    None,
                )

                def fail(manifest: dict[str, Any]):
                    nonlocal batch_finished
                    for entry in manifest.get("items", []):
                        if entry.get("id") == item_id:
                            entry.update(
                                status="error",
                                stage=failed_stage or "error",
                                error=str(exc),
                                resultFolder=checkpoint_folder or entry.get("resultFolder"),
                            )
                    if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                        manifest["status"] = "error"
                        batch_finished = True
                    return True

                await self.store.mutate(batch_id, fail)
                if (
                    isinstance(checkpoint_folder, str)
                    and Path(checkpoint_folder).name == checkpoint_folder
                    and item is not None
                ):
                    failed_result = self.result_root / checkpoint_folder
                    index_result = getattr(self.store, "register_result", None)
                    if (
                        index_result is not None
                        and failed_result.is_dir()
                        and final_file(failed_result) is not None
                    ):
                        try:
                            await index_result(
                                checkpoint_folder,
                                page_order=(config.page_order if config is not None else item.get("pageOrder")),
                                page_id=item.get("pageId"),
                            )
                        except Exception:
                            logger.exception("Could not index failed checkpoint %s", checkpoint_folder)
            except BatchNotFound:
                pass
        finally:
            translator = getattr(instance, "translator", None)
            if hook is not None and translator is not None and hook in translator._progress_hooks:
                translator._progress_hooks.remove(hook)
            image = context = config = None
            reclaim_memory = getattr(instance, "reclaim_memory", None)
            if batch_finished and reclaim_memory is not None:
                if translator is not None and hasattr(translator, "clear_batch_state"):
                    translator.clear_batch_state(batch_id)
                try:
                    await reclaim_memory()
                except Exception as exc:
                    logger.warning(f"Failed to reclaim memory after batch {batch_id}: {exc}")
            await self.executors.free_executor(instance)
            self._wake.set()
            correlation_id_ctx.reset(token)

    async def pause(self, batch_id: str) -> dict[str, Any]:
        result = await self.store.mutate(
            batch_id,
            lambda manifest: self._set_batch_status(manifest, "paused"),
        )
        self._wake.set()
        return result

    async def resume(self, batch_id: str) -> dict[str, Any]:
        result = await self.store.mutate(
            batch_id,
            lambda manifest: self._set_batch_status(manifest, "waiting"),
        )
        self._wake.set()
        return result

    @staticmethod
    def _set_batch_status(manifest: dict[str, Any], status: str) -> bool:
        current = manifest.get("status")
        if status == "paused" and current != "processing":
            return False
        if status == "stopping" and current not in {"waiting", "processing", "paused"}:
            return False
        if status == "waiting" and current not in {"waiting", "processing", "paused"}:
            return False
        manifest["status"] = status
        return True

    async def dismiss(self, batch_id: str) -> dict[str, Any]:
        result = await self.store.mutate(
            batch_id, lambda manifest: manifest.update(dismissed=True) is None
        )
        if not any(item.get("status") in {"queued", "processing"} for item in result.get("items", [])):
            await self._compact_batch_groups(result)
        return result

    async def _compact_batch_groups(self, batch: dict[str, Any]) -> None:
        database = getattr(self.store, "database", None)
        if database is None:
            return
        for group_id in sorted({
            item.get("mangaGroupId")
            for item in batch.get("items", [])
            if item.get("mangaGroupId")
        }):
            await database.compact_page_order(group_id)

    async def update_translator(self, batch_id: str, translator: str) -> dict[str, Any]:
        def mutate(manifest: dict[str, Any]):
            manifest.setdefault("settings", {})["translator"] = translator
            for item in manifest.get("items", []):
                if item.get("status") == "queued":
                    item.pop("config", None)
            return True

        result = await self.store.mutate(batch_id, mutate)
        self._wake.set()
        return result

    async def update_title(self, batch_id: str, title: str) -> dict[str, Any]:
        clean_title = title.strip() or "Ungrouped"

        def mutate(manifest: dict[str, Any]):
            manifest["title"] = clean_title
            manifest["mangaTitle"] = clean_title
            for item in manifest.get("items", []):
                if item.get("status") == "queued":
                    item["mangaTitle"] = clean_title
            return True

        result = await self.store.mutate(batch_id, mutate)
        self._wake.set()
        return result

    async def update_priority(self, batch_id: str, priority: bool) -> dict[str, Any]:
        result = await self.store.mutate(
            batch_id,
            lambda manifest: manifest.update(priority=priority) is None,
        )
        self._wake.set()
        return result

    async def update_manual_review(self, batch_id: str, enabled: bool) -> dict[str, Any]:
        def mutate(manifest: dict[str, Any]):
            manifest.setdefault("settings", {})["keepFailedPagesForEditing"] = enabled
            for item in manifest.get("items", []):
                if item.get("status") in {"queued", "error"}:
                    item.setdefault("settings", {})["keepFailedPagesForEditing"] = enabled
                    item.pop("config", None)
            return True

        result = await self.store.mutate(batch_id, mutate)
        self._wake.set()
        return result

    async def retry_item(
        self,
        batch_id: str,
        item_id: str,
        keep_failed_pages_for_editing: bool | None = None,
        from_stage: str | None = None,
    ) -> dict[str, Any]:
        batch = await self.store.get_batch(batch_id)
        target = next((item for item in batch["items"] if item["id"] == item_id), None)
        is_completed_retry = bool(target and target["status"] == "completed")
        if from_stage is not None:
            from_stage = CHECKPOINT_STAGE_IDS.get(from_stage, from_stage)
            folder = target.get("resultFolder") if target else None
            if not isinstance(folder, str) or Path(folder).name != folder:
                raise InvalidBatch("Cannot retry from a stage: saved result is unavailable")
            run = PipelineRun.get_or_load(self.result_root, folder)
            if run is None:
                database = getattr(self.store, "database", None)
                documents = await database.get_documents(folder) if database is not None else {}
                manifest_path = self.result_root / folder / "pipeline_manifest.json"
                if "pipeline_manifest.json" not in documents and manifest_path.is_file():
                    try:
                        documents["pipeline_manifest.json"] = await asyncio.to_thread(
                            lambda: json.loads(manifest_path.read_text(encoding="utf-8"))
                        )
                    except (OSError, json.JSONDecodeError):
                        pass
                run = PipelineRun.from_documents(self.result_root, folder, documents)
            valid_stages = {
                stage.get("id")
                for stage in (getattr(run, "manifest", {}) or {}).get("stages", [])
                if stage.get("id") != "input"
            }
            if from_stage not in valid_stages:
                raise InvalidBatch("Cannot retry from the requested stage: checkpoint unavailable")
        if is_completed_retry and from_stage is None:
            folder = target.get("resultFolder")
            source = find_asset(self.result_root / folder, "input") if isinstance(folder, str) else None
            if source is None or not source.is_file():
                raise InvalidBatch("Cannot retry page: saved input is unavailable")
            destination = self.store._input_path(batch_id, target)
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

        result = await self.store.mutate(batch_id, mutate)
        self._wake.set()
        return result

    async def update_item(self, batch_id: str, item_id: str, exclude_color: bool) -> dict[str, Any]:
        def mutate(manifest: dict[str, Any]):
            for item in manifest.get("items", []):
                if item.get("id") == item_id and item.get("status") == "queued":
                    item["excludeColor"] = exclude_color
                    return True
            return False

        return await self.store.mutate(batch_id, mutate)

    async def remove_item(self, batch_id: str, item_id: str) -> dict[str, Any]:
        input_path = None
        removed = False
        removed_group_id = None
        try:
            input_path = await self.store.input_path(batch_id, item_id)
        except BatchNotFound:
            pass

        def mutate(manifest: dict[str, Any]):
            nonlocal removed, removed_group_id
            items = manifest.get("items", [])
            target = next((item for item in items if item.get("id") == item_id), None)
            if not target or target.get("status") != "error":
                return False
            removed_group_id = target.get("mangaGroupId")
            items.remove(target)
            manifest["totalItems"] = max(
                int(manifest.get("completedCount", 0)), int(manifest.get("totalItems", len(items))) - 1
            )
            statuses = {item.get("status") for item in items}
            if manifest.get("status") == "paused" and "queued" in statuses:
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

        result = await self.store.mutate(batch_id, mutate)
        if removed and input_path:
            input_path.unlink(missing_ok=True)
        if removed and removed_group_id and getattr(self.store, "database", None) is not None:
            await self.store.database.compact_page_order(removed_group_id)
        self._wake.set()
        return result

    async def remove_batch(self, batch_id: str) -> None:
        existing = self._remove_tasks.get(batch_id)
        if existing:
            await existing
            return

        async def drain_and_delete():
            self._stopping_batches.add(batch_id)
            try:
                try:
                    await self.store.mutate(batch_id, lambda manifest: self._set_batch_status(manifest, "stopping"))
                except BatchNotFound:
                    return
                tasks = [task for (running_batch_id, _), task in self._running.items() if running_batch_id == batch_id]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                await self.store.delete_batch(batch_id)
            finally:
                self._stopping_batches.discard(batch_id)
                self._wake.set()

        task = asyncio.create_task(drain_and_delete(), name=f"remove-batch-{batch_id}")
        self._remove_tasks[batch_id] = task
        try:
            await task
        finally:
            self._remove_tasks.pop(batch_id, None)
