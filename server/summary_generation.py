"""Summary-job orchestration, separated from the HTTP application."""

import asyncio
import gc
import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException, Request

from server.api.schemas.summary import MangaSummaryRequest
from server.summary_ocr_execution import SummaryOCRJob, run_summary_ocr_job


@dataclass(frozen=True)
class SummaryGenerationRuntime:
    result_root: Path
    get_store: Callable[..., Any]
    summary_queue_element: Callable[..., Any]
    run_summary_ocr: Callable[..., Any]
    run_summary_ocr_batch: Callable[..., Any]
    summary_controller: Any
    summary_log: Callable[..., Any]
    summary_ocr_semaphore: Any
    summary_status_for: Callable[..., Any]
    summary_target_language: Callable[..., Any]
    update_summary_job_for: Callable[..., Any]
    get_inference_page_batch_size: Callable[[], int]
    task_queue: Any
    wait_in_queue: Callable[..., Any]
    empty_device_cache: Callable[..., Any]
    deepseek_token_count_factory: Callable[..., Any]
    generate_synopsis: Callable[..., Any]
    group_pages: Callable[..., Any]
    is_page_text_extracted: Callable[..., Any]
    read_page_text: Callable[..., Any]
    resolve_summary_model: Callable[..., Any]
    save_summary: Callable[..., Any]
    source_snapshot: Callable[..., Any]
    summary_error_details: Callable[..., Any]
    transcript_pages: Callable[..., Any]


