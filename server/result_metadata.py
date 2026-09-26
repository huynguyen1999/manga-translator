import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from manga_translator.utils.image_storage import find_asset
from server.image_variants import final_file
from server.manga_summary import group_pages
from server.postgres_store import GroupNotFound


def natural_keys(text: str):
    """Natural sort key for strings with numbers (e.g. page_1 before page_10)"""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', str(text))]

def page_order_value(value: Any) -> Optional[int]:
    try:
        order = int(value)
    except (TypeError, ValueError):
        return None
    return order if order > 0 else None

def meta_page_order(metadata: dict[str, Any]) -> Optional[int]:
    return page_order_value(metadata.get("pageOrder"))

_META_CACHE: dict[str, tuple[float, dict]] = {}

def _get_cached_meta(folder_path: Path) -> dict:
    folder_name = folder_path.name
    meta_file = folder_path / "meta.json"
    if not meta_file.exists():
        return {}
    try:
        mtime = meta_file.stat().st_mtime
    except OSError:
        return {}
    cached = _META_CACHE.get(folder_name)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        parsed = json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception:
        parsed = {}
    _META_CACHE[folder_name] = (mtime, parsed)
    return parsed

def _invalidate_meta_cache(folder_name: Optional[str] = None):
    if folder_name is None:
        _META_CACHE.clear()
    else:
        _META_CACHE.pop(folder_name, None)

def _write_file_backed_meta(item_path: Path, metadata: dict[str, Any]) -> None:
    meta_path = item_path / "meta.json"
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=item_path, prefix=".meta.", suffix=".tmp", delete=False
        ) as temporary:
            json.dump(metadata, temporary, ensure_ascii=False, indent=2)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, meta_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    _invalidate_meta_cache(item_path.name)

