"""Filesystem helpers for importing original manga pages."""

import io
import json
import os
import secrets
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageFile, ImageOps


def validate_original_upload(
    content: bytes,
    filename: str,
    *,
    supported_formats: dict[str, str],
    logger,
) -> tuple[bytes, str]:
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    Image.MAX_IMAGE_PIXELS = None

    try:
        with Image.open(io.BytesIO(content)) as image:
            try:
                image = ImageOps.exif_transpose(image) or image
            except Exception:
                pass
            image.load()
            raw_fmt = (image.format or "").upper()
            suffix = supported_formats.get(raw_fmt, ".png")
            normalized = io.BytesIO()
            has_alpha = (
                "A" in image.getbands()
                or image.mode in ("RGBA", "LA", "PA")
                or bool(image.info.get("transparency") is not None)
            )
            mode = "RGBA" if has_alpha else "RGB"
            image.convert(mode).save(normalized, format="PNG")
    except HTTPException:
        raise
    except Exception as error:
        logger.warning("Failed to validate image %s: %s", filename, error, exc_info=True)
        raise HTTPException(400, detail=f"Invalid image: {filename}") from error
    return normalized.getvalue(), suffix


def is_archive_upload(filename: str, content: bytes) -> bool:
    ext = Path(filename).suffix.casefold()
    if ext in {".cbz", ".zip"}:
        return True
    return content.startswith(b"PK\x03\x04") or content.startswith(b"PK\x05\x06")


def iter_archive_pages(
    source: BinaryIO,
    archive_filename: str,
    *,
    max_item_bytes: int,
    natural_keys: Callable[[str], Any],
    validate_upload: Callable[[bytes, str], tuple[bytes, str]],
    logger,
) -> Iterator[tuple[str, str, bytes, bytes, str]]:
    try:
        # Python 3.10's SpooledTemporaryFile lacks the seekable attribute ZipFile expects.
        zf = zipfile.ZipFile(getattr(source, "_file", source))
    except Exception as error:
        raise HTTPException(400, detail=f"Invalid or corrupted archive: {archive_filename}") from error

    valid_entries: list[zipfile.ZipInfo] = []
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            parts = Path(name).parts
            if any(part.startswith(".") or part == "__MACOSX" for part in parts):
                continue
            ext = Path(name).suffix.casefold()
            if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif", ".avif", ".tga", ".jfif"}:
                valid_entries.append(info)

        if not valid_entries:
            raise HTTPException(400, detail=f"No supported images found in archive: {archive_filename}")

        valid_entries.sort(key=lambda info: natural_keys(info.filename))
        for info in valid_entries:
            if info.file_size > max_item_bytes:
                raise HTTPException(413, detail=f"Archive image is too large: {info.filename}")
            try:
                with zf.open(info) as entry:
                    entry_bytes = entry.read(max_item_bytes + 1)
            except Exception as error:
                logger.exception("Failed reading archive entry %s from %s", info.filename, archive_filename)
                raise HTTPException(400, detail=f"Failed reading archive entry: {info.filename}") from error
            if len(entry_bytes) > max_item_bytes:
                raise HTTPException(413, detail=f"Archive image is too large: {info.filename}")

            raw_basename = Path(info.filename).name
            normalized, suffix = validate_upload(entry_bytes, raw_basename)
            yield raw_basename, f"{archive_filename}/{info.filename}", entry_bytes, normalized, suffix


def iter_original_upload_pages(
    uploads: list[UploadFile],
    source_paths: list[str] | None = None,
    *,
    max_batch_items: int,
    max_item_bytes: int,
    max_import_bytes: int,
    is_archive_upload: Callable[[str, bytes], bool],
    iter_archive_pages: Callable[..., Iterator[tuple[str, str, bytes, bytes, str]]],
    validate_upload: Callable[[bytes, str], tuple[bytes, str]],
) -> Iterator[tuple[str, str, bytes, bytes, str]]:
    page_count = 0
    total_bytes = 0

    for upload_index, upload in enumerate(uploads):
        filename = (upload.filename or "").strip()
        if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise HTTPException(400, detail="Invalid uploaded filename")

        upload.file.seek(0)
        header = upload.file.read(4)
        upload.file.seek(0)
        source_path = source_paths[upload_index] if source_paths and upload_index < len(source_paths) else filename
        if is_archive_upload(filename, header):
            pages = iter_archive_pages(upload.file, source_path)
        else:
            content = upload.file.read(max_item_bytes + 1)
            if len(content) > max_item_bytes:
                raise HTTPException(413, detail=f"Image is too large: {filename}")
            normalized, suffix = validate_upload(content, filename)
            pages = iter(((filename, source_path, content, normalized, suffix),))

        for page in pages:
            page_count += 1
            if page_count > max_batch_items:
                raise HTTPException(413, detail="Manga contains too many pages")
            total_bytes += len(page[2])
            if total_bytes > max_import_bytes:
                raise HTTPException(413, detail="Manga import exceeds 20 GB")
            yield page

    if page_count == 0:
        raise HTTPException(400, detail="At least one image is required")


def write_original_import(
    title: str,
    pages: Iterable[tuple[str, str, bytes, bytes, str]],
    group_id: str | None = None,
    *,
    result_root: Path,
    logger,
    save_jpeg: Callable[..., Any],
) -> dict:
    from datetime import datetime, timezone

    staging = Path(tempfile.mkdtemp(prefix=".original-import-", dir=result_root))
    moved: list[Path] = []
    records: list[dict[str, Any]] = []
    finished_at = datetime.now(timezone.utc).isoformat()
    try:
        for original_name, source_path, content, normalized, suffix in pages:
            folder_name = f"original-{secrets.token_hex(12)}"
            folder = staging / folder_name
            folder.mkdir()
            with Image.open(io.BytesIO(normalized)) as normalized_image:
                save_jpeg(normalized_image, folder / "input.jpg")
                save_jpeg(normalized_image, folder / "final.jpg")
            metadata = {
                "id": folder_name,
                "originalName": original_name,
                "mangaTitle": title,
                "sourceType": "original",
                "finishedAt": finished_at,
                "sourcePath": source_path,
                "settings": {"translator": "none"},
            }
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


def warm_preview_variants(
    folders: list[str],
    *,
    result_root: Path,
    generate_variants: Callable[..., Any],
    logger,
) -> None:
    failures = 0
    for folder in folders:
        try:
            generate_variants(result_root / folder, only="preview")
        except Exception:
            failures += 1
    if failures:
        logger.warning("Preview warmup failed for %d/%d imported pages", failures, len(folders))
