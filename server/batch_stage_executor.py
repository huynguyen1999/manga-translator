"""Batch stage execution helpers for the persistent scheduler."""

import asyncio
from pathlib import Path
from typing import Any

import cv2
from PIL import Image


async def process_prepare_item(
    scheduler, batch_id: str, item_id: str, instance: Any,
    *, log_token, correlation_id_ctx, logger,
) -> None:
    if hasattr(instance, "_run_translation") and getattr(instance, "translator", None) is not None:
        await scheduler._process_checkpointed_prepare_item(batch_id, item_id, instance)
        return

    hook = None
    image = None
    folder = None
    short_b = log_token(batch_id)
    short_i = log_token(item_id)
    token = correlation_id_ctx.set(f"batch-{short_b}/prep-{short_i}")
    logger.info(f"Starting preparation for batch item {item_id} (batch {batch_id})")
    try:
        batch = await scheduler.store.get_batch(batch_id)
        item = next(item for item in batch["items"] if item["id"] == item_id)
        input_path = await scheduler.store.input_path(batch_id, item_id)
        with Image.open(input_path) as opened:
            image = opened.convert("RGB")
        config = scheduler._config_for(batch, item)

        translator = getattr(instance, "translator", None)
        if translator is not None and hasattr(translator, "add_progress_hook"):
            main_loop = asyncio.get_running_loop()

            async def progress(state: str, _finished: bool):
                if state.startswith("debug_folder:"):
                    folder_name = state.split(":", 1)[1]
                    future = asyncio.run_coroutine_threadsafe(
                        scheduler._set_result_folder(batch_id, item_id, folder_name), main_loop
                    )
                    await asyncio.wrap_future(future)
                    return
                if state.startswith(("final_ready:", "rendering_folder:", "offline_model:", "gemini_model:")):
                    return
                future = asyncio.run_coroutine_threadsafe(
                    scheduler._set_stage(batch_id, item_id, state), main_loop
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
            index_result = getattr(scheduler.store, "register_result", None)
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

            await scheduler.store.mutate(batch_id, complete_textless)
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

            await scheduler.store.mutate(batch_id, ready_for_translation)
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

        await scheduler.store.mutate(batch_id, fail)
    finally:
        translator = getattr(instance, "translator", None)
        if hook is not None and translator is not None and hook in translator._progress_hooks:
            translator._progress_hooks.remove(hook)
        if image is not None:
            image.close()
        scheduler._running_items.discard((batch_id, item_id))
        await scheduler.executors.free_executor(instance)
        scheduler._wake.set()
        correlation_id_ctx.reset(token)


async def process_checkpointed_ocr_group(
    scheduler, batch_id: str, claimed: list[dict[str, Any]], instance: Any,
    *, log_token, correlation_id_ctx, logger, deserialize_textlines,
) -> None:
    item_ids = [item["id"] for item in claimed]
    pages = []
    completed_textless_ids = set()
    batch_finished = False
    slot = None
    token = correlation_id_ctx.set(f"batch-{log_token(batch_id)}/ocr-{log_token(item_ids[0])}")
    try:
        translator = instance.translator
        slot = await scheduler._acquire_stage_resource("ocr")
        batch = await scheduler.store.get_batch(batch_id)
        for item in claimed:
            folder = item.get("resultFolder")
            if not isinstance(folder, str):
                raise RuntimeError("OCR batch page has no saved pipeline checkpoint")
            run = await scheduler._checkpoint_run(folder)
            if run is None:
                raise RuntimeError(f"Saved pipeline checkpoint is unavailable for {item['id']}")
            run.memory_batch_id = batch_id
            run.memory_page_id = item.get("pageId") or item["id"]
            run.translator = translator
            config = scheduler._config_for(batch, item)
            ctx = run._ensure_context()
            if not getattr(ctx, "textlines", None):
                detection = run._document("detection.json")
                if detection is not None:
                    ctx.textlines = deserialize_textlines(detection)
            if not getattr(ctx, "textlines", None):
                await scheduler._complete_checkpointed_textless(
                    batch_id, item, folder, "ocr", config, run, instance
                )
                completed_textless_ids.add(item["id"])
                continue
            run.ctx = ctx
            pages.append((item, folder, run, config, ctx))

        if pages:
            await scheduler._set_group_stage(batch_id, item_ids, "ocr")

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
                await scheduler._complete_checkpointed_textless(
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

            await scheduler.store.mutate(batch_id, continue_page)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Error running batched OCR for %s: %s", item_ids, exc)
        failed_item_ids = set(item_ids) - completed_textless_ids

        def fail(manifest: dict[str, Any]):
            nonlocal batch_finished
            for entry in manifest.get("items", []):
                if entry.get("id") in failed_item_ids:
                    entry.update(status="error", stage="ocr", error=str(exc))
            if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                manifest["status"] = "error"
                batch_finished = True
            return True

        await scheduler.store.mutate(batch_id, fail)
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
            scheduler._release_stage_resource("ocr", slot)
        if batch_finished:
            await scheduler._reclaim_batch_memory(batch_id, instance)
        await scheduler.executors.free_executor(instance)
        scheduler._wake.set()
        correlation_id_ctx.reset(token)


async def process_checkpointed_model_group(
    scheduler,
    batch_id: str,
    claimed: list[dict[str, Any]],
    instance: Any,
    stage_id: str,
    *,
    log_token,
    correlation_id_ctx,
    logger,
    set_item_stage,
    deserialize_textblocks,
) -> None:
    if stage_id not in {"upscaling", "detection", "bubble_detection", "inpainting"}:
        raise ValueError(f"Unsupported batched model stage: {stage_id}")
    item_ids = [item["id"] for item in claimed]
    pages = []
    batch_finished = False
    slot = None
    translator = getattr(instance, "translator", None)
    token = correlation_id_ctx.set(f"batch-{log_token(batch_id)}/{stage_id}-{log_token(item_ids[0])}")
    try:
        slot = await scheduler._acquire_stage_resource(stage_id)
        batch = await scheduler.store.get_batch(batch_id)
        for item in claimed:
            folder = item.get("resultFolder")
            if not isinstance(folder, str):
                raise RuntimeError(f"{stage_id} page has no saved pipeline checkpoint")
            run = await scheduler._checkpoint_run(folder)
            if run is None:
                raise RuntimeError(f"Saved pipeline checkpoint is unavailable for {item['id']}")
            run.memory_batch_id = batch_id
            run.memory_page_id = item.get("pageId") or item["id"]
            config = scheduler._config_for(batch, item)
            ctx = run._ensure_context()
            if stage_id == "upscaling":
                if ctx.img_colorized is None:
                    ctx.img_colorized = ctx.input
                if ctx.img_colorized is None:
                    raise RuntimeError(f"No image available for upscaling {item['id']}")
            elif stage_id == "inpainting":
                if ctx.img_rgb is None:
                    raise RuntimeError(f"No image available for inpainting {item['id']}")
                if ctx.mask is None:
                    mask_path = run.path / "mask_final.png"
                    if mask_path.is_file():
                        ctx.mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
                if ctx.protected_edge_mask is None:
                    protected_path = run.path / "protected_bubble_edge.png"
                    if protected_path.is_file():
                        ctx.protected_edge_mask = cv2.imread(str(protected_path), cv2.IMREAD_GRAYSCALE)
                if not getattr(ctx, "text_regions", None):
                    regions = run._document("translations.json") or run._document("text_regions_merged.json")
                    if regions is not None:
                        ctx.text_regions = deserialize_textblocks(regions)
            elif ctx.img_rgb is None:
                activity = "detection" if stage_id == "detection" else "bubble detection"
                raise RuntimeError(f"No image canvas available for {activity} {item['id']}")
            run.translator = translator
            await run.begin_stage(stage_id, config)
            pages.append((item, folder, run, config, ctx))

        if pages:
            await scheduler._set_group_stage(batch_id, item_ids, stage_id)

        async def infer_batch():
            configs = [page[3] for page in pages]
            contexts = [page[4] for page in pages]
            if stage_id == "upscaling":
                return await translator._run_upscaling_batch(configs, contexts)
            if stage_id == "detection":
                return await translator._run_detection_batch(configs, contexts)
            if stage_id == "bubble_detection":
                return await translator._run_bubble_detection_batch(configs, contexts)
            return await translator._run_inpainting_batch(configs, contexts)

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
                if stage_id == "bubble_detection"
                else {"precomputed_inpainting": output}
            )
            await run.retry_stage(
                stage_id,
                config,
                translator,
                stage_already_running=True,
                **kwargs,
            )

        for item, folder, run, _, _ in pages:
            next_stage = scheduler._next_batch_stage(run)

            def continue_page(manifest: dict[str, Any], item_id=item["id"], folder=folder, next_stage=next_stage):
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

            await scheduler.store.mutate(batch_id, continue_page)
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
            nonlocal batch_finished
            for entry in manifest.get("items", []):
                if entry.get("id") in item_ids:
                    entry.update(status="error", stage=stage_id, error=str(exc))
            if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                manifest["status"] = "error"
                batch_finished = True
            return True

        await scheduler.store.mutate(batch_id, fail)
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
            scheduler._release_stage_resource(stage_id, slot)
        if batch_finished:
            await scheduler._reclaim_batch_memory(batch_id, instance)
        await scheduler.executors.free_executor(instance)
        scheduler._wake.set()
        correlation_id_ctx.reset(token)