def _compact_file_backed_group(
    result_dir: Path,
    title: str,
    excluded_folders: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    excluded = excluded_folders or set()
    pages = [page for page in group_pages(result_dir, title) if page["folder"] not in excluded]
    for index, page in enumerate(pages, 1):
        metadata = dict(page["meta"])
        if metadata.get("pageOrder") == index:
            continue
        metadata["pageOrder"] = index
        _write_file_backed_meta(page["path"], metadata)
    return pages

def _delete_file_backed_results(result_dir: Path, folders: list[str]) -> list[str]:
    result_root = result_dir.resolve()
    paths = [(folder, (result_root / folder).resolve()) for folder in folders]
    if any(path.parent != result_root for _, path in paths):
        raise ValueError("Invalid result folder")

    deleted: list[str] = []
    titles: set[str] = set()
    for folder, folder_path in paths:
        if not folder_path.is_dir() or final_file(folder_path) is None:
            continue
        metadata = _get_cached_meta(folder_path)
        title = (metadata.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
        shutil.rmtree(folder_path)
        _invalidate_meta_cache(folder)
        deleted.append(folder)
        titles.add(title)
    for title in titles:
        _compact_file_backed_group(result_root, title)
    return deleted

def _update_file_backed_meta(
    result_dir: Path,
    folders: Optional[List[str]],
    old_title: Optional[str],
    new_title: str,
    group_id: Optional[str],
) -> tuple[Optional[str], int]:
    requested = set(folders or [])
    matched_title: Optional[str] = None
    matches: list[tuple[Path, dict[str, Any], str]] = []
    rename_group = group_id is not None or (not requested and old_title is not None)

    if not result_dir.exists():
        return old_title, 0

    for item_path in result_dir.iterdir():
        if not item_path.is_dir() or final_file(item_path) is None:
            continue
        metadata = dict(_get_cached_meta(item_path))
        current_title = (metadata.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
        matches_group = group_id is not None and group_id in {_manga_id(current_title), current_title}
        matches_page = item_path.name in requested or metadata.get("id") in requested
        matches_title = rename_group and old_title is not None and current_title == old_title
        if not (matches_group or matches_page or matches_title):
            continue
        matches.append((item_path, metadata, current_title))
        matched_title = matched_title or current_title

    if group_id is not None and not matches:
        raise GroupNotFound("Manga group not found")
    if not matches:
        return matched_title or old_title, 0

    if not rename_group:
        selected_folders = {item_path.name for item_path, _, _ in matches}
        _compact_file_backed_group(result_dir, new_title)
        destination_pages = _compact_file_backed_group(result_dir, new_title, selected_folders)
        next_page_order = len(destination_pages) + 1
        for item_path, metadata, _ in sorted(
            matches,
            key=lambda item: (
                meta_page_order(item[1]) is None,
                meta_page_order(item[1]) or 0,
                natural_keys(str(item[1].get("originalName") or item[0].name)),
                item[0].name,
            ),
        ):
            metadata["mangaTitle"] = new_title
            metadata["pageOrder"] = next_page_order
            next_page_order += 1
            _write_file_backed_meta(item_path, metadata)
        for source_title in {current_title for _, _, current_title in matches}:
            _compact_file_backed_group(result_dir, source_title)
    else:
        for item_path, metadata, _ in matches:
            metadata["mangaTitle"] = new_title
            _write_file_backed_meta(item_path, metadata)

    return matched_title or old_title, len(matches)

def _reorder_file_backed_pages(
    result_dir: Path,
    group_value: str,
    page_ids: list[str],
) -> list[dict[str, Any]]:
    if not page_ids or len(set(page_ids)) != len(page_ids):
        raise ValueError("pageIds must contain every page exactly once")

    titles: set[str] = set()
    for item_path in result_dir.iterdir() if result_dir.is_dir() else []:
        if not item_path.is_dir() or final_file(item_path) is None:
            continue
        metadata = _get_cached_meta(item_path)
        title = (metadata.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
        if group_value in {title, _manga_id(title)}:
            titles.add(title)

    if not titles:
        raise GroupNotFound("Manga group not found")
    if len(titles) > 1:
        raise ValueError("Manga group identifier is ambiguous")

    pages = group_pages(result_dir, next(iter(titles)))
    page_keys = [page["meta"].get("id") or page["folder"] for page in pages]
    if len(set(page_keys)) != len(page_keys) or set(page_keys) != set(page_ids):
        raise ValueError("pageIds must contain every active page in the manga group")

    by_id = {page["meta"].get("id") or page["folder"]: page for page in pages}
    for index, page_id in enumerate(page_ids, 1):
        metadata = dict(by_id[page_id]["meta"])
        metadata["pageOrder"] = index
        _write_file_backed_meta(by_id[page_id]["path"], metadata)
    return [{"id": page_id, "pageOrder": index} for index, page_id in enumerate(page_ids, 1)]

def _source_type(meta: dict) -> str:
    if meta.get("sourceType") == "original":
        return "original"
    settings = meta.get("settings") or {}
    if settings.get("translator") == "none" and settings.get("inpainter") == "original":
        return "original"
    return "translated"

def _review_status(meta: dict, folder_path: Optional[Path] = None) -> str:
    status = meta.get("reviewStatus")
    if status == "pending":
        return status
    if folder_path is not None:
        try:
            regions = json.loads((folder_path / "text_regions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            regions = []
        if any(isinstance(region, dict) and region.get("review_required") for region in regions):
            return "pending"
    return status if status in {"approved", "not_required"} else "not_required"

def _review_status_for_regions(regions: list[dict]) -> str:
    return "pending" if any(region.get("review_required") for region in regions) else "approved"

def _manga_id(title: str) -> str:
    value = (title or "Ungrouped").strip() or "Ungrouped"
    hashed = 2166136261
    for byte in value.encode("utf-8"):
        hashed = ((hashed ^ byte) * 16777619) & 0xFFFFFFFF
    return f"manga-{hashed:x}"

def _input_file(folder_path: Path) -> Optional[Path]:
    return find_asset(folder_path, "input")
