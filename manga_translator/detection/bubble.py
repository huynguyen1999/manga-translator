"""Lazy multi-class Manga segmentation (speech-bubble + panel detection)."""
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any
import logging
import cv2
import numpy as np

from .panel import (
    PanelDetection,
    deserialize_bubble_detections as _deserialize_bubbles,
    deserialize_panel_detections,
    resolve_model_checkpoint,
    serialize_bubble_detections as _serialize_bubbles,
    serialize_panel_detections,
    sort_panel_detections_reading_order,
)
from ..utils.model_cache import (
    clear_model_cache, get_cached_model, model_operation, unload_cached_model,
)
from .bubble_state import BubbleDetectionState, get_bubble_detection_state, model_class_map, set_bubble_detection_state
from .bubble_state import detection_class_name

logger = logging.getLogger(__name__)
_bubble_cache: dict[tuple[str, str, float, float, int], "BubbleDetector"] = {}


@dataclass(frozen=True)
class BubbleDetection:
    mask: np.ndarray
    confidence: float


def _resolve_checkpoint(model: str) -> Path:
    return resolve_model_checkpoint(model)


class BubbleDetector:
    def __init__(self, model: str, confidence: float = 0.25, mask_threshold: float = 0.5, image_size: int = 512, device: str = "cpu", *args, **kwargs):
        if len(args) == 1 and isinstance(args[0], str):
            device = args[0]
        elif len(args) >= 3:
            confidence = float(args[0])
            mask_threshold = float(args[1])
            image_size = int(args[2])
            if len(args) >= 4 and isinstance(args[3], str):
                device = args[3]
        if "device" in kwargs:
            device = kwargs["device"]

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
        self.class_map, self.single_class_bubble_model = model_class_map(getattr(self.model, "names", {}))
        self.last_panel_detections: list[PanelDetection] = []

    def __call__(self, image: np.ndarray, confidence: float | None = None, mask_threshold: float | None = None, image_size: int | None = None) -> list[BubbleDetection]:
        conf = confidence if confidence is not None else getattr(self, "confidence", 0.25)
        m_thresh = mask_threshold if mask_threshold is not None else getattr(self, "mask_threshold", 0.5)
        img_sz = image_size if image_size is not None else getattr(self, "image_size", 512)
        bubbles, panels = self.detect_joint(image, conf, m_thresh, img_sz)
        self.last_panel_detections = panels
        return bubbles

    def detect_joint(self, image: np.ndarray, confidence: float | None = None, mask_threshold: float | None = None, image_size: int | None = None) -> tuple[list[BubbleDetection], list[PanelDetection]]:
        conf = confidence if confidence is not None else getattr(self, "confidence", 0.25)
        m_thresh = mask_threshold if mask_threshold is not None else getattr(self, "mask_threshold", 0.5)
        img_sz = image_size if image_size is not None else getattr(self, "image_size", 512)
        logger.info("Bubble detector inference batch pages=1 device=%s", getattr(self, "device", "cpu"))
        result = self.model.predict(
            source=cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
            device=getattr(self, "device", "cpu"),
            conf=conf,
            imgsz=img_sz,
            retina_masks=True,
            verbose=False,
        )[0]
        return self._read_result(result, image.shape[:2], conf, m_thresh)

    def detect_batch(self, images: list[np.ndarray], confidence: float | None = None, mask_threshold: float | None = None, image_size: int | None = None) -> list[list[BubbleDetection]]:
        batch_results = self.detect_batch_joint(images, confidence, mask_threshold, image_size)
        return [bubbles for bubbles, _ in batch_results]

    def detect_batch_joint(self, images: list[np.ndarray], confidence: float | None = None, mask_threshold: float | None = None, image_size: int | None = None) -> list[tuple[list[BubbleDetection], list[PanelDetection]]]:
        if not images:
            return []
        conf = confidence if confidence is not None else getattr(self, "confidence", 0.25)
        m_thresh = mask_threshold if mask_threshold is not None else getattr(self, "mask_threshold", 0.5)
        img_sz = image_size if image_size is not None else getattr(self, "image_size", 512)
        logger.info("Bubble detector inference batch pages=%d device=%s", len(images), getattr(self, "device", "cpu"))
        source = [cv2.cvtColor(image, cv2.COLOR_RGB2BGR) for image in images]
        results = self.model.predict(
            source=source,
            device=getattr(self, "device", "cpu"),
            conf=conf,
            imgsz=img_sz,
            retina_masks=True,
            batch=len(images),
            verbose=False,
        )
        del source
        if len(results) != len(images):
            raise RuntimeError(f"Bubble detector returned {len(results)} pages for {len(images)} inputs")
        return [
            self._read_result(result, image.shape[:2], conf, m_thresh)
            for result, image in zip(results, images)
        ]

    def _read_result(self, result, image_shape: tuple[int, int], confidence_threshold: float, mask_threshold: float) -> tuple[list[BubbleDetection], list[PanelDetection]]:
        if result is None or getattr(result, "masks", None) is None:
            return [], []
        masks_data = getattr(result.masks, "data", None)
        if masks_data is None or len(masks_data) == 0:
            return [], []
        bubbles: list[BubbleDetection] = []
        raw_panels: list[PanelDetection] = []
        raw_polygons = getattr(result.masks, "xy", None)
        boxes = getattr(result, "boxes", None)
        boxes_conf = getattr(boxes, "conf", None) if boxes is not None else None
        if boxes is not None:
            try:
                boxes_iter = [boxes[i] for i in range(len(boxes))]
                if len(boxes_iter) == 0 and len(masks_data) > 0:
                    boxes_iter = list(boxes)
            except Exception:
                try:
                    boxes_iter = list(boxes)
                except Exception:
                    boxes_iter = [None] * len(masks_data)
        else:
            boxes_iter = [None] * len(masks_data)
        if len(boxes_iter) < len(masks_data):
            boxes_iter = boxes_iter + [None] * (len(masks_data) - len(boxes_iter))

        class_map = getattr(self, "class_map", {})

        for idx, mask in enumerate(masks_data):
            conf_t = boxes_conf[idx] if (boxes_conf is not None and idx < len(boxes_conf)) else 1.0
            score = float(conf_t.item() if hasattr(conf_t, "item") else conf_t)
            if score < confidence_threshold:
                continue
            box = boxes_iter[idx]
            cls_id = int(box.cls[0].item()) if (box is not None and hasattr(box, "cls") and len(box.cls) > 0) else 0
            norm_class = detection_class_name(class_map, cls_id, getattr(self, "single_class_bubble_model", False))

            values = mask.detach().float().cpu().numpy() if hasattr(mask, "detach") else np.asarray(mask, dtype=np.float32)
            if values.shape != image_shape:
                values = cv2.resize(values, (image_shape[1], image_shape[0]), interpolation=cv2.INTER_LINEAR)
            binary = np.where(values >= mask_threshold, 255, 0).astype(np.uint8)

            if norm_class == "panel":
                xyxy = [int(v) for v in box.xyxy[0].tolist()] if (box is not None and hasattr(box, "xyxy") and len(box.xyxy) > 0) else [0, 0, image_shape[1], image_shape[0]]
                polys = [[[int(pt[0]), int(pt[1])] for pt in raw_polygons[idx].tolist()]] if raw_polygons is not None and idx < len(raw_polygons) and len(raw_polygons[idx]) > 0 else []
                raw_panels.append(PanelDetection(xyxy=xyxy, polygons=polys, confidence=score, mask=binary))
            elif norm_class in ("balloon", "bubble"):
                if np.count_nonzero(binary) >= 100 or np.count_nonzero(binary) == binary.size:
                    bubbles.append(BubbleDetection(binary, score))

        return suppress_overlapping_bubbles(bubbles), sort_panel_detections_reading_order(raw_panels, rtl=True)


