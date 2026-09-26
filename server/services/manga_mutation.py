"""Manga mutation operations shared by HTTP routes and storage backends."""

import asyncio
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

class MangaMutationService:
    def __init__(
        self,
        get_store: Callable[[], Any],
        get_result_root: Callable[[], Path],
        reorder_file_backed_pages: Callable[..., Any],
        update_file_backed_meta: Callable[..., Any],
        rename_summary: Callable[..., Any],
        result_file: Callable[..., Any],
        invalidate_meta_cache: Callable[[str | None], None],
        remove_summary: Callable[..., Any],
    ):
        self._get_store = get_store
        self._get_result_root = get_result_root
        self._reorder_file_backed_pages = reorder_file_backed_pages
        self._update_file_backed_meta = update_file_backed_meta
        self._rename_summary = rename_summary
        self._result_file = result_file
        self._invalidate_meta_cache = invalidate_meta_cache
        self._remove_summary = remove_summary

    async def reorder_pages(self, manga_id: str, page_ids: list[str]) -> list[dict[str, Any]]:
        store = self._get_store()
        if store is not None:
            return await store.reorder_pages(manga_id, page_ids)
        return await asyncio.to_thread(
            self._reorder_file_backed_pages, self._get_result_root(), manga_id, page_ids
        )

    async def update_metadata(
        self,
        *,
        manga_title: str | None,
        old_manga_title: str | None,
        page_ids: list[str] | None,
        folders: list[str] | None,
        group_id: str | None,
    ) -> tuple[str, str | None, int]:
        clean_title = (manga_title or "").strip() or "Ungrouped"
        store = self._get_store()
        if store is not None:
            if group_id:
                old_title, updated_count = await store.rename_group(group_id, clean_title)
            else:
                old_title = old_manga_title.strip() if old_manga_title else None
                updated_count = await store.update_meta(page_ids or folders, old_title, clean_title)
        else:
            old_title = old_manga_title.strip() if old_manga_title else None
            old_title, updated_count = await asyncio.to_thread(
                self._update_file_backed_meta,
                self._get_result_root(),
                folders,
                old_title,
                clean_title,
                group_id,
            )
            if old_title and old_title != clean_title and updated_count:
                await asyncio.to_thread(
                    self._rename_summary, self._get_result_root(), old_title, clean_title
                )
        return clean_title, old_title, updated_count

    async def delete_group(self, title: str, store: Any) -> tuple[str, int | None]:
        clean_title = (title or "").strip() or "Ungrouped"
        if store is not None:
            clean_title = await store.resolve_group_title(clean_title) or clean_title
            folders = await store.delete_group(clean_title)
            for folder in folders:
                await asyncio.to_thread(shutil.rmtree, self._get_result_root() / folder, True)
                self._invalidate_meta_cache(folder)
            return clean_title, len(folders)

        result_dir = self._get_result_root()
        if not result_dir.exists():
            return clean_title, None

        deleted_count = 0
        for item_path in result_dir.iterdir():
            if item_path.is_dir() and self._result_file(item_path) is not None:
                meta_file = item_path / "meta.json"
                item_manga = "Ungrouped"
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        item_manga = (meta.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
                    except Exception:
                        pass
                if item_manga == clean_title:
                    shutil.rmtree(item_path)
                    self._invalidate_meta_cache(item_path.name)
                    deleted_count += 1
        await asyncio.to_thread(self._remove_summary, self._get_result_root(), clean_title)
        return clean_title, deleted_count
