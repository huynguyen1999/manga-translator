"""Pipeline rerun item lifecycle for the persistent batch scheduler."""

import asyncio
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from manga_translator.pipeline.stages import (
    PipelineStage,
    fingerprint,
    settings_for_stage,
    stage_from_progress,
)


async def process_pipeline_rerun_item(
    scheduler, batch_id: str, item_id: str, instance: Any, *,
    log_token, set_item_stage, correlation_id_ctx, logger,
):
    token = correlation_id_ctx.set(f"batch-{log_token(batch_id)}/rerun-{log_token(item_id)}")
    staging_dir: Path | None = None
    hook = None
    stage_runs: dict[PipelineStage, float] = {}
    batch_finished = False
    ctx = executed_ctx = state = remap_result = None
    scheduler_loop = asyncio.get_running_loop()
    database = getattr(scheduler.store, "database", None)
    page_ref: str | None = None
    try:
        batch = await scheduler.store.get_batch(batch_id)
        item = next(item for item in batch["items"] if item["id"] == item_id)
        folder = item.get("resultFolder")
        if not isinstance(folder, str) or Path(folder).name != folder:
            raise RuntimeError("Rerun item has no valid result folder")
        result_dir = (scheduler.result_root / folder).resolve()
        if result_dir.parent != scheduler.result_root or not result_dir.is_dir():
            raise RuntimeError("Result folder is unavailable")
        isolated_rerun = bool(item.get("isolatedRerun"))
        if isolated_rerun and not (result_dir / ".ai-case").is_file():
            raise RuntimeError("Isolated pipeline case marker is missing")
        if isolated_rerun:
            database = None
        else:
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
        config = scheduler._config_for(batch, config_item)
        if plan.mode == "typesetting":
            config.bubble_detection.enabled = False
            config.translator.translator = "none"
            config.translator.translation_quality = "fast"
            config.upscale.upscale_ratio = None
            config.upscale.revert_upscaling = False

        ctx, state = await load_rerun_context(result_dir, plan, config, database=database, record_id=item.get("pageId"))

        async def update_progress(stage: str):
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
                        return set_item_stage(entry, stage)
                return True
            await scheduler.store.mutate(batch_id, set_stage)

        async def progress_hook(stage: str, _finished: bool = False):
            if asyncio.get_running_loop() is scheduler_loop:
                await update_progress(stage)
                return
            future = asyncio.run_coroutine_threadsafe(update_progress(stage), scheduler_loop)
            await asyncio.wrap_future(future)

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

        index_result = getattr(scheduler.store, "register_result", None)
        if index_result is not None and not isolated_rerun:
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
            nonlocal batch_finished
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
                batch_finished = True
            return True

        await scheduler.store.mutate(batch_id, complete)
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
            nonlocal batch_finished
            for entry in manifest.get("items", []):
                if entry.get("id") == item_id:
                    entry.update(status="error", stage="error", error=str(exc))
            if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                manifest["status"] = "error"
                batch_finished = True
            return True

        await scheduler.store.mutate(batch_id, fail)
    finally:
        if staging_dir is not None and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        translator = getattr(instance, "translator", instance)
        if hook is not None and translator is not None and hook in getattr(translator, "_progress_hooks", []):
            translator._progress_hooks.remove(hook)
        ctx = executed_ctx = state = remap_result = None
        if batch_finished:
            await scheduler._reclaim_batch_memory(batch_id, instance)
        scheduler._running_items.discard((batch_id, item_id))
        await scheduler.executors.free_executor(instance)
        scheduler._wake.set()
        correlation_id_ctx.reset(token)