def suppress_overlapping_bubbles(
    bubbles: list[BubbleDetection],
    iou_threshold: float = 0.5,
    containment_threshold: float = 0.7,
) -> list[BubbleDetection]:
    """Suppress redundant duplicate or false-merge bubble detections with lower confidence."""
    if len(bubbles) <= 1:
        return bubbles
    sorted_bubbles = sorted(bubbles, key=lambda b: b.confidence, reverse=True)
    kept: list[BubbleDetection] = []
    for cand in sorted_bubbles:
        cand_mask = cand.mask > 0
        cand_area = int(np.count_nonzero(cand_mask))
        if cand_area < 100:
            continue
        suppress = False
        for accepted in kept:
            acc_mask = accepted.mask > 0
            acc_area = int(np.count_nonzero(acc_mask))
            inter = int(np.count_nonzero(cand_mask & acc_mask))
            if inter == 0:
                continue
            union = cand_area + acc_area - inter
            iou = inter / union if union > 0 else 0.0
            inter_over_cand = inter / cand_area
            inter_over_acc = inter / acc_area
            if iou >= iou_threshold or inter_over_cand >= containment_threshold or inter_over_acc >= containment_threshold:
                suppress = True
                break
        if not suppress:
            kept.append(cand)
    return kept


def get_detector(model: str, confidence: float = 0.25, mask_threshold: float = 0.5, image_size: int = 512, device: str = "cpu") -> BubbleDetector:
    key = (model, device, confidence, mask_threshold, image_size)

    def create_detector():
        started = perf_counter()
        detector = BubbleDetector(model, confidence, mask_threshold, image_size, device)
        logger.info("Loaded bubble detector %s in %.0fms", model, (perf_counter() - started) * 1000)
        return detector

    return get_cached_model('bubble_detector', _bubble_cache, key, create_detector)


