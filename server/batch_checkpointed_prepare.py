"""Checkpointed prepare-stage lifecycle for the batch scheduler."""

import asyncio
import time
from pathlib import Path
from typing import Any

from PIL import Image

from manga_translator import Context
from manga_translator.config import Config
from manga_translator.pipeline.run import PipelineRun
from manga_translator.pipeline.stages import PipelineStage, ResourceClass
from manga_translator.utils.image_storage import save_jpeg
from server.batch_resources import _stage_resource
from server.image_variants import final_file


async def complete_checkpointed_textless(
    scheduler,
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
        run.write_json("meta.json", translator._build_result_metadata(config, ctx))
        run.progress("skip-no-regions" if stage_id == "detection" else "skip-no-text", True)
        run.manifest["status"] = "completed"
        run.manifest.pop("error", None)
        await run.checkpoint()
        run.release_runtime()

    await instance._run_translation(finalize)
    index_result = getattr(scheduler.store, "register_result", None)
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

    result = await scheduler.store.mutate(batch_id, complete)
    if not any(entry.get("status") in {"queued", "processing"} for entry in result.get("items", [])):
        await scheduler._reclaim_batch_memory(batch_id, instance)
    (await scheduler.store.input_path(batch_id, item["id"])).unlink(missing_ok=True)


async def process_checkpointed_prepare_item(
    scheduler, batch_id: str, item_id: str, instance: Any, *,
    log_token, set_item_stage, correlation_id_ctx, logger,
) -> None:
    image = None
    folder = None
    stage_id = None
    run = None
    batch_finished = False
    page_completed = False
    resource_slot = None
    resource_class = None
    resource_acquired = False
    token = correlation_id_ctx.set(f"batch-{log_token(batch_id)}/stage-{log_token(item_id)}")
    try:
        batch = await scheduler.store.get_batch(batch_id)
        item = next(item for item in batch["items"] if item["id"] == item_id)
        pipeline_stage = item.get("pipelineStage") or "initialize"
        translator = instance.translator
        if _stage_resource(pipeline_stage) == ResourceClass.GPU:
            resource_class = ResourceClass.GPU
            resource_slot = await scheduler._acquire_stage_resource(pipeline_stage)
        else:
            resource_class = _stage_resource(pipeline_stage)
            resource_slot = await scheduler._acquire_stage_resource(pipeline_stage)
        resource_acquired = True
        if not item.get("resultFolder"):
            await scheduler._set_stage(batch_id, item_id, "initialize")
        if not item.get("pipelineStage"):
            def mark_checkpointed(manifest: dict[str, Any]):
                for entry in manifest.get("items", []):
                    if entry.get("id") == item_id:
                        entry["pipelineStage"] = "initialize"
                        return True
                return False
            await scheduler.store.mutate(batch_id, mark_checkpointed)
        config = scheduler._config_for(batch, item)
        folder = item.get("resultFolder")
        if not isinstance(folder, str):
            folder = None
            input_path = await scheduler.store.input_path(batch_id, item_id)
            with Image.open(input_path) as opened:
                image = opened.convert("RGB")
        else:
            run = await scheduler._checkpoint_run(folder)
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
            run = PipelineRun(scheduler.result_root, folder_name, image, config)
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
            await scheduler._set_result_folder(batch_id, item_id, folder)
            stage_id = "initialize"
        else:
            stage_id = (
                scheduler._next_batch_stage(run)
                if pipeline_stage == "initialize"
                else pipeline_stage
            )

            if stage_id is not None and stage_id != "translation":
                if (
                    _stage_resource(stage_id) == ResourceClass.GPU
                    and resource_class != ResourceClass.GPU
                ):
                    scheduler._release_stage_resource(
                        pipeline_stage, resource_slot, resource_class
                    )
                    resource_acquired = False
                    resource_class = ResourceClass.GPU
                    resource_slot = await scheduler._acquire_stage_resource(stage_id)
                    resource_acquired = True
                await scheduler._set_stage(batch_id, item_id, stage_id)

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
                await scheduler._complete_checkpointed_textless(
                    batch_id, item, folder, stage_id, config, run, instance
                )
                return

        next_stage = scheduler._next_batch_stage(run)
        if stage_id == "rendering" and next_stage is None:
            result_path = final_file(scheduler.result_root / folder)
            if result_path is None:
                raise RuntimeError("Rendering produced no final image")
            run.manifest["status"] = "completed"
            run.manifest.pop("error", None)
            await run.checkpoint()
            index_result = getattr(scheduler.store, "register_result", None)
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
                    batch_finished = True
                return True

            await scheduler.store.mutate(batch_id, complete_page)
            (await scheduler.store.input_path(batch_id, item_id)).unlink(missing_ok=True)
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
                    set_item_stage(entry, "awaiting_translation")
                    entry.pop("stageStartedAt", None)
                    entry.pop("pipelineStage", None)
            manifest["status"] = "processing"
            return True

        if not page_completed:
            await scheduler.store.mutate(batch_id, continue_or_translate)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Error running checkpointed batch stage %s for %s: %s", stage_id, item_id, exc)
        def fail(manifest: dict[str, Any]):
            nonlocal batch_finished
            for entry in manifest.get("items", []):
                if entry.get("id") == item_id:
                    entry.update(status="error", stage=stage_id or "error", error=str(exc), resultFolder=folder or entry.get("resultFolder"))
            if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                manifest["status"] = "error"
                batch_finished = True
            return True
        await scheduler.store.mutate(batch_id, fail)
    finally:
        if run is not None:
            run.release_runtime()
            translator = getattr(instance, "translator", None)
            if translator is not None and translator._pipeline_run is run:
                translator._pipeline_run = None
        if image is not None:
            image.close()
        if resource_acquired:
            scheduler._release_stage_resource(pipeline_stage, resource_slot, resource_class)
        if batch_finished:
            await scheduler._reclaim_batch_memory(batch_id, instance)
        await scheduler.executors.free_executor(instance)
        scheduler._wake.set()
        correlation_id_ctx.reset(token)
