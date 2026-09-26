"""Series persistence operations used by the PostgresStore facade."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any


class SeriesStoreError(Exception):
    pass


class GroupConflict(SeriesStoreError):
    pass


class GroupNotFound(SeriesStoreError):
    pass


class SeriesNotFound(SeriesStoreError):
    pass


class SeriesConflict(SeriesStoreError):
    pass


class InvalidSeries(SeriesStoreError):
    pass


class SeriesRepository:
    def __init__(
        self,
        store: Any,
        iso: Callable[[Any], str],
        natural_sort_key: Callable[[str], str],
    ):
        self._store = store
        self._iso_value = iso
        self._natural_sort_key_value = natural_sort_key

    @property
    def pool(self) -> Any:
        return self._store.pool

    def _page_item(self, row: Any, **kwargs: Any) -> dict[str, Any]:
        return self._store._page_item(row, **kwargs)

    def _iso(self, value: Any) -> str:
        return self._iso_value(value)

    def _natural_sort_key(self, value: str) -> str:
        return self._natural_sort_key_value(value)

    def _series_member_item(self, row: Any) -> dict[str, Any]:
        cover = None
        if row.get("id") is not None:
            cover = self._page_item(row, include_cover=True)
        return {
            "id": row["group_id"],
            "title": row["group_title"],
            "position": int(row["series_position"]),
            "count": int(row["page_count"]),
            "latestFinishedAt": self._iso(row["latest_finished_at"]),
            "cover": cover,
        }

    async def _series_members(self, series_id: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            WITH group_stats AS (
                SELECT g.id AS group_id, g.title AS group_title,
                       g.series_position, count(p.id) AS page_count,
                       max(p.finished_at) AS latest_finished_at
                FROM manga_groups g
                LEFT JOIN pages p ON p.active AND p.manga_group_id = g.id
                WHERE g.series_id = $1
                GROUP BY g.id, g.title, g.series_position
            )
            SELECT stats.*, cover.*
            FROM group_stats stats
            LEFT JOIN LATERAL (
                SELECT p.*
                FROM pages p
                WHERE p.active AND p.manga_group_id = stats.group_id
                ORDER BY p.page_order, p.folder
                LIMIT 1
            ) cover ON TRUE
            WHERE stats.page_count > 0
            ORDER BY stats.series_position, stats.group_id
            """,
            series_id,
        )
        return [self._series_member_item(row) for row in rows]

    async def list_series(
        self,
        limit: int = 12,
        offset: int = 0,
        search: str | None = None,
    ) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        page_size = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        clean_search = search.strip() if search and search.strip() else None
        total = await self.pool.fetchval(
            """
            WITH visible_groups AS (
                SELECT g.id AS group_id, g.series_id
                FROM manga_groups g
                WHERE g.series_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM pages p
                      WHERE p.active AND p.manga_group_id = g.id
                  )
            ), visible_series AS (
                SELECT s.id
                FROM manga_series s
                JOIN visible_groups g ON g.series_id = s.id
                WHERE ($1::text IS NULL OR s.title ILIKE '%' || $1 || '%')
                GROUP BY s.id
                HAVING count(g.group_id) >= 2
            )
            SELECT count(*) FROM visible_series
            """,
            clean_search,
        )
        rows = await self.pool.fetch(
            """
            WITH visible_groups AS (
                SELECT g.id AS group_id, g.series_id, g.series_position, g.title AS group_title
                FROM manga_groups g
                WHERE g.series_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM pages p
                      WHERE p.active AND p.manga_group_id = g.id
                  )
            ), visible_series AS (
                SELECT s.id, s.title, s.updated_at, count(g.group_id) AS member_count
                FROM manga_series s
                JOIN visible_groups g ON g.series_id = s.id
                WHERE ($1::text IS NULL OR s.title ILIKE '%' || $1 || '%')
                GROUP BY s.id, s.title, s.updated_at
                HAVING count(g.group_id) >= 2
            )
            SELECT s.id AS series_id, s.title, s.updated_at AS series_updated_at,
                   s.member_count,
                   cover.*
            FROM visible_series s
            LEFT JOIN LATERAL (
                SELECT g.group_id AS cover_group_id, g.group_title AS cover_group_title,
                       g.series_position, p.*
                FROM visible_groups g
                JOIN LATERAL (
                    SELECT p.*
                    FROM pages p
                    WHERE p.active AND p.manga_group_id = g.group_id
                    ORDER BY p.page_order, p.folder
                    LIMIT 1
                ) p ON TRUE
                WHERE g.series_id = s.id
                ORDER BY g.series_position, g.group_id
                LIMIT 1
            ) cover ON TRUE
            ORDER BY lower(s.title), s.title, s.id
            LIMIT $2 OFFSET $3
            """,
            clean_search,
            page_size,
            offset,
        )
        series = []
        for row in rows:
            cover = self._page_item(row, include_cover=True) if row.get("id") else None
            series.append(
                {
                    "id": row["series_id"],
                    "title": row["title"],
                    "memberCount": int(row["member_count"]),
                    "cover": cover,
                    "firstGroupId": row.get("cover_group_id") or (cover.get("groupId") if cover else None),
                    "updatedAt": self._iso(row["series_updated_at"]),
                }
            )
        total_series = int(total or 0)
        next_offset = offset + len(series) if offset + len(series) < total_series else None
        return {
            "series": series,
            "totalSeries": total_series,
            "nextOffset": next_offset,
        }

    async def get_series(self, series_id: str) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        row = await self.pool.fetchrow(
            "SELECT id, title, created_at, updated_at FROM manga_series WHERE id=$1",
            series_id,
        )
        if row is None:
            raise SeriesNotFound("Series not found")
        members = await self._series_members(series_id)
        return {
            "id": row["id"],
            "title": row["title"],
            "members": members,
            "memberCount": len(members),
            "cover": members[0]["cover"] if members else None,
            "firstGroupId": members[0]["id"] if members else None,
            "updatedAt": self._iso(row["updated_at"]),
        }

    async def get_series_for_group(self, group_id: str) -> dict[str, Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        series_id = await self.pool.fetchval(
            "SELECT series_id FROM manga_groups WHERE id=$1 OR title=$1 LIMIT 1",
            group_id,
        )
        return await self.get_series(series_id) if series_id else None

    async def create_series(self, title: str, group_ids: list[str]) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        clean_title = self._store._clean_series_title(title)
        group_ids = self._store._validate_group_ids(group_ids)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                duplicate = await connection.fetchval(
                    "SELECT id FROM manga_series WHERE lower(btrim(title))=lower(btrim($1))",
                    clean_title,
                )
                if duplicate:
                    raise SeriesConflict("A series with this title already exists")
                groups = await connection.fetch(
                    """
                    SELECT g.id, g.title, g.series_id
                    FROM manga_groups g
                    WHERE g.id=ANY($1::text[])
                      AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
                    FOR UPDATE
                    """,
                    group_ids,
                )
                if len(groups) != len(group_ids):
                    raise InvalidSeries("One or more manga groups were not found")
                if any(row["series_id"] for row in groups):
                    raise SeriesConflict("A manga group already belongs to a series")
                ordered = sorted(groups, key=lambda row: (self._natural_sort_key(row["title"]), row["id"]))
                series_id = str(uuid.uuid4())
                await connection.execute(
                    "INSERT INTO manga_series(id,title) VALUES($1,$2)",
                    series_id,
                    clean_title,
                )
                await connection.executemany(
                    """
                    UPDATE manga_groups
                    SET series_id=$1, series_position=$2, updated_at=now()
                    WHERE id=$3
                    """,
                    [(series_id, index, row["id"]) for index, row in enumerate(ordered, start=1)],
                )
        return await self.get_series(series_id)

    async def update_series_title(self, series_id: str, title: str) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        clean_title = self._store._clean_series_title(title)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                exists = await connection.fetchval(
                    "SELECT 1 FROM manga_series WHERE id=$1 FOR UPDATE", series_id
                )
                if not exists:
                    raise SeriesNotFound("Series not found")
                duplicate = await connection.fetchval(
                    """
                    SELECT 1 FROM manga_series
                    WHERE id<>$1 AND lower(btrim(title))=lower(btrim($2))
                    """,
                    series_id,
                    clean_title,
                )
                if duplicate:
                    raise SeriesConflict("A series with this title already exists")
                await connection.execute(
                    "UPDATE manga_series SET title=$2, updated_at=now() WHERE id=$1",
                    series_id,
                    clean_title,
                )
        return await self.get_series(series_id)

    async def replace_series_members(self, series_id: str, group_ids: list[str]) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_ids = self._store._validate_group_ids(group_ids)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if not await connection.fetchval(
                    "SELECT 1 FROM manga_series WHERE id=$1 FOR UPDATE", series_id
                ):
                    raise SeriesNotFound("Series not found")
                await connection.fetch(
                    "SELECT id FROM manga_groups WHERE series_id=$1 FOR UPDATE", series_id
                )
                groups = await connection.fetch(
                    """
                    SELECT g.id, g.series_id
                    FROM manga_groups g
                    WHERE g.id=ANY($1::text[])
                      AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
                    FOR UPDATE
                    """,
                    group_ids,
                )
                if len(groups) != len(group_ids):
                    raise InvalidSeries("One or more manga groups were not found")
                if any(row["series_id"] not in (None, series_id) for row in groups):
                    raise SeriesConflict("A manga group already belongs to another series")
                await connection.execute(
                    """
                    UPDATE manga_groups
                    SET series_id=NULL, series_position=NULL, updated_at=now()
                    WHERE series_id=$1
                    """,
                    series_id,
                )
                await connection.executemany(
                    """
                    UPDATE manga_groups
                    SET series_id=$1, series_position=$2, updated_at=now()
                    WHERE id=$3
                    """,
                    [(series_id, index, group_id) for index, group_id in enumerate(group_ids, start=1)],
                )
                await connection.execute(
                    "UPDATE manga_series SET updated_at=now() WHERE id=$1", series_id
                )
        return await self.get_series(series_id)

    async def delete_series(self, series_id: str) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if not await connection.fetchval(
                    "SELECT 1 FROM manga_series WHERE id=$1 FOR UPDATE", series_id
                ):
                    raise SeriesNotFound("Series not found")
                await connection.execute(
                    "UPDATE manga_groups SET series_id=NULL, series_position=NULL, updated_at=now() WHERE series_id=$1",
                    series_id,
                )
                await connection.execute("DELETE FROM manga_series WHERE id=$1", series_id)

    async def add_manga_to_series(self, series_id: str, group_ids: list[str]) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        if not group_ids:
            raise InvalidSeries("At least one manga group is required")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if not await connection.fetchval(
                    "SELECT 1 FROM manga_series WHERE id=$1 FOR UPDATE", series_id
                ):
                    raise SeriesNotFound("Series not found")

                target_groups = await connection.fetch(
                    """
                    SELECT g.id, g.series_id, g.title
                    FROM manga_groups g
                    WHERE g.id = ANY($1::text[])
                      AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id = g.id)
                    FOR UPDATE
                    """,
                    group_ids,
                )
                if len(target_groups) != len(set(group_ids)):
                    raise InvalidSeries("One or more manga groups were not found")

                affected_prev_series = set()
                for row in target_groups:
                    prev_id = row["series_id"]
                    if prev_id and prev_id != series_id:
                        affected_prev_series.add(prev_id)

                existing_members = await connection.fetch(
                    """
                    SELECT id FROM manga_groups
                    WHERE series_id = $1
                    ORDER BY series_position, id
                    FOR UPDATE
                    """,
                    series_id,
                )
                existing_member_ids = [row["id"] for row in existing_members]

                await connection.execute(
                    """
                    UPDATE manga_groups
                    SET series_id = NULL, series_position = NULL, updated_at = now()
                    WHERE id = ANY($1::text[])
                    """,
                    group_ids,
                )

                for prev_id in affected_prev_series:
                    remaining = await connection.fetch(
                        """
                        SELECT id FROM manga_groups
                        WHERE series_id = $1
                        ORDER BY series_position, id
                        FOR UPDATE
                        """,
                        prev_id,
                    )
                    if len(remaining) < 2:
                        await connection.execute(
                            """
                            UPDATE manga_groups
                            SET series_id = NULL, series_position = NULL, updated_at = now()
                            WHERE series_id = $1
                            """,
                            prev_id,
                        )
                        await connection.execute("DELETE FROM manga_series WHERE id = $1", prev_id)
                    else:
                        await connection.executemany(
                            """
                            UPDATE manga_groups
                            SET series_position = $2, updated_at = now()
                            WHERE id = $1
                            """,
                            [(row["id"], idx) for idx, row in enumerate(remaining, start=1)],
                        )
                        await connection.execute("UPDATE manga_series SET updated_at = now() WHERE id = $1", prev_id)

                final_member_ids = [m_id for m_id in existing_member_ids if m_id not in group_ids]
                for g_id in group_ids:
                    if g_id not in final_member_ids:
                        final_member_ids.append(g_id)

                if len(final_member_ids) < 2:
                    raise InvalidSeries("A series needs at least two manga groups")

                await connection.executemany(
                    """
                    UPDATE manga_groups
                    SET series_id = $1, series_position = $2, updated_at = now()
                    WHERE id = $3
                    """,
                    [(series_id, idx, g_id) for idx, g_id in enumerate(final_member_ids, start=1)],
                )
                await connection.execute("UPDATE manga_series SET updated_at = now() WHERE id = $1", series_id)

        return await self.get_series(series_id)

    async def move_manga_to_series(self, group_id: str, target_series_id: str) -> dict[str, Any]:
        return await self.add_manga_to_series(target_series_id, [group_id])

    async def remove_manga_from_series(self, group_id: str) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT series_id FROM manga_groups WHERE id = $1 FOR UPDATE", group_id
                )
                if not row or not row["series_id"]:
                    return
                series_id = row["series_id"]

                await connection.execute(
                    "UPDATE manga_groups SET series_id = NULL, series_position = NULL, updated_at = now() WHERE id = $1",
                    group_id,
                )

                remaining = await connection.fetch(
                    "SELECT id FROM manga_groups WHERE series_id = $1 ORDER BY series_position, id FOR UPDATE",
                    series_id,
                )
                if len(remaining) < 2:
                    await connection.execute(
                        "UPDATE manga_groups SET series_id = NULL, series_position = NULL, updated_at = now() WHERE series_id = $1",
                        series_id,
                    )
                    await connection.execute("DELETE FROM manga_series WHERE id = $1", series_id)
                else:
                    await connection.executemany(
                        "UPDATE manga_groups SET series_position = $2, updated_at = now() WHERE id = $1",
                        [(r["id"], idx) for idx, r in enumerate(remaining, start=1)],
                    )
                    await connection.execute("UPDATE manga_series SET updated_at = now() WHERE id = $1", series_id)
