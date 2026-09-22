#!/usr/bin/env python3
"""Downscale stale translated results to their sibling input dimensions."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from manga_translator.utils.image_storage import find_asset

INPUT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def find_input(folder: Path) -> Path | None:
    preferred = folder / "input.png"
    if preferred.is_file() and preferred.stat().st_size:
        return preferred
    for candidate in sorted(folder.glob("input.*")):
        if candidate.is_file() and candidate.stat().st_size and candidate.suffix.lower() in INPUT_SUFFIXES:
            return candidate
    return None


def is_original(folder: Path) -> bool:
    if folder.name.startswith("original-"):
        return True
    metadata = folder / "meta.json"
    if not metadata.is_file():
        return False
    try:
        value = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(value, dict) and value.get("sourceType") == "original"


def inspect_folder(folder: Path) -> tuple[Path, tuple[int, int], tuple[int, int]] | None:
    if not folder.is_dir() or folder.name.startswith(".") or is_original(folder):
        return None
    final = folder / "final.png"
    source = find_input(folder)
    if not final.is_file() or not final.stat().st_size or source is None:
        return None
    with Image.open(final) as output, Image.open(source) as original:
        output_size = output.size
        original_size = original.size
    if output_size == original_size:
        return None
    if output_size[0] < original_size[0] or output_size[1] < original_size[1]:
        return None
    return final, output_size, original_size


def backup_file(path: Path, backup_root: Path) -> None:
    backup = backup_root / path.parent.name / path.name
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copy2(path, backup)


def resize_file(path: Path, target_size: tuple[int, int], backup_root: Path) -> None:
    backup_file(path, backup_root)
    with Image.open(path) as image:
        resized = image.resize(target_size, Image.Resampling.BICUBIC)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=path.suffix, dir=path.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            resized.save(temporary, format="PNG" if path.suffix.lower() == ".png" else None)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def scale_editor_regions(path: Path, scale_x: float, scale_y: float, backup_root: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid editor metadata: {path.name}") from error
    if not isinstance(data, list):
        raise ValueError(f"editor metadata is not a list: {path.name}")

    for region in data:
        if not isinstance(region, dict):
            continue
        for key, factor in (("x", scale_x), ("width", scale_x), ("y", scale_y), ("height", scale_y),
                            ("font_size", scale_y), ("stroke_width", scale_y), ("letter_spacing", scale_x)):
            if isinstance(region.get(key), (int, float)):
                region[key] = round(region[key] * factor, 4)
        lines = region.get("lines")
        if isinstance(lines, list):
            for line in lines:
                if not isinstance(line, list):
                    continue
                for point in line:
                    if isinstance(point, list) and len(point) >= 2:
                        if isinstance(point[0], (int, float)):
                            point[0] = round(point[0] * scale_x, 4)
                        if isinstance(point[1], (int, float)):
                            point[1] = round(point[1] * scale_y, 4)
        bounds = region.get("layout_bounds")
        if isinstance(bounds, dict):
            for key, factor in (("x", scale_x), ("width", scale_x), ("y", scale_y), ("height", scale_y)):
                if isinstance(bounds.get(key), (int, float)):
                    bounds[key] = round(bounds[key] * factor, 4)

    backup_file(path, backup_root)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_root", type=Path, help="Results directory, e.g. workspace/results")
    parser.add_argument("--apply", action="store_true", help="Write resized final.png files")
    args = parser.parse_args()

    root = args.results_root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"results directory does not exist: {root}")

    backup_root = root / ".downscale-backups"
    candidates = []
    for folder in sorted(root.iterdir(), key=lambda path: path.name):
        inspected = inspect_folder(folder)
        if inspected is not None:
            candidates.append((folder, inspected))

    if not candidates:
        print("No oversized translated results found.")
        return 0

    action = "would downscale" if not args.apply else "downscaled"
    for folder, (final, output_size, original_size) in candidates:
        print(f"{action}: {folder.name}/final.png {output_size[0]}x{output_size[1]} -> {original_size[0]}x{original_size[1]}")
        if args.apply:
            scale_x = original_size[0] / output_size[0]
            scale_y = original_size[1] / output_size[1]
            resize_file(final, original_size, backup_root)
            inpainted = find_asset(folder, "inpainted")
            if inpainted is not None:
                resize_file(inpainted, original_size, backup_root)
            text_regions = folder / "text_regions.json"
            if text_regions.is_file() and text_regions.stat().st_size:
                scale_editor_regions(text_regions, scale_x, scale_y, backup_root)
            from server.image_variants import generate_image_variants

            generate_image_variants(folder, include_cover=(folder / "cover.webp").exists())

    if not args.apply:
        print(f"Dry run only. Re-run with --apply to update {len(candidates)} folder(s).")
    else:
        print(f"Backups: {backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
