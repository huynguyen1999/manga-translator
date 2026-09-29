#!/usr/bin/env python3
"""Devscript to run PP-OCRv6_manga v0.2 text detection with overlay and corner confidence badges.

Supports:
- Automatic model downloading from Hugging Face (Kellenok/PP-OCRv6_manga).
- Single/multiple image files, directories, and glob wildcard patterns (e.g. `*.png`, `data/**/*.jpg`).
- Full Batch Inference (`--batch-size N`): processes multiple pages simultaneously through the accelerator.
- Hardware acceleration: Apple Silicon (MPS / CoreML), NVIDIA CUDA, DirectML, and CPU.
- Clean visual overlay with polygon fill, anti-aliased borders, and corner confidence badges.
- Optional structured JSON annotations export, binary masks, and cropped text boxes.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np
import onnxruntime as ort
import pyclipper
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_DIR = PROJECT_ROOT / "models" / "detection" / "ppocrv6"
DEFAULT_SEG_MODEL_DIR = PROJECT_ROOT / "models" / "segmentation"
DEFAULT_SEG_MODEL_PATH = DEFAULT_SEG_MODEL_DIR / "textseg.onnx"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}

HF_REPO_ID = "Kellenok/PP-OCRv6_manga"
HF_BASE_URL = f"https://huggingface.co/{HF_REPO_ID}/resolve/main"

SEG_HF_REPO_ID = "vermilion10/manga-textseg"
SEG_HF_FILENAME = "textseg.onnx"

MODEL_FILENAMES = {
    ("v0.2", "fp32"): "det/manga_det_v0.2.onnx",
    ("v0.2", "fp16"): "det/manga_det_v0.2_fp16.onnx",
    ("v0.1", "fp32"): "det/manga_det_v0.1.onnx",
    ("v0.1", "fp16"): "det/manga_det_v0.1_fp16.onnx",
}

COLOR_SCHEMES: dict[str, dict[str, Any]] = {
    "cyan": {
        "border": (255, 170, 0),       # BGR Bright Azure/Cyan
        "fill": (255, 170, 0),
        "badge_bg": (24, 24, 30),
        "badge_border": (255, 170, 0),
        "text": (255, 255, 255),
    },
    "green": {
        "border": (46, 204, 113),      # Emerald Green
        "fill": (46, 204, 113),
        "badge_bg": (20, 28, 20),
        "badge_border": (46, 204, 113),
        "text": (255, 255, 255),
    },
    "magenta": {
        "border": (230, 80, 190),      # Magenta / Violet
        "fill": (230, 80, 190),
        "badge_bg": (28, 20, 26),
        "badge_border": (230, 80, 190),
        "text": (255, 255, 255),
    },
    "orange": {
        "border": (30, 130, 255),      # Orange
        "fill": (30, 130, 255),
        "badge_bg": (20, 20, 25),
        "badge_border": (30, 130, 255),
        "text": (255, 255, 255),
    },
}


def get_confidence_color(score: float) -> dict[str, Any]:
    """Color code based on confidence score."""
    if score >= 0.85:
        # High confidence - Emerald Green
        color = (46, 204, 113)
        bg = (18, 30, 18)
    elif score >= 0.60:
        # Moderate confidence - Amber Yellow
        color = (0, 215, 255)
        bg = (24, 26, 16)
    else:
        # Low confidence - Coral Red
        color = (60, 60, 245)
        bg = (30, 18, 18)
    return {
        "border": color,
        "fill": color,
        "badge_bg": bg,
        "badge_border": color,
        "text": (255, 255, 255),
    }


def ensure_model_file(model_path: Path | None, version: str = "v0.2", precision: str = "fp32") -> Path:
    """Ensure the ONNX detection model is downloaded and return its local path."""
    if model_path is not None and model_path.is_file():
        return model_path

    key = (version, precision.lower())
    if key not in MODEL_FILENAMES:
        key = ("v0.2", "fp32")

    relative_filename = MODEL_FILENAMES[key]
    target_path = DEFAULT_MODEL_DIR / Path(relative_filename).name

    if target_path.is_file():
        return target_path

    target_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[*] PP-OCRv6_manga detector not found locally. Downloading {relative_filename}...")

    # Try huggingface_hub first if available
    try:
        from huggingface_hub import hf_hub_download
        downloaded = hf_hub_download(
            repo_id=HF_REPO_ID,
            filename=relative_filename,
            local_dir=target_path.parent,
        )
        downloaded_path = Path(downloaded)
        if downloaded_path != target_path and downloaded_path.is_file():
            downloaded_path.rename(target_path)
        print(f"[+] Downloaded model to {target_path}")
        return target_path
    except Exception as exc:
        print(f"[*] huggingface_hub download not used ({exc}). Downloading via direct URL...", file=sys.stderr)

    url = f"{HF_BASE_URL}/{relative_filename}"
    print(f"[*] Fetching from {url} -> {target_path}")
    urllib.request.urlretrieve(url, target_path)
    print(f"[+] Successfully downloaded model ({target_path.stat().st_size / (1024 * 1024):.2f} MB)")
    return target_path


def ensure_segmentation_model_file(model_path: Path | None = None) -> Path:
    """Ensure the ONNX stroke segmentation model is downloaded and return its local path."""
    if model_path is not None and model_path.is_file():
        return model_path

    target_path = DEFAULT_SEG_MODEL_PATH
    if target_path.is_file():
        return target_path

    target_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[*] Manga-Text-Segmentation model not found locally. Downloading {SEG_HF_FILENAME}...")

    try:
        from huggingface_hub import hf_hub_download
        downloaded = hf_hub_download(
            repo_id=SEG_HF_REPO_ID,
            filename=SEG_HF_FILENAME,
            local_dir=target_path.parent,
        )
        downloaded_path = Path(downloaded)
        if downloaded_path != target_path and downloaded_path.is_file():
            downloaded_path.rename(target_path)
        print(f"[+] Downloaded segmentation model to {target_path}")
        return target_path
    except Exception as exc:
        print(f"[*] huggingface_hub download not used ({exc}). Downloading via direct URL...", file=sys.stderr)

    url = f"https://huggingface.co/{SEG_HF_REPO_ID}/resolve/main/{SEG_HF_FILENAME}"
    print(f"[*] Fetching from {url} -> {target_path}")
    urllib.request.urlretrieve(url, target_path)
    print(f"[+] Successfully downloaded segmentation model ({target_path.stat().st_size / (1024 * 1024):.2f} MB)")
    return target_path


def select_onnx_providers(device: str = "auto") -> tuple[list[str], str]:
    """Select ONNX Runtime execution providers based on preference and system availability."""
    available = ort.get_available_providers()
    device = device.lower().strip()

    if device in ("mps", "coreml", "apple", "mac"):
        if "CoreMLExecutionProvider" in available:
            return ["CoreMLExecutionProvider", "CPUExecutionProvider"], "Apple Silicon (CoreML / Metal)"
        print("[!] CoreMLExecutionProvider not available in ONNX Runtime; falling back to CPU.", file=sys.stderr)
        return ["CPUExecutionProvider"], "CPU"

    if device == "cuda":
        if "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"], "NVIDIA CUDA"
        print("[!] CUDAExecutionProvider not available in ONNX Runtime; falling back to CPU.", file=sys.stderr)
        return ["CPUExecutionProvider"], "CPU"

    if device == "directml":
        if "DmlExecutionProvider" in available:
            return ["DmlExecutionProvider", "CPUExecutionProvider"], "DirectML"
        return ["CPUExecutionProvider"], "CPU"

    if device == "cpu":
        return ["CPUExecutionProvider"], "CPU"

    # Auto mode
    if "CUDAExecutionProvider" in available:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"], "NVIDIA CUDA (Auto)"
    if "CoreMLExecutionProvider" in available:
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"], "Apple Silicon (CoreML / Metal Auto)"
    return ["CPUExecutionProvider"], "CPU (Auto)"


class PPOCRv6Detector:
    """PP-OCRv6_manga text detection engine with batched acceleration."""

    def __init__(self, model_path: Path, device: str = "auto"):
        self.model_path = model_path
        self.providers, self.device_desc = select_onnx_providers(device)
        self.session = ort.InferenceSession(str(model_path), providers=self.providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        print(f"[*] Loaded PP-OCRv6_manga ({model_path.name}) on {self.device_desc} (providers: {self.session.get_providers()})")

    @staticmethod
    def _box_score_fast(pred: np.ndarray, box: np.ndarray) -> float:
        """Calculate the average prediction confidence score inside a contour polygon."""
        h, w = pred.shape[:2]
        b = box.copy().astype(np.int32)
        xmin = int(np.clip(np.floor(b[:, 0].min()), 0, w - 1))
        xmax = int(np.clip(np.ceil(b[:, 0].max()), 0, w - 1))
        ymin = int(np.clip(np.floor(b[:, 1].min()), 0, h - 1))
        ymax = int(np.clip(np.ceil(b[:, 1].max()), 0, h - 1))
        if xmax <= xmin or ymax <= ymin:
            return 0.0

        score_mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        b[:, 0] -= xmin
        b[:, 1] -= ymin
        cv2.fillPoly(score_mask, [b.reshape(-1, 2)], 1)
        mean_score = cv2.mean(pred[ymin : ymax + 1, xmin : xmax + 1], score_mask)[0]
        return float(mean_score)

    @staticmethod
    def _unclip_polygon(box: np.ndarray, unclip_ratio: float) -> np.ndarray | None:
        """Expand DBNet polygon using pyclipper offset."""
        x, y = box[:, 0], box[:, 1]
        area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        perimeter = float(np.linalg.norm(np.roll(box, -1, axis=0) - box, axis=1).sum())
        if area <= 0 or perimeter <= 0:
            return None
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(box.tolist(), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        paths = offset.Execute(area * unclip_ratio / perimeter)
        if len(paths) == 0:
            return None
        return np.asarray(paths[0], dtype=np.float32)

    def _postprocess_pred_map(
        self,
        pred_map: np.ndarray,
        orig_w: int,
        orig_h: int,
        rw: int,
        rh: int,
        thresh: float,
        box_thresh: float,
        unclip_ratio: float,
    ) -> list[dict[str, Any]]:
        """Extract bounding polygons and confidence scores from a single probability map."""
        binary_mask = (pred_map > thresh).astype(np.uint8) * 255
        contours, _ = cv2.findContours(binary_mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[dict[str, Any]] = []

        for cnt in contours:
            pts = cnt.squeeze(1)
            if pts.ndim != 2 or pts.shape[0] < 4:
                continue

            score = self._box_score_fast(pred_map, pts)
            if score < box_thresh:
                continue

            if unclip_ratio > 1.0:
                unclipped = self._unclip_polygon(pts, unclip_ratio)
                if unclipped is None or len(unclipped) == 0:
                    continue
            else:
                # Tight polygon approximation using Douglas-Peucker simplification
                eps = 0.003 * cv2.arcLength(cnt, True)
                approx = cv2.approxPolyDP(cnt, eps, True).squeeze(1)
                unclipped = approx if (approx.ndim == 2 and approx.shape[0] >= 3) else pts

            rect = cv2.minAreaRect(unclipped.astype(np.float32))
            center, (bw, bh), angle = rect
            if min(bw, bh) < 3:
                continue

            # Scaled 4-point rotated box
            box_points = cv2.boxPoints(rect).astype(np.float32)
            box_points[:, 0] = np.clip(np.round(box_points[:, 0] / rw * orig_w), 0, orig_w)
            box_points[:, 1] = np.clip(np.round(box_points[:, 1] / rh * orig_h), 0, orig_h)

            # Scaled arbitrary segmentation polygon contour
            scaled_poly = unclipped.copy()
            scaled_poly[:, 0] = np.clip(np.round(scaled_poly[:, 0] / rw * orig_w), 0, orig_w)
            scaled_poly[:, 1] = np.clip(np.round(scaled_poly[:, 1] / rh * orig_h), 0, orig_h)

            detections.append({
                "polygon": scaled_poly.astype(int).tolist(),
                "box": box_points.astype(int).tolist(),
                "score": float(score),
                "center": [float(center[0] / rw * orig_w), float(center[1] / rh * orig_h)],
                "width": float(bw / rw * orig_w),
                "height": float(bh / rh * orig_h),
                "angle": float(angle),
            })

        # Sort detections in top-to-bottom, right-to-left order (manga reading convention)
        detections.sort(key=lambda d: (d["center"][1] // 80, -d["center"][0]))
        return detections

    def detect(
        self,
        image_bgr: np.ndarray,
        detect_size: int = 960,
        thresh: float = 0.15,
        box_thresh: float = 0.25,
        unclip_ratio: float = 1.4,
    ) -> tuple[list[dict[str, Any]], np.ndarray]:
        """Run text detection on a single image."""
        results, masks = self.detect_batch(
            [image_bgr],
            detect_size=detect_size,
            thresh=thresh,
            box_thresh=box_thresh,
            unclip_ratio=unclip_ratio,
        )
        return results[0], masks[0]

    def detect_batch(
        self,
        images_bgr: list[np.ndarray],
        detect_size: int = 960,
        thresh: float = 0.15,
        box_thresh: float = 0.25,
        unclip_ratio: float = 1.4,
    ) -> tuple[list[list[dict[str, Any]]], list[np.ndarray]]:
        """Run batched text detection on a list of images simultaneously.

        Returns:
            results: List of detection lists per image.
            masks: List of sliced probability maps (float32) per image.
        """
        if not images_bgr:
            return [], []

        batch_target_sizes: list[tuple[int, int]] = []
        preprocessed_tensors: list[np.ndarray] = []
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

        for img in images_bgr:
            orig_h, orig_w = img.shape[:2]
            if max(orig_h, orig_w) / max(1, min(orig_h, orig_w)) > 2.0:
                ratio = min(1.0, (0.75 * detect_size * detect_size / (orig_h * orig_w)) ** 0.5)
            else:
                ratio = float(detect_size) / max(orig_h, orig_w)

            rh = max(int(round(orig_h * ratio / 32) * 32), 32)
            rw = max(int(round(orig_w * ratio / 32) * 32), 32)
            batch_target_sizes.append((rh, rw))

            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            resized = cv2.resize(img_rgb, (rw, rh), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
            norm = ((resized - mean) / std).transpose((2, 0, 1))
            preprocessed_tensors.append(norm)

        max_rh = max(ts[0] for ts in batch_target_sizes)
        max_rw = max(ts[1] for ts in batch_target_sizes)

        # Assemble unified batched tensor
        batch_size = len(images_bgr)
        batch_tensor = np.zeros((batch_size, 3, max_rh, max_rw), dtype=np.float32)
        for i, tensor in enumerate(preprocessed_tensors):
            rh, rw = batch_target_sizes[i]
            batch_tensor[i, :, :rh, :rw] = tensor

        # Batched model inference
        outputs = self.session.run([self.output_name], {self.input_name: batch_tensor})
        raw_pred_maps = outputs[0]  # shape: (B, 1, max_rh, max_rw)

        batch_detections: list[list[dict[str, Any]]] = []
        batch_masks: list[np.ndarray] = []

        for i, img in enumerate(images_bgr):
            rh, rw = batch_target_sizes[i]
            orig_h, orig_w = img.shape[:2]
            pred_map = raw_pred_maps[i, 0, :rh, :rw]
            batch_masks.append(pred_map)

            detections = self._postprocess_pred_map(
                pred_map,
                orig_w=orig_w,
                orig_h=orig_h,
                rw=rw,
                rh=rh,
                thresh=thresh,
                box_thresh=box_thresh,
                unclip_ratio=unclip_ratio,
            )
            batch_detections.append(detections)

        return batch_detections, batch_masks


def select_device_str(device: str = "auto") -> str:
    """Resolve PyTorch device string."""
    dev = device.lower().strip()
    if dev in ("mps", "coreml", "apple", "mac"):
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    if dev == "cuda":
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    if dev == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class DefaultCoreDetector:
    """Core translation app default DBNet ResNet-34 detector (detect-20241225.ckpt)."""

    def __init__(self, device: str = "auto", weights_path: Path | None = None):
        import asyncio
        from manga_translator.detection.default import DefaultDetector
        self.device = select_device_str(device)
        self.device_desc = f"Core DBNet ResNet-34 ({self.device})"
        self.detector = DefaultDetector()
        print(f"[*] Loaded Core DefaultDetector (detect-20241225.ckpt) on {self.device_desc}")

    def detect(
        self,
        image_bgr: np.ndarray,
        detect_size: int = 1024,
        thresh: float = 0.20,
        box_thresh: float = 0.25,
        unclip_ratio: float = 1.0,
    ) -> tuple[list[dict[str, Any]], np.ndarray]:
        import asyncio
        async def _run():
            await self.detector.load(self.device)
            lines, raw_mask, _ = await self.detector.detect(
                image_bgr,
                detect_size=detect_size,
                text_threshold=thresh,
                box_threshold=box_thresh,
                unclip_ratio=unclip_ratio,
                invert=False,
                gamma_correct=False,
                rotate=False,
                auto_rotate=False,
                verbose=False,
            )
            return lines, raw_mask

        lines, raw_mask = asyncio.run(_run())
        orig_h, orig_w = image_bgr.shape[:2]

        if raw_mask is not None and (raw_mask.shape[0] != orig_h or raw_mask.shape[1] != orig_w):
            prob = cv2.resize(raw_mask.astype(np.float32), (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)
            if prob.max() > 1.0:
                prob /= 255.0
        else:
            prob = (raw_mask / 255.0) if raw_mask is not None and raw_mask.max() > 1.0 else raw_mask

        dets: list[dict[str, Any]] = []
        for line in lines:
            pts = np.array(line.pts, dtype=np.float32)
            score = getattr(line, "confidence", None)
            if score is None or score <= 0:
                if prob is not None:
                    mask_temp = np.zeros((orig_h, orig_w), dtype=np.uint8)
                    cv2.fillPoly(mask_temp, [pts.astype(np.int32)], 1)
                    mean_s = cv2.mean(prob, mask=mask_temp)[0]
                    score = float(mean_s) if mean_s > 0 else 0.85
                else:
                    score = 0.85

            rect = cv2.minAreaRect(pts.astype(np.int32))
            (cx, cy), (bw, bh), angle = rect
            box_pts = cv2.boxPoints(rect).astype(int).tolist()

            dets.append({
                "polygon": pts.astype(int).tolist(),
                "box": box_pts,
                "score": round(float(score), 3),
                "center": [float(cx), float(cy)],
                "width": float(bw),
                "height": float(bh),
                "angle": float(angle),
            })

        dets.sort(key=lambda d: (d["center"][1] // 80, -d["center"][0]))
        return dets, prob if prob is not None else np.zeros((orig_h, orig_w), dtype=np.float32)

    def detect_batch(
        self,
        images_bgr: list[np.ndarray],
        detect_size: int = 1024,
        thresh: float = 0.20,
        box_thresh: float = 0.25,
        unclip_ratio: float = 1.0,
    ) -> tuple[list[list[dict[str, Any]]], list[np.ndarray]]:
        batch_detections: list[list[dict[str, Any]]] = []
        batch_masks: list[np.ndarray] = []
        for img in images_bgr:
            dets, mask = self.detect(
                img,
                detect_size=detect_size,
                thresh=thresh,
                box_thresh=box_thresh,
                unclip_ratio=unclip_ratio,
            )
            batch_detections.append(dets)
            batch_masks.append(mask)
        return batch_detections, batch_masks


class CTDDetector:
    """Comic Text Detector (CTD) text detection engine."""

    def __init__(self, device: str = "auto", weights_path: Path | None = None):
        import asyncio
        from manga_translator.detection.ctd import ComicTextDetector
        self.device = select_device_str(device)
        self.device_desc = f"ComicTextDetector ({self.device})"
        self.detector = ComicTextDetector()
        print(f"[*] Loaded ComicTextDetector (comictextdetector.pt) on {self.device_desc}")

    def detect(
        self,
        image_bgr: np.ndarray,
        detect_size: int = 1024,
        thresh: float = 0.20,
        box_thresh: float = 0.25,
        unclip_ratio: float = 1.0,
    ) -> tuple[list[dict[str, Any]], np.ndarray]:
        import asyncio
        async def _run():
            await self.detector.load(self.device)
            lines, raw_mask, _ = await self.detector.detect(
                image_bgr,
                detect_size=detect_size,
                text_threshold=thresh,
                box_threshold=box_thresh,
                unclip_ratio=unclip_ratio,
                invert=False,
                gamma_correct=False,
                rotate=False,
                auto_rotate=False,
                verbose=False,
            )
            return lines, raw_mask

        lines, raw_mask = asyncio.run(_run())
        orig_h, orig_w = image_bgr.shape[:2]

        if raw_mask is not None and (raw_mask.shape[0] != orig_h or raw_mask.shape[1] != orig_w):
            prob = cv2.resize(raw_mask.astype(np.float32), (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)
            if prob.max() > 1.0:
                prob /= 255.0
        else:
            prob = (raw_mask / 255.0) if raw_mask is not None and raw_mask.max() > 1.0 else raw_mask

        dets: list[dict[str, Any]] = []
        for line in lines:
            pts = np.array(line.pts, dtype=np.float32)
            score = getattr(line, "confidence", None)
            if score is None or score <= 0:
                if prob is not None:
                    mask_temp = np.zeros((orig_h, orig_w), dtype=np.uint8)
                    cv2.fillPoly(mask_temp, [pts.astype(np.int32)], 1)
                    mean_s = cv2.mean(prob, mask=mask_temp)[0]
                    score = float(mean_s) if mean_s > 0 else 0.85
                else:
                    score = 0.85

            rect = cv2.minAreaRect(pts.astype(np.int32))
            (cx, cy), (bw, bh), angle = rect
            box_pts = cv2.boxPoints(rect).astype(int).tolist()

            dets.append({
                "polygon": pts.astype(int).tolist(),
                "box": box_pts,
                "score": round(float(score), 3),
                "center": [float(cx), float(cy)],
                "width": float(bw),
                "height": float(bh),
                "angle": float(angle),
            })

        dets.sort(key=lambda d: (d["center"][1] // 80, -d["center"][0]))
        return dets, prob if prob is not None else np.zeros((orig_h, orig_w), dtype=np.float32)

    def detect_batch(
        self,
        images_bgr: list[np.ndarray],
        detect_size: int = 1024,
        thresh: float = 0.20,
        box_thresh: float = 0.25,
        unclip_ratio: float = 1.0,
    ) -> tuple[list[list[dict[str, Any]]], list[np.ndarray]]:
        batch_detections: list[list[dict[str, Any]]] = []
        batch_masks: list[np.ndarray] = []
        for img in images_bgr:
            dets, mask = self.detect(
                img,
                detect_size=detect_size,
                thresh=thresh,
                box_thresh=box_thresh,
                unclip_ratio=unclip_ratio,
            )
            batch_detections.append(dets)
            batch_masks.append(mask)
        return batch_detections, batch_masks


class ContemporaryCatSegmentationDetector:
    """Manga-Text-Segmentation (ContemporaryCat / juvian) text detection engine."""

    def __init__(self, model_path: Path | None = None, device: str = "auto"):
        self.model_path = ensure_segmentation_model_file(model_path)
        self.providers, self.device_desc = select_onnx_providers(device)
        self.session = ort.InferenceSession(str(self.model_path), providers=self.providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        print(f"[*] Loaded ContemporaryCat Manga-Text-Segmentation ({self.model_path.name}) on {self.device_desc}")

    def detect(
        self,
        image_bgr: np.ndarray,
        detect_size: int = 1536,
        thresh: float = 0.25,
        box_thresh: float = 0.25,
        unclip_ratio: float = 1.0,
    ) -> tuple[list[dict[str, Any]], np.ndarray]:
        orig_h, orig_w = image_bgr.shape[:2]
        scale = min(detect_size / max(orig_h, orig_w), 1.0)
        target_w, target_h = int(round(orig_w * scale)), int(round(orig_h * scale))
        resized = cv2.resize(image_bgr, (target_w, target_h), interpolation=cv2.INTER_LINEAR)

        pad_h = (32 - target_h % 32) % 32
        pad_w = (32 - target_w % 32) % 32
        crop_rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        if pad_h > 0 or pad_w > 0:
            crop_padded = np.pad(crop_rgb, ((0, pad_h), (0, pad_w), (0, 0)), mode="constant")
        else:
            crop_padded = crop_rgb

        tensor = np.transpose(crop_padded, (2, 0, 1))[np.newaxis, :, :, :].astype(np.float32)
        out = self.session.run(None, {self.input_name: tensor})[0]
        raw_prob = out[0, 0]
        upsampled = cv2.resize(raw_prob, (target_w + pad_w, target_h + pad_h), interpolation=cv2.INTER_LINEAR)
        prob_map = upsampled[:target_h, :target_w]

        # Connected component clustering for glyph strokes into text regions
        binary = (prob_map > thresh).astype(np.uint8) * 255
        kernel_size = max(5, int(round(7 * scale)))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
        if unclip_ratio > 1.0:
            closed = cv2.dilate(closed, kernel, iterations=int(round(unclip_ratio)))

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[dict[str, Any]] = []

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < (25 * scale * scale):
                continue

            mask_c = np.zeros((target_h, target_w), dtype=np.uint8)
            cv2.drawContours(mask_c, [cnt], -1, 1, -1)
            score = float(cv2.mean(prob_map, mask=mask_c)[0])
            if score < box_thresh:
                continue

            scaled_cnt = cnt.astype(np.float32)
            scaled_cnt[:, :, 0] *= (orig_w / target_w)
            scaled_cnt[:, :, 1] *= (orig_h / target_h)

            rect = cv2.minAreaRect(scaled_cnt)
            (cx, cy), (bw, bh), angle = rect
            box_points = cv2.boxPoints(rect).astype(int).tolist()

            epsilon = 0.012 * cv2.arcLength(scaled_cnt, True)
            approx = cv2.approxPolyDP(scaled_cnt, epsilon, True).squeeze(1)
            if approx.ndim != 2 or len(approx) < 3:
                continue

            detections.append({
                "polygon": approx.astype(int).tolist(),
                "box": box_points,
                "score": round(float(score), 3),
                "center": [float(cx), float(cy)],
                "width": float(bw),
                "height": float(bh),
                "angle": float(angle),
            })

        detections.sort(key=lambda d: (d["center"][1] // 80, -d["center"][0]))
        full_prob = cv2.resize(prob_map, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)
        return detections, full_prob

    def detect_batch(
        self,
        images_bgr: list[np.ndarray],
        detect_size: int = 1536,
        thresh: float = 0.25,
        box_thresh: float = 0.25,
        unclip_ratio: float = 1.0,
    ) -> tuple[list[list[dict[str, Any]]], list[np.ndarray]]:
        batch_detections: list[list[dict[str, Any]]] = []
        batch_masks: list[np.ndarray] = []
        for img in images_bgr:
            dets, mask = self.detect(
                img,
                detect_size=detect_size,
                thresh=thresh,
                box_thresh=box_thresh,
                unclip_ratio=unclip_ratio,
            )
            batch_detections.append(dets)
            batch_masks.append(mask)
        return batch_detections, batch_masks


def create_detector_engine(
    model_name: str = "ppocrv6",
    device: str = "auto",
    model_version: str = "v0.2",
    precision: str = "fp32",
    custom_weights: Path | None = None,
):
    """Instantiate the requested text detection engine."""
    model_key = model_name.lower().strip().replace("_", "-")
    if model_key in ("ppocrv6", "ppocr", "pp-ocr", "pp-ocrv6"):
        model_file = ensure_model_file(custom_weights, version=model_version, precision=precision)
        return PPOCRv6Detector(model_file, device=device)
    elif model_key in ("default", "dbnet", "core", "manga-text-detector"):
        return DefaultCoreDetector(device=device, weights_path=custom_weights)
    elif model_key in ("ctd", "comictextdetector", "comic-text-detector"):
        return CTDDetector(device=device, weights_path=custom_weights)
    elif model_key in ("contemporarycat", "manga-text-seg", "manga-text-segmentation", "textseg", "juvian"):
        model_file = ensure_segmentation_model_file(custom_weights)
        return ContemporaryCatSegmentationDetector(model_file, device=device)
    else:
        raise ValueError(f"Unknown detector model '{model_name}'. Choose from: 'ppocrv6', 'default', 'ctd', 'contemporarycat'")


def render_overlay(
    image_bgr: np.ndarray,
    detections: list[dict[str, Any]],
    draw_mode: str = "polygon",
    color_scheme_name: str = "cyan",
    color_by_confidence: bool = False,
    fill_alpha: float = 0.22,
    line_thickness: int = 2,
    show_confidence: bool = True,
    score_format: str = "decimal",
    show_indices: bool = False,
) -> np.ndarray:
    """Draw segmentation polygons (or bounding boxes), translucent fills, and corner confidence badges on the image."""
    canvas = image_bgr.copy()
    overlay = image_bgr.copy()
    h, w = canvas.shape[:2]

    # Pre-draw polygon fills on overlay
    for detection in detections:
        pts = np.array(detection["polygon"] if draw_mode == "polygon" else detection["box"], dtype=np.int32)
        score = detection["score"]

        if color_by_confidence:
            cfg = get_confidence_color(score)
        else:
            cfg = COLOR_SCHEMES.get(color_scheme_name, COLOR_SCHEMES["cyan"])

        cv2.fillPoly(overlay, [pts], cfg["fill"])

    # Blend translucent fills
    if fill_alpha > 0 and len(detections) > 0:
        cv2.addWeighted(overlay, fill_alpha, canvas, 1.0 - fill_alpha, 0, canvas)

    # Dynamic badge font scaling based on image dimensions
    base_dim = max(h, w)
    font_scale = max(0.38, min(0.65, base_dim / 2200.0 * 0.45))
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_thickness = 1

    # Draw polygon outlines and corner confidence badges
    for idx, detection in enumerate(detections, start=1):
        pts = np.array(detection["polygon"] if draw_mode == "polygon" else detection["box"], dtype=np.int32)
        score = detection["score"]

        if color_by_confidence:
            cfg = get_confidence_color(score)
        else:
            cfg = COLOR_SCHEMES.get(color_scheme_name, COLOR_SCHEMES["cyan"])

        # Anti-aliased polygon outline
        cv2.polylines(canvas, [pts], isClosed=True, color=cfg["border"], thickness=line_thickness, lineType=cv2.LINE_AA)

        if not show_confidence:
            continue

        # Format score text
        if score_format == "percent":
            score_text = f"{int(round(score * 100))}%"
        else:
            score_text = f"{score:.2f}"

        if show_indices:
            badge_text = f"#{idx} {score_text}"
        else:
            badge_text = score_text

        # Compute corner anchor position (top-left of box)
        min_x = int(np.min(pts[:, 0]))
        min_y = int(np.min(pts[:, 1]))

        (tw, th), baseline = cv2.getTextSize(badge_text, font, font_scale, font_thickness)
        pad_x = 3
        pad_y = 2

        bw = tw + pad_x * 2
        bh = th + pad_y * 2 + baseline

        # Position badge above top-left corner if space allows, otherwise inside
        bx1 = max(0, min(w - bw - 1, min_x))
        if min_y - bh >= 0:
            by1 = min_y - bh
        else:
            by1 = min(h - bh - 1, min_y + 2)

        bx2 = bx1 + bw
        by2 = by1 + bh

        # Render badge background and outline
        cv2.rectangle(canvas, (bx1, by1), (bx2, by2), cfg["badge_bg"], -1)
        cv2.rectangle(canvas, (bx1, by1), (bx2, by2), cfg["badge_border"], 1, lineType=cv2.LINE_AA)

        # Render badge text
        text_origin = (bx1 + pad_x, by1 + th + pad_y)
        cv2.putText(
            canvas,
            badge_text,
            text_origin,
            font,
            font_scale,
            cfg["text"],
            font_thickness,
            lineType=cv2.LINE_AA,
        )

    return canvas


def generate_text_mask(
    image_shape: tuple[int, ...],
    detections: list[dict[str, Any]],
    draw_mode: str = "polygon",
) -> np.ndarray:
    """Generate a high-resolution binary text mask (H, W) with 255 for text regions and 0 for background."""
    h, w = image_shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for det in detections:
        pts = np.array(det["polygon"] if draw_mode == "polygon" else det["box"], dtype=np.int32)
        cv2.fillPoly(mask, [pts], 255)
    return mask


class MangaTextSegmenter:
    """Stroke/glyph-level character segmentation producing crisp character ink masks."""

    def __init__(self, model_path: Path | None = None, device: str = "auto", method: str = "adaptive"):
        self.method = method.lower()
        self.session = None
        self.device_desc = "Adaptive Otsu Glyph Binarizer (CPU)"
        if self.method == "neural":
            self.model_path = ensure_segmentation_model_file(model_path)
            self.providers, self.device_desc = select_onnx_providers(device)
            self.session = ort.InferenceSession(str(self.model_path), providers=self.providers)
            self.input_name = self.session.get_inputs()[0].name
            self.output_name = self.session.get_outputs()[0].name
            print(f"[*] Loaded Manga-Text-Segmentation ({self.model_path.name}) on {self.device_desc}")

    def segment_gated_strokes(
        self,
        image_bgr: np.ndarray,
        detections: list[dict[str, Any]],
        stroke_thresh: float = 0.35,
        dilate_px: int = 0,
        pad_px: int = 2,
    ) -> np.ndarray:
        """Run fine-grained character stroke/glyph segmentation gated by detected text polygons."""
        h, w = image_bgr.shape[:2]
        full_stroke_mask = np.zeros((h, w), dtype=np.uint8)

        if not detections:
            return full_stroke_mask

        for det in detections:
            poly = np.array(det["polygon"], dtype=np.int32)
            if poly.ndim != 2 or len(poly) < 3:
                continue

            x, y, bw, bh = cv2.boundingRect(poly)
            bx1 = max(0, x - pad_px)
            by1 = max(0, y - pad_px)
            bx2 = min(w, x + bw + pad_px)
            by2 = min(h, y + bh + pad_px)

            crop = image_bgr[by1:by2, bx1:bx2]
            ch, cw = crop.shape[:2]
            if ch < 3 or cw < 3:
                continue

            local_poly = poly - np.array([bx1, by1])
            poly_mask = np.zeros((ch, cw), dtype=np.uint8)
            cv2.fillPoly(poly_mask, [local_poly], 255)

            if self.method == "neural" and self.session is not None:
                pad_h = (32 - ch % 32) % 32
                pad_w = (32 - cw % 32) % 32
                crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
                if pad_h > 0 or pad_w > 0:
                    crop_padded = np.pad(crop_rgb, ((0, pad_h), (0, pad_w), (0, 0)), mode="constant")
                else:
                    crop_padded = crop_rgb

                tensor = np.transpose(crop_padded, (2, 0, 1))[np.newaxis, :, :, :].astype(np.float32)
                out = self.session.run(None, {self.input_name: tensor})[0]
                raw_prob = out[0, 0]
                upsampled = cv2.resize(raw_prob, (cw + pad_w, ch + pad_h), interpolation=cv2.INTER_LINEAR)
                prob_map = upsampled[:ch, :cw]
                local_stroke = (prob_map > stroke_thresh).astype(np.uint8) * 255
                clean_bin = cv2.bitwise_and(local_stroke, poly_mask)
            else:
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
                inside_pixels = gray[poly_mask > 0]
                if len(inside_pixels) == 0:
                    continue

                mean_val = float(np.mean(inside_pixels))
                median_val = float(np.median(inside_pixels))

                _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

                if median_val > 127 or mean_val > 127:
                    text_bin = 255 - th
                else:
                    text_bin = th

                text_bin = cv2.bitwise_and(text_bin, poly_mask)

                num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(text_bin, 8)
                clean_bin = np.zeros_like(text_bin)
                for label in range(1, num_labels):
                    area = stats[label, cv2.CC_STAT_AREA]
                    comp_w = stats[label, cv2.CC_STAT_WIDTH]
                    comp_h = stats[label, cv2.CC_STAT_HEIGHT]
                    if area >= 2 and (area < bw * bh * 0.90) and (comp_w < cw or comp_h < ch or area < bw * bh * 0.7):
                        clean_bin[labels == label] = 255

            if dilate_px > 0:
                kernel_size = 2 * dilate_px + 1
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
                clean_bin = cv2.dilate(clean_bin, kernel, iterations=1)

            full_stroke_mask[by1:by2, bx1:bx2] = np.maximum(
                full_stroke_mask[by1:by2, bx1:bx2], clean_bin
            )

        return full_stroke_mask


def render_stroke_overlay(
    image_bgr: np.ndarray,
    stroke_mask: np.ndarray,
    highlight_color: tuple[int, int, int] = (0, 0, 245),
    alpha: float = 0.75,
) -> np.ndarray:
    """Render vivid highlighted glyph strokes directly over the original manga image."""
    overlay = image_bgr.copy()
    mask_indices = stroke_mask > 0
    if not np.any(mask_indices):
        return overlay

    color_layer = np.zeros_like(image_bgr)
    color_layer[:] = highlight_color

    blended = cv2.addWeighted(image_bgr, 1.0 - alpha, color_layer, alpha, 0)
    overlay[mask_indices] = blended[mask_indices]
    return overlay


def expand_input_patterns(patterns: Sequence[str]) -> list[Path]:
    """Expand file paths, directory paths, and wildcard patterns into a list of existing image files."""
    collected: list[Path] = []
    seen: set[str] = set()

    for item in patterns:
        item_str = str(item).strip()
        if not item_str:
            continue

        expanded_str = os.path.expanduser(item_str)

        # Check for glob pattern (e.g. *.png, **/*.jpg)
        if any(c in expanded_str for c in ["*", "?", "["]):
            matched_files = glob.glob(expanded_str, recursive=True)
            for m in sorted(matched_files):
                p = Path(m).resolve()
                if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS and str(p) not in seen:
                    collected.append(p)
                    seen.add(str(p))
            continue

        path = Path(expanded_str)
        if path.is_dir():
            for root, _, files in os.walk(path):
                for f in sorted(files):
                    fp = Path(root) / f
                    if fp.suffix.lower() in IMAGE_EXTENSIONS and str(fp.resolve()) not in seen:
                        collected.append(fp.resolve())
                        seen.add(str(fp.resolve()))
        elif path.is_file():
            if path.suffix.lower() in IMAGE_EXTENSIONS and str(path.resolve()) not in seen:
                collected.append(path.resolve())
                seen.add(str(path.resolve()))
            else:
                print(f"[!] Warning: '{path}' is not a recognized image extension.", file=sys.stderr)
        else:
            print(f"[!] Warning: Path or pattern '{item_str}' does not match any existing files.", file=sys.stderr)

    return collected


def save_crops(image_bgr: np.ndarray, detections: list[dict[str, Any]], output_crop_dir: Path, base_name: str):
    """Save cropped image patches for each detected text region."""
    output_crop_dir.mkdir(parents=True, exist_ok=True)
    h, w = image_bgr.shape[:2]

    for idx, det in enumerate(detections, start=1):
        box = np.array(det["box"], dtype=np.int32)
        x_min = max(0, int(box[:, 0].min()))
        x_max = min(w, int(box[:, 0].max()))
        y_min = max(0, int(box[:, 1].min()))
        y_max = min(h, int(box[:, 1].max()))

        if x_max <= x_min or y_max <= y_min:
            continue

        crop = image_bgr[y_min:y_max, x_min:x_max]
        crop_path = output_crop_dir / f"{base_name}_crop_{idx:03d}_score_{det['score']:.2f}.png"
        cv2.imwrite(str(crop_path), crop)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run PP-OCRv6_manga v0.2 text detection with corner confidence overlays and text masks.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Input and Output
    parser.add_argument(
        "-i", "--input", "--inputs",
        dest="inputs",
        nargs="+",
        required=True,
        help="Input image path(s), directory, or wildcard pattern(s) (e.g. 'devscripts/input/*.png', 'data/**/*.jpg').",
    )
    parser.add_argument(
        "-o", "--output", "--output-dir",
        dest="output",
        default="result/ppocrv6_detection",
        help="Output directory or output file path (if processing a single image).",
    )
    parser.add_argument(
        "--suffix",
        default="_det",
        help="Suffix to append to output overlay image filename (e.g. '_det' -> 'page_001_det.png').",
    )
    parser.add_argument(
        "--mask-suffix",
        default="_mask",
        help="Suffix to append to output binary text mask filename (e.g. '_mask' -> 'page_001_mask.png').",
    )
    parser.add_argument(
        "--no-mask",
        action="store_true",
        help="Disable automatic text mask generation.",
    )

    # Batch and Hardware Configuration
    parser.add_argument(
        "-b", "--batch-size",
        type=int,
        default=4,
        help="Batch size N: number of images dispatched to the GPU/accelerator at a time in a single forward pass.",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "mps", "coreml", "cuda", "directml", "cpu"],
        default="auto",
        help="Hardware accelerator backend ('mps'/'coreml' for Apple Silicon, 'cuda' for NVIDIA, 'cpu').",
    )

    # Detector Engine Selector
    parser.add_argument(
        "-m", "--model", "--detector",
        dest="model",
        default="ppocrv6",
        choices=[
            "ppocrv6", "ppocr", "pp-ocr", "pp-ocrv6",
            "default", "dbnet", "core", "manga-text-detector",
            "ctd", "comictextdetector", "comic-text-detector",
            "contemporarycat", "manga-text-seg", "manga-text-segmentation", "textseg", "juvian",
        ],
        help="Text detector engine: 'ppocrv6' (PP-OCRv6 Manga v0.2), 'default' (Core app DBNet ResNet-34), 'ctd' (Comic Text Detector), or 'contemporarycat' (Manga-Text-Segmentation).",
    )

    # Model configuration
    parser.add_argument(
        "--model-path", "--weights",
        type=Path,
        default=None,
        help="Custom path to model weights file. If not provided, automatically downloaded.",
    )
    parser.add_argument(
        "--model-version",
        choices=["v0.2", "v0.1"],
        default="v0.2",
        help="PP-OCRv6_manga detection model version.",
    )
    parser.add_argument(
        "--precision",
        choices=["fp32", "fp16"],
        default="fp32",
        help="Model precision variant (FP32: 1.73 MB, FP16: 0.92 MB).",
    )

    # Detection parameters
    parser.add_argument(
        "--detect-size", "--size",
        type=int,
        default=960,
        help="Detector input long-side target resolution (multiple of 32 recommended).",
    )
    parser.add_argument(
        "--thresh", "--text-threshold",
        type=float,
        default=0.20,
        help="Pixel probability binarization threshold.",
    )
    parser.add_argument(
        "--box-thresh", "--box-threshold",
        type=float,
        default=0.25,
        help="Minimum bounding box average confidence score threshold.",
    )
    parser.add_argument(
        "--unclip-ratio",
        type=float,
        default=1.0,
        help="Polygon expansion ratio (1.0 = tight segmentation contour, >1.0 = expanded padding for OCR crops).",
    )
    parser.add_argument(
        "--box", "--bbox",
        action="store_true",
        help="Render 4-point bounding boxes instead of multi-point segmentation polygon contours (default: polygon overlay).",
    )

    # Visualization and styling
    parser.add_argument(
        "--color-scheme",
        choices=list(COLOR_SCHEMES.keys()),
        default="cyan",
        help="Overlay border and badge color palette.",
    )
    parser.add_argument(
        "--color-by-confidence",
        action="store_true",
        help="Color code bounding boxes and badges by confidence (Green >= 0.85, Yellow 0.6-0.85, Red < 0.6).",
    )
    parser.add_argument(
        "--fill-alpha",
        type=float,
        default=0.22,
        help="Translucent fill opacity inside detected boxes (0.0 to 1.0).",
    )
    parser.add_argument(
        "--line-thickness",
        type=int,
        default=2,
        help="Bounding box outline thickness.",
    )
    parser.add_argument(
        "--hide-confidence",
        action="store_true",
        help="Hide confidence score badges on overlay.",
    )
    parser.add_argument(
        "--score-format",
        choices=["decimal", "percent"],
        default="decimal",
        help="Display format for confidence badges ('decimal': 0.94, 'percent': 94%%).",
    )
    parser.add_argument(
        "--show-indices",
        action="store_true",
        help="Include line index numbers in confidence badges (e.g. '#1 0.94').",
    )
    parser.add_argument(
        "--side-by-side",
        action="store_true",
        help="Output side-by-side comparison image (original on left, overlay on right).",
    )

    # Stroke / Glyph Segmentation Options
    parser.add_argument(
        "--stroke-mask", "--save-stroke-mask", "--refine-stroke",
        dest="stroke_mask",
        action="store_true",
        help="Generate high-resolution character stroke/glyph mask gated by detected regions (white characters on black background).",
    )
    parser.add_argument(
        "--stroke-method",
        choices=["adaptive", "neural"],
        default="adaptive",
        help="Stroke extraction method: 'adaptive' (crisp Otsu & connected-component glyph binarization) or 'neural' (ONNX model).",
    )
    parser.add_argument(
        "--stroke-thresh",
        type=float,
        default=0.35,
        help="Probability threshold for neural character stroke pixel binarization.",
    )
    parser.add_argument(
        "--stroke-dilate",
        type=int,
        default=0,
        help="Dilation radius in pixels for stroke mask (0 for tightest raw character strokes matching ground truth, 1-2 for inpainting coverage).",
    )
    parser.add_argument(
        "--stroke-overlay", "--save-stroke-overlay",
        dest="stroke_overlay",
        action="store_true",
        help="Export visual stroke overlay image (<stem>_stroke_overlay.png) highlighting character glyphs in bright red.",
    )
    parser.add_argument(
        "--stroke-suffix",
        default="_stroke_mask",
        help="Suffix to append to output stroke mask filename.",
    )
    parser.add_argument(
        "--stroke-overlay-suffix",
        default="_stroke_overlay",
        help="Suffix to append to output stroke overlay image filename.",
    )

    # Extra output artifacts
    parser.add_argument(
        "--save-json", "--json",
        action="store_true",
        help="Export structured detection JSON with coordinates and scores for each image.",
    )
    parser.add_argument(
        "--save-heatmap",
        action="store_true",
        help="Export continuous probability heatmap image.",
    )
    parser.add_argument(
        "--save-crops",
        action="store_true",
        help="Save cropped image patches for each detected text box.",
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    # 1. Resolve input files
    input_images = expand_input_patterns(args.inputs)
    if not input_images:
        print("[!] Error: No valid image files found matching input specification.", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Discovered {len(input_images)} input image(s) to process (batch size = {args.batch_size}).")

    # 2. Setup detector engine
    detector = create_detector_engine(
        model_name=args.model,
        device=args.device,
        model_version=args.model_version,
        precision=args.precision,
        custom_weights=args.model_path,
    )

    segmenter: MangaTextSegmenter | None = None
    if args.stroke_mask or args.stroke_overlay:
        segmenter = MangaTextSegmenter(device=args.device, method=args.stroke_method)

    # 3. Setup output destination
    output_path = Path(args.output).expanduser()
    single_file_target = len(input_images) == 1 and output_path.suffix.lower() in IMAGE_EXTENSIONS

    if not single_file_target:
        output_path.mkdir(parents=True, exist_ok=True)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    total_detections_count = 0
    total_infer_time = 0.0
    draw_mode = "box" if args.box else "polygon"

    print("-" * 60)
    print(f"{'Image':<35} | {'Detections':<10} | {'Time (ms)':<10} | {'Avg Conf':<8}")
    print("-" * 60)

    # Process in batches
    batch_size = max(1, args.batch_size)
    total_batches = (len(input_images) + batch_size - 1) // batch_size

    for batch_idx, start_idx in enumerate(range(0, len(input_images), batch_size), start=1):
        chunk_paths = input_images[start_idx : start_idx + batch_size]
        chunk_imgs: list[np.ndarray] = []
        valid_paths: list[Path] = []

        for p in chunk_paths:
            im = cv2.imread(str(p))
            if im is None:
                print(f"[!] Warning: Failed to read image '{p}'. Skipping.", file=sys.stderr)
                continue
            chunk_imgs.append(im)
            valid_paths.append(p)

        if not chunk_imgs:
            continue

        # Batched inference on GPU / accelerator
        t0 = time.perf_counter()
        batch_detections, batch_masks = detector.detect_batch(
            chunk_imgs,
            detect_size=args.detect_size,
            thresh=args.thresh,
            box_thresh=args.box_thresh,
            unclip_ratio=args.unclip_ratio,
        )
        t1 = time.perf_counter()
        batch_duration_ms = (t1 - t0) * 1000.0
        per_image_ms = batch_duration_ms / len(chunk_imgs)
        total_infer_time += (t1 - t0)

        # Process and save each result in the batch
        for img, img_path, detections, pred_map in zip(chunk_imgs, valid_paths, batch_detections, batch_masks):
            total_detections_count += len(detections)
            avg_score = float(np.mean([d["score"] for d in detections])) if detections else 0.0

            # Render overlay with polygon segmentation contours (or box if requested)
            overlay = render_overlay(
                img,
                detections,
                draw_mode=draw_mode,
                color_scheme_name=args.color_scheme,
                color_by_confidence=args.color_by_confidence,
                fill_alpha=args.fill_alpha,
                line_thickness=args.line_thickness,
                show_confidence=not args.hide_confidence,
                score_format=args.score_format,
                show_indices=args.show_indices,
            )

            if args.side_by_side:
                final_output = np.hstack([img, overlay])
            else:
                final_output = overlay

            # Determine output file path for overlay
            if single_file_target:
                dest_img_path = output_path
                dest_mask_path = output_path.parent / f"{output_path.stem}{args.mask_suffix}.png"
                dest_stroke_mask_path = output_path.parent / f"{output_path.stem}{args.stroke_suffix}.png"
                dest_stroke_overlay_path = output_path.parent / f"{output_path.stem}{args.stroke_overlay_suffix}.png"
                parent_dir = output_path.parent
            else:
                dest_img_path = output_path / f"{img_path.stem}{args.suffix}{img_path.suffix}"
                dest_mask_path = output_path / f"{img_path.stem}{args.mask_suffix}.png"
                dest_stroke_mask_path = output_path / f"{img_path.stem}{args.stroke_suffix}.png"
                dest_stroke_overlay_path = output_path / f"{img_path.stem}{args.stroke_overlay_suffix}.png"
                parent_dir = output_path

            cv2.imwrite(str(dest_img_path), final_output)

            # Generate and save binary text mask matching original image dimensions
            if not args.no_mask:
                binary_mask = generate_text_mask(img.shape, detections, draw_mode=draw_mode)
                cv2.imwrite(str(dest_mask_path), binary_mask)

            # Fine-grained character stroke / glyph segmentation
            if segmenter is not None:
                stroke_mask = segmenter.segment_gated_strokes(
                    img,
                    detections,
                    stroke_thresh=args.stroke_thresh,
                    dilate_px=args.stroke_dilate,
                )
                if args.stroke_mask:
                    cv2.imwrite(str(dest_stroke_mask_path), stroke_mask)
                if args.stroke_overlay:
                    stroke_overlay_img = render_stroke_overlay(img, stroke_mask)
                    cv2.imwrite(str(dest_stroke_overlay_path), stroke_overlay_img)

            # Continuous probability heatmap
            if args.save_heatmap:
                heatmap_uint8 = np.clip(pred_map * 255.0, 0, 255).astype(np.uint8)
                heatmap_dest = parent_dir / f"{img_path.stem}_heatmap.png"
                cv2.imwrite(str(heatmap_dest), heatmap_uint8)

            # JSON metadata export
            if args.save_json:
                json_dest = parent_dir / f"{img_path.stem}_det.json"
                meta = {
                    "image": img_path.name,
                    "width": img.shape[1],
                    "height": img.shape[0],
                    "detector": f"PP-OCRv6_manga {args.model_version} ({args.precision})",
                    "device": detector.device_desc,
                    "detect_size": args.detect_size,
                    "batch_size": batch_size,
                    "inference_ms": round(per_image_ms, 2),
                    "num_detections": len(detections),
                    "detections": detections,
                }
                with open(json_dest, "w", encoding="utf-8") as jf:
                    json.dump(meta, jf, indent=2, ensure_ascii=False)

            if args.save_crops and detections:
                crop_dir = parent_dir / "crops" / img_path.stem
                save_crops(img, detections, crop_dir, img_path.stem)

            print(f"{img_path.name[:35]:<35} | {len(detections):<10} | {per_image_ms:<10.1f} | {avg_score:<8.2f}")

    print("-" * 60)
    throughput = len(input_images) / max(0.001, total_infer_time)
    print(f"[+] Done! Processed {len(input_images)} image(s) in {total_infer_time:.2f}s ({throughput:.1f} img/s).")
    print(f"[+] Total text regions detected: {total_detections_count}")
    print(f"[+] Output saved to: {output_path.resolve()}")


if __name__ == "__main__":
    main()

