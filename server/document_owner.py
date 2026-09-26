"""Resolve the persisted owner of a virtual result document."""

from __future__ import annotations

from typing import Any

from server.postgres_common import _safe_folder


async def resolve_document_owner(
    store: Any, value: str, connection: Any | None = None
) -> tuple[str, str] | None:
    if store.pool is None:
        raise RuntimeError("PostgreSQL store is not started")
    value = _safe_folder(value)
    executor = connection if connection is not None else store.pool
    row = await executor.fetchrow(
        """
        SELECT 'page' AS kind, p.id
        FROM pages p
        WHERE p.active AND (p.id=$1 OR p.folder=$1)
        UNION ALL
        SELECT 'pipeline' AS kind, pr.id
        FROM pipeline_runs pr
        WHERE pr.id=$1 OR pr.folder=$1
        LIMIT 1
        """,
        value,
    )
    return (row["kind"], row["id"]) if row else None
