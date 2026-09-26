"""Manga-wide OCR stage scheduling for summary jobs."""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    from server.summary_generation import SummaryGenerationRuntime


@dataclass
class SummaryOCRJob:
    request: Request | None
    pages: list[dict[str, Any]]
    target_language: str
    worker: Any
    pause_event: asyncio.Event | None
    store: Any
    group_value: str
    clean_title: str
    has_group_text: bool
    refresh_text: bool
    extraction_required: bool
    pages_with_text: int


async def run_summary_ocr_job(
    job: SummaryOCRJob,
    runtime: SummaryGenerationRuntime,
) -> tuple[int, list[str], dict[str, str]]:
    request = job.request
    pages = job.pages
    target_language = job.target_language
    worker = job.worker
    pause_event = job.pause_event
    store = job.store
    group_value = job.group_value
    clean_title = job.clean_title
    has_group_text = job.has_group_text
    refresh_text = job.refresh_text
    extraction_required = job.extraction_required
    pages_with_text = job.pages_with_text
    page_count = len(pages)

    _summary_log = runtime.summary_log
    _update_summary_job_for = runtime.update_summary_job_for
    _run_summary_ocr = runtime.run_summary_ocr
    _run_summary_ocr_batch = runtime.run_summary_ocr_batch
    _summary_ocr_semaphore = runtime.summary_ocr_semaphore
    _summary_controller = runtime.summary_controller
    is_page_text_extracted = runtime.is_page_text_extracted
    SummaryQueueElement = runtime.summary_queue_element
    task_queue = runtime.task_queue
    wait_in_queue = runtime.wait_in_queue
    empty_device_cache = runtime.empty_device_cache

    failed_pages = []
    ocr_errors = {}

    if extraction_required:
        async def _execute_ocr(active_worker):
            nonlocal pages_with_text
            try:
                async with _summary_ocr_semaphore:
                    pending = [
                        (index, page)
                        for index, page in enumerate(pages)
                        if refresh_text or not is_page_text_extracted(page, has_group_text=has_group_text)
                    ]
                    cached_page_count = page_count - len(pending)
                    pending_positions = {index: position + 1 for position, (index, _) in enumerate(pending)}
                    last_progress = 0

                    async def report_stage(
                        pipeline_stage: str,
                        *,
                        page_number: int,
                        batch_position: int | None = None,
                        batch_size: int = 1,
                        batch_number: int = 0,
                        batch_count: int = 1,
                    ) -> None:
                        nonlocal last_progress
                        stage_info = {
                            "detection": ("detecting", 0, "Detecting text"),
                            "ocr": ("ocr", 1 / 3, "Reading OCR"),
                            "textline_merge": ("textline_merge", 2 / 3, "Merging text lines"),
                        }.get(pipeline_stage)
                        if stage_info is None:
                            return
                        stage, offset, label = stage_info
                        pending_passed_count = pending_positions[page_number - 1]
                        if batch_position is None:
                            pending_passed_count -= 1
                        passed_count = cached_page_count + pending_passed_count
                        if batch_position is None:
                            progress = round(((page_number - 1 + offset) / max(1, page_count)) * 70)
                        else:
                            phase = {"detection": 0, "ocr": 1, "textline_merge": 2}[pipeline_stage]
                            progress = round(
                                ((batch_number + (phase + batch_position / max(1, batch_size)) / 3)
                                 / max(1, batch_count)) * 70
                            )
                        last_progress = max(last_progress, progress)
                        await _update_summary_job_for(
                            store,
                            group_value,
                            clean_title,
                            "generating",
                            None,
                            stage,
                            last_progress,
                            label,
                            page_number,
                            page_count,
                            pages_with_text,
                            extraction_required,
                            stage_passed_count=passed_count,
                        )

                    async def wait_if_paused(page_number: int) -> None:
                        if pause_event is not None and not pause_event.is_set():
                            _summary_log("paused", clean_title, group_value, page=f"{page_number}/{page_count}")
                            await pause_event.wait()
                            _summary_log("resumed", clean_title, group_value, page=f"{page_number}/{page_count}")

                    async def record_page(index: int, page: dict, started_at: float, regions=None, error=None):
                        nonlocal pages_with_text, last_progress
                        current_page = index + 1
                        try:
                            _summary_log(
                                "ocr_started",
                                clean_title,
                                group_value,
                                page=f"{current_page}/{page_count}",
                                file=page["name"],
                            )
                            if error is not None:
                                raise error
                            if regions is None:
                                async def page_progress(stage: str) -> None:
                                    await report_stage(stage, page_number=current_page)

                                regions = await _run_summary_ocr(
                                    request,
                                    page,
                                    target_language,
                                    page_progress,
                                    active_worker,
                                    overwrite=refresh_text,
                                )
                            page["textRegions"] = regions
                            if regions:
                                pages_with_text += 1
                            _summary_log(
                                "ocr_completed",
                                clean_title,
                                group_value,
                                page=f"{current_page}/{page_count}",
                                file=page["name"],
                                regions=len(regions),
                                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 1),
                            )
                        except Exception as exc:
                            _summary_log(
                                "ocr_failed",
                                clean_title,
                                group_value,
                                level=logging.WARNING,
                                exc_info=True,
                                page=f"{current_page}/{page_count}",
                                file=page["name"],
                                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 1),
                                error=str(exc),
                            )
                            failed_pages.append(page["name"])
                            ocr_errors[page["name"]] = str(exc)

                        last_progress = max(last_progress, round(current_page / max(1, page_count) * 70))
                        await _update_summary_job_for(
                            store,
                            group_value,
                            clean_title,
                            "generating",
                            None,
                            "textline_merge",
                            last_progress,
                            "Merging text lines",
                            current_page,
                            page_count,
                            pages_with_text,
                            extraction_required,
                            stage_passed_count=cached_page_count + pending_positions[index],
                        )

                    batch_extractor = getattr(active_worker, "extract_text_batch", None)
                    batch_size = max(1, runtime.get_inference_page_batch_size())
                    if pending:
                        chunk = pending
                        await wait_if_paused(chunk[0][0] + 1)
                        started_at = time.perf_counter()
                        started_at_by_index = {index: started_at for index, _ in chunk}
                        batch_results = None
                        if batch_extractor is not None and len(chunk) > 1:
                            async def batch_progress(stage: str, position: int) -> None:
                                index, _ = chunk[position]
                                await report_stage(
                                    stage,
                                    page_number=index + 1,
                                    batch_position=position + 1,
                                    batch_size=len(chunk),
                                    batch_number=0,
                                    batch_count=1,
                                )

                            try:
                                batch_results = await _run_summary_ocr_batch(
                                    [page for _, page in chunk],
                                    target_language,
                                    active_worker,
                                    batch_size,
                                    batch_progress,
                                )
                            except Exception as exc:
                                _summary_log(
                                    "ocr_batch_fallback",
                                    clean_title,
                                    group_value,
                                    level=logging.WARNING,
                                    pages=len(chunk),
                                    error=str(exc),
                                )

                        for offset, (index, page) in enumerate(chunk):
                            if batch_results is None:
                                await wait_if_paused(index + 1)
                                await record_page(index, page, started_at_by_index[index])
                            else:
                                regions, error = batch_results[offset]
                                await record_page(
                                    index, page, started_at_by_index[index], regions, error
                                )
            finally:
                # Reclaim memory on the active worker and clear accelerator cache
                reclaim = getattr(active_worker, "reclaim_memory", None)
                if reclaim is not None:
                    try:
                        await reclaim()
                    except Exception:
                        pass
                empty_device_cache()
                gc.collect()

        if worker is not None:
            await _execute_ocr(worker)
        else:
            ocr_task = SummaryQueueElement(_execute_ocr)
            _summary_controller.register_ocr_task(group_value, ocr_task)
            task_queue.add_task(ocr_task)
            try:
                await wait_in_queue(ocr_task, None)
            finally:
                _summary_controller.unregister_ocr_task(group_value)
    else:
        await _update_summary_job_for(
            store,
            group_value,
            clean_title,
            "generating",
            None,
            "concatenating",
            70,
            f"Reused cached text · all {page_count} pages",
            page_count,
            page_count,
            pages_with_text,
            extraction_required,
            stage_passed_count=page_count,
        )

    return pages_with_text, failed_pages, ocr_errors