def detect(image: np.ndarray, config, device: str = "cpu") -> list[BubbleDetection]:
    target_device = device if device and device != "auto" else getattr(config, "device", "cpu")
    return get_detector(config.model, config.confidence, config.mask_threshold, config.image_size, target_device)(image)


def detect_joint(image: np.ndarray, config, device: str = "cpu") -> tuple[list[BubbleDetection], list[PanelDetection]]:
    target_device = device if device and device != "auto" else getattr(config, "device", "cpu")
    return get_detector(config.model, config.confidence, config.mask_threshold, config.image_size, target_device).detect_joint(image)


@model_operation
async def prepare(config, device: str = "cpu"):
    target_device = device if device and device != "auto" else getattr(config, "device", "cpu")
    get_detector(config.model, config.confidence, config.mask_threshold, config.image_size, target_device)


@model_operation
async def dispatch(image: np.ndarray, config, device: str = "cpu") -> list[BubbleDetection]:
    return detect(image, config, device=device)


@model_operation
async def dispatch_joint(image: np.ndarray, config, device: str = "cpu") -> tuple[list[BubbleDetection], list[PanelDetection]]:
    return detect_joint(image, config, device=device)


@model_operation
async def dispatch_batch(images: list[np.ndarray], config, device: str = "cpu") -> list[list[BubbleDetection]]:
    if not images:
        return []
    target_device = device if device and device != "auto" else getattr(config, "device", "cpu")
    return get_detector(config.model, config.confidence, config.mask_threshold, config.image_size, target_device).detect_batch(images)


@model_operation
async def dispatch_batch_joint(images: list[np.ndarray], config, device: str = "cpu") -> list[tuple[list[BubbleDetection], list[PanelDetection]]]:
    if not images:
        return []
    target_device = device if device and device != "auto" else getattr(config, "device", "cpu")
    return get_detector(config.model, config.confidence, config.mask_threshold, config.image_size, target_device).detect_batch_joint(images)


@model_operation
async def unload(key=None):
    if key is None:
        clear_model_cache('bubble_detector', _bubble_cache)
    else:
        await unload_cached_model('bubble_detector', _bubble_cache, key)


def serialize_bubble_detections(bubble_detections: list[Any], lobe_graphs: list[Any] | None = None) -> list[dict[str, Any]]:
    return _serialize_bubbles(bubble_detections, lobe_graphs=lobe_graphs)


def deserialize_bubble_detections(data: list[dict[str, Any]], image_shape: tuple[int, ...]) -> list[BubbleDetection]:
    return _deserialize_bubbles(data, image_shape, BubbleDetection)
