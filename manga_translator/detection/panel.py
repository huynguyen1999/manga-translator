"""Panel detection data models, checkpoint management, and serialization.

Supports multi-class manga segmentation models (e.g. ShadowB Manga109 YOLO26/YOLO11)
detecting panels/frames, speech bubbles/balloons, and text regions.
"""
from __future__ import annotations

import logging
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Ultralytics serialization shims for custom head/loss layers in YOLO26 models
try:
    import ultralytics.utils.loss as _loss_mod
    if not hasattr(_loss_mod, "E2ELoss") and hasattr(_loss_mod, "E2EDetectLoss"):
        _loss_mod.E2ELoss = _loss_mod.E2EDetectLoss
except Exception:
    pass

try:
    import ultralytics.nn.modules.head as _head_mod
    if not hasattr(_head_mod, "Segment26") and hasattr(_head_mod, "Segment"):
        _head_mod.Segment26 = _head_mod.Segment
except Exception:
    pass

try:
    import ultralytics.nn.modules.block as _block_mod
    if not hasattr(_block_mod, "Proto26") and hasattr(_block_mod, "Proto"):
        _block_mod.Proto26 = _block_mod.Proto
except Exception:
    pass


@dataclass(frozen=True)
class PanelDetection:
    xyxy: list[int]
    polygons: list[list[list[int]]]
    confidence: float
    order_index: int = 0
    mask: Optional[np.ndarray] = None


MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "shadowb": {
        "repo_id": "ShadowB/Manga109-panel-balloon-text-yolov26-segmentation",
        "filename": "best.pt",
        "local_subpath": "shadowb_manga109/best.pt",
    },
    "shadowb_manga109": {
        "repo_id": "ShadowB/Manga109-panel-balloon-text-yolov26-segmentation",
        "filename": "best.pt",
        "local_subpath": "shadowb_manga109/best.pt",
    },
    "manga109_yolov26": {
        "repo_id": "ShadowB/Manga109-panel-balloon-text-yolov26-segmentation",
        "filename": "best.pt",
        "local_subpath": "shadowb_manga109/best.pt",
    },
}


def normalize_class_name(name: str) -> str:
    lower = str(name).lower().strip()
    if lower in ("frame", "panel", "comic", "comic panel", "comic_panel", "panels", "comics"):
        return "panel"
    if lower in ("bubble", "balloon", "bubbles", "balloons", "speech-balloon", "speech_balloon", "thought-balloon", "thought_balloon"):
        return "balloon"
    if lower in ("text", "texts"):
        return "text"
    return lower


