"""Search checkpoints in the application's existing PostgreSQL database."""
from __future__ import annotations

import json
import uuid

from server.search_embeddings import PROFILE, fingerprint


def decoded(value, default=None):
    return json.loads(value) if isinstance(value, str) else value if value is not None else default


class SearchStore:
    def __init__(self, store):
        self.store = store
        self.pool = store.pool
        self.root = store.result_root

    async def recover(self):
        await self.pool.execute("UPDATE search_jobs SET status='interrupted', updated_at=now() WHERE status IN ('queued','running')")

    async def create_job(self, group_ids):
        group_ids = list(dict.fromkeys(group_ids))
        job_id = str(uuid.uuid4())
        async with self.pool.acquire() as connection, connection.transaction():
            rows = await connection.fetch("SELECT id FROM manga_groups WHERE id=ANY($1::text[])", group_ids)
            if len(rows) != len(group_ids):
                raise ValueError("One or more selected manga no longer exist. Refresh the list.")
            await connection.execute("INSERT INTO search_jobs(id,group_ids) VALUES($1,$2::jsonb)", job_id, json.dumps(group_ids))
            await connection.execute("""
                INSERT INTO search_job_items(job_id,source_key,group_id,page_id,modality)
                SELECT $1, 'summary:'||id, id, NULL, 'summary' FROM manga_groups
                WHERE id=ANY($2::text[])
                """, job_id, group_ids)
        return job_id

    async def jobs(self, job_id=None):
        rows = await self.pool.fetch("""
            SELECT j.*, count(i.*)::int AS total,
              count(*) FILTER (WHERE i.status='complete')::int AS completed,
              count(*) FILTER (WHERE i.status='unchanged')::int AS unchanged,
              count(*) FILTER (WHERE i.status='skipped')::int AS skipped,
              count(*) FILTER (WHERE i.status='error')::int AS failed
            FROM search_jobs j LEFT JOIN search_job_items i ON i.job_id=j.id
            WHERE ($1::text IS NULL OR j.id=$1)
            GROUP BY j.id ORDER BY j.created_at DESC LIMIT 10
            """, job_id)
        result = []
        for row in rows:
            value = dict(row)
            value["groupIds"] = decoded(value.pop("group_ids"), [])
            value["createdAt"] = value.pop("created_at").isoformat()
            value["updatedAt"] = value.pop("updated_at").isoformat()
            value["cancelRequested"] = value.pop("cancel_requested")
            value["issues"] = [dict(issue) for issue in await self.pool.fetch(
                """SELECT i.source_key AS "sourceKey", i.error, i.group_id AS "groupId",
                     COALESCE(g.title, 'Deleted manga') AS title, p.page_order AS "pageNumber"
                   FROM search_job_items i LEFT JOIN manga_groups g ON g.id=i.group_id
                   LEFT JOIN pages p ON p.id=i.page_id
                   WHERE i.job_id=$1 AND i.error IS NOT NULL ORDER BY i.source_key LIMIT 20""", value["id"])]
            result.append(value)
        return result

    async def job_state(self, job_id, status, error=None):
        await self.pool.execute("UPDATE search_jobs SET status=$2,error=$3,updated_at=now() WHERE id=$1", job_id, status, error)

    async def resume(self, job_id):
        async with self.pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow("SELECT status FROM search_jobs WHERE id=$1 FOR UPDATE", job_id)
            if not row:
                raise ValueError("Embedding job not found")
            if row["status"] not in {"cancelled", "interrupted", "error", "partial"}:
                raise ValueError("This job cannot be resumed")
            await connection.execute("UPDATE search_jobs SET status='queued',cancel_requested=false,error=NULL,updated_at=now() WHERE id=$1", job_id)
            await connection.execute("UPDATE search_job_items SET status='pending',error=NULL WHERE job_id=$1 AND status IN ('error','skipped')", job_id)

    async def pending(self, job_id):
        return await self.pool.fetchrow("SELECT * FROM search_job_items WHERE job_id=$1 AND status='pending' AND modality='summary' ORDER BY source_key LIMIT 1", job_id)

    async def finish_item(self, job_id, key, status, error=None):
        await self.pool.execute("UPDATE search_job_items SET status=$3,error=$4,updated_at=now() WHERE job_id=$1 AND source_key=$2", job_id, key, status, error)

    async def source(self, key):
        row = await self.pool.fetchrow("SELECT * FROM search_sources WHERE source_key=$1", key)
        if row is None:
            return None
        value = dict(row)
        value["point_ids"] = decoded(value["point_ids"], [])
        value["metadata"] = decoded(value["metadata"], {})
        return value

    async def save_source(self, item, version, ids, metadata):
        await self.pool.execute("""
            INSERT INTO search_sources(source_key,group_id,page_id,modality,fingerprint,profile,point_ids,metadata)
            VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb)
            ON CONFLICT(source_key) DO UPDATE SET group_id=EXCLUDED.group_id,page_id=EXCLUDED.page_id,
              fingerprint=EXCLUDED.fingerprint,profile=EXCLUDED.profile,point_ids=EXCLUDED.point_ids,
              metadata=EXCLUDED.metadata,indexed_at=now()
            """, item["source_key"], item["group_id"], item["page_id"], item["modality"], version, PROFILE, json.dumps(ids), json.dumps(metadata))

    async def delete_sources(self, group_id=None):
        return dict(await self.pool.fetchrow(
            "WITH deleted AS (DELETE FROM search_sources WHERE ($1::text IS NULL OR group_id=$1) RETURNING modality) "
            "SELECT count(*) FILTER (WHERE modality='summary')::int AS summary FROM deleted",
            group_id,
        ))

    async def coverage(self, group_ids=None):
        row = await self.pool.fetchrow("""
            SELECT count(DISTINCT g.id)::int AS manga,
              count(DISTINCT t.group_id)::int AS indexed_summaries
            FROM manga_groups g
            LEFT JOIN search_sources t ON t.source_key='summary:'||g.id AND t.profile=$2 AND t.modality='summary'
            WHERE ($1::text[] IS NULL OR g.id=ANY($1))
              AND EXISTS (SELECT 1 FROM pages active_page WHERE active_page.active AND active_page.manga_group_id=g.id)
            """, group_ids, PROFILE)
        return {"manga": row["manga"], "indexedSummaries": row["indexed_summaries"]}

    async def manga(self, search="", offset=0, limit=25, group_ids=None, status="all"):
        rows = await self.pool.fetch("""
            SELECT g.id,g.title,count(*) OVER()::int AS total FROM manga_groups g
            WHERE ($1='' OR g.title ILIKE '%'||$1||'%') AND ($2::text[] IS NULL OR g.id=ANY($2))
              AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
              AND (
                  $5::text IS NULL OR $5 = 'all'
                  OR ($5 = 'summarized' AND EXISTS (
                      SELECT 1 FROM manga_summaries ms
                      WHERE ms.group_id=g.id AND NULLIF(ms.payload->>'summary', '') IS NOT NULL
                  ))
                  OR ($5 = 'not-summarized' AND NOT EXISTS (
                      SELECT 1 FROM manga_summaries ms
                      WHERE ms.group_id=g.id AND NULLIF(ms.payload->>'summary', '') IS NOT NULL
                  ))
                  OR ($5 = 'indexed' AND EXISTS (
                      SELECT 1 FROM search_sources s WHERE s.group_id=g.id AND s.profile=$6 AND s.modality='summary'
                  ))
                  OR ($5 = 'not-indexed' AND NOT EXISTS (
                      SELECT 1 FROM search_sources s WHERE s.group_id=g.id AND s.profile=$6 AND s.modality='summary'
                  ))
              )
            ORDER BY lower(g.title),g.id LIMIT $3 OFFSET $4
            """, search, group_ids, limit, offset, status, PROFILE)
        result = []
        for row in rows:
            group_id = row["id"]
            pages = await self.pool.fetch("SELECT id FROM pages WHERE manga_group_id=$1 AND active ORDER BY page_order, folder", group_id)
            indexed_summary = await self.pool.fetchrow(
                "SELECT * FROM search_sources WHERE source_key=$1 AND profile=$2 AND modality='summary'",
                "summary:" + group_id, PROFILE,
            )
            summary_info = await self.store.summary_status(group_id) or {}
            summary = summary_info.get("summary") or ""
            summary_outdated = bool(indexed_summary and (summary_info.get("stale") or indexed_summary["fingerprint"] != fingerprint(summary)))
            first_page = pages[0] if pages else None
            cover_url = f"/result/{first_page['id']}/thumbnail.webp" if first_page else None
            result.append({
                "id": group_id, "title": row["title"], "pageCount": len(pages),
                "summaryAvailable": bool(summary), "summaryStale": bool(summary_info.get("stale")),
                "summaryIndexed": bool(indexed_summary), "summaryOutdated": summary_outdated,
                "outdated": summary_outdated, "partial": not bool(indexed_summary),
                "coverUrl": cover_url,
            })
        return {"items": result, "total": rows[0]["total"] if rows else 0}
