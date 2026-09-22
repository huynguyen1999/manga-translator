"""Small helpers for the storage formats used by result folders."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image

def find_asset(folder: str | Path, stem: str) -> Path | None:
    folder = Path(folder)
    for suffix in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        candidate = folder / f"{stem}{suffix}"
        if candidate.is_file() and candidate.stat().st_size:
            return candidate
    return None


def save_jpeg(image: Image.Image | np.ndarray, path: str | Path, quality: int = 90) -> None:
    """Save an image as an RGB JPEG, flattening transparency onto white."""
    if isinstance(image, np.ndarray):
        image = Image.fromarray(image)
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        image = Image.alpha_composite(background, rgba).convert("RGB")
    elif image.mode not in {"L", "RGB"}:
        image = image.convert("RGB")
    temporary = Path(path).with_name(f".{Path(path).name}.tmp")
    image.save(temporary, format="JPEG", quality=quality, optimize=True, subsampling=0)
    os.replace(temporary, path)
