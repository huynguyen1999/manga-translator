#!/usr/bin/env python3
"""Compare Manga-Text-Segmentation-2025 with detect-20241225.ckpt.

This development script evaluates and compares the Hugging Face model
`a-b-c-x-y-z/Manga-Text-Segmentation-2025` (Unet++ EfficientNetv2) with
the repository's baseline text detector `detect-20241225.ckpt` (DBNet ResNet-34).

Usage examples:
    # Compare both models on a single image (auto device: MPS/CUDA/CPU)
    python devscripts/evaluate_text_segmentation.py --input test/testdata/render/default1.png

    # Run on a directory of images and save visual outputs to a custom folder
    python devscripts/evaluate_text_segmentation.py --input-dir /path/to/pages --output-dir result/seg_comparison

    # Try only the Hugging Face model with TTA enabled
    python devscripts/evaluate_text_segmentation.py --input page.png --mode hf --tta

    # Try only the repo baseline model
    python devscripts/evaluate_text_segmentation.py --input page.png --mode repo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
GENERATED_SUFFIXES = (
    "-comparison.png",
    "-hf-mask.png",
    "-hf-overlay.png",
    "-hf-cleaned.png",
    "-hf-boxes.png",
    "-repo-mask.png",
    "-repo-overlay.png",
    "-repo-boxes.png",
    "-diff-map.png",
)

HF_REPO_ID = "a-b-c-x-y-z/Manga-Text-Segmentation-2025"
HF_WEIGHT_FILE = "model.pth"
REPO_MODEL_DEFAULT_PATH = PROJECT_ROOT / "models" / "detection" / "detect-20241225.ckpt"


def select_device(preferred: str = "auto") -> str:
    """Resolve the target device (cuda, mps, or cpu)."""
    if preferred != "auto":
        return preferred
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def convert_batchnorm_to_groupnorm(module: nn.Module) -> None:
    """Convert BatchNorm2d layers in decoder to GroupNorm for inference compatibility."""
    for name, child in module.named_children():
        if isinstance(child, nn.BatchNorm2d):
            num_channels = child.num_features
            num_groups = 8
            if num_channels < num_groups or num_channels % num_groups != 0:
                for i in range(min(num_channels, 8), 1, -1):
                    if num_channels % i == 0:
                        num_groups = i
                        break
                else:
                    num_groups = 1
            setattr(module, name, nn.GroupNorm(num_groups=num_groups, num_channels=num_channels))
        else:
            convert_batchnorm_to_groupnorm(child)


def resolve_hf_model(custom_path: Optional[Path] = None) -> Path:
    """Download or locate the Hugging Face model weights."""
    if custom_path and custom_path.is_file():
        return custom_path

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is required to download weights. Install via `pip install huggingface_hub`."
        ) from exc

    downloaded = hf_hub_download(repo_id=HF_REPO_ID, filename=HF_WEIGHT_FILE)
    return Path(downloaded)


def build_hf_model(weight_path: Path, device: str) -> nn.Module:
    """Build and load the Manga-Text-Segmentation-2025 Unet++ model."""
    try:
        import segmentation_models_pytorch as smp
    except ImportError as exc:
        raise RuntimeError(
            "segmentation_models_pytorch is required. Install via `pip install segmentation-models-pytorch --no-deps`."
        ) from exc

    model = smp.UnetPlusPlus(
        encoder_name="tu-efficientnetv2_rw_m",
        encoder_weights=None,
        in_channels=3,
        classes=1,
        activation=None,
        decoder_attention_type="scse",
    )
    convert_batchnorm_to_groupnorm(model.decoder)

    state_dict = torch.load(weight_path, map_location="cpu")
    if "state_dict" in state_dict:
        state_dict = state_dict["state_dict"]
    model.load_state_dict(state_dict)

    if device.startswith("cuda") or device == "mps":
        model = model.to(device)
    model.eval()
    return model


class MangaTextSegmentation2025:
    """Wrapper for running inference with Manga-Text-Segmentation-2025."""

    def __init__(self, weight_path: Optional[Path] = None, device: str = "auto"):
        self.device = select_device(device)
        self.weight_path = resolve_hf_model(weight_path)
        self.model = build_hf_model(self.weight_path, self.device)
        self.mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1).to(self.device)
        self.std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1).to(self.device)

    def predict(
        self,
        image_rgb: np.ndarray,
        threshold: float = 0.5,
        tta: bool = False,
        gap_closing: int = 0,
        fill_holes: bool = False,
        padding_iter: int = 2,
    ) -> Dict[str, Any]:
        """Run text segmentation on an RGB numpy image."""
        h, w = image_rgb.shape[:2]
        pad_h = (32 - h % 32) % 32
        pad_w = (32 - w % 32) % 32

        # Normalize with ImageNet mean/std
        tensor = torch.from_numpy(image_rgb).float().permute(2, 0, 1).unsqueeze(0).to(self.device) / 255.0
        tensor = (tensor - self.mean) / self.std

        if pad_h > 0 or pad_w > 0:
            tensor = F.pad(tensor, (0, pad_w, 0, pad_h), mode="constant", value=0)

        steps = 1 + (2 if tta else 0)
        accumulated_probs = None

        start_time = time.perf_counter()
        with torch.no_grad():
            if self.device == "cuda":
                with torch.amp.autocast("cuda"):
                    logits = self.model(tensor)
                    probs = logits.sigmoid()
            else:
                logits = self.model(tensor)
                probs = logits.sigmoid()
            accumulated_probs = probs

            if tta:
                # Horizontal flip
                tensor_hf = torch.flip(tensor, [3])
                logits_hf = self.model(tensor_hf)
                accumulated_probs += torch.flip(logits_hf.sigmoid(), [3])

                # Vertical flip
                tensor_vf = torch.flip(tensor, [2])
                logits_vf = self.model(tensor_vf)
                accumulated_probs += torch.flip(logits_vf.sigmoid(), [2])

        final_probs = accumulated_probs / steps
        prob_map = final_probs[0, 0, :h, :w].detach().cpu().numpy()
        inference_time_ms = (time.perf_counter() - start_time) * 1000.0

        # Post-processing
        binary_mask = (prob_map > threshold).astype(np.uint8) * 255

        if gap_closing > 0:
            k_size = int(gap_closing)
            if k_size % 2 == 0:
                k_size += 1
            kernel_morph = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_size, k_size))
            binary_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_CLOSE, kernel_morph)

        if fill_holes:
            contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(binary_mask, contours, -1, 255, -1)

        # White-out / Cleaned image
        cleaned = image_rgb.copy()
        if padding_iter > 0:
            kernel_pad = np.ones((3, 3), np.uint8)
            whiteout_mask = cv2.dilate(binary_mask, kernel_pad, iterations=int(padding_iter))
        else:
            whiteout_mask = binary_mask
        cleaned[whiteout_mask == 255] = [255, 255, 255]

        # Extract contours / bounding boxes
        contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for cnt in contours:
            if cv2.contourArea(cnt) < 16:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            boxes.append([x, y, x + bw, y + bh])

        return {
            "prob_map": prob_map,
            "binary_mask": binary_mask,
            "cleaned": cleaned,
            "whiteout_mask": whiteout_mask,
            "boxes": boxes,
            "contour_count": len(contours),
            "box_count": len(boxes),
            "inference_time_ms": inference_time_ms,
        }


class RepoBaselineDetector:
    """Wrapper for running detect-20241225.ckpt from manga_translator."""

    def __init__(self, weight_path: Optional[Path] = None, device: str = "auto"):
        self.device = select_device(device)
        self.weight_path = weight_path or REPO_MODEL_DEFAULT_PATH

        from manga_translator.detection.default import DefaultDetector

        self.detector = DefaultDetector()
        asyncio.run(self.detector.load(self.device))

    def predict(
        self,
        image_rgb: np.ndarray,
        detect_size: int = 1024,
        text_threshold: float = 0.6,
        box_threshold: float = 0.6,
        unclip_ratio: float = 1.5,
    ) -> Dict[str, Any]:
        """Run DBNet detection on RGB numpy image."""
        start_time = time.perf_counter()
        textlines, raw_mask, _ = asyncio.run(
            self.detector.detect(
                image_rgb,
                detect_size=detect_size,
                text_threshold=text_threshold,
                box_threshold=box_threshold,
                unclip_ratio=unclip_ratio,
                invert=False,
                gamma_correct=False,
                rotate=False,
                auto_rotate=False,
                verbose=False,
            )
        )
        inference_time_ms = (time.perf_counter() - start_time) * 1000.0

        h, w = image_rgb.shape[:2]
        if raw_mask.shape[:2] != (h, w):
            raw_mask = cv2.resize(raw_mask, (w, h), interpolation=cv2.INTER_LINEAR)

        binary_mask = (raw_mask > (text_threshold * 255)).astype(np.uint8) * 255

        boxes = []
        polys = []
        for line in textlines:
            pts = line.pts if hasattr(line, "pts") else np.array(line)
            polys.append(pts)
            xmin = int(np.min(pts[:, 0]))
            ymin = int(np.min(pts[:, 1]))
            xmax = int(np.max(pts[:, 0]))
            ymax = int(np.max(pts[:, 1]))
            boxes.append([xmin, ymin, xmax, ymax])

        # Cleaned white-out image
        cleaned = image_rgb.copy()
        cleaned[binary_mask == 255] = [255, 255, 255]

        return {
            "raw_mask": raw_mask,
            "binary_mask": binary_mask,
            "cleaned": cleaned,
            "textlines": textlines,
            "polys": polys,
            "boxes": boxes,
            "box_count": len(textlines),
            "inference_time_ms": inference_time_ms,
        }


def compute_mask_metrics(mask_hf: np.ndarray, mask_repo: np.ndarray) -> Dict[str, float]:
    """Compute quantitative overlap and comparative metrics between two binary masks."""
    bin_hf = (mask_hf > 127).astype(bool)
    bin_repo = (mask_repo > 127).astype(bool)

    intersection = np.logical_and(bin_hf, bin_repo).sum()
    union = np.logical_or(bin_hf, bin_repo).sum()
    hf_area = bin_hf.sum()
    repo_area = bin_repo.sum()
    total_pixels = bin_hf.size

    iou = float(intersection / union) if union > 0 else 1.0
    dice = float((2.0 * intersection) / (hf_area + repo_area)) if (hf_area + repo_area) > 0 else 1.0
    precision_hf_vs_repo = float(intersection / hf_area) if hf_area > 0 else 1.0
    recall_hf_vs_repo = float(intersection / repo_area) if repo_area > 0 else 1.0
    pixel_agreement = float((bin_hf == bin_repo).sum() / total_pixels)

    return {
        "iou": round(iou, 4),
        "dice": round(dice, 4),
        "precision_hf_vs_repo": round(precision_hf_vs_repo, 4),
        "recall_hf_vs_repo": round(recall_hf_vs_repo, 4),
        "pixel_agreement": round(pixel_agreement, 4),
        "hf_coverage_pct": round(float(hf_area / total_pixels) * 100.0, 2),
        "repo_coverage_pct": round(float(repo_area / total_pixels) * 100.0, 2),
        "intersection_pixels": int(intersection),
        "union_pixels": int(union),
    }


def create_overlay(image_rgb: np.ndarray, mask: np.ndarray, color: Tuple[int, int, int] = (255, 0, 0), alpha: float = 0.45) -> np.ndarray:
    """Create a semi-transparent colored mask overlay on top of RGB image."""
    overlay = image_rgb.copy()
    colored_layer = np.zeros_like(image_rgb)
    colored_layer[:] = color

    mask_bool = mask > 127
    if np.any(mask_bool):
        overlay[mask_bool] = cv2.addWeighted(image_rgb[mask_bool], 1.0 - alpha, colored_layer[mask_bool], alpha, 0)
    return overlay


def create_boxes_image(image_rgb: np.ndarray, boxes: List[List[int]], color: Tuple[int, int, int] = (0, 255, 0), thickness: int = 2) -> np.ndarray:
    """Draw bounding boxes on image."""
    canvas = image_rgb.copy()
    for box in boxes:
        xmin, ymin, xmax, ymax = box
        cv2.rectangle(canvas, (int(xmin), int(ymin)), (int(xmax), int(ymax)), color, thickness)
    return canvas


def create_difference_map(mask_hf: np.ndarray, mask_repo: np.ndarray, image_rgb: Optional[np.ndarray] = None) -> np.ndarray:
    """Create a color-coded difference map.

    - Green: Agreement (both detected text)
    - Blue: Detected ONLY by Hugging Face model
    - Red: Detected ONLY by detect-20241225.ckpt
    - White / Light Gray: Background
    """
    bin_hf = mask_hf > 127
    bin_repo = mask_repo > 127

    both = np.logical_and(bin_hf, bin_repo)
    hf_only = np.logical_and(bin_hf, ~bin_repo)
    repo_only = np.logical_and(~bin_hf, bin_repo)

    if image_rgb is not None:
        # Subtle desaturated base
        gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        base = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB) // 2 + 100
    else:
        base = np.full((mask_hf.shape[0], mask_hf.shape[1], 3), 245, dtype=np.uint8)

    diff_map = base.copy()
    diff_map[both] = [46, 204, 113]      # Emerald Green
    diff_map[hf_only] = [52, 152, 219]    # Sky Blue (HF only)
    diff_map[repo_only] = [231, 76, 60]   # Red (Repo only)

    return diff_map


def build_comparison_grid(
    image_rgb: np.ndarray,
    hf_mask: np.ndarray,
    hf_overlay: np.ndarray,
    repo_mask: np.ndarray,
    repo_overlay: np.ndarray,
    diff_map: np.ndarray,
    metrics: Dict[str, Any],
    image_name: str,
) -> np.ndarray:
    """Construct a side-by-side composite comparison image."""
    h, w = image_rgb.shape[:2]

    # Standardize size for display
    target_w = 480
    scale = target_w / float(w)
    target_h = int(h * scale)
    dim = (target_w, target_h)

    def _resize(img: np.ndarray) -> np.ndarray:
        if len(img.shape) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        return cv2.resize(img, dim, interpolation=cv2.INTER_AREA)

    def _add_label(img: np.ndarray, label: str, subtext: str = "") -> np.ndarray:
        canvas = img.copy()
        bar_h = 36 if subtext else 26
        cv2.rectangle(canvas, (0, 0), (target_w, bar_h), (20, 20, 20), -1)
        cv2.putText(canvas, label, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        if subtext:
            cv2.putText(canvas, subtext, (8, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1, cv2.LINE_AA)
        return canvas

    img_raw = _add_label(_resize(image_rgb), "Original Image", f"{w}x{h}")
    img_diff = _add_label(_resize(diff_map), "Difference Map", "Green: Both | Blue: HF only | Red: Repo only")

    hf_time = metrics.get("hf_time_ms", 0.0)
    repo_time = metrics.get("repo_time_ms", 0.0)
    hf_boxes = metrics.get("hf_box_count", 0)
    repo_boxes = metrics.get("repo_box_count", 0)

    img_repo_over = _add_label(_resize(repo_overlay), "detect-20241225.ckpt (DBNet)", f"{repo_time:.1f}ms | {repo_boxes} boxes")
    img_hf_over = _add_label(_resize(hf_overlay), "Manga-Text-Seg-2025 (Unet++)", f"{hf_time:.1f}ms | {hf_boxes} boxes")

    img_repo_mask = _add_label(_resize(repo_mask), "detect-20241225 Mask", f"Cov: {metrics.get('repo_coverage_pct', 0)}%")
    img_hf_mask = _add_label(_resize(hf_mask), "Manga-Text-Seg-2025 Mask", f"Cov: {metrics.get('hf_coverage_pct', 0)}%")

    # Assemble 3x2 grid
    row1 = np.hstack([img_raw, img_diff])
    row2 = np.hstack([img_repo_over, img_hf_over])
    row3 = np.hstack([img_repo_mask, img_hf_mask])
    grid = np.vstack([row1, row2, row3])

    # Header banner
    grid_w = grid.shape[1]
    header = np.zeros((56, grid_w, 3), dtype=np.uint8)
    header[:] = (30, 30, 30)

    title = f"Model Comparison: {image_name}"
    iou_str = f"Mask IoU: {metrics.get('iou', 0.0):.3f} | Dice: {metrics.get('dice', 0.0):.3f} | Agreement: {metrics.get('pixel_agreement', 0.0)*100:.1f}%"
    cv2.putText(header, title, (14, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(header, iou_str, (14, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (52, 152, 219), 1, cv2.LINE_AA)

    return np.vstack([header, grid])


def generate_html_report(all_results: List[Dict[str, Any]], output_dir: Path) -> Path:
    """Generate an interactive HTML comparison report."""
    rows_html = []
    for res in all_results:
        metrics = res.get("metrics", {})
        stem = res["stem"]
        rows_html.append(f"""
        <tr>
            <td style="font-weight: 600;">{res["image_name"]}</td>
            <td>{metrics.get("iou", "N/A")}</td>
            <td>{metrics.get("dice", "N/A")}</td>
            <td>{metrics.get("pixel_agreement", "N/A")}</td>
            <td>{res.get("repo_boxes", "N/A")}</td>
            <td>{res.get("hf_boxes", "N/A")}</td>
            <td>{res.get("repo_time_ms", 0.0):.1f} ms</td>
            <td>{res.get("hf_time_ms", 0.0):.1f} ms</td>
            <td>
                <a href="{stem}-comparison.png" target="_blank">Grid</a> |
                <a href="{stem}-diff-map.png" target="_blank">Diff</a> |
                <a href="{stem}-hf-overlay.png" target="_blank">HF</a> |
                <a href="{stem}-repo-overlay.png" target="_blank">Repo</a>
            </td>
        </tr>
        """)

    cards_html = []
    for res in all_results:
        stem = res["stem"]
        cards_html.append(f"""
        <div class="card">
            <h3>{res["image_name"]}</h3>
            <div class="metrics-pill">
                <span>IoU: <strong>{res.get('metrics', {}).get('iou', 'N/A')}</strong></span>
                <span>Dice: <strong>{res.get('metrics', {}).get('dice', 'N/A')}</strong></span>
                <span>HF Time: <strong>{res.get('hf_time_ms', 0):.1f}ms</strong></span>
                <span>Repo Time: <strong>{res.get('repo_time_ms', 0):.1f}ms</strong></span>
            </div>
            <a href="{stem}-comparison.png" target="_blank">
                <img src="{stem}-comparison.png" alt="Comparison Grid" style="width: 100%; border-radius: 6px; border: 1px solid #ddd; margin-top: 10px;" />
            </a>
        </div>
        """)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Manga Text Segmentation Model Comparison Report</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #f8f9fa;
            color: #212529;
            margin: 0;
            padding: 24px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ margin-top: 0; font-size: 26px; }}
        .summary-box {{
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 12px;
        }}
        th, td {{
            padding: 10px 14px;
            text-align: left;
            border-bottom: 1px solid #e2e8f0;
            font-size: 14px;
        }}
        th {{ background: #f1f5f9; font-weight: 600; }}
        .grid-container {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 24px;
        }}
        .card {{
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 16px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}
        .card h3 {{ margin: 0 0 8px 0; font-size: 17px; }}
        .metrics-pill {{
            display: flex;
            gap: 12px;
            font-size: 13px;
            color: #475569;
            margin-bottom: 8px;
            flex-wrap: wrap;
        }}
        .legend {{
            display: flex;
            gap: 16px;
            margin: 12px 0;
            font-size: 14px;
        }}
        .legend-item {{ display: flex; items-center; gap: 6px; }}
        .color-dot {{ width: 14px; height: 14px; border-radius: 3px; display: inline-block; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Manga Text Segmentation Model Comparison Report</h1>
        <div class="summary-box">
            <p><strong>Compared Models:</strong></p>
            <ul>
                <li><strong>Manga-Text-Segmentation-2025:</strong> Hugging Face (<code>a-b-c-x-y-z/Manga-Text-Segmentation-2025</code>) - Unet++ with EfficientNetv2 backbone.</li>
                <li><strong>detect-20241225.ckpt:</strong> Repository Default Baseline - DBNet with ResNet-34 backbone.</li>
            </ul>
            <div class="legend">
                <div class="legend-item"><span class="color-dot" style="background: #2ecc71;"></span> Green: Agreement (Both models)</div>
                <div class="legend-item"><span class="color-dot" style="background: #3498db;"></span> Blue: Manga-Text-Seg-2025 only</div>
                <div class="legend-item"><span class="color-dot" style="background: #e74c3c;"></span> Red: detect-20241225 only</div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Page</th>
                        <th>IoU</th>
                        <th>Dice</th>
                        <th>Pixel Agree</th>
                        <th>Repo Boxes</th>
                        <th>HF Boxes</th>
                        <th>Repo Latency</th>
                        <th>HF Latency</th>
                        <th>Files</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows_html)}
                </tbody>
            </table>
        </div>

        <h2>Detailed Visual Comparisons</h2>
        <div class="grid-container">
            {''.join(cards_html)}
        </div>
    </div>
</body>
</html>
"""
    report_file = output_dir / "index.html"
    report_file.write_text(html_content, encoding="utf-8")
    return report_file


