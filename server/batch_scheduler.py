"""Persistent batch scheduler using the server's existing executor pool."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from manga_translator import Config, Context
from manga_translator.config import Detector, Inpainter, Ocr
from manga_translator.pipeline.run import (
    CHECKPOINT_STAGE_IDS,
    PipelineRun,
    deserialize_textblocks,
    deserialize_textlines,
    serialize_regions,
)
from manga_translator.pipeline.stages import PipelineStage, ResourceClass, fingerprint, settings_for_stage, stage_from_progress
from manga_translator.rendering import resolve_font_name_or_path
from server.batch_store import BatchNotFound, BatchStore, InvalidBatch
from server.batch_stage_executor import (
    process_checkpointed_model_group,
    process_checkpointed_ocr_group,
    process_prepare_item,
)
from server.batch_pipeline_rerun import process_pipeline_rerun_item
from server.batch_item_runner import process_item
from server.batch_checkpointed_prepare import (
    complete_checkpointed_textless,
    process_checkpointed_prepare_item,
)
from server.batch_translation_group import process_translation_group
from server.batch_config import config_for
from server.batch_claims import (
    claim_item,
    claim_items,
    claim_pipeline_rerun_item,
    claim_prepare_item,
    claim_prepare_items,
    claim_translation_group,
)
from server.batch_resources import (
    BatchResourceManager,
    MODEL_EXECUTOR_CONCURRENCY,
    _stage_resource,
    stage_resource_limits,
)
from server.batch_memory_limits import inference_page_limit
from server.batch_inference_groups import find_ocr_group, find_page_inference_group
from server.batch_dispatch import launch_available, log_token as _log_token
from server.batch_group_selection import (
    _BATCH_STAGE_ALIASES,
    _BATCH_STAGE_ORDER,
    _PRE_TRANSLATION_STAGES,
    current_batch_stage,
    find_next_queued_item,
    find_ready_translation_group,
    item_batch_stage,
    story_plan_for_group,
)
from server.batch_stage_progression import next_batch_stage, next_prepare_stage
from server import batch_mutations
from server.logger import correlation_id_ctx, get_logger
from server.image_variants import final_file
from manga_translator.utils.image_storage import find_asset, save_jpeg

logger = get_logger("batch_scheduler")

_INFERENCE_PAGE_LIMITS = {
    "detection": 3,
    "ocr": 3,
    "bubble_detection": 3,
    "inpainting": 2,
    "upscaling": 2,
}
def _set_item_stage(item: dict[str, Any], stage: str) -> bool:
    if item.get("stage") == stage and item.get("stageStartedAt") is not None:
        return False
    item["stage"] = stage
    item["stageStartedAt"] = int(time.time() * 1000)
    return True


class BatchScheduler:
    def __init__(
        self,
        store: BatchStore,
        executors: Any,
        result_root: str | Path,
        resource_limits: dict[ResourceClass, int] | None = None,
        inference_page_batch_size: int = 2,
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
        self.inference_page_batch_size = max(1, inference_page_batch_size)
        self._resource_manager = BatchResourceManager(
            resource_limits,
            logger=logger,
            correlation_id=correlation_id_ctx,
        )
        self.resource_limits = self._resource_manager.resource_limits
        self._resource_slots = self._resource_manager.slots
        self._closed = False

    def _inference_page_limit(self, stage_id: str) -> int:
        return inference_page_limit(
            stage_id,
            self.inference_page_batch_size,
            _INFERENCE_PAGE_LIMITS,
            self.executors,
        )

    def wake(self) -> None:
        self._wake.set()

    async def _reclaim_batch_memory(self, batch_id: str, instance: Any) -> None:
        translator = getattr(instance, "translator", None)
        if translator is not None and hasattr(translator, "clear_batch_state"):
            translator.clear_batch_state(batch_id)
        reclaim_memory = getattr(instance, "reclaim_memory", None)
        if reclaim_memory is not None:
            try:
                await reclaim_memory()
            except Exception as exc:
                logger.warning("Failed to reclaim memory after batch %s: %s", batch_id, exc)

    async def _acquire_resource(
        self, stage_id: str, resource: ResourceClass
    ) -> asyncio.Semaphore:
        return await self._resource_manager.acquire(stage_id, resource)

    async def _acquire_stage_resource(self, stage_id: str) -> asyncio.Semaphore:
        return await self._resource_manager.acquire_stage(stage_id)

    def _release_stage_resource(
        self, stage_id: str, slot: asyncio.Semaphore, resource: ResourceClass | None = None
    ) -> None:
        self._resource_manager.release_stage(stage_id, slot, resource)

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
        return find_ready_translation_group(self, batch_id, items, size, settings)

    @staticmethod
    def _story_plan_for_group(
        story_plan: Any,
        items: list[dict[str, Any]],
        item_ids: list[str],
    ) -> Any:
        return story_plan_for_group(story_plan, items, item_ids)

    def _find_next_queued_item(
        self, batch_id: str, items: list[dict[str, Any]], stage_id: str | None = None
    ) -> dict[str, Any] | None:
        return find_next_queued_item(self, batch_id, items, stage_id)

    @staticmethod
    def _item_batch_stage(item: dict[str, Any]) -> str:
        return item_batch_stage(item)

    @classmethod
    def _current_batch_stage(cls, items: list[dict[str, Any]]) -> str | None:
        return current_batch_stage(cls, items)

    def _find_ocr_group(self, batch: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return find_ocr_group(self, batch, items)

    def _find_page_inference_group(
        self, batch: dict[str, Any], items: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]] | None:
        return find_page_inference_group(self, batch, items)

    async def _claim_prepare_items(
        self, batch_id: str, group: list[dict[str, Any]], stage_id: str = "ocr"
    ) -> list[dict[str, Any]]:
        return await claim_prepare_items(self, batch_id, group, stage_id)

    async def _launch_available(self) -> bool:
        return await launch_available(self)

    async def _claim_prepare_item(self, batch_id: str, item_id: str) -> dict[str, Any] | None:
        return await claim_prepare_item(self, batch_id, item_id)

    async def _claim_pipeline_rerun_item(self, batch_id: str, item_id: str) -> dict[str, Any] | None:
        return await claim_pipeline_rerun_item(self, batch_id, item_id)

    async def _claim_rerender_item(self, batch_id: str, item_id: str) -> dict[str, Any] | None:
        return await self._claim_pipeline_rerun_item(batch_id, item_id)


    async def _claim_translation_group(
        self, batch_id: str, group_items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return await claim_translation_group(self, batch_id, group_items)

    async def _claim_items(self, batch_id: str, limit: int) -> list[dict[str, Any]]:
        return await claim_items(self, batch_id, limit, set_item_stage=_set_item_stage)

    async def _claim_item(self, batch_id: str) -> dict[str, Any] | None:
        return await claim_item(self, batch_id, set_item_stage=_set_item_stage)

    async def _process_prepare_item(self, batch_id: str, item_id: str, instance: Any) -> None:
        await process_prepare_item(
            self,
            batch_id,
            item_id,
            instance,
            log_token=_log_token,
            correlation_id_ctx=correlation_id_ctx,
            logger=logger,
        )

    @staticmethod
    def _next_prepare_stage(run: PipelineRun) -> str | None:
        return next_prepare_stage(run)

    @staticmethod
    def _next_batch_stage(run: PipelineRun) -> str | None:
        return next_batch_stage(run)

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
        await complete_checkpointed_textless(
            self, batch_id, item, folder, stage_id, config, run, instance
        )

    async def _process_checkpointed_ocr_group(
        self, batch_id: str, claimed: list[dict[str, Any]], instance: Any
    ) -> None:
        return await process_checkpointed_ocr_group(
            self,
            batch_id,
            claimed,
            instance,
            log_token=_log_token,
            correlation_id_ctx=correlation_id_ctx,
            logger=logger,
            deserialize_textlines=deserialize_textlines,
        )

    async def _process_checkpointed_model_group(
        self,
        batch_id: str,
        claimed: list[dict[str, Any]],
        instance: Any,
        stage_id: str,
    ) -> None:
        return await process_checkpointed_model_group(
            self,
            batch_id,
            claimed,
            instance,
            stage_id,
            log_token=_log_token,
            correlation_id_ctx=correlation_id_ctx,
            logger=logger,
            set_item_stage=_set_item_stage,
            deserialize_textblocks=deserialize_textblocks,
        )

    async def _process_checkpointed_prepare_item(
        self, batch_id: str, item_id: str, instance: Any
    ) -> None:
        await process_checkpointed_prepare_item(
            self,
            batch_id,
            item_id,
            instance,
            log_token=_log_token,
            set_item_stage=_set_item_stage,
            correlation_id_ctx=correlation_id_ctx,
            logger=logger,
        )

    async def _process_pipeline_rerun_item(self, batch_id: str, item_id: str, instance: Any) -> None:
        await process_pipeline_rerun_item(
            self,
            batch_id,
            item_id,
            instance,
            log_token=_log_token,
            set_item_stage=_set_item_stage,
            correlation_id_ctx=correlation_id_ctx,
            logger=logger,
        )

    async def _process_rerender_item(self, batch_id: str, item_id: str, instance: Any) -> None:
        return await self._process_pipeline_rerun_item(batch_id, item_id, instance)


    async def _process_translation_group(
        self, batch_id: str, claimed: list[dict[str, Any]], instance: Any
    ) -> None:
        await process_translation_group(
            self,
            batch_id,
            claimed,
            instance,
            log_token=_log_token,
            correlation_id_ctx=correlation_id_ctx,
            logger=logger,
        )

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
        return config_for(batch, item)

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
        await process_item(
            self,
            batch_id,
            item_id,
            instance,
            log_token=_log_token,
            correlation_id_ctx=correlation_id_ctx,
            logger=logger,
        )

    async def pause(self, batch_id: str) -> dict[str, Any]:
        return await batch_mutations.pause_batch(
            self.store, self._wake.set, batch_id, self._set_batch_status
        )

    async def resume(self, batch_id: str) -> dict[str, Any]:
        return await batch_mutations.resume_batch(
            self.store, self._wake.set, batch_id, self._set_batch_status
        )

    @staticmethod
    def _set_batch_status(manifest: dict[str, Any], status: str) -> bool:
        return batch_mutations.set_batch_status(manifest, status)

    async def dismiss(self, batch_id: str) -> dict[str, Any]:
        return await batch_mutations.dismiss_batch(
            self.store, batch_id, self._compact_batch_groups
        )

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
        return await batch_mutations.update_translator(
            self.store, self._wake.set, batch_id, translator
        )

    async def update_title(self, batch_id: str, title: str) -> dict[str, Any]:
        return await batch_mutations.update_title(
            self.store, self._wake.set, batch_id, title
        )

    async def update_priority(self, batch_id: str, priority: bool) -> dict[str, Any]:
        return await batch_mutations.update_priority(
            self.store, self._wake.set, batch_id, priority
        )

    async def update_manual_review(self, batch_id: str, enabled: bool) -> dict[str, Any]:
        return await batch_mutations.update_manual_review(
            self.store, self._wake.set, batch_id, enabled
        )

    async def retry_item(
        self,
        batch_id: str,
        item_id: str,
        keep_failed_pages_for_editing: bool | None = None,
        from_stage: str | None = None,
    ) -> dict[str, Any]:
        return await batch_mutations.retry_item(
            self, batch_id, item_id, keep_failed_pages_for_editing, from_stage
        )

    async def update_item(self, batch_id: str, item_id: str, exclude_color: bool) -> dict[str, Any]:
        return await batch_mutations.update_item(self, batch_id, item_id, exclude_color)

    async def remove_item(self, batch_id: str, item_id: str) -> dict[str, Any]:
        return await batch_mutations.remove_item(self, batch_id, item_id)

    async def remove_batch(self, batch_id: str) -> None:
        await batch_mutations.remove_batch(self, batch_id)
