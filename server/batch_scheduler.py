"""Persistent batch scheduler using the server's existing executor pool."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from manga_translator import Config
from manga_translator.config import PipelineLabConfig
from manga_translator import Config, Context
from manga_translator.config import PipelineLabConfig
from manga_translator.pipeline_lab import PipelineLabRun, deserialize_textblocks
from server.batch_store import BatchNotFound, BatchStore, InvalidBatch
from server.logger import correlation_id_ctx, get_logger
from server.image_variants import final_file
from manga_translator.utils.image_storage import find_asset, save_jpeg

logger = get_logger("batch_scheduler")

# Maximum number of professional-mode page renders that may run concurrently.
# Each concurrent render holds a decoded PIL image, mask arrays, and a canvas,
# so keeping this small is the primary guard against memory spikes.
_RENDER_SEMAPHORE_SIZE = 2


class BatchScheduler:
    def __init__(self, store: BatchStore, executors: Any, result_root: str | Path):
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
        self._closed = False

    def wake(self) -> None:
        self._wake.set()

    async def start(self) -> None:
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
        self, batch_id: str, items: list[dict[str, Any]], size: int
    ) -> list[dict[str, Any]] | None:
        uncompleted = [
            item for item in items
            if item.get("status") not in {"completed", "error"} and item.get("id")
        ]
        if not uncompleted:
            return None

        first = uncompleted[0]
        first_id = first.get("id")
        if not first_id or (batch_id, first_id) in self._running_items:
            return None
        if first.get("stage") != "awaiting_translation":
            return None

        ready_group: list[dict[str, Any]] = []
        for item in uncompleted:
            item_id = item.get("id")
            if not item_id or (batch_id, item_id) in self._running_items:
                break
            if item.get("stage") != "awaiting_translation":
                break
            ready_group.append(item)

        if len(ready_group) >= size:
            return ready_group[:size]

        if len(ready_group) == len(uncompleted):
            return ready_group

        return None

    def _find_next_queued_item(
        self, batch_id: str, items: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        for item in items:
            item_id = item.get("id")
            if not item_id:
                continue
            if (batch_id, item_id) in self._running_items:
                continue
            if item.get("status") == "queued" and item.get("stage") not in {"awaiting_translation", "reserved"}:
                return item
        return None

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

            if batch.get("kind") == "rerender":
                next_queued = self._find_next_queued_item(batch_id, items)
                if next_queued:
                    instance = await self.executors.find_executor()
                    claimed_item = await self._claim_rerender_item(batch_id, next_queued["id"])
                    if not claimed_item:
                        await self.executors.free_executor(instance)
                        continue
                    item_id = claimed_item["id"]
                    self._running_items.add((batch_id, item_id))
                    task = asyncio.create_task(
                        self._process_rerender_item(batch_id, item_id, instance),
                        name=f"batch-{batch_id[:8]}-rerender-{item_id[:8]}",
                    )
                    running_key = (batch_id, f"rerender:{item_id}")
                    self._running[running_key] = task
                    def on_rerender_done(_, key=running_key, b_id=batch_id, i_id=item_id):
                        self._running.pop(key, None)
                        self._running_items.discard((b_id, i_id))
                    task.add_done_callback(on_rerender_done)
                    return True
                continue

            settings = batch.get("settings", {})
            if settings.get("translationQuality") == "professional":
                size = len([
                    item for item in items
                    if item.get("status") not in {"completed", "error"}
                ])
            else:
                size = max(1, min(100, int(settings.get("translationBatchSize", 20))))

            ready_group = self._find_ready_translation_group(batch_id, items, size)
            if ready_group:
                instance = await self.executors.find_executor()
                claimed = await self._claim_translation_group(batch_id, ready_group)
                if not claimed:
                    await self.executors.free_executor(instance)
                    continue
                item_ids = [item["id"] for item in claimed]
                for item_id in item_ids:
                    self._running_items.add((batch_id, item_id))
                task = asyncio.create_task(
                    self._process_translation_group(batch_id, claimed, instance),
                    name=f"batch-{batch_id[:8]}-trans-{item_ids[0][:8]}",
                )
                running_key = (batch_id, f"trans:{item_ids[0]}")
                self._running[running_key] = task
                def on_trans_done(_, key=running_key, b_id=batch_id, ids=item_ids):
                    self._running.pop(key, None)
                    for i_id in ids:
                        self._running_items.discard((b_id, i_id))
                task.add_done_callback(on_trans_done)
                return True

            next_queued = self._find_next_queued_item(batch_id, items)
            if next_queued:
                instance = await self.executors.find_executor()
                claimed_item = await self._claim_prepare_item(batch_id, next_queued["id"])
                if not claimed_item:
                    await self.executors.free_executor(instance)
                    continue
                item_id = claimed_item["id"]
                self._running_items.add((batch_id, item_id))
                task = asyncio.create_task(
                    self._process_prepare_item(batch_id, item_id, instance),
                    name=f"batch-{batch_id[:8]}-prep-{item_id[:8]}",
                )
                running_key = (batch_id, f"prep:{item_id}")
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
                    item["status"] = "processing"
                    item["stage"] = "starting"
                    item["settings"] = dict(manifest.get("settings", {}))
                    claimed = dict(item)
                    manifest["status"] = "processing"
                    return True
            return False

        await self.store.mutate(batch_id, mutate)
        return claimed

    async def _claim_rerender_item(self, batch_id: str, item_id: str) -> dict[str, Any] | None:
        claimed: dict[str, Any] | None = None

        def mutate(manifest: dict[str, Any]):
            nonlocal claimed
            if manifest.get("status") == "paused" or batch_id in self._stopping_batches:
                return False
            for item in manifest.get("items", []):
                if item.get("id") == item_id and item.get("status") == "queued":
                    item.update(status="processing", stage="rendering")
                    claimed = dict(item)
                    manifest["status"] = "processing"
                    return True
            return False

        await self.store.mutate(batch_id, mutate)
        return claimed

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
                item["stage"] = "reserved"
                item["settings"] = dict(manifest.get("settings", {}))
                claimed.append(dict(item))
                if len(claimed) == limit:
                    break
            if claimed:
                claimed_id = claimed[0]["id"]
                for item in manifest.get("items", []):
                    if item.get("id") == claimed_id:
                        item.update(status="processing", stage="starting")
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
                    item["stage"] = "starting"
                    item["settings"] = dict(manifest.get("settings", {}))
                    claimed = dict(item)
                    manifest["status"] = "processing"
                    return True
            return False

        await self.store.mutate(batch_id, mutate)
        return claimed

    async def _process_prepare_item(self, batch_id: str, item_id: str, instance: Any) -> None:
        hook = None
        image = None
        folder = None
        short_b = batch_id[:8] if len(batch_id) > 8 else batch_id
        short_i = item_id[:8] if len(item_id) > 8 else item_id
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

    async def _process_rerender_item(self, batch_id: str, item_id: str, instance: Any) -> None:
        token = correlation_id_ctx.set(f"batch-{batch_id[:8]}/rerender-{item_id[:8]}")
        staged_final: Path | None = None
        staged_regions: Path | None = None
        try:
            batch = await self.store.get_batch(batch_id)
            item = next(item for item in batch["items"] if item["id"] == item_id)
            folder = item.get("resultFolder")
            if not isinstance(folder, str) or Path(folder).name != folder:
                raise RuntimeError("Rerender item has no valid result folder")
            result_dir = (self.result_root / folder).resolve()
            if result_dir.parent != self.result_root or not result_dir.is_dir():
                raise RuntimeError("Result folder is unavailable")

            regions_path = result_dir / "text_regions.json"
            regions = None
            if regions_path.is_file():
                regions = json.loads(regions_path.read_text("utf-8"))
            database = getattr(self.store, "database", None)
            if regions is None and database is not None:
                documents = await database.get_documents(folder)
                regions = documents.get("text_regions.json")
                if regions is None:
                    regions = await database.get_text_regions(folder)
            if not isinstance(regions, list) or not regions:
                raise RuntimeError("Saved translated text regions are unavailable")

            inpainted_path = find_asset(result_dir, "inpainted")
            final_path = final_file(result_dir)
            if inpainted_path is None or final_path is None:
                raise RuntimeError("Saved inpainted and final images are required")

            saved_settings = item.get("settings") if isinstance(item.get("settings"), dict) else {}
            config_item = {**item, "settings": saved_settings}
            config = self._config_for(batch, config_item)
            config.bubble_detection.enabled = False
            config.translator.translator = "none"
            config.translator.translation_quality = "fast"
            config.upscale.upscale_ratio = None
            config.upscale.revert_upscaling = False

            with Image.open(inpainted_path) as opened:
                inpainted = np.array(opened.convert("RGB"))

            original_path = find_asset(result_dir, "original_canvas") or find_asset(result_dir, "input")
            if original_path is not None:
                with Image.open(original_path) as opened:
                    orig_img = np.array(opened.convert("RGB"))
            else:
                orig_img = inpainted.copy()

            ctx = Context()
            ctx.debug_folder = folder
            ctx.image_context = {
                "subfolder": folder,
                "file_md5": folder.split("-")[-1] if "-" in folder else folder,
                "request_id": item.get("requestId"),
            }
            ctx.img_rgb = orig_img.copy()
            ctx.img_inpainted = inpainted.copy()

            inpaint_mask_path = result_dir / "inpaint_mask.png"
            mask_final_path = result_dir / "mask_final.png"
            if inpaint_mask_path.is_file():
                ctx.inpaint_mask = cv2.imread(str(inpaint_mask_path), cv2.IMREAD_GRAYSCALE)
            elif mask_final_path.is_file():
                ctx.inpaint_mask = cv2.imread(str(mask_final_path), cv2.IMREAD_GRAYSCALE)
            else:
                ctx.inpaint_mask = None
            ctx.mask = ctx.inpaint_mask

            bubble_mask_path = result_dir / "bubble_mask.png"
            if bubble_mask_path.is_file():
                ctx.bubble_mask = cv2.imread(str(bubble_mask_path), cv2.IMREAD_GRAYSCALE)
            else:
                ctx.bubble_mask = None

            bubble_detections_path = result_dir / "bubble_detections.json"
            if bubble_detections_path.is_file():
                try:
                    from manga_translator.detection.bubble import deserialize_bubble_detections
                    bds_raw = json.loads(bubble_detections_path.read_text("utf-8"))
                    ctx.bubble_detections = deserialize_bubble_detections(bds_raw, orig_img.shape)
                    ctx._bubble_detection_done = True
                except Exception:
                    pass

            ctx.text_regions = deserialize_textblocks(regions)
            for region in ctx.text_regions:
                if not getattr(region, "target_lang", None):
                    region.target_lang = config.translator.target_lang

            if not hasattr(instance, "render_saved"):
                raise RuntimeError("Executor instance does not support saved rendering")
            rendered_ctx = await instance.render_saved(ctx, config)
            error = getattr(rendered_ctx, "translation_error", None)
            rendered = getattr(rendered_ctx, "result", None)
            if error or rendered is None:
                raise RuntimeError(error or "Saved rendering produced no image")

            from manga_translator.pipeline_lab import serialize_editor_regions

            updated_regions = serialize_editor_regions(rendered_ctx.text_regions)
            staged_final = result_dir / f".{final_path.name}.rerender"
            staged_regions = result_dir / ".text_regions.json.rerender"
            if final_path.suffix.lower() in {".jpg", ".jpeg"}:
                save_jpeg(rendered, staged_final)
            else:
                rendered.save(staged_final, format="PNG")
            staged_regions.write_text(
                json.dumps(updated_regions, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            if database is not None:
                await database.save_documents(folder, {"text_regions.json": updated_regions})
            os.replace(staged_final, final_path)
            if database is None:
                os.replace(staged_regions, regions_path)

            for variant in ("batch.webp", "cover.webp", "preview.webp", "reader.webp"):
                (result_dir / variant).unlink(missing_ok=True)

            index_result = getattr(self.store, "register_result", None)
            if index_result is not None:
                await index_result(
                    folder,
                    page_order=item.get("pageOrder"),
                    page_id=item.get("pageId"),
                )

            needs_review = any(
                bool(region.get("review_required"))
                for region in updated_regions
                if isinstance(region, dict)
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
            logger.error("Error rerendering batch item %s: %s", item_id, exc)

            def fail(manifest: dict[str, Any]):
                for entry in manifest.get("items", []):
                    if entry.get("id") == item_id:
                        entry.update(status="error", stage="rendering", error=str(exc))
                if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                    manifest["status"] = "error"
                return True

            await self.store.mutate(batch_id, fail)
        finally:
            if staged_final is not None:
                staged_final.unlink(missing_ok=True)
            if staged_regions is not None:
                staged_regions.unlink(missing_ok=True)
            self._running_items.discard((batch_id, item_id))
            await self.executors.free_executor(instance)
            self._wake.set()
            correlation_id_ctx.reset(token)

    async def _process_translation_group(
        self, batch_id: str, claimed: list[dict[str, Any]], instance: Any
    ) -> None:
        item_ids = [item["id"] for item in claimed]
        images: list[Image.Image] = []
        hook = None
        owns_translation_lock = False
        lock = self._translation_locks.setdefault(batch_id, asyncio.Lock())
        main_loop = asyncio.get_running_loop()
        token = correlation_id_ctx.set(f"batch-{batch_id[:8]}/trans-{item_ids[0][:8]}")
        logger.info(f"Starting batch translation for items {item_ids} (batch {batch_id})")
        try:
            batch = await self.store.get_batch(batch_id)
            current = {item["id"]: item for item in batch["items"]}
            configs = [self._config_for(batch, current[item_id]) for item_id in item_ids]

            contexts_with_configs = []
            is_professional = any(
                self._config_for(batch, current[item_id]).translator.translation_quality == "professional"
                for item_id in item_ids
            )
            for item_id, config in zip(item_ids, configs):
                item_entry = current[item_id]
                folder = item_entry.get("resultFolder")
                result_dir = self.result_root / folder if isinstance(folder, str) else None

                merged_data = None
                if result_dir and (result_dir / "text_regions_merged.json").is_file():
                    try:
                        merged_data = json.loads((result_dir / "text_regions_merged.json").read_text("utf-8"))
                    except Exception:
                        pass
                elif getattr(self.store, "database", None) is not None and folder:
                    docs = await self.store.database.get_documents(folder)
                    merged_data = docs.get("text_regions_merged.json")

                ctx = Context()
                if merged_data:
                    ctx.text_regions = deserialize_textblocks(merged_data)
                    ctx.result_documents = {"text_regions_merged.json": merged_data}
                else:
                    ctx.text_regions = []

                input_path = await self.store.input_path(batch_id, item_id)
                if input_path.is_file():
                    if is_professional:
                        # Defer image loading to render time so that only one page's
                        # decoded pixels exist in memory at a time during professional
                        # translation (which never accesses ctx.input).
                        ctx._deferred_image_path = input_path
                    else:
                        with Image.open(input_path) as opened:
                            img = opened.convert("RGB")
                            images.append(img)
                            ctx.input = img

                if folder:
                    ctx.debug_folder = folder
                    ctx.image_context = {
                        "subfolder": folder,
                        "file_md5": folder.split("-")[-1] if "-" in folder else folder,
                        "request_id": item_entry.get("requestId"),
                    }
                contexts_with_configs.append((ctx, config))

            translator = getattr(instance, "translator", None)
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

            translated_pairs = None
            if hasattr(instance, "translate_batch_contexts"):
                translated_pairs = await instance.translate_batch_contexts(
                    contexts_with_configs, batch_size=len(contexts_with_configs)
                )
            elif hasattr(instance, "translate_and_render_batch"):
                contexts = await instance.translate_and_render_batch(
                    contexts_with_configs, batch_size=len(contexts_with_configs)
                )
            elif hasattr(instance, "sent_batch"):
                batch_images = [ctx.input for ctx, _ in contexts_with_configs]
                contexts = await instance.sent_batch(batch_images, configs, len(batch_images))
            else:
                raise RuntimeError("Executor instance does not support translate_batch_contexts, translate_and_render_batch, or sent_batch")

            if translated_pairs is not None:
                if hook is not None and translator is not None and hook in translator._progress_hooks:
                    translator._progress_hooks.remove(hook)
                    hook = None
                if owns_translation_lock:
                    lock.release()
                    owns_translation_lock = False
                await self.executors.free_executor(instance)
                instance = None
                self._wake.set()

                async def _render_item(item_id: str, config: Config, ctx: Context) -> None:
                    worker = await self.executors.find_executor()
                    worker_hook = None
                    worker_translator = getattr(worker, "translator", None)
                    try:
                        await self._set_stage(batch_id, item_id, "rendering")
                        if worker_translator is not None and hasattr(worker_translator, "add_progress_hook"):
                            async def w_progress(state: str, _finished: bool):
                                if state.startswith(("debug_folder:", "final_ready:", "rendering_folder:", "offline_model:", "gemini_model:")) or state == "after-translating":
                                    return
                                future = asyncio.run_coroutine_threadsafe(
                                    self._set_stage(batch_id, item_id, state), main_loop
                                )
                                await asyncio.wrap_future(future)
                            worker_hook = w_progress
                            worker_translator.add_progress_hook(worker_hook)

                        # Load the deferred image just before rendering so that only
                        # one page's decoded pixels exist in memory at a time.
                        deferred_path = getattr(ctx, "_deferred_image_path", None)
                        if deferred_path is not None and ctx.input is None:
                            with Image.open(deferred_path) as _img:
                                ctx.input = _img.convert("RGB")

                        if hasattr(worker, "render"):
                            rendered_ctx = await worker.render(ctx, config)
                        elif hasattr(worker, "translate_and_render_batch"):
                            rendered_contexts = await worker.translate_and_render_batch([(ctx, config)], batch_size=1)
                            rendered_ctx = rendered_contexts[0] if rendered_contexts else ctx
                        else:
                            rendered_ctx = await worker.sent(ctx.input, config)

                        # Release the decoded image immediately — the result image is
                        # already persisted to disk inside render/translate_and_render_batch.
                        if deferred_path is not None:
                            ctx.input = None

                        folder = getattr(rendered_ctx, "debug_folder", None)
                        result_folder = self.result_root / folder if isinstance(folder, str) else None
                        error = getattr(rendered_ctx, "translation_error", None)
                        if error or not result_folder or final_file(result_folder) is None:
                            def fail_item(manifest: dict[str, Any]):
                                for entry in manifest.get("items", []):
                                    if entry.get("id") == item_id:
                                        entry.update(
                                            status="error",
                                            stage="rendering",
                                            error=error or "Rendering produced no final image",
                                            resultFolder=folder,
                                        )
                                return True
                            await self.store.mutate(batch_id, fail_item)
                            return

                        index_result = getattr(self.store, "register_result", None)
                        if index_result is not None:
                            await index_result(folder, page_order=config.page_order, page_id=current[item_id].get("pageId"))

                        def complete_item(manifest: dict[str, Any]):
                            for entry in manifest.get("items", []):
                                if entry.get("id") == item_id:
                                    entry.update(
                                        status="completed",
                                        stage="finished",
                                        error=None,
                                        resultFolder=folder,
                                        needsReview=bool(getattr(rendered_ctx, "manual_review_required", False)),
                                    )
                            return True
                        await self.store.mutate(batch_id, complete_item)
                        (await self.store.input_path(batch_id, item_id)).unlink(missing_ok=True)
                    except Exception as exc:
                        logger.error(f"Error rendering batch item {item_id}: {exc}")
                        if getattr(ctx, "_deferred_image_path", None) is not None:
                            ctx.input = None
                        def fail_item(manifest: dict[str, Any]):
                            for entry in manifest.get("items", []):
                                if entry.get("id") == item_id:
                                    entry.update(status="error", stage="rendering", error=str(exc))
                            return True
                        await self.store.mutate(batch_id, fail_item)
                    finally:
                        if worker_hook is not None and worker_translator is not None and worker_hook in worker_translator._progress_hooks:
                            worker_translator._progress_hooks.remove(worker_hook)
                        self._running_items.discard((batch_id, item_id))
                        await self.executors.free_executor(worker)
                        self._wake.set()

                # Limit concurrent renders so at most _RENDER_SEMAPHORE_SIZE pages'
                # decoded pixels, masks, and canvases exist simultaneously.
                _render_sem = asyncio.Semaphore(_RENDER_SEMAPHORE_SIZE)

                async def _bounded_render(i_id: str, cfg: Config, ctx_pair: tuple) -> None:
                    async with _render_sem:
                        await _render_item(i_id, cfg, ctx_pair[0])

                await asyncio.gather(*(
                    _bounded_render(i_id, cfg, ctx_pair)
                    for i_id, cfg, ctx_pair in zip(item_ids, configs, translated_pairs)
                ))

            else:
                for item_id, config, context in zip(item_ids, configs, contexts):
                    folder = getattr(context, "debug_folder", None)
                    result_folder = self.result_root / folder if isinstance(folder, str) else None
                    error = getattr(context, "translation_error", None)
                    if error or not result_folder or final_file(result_folder) is None:
                        def fail_item(manifest: dict[str, Any], item_id=item_id, error=error, folder=folder):
                            for entry in manifest.get("items", []):
                                if entry.get("id") == item_id:
                                    entry.update(status="error", stage="translation", error=error or "Translation produced no final image", resultFolder=folder)
                            return True
                        await self.store.mutate(batch_id, fail_item)
                        continue

                    index_result = getattr(self.store, "register_result", None)
                    if index_result is not None:
                        await index_result(folder, page_order=config.page_order, page_id=current[item_id].get("pageId"))

                    def complete_item(manifest: dict[str, Any], item_id=item_id, folder=folder, context=context):
                        for entry in manifest.get("items", []):
                            if entry.get("id") == item_id:
                                entry.update(
                                    status="completed", stage="finished", error=None,
                                    resultFolder=folder,
                                    needsReview=bool(getattr(context, "manual_review_required", False)),
                                )
                        return True

                    await self.store.mutate(batch_id, complete_item)
                    (await self.store.input_path(batch_id, item_id)).unlink(missing_ok=True)

            batch_finished = False
            def finish_batch(manifest: dict[str, Any]):
                nonlocal batch_finished
                pending = any(item.get("status") in {"queued", "processing"} for item in manifest.get("items", []))
                if not pending:
                    manifest["status"] = "error" if any(item.get("status") == "error" for item in manifest.get("items", [])) else "completed"
                    batch_finished = True
                return True
            await self.store.mutate(batch_id, finish_batch)

            reclaim_memory = getattr(instance, "reclaim_memory", None)
            if batch_finished and reclaim_memory is not None:
                try:
                    await reclaim_memory()
                except Exception as exc:
                    logger.warning(f"Failed to reclaim memory after batch {batch_id}: {exc}")
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
            if instance is not None:
                translator = getattr(instance, "translator", None)
                if hook is not None and translator is not None and hook in translator._progress_hooks:
                    translator._progress_hooks.remove(hook)
                await self.executors.free_executor(instance)
            if owns_translation_lock:
                lock.release()
            for image in images:
                image.close()
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
                    item.update(status="processing", stage=stage)
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
                    item.update(status="processing", stage=stage)
                elif item.get("stage") == "awaiting_translation":
                    item.update(status="processing", stage="awaiting_translation")
                elif item.get("status") == "processing":
                    item.update(status="queued", stage="reserved")
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
                    "ocr": settings.get("ocr", "48px"),
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
                    "gimp_font": settings.get("renderFont", "Sans-serif"),
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
        # Keep stage artifacts so translation failures can resume without rerunning earlier stages.
        config.pipeline_lab = PipelineLabConfig(enabled=True, stage_plan={}, manual=False)
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
                item["stage"] = stage
                return True
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
        checkpoint_folder = None
        checkpoint_run = None
        short_b = batch_id[:8] if len(batch_id) > 8 else batch_id
        short_i = item_id[:8] if len(item_id) > 8 else item_id
        token = correlation_id_ctx.set(f"batch-{short_b}/item-{short_i}")
        logger.info(f"Starting batch item {item_id} (batch {batch_id})")
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
                run = PipelineLabRun.get_or_load(self.result_root, checkpoint_folder)
                if run is None and getattr(self.store, "database", None) is not None:
                    documents = await self.store.database.get_documents(checkpoint_folder)
                    run = PipelineLabRun.from_documents(
                        self.result_root, checkpoint_folder, documents
                    )
            checkpoint_run = run
            folder = checkpoint_folder
            failed_stage = next(
                (
                    stage["id"]
                    for stage in (getattr(run, "manifest", {}) or {}).get("stages", [])
                    if stage.get("status") in {"failed", "running"}
                ),
                None,
            )
            if run is not None and failed_stage == "translation" and hasattr(instance, "_run_translation"):
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
            input_path.unlink(missing_ok=True)
            logger.info(f"Completed batch item {item_id} (folder: {folder})")
        except asyncio.CancelledError:
            logger.warning(f"Cancelled batch item {item_id}")
            raise
        except Exception as exc:
            logger.error(f"Error processing batch item {item_id}: {exc}")
            try:
                if not checkpoint_folder:
                    active_run = getattr(getattr(instance, "translator", None), "_pipeline_lab_run", None)
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
            except BatchNotFound:
                pass
        finally:
            translator = getattr(instance, "translator", None)
            if hook is not None and translator is not None and hook in translator._progress_hooks:
                translator._progress_hooks.remove(hook)
            image = context = config = None
            reclaim_memory = getattr(instance, "reclaim_memory", None)
            if batch_finished and reclaim_memory is not None:
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
    ) -> dict[str, Any]:
        batch = await self.store.get_batch(batch_id)
        target = next((item for item in batch["items"] if item["id"] == item_id), None)
        is_completed_retry = bool(target and target["status"] == "completed")
        if is_completed_retry:
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
                    if was_dismissed:
                        # Dismissal compacts the group; retrying is a new append.
                        item["pageOrder"] = None
                        manifest["dismissed"] = False
                    if item.get("resultFolder") and is_completed_retry:
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
