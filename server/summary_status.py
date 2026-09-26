"""Read and update summary-job status across PostgreSQL and file storage."""

from __future__ import annotations

import asyncio
from typing import Any, Callable


async def summary_status_for(
    store,
    group_value: str,
    title: str,
    pages: list[dict[str, Any]],
    *,
    result_root,
    file_status_fn: Callable,
) -> dict[str, Any]:
    if store is not None:
        return (await store.summary_status(group_value)) or {}
    return await asyncio.to_thread(file_status_fn, result_root, title, pages)


async def update_summary_job_for(
    store,
    group_value: str,
    title: str,
    status: str,
    error: str | None = None,
    stage: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    current_page: int | None = None,
    page_count: int | None = None,
    pages_with_text: int | None = None,
    extraction_required: bool | None = None,
    provider: str | None = None,
    model: str | None = None,
    refresh_text: bool | None = None,
    regenerate: bool | None = None,
    stage_passed_count: int | None = None,
    *,
    result_root,
    file_update_fn: Callable,
) -> None:
    if store is not None:
        await store.update_summary_job(
            group_value,
            status,
            error,
            stage,
            progress,
            message,
            current_page,
            page_count,
            pages_with_text,
            extraction_required,
            provider,
            model,
            refresh_text=refresh_text,
            regenerate=regenerate,
            stage_passed_count=stage_passed_count,
        )
        return

    await asyncio.to_thread(
        file_update_fn,
        result_root,
        title,
        status,
        error,
        stage,
        progress,
        message,
        current_page,
        page_count,
        pages_with_text,
        extraction_required,
        provider,
        model,
        refresh_text=refresh_text,
        regenerate=regenerate,
        stage_passed_count=stage_passed_count,
    )
