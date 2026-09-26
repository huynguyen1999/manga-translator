"""PostgreSQL persistence for batch manifests and their page reservations."""

import logging
import uuid
from typing import Any

from server.postgres_common import _json_dump, _page_order, _safe_folder

logger = logging.getLogger("manga-translator.postgres")


async def save_batch_manifest(
    store, manifest: dict[str, Any], connection: Any = None
) -> None:
    async with store._batch_connection(connection) as connection:
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO batches(
                    id,title,status,dismissed,added_at,updated_at,
                    total_items,completed_count,manifest,snapshot_path,active
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,TRUE)
                ON CONFLICT(id) DO UPDATE SET
                    title=EXCLUDED.title,
                    status=EXCLUDED.status,
                    dismissed=EXCLUDED.dismissed,
                    added_at=EXCLUDED.added_at,
                    updated_at=EXCLUDED.updated_at,
                    total_items=EXCLUDED.total_items,
                    completed_count=EXCLUDED.completed_count,
                    manifest=EXCLUDED.manifest,
                    snapshot_path=EXCLUDED.snapshot_path,
                    active=TRUE
                """,
                manifest["id"],
                manifest["title"],
                manifest["status"],
                bool(manifest.get("dismissed", False)),
                int(manifest.get("addedAt", 0)),
                int(manifest.get("updatedAt", 0)),
                int(manifest.get("totalItems", len(manifest.get("items", [])))),
                int(manifest.get("completedCount", 0)),
                _json_dump(manifest),
                str(store._manifest_path(manifest["id"])),
            )
            reservations = {
                row["id"]: row
                for row in await connection.fetch(
                    "SELECT id, manga_group_id, page_order FROM batch_items WHERE batch_id=$1",
                    manifest["id"],
                )
            }
            await connection.execute("DELETE FROM batch_items WHERE batch_id=$1", manifest["id"])
            prepared_items: list[tuple[dict[str, Any], str, str | None, int | None]] = []
            for item in manifest.get("items", []):
                item_title = item.get("mangaTitle", manifest["mangaTitle"])
                group_id = await connection.fetchval(
                    "SELECT id FROM manga_groups WHERE id=$1", item.get("mangaGroupId")
                )
                if group_id is None:
                    group_id = await connection.fetchval(
                        "SELECT id FROM manga_groups WHERE title=$1", item_title
                    )
                if group_id is None:
                    group_id = await connection.fetchval(
                        "INSERT INTO manga_groups(id,title) VALUES($1,$2) RETURNING id",
                        str(uuid.uuid4()),
                        item_title,
                    )
                page_value = item.get("pageId") or item.get("resultFolder")
                page_id = None
                page = None
                if page_value:
                    page = await connection.fetchrow(
                        """
                        SELECT p.id, p.manga_group_id, p.page_order, g.title AS manga_title
                        FROM pages p
                        JOIN manga_groups g ON g.id=p.manga_group_id
                        WHERE p.active AND (p.id=$1 OR p.folder=$1)
                        LIMIT 1
                        """,
                        str(page_value),
                    )
                if page is not None:
                        if page["manga_group_id"] != group_id:
                            logger.warning(
                                "Repairing stale batch item group for page %s: %s -> %s",
                                page["id"],
                                group_id,
                                page["manga_group_id"],
                            )
                            group_id = page["manga_group_id"]
                            item_title = page["manga_title"]
                        page_id = page["id"]
                        item["pageOrder"] = (
                            page.get("page_order")
                            if hasattr(page, "get")
                            else page["page_order"]
                        )
                else:
                    # Progress writes must keep the reservation used by the running job.
                    # Only persisted positions are trusted, never client-supplied ones.
                    reservation = reservations.get(item["id"])
                    item["pageOrder"] = (
                        reservation["page_order"]
                        if reservation and reservation["manga_group_id"] == group_id
                        else None
                    )
                item["mangaTitle"] = item_title
                item["mangaGroupId"] = group_id
                item["pageId"] = page_id
                prepared_items.append((item, group_id, page_id, _page_order(item.get("pageOrder"))))

            by_group: dict[str, list[dict[str, Any]]] = {}
            for item, group_id, _page_id, page_order in prepared_items:
                if page_order is None:
                    by_group.setdefault(group_id, []).append(item)
            for group_id, missing_items in sorted(by_group.items()):
                await connection.fetchrow(
                    "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                )
                current_max = int(
                    await connection.fetchval(
                        """
                        SELECT GREATEST(
                            COALESCE((
                                SELECT MAX(page_order) FROM pages
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
                            ), 0),
                            $2
                        )
                        """,
                        group_id,
                        max(
                            (_page_order(item.get("pageOrder")) or 0)
                            for item in manifest.get("items", [])
                            if item.get("mangaGroupId") == group_id
                        ),
                    )
                )
                for item in missing_items:
                    current_max += 1
                    item["pageOrder"] = current_max

            item_rows = []
            for item, group_id, page_id, page_order in prepared_items:
                page_order = _page_order(item.get("pageOrder")) or page_order
                item_rows.append(
                    (
                        manifest["id"],
                        item["id"],
                        item["name"],
                        group_id,
                        page_id,
                        item.get("status", "queued"),
                        item.get("stage"),
                        item.get("stageStartedAt"),
                        item.get("error"),
                        item.get("requestId"),
                        page_order,
                        _json_dump(item),
                    )
                )
            if not manifest.get("mangaGroupId") and prepared_items:
                manifest["mangaGroupId"] = prepared_items[0][1]
            await connection.execute(
                "UPDATE batches SET manifest=$2::jsonb WHERE id=$1",
                manifest["id"],
                _json_dump(manifest),
            )
            await connection.executemany(
                """
                INSERT INTO batch_items(
                    batch_id,id,name,manga_group_id,page_id,status,stage,stage_started_at,
                    error,request_id,page_order,payload
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
                ON CONFLICT (batch_id, id) DO UPDATE SET
                    name = EXCLUDED.name,
                    manga_group_id = EXCLUDED.manga_group_id,
                    page_id = EXCLUDED.page_id,
                    status = EXCLUDED.status,
                    stage = EXCLUDED.stage,
                    stage_started_at = EXCLUDED.stage_started_at,
                    error = EXCLUDED.error,
                    request_id = EXCLUDED.request_id,
                    page_order = EXCLUDED.page_order,
                    payload = EXCLUDED.payload
                """,
                item_rows,
            )
