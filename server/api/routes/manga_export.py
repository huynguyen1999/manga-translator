import asyncio
import json
import os
import re
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from manga_translator.config import MAX_MANGA_TITLE_LENGTH
from server.api.schemas.manga import ExportCbzRequest


def create_manga_export_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    natural_keys: Callable[[str], list[Any]],
    meta_page_order: Callable[[dict[str, Any]], Optional[int]],
    source_type: Callable[[dict[str, Any]], str],
    input_file: Callable[[Path], Optional[Path]],
    result_file: Callable[[Path], Optional[Path]],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    def _build_cbz_archive(tmp_path: str, pages: list, safe_title: str):
        """Worker function to build CBZ archive on disk synchronously in a worker thread"""
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
            comic_info = f"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <Title>{safe_title}</Title>
  <PageCount>{len(pages)}</PageCount>
</ComicInfo>"""
            zf.writestr("ComicInfo.xml", comic_info)

            for idx, page in enumerate(pages):
                orig_name = page["originalName"]
                file_path = page["path"]
                ext = os.path.splitext(orig_name)[1] or ".png"
                base = os.path.splitext(orig_name)[0]
                clean_base = re.sub(r'[\\/*?:"<>|]', "_", base)
                arcname = f"{idx+1:03d}_{clean_base}{ext}"
                zf.write(file_path, arcname=arcname)

    async def create_cbz_stream(pages: list, manga_title: str, original: bool = False):
        """Generates a zip archive (CBZ) containing pages sorted naturally with ComicInfo.xml using FileResponse"""
        pages.sort(
            key=lambda p: (
                p.get("pageOrder") is None,
                p.get("pageOrder") or 0,
                natural_keys(p["originalName"]),
                p["path"],
            )
        )
        safe_title = (manga_title or "Manga").strip() or "Manga"

        tmp_file = tempfile.NamedTemporaryFile(suffix=".cbz", delete=False)
        tmp_path = tmp_file.name
        tmp_file.close()

        try:
            await asyncio.to_thread(_build_cbz_archive, tmp_path, pages, safe_title)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            raise

        safe_filename = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5\u3040-\u30ff\uac00-\ud7af\.\-]', "_", safe_title)
        if not safe_filename:
            safe_filename = "manga"
        cbz_filename = f"{safe_filename}{'_original' if original else ''}.cbz"

        def cleanup(path: str):
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except Exception:
                pass

        return FileResponse(
            tmp_path,
            media_type="application/vnd.comicbook+zip",
            filename=cbz_filename,
            background=BackgroundTask(cleanup, tmp_path),
            headers={"Access-Control-Expose-Headers": "Content-Disposition"},
        )

    @router.post("/results/export/cbz", tags=["api"])
    @router.post("/api/results/export/cbz", tags=["api"])
    async def export_cbz_post(data: ExportCbzRequest):
        """Export a manga group as a translated or original .cbz comic archive."""
        store = get_store()
        if store is not None:
            group_value = (data.groupId or data.mangaTitle or "").strip()
            if data.groupId and data.mangaTitle and await store.resolve_group_id(group_value) is None:
                group_value = data.mangaTitle.strip()
            manga_title = await store.resolve_group_title(group_value) or group_value or "Manga"
            pages = await store.export_pages(group_value, data.folders, original=data.original)
            if not pages:
                raise HTTPException(404, detail="No manga pages found to export")
            return await create_cbz_stream(pages, manga_title, original=data.original)

        result_dir = get_result_root()
        if not result_dir.exists():
            raise HTTPException(404, detail="Result directory not found")

        manga_title = (data.mangaTitle or "Manga").strip()
        pages = []
        target_folders = set(data.folders) if data.folders else None

        for item_path in result_dir.iterdir():
            if item_path.is_dir():
                if result_file(item_path) is not None:
                    folder_name = item_path.name
                    if target_folders is not None and folder_name not in target_folders:
                        continue

                    meta_file = item_path / "meta.json"
                    meta = {}
                    if meta_file.exists():
                        try:
                            meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        except Exception:
                            pass

                    item_manga = meta.get("mangaTitle") or "Ungrouped"
                    if target_folders is None and item_manga != manga_title:
                        continue

                    orig_name = meta.get("originalName")
                    if not orig_name or orig_name == "Unknown":
                        orig_name = f"{folder_name}.png"
                    page_path = result_file(item_path)
                    if data.original or source_type(meta) == "original":
                        page_path = input_file(item_path) or page_path
                    pages.append({
                        "originalName": orig_name,
                        "pageOrder": meta_page_order(meta),
                        "path": str(page_path),
                    })

        if not pages:
            raise HTTPException(404, detail="No manga pages found to export")

        return await create_cbz_stream(pages, manga_title, original=data.original)

    @router.get("/results/export/cbz", tags=["api"])
    @router.get("/api/results/export/cbz", tags=["api"])
    async def export_cbz_get(
        manga: Optional[str] = Query(None, max_length=MAX_MANGA_TITLE_LENGTH),
        groupId: Optional[str] = None,
        folders: Optional[str] = None,
        original: bool = False,
    ):
        """Export a manga group as a translated or original .cbz comic archive."""
        folder_list = [f.strip() for f in folders.split(",") if f.strip()] if isinstance(folders, str) and folders.strip() else None
        manga_title = manga if isinstance(manga, str) and manga.strip() else "Manga"
        return await export_cbz_post(
            ExportCbzRequest(groupId=groupId, mangaTitle=manga_title, folders=folder_list, original=original)
        )

    return router, (_build_cbz_archive, create_cbz_stream, export_cbz_post, export_cbz_get)
