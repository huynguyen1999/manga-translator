"""PostgreSQL-backed adapter for the existing batch store interface."""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
import datetime as dt
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from server.batch_store import BatchConflict, BatchNotFound, BatchStore, InvalidBatch, _batch_progress
from server.image_variants import final_file
from server.postgres_common import _json_dump, _json_load, _page_order, _safe_folder
from server.postgres_batch_persistence import save_batch_manifest
from manga_translator.utils.image_storage import find_asset

if TYPE_CHECKING:
    from server.postgres_store import PostgresStore

logger = logging.getLogger("manga-translator.postgres")


class PostgresBatchStore(BatchStore):
    """BatchStore-compatible adapter; PostgreSQL owns state, files own inputs."""

    def __init__(self, database: PostgresStore, root: str | Path, result_root: str | Path):
        super().__init__(root, result_root)
        self.database = database

    @property
    def _pool(self) -> Any:
        if self.database.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        return self.database.pool

    @asynccontextmanager
    async def _batch_connection(self, connection: Any = None):
        if connection is not None:
            yield connection
            return
        async with self._pool.acquire() as acquired:
            yield acquired

    async def _save_db_manifest(self, manifest: dict[str, Any], connection: Any = None) -> None:
        return await save_batch_manifest(self, manifest, connection)

    async def _hydrate_manifest(
        self, manifest: dict[str, Any], connection: Any = None
    ) -> dict[str, Any]:
        fetch = connection.fetch if connection is not None else self._pool.fetch
        rows = await fetch(
            """
            SELECT i.id, i.manga_group_id, g.title AS manga_title,
                   i.page_id, i.page_order, p.folder AS result_folder,
                   i.status, i.stage, i.stage_started_at, i.error, i.request_id, i.payload
            FROM batch_items i
            LEFT JOIN manga_groups g ON g.id=i.manga_group_id
            LEFT JOIN pages p ON p.id=i.page_id
            WHERE i.batch_id=$1
            """,
            manifest["id"],
        )
        by_id = {row["id"]: row for row in rows}
        hydrated = copy.deepcopy(manifest)
        for item in hydrated.get("items", []):
            row = by_id.get(item.get("id"))
            if row is None:
                continue
            payload = _json_load(row.get("payload"), {})
            if isinstance(payload, dict):
                item.update(payload)
            for column, key in (
                ("status", "status"),
                ("stage", "stage"),
                ("stage_started_at", "stageStartedAt"),
                ("error", "error"),
                ("request_id", "requestId"),
            ):
                if column in row.keys():
                    if column == "stage_started_at" and row[column] is None:
                        continue
                    item[key] = row[column]
            item["mangaGroupId"] = row["manga_group_id"]
            item["pageId"] = row["page_id"]
            item["pageOrder"] = row["page_order"]
            item["mangaTitle"] = row["manga_title"] or item.get("mangaTitle", hydrated.get("mangaTitle"))
            if row["result_folder"] is not None:
                item["resultFolder"] = row["result_folder"]
        return hydrated

    async def _db_manifest(
        self, batch_id: str, connection: Any = None, for_update: bool = False
    ) -> dict[str, Any]:
        query = """SELECT manifest,status,title,dismissed,added_at,
                           updated_at,total_items,completed_count
                    FROM batches WHERE id=$1 AND active"""
        if for_update:
            query += " FOR UPDATE"
        fetchrow = connection.fetchrow if connection is not None else self._pool.fetchrow
        row = await fetchrow(query, batch_id)
        if row is None:
            raise BatchNotFound(batch_id)
        value = _json_load(row["manifest"], None)
        if not isinstance(value, dict):
            raise InvalidBatch(f"Malformed database manifest: {batch_id}")
        return await self._hydrate_manifest(self._overlay_batch_columns(value, row), connection)

    @staticmethod
    def _overlay_batch_columns(manifest: dict[str, Any], row: Any) -> dict[str, Any]:
        hydrated = copy.deepcopy(manifest)
        fields = {
            "status": "status",
            "title": "title",
            "dismissed": "dismissed",
            "added_at": "addedAt",
            "updated_at": "updatedAt",
            "total_items": "totalItems",
            "completed_count": "completedCount",
        }
        for column, key in fields.items():
            if column in row.keys():
                hydrated[key] = row[column]
        return hydrated

    def _write_snapshot(self, manifest: dict[str, Any]) -> None:
        path = self._manifest_path(manifest["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_manifest(path, manifest)

    def _stage_batch(
        self,
        batch_id: str,
        normalized: dict[str, Any],
        files: dict[str, tuple[str, bytes]],
    ) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        batch_dir = self._batch_dir(batch_id)
        if batch_dir.exists():
            raise BatchConflict(f"Batch {batch_id} already exists but is not indexed")
        expected = set() if normalized.get("kind") in {"rerender", "pipeline-rerun"} else {
            item["id"] for item in normalized["items"] if item["status"] != "completed"
        }
        if set(files) != expected:
            missing = expected - set(files)
            extra = set(files) - expected
            detail = []
            if missing:
                detail.append(f"missing uploads: {sorted(missing)}")
            if extra:
                detail.append(f"unknown uploads: {sorted(extra)}")
            raise InvalidBatch("; ".join(detail) or "Upload does not match manifest")
        temporary = Path(tempfile.mkdtemp(prefix=f".{batch_id}-", dir=self.root))
        try:
            inputs = temporary / "inputs"
            inputs.mkdir()
            for item in normalized["items"]:
                if item["status"] == "completed":
                    continue
                if normalized.get("kind") in {"rerender", "pipeline-rerun"}:
                    continue
                filename, content = files[item["id"]]
                if not isinstance(filename, str) or Path(filename).name != filename:
                    raise InvalidBatch("Invalid uploaded filename")
                suffix = Path(filename).suffix.lower() or Path(item["name"]).suffix.lower() or ".bin"
                item["input"] = f"inputs/{item['id']}{suffix}"
                dest = inputs / f"{item['id']}{suffix}"
                if isinstance(content, bytes):
                    dest.write_bytes(content)
                elif hasattr(content, "file"):
                    content.file.seek(0)
                    with dest.open("wb") as out:
                        shutil.copyfileobj(content.file, out)
                elif hasattr(content, "read"):
                    content.seek(0)
                    with dest.open("wb") as out:
                        shutil.copyfileobj(content, out)
                else:
                    dest.write_bytes(bytes(content))
            normalized["updatedAt"] = int(dt.datetime.now().timestamp() * 1000)
            self._write_manifest(temporary / "manifest.json", normalized)
            os.replace(temporary, batch_dir)
            return normalized
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    async def put_batch(
        self,
        batch_id: str,
        manifest: dict[str, Any],
        files: dict[str, tuple[str, bytes]],
    ) -> dict[str, Any]:
        async with self._lock:
            normalized = self._normalize_manifest(batch_id, manifest)
            try:
                existing = await self._db_manifest(batch_id)
            except BatchNotFound:
                existing = None
            if existing is not None:
                if self._submission_shape(existing) == self._submission_shape(normalized):
                    return self._to_dto(existing)
                raise BatchConflict(f"Batch {batch_id} already exists with different data")
            staged = await asyncio.to_thread(self._stage_batch, batch_id, normalized, files)
            try:
                await self._save_db_manifest(staged)
            except Exception:
                logger.exception("Batch %s staged but database indexing failed", batch_id)
                raise
            return self._to_dto(staged)

    async def list_runnable_batches(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT id,manifest,status,title,dismissed,added_at,
                   updated_at,total_items,completed_count FROM batches
            WHERE active AND status IN ('waiting', 'processing')
            ORDER BY CASE WHEN manifest->>'priority' = 'true' THEN 0 ELSE 1 END, added_at, id
            """
        )
        result = []
        for row in rows:
            manifest = _json_load(row["manifest"], {})
            if isinstance(manifest, dict):
                manifest = self._overlay_batch_columns(manifest, row)
                result.append(await self._hydrate_manifest(manifest) if manifest.get("items") else manifest)
        return result

    async def list_batches(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """SELECT id,manifest,status,title,dismissed,added_at,
                      updated_at,total_items,completed_count FROM batches WHERE active
               ORDER BY CASE WHEN manifest->>'priority' = 'true' THEN 0 ELSE 1 END, added_at, id"""
        )
        result = []
        for row in rows:
            manifest = _json_load(row["manifest"], {})
            if isinstance(manifest, dict):
                manifest = self._overlay_batch_columns(manifest, row)
                hydrated = await self._hydrate_manifest(manifest) if manifest.get("items") else manifest
                result.append(self._to_dto(hydrated))
        return result

    async def list_batch_summaries(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT
                b.id,
                b.title,
                b.status,
                b.dismissed,
                b.added_at,
                b.updated_at,
                b.total_items,
                b.completed_count,
                b.manifest->'settings' AS settings,
                b.manifest->>'kind' AS kind,
                COALESCE(b.manifest->>'mangaGroupId', MAX(i.manga_group_id)) AS manga_group_id,
                (b.manifest->>'priority')::boolean AS priority,
                COUNT(*) FILTER (WHERE i.status = 'queued' OR i.stage IN ('awaiting_translation', 'reserved')) AS queued_count,
                COUNT(*) FILTER (WHERE i.status = 'processing' AND COALESCE(i.stage, '') NOT IN ('awaiting_translation', 'reserved')) AS processing_count,
                COUNT(*) FILTER (WHERE i.status = 'error') AS failed_count,
                COUNT(*) FILTER (WHERE i.payload->>'needsReview' = 'true') AS needs_review_count,
                jsonb_agg(jsonb_build_object(
                    'status', i.status,
                    'stage', i.stage,
                    'pipelineStage', i.payload->>'pipelineStage',
                    'retryFromStage', i.payload->>'retryFromStage'
                )) FILTER (WHERE i.id IS NOT NULL) AS stage_items
            FROM batches b
            LEFT JOIN batch_items i ON i.batch_id = b.id
            WHERE b.active
            GROUP BY b.id
            ORDER BY CASE WHEN (b.manifest->>'priority')::boolean THEN 0 ELSE 1 END,
                     b.added_at,
                     b.id
            """
        )
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "mangaTitle": row["title"],
                "mangaGroupId": row["manga_group_id"],
                "kind": row["kind"],
                "status": row["status"],
                "dismissed": bool(row["dismissed"]),
                "addedAt": row["added_at"],
                "updatedAt": row["updated_at"],
                "settings": _json_load(row["settings"], {}),
                "priority": bool(row["priority"]),
                "totalItems": int(row["total_items"]),
                "completedCount": int(row["completed_count"]),
                "queuedCount": int(row["queued_count"] or 0),
                "processingCount": int(row["processing_count"] or 0),
                "failedCount": int(row["failed_count"] or 0),
                "needsReviewCount": int(row["needs_review_count"] or 0),
                **_batch_progress(_json_load(row["stage_items"], [])),
            }
            for row in rows
        ]

    async def update_review_for_result(self, folder: str, needs_review: bool) -> int:
        folder = _safe_folder(folder)
        snapshots = []
        updated = 0
        async with self._lock:
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    rows = await connection.fetch(
                        """
                        SELECT b.id AS batch_id, b.manifest, i.id AS item_id
                        FROM batches b
                        JOIN batch_items i ON i.batch_id=b.id
                        LEFT JOIN pages p ON p.id=i.page_id
                        WHERE b.active
                          AND (p.folder=$1 OR i.payload->>'resultFolder'=$1)
                        FOR UPDATE OF b
                        """,
                        folder,
                    )
                    by_batch: dict[str, tuple[dict[str, Any], set[str]]] = {}
                    for row in rows:
                        batch_id = row["batch_id"]
                        if batch_id not in by_batch:
                            by_batch[batch_id] = (copy.deepcopy(_json_load(row["manifest"], {})), set())
                        by_batch[batch_id][1].add(row["item_id"])

                    now = int(dt.datetime.now().timestamp() * 1000)
                    for batch_id, (manifest, item_ids) in by_batch.items():
                        for item in manifest.get("items", []):
                            if item.get("id") in item_ids:
                                item["needsReview"] = needs_review
                        manifest["updatedAt"] = now
                        await connection.execute(
                            """
                            UPDATE batch_items
                            SET payload=jsonb_set(payload, '{needsReview}', to_jsonb($3::boolean), true)
                            WHERE batch_id=$1 AND id=ANY($2::text[])
                            """,
                            batch_id,
                            list(item_ids),
                            needs_review,
                        )
                        await connection.execute(
                            "UPDATE batches SET manifest=$2::jsonb, updated_at=$3 WHERE id=$1",
                            batch_id,
                            _json_dump(manifest),
                            now,
                        )
                        snapshots.append(manifest)
                        updated += len(item_ids)
            for manifest in snapshots:
                await asyncio.to_thread(self._write_snapshot, manifest)
        return updated

    async def get_batch(self, batch_id: str) -> dict[str, Any]:
        return self._to_dto(await self._db_manifest(batch_id))

    async def mutate(self, batch_id: str, mutator: Any) -> dict[str, Any]:
        async with self._lock:
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    manifest = await self._db_manifest(batch_id, connection, for_update=True)
                    result = mutator(manifest)
                    if result is False:
                        return self._to_dto(manifest)
                    manifest["updatedAt"] = int(dt.datetime.now().timestamp() * 1000)
                    manifest["totalItems"] = max(
                        len(manifest.get("items", [])), int(manifest.get("totalItems", 0))
                    )
                    manifest["completedCount"] = max(
                        sum(item.get("status") == "completed" for item in manifest.get("items", [])),
                        int(manifest.get("completedCount", 0)),
                    )
                    await self._save_db_manifest(manifest, connection)
            await asyncio.to_thread(self._write_snapshot, manifest)
            return self._to_dto(manifest)

    async def delete_batch(self, batch_id: str) -> None:
        async with self._lock:
            manifest = await self._db_manifest(batch_id)
            group_ids = sorted({
                item.get("mangaGroupId")
                for item in manifest.get("items", [])
                if item.get("mangaGroupId")
            })
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    for group_id in group_ids:
                        await connection.fetchrow(
                            "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                        )
                    for group_id in group_ids:
                        await self.database._compact_page_order(connection, group_id)
                    await connection.execute("DELETE FROM batches WHERE id=$1", batch_id)
            await asyncio.to_thread(shutil.rmtree, self._batch_dir(batch_id), True)

    async def input_path(self, batch_id: str, item_id: str) -> Path:
        manifest = await self._db_manifest(batch_id)
        for item in manifest.get("items", []):
            if item.get("id") == item_id:
                path = self._input_path(batch_id, item)
                if path.is_file():
                    return path
                folder = item.get("resultFolder")
                if folder and isinstance(folder, str):
                    result = self.result_root / folder
                    for stem in ("input", "inpainted", "final"):
                        fallback = find_asset(result, stem)
                        if fallback and fallback.is_file():
                            return fallback
                break
        raise BatchNotFound(f"{batch_id}/{item_id}")

    async def reconcile(self, result_root: str | Path | None = None) -> None:
        root = Path(result_root or self.result_root).resolve()
        rows = await self._pool.fetch("SELECT manifest FROM batches WHERE active")
        request_ids = [
            item.get("requestId")
            for row in rows
            for item in _json_load(row["manifest"], {}).get("items", [])
            if item.get("status") == "processing" and item.get("requestId")
        ]
        folders_by_request = {}
        if request_ids:
            matches = await self._pool.fetch(
                """
                SELECT DISTINCT ON (request_id) request_id, folder
                FROM pages
                WHERE active AND request_id = ANY($1::text[])
                ORDER BY request_id, updated_at DESC
                """,
                request_ids,
            )
            folders_by_request = {row["request_id"]: row["folder"] for row in matches}
        for row in rows:
            manifest = _json_load(row["manifest"], {})
            if manifest.get("status") == "stopping":
                await self.delete_batch(manifest["id"])
                continue
            was_paused = manifest.get("status") == "paused"
            changed = False
            for item in manifest.get("items", []):
                if item.get("status") != "processing":
                    continue
                folder = folders_by_request.get(item.get("requestId")) or item.get("resultFolder")
                result = (
                    root / folder
                    if isinstance(folder, str) and Path(folder).name == folder
                    else None
                )
                if result and final_file(result) is not None:
                    item.update(status="completed", stage="finished", resultFolder=folder)
                    self._input_path(batch_id=manifest["id"], item=item).unlink(missing_ok=True)
                else:
                    item.update(status="queued", stage=None)
                changed = True
            if changed:
                statuses = {item.get("status") for item in manifest.get("items", [])}
                manifest["status"] = (
                    "paused" if was_paused and "queued" in statuses else
                    "error" if "error" in statuses else
                    "completed" if statuses and statuses <= {"completed"} else
                    "waiting"
                )
                await self._save_db_manifest(manifest)
                await asyncio.to_thread(self._write_snapshot, manifest)

    async def register_result(
        self,
        folder: str,
        page_order: int | None = None,
        page_id: str | None = None,
    ) -> None:
        await self.database.sync_result_folder(
            folder,
            page_order=page_order,
            replace_page_id=page_id,
        )

    async def import_existing_batch(self, batch_id: str) -> dict[str, Any]:
        normalized = await asyncio.to_thread(
            self._normalize_manifest,
            batch_id,
            self._read_manifest(self._manifest_path(batch_id)),
        )
        await self._save_db_manifest(normalized)
        return self._to_dto(normalized)
