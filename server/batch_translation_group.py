"""Stage-barrier translation group execution for the batch scheduler."""

import asyncio
import json
from typing import Any

from manga_translator import Context
from manga_translator.pipeline.run import deserialize_textblocks, serialize_regions


async def process_translation_group(
    scheduler, batch_id: str, claimed: list[dict[str, Any]], instance: Any, *,
    log_token, correlation_id_ctx, logger,
) -> None:
    item_ids = [item["id"] for item in claimed]
    hook = None
    owns_translation_lock = False
    owns_network_slot = False
    batch_finished = False
    translation_runs = {}
    lock = scheduler._translation_locks.setdefault(batch_id, asyncio.Lock())
    main_loop = asyncio.get_running_loop()
    token = correlation_id_ctx.set(f"batch-{log_token(batch_id)}/trans-{log_token(item_ids[0])}")
    logger.info(f"Starting batch translation for items {item_ids} (batch {batch_id})")
    try:
        network_slot = await scheduler._acquire_stage_resource("translation")
        owns_network_slot = True
        batch = await scheduler.store.get_batch(batch_id)
        current = {item["id"]: item for item in batch["items"]}
        configs = [scheduler._config_for(batch, current[item_id]) for item_id in item_ids]

        story_plan = scheduler._story_plan_for_group(
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
            run = await scheduler._checkpoint_run(folder) if isinstance(folder, str) else None
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
            result_dir = scheduler.result_root / folder if isinstance(folder, str) else None

            saved_documents = {}
            database = getattr(scheduler.store, "database", None)
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
                        scheduler._set_group_stage(batch_id, item_ids, state),
                        main_loop,
                    )
                    await asyncio.wrap_future(future)
                    return
                target_ids = scheduler._progress_item_ids(state, item_ids, translator)
                if target_ids:
                    future = asyncio.run_coroutine_threadsafe(
                        scheduler._set_active_group_item(batch_id, item_ids, target_ids[0], state),
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
                await scheduler.store.mutate(batch_id, fail_page)
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
            await scheduler.store.mutate(batch_id, queue_mask_stage)

        def update_batch_status(manifest: dict[str, Any]):
            nonlocal batch_finished
            pending = any(
                item.get("status") in {"queued", "processing"}
                for item in manifest.get("items", [])
            )
            if not pending:
                manifest["status"] = "error" if any(
                    item.get("status") == "error" for item in manifest.get("items", [])
                ) else "completed"
                batch_finished = True
            elif manifest.get("status") != "paused":
                manifest["status"] = "processing"
            return True
        await scheduler.store.mutate(batch_id, update_batch_status)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Error processing batch group %s: %s", item_ids, exc)
        def fail_group(manifest: dict[str, Any]):
            nonlocal batch_finished
            for entry in manifest.get("items", []):
                if entry.get("id") in item_ids:
                    entry.update(status="error", stage="translation", error=str(exc))
            if not any(entry.get("status") in {"queued", "processing"} for entry in manifest.get("items", [])):
                manifest["status"] = "error"
                batch_finished = True
            return True
        await scheduler.store.mutate(batch_id, fail_group)
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
            if batch_finished:
                await scheduler._reclaim_batch_memory(batch_id, instance)
            await scheduler.executors.free_executor(instance)
        if owns_translation_lock:
            lock.release()
        if owns_network_slot:
            scheduler._release_stage_resource("translation", network_slot)
        scheduler._reserved_items.difference_update((batch_id, item_id) for item_id in item_ids)
        for item_id in item_ids:
            scheduler._running_items.discard((batch_id, item_id))
        scheduler._wake.set()
        correlation_id_ctx.reset(token)