def expand_input_paths(inputs: List[Path], output_dir: Optional[Path] = None) -> List[Path]:
    """Expand directory and image inputs into sorted unique image file paths."""
    resolved_out = output_dir.resolve() if output_dir else None
    paths: List[Path] = []
    for value in inputs:
        path = value.expanduser()
        if not path.is_dir():
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                paths.append(path)
            continue
        for candidate in sorted(path.iterdir(), key=lambda item: item.name.lower()):
            if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if resolved_out == candidate.parent.resolve() and any(candidate.name.endswith(sfx) for sfx in GENERATED_SUFFIXES):
                continue
            paths.append(candidate)
    if not paths:
        raise ValueError("No image files found in the supplied input path(s)")
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dev script to test and compare Manga-Text-Segmentation-2025 vs detect-20241225.ckpt",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        nargs="*",
        default=[PROJECT_ROOT / "test" / "testdata" / "render" / "default1.png"],
        help="Input image file(s) or directory",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=PROJECT_ROOT / "result" / "text_seg_comparison",
        help="Directory to save comparison images and metrics",
    )
    parser.add_argument(
        "--mode",
        choices=["both", "hf", "repo"],
        default="both",
        help="Evaluation mode: 'both' (compare), 'hf' (only HF model), or 'repo' (only detect-20241225)",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "mps", "cpu"],
        default="auto",
        help="Compute device for neural inference",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="HF model probability threshold for binarization",
    )
    parser.add_argument(
        "--tta",
        action="store_true",
        help="Enable test-time augmentation (horizontal/vertical flip) for HF model",
    )
    parser.add_argument(
        "--gap-closing",
        type=int,
        default=0,
        help="Morphological closing kernel size for HF mask",
    )
    parser.add_argument(
        "--fill-holes",
        action="store_true",
        help="Fill enclosed contour holes in HF mask",
    )
    parser.add_argument(
        "--padding",
        type=int,
        default=2,
        help="Dilation padding iterations for whiteout cleaned image",
    )
    parser.add_argument(
        "--detect-size",
        type=int,
        default=1024,
        help="Detection size resolution for detect-20241225 DBNet",
    )
    parser.add_argument(
        "--hf-model-path",
        type=Path,
        default=None,
        help="Custom local path to HF model.pth checkpoint",
    )
    parser.add_argument(
        "--repo-model-path",
        type=Path,
        default=None,
        help="Custom local path to detect-20241225.ckpt checkpoint",
    )
    parser.add_argument(
        "--no-html",
        action="store_true",
        help="Disable generating HTML report",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON metrics to stdout",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    device = select_device(args.device)
    print(f"[*] Target device: {device}")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    input_paths = expand_input_paths(args.input, output_dir)
    print(f"[*] Found {len(input_paths)} image(s) to process")

    hf_model = None
    repo_model = None

    if args.mode in ("both", "hf"):
        print(f"[*] Loading Manga-Text-Segmentation-2025 (Unet++ EfficientNetv2)...")
        t0 = time.perf_counter()
        hf_model = MangaTextSegmentation2025(weight_path=args.hf_model_path, device=device)
        print(f"[+] Loaded HF model in {(time.perf_counter() - t0):.2f}s")

    if args.mode in ("both", "repo"):
        print(f"[*] Loading detect-20241225.ckpt (DBNet ResNet-34)...")
        t0 = time.perf_counter()
        repo_model = RepoBaselineDetector(weight_path=args.repo_model_path, device=device)
        print(f"[+] Loaded Repo detector in {(time.perf_counter() - t0):.2f}s")

    all_results = []

    print("\n" + "=" * 80)
    print(f"{'Image':<24} | {'IoU':<6} | {'Dice':<6} | {'HF Cov%':<8} | {'Repo Cov%':<9} | {'HF ms':<8} | {'Repo ms':<8}")
    print("=" * 80)

    for img_path in input_paths:
        bgr = cv2.imread(str(img_path))
        if bgr is None:
            print(f"[!] Warning: Failed to read image {img_path}, skipping.")
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        stem = img_path.stem

        res_entry: Dict[str, Any] = {
            "image_name": img_path.name,
            "stem": stem,
            "path": str(img_path),
            "width": rgb.shape[1],
            "height": rgb.shape[0],
        }

        hf_pred = None
        repo_pred = None

        if hf_model is not None:
            hf_pred = hf_model.predict(
                rgb,
                threshold=args.threshold,
                tta=args.tta,
                gap_closing=args.gap_closing,
                fill_holes=args.fill_holes,
                padding_iter=args.padding,
            )
            res_entry["hf_time_ms"] = hf_pred["inference_time_ms"]
            res_entry["hf_boxes"] = hf_pred["box_count"]

            # Save individual HF outputs
            cv2.imwrite(str(output_dir / f"{stem}-hf-mask.png"), hf_pred["binary_mask"])
            hf_overlay = create_overlay(rgb, hf_pred["binary_mask"], color=(0, 100, 255))
            cv2.imwrite(str(output_dir / f"{stem}-hf-overlay.png"), cv2.cvtColor(hf_overlay, cv2.COLOR_RGB2BGR))
            cv2.imwrite(str(output_dir / f"{stem}-hf-cleaned.png"), cv2.cvtColor(hf_pred["cleaned"], cv2.COLOR_RGB2BGR))

        if repo_model is not None:
            repo_pred = repo_model.predict(
                rgb,
                detect_size=args.detect_size,
            )
            res_entry["repo_time_ms"] = repo_pred["inference_time_ms"]
            res_entry["repo_boxes"] = repo_pred["box_count"]

            # Save individual Repo outputs
            cv2.imwrite(str(output_dir / f"{stem}-repo-mask.png"), repo_pred["binary_mask"])
            repo_overlay = create_overlay(rgb, repo_pred["binary_mask"], color=(255, 50, 50))
            cv2.imwrite(str(output_dir / f"{stem}-repo-overlay.png"), cv2.cvtColor(repo_overlay, cv2.COLOR_RGB2BGR))

        if hf_pred is not None and repo_pred is not None:
            metrics = compute_mask_metrics(hf_pred["binary_mask"], repo_pred["binary_mask"])
            metrics["hf_time_ms"] = hf_pred["inference_time_ms"]
            metrics["repo_time_ms"] = repo_pred["inference_time_ms"]
            metrics["hf_box_count"] = hf_pred["box_count"]
            metrics["repo_box_count"] = repo_pred["box_count"]
            res_entry["metrics"] = metrics

            diff_map = create_difference_map(hf_pred["binary_mask"], repo_pred["binary_mask"], rgb)
            cv2.imwrite(str(output_dir / f"{stem}-diff-map.png"), cv2.cvtColor(diff_map, cv2.COLOR_RGB2BGR))

            grid = build_comparison_grid(
                image_rgb=rgb,
                hf_mask=hf_pred["binary_mask"],
                hf_overlay=hf_overlay,
                repo_mask=repo_pred["binary_mask"],
                repo_overlay=repo_overlay,
                diff_map=diff_map,
                metrics=metrics,
                image_name=img_path.name,
            )
            cv2.imwrite(str(output_dir / f"{stem}-comparison.png"), cv2.cvtColor(grid, cv2.COLOR_RGB2BGR))

            print(
                f"{img_path.name[:24]:<24} | {metrics['iou']:<6.3f} | {metrics['dice']:<6.3f} | "
                f"{metrics['hf_coverage_pct']:<8.2f} | {metrics['repo_coverage_pct']:<9.2f} | "
                f"{hf_pred['inference_time_ms']:<8.1f} | {repo_pred['inference_time_ms']:<8.1f}"
            )
        elif hf_pred is not None:
            print(f"{img_path.name[:24]:<24} | HF Mode | Boxes: {hf_pred['box_count']} | Latency: {hf_pred['inference_time_ms']:.1f}ms")
        elif repo_pred is not None:
            print(f"{img_path.name[:24]:<24} | Repo Mode | Boxes: {repo_pred['box_count']} | Latency: {repo_pred['inference_time_ms']:.1f}ms")

        all_results.append(res_entry)

    print("=" * 80)

    # Save metrics.json
    metrics_path = output_dir / "metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n[+] Saved metrics JSON to: {metrics_path}")

    # Generate HTML report
    if not args.no_html and args.mode == "both":
        html_path = generate_html_report(all_results, output_dir)
        print(f"[+] Saved HTML comparison report to: {html_path}")

    if args.json:
        print(json.dumps(all_results, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