async def generate_manga_summary(
    request: Request,
    data: MangaSummaryRequest,
    worker=None,
    pause_event: asyncio.Event | None = None,
    *,
    runtime: SummaryGenerationRuntime,
):
    _postgres = runtime.get_store
    RESULT_ROOT = runtime.result_root
    _summary_controller = runtime.summary_controller
    _summary_log = runtime.summary_log
    _summary_status_for = runtime.summary_status_for
    _summary_target_language = runtime.summary_target_language
    _update_summary_job_for = runtime.update_summary_job_for
    empty_device_cache = runtime.empty_device_cache
    _deepseek_token_count_factory = runtime.deepseek_token_count_factory
    generate_synopsis = runtime.generate_synopsis
    group_pages = runtime.group_pages
    is_page_text_extracted = runtime.is_page_text_extracted
    read_page_text = runtime.read_page_text
    resolve_summary_model = runtime.resolve_summary_model
    save_summary = runtime.save_summary
    source_snapshot = runtime.source_snapshot
    summary_error_details = runtime.summary_error_details
    transcript_pages = runtime.transcript_pages
    group_value = (data.groupId or data.mangaTitle or "").strip()
    if not group_value:
        raise HTTPException(400, detail="groupId is required")
    clean_title = (data.mangaTitle or "Ungrouped").strip() or "Ungrouped"
    # Worker availability, not a global lock, controls summary concurrency.
    async with nullcontext():
        store = _postgres()
        if store is not None:
            resolved_group = await store.resolve_group_id(group_value)
            if resolved_group is None and data.mangaTitle:
                resolved_group = await store.resolve_group_id(data.mangaTitle.strip())
            group_value = resolved_group or group_value
            clean_title = await store.resolve_group_title(group_value) or clean_title
            pages = await store.group_pages(group_value)
        else:
            pages = await asyncio.to_thread(group_pages, RESULT_ROOT, clean_title)
        if not pages:
            raise HTTPException(404, detail="Manga group not found")

        force_regenerate = data.regenerate or data.refreshText
        status = await _summary_status_for(store, group_value, clean_title, pages)
        if status["jobStatus"] == "generating" and not force_regenerate:
            existing_task = _summary_controller.get_task(group_value) or _summary_controller.get_task(clean_title)
            current_task = asyncio.current_task()
            if existing_task is not None and existing_task is not current_task and not existing_task.done():
                _summary_log(
                    "skipped",
                    clean_title,
                    group_value,
                    reason="already_generating",
                    stage=status.get("jobStage"),
                    progress=status.get("jobProgress"),
                )
                return status
        if status["summary"] and not status["stale"] and not force_regenerate:
            if status.get("jobStatus") != "ready":
                await _update_summary_job_for(
                    store,
                    group_value,
                    clean_title,
                    "ready",
                    stage="complete",
                    progress=100,
                    message="Summary ready",
                )
                status["jobStatus"] = "ready"
                status["jobStage"] = "complete"
                status["jobProgress"] = 100
                status["jobMessage"] = "Summary ready"
            _summary_log(
                "skipped",
                clean_title,
                group_value,
                reason="already_ready",
                summary_available=True,
            )
            return status

        page_count = len(pages)
        try:
            summary_provider, summary_model, _, _ = resolve_summary_model(data.summaryModel)
        except ValueError as exc:
            _summary_log(
                "rejected",
                clean_title,
                group_value,
                level=logging.WARNING,
                reason="invalid_model",
                error=str(exc),
            )
            raise HTTPException(400, detail=str(exc)) from exc
        has_group_text = any(bool(read_page_text(page)) for page in pages)
        missing_page_count = (
            page_count
            if data.refreshText
            else sum(not is_page_text_extracted(page, has_group_text=has_group_text) for page in pages)
        )
        cached_page_count = page_count - missing_page_count
        extraction_required = missing_page_count > 0
        pages_with_text = 0 if data.refreshText else sum(bool(read_page_text(page)) for page in pages)
        initial_stage = "detecting" if extraction_required else "concatenating"
        if data.refreshText:
            initial_message = f"Re-reading text · all {page_count} pages will be replaced"
        elif extraction_required:
            initial_message = (
                f"Detecting text · {missing_page_count} pages need extraction; "
                f"{cached_page_count} cached pages reused"
            )
        else:
            initial_message = f"Reusing text from all {page_count} cached pages"
        await _update_summary_job_for(
            store,
            group_value,
            clean_title,
            "generating",
            None,
            initial_stage,
            0 if extraction_required else 70,
            initial_message,
            0,
            page_count,
            pages_with_text,
            extraction_required,
            summary_provider,
            summary_model,
            stage_passed_count=cached_page_count,
        )
        target_language = _summary_target_language(pages)
        summary_started_at = time.perf_counter()
        _summary_log(
            "started",
            clean_title,
            group_value,
            pages=page_count,
            missing_pages=missing_page_count,
            cached_pages=cached_page_count,
            regenerate=force_regenerate,
            refresh_text=data.refreshText,
            provider=summary_provider,
            model=summary_model,
            language=target_language,
        )
        pages_with_text, failed_pages, ocr_errors = await run_summary_ocr_job(
            SummaryOCRJob(
                request=request,
                pages=pages,
                target_language=target_language,
                worker=worker,
                pause_event=pause_event,
                store=store,
                group_value=group_value,
                clean_title=clean_title,
                has_group_text=has_group_text,
                refresh_text=data.refreshText,
                extraction_required=extraction_required,
                pages_with_text=pages_with_text,
            ),
            runtime,
        )
        if pause_event is not None and not pause_event.is_set():
            _summary_log(
                "paused",
                clean_title,
                group_value,
                stage="summarizing",
            )
            await pause_event.wait()
            _summary_log(
                "resumed",
                clean_title,
                group_value,
                stage="summarizing",
            )

        await _update_summary_job_for(
            store,
            group_value,
            clean_title,
            "generating",
            None,
            "concatenating",
            72,
            f"Combining text from {pages_with_text} pages",
            page_count,
            page_count,
            pages_with_text,
            extraction_required,
            summary_provider,
            summary_model,
            stage_passed_count=page_count,
        )
        snapshot = await asyncio.to_thread(source_snapshot, pages)
        for entry in snapshot["missing"]:
            if entry["name"] not in failed_pages:
                failed_pages.append(entry["name"])
                ocr_errors[entry["name"]] = "No text detected"
        _summary_log(
            "text_collected",
            clean_title,
            group_value,
            pages_with_text=sum(bool(entry["texts"]) for entry in snapshot["entries"]),
            pages=page_count,
            missing_pages=len(snapshot["missing"]),
            failed_pages=len(failed_pages),
            transcript_chars=sum(len(text) for text in snapshot["texts"]),
        )
        if not snapshot["texts"]:
            detail = "No original text could be recovered"
            if ocr_errors:
                error_items = [f"{page_name} ({err})" if err != "No text detected" else page_name for page_name, err in ocr_errors.items()]
                if len(error_items) > 8:
                    summary_failed = f"{', '.join(error_items[:8])} (and {len(error_items) - 8} more)"
                else:
                    summary_failed = ", ".join(error_items)
                detail += f"; OCR failed for: {summary_failed}"
            elif failed_pages:
                if len(failed_pages) > 8:
                    summary_failed = f"{', '.join(failed_pages[:8])} (and {len(failed_pages) - 8} more)"
                else:
                    summary_failed = ", ".join(failed_pages)
                detail += f"; OCR failed for: {summary_failed}"
            _summary_log(
                "failed",
                clean_title,
                group_value,
                level=logging.ERROR,
                stage="text_collection",
                duration_ms=round((time.perf_counter() - summary_started_at) * 1000, 1),
                error=detail,
            )
            await _update_summary_job_for(
                store,
                group_value,
                clean_title,
                "error",
                detail,
                "textline_merge" if extraction_required else "concatenating",
                70,
                detail,
                page_count,
                page_count,
                pages_with_text,
                extraction_required,
                stage_passed_count=page_count,
            )
            raise HTTPException(422, detail=detail)

        transcript = await asyncio.to_thread(transcript_pages, snapshot)
        await _update_summary_job_for(
            store,
            group_value,
            clean_title,
            "generating",
            None,
            "summarizing",
            85,
            f"Generating the manga synopsis with {summary_model}",
            page_count,
            page_count,
            pages_with_text,
            extraction_required,
        )
        provider_started_at = time.perf_counter()
        _summary_log(
            "summarization_started",
            clean_title,
            group_value,
            provider=summary_provider,
            model=summary_model,
            language=target_language,
            transcript_pages=len(transcript),
            transcript_chars=sum(len(text) for text in snapshot["texts"]),
        )
        try:
            summary = await generate_synopsis(
                transcript,
                target_language,
                _deepseek_token_count_factory(),
                data.summaryModel,
            )
            _summary_log(
                "summarization_completed",
                clean_title,
                group_value,
                provider=summary_provider,
                model=summary_model,
                summary_chars=len(summary),
                elapsed_ms=round((time.perf_counter() - provider_started_at) * 1000, 1),
            )
        except HTTPException as exc:
            _summary_log(
                "failed",
                clean_title,
                group_value,
                level=logging.WARNING,
                stage="summarizing",
                provider=summary_provider,
                model=summary_model,
                duration_ms=round((time.perf_counter() - summary_started_at) * 1000, 1),
                error=str(exc.detail),
            )
            await _update_summary_job_for(
                store,
                group_value,
                clean_title,
                "error",
                str(exc.detail),
                "summarizing",
                85,
                str(exc.detail),
                page_count,
                page_count,
                pages_with_text,
                extraction_required,
            )
            raise
        except Exception as exc:
            error_detail = summary_error_details(exc)
            _summary_log(
                "failed",
                clean_title,
                group_value,
                level=logging.ERROR,
                exc_info=True,
                stage="summarizing",
                provider=summary_provider,
                model=summary_model,
                duration_ms=round((time.perf_counter() - summary_started_at) * 1000, 1),
                error_type=type(exc).__name__,
                error=error_detail,
            )
            await _update_summary_job_for(
                store,
                group_value,
                clean_title,
                "error",
                str(exc),
                "summarizing",
                85,
                str(exc),
                page_count,
                page_count,
                pages_with_text,
                extraction_required,
            )
            raise HTTPException(502, detail=f"{summary_provider.title()} synopsis generation failed: {exc}") from exc

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record = {
            "groupId": group_value if store is not None else None,
            "mangaTitle": clean_title,
            "summary": summary,
            "language": target_language,
            "generatedAt": now,
            "sourceFingerprint": snapshot["fingerprint"],
            "pageCount": len(pages),
            "textPageCount": sum(bool(entry["texts"]) for entry in snapshot["entries"]),
            "skippedPages": failed_pages,
            "ocrErrors": ocr_errors,
            "provider": summary_provider,
            "model": summary_model,
            "jobStatus": "ready",
            "jobError": None,
            "jobUpdatedAt": now,
            "jobStage": "complete",
            "jobProgress": 100,
            "jobMessage": "Summary ready",
            "jobCurrentPage": page_count,
            "jobPageCount": page_count,
            "jobPagesWithText": pages_with_text,
            "jobExtractionRequired": extraction_required,
            "jobDismissed": False,
        }
        if store is not None:
            await store.save_summary_payload(group_value, record)
            await asyncio.to_thread(save_summary, RESULT_ROOT, clean_title, record)
        else:
            await asyncio.to_thread(save_summary, RESULT_ROOT, clean_title, record)
        _summary_log(
            "completed",
            clean_title,
            group_value,
            provider=summary_provider,
            model=summary_model,
            pages=page_count,
            pages_with_text=pages_with_text,
            skipped_pages=len(failed_pages),
            summary_chars=len(summary),
            duration_ms=round((time.perf_counter() - summary_started_at) * 1000, 1),
        )
        empty_device_cache()
        gc.collect()
        return await _summary_status_for(store, group_value, clean_title, pages)
