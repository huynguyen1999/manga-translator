"""Lazy Manga109 speech-bubble segmentation.

The detector is optional so the normal translation path never imports or
downloads the YOLO stack.
"""
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
import logging
import shutil

import cv2
import numpy as np

from ..utils.model_cache import get_model_cache, model_operation

logger = logging.getLogger(__name__)
_bubble_cache: dict[tuple[str, str, float, float, int], "BubbleDetector"] = {}


@dataclass(frozen=True)
class BubbleDetection:
    mask: np.ndarray
    confidence: float


def _resolve_checkpoint(model: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "models" / "bubbles"
    if model == "manga109":
        target = root / "manga109" / "best.pt"
        repo_id, filename = "juithealien/manga109-segmentation-bubble", "best.pt"
    elif model == "yolov8m":
        target = root / "yolov8m_seg-speech-bubble.pt"
        repo_id = filename = None
    else:
        raise ValueError(f"unknown bubble model: {model!r}")
    if target.is_file():
        return target
    if not repo_id:
        raise FileNotFoundError(f"bubble model was not found at {target}")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as error:
        raise RuntimeError("Manga109 requires huggingface_hub") from error
    target.parent.mkdir(parents=True, exist_ok=True)
    downloaded = Path(hf_hub_download(repo_id=repo_id, filename=filename, local_dir=str(target.parent)))
    if downloaded != target and downloaded.is_file():
        shutil.copy2(downloaded, target)
    if not target.is_file():
        raise RuntimeError(f"bubble model download did not create {target}")
    return target


class BubbleDetector:
    def __init__(self, model: str, confidence: float, mask_threshold: float, image_size: int, device: str):
        try:
            import torch
            from ultralytics import YOLO
        except ImportError as error:
            raise RuntimeError("bubble detection requires ultralytics and torch") from error
        if device == "auto":
            if torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        self.device = device
        self.confidence = confidence
        self.mask_threshold = mask_threshold
        self.image_size = image_size
        self.model = YOLO(str(_resolve_checkpoint(model)))

    def __call__(self, image: np.ndarray) -> list[BubbleDetection]:
        result = self.model.predict(
            source=cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
            device=self.device,
            conf=self.confidence,
            imgsz=self.image_size,
            retina_masks=True,
            verbose=False,
        )[0]
        if result.masks is None:
            return []
        detections = []
        for mask, confidence in zip(result.masks.data, result.boxes.conf):
            score = float(confidence)
            if score < self.confidence:
                continue
            values = mask.detach().float().cpu().numpy()
            if values.shape != image.shape[:2]:
                values = cv2.resize(values, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)
            binary = np.where(values >= self.mask_threshold, 255, 0).astype(np.uint8)
            if np.count_nonzero(binary) >= 100:
                detections.append(BubbleDetection(binary, score))
        return detections


def get_detector(model: str, confidence: float, mask_threshold: float, image_size: int, device: str = "cpu") -> BubbleDetector:
    cache = get_model_cache('bubble_detector', _bubble_cache)
    key = (model, device, confidence, mask_threshold, image_size)
    detector = cache.get(key)
    if detector is None:
        started = perf_counter()
        detector = BubbleDetector(model, confidence, mask_threshold, image_size, device)
        cache[key] = detector
        logger.info("Loaded bubble detector %s in %.0fms", model, (perf_counter() - started) * 1000)
    return detector


def detect(image: np.ndarray, config, device: str = "cpu") -> list[BubbleDetection]:
    target_device = device if device and device != "auto" else getattr(config, "device", "cpu")
    detector = get_detector(config.model, config.confidence, config.mask_threshold, config.image_size, target_device)
    return detector(image)


@model_operation
async def prepare(config, device: str = "cpu"):
    target_device = device if device and device != "auto" else getattr(config, "device", "cpu")
    get_detector(config.model, config.confidence, config.mask_threshold, config.image_size, target_device)


@model_operation
async def dispatch(image: np.ndarray, config, device: str = "cpu") -> list[BubbleDetection]:
    return detect(image, config, device=device)


@model_operation
async def unload():
    get_model_cache('bubble_detector', _bubble_cache).clear()
