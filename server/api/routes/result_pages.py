import asyncio
import datetime
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from server.api.schemas.manga import DeletePagesRequest


def create_result_pages_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    result_file: Callable[[Path], Optional[Path]],
    get_cached_meta: Callable[[Path], dict],
    input_file: Callable[[Path], Optional[Path]],
    source_type: Callable[[dict], str],
    generate_image_variants: Callable[[Path], Any],
    image_urls: Callable[..., dict[str, Optional[str]]],
    asset_version: Callable[[Path], int],
    find_asset: Callable[[Path, str], Optional[Path]],
    page_order: Callable[[dict], Optional[int]],
    review_status: Callable[..., str],
    manga_id: Callable[[str], str],
    invalidate_meta_cache: Callable[[Optional[str]], None],
    compact_file_backed_group: Callable[..., Any],
    delete_file_backed_results: Callable[[Path, list[str]], list[str]],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.delete("/results/clear", tags=["api"])
    @router.delete("/api/results/clear", tags=["api"])
    async def clear_results():
        """Delete all result directories"""
        store = get_store()
        result_root = get_result_root()
        if store is not None:
            deleted_count = await store.clear_results()
            await asyncio.to_thread(shutil.rmtree, result_root / ".summaries", True)
            invalidate_meta_cache()
            return {"message": f"Deleted {deleted_count} result directories"}

        result_dir = result_root
        if not result_dir.exists():
            return {"message": "No results directory found"}

        try:
            deleted_count = 0
            for item_path in result_dir.iterdir():
                if item_path.is_dir():
                    if result_file(item_path) is not None:
                        shutil.rmtree(item_path)
                        deleted_count += 1

            await asyncio.to_thread(shutil.rmtree, result_dir / ".summaries", ignore_errors=True)
            invalidate_meta_cache()
            return {"message": f"Deleted {deleted_count} result directories"}
        except Exception as error:
            raise HTTPException(500, detail=f"Error clearing results: {str(error)}")

    @router.get("/results/{folder_name}", tags=["api"])
    @router.get("/api/results/{folder_name}", tags=["api"])
    @router.get("/api/pages/{folder_name}", tags=["api"])
    async def get_result_detail(folder_name: str):
        """Get full metadata for a specific result folder to restore viewer/editor deep links"""
        store = get_store()
        if store is not None:
            item = await store.page_detail(folder_name)
            if item is None:
                raise HTTPException(404, detail="Result directory not found")
            return item

        result_dir = get_result_root().resolve()
        folder_path = result_dir / folder_name
        if not folder_path.exists() or not folder_path.is_dir():
            raise HTTPException(404, detail="Result directory not found")

        final_path = result_file(folder_path)
        if final_path is None:
            raise HTTPException(404, detail="Result file not found")

        meta = get_cached_meta(folder_path)
        original_name = meta.get("originalName")
        if not original_name or original_name == "Unknown":
            original_name = f"{folder_name}.png"
        manga_title = (meta.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
        finished_at = meta.get("finishedAt")
        if not finished_at:
            finished_at = datetime.datetime.fromtimestamp(
                folder_path.stat().st_mtime, datetime.timezone.utc
            ).isoformat()

        page_input_file = input_file(folder_path)
        page_source_type = source_type(meta)
        await asyncio.to_thread(generate_image_variants, folder_path)
        urls = image_urls(folder_name, asset_version(folder_path), page_input_file)
        has_inpainted = find_asset(folder_path, "inpainted") is not None
        has_regions = (folder_path / "text_regions.json").exists()
        has_bubble_mask = (folder_path / "bubble_mask.png").is_file()
        page_review_status = review_status(meta, folder_path)

        return {
            "id": meta.get("id") or folder_name,
            "groupId": manga_id(manga_title),
            "folder": folder_name,
            "originalName": original_name,
            "pageOrder": page_order(meta),
            "sourcePath": meta.get("sourcePath"),
            "mangaTitle": manga_title,
            **urls,
            "inpaintedUrl": f"/result/{folder_name}/inpainted.jpg" if has_inpainted else None,
            "textRegionsUrl": f"/result/{folder_name}/text_regions.json" if has_regions else None,
            "bubbleMaskUrl": f"/result/{folder_name}/bubble_mask.png" if has_bubble_mask else None,
            "hasTextRegions": has_regions,
            "sourceType": page_source_type,
            "finishedAt": finished_at,
            "settings": meta.get("settings", {}),
            "reviewStatus": page_review_status,
            "reviewedAt": meta.get("reviewedAt"),
            "needsReview": page_review_status == "pending",
        }

    @router.delete("/results/{folder_name}", tags=["api"])
    @router.delete("/api/results/{folder_name}", tags=["api"])
    @router.delete("/api/pages/{folder_name}", tags=["api"])
    async def delete_result(folder_name: str):
        """Delete a specific result directory"""
        store = get_store()
        result_root = get_result_root()
        if store is not None:
            resolved_folder = await store.resolve_folder(folder_name)
            if resolved_folder is None:
                raise HTTPException(404, detail="Result file not found")
            folder_name = resolved_folder
            folder_path = result_root / folder_name
            if not folder_path.is_dir() or result_file(folder_path) is None:
                raise HTTPException(404, detail="Result file not found")
            await store.delete_result(folder_name)
            await asyncio.to_thread(shutil.rmtree, folder_path, True)
            invalidate_meta_cache(folder_name)
            return {"message": f"Deleted result directory: {folder_name}"}

        result_dir = result_root.resolve()
        folder_path = result_dir / folder_name

        if not folder_path.exists():
            raise HTTPException(404, detail="Result directory not found")

        try:
            if result_file(folder_path) is None:
                raise HTTPException(404, detail="Result file not found")
            metadata = dict(get_cached_meta(folder_path))
            manga_title = (metadata.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
            shutil.rmtree(folder_path)
            invalidate_meta_cache(folder_name)
            compact_file_backed_group(result_dir, manga_title)
            return {"message": f"Deleted result directory: {folder_name}"}
        except Exception as error:
            raise HTTPException(500, detail=f"Error deleting result: {str(error)}")

    @router.post("/results/batch-delete", tags=["api"])
    @router.post("/api/results/batch-delete", tags=["api"])
    async def delete_results(data: DeletePagesRequest):
        folders = list(dict.fromkeys(data.folders))
        if any(not folder or Path(folder).name != folder or "\\" in folder for folder in folders):
            raise HTTPException(400, detail="Invalid result folder")

        store = get_store()
        result_root = get_result_root()
        try:
            if store is not None:
                deleted = await store.delete_results(folders)
                await asyncio.to_thread(_remove_result_directories, result_root, deleted)
            else:
                deleted = await asyncio.to_thread(delete_file_backed_results, result_root, folders)
        except ValueError as error:
            raise HTTPException(400, detail=str(error)) from error

        for folder in deleted:
            invalidate_meta_cache(folder)
        return {"deleted": len(deleted), "folders": deleted}

    return router, (clear_results, get_result_detail, delete_result, delete_results)


def _remove_result_directories(result_root: Path, folders: list[str]) -> None:
    for folder in folders:
        shutil.rmtree(result_root / folder, ignore_errors=True)
