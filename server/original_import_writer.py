"""Filesystem writer for original manga import pages."""

import hashlib
import io
import json
import os
import secrets
import shutil
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from PIL import Image


def write_original_import(
    title: str,
    pages: Iterable[tuple[str, str, bytes, bytes, str]],
    group_id: str | None = None,
    *,
    result_root: Path,
    logger,
    save_jpeg: Callable[..., Any],
    import_job_id: str | None = None,
) -> dict:
    from datetime import datetime, timezone

    staging = Path(tempfile.mkdtemp(prefix=".original-import-", dir=result_root))
    moved: list[Path] = []
    records: list[dict[str, Any]] = []
    finished_at = datetime.now(timezone.utc).isoformat()
    try:
        for page_index, (original_name, source_path, content, normalized, suffix) in enumerate(pages):
            folder_name = (
                f"original-{hashlib.sha256(f'{import_job_id}:{page_index}'.encode()).hexdigest()[:24]}"
                if import_job_id else f"original-{secrets.token_hex(12)}"
            )
            destination = result_root / folder_name
            if destination.exists():
                try:
                    metadata = json.loads((destination / "meta.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise RuntimeError(f"Incomplete page from manga import job {import_job_id}") from error
                if metadata.get("importJobId") != import_job_id or metadata.get("importPageIndex") != page_index:
                    raise RuntimeError(f"Page folder collision during manga import: {folder_name}")
                records.append({"folder": folder_name, "metadata": metadata})
                continue
            folder = staging / folder_name
            folder.mkdir()
            with Image.open(io.BytesIO(normalized)) as normalized_image:
                save_jpeg(normalized_image, folder / "input.jpg")
            shutil.copyfile(folder / "input.jpg", folder / "final.jpg")
            metadata = {
                "id": folder_name,
                "originalName": original_name,
                "mangaTitle": title,
                "sourceType": "original",
                "finishedAt": finished_at,
                "sourcePath": source_path,
                "settings": {"translator": "none"},
            }
            if import_job_id:
                metadata["importJobId"] = import_job_id
                metadata["importPageIndex"] = page_index
            if group_id:
                metadata["mangaGroupId"] = group_id
                metadata["groupId"] = group_id
            records.append({
                "folder": folder_name,
                "metadata": metadata,
            })
            (folder / "meta.json").write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )
            if len(records) == 1 or len(records) % 25 == 0:
                logger.info("Original manga import staging: title=%r pages=%d", title, len(records))

        logger.info("Original manga import staged: title=%r pages=%d", title, len(records))
        for folder in staging.iterdir():
            destination = result_root / folder.name
            os.replace(folder, destination)
            moved.append(destination)
        staging.rmdir()
    except Exception:
        for destination in moved:
            shutil.rmtree(destination, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {"records": records}

