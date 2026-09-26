"""Batch item lifecycle execution for the persistent scheduler."""

import asyncio
from pathlib import Path
from typing import Any

from PIL import Image

from manga_translator.pipeline.run import PipelineRun
from server.batch_store import BatchNotFound
from server.image_variants import final_file


async def process_item(
    scheduler, batch_id: str, item_id: str, instance: Any, *,
    log_token, correlation_id_ctx, logger,
):
    hook = None
    batch_finished = False
    image = context = config = None
    item = None
    input_path = None
    checkpoint_folder = None
    checkpoint_run = None
    short_b = log_token(batch_id)
    short_i = log_token(item_id)
    token = correlation_id_ctx.set(f"batch-{short_b}/item-{short_i}")
    logger.info(f"Starting batch item {item_id} (batch {batch_id})")
    try:
        batch = await scheduler.store.get_batch(batch_id)
        item = next(item for item in batch["items"] if item["id"] == item_id)
        if not item.get("retryFromStage"):
            input_path = await scheduler.store.input_path(batch_id, item_id)
            with Image.open(input_path) as opened:
                image = opened.convert("RGB")
        config = scheduler._config_for(batch, item)

        translator = getattr(instance, "translator", None)
        if translator is not None and hasattr(translator, "add_progress_hook"):
            main_loop = asyncio.get_running_loop()

            async def progress(state: str, _finished: bool):
                if state.startswith("debug_folder:"):
                    folder = state.split(":", 1)[1]
                    current_loop = asyncio.get_running_loop()
                    if current_loop is main_loop:
                        await scheduler._set_result_folder(batch_id, item_id, folder)
                    else:
                        future = asyncio.run_coroutine_threadsafe(
                            scheduler._set_result_folder(batch_id, item_id, folder), main_loop
                        )
                        await asyncio.wrap_future(future)
                    return
                if state.startswith(("final_ready:", "rendering_folder:", "offline_model:", "gemini_model:")):
                    return
                current_loop = asyncio.get_running_loop()
                if current_loop is main_loop:
                    await scheduler._set_stage(batch_id, item_id, state)
                    return
                future = asyncio.run_coroutine_threadsafe(
                    scheduler._set_stage(batch_id, item_id, state), main_loop
                )
                await asyncio.wrap_future(future)

            hook = progress
            translator.add_progress_hook(hook)

        checkpoint_folder = item.get("resultFolder")
        run = None
        if isinstance(checkpoint_folder, str):
            run = PipelineRun.get_or_load(scheduler.result_root, checkpoint_folder)
            if run is None and getattr(scheduler.store, "database", None) is not None:
                documents = await scheduler.store.database.get_documents(checkpoint_folder)
                run = PipelineRun.from_documents(
                    scheduler.result_root, checkpoint_folder, documents
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
        result_folder = scheduler.result_root / folder if isinstance(folder, str) else None
        if not result_folder or not result_folder.is_dir() or final_file(result_folder) is None:
            raise RuntimeError("Translation completed without a verified result folder")

        index_result = getattr(scheduler.store, "register_result", None)
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

        await scheduler.store.mutate(batch_id, complete)
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

            await scheduler.store.mutate(batch_id, fail)
            if (
                isinstance(checkpoint_folder, str)
                and Path(checkpoint_folder).name == checkpoint_folder
                and item is not None
            ):
                failed_result = scheduler.result_root / checkpoint_folder
                index_result = getattr(scheduler.store, "register_result", None)
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
        if batch_finished:
            await scheduler._reclaim_batch_memory(batch_id, instance)
        await scheduler.executors.free_executor(instance)
        scheduler._wake.set()
        correlation_id_ctx.reset(token)
