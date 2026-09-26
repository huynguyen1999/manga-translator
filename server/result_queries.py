from pathlib import Path
from typing import Optional

from manga_translator.utils.image_storage import find_asset
from server.constants import SERVER_RESULT_ROOT
from server.image_variants import asset_version, final_file
from server.manga_summary import load_summary
from server.result_metadata import (
    _get_cached_meta,
    _input_file,
    _manga_id,
    _review_status,
    _source_type,
    meta_page_order,
    natural_keys,
)

RESULT_ROOT = SERVER_RESULT_ROOT.resolve()


def _image_urls(
    folder_name: str,
    version: int,
    input_file: Optional[Path],
    *,
    include_cover: bool = False,
) -> dict[str, Optional[str]]:
    suffix = f"?v={version}" if version else ""
    base = f"/result/{folder_name}"
    urls = {
        "resultUrl": f"{base}/{(final_file(Path(RESULT_ROOT) / folder_name) or Path('final.png')).name}",
        "fullUrl": f"{base}/{(final_file(Path(RESULT_ROOT) / folder_name) or Path('final.png')).name}{suffix}",
        "thumbnailUrl": f"{base}/thumbnail.webp",
        "batchPreviewUrl": f"{base}/batch.webp{suffix}",
        "detailPreviewUrl": f"{base}/preview.webp{suffix}",
        "readerUrl": f"{base}/reader.webp{suffix}",
        "inputUrl": f"{base}/{input_file.name}" if input_file else None,
    }
    if include_cover:
        urls["coverUrl"] = f"{base}/cover.webp{suffix}"
    return urls

