"""Lifecycle wrapper for a scheduled manga summary job."""

import asyncio
import logging
from typing import Any, Callable

from fastapi import HTTPException, Request

from server.api.schemas.summary import MangaSummaryRequest


async def run_summary_task(
    request: Request | None,
    data: MangaSummaryRequest,
    store: Any,
    group_value: str,
    clean_title: str,
    worker=None,
    pause_event: asyncio.Event | None = None,
    *,
    generate_summary: Callable[..., Any],
    log: Callable[..., Any],
    update_summary_job: Callable[..., Any],
    controller: Any,
    reclaim_memory: Callable[..., Any],
) -> None:
    current_task = asyncio.current_task()
    try:
        await generate_summary(request, data, worker, pause_event=pause_event)
    except asyncio.CancelledError:
        log(
            "cancelled",
            clean_title,
            group_value,
            level=logging.INFO,
            stage="job",
            error="Summary task was cancelled",
        )
        raise
    except HTTPException as exc:
        log(
            "failed",
            clean_title,
            group_value,
            level=logging.WARNING,
            stage="job",
            status_code=exc.status_code,
            error=str(exc.detail),
        )
        if exc.status_code in {400, 404}:
            await update_summary_job(
                store,
                group_value,
                clean_title,
                "error",
                str(exc.detail),
                "job",
                0,
                str(exc.detail),
            )
    except Exception as exc:
        log(
            "failed",
            clean_title,
            group_value,
            level=logging.ERROR,
            exc_info=True,
            stage="job",
            error=str(exc),
        )
        await update_summary_job(
            store,
            group_value,
            clean_title,
            "error",
            str(exc),
            "summarizing",
            0,
            str(exc),
        )
    finally:
        controller.unregister_task(group_value, current_task)
        controller.unregister_task(clean_title, current_task)
        await reclaim_memory(worker)
