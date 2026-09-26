"""Page ordering and manga mutation queries behind the PostgresStore facade."""

from __future__ import annotations

import uuid
from typing import Any

from server.postgres_common import _json_dump, _json_load, _safe_folder
from server.series_repository import GroupConflict, GroupNotFound


class MangaMutationRepository:
    def __init__(self, store: Any):
        self._store = store

    @property
    def pool(self) -> Any:
        return self._store.pool

    async def resolve_group_id(self, value: str) -> str | None:
        return await self._store.resolve_group_id(value)

    async def reorder_pages(self, group_id: str, page_ids: list[str]) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        if not page_ids or len(set(page_ids)) != len(page_ids):
            raise ValueError("pageIds must contain every page exactly once")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if await connection.fetchval(
                    "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                ) is None:
                    raise GroupNotFound("Manga group not found")
                rows = await connection.fetch(
                    """
                    SELECT id
                    FROM pages
                    WHERE active AND manga_group_id=$1
                    FOR UPDATE
                    """,
                    group_id,
                )
                expected = {row["id"] for row in rows}
                if expected != set(page_ids):
                    raise ValueError("pageIds must contain every active page in the manga group")
                await connection.execute(
                    """
                    WITH ordered AS (
                        SELECT id, ROW_NUMBER() OVER (ORDER BY page_order, folder)::INTEGER AS position
                        FROM pages
                        WHERE active AND manga_group_id=$1
                    )
                    UPDATE pages AS p
                    SET page_order=-ordered.position
                    FROM ordered
                    WHERE p.id=ordered.id
                    """,
                    group_id,
                )
                await connection.executemany(
                    "UPDATE pages SET page_order=$2, updated_at=now() WHERE id=$1",
                    [(page_id, index) for index, page_id in enumerate(page_ids, start=1)],
                )
                return [
                    {"id": page_id, "pageOrder": index}
                    for index, page_id in enumerate(page_ids, start=1)
                ]

    async def compact_page_order(self, group_id: str) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.fetchrow(
                    "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                )
                await self._compact_page_order(connection, group_id)

    @staticmethod
    async def _compact_page_order(connection: Any, group_id: str) -> None:
        pages = await connection.fetch(
            """
            SELECT id
            FROM pages
            WHERE active AND manga_group_id=$1
            ORDER BY page_order, folder
            FOR UPDATE
            """,
            group_id,
        )
        if not pages:
            return
        reserved_rows = await connection.fetch(
            """
            SELECT DISTINCT bi.page_order
            FROM batch_items bi
            JOIN batches b ON b.id=bi.batch_id
            WHERE bi.manga_group_id=$1
              AND bi.page_order IS NOT NULL
              AND bi.page_order > 0
              AND b.active AND NOT b.dismissed
              AND bi.status IN ('queued', 'processing', 'error')
            """,
            group_id,
        )
        reserved = {int(row["page_order"]) for row in reserved_rows}
        next_order = 1
        assignments: list[tuple[str, int]] = []
        for page in pages:
            while next_order in reserved:
                next_order += 1
            assignments.append((page["id"], next_order))
            next_order += 1

        await connection.executemany(
            "UPDATE pages SET page_order=$2, updated_at=now() WHERE id=$1",
            [(page_id, -index) for index, (page_id, _) in enumerate(assignments, start=1)],
        )
        await connection.executemany(
            "UPDATE pages SET page_order=$2, updated_at=now() WHERE id=$1",
            assignments,
        )

    @staticmethod
    async def _next_page_order(connection: Any, group_id: str) -> int:
        return int(
            await connection.fetchval(
                """
                SELECT GREATEST(
                    COALESCE((
                        SELECT MAX(page_order)
                        FROM pages
                        WHERE active AND manga_group_id=$1
                    ), 0),
                    COALESCE((
                        SELECT MAX(bi.page_order)
                        FROM batch_items bi
                        JOIN batches b ON b.id=bi.batch_id
                        WHERE bi.manga_group_id=$1
                          AND bi.page_order IS NOT NULL
                          AND b.active AND NOT b.dismissed
                          AND bi.status IN ('queued', 'processing', 'error')
                    ), 0)
                ) + 1
                """,
                group_id,
            )
        )

    async def update_meta(
        self,
        folders: list[str] | None,
        old_title: str | None,
        new_title: str,
    ) -> int:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        clean_title = new_title.strip() or "Ungrouped"
        old_group_id = await self.resolve_group_id(old_title) if old_title is not None else None
        record_values = [str(folder) for folder in (folders or [])]
        condition = []
        args: list[Any] = []
        if record_values:
            placeholder = len(args) + 1
            condition.append(f"(id = ANY(${placeholder}::text[]) OR folder = ANY(${placeholder}::text[]))")
            args.append(record_values)
        if old_title is not None:
            if old_group_id is None:
                return 0
            condition.append(f"manga_group_id=${len(args) + 1}")
            args.append(old_group_id)
        if not condition:
            return 0
        rows = await self.pool.fetch(
            f"SELECT id, manga_group_id, page_order, metadata FROM pages WHERE active AND ({' OR '.join(condition)})",
            *args,
        )
        if not rows:
            return 0
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                target_group_id = await connection.fetchval(
                    "SELECT id FROM manga_groups WHERE title=$1", clean_title
                )
                if target_group_id is None:
                    target_group_id = await connection.fetchval(
                        "INSERT INTO manga_groups(id,title) VALUES($1,$2) RETURNING id",
                        str(uuid.uuid4()),
                        clean_title,
                    )
                source_group_ids = {row["manga_group_id"] for row in rows}
                for locked_group_id in sorted(source_group_ids | {target_group_id}):
                    await connection.fetchrow(
                        "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", locked_group_id
                    )
                moving_rows = sorted(
                    (row for row in rows if row["manga_group_id"] != target_group_id),
                    key=lambda row: (row["page_order"], row["id"]),
                )
                moving_ids = {row["id"] for row in moving_rows}
                next_order = await self._next_page_order(connection, target_group_id)
                update_rows = []
                for row in sorted(
                    rows,
                    key=lambda row: (
                        row["manga_group_id"] == target_group_id,
                        row["page_order"],
                        row["id"],
                    ),
                ):
                    metadata = _json_load(row["metadata"], {})
                    metadata["mangaTitle"] = clean_title
                    metadata["mangaGroupId"] = target_group_id
                    metadata["groupId"] = target_group_id
                    assigned_order = next_order if row["id"] in moving_ids else row["page_order"]
                    if row["id"] in moving_ids:
                        next_order += 1
                    update_rows.append(
                        (target_group_id, assigned_order, _json_dump(metadata), row["id"])
                    )
                await connection.executemany(
                    """
                    UPDATE pages SET manga_group_id=$1, page_order=$2, metadata=$3::jsonb,
                        updated_at=now() WHERE id=$4
                    """,
                    update_rows,
                )
                for source_group_id in sorted(source_group_ids - {target_group_id}):
                    await self._compact_page_order(connection, source_group_id)
        return len(rows)

    async def rename_group(self, group_id: str, new_title: str) -> tuple[str, int]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        clean_title = (new_title or "").strip() or "Ungrouped"
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT id, title FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                )
                if row is None:
                    raise GroupNotFound("Manga group not found")
                duplicate = await connection.fetchval(
                    "SELECT 1 FROM manga_groups WHERE id<>$1 AND title=$2",
                    group_id,
                    clean_title,
                )
                if duplicate:
                    raise GroupConflict("A manga group with this title already exists")
                old_title = row["title"]
                if old_title == clean_title:
                    count = await connection.fetchval(
                        "SELECT count(*) FROM pages WHERE active AND manga_group_id=$1", group_id
                    )
                    return old_title, int(count or 0)
                count = await connection.fetchval(
                    "SELECT count(*) FROM pages WHERE active AND manga_group_id=$1", group_id
                )
                await connection.execute(
                    "UPDATE manga_groups SET title=$2, updated_at=now() WHERE id=$1",
                    group_id,
                    clean_title,
                )
                await connection.execute(
                    """
                    UPDATE pages
                    SET metadata=jsonb_set(metadata, '{mangaTitle}', to_jsonb($2::text), TRUE),
                        updated_at=now()
                    WHERE active AND manga_group_id=$1
                    """,
                    group_id,
                    clean_title,
                )
        return old_title, int(count or 0)

    async def delete_results(self, folders: list[str]) -> list[str]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        safe_folders = list(dict.fromkeys(_safe_folder(str(folder)) for folder in folders))
        if not safe_folders:
            return []
        async with self.pool.acquire() as connection:
            while True:
                retry = False
                async with connection.transaction():
                    group_ids = [
                        row["group_id"]
                        for row in await connection.fetch(
                            """SELECT DISTINCT manga_group_id AS group_id FROM pages
                               WHERE active AND folder=ANY($1::text[])
                               ORDER BY manga_group_id""",
                            safe_folders,
                        )
                    ]
                    if not group_ids:
                        return []
                    for group_id in group_ids:
                        await connection.fetchval(
                            "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                        )

                    pages = await connection.fetch(
                        """
                        SELECT p.id, p.folder, p.manga_group_id AS group_id, g.series_id
                        FROM pages p
                        JOIN manga_groups g ON g.id=p.manga_group_id
                        WHERE p.active AND p.folder=ANY($1::text[])
                        ORDER BY g.id, p.id
                        FOR UPDATE OF p
                        """,
                        safe_folders,
                    )
                    page_group_ids = {page["group_id"] for page in pages}
                    if not page_group_ids.issubset(group_ids):
                        retry = True
                    elif pages:
                        group_ids = sorted(page_group_ids)
                        await connection.execute(
                            "DELETE FROM pages WHERE id=ANY($1::text[])",
                            [page["id"] for page in pages],
                        )
                        series_ids = sorted({page["series_id"] for page in pages if page["series_id"]})
                        for group_id in group_ids:
                            await self._compact_page_order(connection, group_id)

                        remaining_groups = {
                            row["id"]
                            for row in await connection.fetch(
                                """
                                SELECT g.id FROM manga_groups g
                                WHERE g.id=ANY($1::text[])
                                  AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
                                """,
                                group_ids,
                            )
                        }
                        empty_group_ids = [group_id for group_id in group_ids if group_id not in remaining_groups]
                        if empty_group_ids:
                            await connection.execute(
                                "DELETE FROM manga_groups WHERE id=ANY($1::text[])", empty_group_ids
                            )

                        if series_ids:
                            series_counts = await connection.fetch(
                                """
                                SELECT s.id, count(g.id) AS group_count
                                FROM manga_series s
                                LEFT JOIN manga_groups g ON g.series_id=s.id
                                  AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
                                WHERE s.id=ANY($1::text[])
                                GROUP BY s.id
                                """,
                                series_ids,
                            )
                            empty_series_ids = [row["id"] for row in series_counts if int(row["group_count"]) < 2]
                            if empty_series_ids:
                                await connection.execute(
                                    "UPDATE manga_groups SET series_id=NULL, series_position=NULL WHERE series_id=ANY($1::text[])",
                                    empty_series_ids,
                                )
                                await connection.execute(
                                    "DELETE FROM manga_series WHERE id=ANY($1::text[])", empty_series_ids
                                )
                        deleted_folders = [page["folder"] for page in pages]
                if not retry:
                    return deleted_folders if pages else []

    async def delete_result(self, folder: str) -> bool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        record_id = _safe_folder(str(folder))
        async with self.pool.acquire() as connection:
            while True:
                retry = False
                async with connection.transaction():
                    group_id = await connection.fetchval(
                        "SELECT manga_group_id FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1",
                        record_id,
                    )
                    if group_id is None:
                        return False
                    if await connection.fetchval(
                        "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                    ) is None:
                        retry = True
                    else:
                        page = await connection.fetchrow(
                            """
                            SELECT p.id, g.id AS group_id, g.series_id
                            FROM pages p
                            JOIN manga_groups g ON g.id=p.manga_group_id
                            WHERE p.active AND (p.id=$1 OR p.folder=$1)
                            FOR UPDATE OF p
                            """,
                            record_id,
                        )
                        if page is None:
                            return False
                        if page["group_id"] != group_id:
                            retry = True
                        else:
                            result = await connection.execute(
                                "DELETE FROM pages WHERE id=$1", page["id"]
                            )
                            await self._compact_page_order(connection, group_id)
                            if not await connection.fetchval(
                                "SELECT 1 FROM pages WHERE active AND manga_group_id=$1 LIMIT 1",
                                group_id,
                            ):
                                await connection.execute(
                                    "DELETE FROM manga_groups WHERE id=$1", group_id
                                )
                            if page["series_id"]:
                                remaining = await connection.fetchval(
                                    """
                                    SELECT count(*) FROM manga_groups g
                                    WHERE g.series_id=$1
                                      AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
                                    """,
                                    page["series_id"],
                                )
                                if int(remaining or 0) < 2:
                                    await connection.execute(
                                        "UPDATE manga_groups SET series_id=NULL, series_position=NULL WHERE series_id=$1",
                                        page["series_id"],
                                    )
                                    await connection.execute(
                                        "DELETE FROM manga_series WHERE id=$1", page["series_id"]
                                    )
                            deleted = result.endswith("1")
                if not retry:
                    return deleted

    async def delete_group(self, title: str) -> list[str]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(title)
        if group_id is None:
            return []
        rows = await self.pool.fetch("SELECT folder FROM pages WHERE manga_group_id=$1 AND active", group_id)
        folders = [row["folder"] for row in rows]
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                group = await connection.fetchrow(
                    "SELECT id, series_id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                )
                await connection.execute("DELETE FROM pages WHERE manga_group_id=$1", group_id)
                if group:
                    await connection.execute("DELETE FROM manga_groups WHERE id=$1", group["id"])
                    if group["series_id"]:
                        remaining = await connection.fetchval(
                            """
                            SELECT count(*) FROM manga_groups g
                            WHERE g.series_id=$1
                              AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
                            """,
                            group["series_id"],
                        )
                        if int(remaining or 0) < 2:
                            await connection.execute(
                                "UPDATE manga_groups SET series_id=NULL, series_position=NULL WHERE series_id=$1",
                                group["series_id"],
                            )
                            await connection.execute(
                                "DELETE FROM manga_series WHERE id=$1", group["series_id"]
                            )
        return folders

    async def clear_results(self) -> int:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        count = await self.pool.fetchval("SELECT count(*) FROM pages WHERE active")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("DELETE FROM result_documents")
                await connection.execute("DELETE FROM pages")
                await connection.execute("DELETE FROM pipeline_runs")
        return int(count)

