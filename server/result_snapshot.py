"""Build the file-backed snapshot stored for an indexed result page."""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Callable

from server.postgres_common import _page_order, _safe_folder


def natural_sort_key(value: str) -> str:
    return re.sub(
        r"\d+",
        lambda match: f"{int(match.group()):020d}",
        str(value).casefold(),
    )


def parse_finished_at(value: Any, fallback: dt.datetime) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return fallback


def page_snapshot(
    folder_path: Path,
    read_metadata: bool = True,
    read_regions: bool = True,
    metadata_override: dict[str, Any] | None = None,
    regions_override: list[Any] | None = None,
    generate_variants: bool = True,
    *,
    final_file: Callable[[Path], Path | None],
    input_file: Callable[[Path], Path | None],
    generate_image_variants: Callable[[Path], dict[str, Any]],
    asset_version: Callable[[Path], Any],
    find_asset: Callable[[Path, str], Path | None],
) -> dict[str, Any]:
    folder = _safe_folder(folder_path.name)
    final_path = final_file(folder_path)
    if final_path is None:
        raise ValueError(f"Missing final image: {folder}")
    metadata_path = folder_path / "meta.json"
    metadata: dict[str, Any] = copy.deepcopy(metadata_override or {})
    if read_metadata and not metadata and metadata_path.is_file():
        try:
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                metadata = value
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError(f"Malformed meta.json: {folder}") from error

    regions_path = folder_path / "text_regions.json"
    text_regions: list[Any] = copy.deepcopy(regions_override or [])
    if read_regions and not text_regions and regions_path.is_file():
        try:
            value = json.loads(regions_path.read_text(encoding="utf-8"))
            if not isinstance(value, list):
                raise ValueError("text regions are not an array")
            text_regions = value
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError(f"Malformed text_regions.json: {folder}") from error

    original_name = metadata.get("originalName")
    if not original_name or original_name == "Unknown":
        original_name = f"{folder}.png"
    manga_title = str(metadata.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
    manga_group_id = metadata.get("mangaGroupId") or metadata.get("groupId")
    if manga_group_id is not None:
        manga_group_id = str(manga_group_id).strip() or None
    input_path = input_file(folder_path)
    finished_at = parse_finished_at(
        metadata.get("finishedAt"),
        dt.datetime.fromtimestamp(final_path.stat().st_mtime, dt.timezone.utc),
    )
    artifact_names = {
        path.name
        for path in folder_path.iterdir()
        if path.is_file()
        and path.name not in {"meta.json", "text_regions.json"}
        and path.suffix.lower() != ".json"
    }
    documents: dict[str, Any] = {}
    for path in folder_path.glob("*.json"):
        if path.name in {"meta.json", "text_regions.json"}:
            continue
        try:
            documents[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError(f"Malformed {path.name}: {folder}") from error
    variants = generate_image_variants(folder_path) if generate_variants else {}
    artifact_names.update(f"{name}.webp" for name in variants)
    artifacts = [
        {
            "name": name,
            "relative_path": name,
            "size_bytes": (folder_path / name).stat().st_size,
        }
        for name in sorted(artifact_names)
        if (folder_path / name).is_file()
    ]
    return {
        "folder": folder,
        "manga_title": manga_title,
        "manga_group_id": manga_group_id,
        "original_name": str(original_name),
        "original_sort_key": natural_sort_key(str(original_name)),
        "page_order": _page_order(metadata.get("pageOrder")),
        "source_type": (
            "original"
            if metadata.get("sourceType") == "original"
            or (
                (metadata.get("settings") or {}).get("translator") == "none"
                and (metadata.get("settings") or {}).get("inpainter") == "original"
            )
            else "translated"
        ),
        "finished_at": finished_at,
        "request_id": metadata.get("requestId"),
        "input_name": input_path.name if input_path else None,
        "final_name": final_path.name,
        "has_inpainted": find_asset(folder_path, "inpainted") is not None,
        "has_regions": bool(text_regions),
        "has_thumbnail": "thumbnail.webp" in artifact_names,
        "asset_version": asset_version(folder_path),
        "metadata": metadata,
        "text_regions": text_regions,
        "documents": documents,
        "artifacts": artifacts,
    }
