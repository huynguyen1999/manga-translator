"""Lazy Manga109 speech-bubble segmentation.

The detector is optional so the normal translation path never imports or
downloads the YOLO stack.
"""
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Optional
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
        return self._read_result(
            self.model.predict(
                source=cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
                device=self.device,
                conf=self.confidence,
                imgsz=self.image_size,
                retina_masks=True,
                verbose=False,
            )[0],
            image.shape[:2],
        )

    def detect_batch(self, images: list[np.ndarray]) -> list[list[BubbleDetection]]:
        if not images:
            return []
        results = self.model.predict(
            source=[cv2.cvtColor(image, cv2.COLOR_RGB2BGR) for image in images],
            device=self.device,
            conf=self.confidence,
            imgsz=self.image_size,
            retina_masks=True,
            batch=len(images),
            verbose=False,
        )
        if len(results) != len(images):
            raise RuntimeError(f"Bubble detector returned {len(results)} pages for {len(images)} inputs")
        return [self._read_result(result, image.shape[:2]) for result, image in zip(results, images)]

    def _read_result(self, result, image_shape: tuple[int, int]) -> list[BubbleDetection]:
        if result.masks is None:
            return []
        detections = []
        for mask, confidence in zip(result.masks.data, result.boxes.conf):
            score = float(confidence)
            if score < self.confidence:
                continue
            values = mask.detach().float().cpu().numpy()
            if values.shape != image_shape:
                values = cv2.resize(values, (image_shape[1], image_shape[0]), interpolation=cv2.INTER_LINEAR)
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
async def dispatch_batch(images: list[np.ndarray], config, device: str = "cpu") -> list[list[BubbleDetection]]:
    if not images:
        return []
    target_device = device if device and device != "auto" else getattr(config, "device", "cpu")
    detector = get_detector(config.model, config.confidence, config.mask_threshold, config.image_size, target_device)
    return detector.detect_batch(images)


@model_operation
async def unload():
    get_model_cache('bubble_detector', _bubble_cache).clear()


def serialize_bubble_detections(
    bubble_detections: list[Any],
    lobe_graphs: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert BubbleDetection objects into JSON serializable dictionaries."""
    serialized = []
    for idx, bd in enumerate(bubble_detections or []):
        mask = getattr(bd, "mask", None)
        conf = float(getattr(bd, "confidence", 1.0))
        entry: dict[str, Any] = {
            "index": idx,
            "confidence": conf,
            "xyxy": [],
            "xywh": [],
            "area_pixels": 0,
            "polygon": [],
            "polygons": [],
        }
        if mask is not None and np.any(mask):
            mask_arr = np.asarray(mask, dtype=np.uint8)
            ys, xs = np.where(mask_arr > 0)
            if len(xs) > 0 and len(ys) > 0:
                x1, x2 = int(xs.min()), int(xs.max())
                y1, y2 = int(ys.min()), int(ys.max())
                entry["xyxy"] = [x1, y1, x2, y2]
                entry["xywh"] = [x1, y1, x2 - x1, y2 - y1]
                entry["area_pixels"] = int(np.count_nonzero(mask_arr))

                contours, _ = cv2.findContours(mask_arr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                polys = []
                for cnt in contours:
                    if len(cnt) >= 3:
                        peri = cv2.arcLength(cnt, True)
                        approx = cv2.approxPolyDP(cnt, 0.01 * peri, True)
                        polys.append(approx.reshape(-1, 2).tolist())
                entry["polygons"] = polys
                if polys:
                    entry["polygon"] = polys[0]
        if lobe_graphs is not None and idx < len(lobe_graphs):
            graph = lobe_graphs[idx]
            if hasattr(graph, "to_dict"):
                entry["lobe_graph"] = graph.to_dict()
            elif isinstance(graph, dict):
                entry["lobe_graph"] = graph
        serialized.append(entry)
    return serialized


def deserialize_bubble_detections(
    data: list[dict[str, Any]],
    image_shape: tuple[int, ...],
) -> list[BubbleDetection]:
    """Reconstruct BubbleDetection objects from serialized JSON dictionaries."""
    reconstructed = []
    for item in data or []:
        conf = float(item.get("confidence", 0.9))
        mask = np.zeros(image_shape[:2], dtype=np.uint8)
        polys = item.get("polygons")
        if not polys and "polygon" in item and item["polygon"]:
            polys = [item["polygon"]]
        if polys:
            for poly in polys:
                pts = np.asarray(poly, dtype=np.int32)
                if len(pts) >= 3:
                    cv2.fillPoly(mask, [pts], 255)
        elif "xyxy" in item and item["xyxy"]:
            x1, y1, x2, y2 = item["xyxy"]
            mask[y1:y2, x1:x2] = 255
        reconstructed.append(BubbleDetection(mask=mask, confidence=conf))
    return reconstructed
