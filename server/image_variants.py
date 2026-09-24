"""Small, deterministic WebP derivatives for result pages."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from manga_translator.utils.image_storage import find_asset

logger = logging.getLogger("manga-translator.image-variants")

# (maximum width, WebP quality).  Never upscale source pages.
VARIANT_SPECS: dict[str, tuple[int, int]] = {
    "batch": (160, 70),
    "cover": (480, 78),
    "preview": (480, 80),
    "reader": (1600, 82),
}

READER_ASSET_VARIANTS = {"input-reader.webp": "input", "inpainted-reader.webp": "inpainted"}


def source_file(folder_path: Path) -> Path | None:
    final = find_asset(folder_path, "final")
    if final is not None:
        return final
    candidate = find_asset(folder_path, "input")
    if candidate is not None:
        return candidate
    return None


def final_file(folder_path: Path) -> Path | None:
    return find_asset(folder_path, "final")


def asset_version(folder_path: Path) -> int:
    source = source_file(folder_path)
    return source.stat().st_mtime_ns if source else 0


def generate_image_variants(
    folder_path: Path | str,
    *,
    force: bool = False,
    include_cover: bool = False,
    only: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Generate missing/stale derivatives and return their file metadata."""
    folder = Path(folder_path)
    source = source_file(folder)
    if source is None:
        return {}
    if only is not None and only not in VARIANT_SPECS:
        raise ValueError(f"Unknown image variant: {only}")
    source_mtime = source.stat().st_mtime_ns
    result: dict[str, dict[str, Any]] = {}
    names = (only,) if only else tuple(name for name in VARIANT_SPECS if include_cover or name != "cover")
    if not force and all(
        (folder / f"{name}.webp").is_file()
        and (folder / f"{name}.webp").stat().st_size
        and (folder / f"{name}.webp").stat().st_mtime_ns >= source_mtime
        for name in names
    ):
        return {
            name: {"path": folder / f"{name}.webp", "bytes": (folder / f"{name}.webp").stat().st_size}
            for name in names
        }
    try:
        from PIL import Image

        with Image.open(source) as opened:
            mode = "RGBA" if "A" in opened.getbands() else "RGB"
            image = opened.convert(mode)
            for name in names:
                max_width, quality = VARIANT_SPECS[name]
                target = folder / f"{name}.webp"
                if (
                    not force
                    and target.is_file()
                    and target.stat().st_size
                    and target.stat().st_mtime_ns >= source_mtime
                ):
                    result[name] = {"path": target, "bytes": target.stat().st_size, "width": image.width, "height": image.height}
                    continue
                derivative = image.copy()
                if derivative.width > max_width:
                    height = max(1, round(derivative.height * max_width / derivative.width))
                    derivative = derivative.resize((max_width, height), Image.Resampling.LANCZOS)
                temporary = target.with_name(f".{target.name}.tmp")
                derivative.save(temporary, format="WEBP", quality=quality, method=2)
                os.replace(temporary, target)
                result[name] = {"path": target, "bytes": target.stat().st_size, "width": derivative.width, "height": derivative.height}
    except Exception as error:  # pragma: no cover - codec/runtime dependent
        logger.warning("Could not generate image variants for %s: %s", folder.name, error)
    return result


def generate_reader_asset(folder_path: Path | str, variant_name: str) -> Path | None:
    """Create a 1600px-or-smaller reader derivative of the source or clean image."""
    asset = READER_ASSET_VARIANTS.get(variant_name)
    if asset is None:
        raise ValueError(f"Unknown reader image variant: {variant_name}")
    folder = Path(folder_path)
    source = find_asset(folder, asset)
    if source is None:
        return None
    target = folder / variant_name
    if target.is_file() and target.stat().st_size and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return target

    try:
        from PIL import Image

        with Image.open(source) as opened:
            mode = "RGBA" if "A" in opened.getbands() else "RGB"
            image = opened.convert(mode)
            if image.width > 1600:
                image = image.resize((1600, max(1, round(image.height * 1600 / image.width))), Image.Resampling.LANCZOS)
            temporary = target.with_name(f".{target.name}.tmp")
            image.save(temporary, format="WEBP", quality=82, method=2)
            os.replace(temporary, target)
        return target
    except Exception as error:  # pragma: no cover - codec/runtime dependent
        logger.warning("Could not generate reader image %s for %s: %s", variant_name, folder.name, error)
        return None
