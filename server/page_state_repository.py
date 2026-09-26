"""Page identity, text-region, and review-state queries for PostgresStore."""

from __future__ import annotations

import uuid
from typing import Any, Callable

from server.postgres_common import _json_dump, _json_load


class PageStateRepository:
    def __init__(self, store: Any, manga_id: Callable[[str], str]):
        self._store = store
        self._manga_id = manga_id

    @property
    def pool(self) -> Any:
        return self._store.pool

    async def get_page_id(self, page_ref: str) -> str | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        return await self.pool.fetchval(
            "SELECT id FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1",
            page_ref,
        )

    async def resolve_group_id(self, value: str, *, create: bool = False) -> str | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        value = str(value or "").strip()
        if not value:
            return None
        group_id = await self.pool.fetchval("SELECT id FROM manga_groups WHERE id=$1", value)
        if group_id is not None:
            return group_id
        group_id = await self.pool.fetchval("SELECT id FROM manga_groups WHERE title=$1", value)
        if group_id is not None:
            return group_id
        if value.startswith("manga-"):
            rows = await self.pool.fetch("SELECT id, title FROM manga_groups")
            for row in rows:
                if self._manga_id(row["title"]) == value:
                    return row["id"]
        if not create:
            return None
        await self.pool.execute(
            "INSERT INTO manga_groups(id,title) VALUES($1,$2) ON CONFLICT(title) DO NOTHING",
            str(uuid.uuid4()),
            value,
        )
        return await self.pool.fetchval("SELECT id FROM manga_groups WHERE title=$1", value)

    async def get_text_regions(self, record_id: str) -> list[Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        value = await self.pool.fetchval(
            "SELECT text_regions FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1",
            record_id,
        )
        return _json_load(value, []) if value is not None else None

    async def update_text_regions(
        self, record_id: str, regions: list[dict[str, Any]]
    ) -> bool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        result = await self.pool.execute(
            """
            UPDATE pages SET text_regions=$1::jsonb, has_regions=$2, updated_at=now()
            WHERE active AND (id=$3 OR folder=$3)
            """,
            _json_dump(regions),
            bool(regions),
            record_id,
        )
        return result == "UPDATE 1"

    async def update_review_status(
        self, record_id: str, status: str, reviewed_at: str | None
    ) -> bool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        if reviewed_at is None:
            result = await self.pool.execute(
                """
                UPDATE pages
                SET metadata=jsonb_set(COALESCE(metadata, '{}'::jsonb) - 'reviewedAt', '{reviewStatus}', to_jsonb($1::text), true),
                    updated_at=now()
                WHERE active AND (id=$2 OR folder=$2)
                """,
                status,
                record_id,
            )
        else:
            result = await self.pool.execute(
                """
                UPDATE pages
                SET metadata=jsonb_set(
                        jsonb_set(COALESCE(metadata, '{}'::jsonb), '{reviewStatus}', to_jsonb($1::text), true),
                        '{reviewedAt}', to_jsonb($2::text), true
                    ),
                    updated_at=now()
                WHERE active AND (id=$3 OR folder=$3)
                """,
                status,
                reviewed_at,
                record_id,
            )
        return result == "UPDATE 1"

    async def find_request(self, request_id: str) -> str | None:
        if self.pool is None:
            return None
        return await self.pool.fetchval(
            "SELECT folder FROM pages WHERE active AND request_id=$1 ORDER BY updated_at DESC LIMIT 1",
            request_id,
        )

    async def resolve_folder(self, record_id: str) -> str | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        row = await self.pool.fetchrow(
            "SELECT folder FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1",
            record_id,
        )
        return row["folder"] if row else None

    async def group_exists(self, title: str) -> bool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self._store.resolve_group_id(title)
        return bool(
            group_id
            and await self.pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pages WHERE active AND manga_group_id=$1)",
                group_id,
            )
        )

    async def resolve_group_title(self, record_id: str) -> str | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self._store.resolve_group_id(record_id)
        row = await self.pool.fetchrow(
            "SELECT title FROM manga_groups WHERE id=$1", group_id
        ) if group_id else None
        return row["title"] if row else None
