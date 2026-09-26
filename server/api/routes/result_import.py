import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request


def create_result_import_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    get_max_batch_items: Callable[[], int],
    get_max_batch_item_bytes: Callable[[], int],
    get_max_title_length: Callable[[], int],
    has_file_backed_group: Callable[[str], bool],
    manga_id: Callable[[str], str],
    write_original_import: Callable[..., Any],
    iter_original_upload_pages: Callable[..., Any],
    compact_file_backed_group: Callable[..., Any],
    write_file_backed_meta: Callable[..., Any],
    scan_manga_groups: Callable[..., Any],
    scan_results: Callable[..., Any],
    invalidate_meta_cache: Callable[[], None],
    warm_preview_variants: Callable[[list[str]], Any],
    logger: Any,
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.post("/results/import", tags=["api", "gallery"])
    @router.post("/api/results/import", tags=["api", "gallery"])
    async def import_original_manga(request: Request, background_tasks: BackgroundTasks):
        max_items = get_max_batch_items()
        try:
            form = await request.form(
                max_files=max_items,
                max_fields=max_items,
                max_part_size=get_max_batch_item_bytes(),
            )
        except Exception as error:
            logger.exception("Failed to parse multipart body in import_original_manga")
            raise HTTPException(400, detail=f"Failed to parse upload: {error}") from error

        raw_title = form.get("mangaTitle")
        if hasattr(raw_title, "read"):
            raw_title = (await raw_title.read()).decode("utf-8")
        clean_title = str(raw_title or "").strip()
        if not clean_title or clean_title.casefold() == "ungrouped":
            raise HTTPException(400, detail="A manga title is required")
        max_title_length = get_max_title_length()
        if len(clean_title) > max_title_length:
            raise HTTPException(
                422,
                detail=f"Manga title must be at most {max_title_length} characters",
            )

        raw_group_id = form.get("mangaGroupId") or form.get("groupId")
        if hasattr(raw_group_id, "read"):
            raw_group_id = (await raw_group_id.read()).decode("utf-8")
        clean_group_id = str(raw_group_id or "").strip() or None

        raw_is_new_group = form.get("isNewGroup")
        if hasattr(raw_is_new_group, "read"):
            raw_is_new_group = (await raw_is_new_group.read()).decode("utf-8")
        is_new_group = str(raw_is_new_group or "").strip().lower() in ("true", "1") if raw_is_new_group is not None else None

        files = [
            value
            for _, value in form.multi_items()
            if hasattr(value, "file") and hasattr(value, "filename")
        ]
        if not files:
            raise HTTPException(400, detail="At least one image is required")
        if len(files) > max_items:
            raise HTTPException(413, detail="Manga contains too many pages")

        source_paths: list[str] | None = None
        raw_page_metadata = form.get("pageMetadata")
        if hasattr(raw_page_metadata, "read"):
            raw_page_metadata = (await raw_page_metadata.read()).decode("utf-8")
        if raw_page_metadata:
            try:
                metadata_entries = json.loads(str(raw_page_metadata))
                if isinstance(metadata_entries, list):
                    source_paths = [
                        str(entry.get("sourcePath") or entry.get("originalName") or "")
                        if isinstance(entry, dict) else ""
                        for entry in metadata_entries
                    ]
            except (TypeError, ValueError):
                raise HTTPException(400, detail="Invalid page metadata")

        store = get_store()
        resolved_group_id = None

        if clean_group_id and store is not None:
            resolved_group_id = await store.resolve_group_id(clean_group_id, create=False)
            if resolved_group_id is None:
                resolved_group_id = await store.resolve_group_id(clean_title, create=False)

        if store is not None and resolved_group_id is None:
            resolved_group_id = await store.resolve_group_id(clean_title, create=False)

        if is_new_group is True or (clean_group_id is None and is_new_group is None):
            duplicate = (
                await store.group_exists(clean_title)
                if store is not None
                else has_file_backed_group(clean_title)
            )
            if duplicate:
                raise HTTPException(409, detail="A manga with this title already exists")

        if store is not None and resolved_group_id is None:
            resolved_group_id = await store.resolve_group_id(clean_group_id or clean_title, create=True)

        target_group_id = resolved_group_id or clean_group_id or manga_id(clean_title)

        logger.info("Original manga import started: title=%r uploads=%d group_id=%r", clean_title, len(files), target_group_id)
        try:
            imported = await asyncio.to_thread(
                write_original_import,
                clean_title,
                iter_original_upload_pages(files, source_paths),
                target_group_id,
            )
            if store is not None:
                for record in imported["records"]:
                    await store.save_documents(
                        record["folder"], {"meta.json": record["metadata"]}
                    )
                    # Keep import completion fast; the detail preview is warmed after the response.
                    await store.sync_result_folder(record["folder"], generate_variants=False)
                groups_payload = await store.list_groups(limit=1, manga_id=target_group_id)
                if not groups_payload["groups"]:
                    groups_payload = await store.list_groups(limit=1, search=clean_title)
                pages_payload = await store.list_results(manga=clean_title, limit=500)
            else:
                result_root = get_result_root()
                imported_folders = {record["folder"] for record in imported["records"]}
                existing_pages = compact_file_backed_group(result_root, clean_title, imported_folders)
                next_page_order = len(existing_pages) + 1
                for record in imported["records"]:
                    record["metadata"]["pageOrder"] = next_page_order
                    next_page_order += 1
                    write_file_backed_meta(result_root, record["folder"], record["metadata"])
                groups_payload = await asyncio.to_thread(
                    scan_manga_groups, result_root, 1, 0, None, clean_title, "alpha-asc"
                )
                pages_payload = await asyncio.to_thread(
                    scan_results, result_root, "alpha", clean_title, None, 500, 0, None
                )
                invalidate_meta_cache()
            if not groups_payload["groups"]:
                raise RuntimeError("Imported manga group was not found after saving")
            background_tasks.add_task(
                warm_preview_variants,
                [record["folder"] for record in imported["records"]],
            )
            return {
                "group": groups_payload["groups"][0],
                "items": pages_payload["items"],
                "totalImages": groups_payload["totalImages"],
            }
        except HTTPException:
            raise
        except Exception as error:
            logger.exception("Original manga import failed")
            raise HTTPException(500, detail="Could not import manga") from error
        finally:
            await asyncio.gather(*(upload.close() for upload in files if hasattr(upload, "close")), return_exceptions=True)

    return router, (import_original_manga,)
