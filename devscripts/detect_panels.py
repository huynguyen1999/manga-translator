#!/usr/bin/env python3
"""Detect and segment manga/comic panels, text, and balloons using YOLO models.

Supported models:
  1. `shadowb`: ShadowB/Manga109-panel-balloon-text-yolov26-segmentation (Instance Segmentation: panel, text, balloon)
  2. `ashu` / `manga-segment`: Ashu11-A/Manga-Segment (YOLOv11s Instance Segmentation: panel/comic, speech-balloon)
  3. `leoxs22`: leoxs22/manga-panel-detector-yolo26n (Object Detection: panel, text)
  4. `jebin2` / `mosesb`: mosesb/best-comic-panel-detection (Object Detection: Comic Panel)

Features:
  - Automatic download from Hugging Face Hub / GitHub Releases if weights are not present locally.
  - Full Batch Inference (`--batch-size N`): process multiple pages simultaneously through the GPU/accelerator.
  - Clear visualization with high-contrast color badges displaying:
      * Exact detected object type (panel/frame, speech bubble/balloon, text region).
      * Reading order sequence (#1, #2, ... for manga panels).
      * Confidence score percentage (e.g. `96%` or `0.96`).
      * Translucent masks & crisp bounding box outlines on the page.
      * Side legend explaining object types and colors.
  - Reading-order sorting for panels (RTL default for manga, LTR optional for Western comics).
  - High-res segmentation polygon mask extraction (`--retina-masks`).
  - Panel cropping (`--crop-panels`) to save individual panels as images.
  - Export structured JSON metadata with bounding boxes, segmentation polygons, reading order, and confidence scores.

Usage examples:
    # 1. Run Ashu11-A/Manga-Segment model
    python devscripts/detect_panels.py --model ashu --input devscripts/input/img1.png --crop-panels

    # 2. Run ShadowB segmentation model with batch inference (e.g. batch size 4)
    python devscripts/detect_panels.py --model shadowb --input devscripts/input --batch-size 4 --crop-panels

    # 3. Run jebin2 / mosesb comic panel detector
    python devscripts/detect_panels.py --model jebin2 --input devscripts/input/img1.png

    # 4. Run Leoxs22 detector (lightweight YOLO26n)
    python devscripts/detect_panels.py --model leoxs22 --input devscripts/input/img1.png
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path
from time import perf_counter
from typing import Any

import cv2
import numpy as np

# Patch compatibility aliases across Ultralytics module variations
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_ROOT / "models/panels"

IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}

MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "shadowb": {
        "source_type": "huggingface",
        "repo_id": "ShadowB/Manga109-panel-balloon-text-yolov26-segmentation",
        "filename": "best.pt",
        "local_subpath": "shadowb_yolo26s_seg/best.pt",
        "default_imgsz": 1280,
        "is_segmentation": True,
        "description": "ShadowB YOLO26s Instance Segmentation (Classes: panel/frame, text, balloon)",
    },
    "ashu": {
        "source_type": "github_release_tar",
        "url": "https://github.com/Ashu11-A/Manga-Segment/releases/download/v0.2.0/yolov11s_model.tar.xz",
        "tar_target_path": "weights/best.pt",
        "local_subpath": "ashu_manga_segment/weights/best.pt",
        "default_imgsz": 1280,
        "is_segmentation": True,
        "description": "Ashu11-A Manga-Segment YOLOv11s Instance Segmentation (Classes: comic/panel, speech-balloon)",
    },
    "manga-segment": {
        "source_type": "github_release_tar",
        "url": "https://github.com/Ashu11-A/Manga-Segment/releases/download/v0.2.0/yolov11s_model.tar.xz",
        "tar_target_path": "weights/best.pt",
        "local_subpath": "ashu_manga_segment/weights/best.pt",
        "default_imgsz": 1280,
        "is_segmentation": True,
        "description": "Ashu11-A Manga-Segment YOLOv11s Instance Segmentation (Classes: comic/panel, speech-balloon)",
    },
    "leoxs22": {
        "source_type": "huggingface",
        "repo_id": "leoxs22/manga-panel-detector-yolo26n",
        "filename": "manga_panel_detector_fp32.pt",
        "local_subpath": "leoxs22/manga_panel_detector_fp32.pt",
        "default_imgsz": 640,
        "is_segmentation": False,
        "description": "Leoxs22 YOLO26-nano Detection (Classes: panel, text)",
    },
    "jebin2": {
        "source_type": "huggingface",
        "repo_id": "mosesb/best-comic-panel-detection",
        "filename": "best.pt",
        "local_subpath": "mosesb_comic/best.pt",
        "default_imgsz": 640,
        "is_segmentation": False,
        "description": "Jebin2 / Mosesb Comic Panel Detection (Classes: Comic Panel)",
    },
    "mosesb": {
        "source_type": "huggingface",
        "repo_id": "mosesb/best-comic-panel-detection",
        "filename": "best.pt",
        "local_subpath": "mosesb_comic/best.pt",
        "default_imgsz": 640,
        "is_segmentation": False,
        "description": "Mosesb Comic Panel Detection (Classes: Comic Panel)",
    },
}

# Visual styling with vibrant distinct colors (BGR)
CLASS_CONFIG: dict[str, dict[str, Any]] = {
    "panel": {
        "display_name": "PANEL",
        "color": (46, 204, 113),       # Emerald Green
        "fill_alpha": 0.12,
        "border_thickness": 3,
    },
    "balloon": {
        "display_name": "BUBBLE",
        "color": (230, 80, 160),       # Magenta / Violet
        "fill_alpha": 0.30,
        "border_thickness": 2,
    },
    "text": {
        "display_name": "TEXT",
        "color": (30, 144, 255),       # Dodger Blue
        "fill_alpha": 0.25,
        "border_thickness": 2,
    },
    "default": {
        "display_name": "OBJECT",
        "color": (241, 196, 15),       # Amber Yellow
        "fill_alpha": 0.20,
        "border_thickness": 2,
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


def get_class_cfg(name: str) -> dict[str, Any]:
    norm = normalize_class_name(name)
    return CLASS_CONFIG.get(norm, CLASS_CONFIG["default"])


def ensure_model_weights(weights_path: Path, preset: dict[str, Any]) -> Path:
    """Ensure the PyTorch model checkpoint is available locally, downloading if needed."""
    if weights_path.is_file():
        return weights_path

    weights_path.parent.mkdir(parents=True, exist_ok=True)
    source_type = preset.get("source_type", "huggingface")

    if source_type == "huggingface":
        repo_id = preset["repo_id"]
        filename = preset["filename"]
        print(f"[*] Downloading model '{filename}' from Hugging Face repo '{repo_id}'...")
        try:
            from huggingface_hub import hf_hub_download
            downloaded = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=weights_path.parent,
            )
            return Path(downloaded)
        except Exception as exc:
            print(f"[!] huggingface_hub download failed: {exc}. Trying direct URL download...", file=sys.stderr)
            url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
            urllib.request.urlretrieve(url, weights_path)
            return weights_path

    elif source_type == "github_release_tar":
        url = preset["url"]
        print(f"[*] Downloading release archive from '{url}'...")
        extract_root = weights_path.parents[1] if weights_path.parent.name == "weights" else weights_path.parent
        extract_root.mkdir(parents=True, exist_ok=True)
        req = urllib.request.urlopen(url)
        buf = io.BytesIO(req.read())
        with tarfile.open(fileobj=buf) as tar:
            tar.extractall(extract_root)
        return weights_path

    raise ValueError(f"Unknown source_type: {source_type}")


def expand_input_paths(inputs: list[Path], output_dir: Path | None = None) -> list[Path]:
    """Expand file and directory inputs into sorted image files."""
    output_dir_resolved = output_dir.resolve() if output_dir else None
    paths: list[Path] = []
    for value in inputs:
        path = value.expanduser()
        if not path.is_dir():
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                paths.append(path)
            elif path.exists():
                print(f"[!] Skipping non-image file: {path}", file=sys.stderr)
            else:
                print(f"[!] Path does not exist: {path}", file=sys.stderr)
            continue
        for candidate in sorted(path.iterdir(), key=lambda item: item.name.lower()):
            if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if output_dir_resolved and candidate.parent.resolve() == output_dir_resolved:
                continue
            paths.append(candidate)
    if not paths:
        raise ValueError("No valid image files found in the supplied input path(s).")
    return paths


def sort_panels_reading_order(panels: list[dict[str, Any]], rtl: bool = True) -> list[dict[str, Any]]:
    """Sort panels in standard reading order (RTL default for Manga, LTR for Western Comics)."""
    if not panels:
        return []

    # Sort with a vertical tolerance band (e.g. 80px) to group rows
    def sort_key(item: dict[str, Any]):
        x1, y1, x2, y2 = item["bbox"]
        yc = (y1 + y2) / 2.0
        xc = (x1 + x2) / 2.0
        band = round(yc / 80.0)
        x_val = -xc if rtl else xc
        return (band, x_val)

    sorted_panels = sorted(panels, key=sort_key)
    for idx, panel in enumerate(sorted_panels, start=1):
        panel["reading_order"] = idx
    return sorted_panels


def draw_detections(
    image: np.ndarray,
    detections: list[dict[str, Any]],
    show_labels: bool = True,
    show_legend: bool = True,
) -> np.ndarray:
    """Draw bounding boxes, segmentation masks, labels, confidence badges, and legend."""
    vis = image.copy()
    overlay = image.copy()
    img_h, img_w = image.shape[:2]

    # Draw masks on overlay (panels first, then bubbles, then text)
    def draw_order(d: dict[str, Any]) -> int:
        c = normalize_class_name(d["class_name"])
        if c == "panel":
            return 0
        if c == "balloon":
            return 1
        return 2

    sorted_for_drawing = sorted(detections, key=draw_order)

    # 1. Fill translucent regions
    for item in sorted_for_drawing:
        cfg = get_class_cfg(item["class_name"])
        color = cfg["color"]
        polygon = item.get("polygon")

        if polygon and len(polygon) >= 3:
            pts = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(overlay, [pts], color)
        else:
            x1, y1, x2, y2 = [int(v) for v in item["bbox"]]
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)

    # Blend translucent fill with image (using 0.22 opacity)
    vis = cv2.addWeighted(overlay, 0.22, vis, 0.78, 0)

    # 2. Draw crisp outlines and labels
    for item in sorted_for_drawing:
        x1, y1, x2, y2 = [int(v) for v in item["bbox"]]
        norm_class = normalize_class_name(item["class_name"])
        cfg = get_class_cfg(norm_class)
        color = cfg["color"]
        display_label = cfg["display_name"]
        conf = item["confidence"]
        polygon = item.get("polygon")

        # Crisp border outline
        thickness = cfg["border_thickness"]
        if polygon and len(polygon) >= 3:
            pts = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(vis, [pts], isClosed=True, color=color, thickness=thickness, lineType=cv2.LINE_AA)

        # Panel box outline
        if norm_class == "panel":
            cv2.rectangle(vis, (x1, y1), (x2, y2), color, thickness=thickness, lineType=cv2.LINE_AA)
        elif not polygon:
            cv2.rectangle(vis, (x1, y1), (x2, y2), color, thickness=thickness, lineType=cv2.LINE_AA)

        if show_labels:
            order_tag = f"#{item['reading_order']} " if "reading_order" in item else ""
            caption = f"{order_tag}{display_label} {conf * 100:.0f}%"

            font = cv2.FONT_HERSHEY_DUPLEX
            scale = 0.52 if norm_class == "panel" else 0.44
            text_thickness = 1

            (w, h), baseline = cv2.getTextSize(caption, font, scale, text_thickness)

            pad_x = 6
            pad_y = 4
            badge_h = h + (pad_y * 2)
            badge_w = w + (pad_x * 2)

            bx1 = x1
            if norm_class == "panel":
                by1 = y1
            else:
                by1 = max(0, y1 - badge_h)

            by2 = min(img_h, by1 + badge_h)
            bx2 = min(img_w, bx1 + badge_w)

            cv2.rectangle(vis, (bx1, by1), (bx2, by2), (20, 20, 20), -1)
            cv2.rectangle(vis, (bx1, by1), (bx2, by2), color, 1, lineType=cv2.LINE_AA)

            text_y = by1 + pad_y + h
            cv2.putText(
                vis,
                caption,
                (bx1 + pad_x, text_y),
                font,
                scale,
                color,
                text_thickness,
                lineType=cv2.LINE_AA,
            )

    # 3. Draw Legend Overlay on Top-Right Corner
    if show_legend:
        present_classes = {normalize_class_name(d["class_name"]) for d in detections}
        legend_items = []
        for cname in ["panel", "balloon", "text"]:
            if cname in present_classes:
                cnt = sum(1 for d in detections if normalize_class_name(d["class_name"]) == cname)
                cfg = get_class_cfg(cname)
                legend_items.append((cfg["display_name"], cfg["color"], cnt))

        if legend_items:
            lw = 180
            lh = 28 + (len(legend_items) * 22)
            lx1 = max(10, img_w - lw - 15)
            ly1 = 15
            lx2 = lx1 + lw
            ly2 = ly1 + lh

            card_overlay = vis.copy()
            cv2.rectangle(card_overlay, (lx1, ly1), (lx2, ly2), (15, 15, 15), -1)
            cv2.rectangle(card_overlay, (lx1, ly1), (lx2, ly2), (80, 80, 80), 1)
            vis = cv2.addWeighted(card_overlay, 0.85, vis, 0.15, 0)

            cv2.putText(
                vis,
                "DETECTIONS",
                (lx1 + 10, ly1 + 18),
                cv2.FONT_HERSHEY_DUPLEX,
                0.45,
                (220, 220, 220),
                1,
                lineType=cv2.LINE_AA,
            )

            for idx, (dname, col, cnt) in enumerate(legend_items):
                row_y = ly1 + 38 + (idx * 20)
                cv2.rectangle(vis, (lx1 + 10, row_y - 10), (lx1 + 22, row_y + 2), col, -1)
                cv2.rectangle(vis, (lx1 + 10, row_y - 10), (lx1 + 22, row_y + 2), (255, 255, 255), 1)
                row_text = f"{dname}: {cnt}"
                cv2.putText(
                    vis,
                    row_text,
                    (lx1 + 28, row_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    (255, 255, 255),
                    1,
                    lineType=cv2.LINE_AA,
                )

    return vis


def crop_panels(
    image: np.ndarray,
    panels: list[dict[str, Any]],
    output_dir: Path,
    stem: str,
) -> list[Path]:
    """Crop detected panels and save them as individual image files."""
    crop_paths = []
    crops_dir = output_dir / f"{stem}_panels"
    crops_dir.mkdir(parents=True, exist_ok=True)

    h, w = image.shape[:2]
    for idx, panel in enumerate(panels, start=1):
        order = panel.get("reading_order", idx)
        x1, y1, x2, y2 = [int(v) for v in panel["bbox"]]
        x1 = max(0, min(w - 1, x1))
        y1 = max(0, min(h - 1, y1))
        x2 = max(x1 + 1, min(w, x2))
        y2 = max(y1 + 1, min(h, y2))

        crop = image[y1:y2, x1:x2]
        crop_name = f"panel_{order:02d}.png"
        crop_path = crops_dir / crop_name
        cv2.imwrite(str(crop_path), crop)
        crop_paths.append(crop_path)

    return crop_paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect and segment manga/comic panels, text, and balloons using YOLO models.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        nargs="+",
        required=True,
        help="Input image file(s) or directory",
    )
    parser.add_argument(
        "--model",
        "-m",
        choices=list(MODEL_PRESETS.keys()),
        default="shadowb",
        help="Model preset: 'shadowb' (Manga109 segmentation), 'ashu'/'manga-segment' (Ashu11-A YOLOv11s segmentation), 'leoxs22' (YOLO26n detection), or 'jebin2'/'mosesb' (Comic panel detector)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=PROJECT_ROOT / "outputs/panel_detection",
        help="Output directory to save detection visualizations and JSON results",
    )
    parser.add_argument(
        "--weights",
        "-w",
        type=Path,
        default=None,
        help="Path to custom model weights file (.pt). If not provided, downloads default for preset.",
    )
    parser.add_argument(
        "--batch-size",
        "-b",
        type=int,
        default=1,
        help="Number of images to process concurrently in a single batch inference call",
    )
    parser.add_argument(
        "--conf",
        "-c",
        type=float,
        default=0.25,
        help="Confidence threshold for detection/segmentation",
    )
    parser.add_argument(
        "--iou",
        type=float,
        default=0.7,
        help="IoU threshold for NMS",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=None,
        help="Inference image size (defaults to 1280 for shadowb/ashu, 640 for leoxs22/jebin2)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="",
        help="Inference device: 'cpu', 'cuda', 'mps', or empty for auto-detection",
    )
    parser.add_argument(
        "--crop-panels",
        action="store_true",
        help="Extract and save cropped images for each detected panel",
    )
    parser.add_argument(
        "--panels-only",
        action="store_true",
        help="Only output and visualize panel boxes (ignore balloons and text)",
    )
    parser.add_argument(
        "--ltr",
        action="store_true",
        help="Sort panels Left-to-Right (Western comics) instead of Right-to-Left (Manga)",
    )
    parser.add_argument(
        "--no-viz",
        action="store_true",
        help="Skip saving rendered visual images (save JSON only)",
    )
    parser.add_argument(
        "--no-legend",
        action="store_true",
        help="Skip drawing detection legend in the visualization image",
    )

    args = parser.parse_args()

    # 1. Resolve preset & weights
    preset = MODEL_PRESETS[args.model]
    imgsz = args.imgsz if args.imgsz is not None else preset["default_imgsz"]
    weights_path = args.weights or (MODEL_DIR / preset["local_subpath"])
    weights_path = ensure_model_weights(weights_path, preset)

    # 2. Expand input paths
    try:
        image_paths = expand_input_paths(args.input, output_dir=args.output_dir)
    except ValueError as err:
        print(f"[!] Error: {err}", file=sys.stderr)
        return 1

    # 3. Load YOLO model
    print(f"[*] Loading [{args.model}] model from {weights_path}...")
    try:
        from ultralytics import YOLO
        model = YOLO(str(weights_path))
    except Exception as exc:
        print(f"[!] Failed to load model with ultralytics: {exc}", file=sys.stderr)
        return 1

    # Class name mapping
    raw_names = getattr(model, "names", {0: "frame", 1: "text", 2: "balloon"})
    class_map = {int(k): normalize_class_name(v) for k, v in raw_names.items()}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    batch_size = max(1, args.batch_size)
    print(f"[*] Model: {preset['description']}")
    print(f"[*] Processing {len(image_paths)} image(s) (batch_size={batch_size}, conf={args.conf}, imgsz={imgsz})...\n")

    total_time = 0.0
    total_panels = 0
    total_balloons = 0
    total_text = 0

    # Chunk image paths into batches
    for b_idx in range(0, len(image_paths), batch_size):
        batch_paths = image_paths[b_idx : b_idx + batch_size]
        batch_str_sources = [str(p) for p in batch_paths]

        t0 = perf_counter()
        predict_kwargs: dict[str, Any] = {
            "source": batch_str_sources,
            "conf": args.conf,
            "iou": args.iou,
            "imgsz": imgsz,
            "batch": len(batch_paths),
            "verbose": False,
        }
        if preset["is_segmentation"]:
            predict_kwargs["retina_masks"] = True
        if args.device:
            predict_kwargs["device"] = args.device

        results = model.predict(**predict_kwargs)
        elapsed = perf_counter() - t0
        total_time += elapsed

        for item_idx, (img_path, result) in enumerate(zip(batch_paths, results), start=1):
            global_idx = b_idx + item_idx
            image = cv2.imread(str(img_path))
            if image is None:
                print(f"  [!] Failed to read image: {img_path}", file=sys.stderr)
                continue

            detections: list[dict[str, Any]] = []
            has_masks = result.masks is not None and len(result.masks) > 0
            raw_polygons = result.masks.xy if has_masks else None

            for box_idx, box in enumerate(result.boxes):
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                xyxy = box.xyxy[0].tolist()
                norm_name = class_map.get(cls_id, f"class_{cls_id}")

                if args.panels_only and norm_name != "panel":
                    continue

                det_entry: dict[str, Any] = {
                    "class_id": cls_id,
                    "class_name": norm_name,
                    "confidence": round(conf, 4),
                    "bbox": [round(coord, 2) for coord in xyxy],
                }

                if raw_polygons is not None and box_idx < len(raw_polygons):
                    poly_arr = raw_polygons[box_idx]
                    if len(poly_arr) > 0:
                        det_entry["polygon"] = [[round(pt[0], 1), round(pt[1], 1)] for pt in poly_arr.tolist()]

                detections.append(det_entry)

            # Separate items and sort panels in reading order
            panels = [d for d in detections if d["class_name"] == "panel"]
            balloons = [d for d in detections if d["class_name"] == "balloon"]
            texts = [d for d in detections if d["class_name"] == "text"]
            others = [d for d in detections if d["class_name"] not in ("panel", "balloon", "text")]

            panels = sort_panels_reading_order(panels, rtl=not args.ltr)
            ordered_detections = panels + balloons + texts + others

            total_panels += len(panels)
            total_balloons += len(balloons)
            total_text += len(texts)

            summary_parts = [f"{len(panels)} panel(s)"]
            if balloons:
                summary_parts.append(f"{len(balloons)} balloon(s)")
            if texts:
                summary_parts.append(f"{len(texts)} text region(s)")
            per_item_ms = (elapsed / len(batch_paths)) * 1000
            print(f"[{global_idx}/{len(image_paths)}] {img_path.name} -> {', '.join(summary_parts)} ({per_item_ms:.1f} ms/page)")

            # Save JSON output
            out_json_path = args.output_dir / f"{img_path.stem}_detections.json"
            metadata = {
                "image_path": str(img_path.resolve()),
                "image_size": [image.shape[1], image.shape[0]],  # [width, height]
                "model": args.model,
                "is_segmentation": preset["is_segmentation"],
                "num_panels": len(panels),
                "num_balloons": len(balloons),
                "num_texts": len(texts),
                "inference_time_ms": round(per_item_ms, 2),
                "panels": panels,
                "balloons": balloons,
                "texts": texts,
                "detections": ordered_detections,
            }
            with open(out_json_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)

            # Save visualization
            if not args.no_viz:
                vis_img = draw_detections(image, ordered_detections, show_legend=not args.no_legend)
                out_vis_path = args.output_dir / f"{img_path.stem}_annotated.png"
                cv2.imwrite(str(out_vis_path), vis_img)

            # Optionally crop panels
            if args.crop_panels and panels:
                crop_paths = crop_panels(image, panels, args.output_dir, img_path.stem)
                print(f"  -> Cropped {len(crop_paths)} panel(s) to: {args.output_dir / f'{img_path.stem}_panels'}")

    print("\n" + "=" * 50)
    print(f"Done! Processed {len(image_paths)} images in {total_time:.2f}s (avg {total_time / max(1, len(image_paths)) * 1000:.1f}ms/page)")
    stats = [f"{total_panels} panels"]
    if total_balloons > 0:
        stats.append(f"{total_balloons} balloons")
    if total_text > 0:
        stats.append(f"{total_text} text regions")
    print(f"Total Detections: {', '.join(stats)}")
    print(f"Outputs saved to: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
