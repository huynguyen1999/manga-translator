#!/usr/bin/env python3
"""Devscript to run inpainting using hustvl/Moebius.

Accepts an image URL or local path, or a server page URL (/gallery/pages/<folder> or /result/<folder>/final.jpg),
copies/downloads the original image and corresponding inpainting mask (or generates/accepts a mask),
and runs Moebius inpainting on the target.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

import cv2
import numpy as np
from PIL import Image


def download_or_read_bytes(source: str, timeout: int = 30) -> bytes:
    """Download data from a URL or read from a local file path."""
    if source.startswith(("http://", "https://")):
        req = Request(source, headers={"User-Agent": "Mozilla/5.0 (MangaImageTranslator Devscript)"})
        try:
            with urlopen(req, timeout=timeout) as response:
                return response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code} fetching {source}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Could not reach {source}: {error.reason}") from error
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {source}")
    return path.read_bytes()


def parse_page_reference(value: str) -> tuple[str, str]:
    """Extract server origin and page identifier if value is a server page/result URL."""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return "", ""
    path_segments = [unquote(p) for p in parts.path.split("/") if p]
    origin = f"{parts.scheme}://{parts.netloc}"
    if len(path_segments) == 3 and path_segments[:2] == ["gallery", "pages"]:
        return origin, path_segments[2]
    if len(path_segments) >= 3 and path_segments[0] in {"result", "api", "results", "pages"}:
        if path_segments[0] == "api" and path_segments[1] in {"results", "pages"}:
            return origin, path_segments[2]
        if path_segments[0] in {"result", "results", "pages"}:
            return origin, path_segments[1]
    return "", ""


def fetch_server_page_assets(origin: str, page_ref: str) -> tuple[Image.Image, Image.Image]:
    """Fetch input image and inpaint mask for a server page reference."""
    ref_encoded = quote(page_ref, safe="")
    # Check pipeline-cases data or fetch directly
    img_urls = [
        f"{origin}/api/result/{ref_encoded}/input.png",
        f"{origin}/result/{ref_encoded}/input.png",
        f"{origin}/api/result/{ref_encoded}/input.jpg",
        f"{origin}/result/{ref_encoded}/input.jpg",
    ]
    mask_urls = [
        f"{origin}/api/result/{ref_encoded}/mask_final.png",
        f"{origin}/result/{ref_encoded}/mask_final.png",
        f"{origin}/api/result/{ref_encoded}/text_mask.png",
        f"{origin}/result/{ref_encoded}/text_mask.png",
        f"{origin}/api/result/{ref_encoded}/mask_raw.png",
        f"{origin}/result/{ref_encoded}/mask_raw.png",
    ]

    image = None
    for url in img_urls:
        try:
            data = download_or_read_bytes(url)
            image = Image.open(io.BytesIO(data)).convert("RGB")
            break
        except Exception:
            continue

    if image is None:
        raise RuntimeError(f"Could not retrieve input image for page '{page_ref}' from {origin}")

    mask = None
    for url in mask_urls:
        try:
            data = download_or_read_bytes(url)
            mask = Image.open(io.BytesIO(data)).convert("L")
            break
        except Exception:
            continue

    if mask is None:
        raise RuntimeError(f"Could not retrieve inpaint mask for page '{page_ref}' from {origin}")

    return image, mask


def prepare_inputs(
    image_source: str,
    mask_source: str | None = None,
    work_dir: Path | None = None,
) -> tuple[Image.Image, Image.Image, Path, Path]:
    """Resolve input image and mask from URLs or local files and save copies to work_dir."""
    if work_dir is None:
        work_dir = Path("./dataset.local")

    origin, page_ref = parse_page_reference(image_source)
    if origin and page_ref and mask_source is None:
        print(f"Detected server page URL: origin={origin}, page={page_ref}")
        image, mask = fetch_server_page_assets(origin, page_ref)
    else:
        print(f"Loading image from: {image_source}")
        img_bytes = download_or_read_bytes(image_source)
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")

        if mask_source:
            print(f"Loading mask from: {mask_source}")
            mask_bytes = download_or_read_bytes(mask_source)
            mask = Image.open(io.BytesIO(mask_bytes)).convert("L")
        else:
            raise ValueError(
                "Mask source (--mask) must be provided unless a manga-image-translator server page URL is given."
            )

    imgs_dir = work_dir / "imgs"
    masks_dir = work_dir / "masks"
    imgs_dir.mkdir(parents=True, exist_ok=True)
    masks_dir.mkdir(parents=True, exist_ok=True)

    img_path = imgs_dir / "sample.png"
    mask_path = masks_dir / "sample.png"

    image.save(img_path)
    mask.save(mask_path)
    print(f"Saved original image copy -> {img_path} ({image.size[0]}x{image.size[1]})")
    print(f"Saved mask copy           -> {mask_path} ({mask.size[0]}x{mask.size[1]})")

    return image, mask, img_path, mask_path


def pad_to_square(image: Image.Image, mask: Image.Image) -> tuple[Image.Image, Image.Image, tuple[int, int, int, int]]:
    """Pad non-square image and mask to a centered square as required by Moebius LλMI blocks."""
    w, h = image.size
    max_dim = max(w, h)
    pad_left = (max_dim - w) // 2
    pad_top = (max_dim - h) // 2

    square_img = Image.new("RGB", (max_dim, max_dim), (255, 255, 255))
    square_img.paste(image, (pad_left, pad_top))

    square_mask = Image.new("L", (max_dim, max_dim), 0)
    square_mask.paste(mask, (pad_left, pad_top))

    box = (pad_left, pad_top, pad_left + w, pad_top + h)
    return square_img, square_mask, box


def ensure_moebius_repo_and_weights(repo_dir: Path, weight_type: str = "pretrained") -> tuple[Path, Path]:
    """Ensure hustvl/Moebius repo is cloned and weights are downloaded."""
    if not (repo_dir / "infer" / "infer_moebius.py").is_file():
        print(f"Cloning https://github.com/hustvl/Moebius into {repo_dir} ...")
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        import subprocess
        subprocess.run(["git", "clone", "https://github.com/hustvl/Moebius.git", str(repo_dir)], check=True)

    weight_subpath = f"{weight_type}/diffusion_pytorch_model.bin"
    local_weight_file = repo_dir / "weight" / "Moebius" / weight_subpath
    if not local_weight_file.is_file():
        print(f"Downloading {weight_subpath} from Hugging Face (hustvl/Moebius) ...")
        local_weight_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            from huggingface_hub import hf_hub_download
            downloaded = hf_hub_download(
                repo_id="hustvl/Moebius",
                filename=weight_subpath,
                local_dir=str(repo_dir / "weight" / "Moebius"),
            )
            local_weight_file = Path(downloaded)
        except Exception as e:
            print(f"Failed to auto-download weights via huggingface_hub: {e}")
            print(f"Please download '{weight_subpath}' from https://huggingface.co/hustvl/Moebius into {local_weight_file}")
            raise

    return repo_dir, local_weight_file


def run_moebius_cli(
    moebius_repo_dir: Path,
    img_path: Path,
    mask_path: Path,
    output_dir: Path,
    cfg: float = 2.0,
    model_config: str = "config/model_cfg/moebius.yaml",
    weight_file: Path | None = None,
) -> Path | None:
    """Execute Moebius repository CLI infer script."""
    import subprocess

    if weight_file is None:
        weight_file = moebius_repo_dir / "weight/Moebius/pretrained/diffusion_pytorch_model.bin"

    # Make sure config file exists in repo
    cfg_file = moebius_repo_dir / model_config
    if not cfg_file.is_file():
        raise FileNotFoundError(f"Model config not found at: {cfg_file}")

    # Check device
    import torch
    if torch.cuda.is_available():
        device_str = "cuda"
    elif torch.backends.mps.is_available():
        device_str = "mps"
    else:
        device_str = "cpu"

    cmd = [
        sys.executable,
        "-m",
        "infer.infer_moebius",
        "--model-config",
        str(model_config),
        "--model-weight",
        str(weight_file.resolve()),
        "--real-dir",
        str(img_path.parent.resolve()),
        "--mask-dir",
        str(mask_path.parent.resolve()),
        "--save-dir",
        str(output_dir.resolve()),
        "--device",
        device_str,
        "--cfg",
        str(cfg),
        "--batch-size",
        "1",
        "--num-workers",
        "1",
    ]
    print("\nRunning Moebius inference:")
    print(" ".join(cmd))
    res = subprocess.run(cmd, cwd=str(moebius_repo_dir.resolve()), check=True)
    if res.returncode == 0:
        candidates = list(output_dir.glob(f"*{img_path.name}*")) or list(output_dir.glob("*.png")) + list(output_dir.glob("*.jpg"))
        if candidates:
            return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_url", help="Image URL, local path, or server page URL (e.g. /gallery/pages/<folder>)")
    parser.add_argument("--mask", help="Mask image URL or local path (optional if using server page URL)")
    parser.add_argument("--work-dir", default="./dataset.local", help="Directory to store copies of original and mask")
    parser.add_argument("--output", "-o", default="./outputs/inpainted.png", help="Output destination for inpainted image")
    parser.add_argument("--moebius-repo", default="./Moebius", help="Path to Moebius repository (auto-cloned if missing)")
    parser.add_argument("--weight-type", choices=["pretrained", "ft_celebahq", "ft_ffhq", "ft_places2"], default="pretrained", help="Moebius model weight variant")
    parser.add_argument("--cfg", type=float, default=2.0, help="Classifier-Free Guidance scale")

    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir)
    image, mask, img_file, mask_file = prepare_inputs(args.image_url, args.mask, work_dir=work_dir)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    moebius_repo_dir = Path(args.moebius_repo).resolve()
    repo_dir, weight_file = ensure_moebius_repo_and_weights(moebius_repo_dir, weight_type=args.weight_type)

    import time
    start_total_time = time.perf_counter()

    try:
        temp_out = output_path.parent / "_temp_moebius_out"
        temp_out.mkdir(parents=True, exist_ok=True)
        res = run_moebius_cli(
            repo_dir,
            img_file,
            mask_file,
            temp_out,
            cfg=args.cfg,
            weight_file=weight_file,
        )
        total_duration = time.perf_counter() - start_total_time
        if res and res.is_file():
            shutil.copyfile(res, output_path)
            shutil.rmtree(temp_out, ignore_errors=True)
            print(f"\n✨ Inpainting complete in {total_duration:.2f}s total wall-time! Result saved to -> {output_path}")
        else:
            print(f"Warning: Could not locate output file in {temp_out}")
    except Exception as e:
        print(f"\n[Error during Moebius inpainting]: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