def resolve_model_checkpoint(model: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "models" / "bubbles"
    canonical_key = model.lower().strip().replace(" ", "-").replace("_", "-")
    shadowb_keys = (
        "shadowb",
        "shadowb-manga109",
        "manga109-multiclass",
        "yolo26-manga109",
        "manga109-yolo26",
        "manga109-yolov26",
        "manga109-panel-balloon-text-yolov26-segmentation",
        "manga109-panel-balloon-text-yolov26",
        "shadowb/manga109-panel-balloon-text-yolov26-segmentation",
        "yolov8m",
        "yolov8",
        "manga109",
        "yolo11-manga-seg",
    )
    if canonical_key in shadowb_keys or model.lower().strip() in MODEL_PRESETS:
        preset = MODEL_PRESETS["shadowb_manga109"]
    elif canonical_key in MODEL_PRESETS:
        preset = MODEL_PRESETS[canonical_key]
    else:
        direct = Path(model)
        if direct.is_file():
            return direct
        target = root / model
        if target.is_file():
            return target
        raise ValueError(f"Unknown bubble/panel detection model: {model!r}")

    target = root / preset["local_subpath"]
    if target.is_file():
        return target

    repo_id, filename = preset.get("repo_id"), preset.get("filename")
    if not repo_id:
        raise FileNotFoundError(f"Model file not found at {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download
        downloaded = Path(hf_hub_download(repo_id=repo_id, filename=filename, local_dir=str(target.parent)))
        if downloaded != target and downloaded.is_file():
            shutil.copy2(downloaded, target)
    except Exception as exc:
        logger.warning("huggingface_hub download failed: %s. Trying direct URL fallback...", exc)
        urllib.request.urlretrieve(f"https://huggingface.co/{repo_id}/resolve/main/{filename}", target)

    if not target.is_file():
        raise RuntimeError(f"Failed to obtain model checkpoint at {target}")
    return target


def sort_panel_detections_reading_order(
    panels: list[PanelDetection],
    rtl: bool = True,
    row_tolerance_px: float = 80.0,
) -> list[PanelDetection]:
    """Sort panels into natural reading order (RTL for Manga, LTR for Western Comics)."""
    if not panels:
        return []

    def sort_key(panel: PanelDetection):
        x1, y1, x2, y2 = panel.xyxy
        yc, xc = (y1 + y2) / 2.0, (x1 + x2) / 2.0
        return (round(yc / max(10.0, row_tolerance_px)), -xc if rtl else xc)

    return [
        PanelDetection(
            xyxy=panel.xyxy,
            polygons=panel.polygons,
            confidence=panel.confidence,
            order_index=idx + 1,
            mask=panel.mask,
        )
        for idx, panel in enumerate(sorted(panels, key=sort_key))
    ]


def serialize_panel_detections(
    panel_detections: list[PanelDetection],
    image_shape: tuple[int, ...] | None = None,
) -> list[dict[str, Any]]:
    """Convert PanelDetection objects into JSON serializable dictionaries."""
    serialized = []
    for idx, panel in enumerate(panel_detections or []):
        x1, y1, x2, y2 = panel.xyxy
        order = panel.order_index if panel.order_index > 0 else (idx + 1)
        entry: dict[str, Any] = {
            "index": idx,
            "order": order,
            "confidence": float(panel.confidence),
            "xyxy": [int(x1), int(y1), int(x2), int(y2)],
            "xywh": [int(x1), int(y1), int(x2 - x1), int(y2 - y1)],
            "area_pixels": int(max(0, x2 - x1) * max(0, y2 - y1)),
            "polygon": panel.polygons[0] if panel.polygons else [],
            "polygons": panel.polygons,
        }
        if image_shape is not None and len(image_shape) >= 2:
            entry["image_size"] = [int(image_shape[1]), int(image_shape[0])]
        serialized.append(entry)
    return serialized


def deserialize_panel_detections(
    data: Any,
    image_shape: tuple[int, ...] | None = None,
) -> list[PanelDetection]:
    """Reconstruct PanelDetection objects from serialized JSON dictionaries."""
    if not data:
        return []
    items = data.get("panels", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    reconstructed = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        conf = float(item.get("confidence", 0.9))
        order = int(item.get("order", item.get("reading_order", idx + 1)))
        xyxy = item.get("xyxy")
        polygons = item.get("polygons") or []
        if not polygons and "polygon" in item and item["polygon"]:
            polygons = [item["polygon"]]
        if not xyxy and polygons and len(polygons[0]) > 0:
            xs = [pt[0] for poly in polygons for pt in poly]
            ys = [pt[1] for poly in polygons for pt in poly]
            if xs and ys:
                xyxy = [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]
        if not xyxy:
            xywh = item.get("xywh", [0, 0, 0, 0])
            xyxy = [int(xywh[0]), int(xywh[1]), int(xywh[0] + xywh[2]), int(xywh[1] + xywh[3])]
        reconstructed.append(
            PanelDetection(
                xyxy=[int(v) for v in xyxy],
                polygons=polygons,
                confidence=conf,
                order_index=order,
            )
        )
    return reconstructed


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
        if mask is not None:
            mask_arr = np.asarray(mask, dtype=np.uint8)
            entry["image_size"] = [int(mask_arr.shape[1]), int(mask_arr.shape[0])]
            if np.any(mask_arr):
                ys, xs = np.where(mask_arr > 0)
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
    bubble_cls: Any,
) -> list[Any]:
    """Reconstruct BubbleDetection objects from serialized JSON dictionaries."""
    reconstructed = []
    for item in data or []:
        conf = float(item.get("confidence", 0.9))
        mask = np.zeros(image_shape[:2], dtype=np.uint8)
        polys = item.get("polygons") or ([item["polygon"]] if "polygon" in item and item["polygon"] else [])
        if polys:
            for poly in polys:
                pts = np.asarray(poly, dtype=np.int32)
                if len(pts) >= 3:
                    cv2.fillPoly(mask, [pts], 255)
        elif "xyxy" in item and item["xyxy"]:
            x1, y1, x2, y2 = item["xyxy"]
            mask[y1:y2, x1:x2] = 255
        reconstructed.append(bubble_cls(mask=mask, confidence=conf))
    return reconstructed
