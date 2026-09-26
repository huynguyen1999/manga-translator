"""Manga, page, and reading-progress operations used by the PostgresStore facade."""

from __future__ import annotations

from collections.abc import Callable
import datetime as dt
import uuid
from pathlib import Path
from typing import Any

from server.image_variants import final_file
from server.postgres_common import _json_load
from server.series_repository import GroupNotFound


class MangaRepository:
    def __init__(self, store: Any, iso: Callable[[Any], str], parse_finished_at: Callable[..., Any]):
        self._store = store
        self._iso_value = iso
        self._parse_finished_at_value = parse_finished_at

    @property
    def pool(self) -> Any:
        return self._store.pool

    @property
    def result_root(self) -> Path:
        return self._store.result_root

    def _iso(self, value: Any) -> str:
        return self._iso_value(value)

    async def resolve_group_id(self, value: str) -> str | None:
        return await self._store.resolve_group_id(value)

    def _page_item(self, row: Any, slim: bool = False, include_cover: bool = False) -> dict[str, Any]:
        metadata = _json_load(row["metadata"], {})
        try:
            version = row["asset_version"]
        except (KeyError, IndexError):
            version = 0
        suffix = f"?v={version}" if version else ""
        base = f"/result/{row['id']}"
        final_name = row.get("final_name", "final.png") if hasattr(row, "get") else row["final_name"]
        group_id = (row.get("group_id") or row.get("manga_group_id")) if hasattr(row, "get") else None
        manga_title = (row.get("manga_title") or row.get("group_title", "")) if hasattr(row, "get") else ""
        page_order = row.get("page_order") if hasattr(row, "get") else row["page_order"]
        full_url = f"{base}/{final_name}{suffix}"
        item = {
            "id": row["id"],
            "legacyId": metadata.get("id"),
            "folder": row["folder"],
            "originalName": row["original_name"] if row["original_name"] and row["original_name"] != "Unknown" else f"{row['folder']}.png",
            "pageOrder": page_order,
            "sourcePath": metadata.get("sourcePath"),
            "groupId": group_id,
            "mangaTitle": manga_title,
            "resultUrl": f"{base}/{final_name}",
            "fullUrl": full_url,
            "thumbnailUrl": f"{base}/thumbnail.webp",
            "batchPreviewUrl": f"{base}/batch.webp{suffix}",
            "detailPreviewUrl": f"{base}/preview.webp{suffix}",
            "readerUrl": f"{base}/reader.webp{suffix}",
            "inputUrl": f"{base}/{row['input_name']}" if row["input_name"] else None,
            "sourceType": row["source_type"],
            "finishedAt": self._iso(row["finished_at"]),
        }
        review_status = metadata.get("reviewStatus")
        has_review_flags = any(
            isinstance(region, dict) and region.get("review_required")
            for region in _json_load(row.get("text_regions"), [])
        )
        if has_review_flags:
            review_status = "pending"
        elif review_status not in {"approved", "not_required"}:
            review_status = "not_required"
        item.update({
            "reviewStatus": review_status,
            "reviewedAt": metadata.get("reviewedAt"),
            "needsReview": review_status == "pending",
        })
        if hasattr(row, "get") and row.get("series_id"):
            item["seriesId"] = row["series_id"]
            item["seriesTitle"] = row.get("series_title")
        if include_cover:
            item["coverUrl"] = f"{base}/cover.webp{suffix}"
        if not slim:
            has_bubble_mask = (self.result_root / row["folder"] / "bubble_mask.png").is_file()
            item.update(
                {
                    "inpaintedUrl": f"/result/{row['id']}/inpainted.jpg" if row["has_inpainted"] else None,
                    "textRegionsUrl": f"/result/{row['id']}/text_regions.json" if row["has_regions"] else None,
                    "bubbleMaskUrl": f"/result/{row['id']}/bubble_mask.png" if has_bubble_mask else None,
                    "hasTextRegions": bool(row["has_regions"]),
                    "settings": metadata.get("settings", {}),
                }
            )
        return item
    async def list_groups(
        self,
        limit: int = 12,
        offset: int = 0,
        manga_id: str | None = None,
        search: str | None = None,
        sort: str = "alpha-asc",
        review: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        page_size = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        clean_search = search.strip() if search and search.strip() else None
        effective_status = "review" if review == "pending" or status == "review" else (status or None)
        clean_manga_id = manga_id.strip() if manga_id and manga_id.strip() else None
        target_group_id = await self.resolve_group_id(clean_manga_id) if clean_manga_id else None
        filter_manga = target_group_id or clean_manga_id
        order_by = {
            "alpha-asc": "lower(g.title), g.title",
            "alpha-desc": "lower(g.title) DESC, g.title DESC",
            "date-asc": "grouped.latest_finished_at, lower(g.title), g.title",
            "date-desc": "grouped.latest_finished_at DESC, lower(g.title), g.title",
        }.get(sort, "lower(g.title), g.title")
        rows = await self.pool.fetch(
            f"""
            WITH matched_groups AS (
                SELECT id, title
                FROM manga_groups
                WHERE ($3::text IS NULL OR id=$3 OR title=$3)
                  AND ($4::text IS NULL OR title ILIKE '%' || $4 || '%')
                  AND (
                      $5::text IS NULL OR $5 = 'all'
                      OR ($5 = 'review' AND EXISTS (
                          SELECT 1 FROM pages rp
                          WHERE rp.active AND rp.manga_group_id = manga_groups.id
                            AND (rp.metadata->>'reviewStatus'='pending'
                                 OR rp.text_regions @> '[{{"review_required": true}}]'::jsonb)
                      ))
                      OR ($5 = 'translated' AND EXISTS (
                          SELECT 1 FROM pages tp
                          WHERE tp.active AND tp.manga_group_id = manga_groups.id
                            AND tp.source_type = 'translated'
                      ))
                      OR ($5 = 'original' AND NOT EXISTS (
                          SELECT 1 FROM pages tp
                          WHERE tp.active AND tp.manga_group_id = manga_groups.id
                            AND tp.source_type = 'translated'
                      ))
                      OR ($5 = 'summarized' AND EXISTS (
                          SELECT 1 FROM manga_summaries ms
                          WHERE ms.group_id = manga_groups.id
                            AND NULLIF(ms.payload->>'summary', '') IS NOT NULL
                      ))
                  )
            ), grouped AS (
                SELECT mg.id AS group_id, mg.title AS manga_title,
                       count(*) AS page_count,
                       count(*) FILTER (WHERE $5::text='review'
                         OR p.metadata->>'reviewStatus'='pending'
                         OR p.text_regions @> '[{{"review_required": true}}]'::jsonb) AS review_count,
                       max(p.finished_at) AS latest_finished_at
                FROM matched_groups mg
                JOIN pages p ON p.active AND p.manga_group_id = mg.id
                    AND ($5::text IS NULL OR $5::text != 'review' OR p.metadata->>'reviewStatus'='pending'
                         OR p.text_regions @> '[{{"review_required": true}}]'::jsonb)
                GROUP BY mg.id, mg.title
            ), page_rows AS (
                SELECT cover.*, grouped.page_count, grouped.review_count, grouped.latest_finished_at,
                       g.id AS group_id, g.title AS manga_title, g.series_id, s.title AS series_title,
                       EXISTS (
                           SELECT 1 FROM manga_summaries ms
                           WHERE ms.group_id=g.id AND NULLIF(ms.payload->>'summary', '') IS NOT NULL
                       ) AS has_summary,
                       row_number() OVER (
                           ORDER BY CASE WHEN g.title='Ungrouped' THEN 1 ELSE 0 END, {order_by}
                       ) AS row_position
                FROM grouped
                JOIN manga_groups g ON g.id = grouped.group_id
                LEFT JOIN manga_series s ON s.id = g.series_id
                JOIN LATERAL (
                    SELECT p.*
                    FROM pages p
                    WHERE p.active AND p.manga_group_id = grouped.group_id
                    ORDER BY p.page_order, p.folder
                    LIMIT 1
                ) cover ON TRUE
                ORDER BY CASE WHEN g.title='Ungrouped' THEN 1 ELSE 0 END,
                         {order_by}
                LIMIT $1 OFFSET $2
            ), stats AS (
                SELECT count(*) AS total_groups, COALESCE(sum(page_count), 0) AS total_images
                FROM grouped
            )
            SELECT page_rows.*, stats.total_groups, stats.total_images
            FROM stats LEFT JOIN page_rows ON TRUE
            ORDER BY page_rows.row_position
            """,
            page_size,
            offset,
            filter_manga,
            clean_search,
            effective_status,
        )
        total_groups = int(rows[0]["total_groups"])
        total_images = int(rows[0]["total_images"])
        groups = []
        for row in rows:
            if row["group_id"] is None:
                continue
            count = int(row["page_count"])
            title = row["manga_title"]
            groups.append(
                {
                    "id": row["group_id"],
                    "title": title,
                    "count": count,
                    "needsReviewCount": int(row.get("review_count") or 0),
                    "cover": self._store._page_item(row, include_cover=True),
                    "latestFinishedAt": self._iso(row["latest_finished_at"]),
                    "seriesId": row.get("series_id") if hasattr(row, "get") else None,
                    "seriesTitle": row.get("series_title") if hasattr(row, "get") else None,
                    "hasSummary": bool(row.get("has_summary")),
                }
            )
        next_offset = offset + len(groups) if offset + len(groups) < total_groups else None
        return {
            "groups": groups,
            "totalGroups": total_groups,
            "totalImages": total_images,
            "nextOffset": next_offset,
        }
    async def list_results(
        self,
        sort: str = "alpha",
        manga: str | None = None,
        detail: str | None = None,
        limit: int = 100,
        offset: int = 0,
        review: str | None = None,
    ) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        order_by = "p.page_order ASC, p.folder ASC"
        clean_manga = manga.strip() if manga is not None else None
        group_id = await self.resolve_group_id(clean_manga) if clean_manga else None
        if clean_manga and group_id is None:
            return {"directories": [], "items": [], "total": 0, "nextOffset": None}
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 500))
        where = "p.active AND ($1::text IS NULL OR p.manga_group_id=$1) AND ($2::text IS NULL OR p.metadata->>'reviewStatus'='pending' OR p.text_regions @> '[{\"review_required\": true}]'::jsonb)"
        total = await self.pool.fetchval(f"SELECT count(*) FROM pages p WHERE {where}", group_id, review)
        rows = await self.pool.fetch(
            f"""
            SELECT p.*, g.id AS group_id, g.title AS manga_title, g.series_id, s.title AS series_title
            FROM pages p
            JOIN manga_groups g ON g.id = p.manga_group_id
            LEFT JOIN manga_series s ON s.id = g.series_id
            WHERE {where}
            ORDER BY {order_by}
            LIMIT $3 OFFSET $4
            """,
            group_id,
            review,
            limit,
            offset,
        )
        slim = detail in {"reader", "slim"}
        items = [self._store._page_item(row, slim=slim) for row in rows]
        next_offset = offset + len(items) if offset + len(items) < int(total) else None
        return {
            "directories": [item["folder"] for item in items],
            "items": items,
            "total": int(total),
            "nextOffset": next_offset,
        }
    async def page_detail(self, record_id: str) -> dict[str, Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        row = await self.pool.fetchrow(
            """
            SELECT p.*, g.id AS group_id, g.title AS manga_title, g.series_id, s.title AS series_title
            FROM pages p
            JOIN manga_groups g ON g.id = p.manga_group_id
            LEFT JOIN manga_series s ON s.id = g.series_id
            WHERE p.active AND (p.id=$1 OR p.folder=$1)
            LIMIT 1
            """,
            record_id,
        )
        return self._store._page_item(row) if row else None
    async def group_pages(self, title: str) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(title)
        if group_id is None:
            return []
        rows = await self.pool.fetch(
            """SELECT p.*, g.id AS group_id, g.title AS group_title
               FROM pages p
               JOIN manga_groups g ON g.id=p.manga_group_id
               WHERE p.active AND p.manga_group_id=$1
               ORDER BY p.page_order, p.original_sort_key, p.folder""",
            group_id,
        )
        pages = []
        for row in rows:
            metadata = _json_load(row["metadata"], {})
            metadata.update({
                "mangaTitle": row["group_title"],
                "mangaGroupId": row["group_id"],
                "groupId": row["group_id"],
                "pageOrder": row["page_order"],
                "sourceType": row["source_type"],
            })
            pages.append({
                "id": row["id"],
                "folder": row["folder"],
                "path": self.result_root / row["folder"],
                "name": row["original_name"] if row["original_name"] and row["original_name"] != "Unknown" else f"{row['folder']}.png",
                "meta": metadata,
                "groupId": row["group_id"],
                "mangaTitle": row["group_title"],
                "sourceType": row["source_type"],
                "hasRegions": bool(row["has_regions"]),
                "textRegions": _json_load(row["text_regions"], []),
                "pageOrder": row["page_order"],
            })
        return pages
    async def export_pages(
        self, title: str, folders: list[str] | None = None, *, original: bool = False
    ) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(title)
        if group_id is None:
            return []
        if folders:
            record_values = [str(folder) for folder in folders]
            rows = await self.pool.fetch(
                """
                SELECT * FROM pages
                WHERE active AND manga_group_id=$1
                  AND (id=ANY($2::text[]) OR folder=ANY($2::text[]))
                ORDER BY page_order, original_sort_key, folder
                """,
                group_id,
                record_values,
            )
        else:
            rows = await self.pool.fetch(
                "SELECT * FROM pages WHERE active AND manga_group_id=$1 ORDER BY page_order, original_sort_key, folder",
                group_id,
            )
        pages = []
        for row in rows:
            page_root = self.result_root / row["folder"]
            use_input = (original or row["source_type"] == "original") and row["input_name"]
            source = page_root / row["input_name"] if use_input else final_file(page_root)
            if source is not None and source.is_file():
                pages.append({
                    "originalName": row["original_name"] if row["original_name"] and row["original_name"] != "Unknown" else f"{row['folder']}.png",
                    "pageOrder": row["page_order"],
                    "path": str(source),
                })
        return pages

    async def get_progress(self, installation_id: str, manga_title: str) -> dict[str, Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(manga_title)
        if group_id is None:
            return None
        row = await self.pool.fetchrow(
            """
            SELECT rp.id, rp.page_id, rp.page_number, rp.scroll_top, rp.complete,
                   rp.updated_at, g.id AS group_id, g.title AS manga_title
            FROM reading_progress rp
            JOIN manga_groups g ON g.id=rp.group_id
            WHERE rp.installation_id=$1 AND rp.group_id=$2
            """,
            installation_id,
            group_id,
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "installationId": installation_id,
            "groupId": row["group_id"],
            "mangaTitle": row["manga_title"],
            "pageId": row["page_id"],
            "page": row["page_number"],
            "scrollTop": row["scroll_top"],
            "complete": row["complete"],
            "updatedAt": self._iso(row["updated_at"]),
        }

    async def save_progress(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        updated_at = payload.get("updatedAt")
        parsed_updated_at = self._parse_finished_at_value(updated_at, dt.datetime.now(dt.timezone.utc))
        group_value = payload.get("groupId") or payload.get("mangaTitle")
        group_id = await self.resolve_group_id(group_value) if group_value else None
        if group_id is None:
            raise GroupNotFound("Manga group not found")
        page_id = payload.get("pageId")
        if page_id:
            page = await self.pool.fetchrow(
                "SELECT id, manga_group_id FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1",
                str(page_id),
            )
            if page is None:
                page_id = None
            elif page["manga_group_id"] != group_id:
                raise ValueError("Page does not belong to the manga group")
            else:
                page_id = page["id"]
        values = (
            str(uuid.uuid4()),
            payload["installationId"],
            group_id,
            page_id,
            payload.get("page"),
            max(0, int(payload.get("scrollTop", 0))),
            bool(payload.get("complete", False)),
            parsed_updated_at,
        )
        row = await self.pool.fetchrow(
            """
            INSERT INTO reading_progress(
                id,installation_id,group_id,page_id,page_number,scroll_top,complete,updated_at
            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT(installation_id,group_id) DO UPDATE SET
                page_id=EXCLUDED.page_id,
                page_number=EXCLUDED.page_number,
                scroll_top=EXCLUDED.scroll_top,
                complete=EXCLUDED.complete,
                updated_at=EXCLUDED.updated_at
            WHERE reading_progress.updated_at <= EXCLUDED.updated_at
            RETURNING id,page_id,page_number,scroll_top,complete,updated_at
            """,
            *values,
        )
        if row is None:
            return (await self.get_progress(values[1], values[2])) or payload
        group_title = await self.pool.fetchval("SELECT title FROM manga_groups WHERE id=$1", group_id)
        return {
            "id": row["id"],
            "installationId": values[1],
            "groupId": group_id,
            "mangaTitle": group_title,
            "pageId": row["page_id"],
            "page": row["page_number"],
            "scrollTop": row["scroll_top"],
            "complete": row["complete"],
            "updatedAt": self._iso(row["updated_at"]),
        }
