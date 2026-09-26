"""Summary persistence operations behind the PostgresStore facade."""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

from server.manga_summary import synopsis_status
from server.postgres_common import _json_dump, _json_load
from server.series_repository import GroupNotFound


class SummaryRepository:
    def __init__(self, store: Any):
        self._store = store

    @property
    def pool(self) -> Any:
        return self._store.pool

    @property
    def result_root(self) -> Path:
        return self._store.result_root

    async def resolve_group_id(self, value: str) -> str | None:
        return await self._store.resolve_group_id(value)

    async def resolve_group_title(self, record_id: str) -> str | None:
        return await self._store.resolve_group_title(record_id)

    async def group_pages(self, title: str) -> list[dict[str, Any]]:
        return await self._store.group_pages(title)

    async def get_summary_payload(self, group_value: str) -> dict[str, Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(group_value)
        if group_id is None:
            return None
        value = await self.pool.fetchval(
            "SELECT payload FROM manga_summaries WHERE group_id=$1", group_id
        )
        payload = _json_load(value, None)
        return payload if isinstance(payload, dict) else None

    async def save_summary_payload(self, group_value: str, payload: dict[str, Any]) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(group_value)
        if group_id is None:
            raise GroupNotFound("Manga group not found")
        await self.pool.execute(
            """
            INSERT INTO manga_summaries(id,group_id,payload,updated_at)
            VALUES($1,$2,$3::jsonb,now())
            ON CONFLICT(group_id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=now()
            """,
            str(uuid.uuid4()),
            group_id,
            _json_dump(payload),
        )

    async def summary_status(self, group_value: str) -> dict[str, Any] | None:
        group_id = await self.resolve_group_id(group_value)
        if group_id is None:
            return None
        title = await self.resolve_group_title(group_id)
        pages = await self.group_pages(group_id)
        saved = await self.get_summary_payload(group_id)
        status = synopsis_status(self.result_root, title or "Ungrouped", pages, saved=saved)
        status["groupId"] = group_id
        return status

    async def update_summary_job(
        self,
        group_value: str,
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
    ) -> None:
        group_id = await self.resolve_group_id(group_value)
        if group_id is None:
            raise GroupNotFound("Manga group not found")
        title = await self.resolve_group_title(group_id) or "Ungrouped"
        value = await self.get_summary_payload(group_id) or {}
        value.update(
            {
                "mangaTitle": title,
                "jobStatus": status,
                "jobError": error,
                "jobUpdatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        )
        if status in {"queued", "generating", "paused"}:
            value["jobDismissed"] = False
        if stage is not None:
            value["jobStage"] = stage
        if progress is not None:
            value["jobProgress"] = max(0, min(100, progress))
        if message is not None:
            value["jobMessage"] = message
        if current_page is not None:
            value["jobCurrentPage"] = max(0, current_page)
        if stage_passed_count is not None:
            value["jobStagePassedCount"] = max(0, stage_passed_count)
        if page_count is not None:
            value["jobPageCount"] = max(0, page_count)
        if pages_with_text is not None:
            value["jobPagesWithText"] = max(0, pages_with_text)
        if extraction_required is not None:
            value["jobExtractionRequired"] = extraction_required
        if provider is not None:
            value["provider"] = provider
        if model is not None:
            value["model"] = model
        if refresh_text is not None:
            value["jobRefreshText"] = refresh_text
        if regenerate is not None:
            value["jobRegenerate"] = regenerate
        await self.save_summary_payload(group_id, value)

    async def dismiss_summary_job(self, group_value: str) -> None:
        group_id = await self.resolve_group_id(group_value)
        if group_id is None:
            raise GroupNotFound("Manga group not found")
        value = await self.get_summary_payload(group_id) or {}
        value["jobDismissed"] = True
        await self.save_summary_payload(group_id, value)

    async def reconcile_summary_jobs(self) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        rows = await self.pool.fetch(
            """
            SELECT group_id, payload
            FROM manga_summaries
            WHERE payload->>'jobStatus' IN ('generating', 'queued')
            """
        )
        for row in rows:
            payload = _json_load(row["payload"], {})
            if not isinstance(payload, dict):
                continue
            status = payload.get("jobStatus")
            if status == "generating":
                if payload.get("summary"):
                    payload["jobStatus"] = "ready"
                    payload["jobStage"] = "complete"
                    payload["jobProgress"] = 100
                    payload["jobMessage"] = None
                else:
                    payload["jobStatus"] = "queued"
                    payload["jobProgress"] = 0
                    payload["jobMessage"] = "Waiting for an available worker"
                    payload["jobStage"] = (
                        "detecting" if payload.get("jobExtractionRequired", True) else "concatenating"
                    )
                payload["jobUpdatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
                await self.save_summary_payload(str(row["group_id"]), payload)
            elif status == "queued":
                if payload.get("summary") and not payload.get("jobRegenerate") and not payload.get("jobRefreshText"):
                    payload["jobStatus"] = "ready"
                    payload["jobStage"] = "complete"
                    payload["jobProgress"] = 100
                    payload["jobMessage"] = None
                    payload["jobUpdatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
                    await self.save_summary_payload(str(row["group_id"]), payload)

    async def list_runnable_summary_jobs(self) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        rows = await self.pool.fetch(
            """
            SELECT ms.group_id, g.title, ms.payload
            FROM manga_summaries ms
            JOIN manga_groups g ON g.id = ms.group_id
            WHERE COALESCE(ms.payload->>'jobDismissed', 'false') <> 'true'
              AND ms.payload->>'jobStatus' = 'queued'
            ORDER BY ms.updated_at ASC
            """
        )
        runnable: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_load(row["payload"], {})
            if not isinstance(payload, dict):
                continue
            if payload.get("jobStatus") != "queued":
                continue
            if payload.get("summary") and not payload.get("jobRegenerate") and not payload.get("jobRefreshText"):
                continue
            record = {
                "id": f"summary:{row['group_id']}",
                "kind": "summary",
                "groupId": str(row["group_id"]),
                "title": row["title"] or payload.get("mangaTitle") or "Ungrouped",
                "status": "queued",
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "updatedAt": payload.get("jobUpdatedAt") or payload.get("generatedAt"),
                "jobStage": payload.get("jobStage"),
                "jobProgress": payload.get("jobProgress"),
                "jobMessage": payload.get("jobMessage"),
                "jobError": payload.get("jobError"),
                "jobCurrentPage": payload.get("jobCurrentPage"),
                "jobStagePassedCount": payload.get("jobStagePassedCount"),
                "jobPageCount": payload.get("jobPageCount"),
                "jobPagesWithText": payload.get("jobPagesWithText"),
                "jobExtractionRequired": payload.get("jobExtractionRequired"),
                "jobRefreshText": bool(payload.get("jobRefreshText", False)),
                "jobRegenerate": bool(payload.get("jobRegenerate", False)),
                "summaryAvailable": bool(payload.get("summary")),
            }
            runnable.append(record)
        return runnable

    async def list_summary_jobs(self, completed_limit: int = 20) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        rows = await self.pool.fetch(
            """
            SELECT ms.group_id, g.title, ms.payload
            FROM manga_summaries ms
            JOIN manga_groups g ON g.id = ms.group_id
            WHERE COALESCE(ms.payload->>'jobDismissed', 'false') <> 'true'
              AND (
                ms.payload->>'jobStatus' IN ('queued', 'generating', 'paused', 'error')
                OR NULLIF(ms.payload->>'summary', '') IS NOT NULL
              )
            """
        )
        active: list[dict[str, Any]] = []
        completed: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_load(row["payload"], {})
            if not isinstance(payload, dict):
                continue
            status = payload.get("jobStatus")
            if status is None and payload.get("summary"):
                status = "ready"
            if status not in {"queued", "generating", "paused", "ready", "error"}:
                continue
            record = {
                "id": f"summary:{row['group_id']}",
                "kind": "summary",
                "groupId": str(row["group_id"]),
                "title": row["title"] or payload.get("mangaTitle") or "Ungrouped",
                "status": status,
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "updatedAt": payload.get("jobUpdatedAt") or payload.get("generatedAt"),
                "jobStage": payload.get("jobStage"),
                "jobProgress": payload.get("jobProgress"),
                "jobMessage": payload.get("jobMessage"),
                "jobError": payload.get("jobError"),
                "jobCurrentPage": payload.get("jobCurrentPage"),
                "jobStagePassedCount": payload.get("jobStagePassedCount"),
                "jobPageCount": payload.get("jobPageCount"),
                "jobPagesWithText": payload.get("jobPagesWithText"),
                "jobExtractionRequired": payload.get("jobExtractionRequired"),
                "jobRefreshText": bool(payload.get("jobRefreshText", False)),
                "jobRegenerate": bool(payload.get("jobRegenerate", False)),
                "summaryAvailable": bool(payload.get("summary")),
            }
            (completed if status == "ready" else active).append(record)

        active.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        completed.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return active + completed[:max(0, completed_limit)]

