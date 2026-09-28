"""Manga review approval operations for PostgreSQL and file-backed storage."""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

from server.postgres_common import _json_dump, _json_load
from server.result_metadata import _manga_id
from server.series_repository import GroupNotFound


class MangaReviewMutationService:
    def __init__(
        self,
        get_store: Callable[[], Any],
        get_result_root: Callable[[], Path],
        result_file: Callable[[Path], Optional[Path]],
        invalidate_meta_cache: Callable[[Optional[str]], None],
        sync_batch_review: Optional[Callable[[str, bool], Any]] = None,
    ):
        self._get_store = get_store
        self._get_result_root = get_result_root
        self._result_file = result_file
        self._invalidate_meta_cache = invalidate_meta_cache
        self._sync_batch_review = sync_batch_review

    async def approve_manga_review(self, manga_id: str) -> tuple[str, int]:
        store = self._get_store()
        if store is not None:
            return await self._approve_postgres_review(store, manga_id)
        return await asyncio.to_thread(self._approve_file_backed_review, manga_id)

    async def _approve_postgres_review(self, store: Any, manga_id: str) -> tuple[str, int]:
        group_id = await store.resolve_group_id(manga_id)
        if group_id is None:
            resolved_title = await store.resolve_group_title(manga_id)
            if resolved_title is not None:
                group_id = await store.resolve_group_id(resolved_title)
        if group_id is None:
            raise GroupNotFound(f"Manga group '{manga_id}' not found")

        clean_title = await store.resolve_group_title(group_id) or manga_id
        pool = getattr(store, "pool", None)
        if pool is None:
            raise RuntimeError("PostgreSQL store is not started")

        reviewed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        approved_folders: list[str] = []

        async with pool.acquire() as connection:
            async with connection.transaction():
                if await connection.fetchval(
                    "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                ) is None:
                    raise GroupNotFound("Manga group not found")

                rows = await connection.fetch(
                    """
                    SELECT id, folder, text_regions, metadata
                    FROM pages
                    WHERE active AND manga_group_id=$1
                      AND (metadata->>'reviewStatus' = 'pending' OR text_regions @> '[{"review_required": true}]'::jsonb)
                    FOR UPDATE
                    """,
                    group_id,
                )
                if rows:
                    update_items = []
                    for row in rows:
                        regions = _json_load(row["text_regions"], [])
                        if isinstance(regions, list):
                            for reg in regions:
                                if isinstance(reg, dict) and reg.get("review_required"):
                                    reg["review_required"] = False
                                    reg["review_reason"] = None
                        meta = _json_load(row["metadata"], {})
                        if isinstance(meta, dict):
                            meta["reviewStatus"] = "approved"
                            meta["reviewedAt"] = reviewed_at
                        update_items.append((
                            _json_dump(regions),
                            _json_dump(meta),
                            row["id"],
                        ))
                        approved_folders.append(row["folder"])

                    await connection.executemany(
                        """
                        UPDATE pages
                        SET text_regions=$1::jsonb, metadata=$2::jsonb, updated_at=now()
                        WHERE id=$3
                        """,
                        update_items,
                    )

        for folder in approved_folders:
            self._invalidate_meta_cache(folder)
            if self._sync_batch_review is not None:
                try:
                    res = self._sync_batch_review(folder, False)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

        return clean_title, len(approved_folders)

    def _approve_file_backed_review(self, manga_id: str) -> tuple[str, int]:
        result_dir = self._get_result_root()
        if not result_dir.exists():
            raise GroupNotFound(f"Manga group '{manga_id}' not found")

        clean_id = manga_id.strip()
        reviewed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        matched_group_title: Optional[str] = None
        approved_count = 0
        matching_folders: list[Path] = []

        for item_path in result_dir.iterdir():
            if not (item_path.is_dir() and self._result_file(item_path) is not None):
                continue
            meta_path = item_path / "meta.json"
            item_manga = "Ungrouped"
            item_group_id = None
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    item_manga = (meta.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
                    item_group_id = meta.get("mangaGroupId") or meta.get("groupId")
                except Exception:
                    pass
            computed_id = _manga_id(item_manga)
            if clean_id in {item_manga, item_group_id, computed_id}:
                if matched_group_title is None:
                    matched_group_title = item_manga
                matching_folders.append(item_path)

        if not matching_folders and matched_group_title is None:
            raise GroupNotFound(f"Manga group '{manga_id}' not found")

        resolved_title = matched_group_title or clean_id

        for folder_path in matching_folders:
            meta_path = folder_path / "meta.json"
            regions_path = folder_path / "text_regions.json"
            meta: dict[str, Any] = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}

            regions: list[dict[str, Any]] = []
            if regions_path.exists():
                try:
                    regions = json.loads(regions_path.read_text(encoding="utf-8"))
                except Exception:
                    regions = []

            needs_review = (
                meta.get("reviewStatus") == "pending"
                or any(isinstance(r, dict) and r.get("review_required") for r in regions)
            )

            if not needs_review:
                continue

            has_modified_regions = False
            for reg in regions:
                if isinstance(reg, dict) and reg.get("review_required"):
                    reg["review_required"] = False
                    reg["review_reason"] = None
                    has_modified_regions = True

            if has_modified_regions:
                temporary_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        "w", encoding="utf-8", dir=folder_path, prefix=".regions.", suffix=".tmp", delete=False
                    ) as temporary:
                        json.dump(regions, temporary, ensure_ascii=False, indent=2)
                        temporary_path = Path(temporary.name)
                    os.replace(temporary_path, regions_path)
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)

            meta["reviewStatus"] = "approved"
            meta["reviewedAt"] = reviewed_at
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=folder_path, prefix=".meta.", suffix=".tmp", delete=False
                ) as temporary:
                    json.dump(meta, temporary, ensure_ascii=False, indent=2)
                    temporary_path = Path(temporary.name)
                os.replace(temporary_path, meta_path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

            self._invalidate_meta_cache(folder_path.name)
            if self._sync_batch_review is not None:
                try:
                    res = self._sync_batch_review(folder_path.name, False)
                    if asyncio.iscoroutine(res):
                        # If called within sync thread, run with loop if needed
                        asyncio.run(res)
                except Exception:
                    pass
            approved_count += 1

        return resolved_title, approved_count