def _scan_results(
    result_dir: Path,
    sort: str,
    manga: Optional[str] = None,
    detail: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    review: Optional[str] = None,
):
    import datetime
    from pathlib import Path

    result_dir = Path(result_dir)
    items = []

    valid_dirs = [
        d for d in result_dir.iterdir()
        if d.is_dir() and not (d / ".ai-case").is_file() and final_file(d) is not None
    ]
    sorted_dirs = sorted(valid_dirs, key=lambda p: p.stat().st_mtime, reverse=True)

    clean_target_manga = manga.strip() if manga is not None else None
    is_slim = detail in ("reader", "slim")

    for item_path in sorted_dirs:
        folder_name = item_path.name
        meta = _get_cached_meta(item_path)

        review_status = _review_status(meta, item_path)
        if review == "pending" and review_status != "pending":
            continue

        manga_title = (meta.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"

        if clean_target_manga is not None and clean_target_manga not in {_manga_id(manga_title), manga_title}:
            continue

        original_name = meta.get("originalName")
        if not original_name or original_name == "Unknown":
            original_name = f"{folder_name}.png"

        finished_at = meta.get("finishedAt")
        if not finished_at:
            finished_at = datetime.datetime.fromtimestamp(
                item_path.stat().st_mtime, datetime.timezone.utc
            ).isoformat()

        input_file = _input_file(item_path)
        source_type = _source_type(meta)
        urls = _image_urls(folder_name, asset_version(item_path), input_file)

        if is_slim:
            items.append({
                "id": meta.get("id") or folder_name,
                "groupId": _manga_id(manga_title),
                "folder": folder_name,
                "originalName": original_name,
                "pageOrder": meta_page_order(meta),
                "sourcePath": meta.get("sourcePath"),
                "mangaTitle": manga_title,
                **urls,
                "sourceType": source_type,
                "finishedAt": finished_at,
                "reviewStatus": review_status,
                "reviewedAt": meta.get("reviewedAt"),
                "needsReview": review_status == "pending",
            })
        else:
            has_inpainted = find_asset(item_path, "inpainted") is not None
            has_regions = (item_path / "text_regions.json").exists()
            has_bubble_mask = (item_path / "bubble_mask.png").is_file()
            items.append({
                "id": meta.get("id") or folder_name,
                "groupId": _manga_id(manga_title),
                "folder": folder_name,
                "originalName": original_name,
                "pageOrder": meta_page_order(meta),
                "sourcePath": meta.get("sourcePath"),
                "mangaTitle": manga_title,
                **urls,
                "inpaintedUrl": f"/result/{folder_name}/inpainted.jpg" if has_inpainted else None,
                "textRegionsUrl": f"/result/{folder_name}/text_regions.json" if has_regions else None,
                "bubbleMaskUrl": f"/result/{folder_name}/bubble_mask.png" if has_bubble_mask else None,
                "hasTextRegions": has_regions,
                "sourceType": source_type,
                "finishedAt": finished_at,
                "settings": meta.get("settings", {}),
                "reviewStatus": review_status,
                "reviewedAt": meta.get("reviewedAt"),
                "needsReview": review_status == "pending",
            })

    has_page_order = any(item.get("pageOrder") is not None for item in items)
    if has_page_order:
        items.sort(
            key=lambda x: (
                x.get("pageOrder") is None,
                x.get("pageOrder") or 0,
                natural_keys(x["originalName"]),
                x["folder"],
            )
        )
    elif sort == "alpha_desc":
        items.sort(key=lambda x: natural_keys(x["originalName"]), reverse=True)
    elif sort == "date_asc":
        items.sort(key=lambda x: x["finishedAt"])
    elif sort == "date_desc":
        items.sort(key=lambda x: x["finishedAt"], reverse=True)
    else:
        items.sort(key=lambda x: (natural_keys(x["originalName"]), x["folder"]))

    total = len(items)
    if limit is None:
        visible_items = items
        next_offset = None
    else:
        page_size = max(1, min(int(limit), 500))
        page_offset = max(0, int(offset))
        visible_items = items[page_offset : page_offset + page_size]
        next_offset = page_offset + len(visible_items) if page_offset + len(visible_items) < total else None

    return {
        "directories": [item["folder"] for item in visible_items],
        "items": visible_items,
        "total": total,
        "nextOffset": next_offset,
    }

def _scan_manga_groups(
    result_dir: Path,
    limit: Optional[int] = None,
    offset: int = 0,
    manga_id: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "alpha-asc",
    review: Optional[str] = None,
    status: Optional[str] = None,
):
    import datetime
    from pathlib import Path

    result_dir = Path(result_dir)
    valid_dirs = [
        d for d in result_dir.iterdir()
        if d.is_dir() and not (d / ".ai-case").is_file() and final_file(d) is not None
    ]

    effective_status = "review" if review == "pending" or status == "review" else (status or "all")
    groups_map = {}
    query = search.strip().casefold() if search and search.strip() else ""

    for item_path in valid_dirs:
        folder_name = item_path.name
        meta = _get_cached_meta(item_path)
        review_status = _review_status(meta, item_path)
        if effective_status == "review" and review_status != "pending":
            continue

        manga_title = (meta.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
        if manga_id and manga_id not in {_manga_id(manga_title), manga_title}:
            continue
        if query and query not in manga_title.casefold():
            continue

        source_type = _source_type(meta)
        original_name = meta.get("originalName")
        if not original_name or original_name == "Unknown":
            original_name = f"{folder_name}.png"
        finished_at = meta.get("finishedAt")
        if not finished_at:
            finished_at = datetime.datetime.fromtimestamp(
                item_path.stat().st_mtime, datetime.timezone.utc
            ).isoformat()
        if manga_title not in groups_map:
            groups_map[manga_title] = {
                "id": _manga_id(manga_title),
                "title": manga_title,
                "count": 0,
                "translatedCount": 0,
                "originalCount": 0,
                "needsReviewCount": 0,
                "coverCandidate": None,
                "latestFinishedAt": finished_at,
            }

        grp = groups_map[manga_title]
        grp["count"] += 1
        if source_type == "original":
            grp["originalCount"] += 1
        else:
            grp["translatedCount"] += 1
        if review_status == "pending":
            grp["needsReviewCount"] += 1
        if finished_at > grp["latestFinishedAt"]:
            grp["latestFinishedAt"] = finished_at

        item_info = {
            "id": meta.get("id") or folder_name,
            "groupId": _manga_id(manga_title),
            "folder": folder_name,
            "originalName": original_name,
            "pageOrder": meta_page_order(meta),
            "sourcePath": meta.get("sourcePath"),
            "mangaTitle": manga_title,
            **_image_urls(folder_name, asset_version(item_path), _input_file(item_path)),
            "hasTextRegions": (item_path / "text_regions.json").exists(),
            "sourceType": source_type,
            "finishedAt": finished_at,
            "settings": meta.get("settings", {}),
            "reviewStatus": review_status,
            "reviewedAt": meta.get("reviewedAt"),
            "needsReview": review_status == "pending",
        }
        if grp["coverCandidate"] is None or (
            (item_info.get("pageOrder") is None, item_info.get("pageOrder") or 0,
             natural_keys(original_name), folder_name)
            < (grp["coverCandidate"].get("pageOrder") is None,
               grp["coverCandidate"].get("pageOrder") or 0,
               natural_keys(grp["coverCandidate"]["originalName"]),
               grp["coverCandidate"]["folder"])
        ):
            grp["coverCandidate"] = item_info

    for group in groups_map.values():
        candidate = group["coverCandidate"]
        if candidate is None:
            continue
        candidate_path = result_dir / candidate["folder"]
        candidate["coverUrl"] = _image_urls(
            candidate["folder"],
            asset_version(candidate_path),
            _input_file(candidate_path),
            include_cover=True,
        )["coverUrl"]

    groups = []
    total_images = 0
    for title, group in groups_map.items():
        saved = load_summary(result_dir, title)
        has_summary = bool(saved and saved.get("summary"))

        if effective_status == "translated" and group["translatedCount"] == 0:
            continue
        if effective_status == "original" and (group["originalCount"] == 0 or group["translatedCount"] > 0):
            continue
        if effective_status == "summarized" and not has_summary:
            continue
        if effective_status == "review" and group["needsReviewCount"] == 0:
            continue

        total_images += group["count"]
        groups.append({
            "id": group["id"],
            "title": title,
            "count": group["count"],
            "needsReviewCount": group["needsReviewCount"],
            "cover": group["coverCandidate"],
            "latestFinishedAt": group["latestFinishedAt"],
            "hasSummary": has_summary,
        })

    others = [group for group in groups if group["title"] != "Ungrouped"]
    ungrouped = [group for group in groups if group["title"] == "Ungrouped"]
    others.sort(key=lambda group: natural_keys(group["title"]), reverse=sort == "alpha-desc")
    if sort in {"date-asc", "date-desc"}:
        def timestamp(group):
            try:
                return datetime.datetime.fromisoformat(
                    str(group["latestFinishedAt"]).replace("Z", "+00:00")
                ).timestamp()
            except (TypeError, ValueError, OSError):
                return 0

        others.sort(key=timestamp, reverse=sort == "date-desc")
    groups = others + ungrouped

    if limit is None:
        visible_groups = groups
        next_offset = None
    else:
        page_size = max(1, min(int(limit), 500))
        page_offset = max(0, int(offset))
        visible_groups = groups[page_offset : page_offset + page_size]
        next_offset = page_offset + len(visible_groups) if page_offset + len(visible_groups) < len(groups) else None

    return {
        "groups": visible_groups,
        "totalGroups": len(groups),
        "totalImages": total_images,
        "nextOffset": next_offset,
    }
