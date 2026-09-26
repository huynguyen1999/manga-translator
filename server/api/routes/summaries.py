import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi import Request
from fastapi.responses import StreamingResponse

from manga_translator.config import MAX_MANGA_TITLE_LENGTH
from server.api.schemas.summary import MangaSummaryRequest, SummaryControlRequest, SummaryDismissRequest
from server.manga_summary import DEFAULT_SUMMARY_MODEL


def create_summary_read_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    group_pages: Callable[..., list[dict[str, Any]]],
    summary_status_for: Callable[..., Any],
    list_summary_jobs: Callable[..., list[dict[str, Any]]],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.get("/results/group/summary", tags=["api"])
    @router.get("/api/results/group/summary", tags=["api"])
    async def get_manga_summary(
        groupId: Optional[str] = Query(None, min_length=1),
        title: Optional[str] = Query(None, min_length=1, max_length=MAX_MANGA_TITLE_LENGTH),
    ):
        group_value = (groupId or title or "").strip()
        if not group_value:
            raise HTTPException(400, detail="groupId is required")
        clean_title = title.strip() if title else "Ungrouped"
        store = get_store()
        if store is not None:
            resolved_group = await store.resolve_group_id(group_value)
            if resolved_group is None and title:
                resolved_group = await store.resolve_group_id(title.strip())
            group_value = resolved_group or group_value
            clean_title = await store.resolve_group_title(group_value) or clean_title
            pages = await store.group_pages(group_value)
        else:
            pages = await asyncio.to_thread(group_pages, get_result_root(), clean_title)
        if not pages:
            raise HTTPException(404, detail="Manga group not found")
        return await summary_status_for(store, group_value, clean_title, pages)

    @router.get("/results/group/summary/config", tags=["api"])
    @router.get("/api/results/group/summary/config", tags=["api"])
    async def get_manga_summary_config():
        from manga_translator.translators.keys import GEMINI_MODEL, GROQ_MODEL

        return {
            "provider": "deepseek",
            "model": DEFAULT_SUMMARY_MODEL,
            "models": ["deepseek-flash", "groq", "gemini"],
            "providers": {
                "deepseek": ["deepseek-flash"],
                "groq": [GROQ_MODEL],
                "gemini": [GEMINI_MODEL],
            },
        }

    @router.get("/results/group/summary/jobs", tags=["api"])
    @router.get("/api/results/group/summary/jobs", tags=["api"])
    async def get_manga_summary_jobs():
        store = get_store()
        if store is not None:
            return await store.list_summary_jobs(20)
        return await asyncio.to_thread(list_summary_jobs, get_result_root(), 20)

    async def _summary_job_events():
        last_snapshot = None
        while True:
            # ponytail: two-second snapshot reads keep this simple; switch to store notifications if stream load grows.
            store = get_store()
            jobs = (
                await store.list_summary_jobs(20)
                if store is not None
                else await asyncio.to_thread(list_summary_jobs, get_result_root(), 20)
            )
            snapshot = json.dumps(jobs, sort_keys=True, separators=(",", ":"))
            if snapshot != last_snapshot:
                last_snapshot = snapshot
                yield f"data: {snapshot}\n\n"
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(2)

    @router.get("/results/group/summary/jobs/events", tags=["api"])
    @router.get("/api/results/group/summary/jobs/events", tags=["api"])
    async def manga_summary_job_events():
        return StreamingResponse(
            _summary_job_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router, (
        get_manga_summary,
        get_manga_summary_config,
        get_manga_summary_jobs,
        _summary_job_events,
        manga_summary_job_events,
    )


def create_summary_control_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    group_pages: Callable[..., list[dict[str, Any]]],
    summary_status_for: Callable[..., Any],
    update_job_for: Callable[..., Any],
    get_controller: Callable[[], Any],
    get_scheduler: Callable[[], Any],
    run_summary_task: Callable[..., Any],
    submit_lock: Any,
    resolve_summary_model: Callable[..., Any],
    read_page_text: Callable[..., Any],
    is_page_text_extracted: Callable[..., Any],
    dismiss_summary_job: Callable[..., Any],
    summary_log: Callable[..., Any],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.post("/results/group/summary/dismiss", tags=["api"])
    @router.post("/api/results/group/summary/dismiss", tags=["api"])
    async def dismiss_manga_summary_job(data: SummaryDismissRequest):
        group_value = (data.groupId or data.mangaTitle or "").strip()
        if not group_value:
            raise HTTPException(400, detail="groupId is required")
        get_controller().stop(group_value)
        if data.mangaTitle:
            get_controller().stop(data.mangaTitle.strip())
        store = get_store()
        if store is not None:
            await store.dismiss_summary_job(group_value)
        else:
            await asyncio.to_thread(dismiss_summary_job, get_result_root(), data.mangaTitle or group_value)
        if get_scheduler() is not None:
            get_scheduler().wake()
        return {"status": "dismissed"}

    @router.post("/results/group/summary/pause", tags=["api"])
    @router.post("/api/results/group/summary/pause", tags=["api"])
    async def pause_manga_summary_job(data: SummaryControlRequest):
        group_value = (data.groupId or data.mangaTitle or "").strip()
        if not group_value:
            raise HTTPException(400, detail="groupId is required")
        clean_title = (data.mangaTitle or "Ungrouped").strip() or "Ungrouped"
        store = get_store()
        if store is not None:
            resolved_group = await store.resolve_group_id(group_value)
            if resolved_group is None and data.mangaTitle:
                resolved_group = await store.resolve_group_id(data.mangaTitle.strip())
            group_value = resolved_group or group_value
            clean_title = await store.resolve_group_title(group_value) or clean_title
            pages = await store.group_pages(group_value)
        else:
            pages = await asyncio.to_thread(group_pages, get_result_root(), clean_title)

        status = await summary_status_for(store, group_value, clean_title, pages)
        current_status = status.get("jobStatus")
        if current_status not in {"generating", "queued"}:
            if current_status == "paused":
                return status
            raise HTTPException(400, detail=f"Cannot pause summary job with status '{current_status}'")

        get_controller().pause(group_value)
        get_controller().pause(clean_title)

        await update_job_for(
            store,
            group_value,
            clean_title,
            "paused",
            None,
            status.get("jobStage"),
            status.get("jobProgress"),
            "Paused",
            status.get("jobCurrentPage"),
            status.get("jobPageCount"),
            status.get("jobPagesWithText"),
            status.get("jobExtractionRequired"),
            status.get("provider"),
            status.get("model"),
        )
        summary_log("paused", clean_title, group_value)
        if get_scheduler() is not None:
            get_scheduler().wake()
        return await summary_status_for(store, group_value, clean_title, pages)

    @router.post("/results/group/summary/resume", tags=["api"])
    @router.post("/api/results/group/summary/resume", tags=["api"])
    async def resume_manga_summary_job(request: Request, data: SummaryControlRequest):
        group_value = (data.groupId or data.mangaTitle or "").strip()
        if not group_value:
            raise HTTPException(400, detail="groupId is required")
        clean_title = (data.mangaTitle or "Ungrouped").strip() or "Ungrouped"
        store = get_store()
        if store is not None:
            resolved_group = await store.resolve_group_id(group_value)
            if resolved_group is None and data.mangaTitle:
                resolved_group = await store.resolve_group_id(data.mangaTitle.strip())
            group_value = resolved_group or group_value
            clean_title = await store.resolve_group_title(group_value) or clean_title
            pages = await store.group_pages(group_value)
        else:
            pages = await asyncio.to_thread(group_pages, get_result_root(), clean_title)

        status = await summary_status_for(store, group_value, clean_title, pages)
        current_status = status.get("jobStatus")
        if current_status != "paused":
            if current_status in {"generating", "queued"}:
                return status
            raise HTTPException(400, detail=f"Cannot resume summary job with status '{current_status}'")

        get_controller().resume(group_value)
        get_controller().resume(clean_title)

        await update_job_for(
            store,
            group_value,
            clean_title,
            "generating",
            None,
            status.get("jobStage") or "concatenating",
            status.get("jobProgress") or 0,
            "Resuming synopsis generation…",
            status.get("jobCurrentPage"),
            status.get("jobPageCount"),
            status.get("jobPagesWithText"),
            status.get("jobExtractionRequired"),
            status.get("provider"),
            status.get("model"),
        )
        summary_log("resumed", clean_title, group_value)

        active_task = get_controller().get_task(group_value) or get_controller().get_task(clean_title)
        if active_task is not None and not active_task.done():
            pass
        elif get_scheduler() is not None:
            await update_job_for(
                store,
                group_value,
                clean_title,
                "queued",
                None,
                status.get("jobStage") or "concatenating",
                status.get("jobProgress") or 0,
                "Waiting for an available worker",
                status.get("jobCurrentPage"),
                status.get("jobPageCount"),
                status.get("jobPagesWithText"),
                status.get("jobExtractionRequired"),
                status.get("provider"),
                status.get("model"),
                regenerate=True,
            )
            get_scheduler().wake()
        else:
            req_data = MangaSummaryRequest(
                groupId=group_value if store is not None else None,
                mangaTitle=clean_title,
                summaryModel=status.get("model"),
                regenerate=True,
                refreshText=False,
            )
            pause_event = get_controller().get_pause_event(group_value)
            pause_event.set()
            task = asyncio.create_task(
                run_summary_task(
                    request, req_data, store, group_value, clean_title, worker=None, pause_event=pause_event
                ),
                name=f"summary-{group_value}",
            )
            get_controller().register_task(group_value, req_data, task)

        return await summary_status_for(store, group_value, clean_title, pages)

    @router.post("/results/group/summary/stop", tags=["api"])
    @router.post("/api/results/group/summary/stop", tags=["api"])
    async def stop_manga_summary_job(data: SummaryControlRequest):
        group_value = (data.groupId or data.mangaTitle or "").strip()
        if not group_value:
            raise HTTPException(400, detail="groupId is required")
        clean_title = (data.mangaTitle or "Ungrouped").strip() or "Ungrouped"
        store = get_store()
        if store is not None:
            resolved_group = await store.resolve_group_id(group_value)
            if resolved_group is None and data.mangaTitle:
                resolved_group = await store.resolve_group_id(data.mangaTitle.strip())
            group_value = resolved_group or group_value
            clean_title = await store.resolve_group_title(group_value) or clean_title

        get_controller().stop(group_value)
        get_controller().stop(clean_title)

        if store is not None:
            await store.dismiss_summary_job(group_value)
        else:
            await asyncio.to_thread(dismiss_summary_job, get_result_root(), clean_title)

        if get_scheduler() is not None:
            get_scheduler().wake()

        summary_log("stopped", clean_title, group_value)
        return {"status": "stopped"}

    @router.post("/results/group/summary", tags=["api"])
    @router.post("/api/results/group/summary", tags=["api"])
    async def create_manga_summary(request: Request, data: MangaSummaryRequest):
        group_value = (data.groupId or data.mangaTitle or "").strip()
        if not group_value:
            raise HTTPException(400, detail="groupId is required")
        clean_title = (data.mangaTitle or "Ungrouped").strip() or "Ungrouped"
        async with submit_lock:
            store = get_store()
            if store is not None:
                resolved_group = await store.resolve_group_id(group_value)
                if resolved_group is None and data.mangaTitle:
                    resolved_group = await store.resolve_group_id(data.mangaTitle.strip())
                group_value = resolved_group or group_value
                clean_title = await store.resolve_group_title(group_value) or clean_title
                pages = await store.group_pages(group_value)
            else:
                pages = await asyncio.to_thread(group_pages, get_result_root(), clean_title)
            if not pages:
                raise HTTPException(404, detail="Manga group not found")

            force_regenerate = data.regenerate or data.refreshText
            status = await summary_status_for(store, group_value, clean_title, pages)
            if status["jobStatus"] in {"queued", "generating", "paused"}:
                if not force_regenerate:
                    summary_log(
                        "skipped",
                        clean_title,
                        group_value,
                        reason=f"already_{status['jobStatus']}",
                        stage=status.get("jobStage"),
                        progress=status.get("jobProgress"),
                    )
                    return status
                else:
                    get_controller().stop(group_value)
                    get_controller().stop(clean_title)
            if status["summary"] and not status["stale"] and not force_regenerate:
                if status.get("jobStatus") != "ready":
                    await update_job_for(
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
                summary_log(
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
                summary_log(
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
            await update_job_for(
                store,
                group_value,
                clean_title,
                "queued",
                None,
                "detecting" if extraction_required else "concatenating",
                0,
                (
                    "Waiting to re-read text from all pages"
                    if data.refreshText
                    else "Waiting for an available worker"
                ),
                0,
                page_count,
                pages_with_text,
                extraction_required,
                summary_provider,
                summary_model,
                refresh_text=data.refreshText,
                regenerate=force_regenerate,
                stage_passed_count=cached_page_count,
            )
            summary_log(
                "queued",
                clean_title,
                group_value,
                pages=page_count,
                missing_pages=missing_page_count,
                cached_pages=cached_page_count,
                regenerate=force_regenerate,
                refresh_text=data.refreshText,
                provider=summary_provider,
                model=summary_model,
            )
            if get_scheduler() is not None:
                get_scheduler().wake()
            else:
                pause_event = get_controller().get_pause_event(group_value)
                pause_event.set()
                summary_task = asyncio.create_task(
                    run_summary_task(
                        request, data, store, group_value, clean_title, worker=None, pause_event=pause_event
                    ),
                    name=f"summary-{group_value}",
                )
                get_controller().register_task(group_value, data, summary_task)
            return await summary_status_for(store, group_value, clean_title, pages)

    return router, (
        dismiss_manga_summary_job,
        pause_manga_summary_job,
        resume_manga_summary_job,
        stop_manga_summary_job,
        create_manga_summary,
    )
