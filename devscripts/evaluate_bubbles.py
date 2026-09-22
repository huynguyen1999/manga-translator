#!/usr/bin/env python3
"""Compare speech-bubble masks and the current text placement.

The evaluator is deliberately separate from production rendering.  Pass one
or more image paths directly, then run for example::

    python devscripts/evaluate_bubbles.py --input page.png \
        --mode both \
        --output-dir /tmp/bubble-eval

The model is only needed for ``yolo`` and ``both`` modes.  ``--model yolov8m``
uses the local checkpoint; ``--model manga109`` downloads the recommended
``best.pt`` checkpoint from Hugging Face on first use.
Reference polygons or mask files are intentionally supplied by the evaluator
user; the script must not pretend that a thresholded page is ground truth.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_ROOT / "models/bubbles"
DEFAULT_MODEL_NAME = "yolov8m"
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
GENERATED_SUFFIXES = (
    "-bubble-detection.png",
    "-heuristic-render.png",
    "-masks.png",
    "-yolo-render.png",
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from manga_translator.rendering import (  # noqa: E402
    _composite_box_to_image,
    _find_horizontal_placement,
    _horizontal_layout,
    _points_for_rect,
    fg_bg_compare,
    text_render,
)
from manga_translator.rendering.ballon_extractor import extract_ballon_region  # noqa: E402
from manga_translator.utils import TextBlock  # noqa: E402


def _parse_region(value: str) -> list[int]:
    try:
        values = [int(part) for part in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("region must be X,Y,W,H") from error
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        raise argparse.ArgumentTypeError("region must be X,Y,W,H with positive W and H")
    return values


def expand_input_paths(inputs: list[Path], output_dir: Path | None = None) -> list[Path]:
    """Expand directory inputs into sorted image files."""
    output_dir = output_dir.resolve() if output_dir else None
    paths: list[Path] = []
    for value in inputs:
        path = value.expanduser()
        if not path.is_dir():
            paths.append(path)
            continue
        for candidate in sorted(path.iterdir(), key=lambda item: item.name.lower()):
            if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if output_dir == candidate.parent.resolve() and candidate.name.endswith(GENERATED_SUFFIXES):
                continue
            paths.append(candidate)
    if not paths:
        raise ValueError("no image files found in the supplied input path(s)")
    return paths


@dataclass(frozen=True)
class MaskScore:
    iou: float
    boundary_f1: float
    candidate_outside: float


@dataclass(frozen=True)
class ModelSpec:
    name: str
    display_name: str
    local_path: Path
    repo_id: str | None = None
    remote_filename: str | None = None


MODEL_REGISTRY = {
    "yolov8m": ModelSpec(
        name="yolov8m",
        display_name="YOLOv8m speech bubble",
        local_path=Path("yolov8m_seg-speech-bubble.pt"),
    ),
    "manga109": ModelSpec(
        name="manga109",
        display_name="Manga109 YOLO11n speech bubble",
        local_path=Path("manga109/best.pt"),
        repo_id="juithealien/manga109-segmentation-bubble",
        remote_filename="best.pt",
    ),
}


def _download_from_huggingface(spec: ModelSpec, target: Path) -> Path:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as error:  # pragma: no cover - dependency is in requirements.txt
        raise RuntimeError(
            "Hugging Face model downloads require huggingface_hub; install it with "
            "`python -m pip install huggingface_hub`"
        ) from error
    if not spec.repo_id or not spec.remote_filename:
        raise RuntimeError(f"model {spec.name!r} has no Hugging Face download information")
    return Path(hf_hub_download(
        repo_id=spec.repo_id,
        filename=spec.remote_filename,
        local_dir=str(target.parent),
    ))


def resolve_model(model_name: str) -> tuple[ModelSpec, Path, float | None]:
    """Resolve a named checkpoint, downloading a missing remote model once."""
    spec = MODEL_REGISTRY.get(model_name)
    if spec is None:
        choices = ", ".join(MODEL_REGISTRY)
        raise ValueError(f"unknown model {model_name!r}; choose one of: {choices}")
    target = MODEL_DIR / spec.local_path
    if target.is_file():
        return spec, target, None
    if not spec.repo_id:
        raise FileNotFoundError(f"model {model_name!r} was not found at {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    download_start = perf_counter()
    try:
        downloaded = _download_from_huggingface(spec, target)
        if not target.is_file() and downloaded.is_file() and downloaded != target:
            shutil.copy2(downloaded, target)
    except Exception as error:
        raise RuntimeError(
            f"could not download model {model_name!r} from "
            f"{spec.repo_id}/{spec.remote_filename}: {error}"
        ) from error
    if not target.is_file():
        raise RuntimeError(f"model download completed but checkpoint was not found at {target}")
    return spec, target, round((perf_counter() - download_start) * 1000, 2)


def mask_iou(candidate: np.ndarray, reference: np.ndarray) -> float:
    """Return intersection-over-union for two boolean masks."""
    candidate = candidate.astype(bool)
    reference = reference.astype(bool)
    union = np.count_nonzero(candidate | reference)
    return float(np.count_nonzero(candidate & reference) / union) if union else 1.0


def boundary_f1(candidate: np.ndarray, reference: np.ndarray, tolerance: int = 2) -> float:
    """Compare boundaries after allowing a small pixel tolerance."""
    candidate = candidate.astype(np.uint8)
    reference = reference.astype(np.uint8)
    candidate_edge = cv2.morphologyEx(candidate, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    reference_edge = cv2.morphologyEx(reference, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    if not candidate_edge.any() and not reference_edge.any():
        return 1.0
    kernel = np.ones((2 * tolerance + 1, 2 * tolerance + 1), np.uint8)
    candidate_near_reference = cv2.dilate(reference_edge, kernel) & candidate_edge
    reference_near_candidate = cv2.dilate(candidate_edge, kernel) & reference_edge
    precision = np.count_nonzero(candidate_near_reference) / max(1, np.count_nonzero(candidate_edge))
    recall = np.count_nonzero(reference_near_candidate) / max(1, np.count_nonzero(reference_edge))
    return float(2 * precision * recall / (precision + recall)) if precision + recall else 0.0


def candidate_outside(candidate: np.ndarray, reference: np.ndarray) -> float:
    """Return the fraction of candidate pixels outside the reference."""
    candidate = candidate.astype(bool)
    outside = np.count_nonzero(candidate & ~reference.astype(bool))
    return float(outside / max(1, np.count_nonzero(candidate)))


def score_mask(candidate: np.ndarray, reference: np.ndarray) -> MaskScore:
    return MaskScore(mask_iou(candidate, reference), boundary_f1(candidate, reference), candidate_outside(candidate, reference))


class YoloSpeechBubble:
    """Run the repository's YOLO segmentation checkpoint through PyTorch."""

    def __init__(
        self,
        model_path: Path,
        device: str = "auto",
        confidence: float = 0.25,
        mask_threshold: float = 0.5,
        image_size: int = 512,
        retina_masks: bool = False,
        model_name: str | None = None,
    ):
        if model_path.suffix.lower() != ".pt":
            raise ValueError("YOLO checkpoint must be a PyTorch .pt file; ONNX is not supported")
        try:
            import torch
            from ultralytics import YOLO
        except ImportError as error:  # pragma: no cover - depends on the user's environment
            raise RuntimeError(
                "YOLO mode requires ultralytics; install it with `python -m pip install ultralytics`"
            ) from error
        if device == "auto":
            if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        if device == "mps" and not torch.backends.mps.is_available():
            raise RuntimeError("MPS was requested but torch.backends.mps.is_available() is false")
        if image_size < 32:
            raise ValueError("YOLO image_size must be at least 32")
        self.device = device
        self.model = YOLO(str(model_path))
        self.confidence = confidence
        self.mask_threshold = mask_threshold
        self.image_size = image_size
        self.retina_masks = retina_masks
        self.model_name = model_name or model_path.stem
        self.model_path = model_path
        self.runtime_device = None

    def __call__(self, image: np.ndarray) -> np.ndarray:
        result = self.model.predict(
            source=cv2.cvtColor(image, cv2.COLOR_BGR2RGB),
            device=self.device,
            conf=self.confidence,
            imgsz=self.image_size,
            retina_masks=self.retina_masks,
            verbose=False,
        )[0]
        tensor = result.masks.data if result.masks is not None else result.boxes.data
        predictor = getattr(self.model, "predictor", None)
        self.runtime_device = str(getattr(predictor, "device", tensor.device))
        output = np.zeros(image.shape[:2], np.uint8)
        if result.masks is None:
            return output
        for mask, confidence in zip(result.masks.data, result.boxes.conf):
            if float(confidence) < self.confidence:
                continue
            mask = mask.detach().float().cpu().numpy()
            if mask.shape != image.shape[:2]:
                mask = cv2.resize(mask, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)
            output[mask >= self.mask_threshold] = 255
        return output


def heuristic_mask(image: np.ndarray, regions: list[dict[str, Any]]) -> np.ndarray:
    if not regions:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Page-wide fallback: thicken dark outlines, then keep enclosed light
        # components. It is intentionally conservative around open/irregular
        # bubbles; supplying OCR seed regions uses the existing extractor.
        barrier = cv2.dilate((gray < 180).astype(np.uint8), np.ones((5, 5), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats((barrier == 0).astype(np.uint8), connectivity=4)
        output = np.zeros(gray.shape, np.uint8)
        for label in range(1, count):
            x, y, width, height, area = stats[label]
            if area >= 100 and x > 0 and y > 0 and x + width < gray.shape[1] and y + height < gray.shape[0]:
                output[labels == label] = 255
        return output
    output = np.zeros(image.shape[:2], np.uint8)
    for region in regions:
        x, y, width, height = map(int, region["box"])
        if width <= 0 or height <= 0:
            continue
        mask, window = extract_ballon_region(image, [x, y, width, height], enlarge_ratio=1.8)
        x1, y1, x2, y2 = window
        output[y1:y2, x1:x2] = np.maximum(output[y1:y2, x1:x2], mask)
    return output


def reference_mask(case: dict[str, Any], image_shape: tuple[int, int], root: Path) -> np.ndarray | None:
    reference = case.get("reference")
    if not reference:
        return None
    if isinstance(reference, str):
        reference = {"mask": reference}
    output = np.zeros(image_shape, np.uint8)
    if reference.get("mask"):
        path = (root / reference["mask"]).resolve()
        loaded = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if loaded is None:
            raise ValueError(f"reference mask does not exist: {path}")
        output = cv2.resize(loaded, (image_shape[1], image_shape[0]), interpolation=cv2.INTER_NEAREST)
        output = np.where(output > 0, 255, 0).astype(np.uint8)
    polygons = reference.get("polygons", reference.get("polygon", []))
    if polygons and isinstance(polygons[0][0], (int, float)):
        polygons = [polygons]
    for polygon in polygons:
        cv2.fillPoly(output, [np.asarray(polygon, np.int32)], 255)
    return output


def overlay(image: np.ndarray, masks: list[tuple[str, np.ndarray, tuple[int, int, int]]]) -> np.ndarray:
    result = image.copy()
    for _, mask, color in masks:
        active = mask > 0
        result[active] = (result[active].astype(np.float32) * 0.55 + np.asarray(color) * 0.45).astype(np.uint8)
    if masks:
        legend_height = 8 + 22 * len(masks)
        result[:legend_height, :220] = (result[:legend_height, :220].astype(np.float32) * 0.35).astype(np.uint8)
        for index, (label, _, color) in enumerate(masks):
            y = 20 + index * 22
            cv2.rectangle(result, (8, y - 11), (22, y + 3), color, -1)
            cv2.putText(result, label, (30, y), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
    return result


def _regions_for_render(case: dict[str, Any]) -> list[TextBlock]:
    result = []
    for item in case.get("regions", []):
        if not item.get("translation"):
            continue
        x, y, width, height = map(int, item["box"])
        result.append(TextBlock(
            [[[x, y], [x + width, y], [x + width, y + height], [x, y + height]]],
            texts=[item.get("source", "source")], translation=item.get("translation", "Test sentence."),
            font_size=int(item.get("font_size", 20)), target_lang=item.get("target_lang", "ENG"),
            fg_color=(0, 0, 0), bg_color=(255, 255, 255),
        ))
    return result


def render_candidate(image: np.ndarray, mask: np.ndarray, case: dict[str, Any], font_path: str, minimum: int) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Render each passage with the existing bounded placement against a candidate mask."""
    if not case.get("regions"):
        return image.copy(), []
    text_render.set_font(font_path)
    _horizontal_layout.cache_clear()
    output = image.copy()
    # Make the candidate's claimed interior readable without requiring an
    # inpainted page. This is a diagnostic visualization, not a production
    # eraser.
    output[mask > 0] = 255
    obstacles: list[list[int]] = []
    placements = []
    for region in _regions_for_render(case):
        region._bubble_interior = mask
        placement = _find_horizontal_placement(
            region, list(map(int, region.xyxy)), image.shape, region.font_size, minimum,
            region.translation, True, 0, obstacles, is_bubble=True,
        )
        if placement is None:
            placements.append({"fit": False, "box": None, "translation": region.translation})
            continue
        region.font_size, rect = placement
        fg, bg = fg_bg_compare(*region.get_font_colors())
        box = text_render.put_text_horizontal(
            region.font_size, region.translation, rect[2] - rect[0], rect[3] - rect[1],
            region.alignment, region.direction == "hr", fg, bg, region.target_lang, True, 0,
            font_size_minimum=minimum,
        )
        if box is None:
            placements.append({"fit": False, "box": None, "translation": region.translation})
            continue
        points = _points_for_rect(region, rect, image.shape[1], image.shape[0])
        output = _composite_box_to_image(output, box, points)
        obstacles.append(rect)
        placements.append({"fit": True, "box": rect, "font_size": region.font_size, "translation": region.translation})
    return output, placements


def placement_summary(placements: list[dict[str, Any]]) -> dict[str, int]:
    fitted = [item for item in placements if item.get("fit") and item.get("box")]
    overlaps = 0
    for index, left in enumerate(fitted):
        for right in fitted[index + 1:]:
            a, b = left["box"], right["box"]
            if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                overlaps += 1
    return {"passages": len(placements), "fit_failures": len(placements) - len(fitted), "overlaps": overlaps}


def evaluate_case(case: dict[str, Any], root: Path, model: YoloSpeechBubble | None, output_dir: Path, font_path: str, minimum: int, mode: str = "both") -> dict[str, Any]:
    case_start = perf_counter()
    image_path = (root / case["image"]).resolve()
    read_start = perf_counter()
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    image_read_ms = round((perf_counter() - read_start) * 1000, 2)
    if image is None:
        raise ValueError(f"image does not exist: {image_path}")
    reference = reference_mask(case, image.shape[:2], root)
    heuristic = None
    heuristic_ms = None
    if mode in ("heuristic", "both"):
        start = perf_counter()
        heuristic = heuristic_mask(image, case.get("regions", []))
        heuristic_ms = round((perf_counter() - start) * 1000, 2)
    model_mask = None
    model_ms = None
    if mode in ("yolo", "both") and model is not None:
        start = perf_counter()
        model_mask = model(image)
        model_ms = round((perf_counter() - start) * 1000, 2)

    stem = Path(case["image"]).stem
    masks = []
    if reference is not None:
        masks.append(("reference", reference, (30, 180, 30)))
    if heuristic is not None:
        masks.append(("heuristic", heuristic, (255, 80, 30)))
    if model_mask is not None:
        masks.append(("YOLO", model_mask, (30, 80, 220)))
    render_start = perf_counter()
    detection_image = output_dir / f"{stem}-bubble-detection.png"
    detection = overlay(image, masks)
    cv2.imwrite(str(detection_image), detection)
    # Keep the older filename for callers that already consume it.
    cv2.imwrite(str(output_dir / f"{stem}-masks.png"), detection)
    result: dict[str, Any] = {
        "image": str(image_path),
        "detection_image": str(detection_image),
        "mode": mode,
        "image_read_ms": image_read_ms,
        "bubble_render_ms": round((perf_counter() - render_start) * 1000, 2),
        "heuristic_ms": heuristic_ms,
    }
    if model is not None:
        result["yolo_device"] = model.device
        result["yolo_model"] = model.model_name
        result["yolo_model_path"] = str(model.model_path)
        result["yolo_imgsz"] = model.image_size
        result["yolo_retina_masks"] = model.retina_masks
    if heuristic is not None and reference is not None:
        result["heuristic"] = score_mask(heuristic, reference).__dict__
    if model_mask is not None:
        result["yolo_ms"] = model_ms
        result["yolo_runtime_device"] = model.runtime_device
        if reference is not None:
            result["yolo"] = score_mask(model_mask, reference).__dict__
    for name, mask in (("heuristic", heuristic), ("yolo", model_mask)):
        if not case.get("regions"):
            continue
        if mask is None:
            continue
        try:
            render_start = perf_counter()
            rendered, placements = render_candidate(image, mask, case, font_path, minimum)
        except (ImportError, OSError, AttributeError) as error:
            # Mask comparison remains useful on hosts without the optional
            # FreeType bindings used by the production text renderer.
            result[f"{name}_render_error"] = str(error)
        else:
            cv2.imwrite(str(output_dir / f"{stem}-{name}-render.png"), rendered)
            result[f"{name}_placements"] = placements
            result[f"{name}_layout"] = placement_summary(placements)
            result[f"{name}_render_ms"] = round((perf_counter() - render_start) * 1000, 2)
    result["total_ms"] = round((perf_counter() - case_start) * 1000, 2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", type=Path, required=True, metavar="IMAGE_OR_DIR",
                        help="one or more images or directories of images")
    parser.add_argument("--mode", choices=("yolo", "heuristic", "both"), default="both",
                        help="which bubble detector(s) to run; both writes a combined image with a legend")
    parser.add_argument("--device", choices=("auto", "mps", "cuda", "cpu"), default="auto",
                        help="YOLO device; auto prefers MPS, then CUDA, then CPU")
    parser.add_argument("--model", choices=tuple(MODEL_REGISTRY), default=DEFAULT_MODEL_NAME,
                        help="YOLO model name; manga109 downloads best.pt on first use")
    parser.add_argument("--imgsz", type=int, default=512,
                        help="YOLO inference side length (512 is faster than the 640 default; 416 is faster still)")
    parser.add_argument("--retina-masks", action="store_true",
                        help="use full-resolution masks; slower, but preserves finer bubble boundaries")
    parser.add_argument("--region", action="append", type=_parse_region, metavar="X,Y,W,H",
                        help="optional heuristic seed region; repeat for multiple text boxes")
    parser.add_argument("--output-dir", type=Path, required=True, help="directory for overlays, renders, and metrics.json")
    parser.add_argument("--font", default=str(PROJECT_ROOT / "fonts/comic shanns 2.ttf"))
    parser.add_argument("--minimum-font-size", type=int, default=12)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        input_paths = expand_input_paths(args.input, output_dir)
    except ValueError as error:
        parser.error(str(error))
    model_spec = None
    model_path = None
    model_download_ms = None
    model_load_ms = None
    if args.mode in ("yolo", "both"):
        try:
            model_spec, model_path, model_download_ms = resolve_model(args.model)
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            parser.error(str(error))
        model_start = perf_counter()
        model = YoloSpeechBubble(
            model_path,
            device=args.device,
            image_size=args.imgsz,
            retina_masks=args.retina_masks,
            model_name=args.model,
        )
        model_load_ms = round((perf_counter() - model_start) * 1000, 2)
    else:
        model = None
    print(f"mode={args.mode}")
    if model is not None:
        print(f"yolo_model_name={model_spec.name}")
        print(f"yolo_model={model_spec.display_name}")
        print(f"yolo_model_path={model_path}")
        if model_download_ms is not None:
            print(f"yolo_model_download_time={model_download_ms:.1f}ms")
        print(f"yolo_device_requested={args.device} yolo_device_selected={model.device}")
        print(f"yolo_imgsz={model.image_size} yolo_retina_masks={model.retina_masks}")
        print(f"yolo_model_load_time={model_load_ms:.1f}ms")
    regions = [{"box": box} for box in (args.region or [])]
    cases = [{"image": str(path.resolve()), "regions": regions} for path in input_paths]
    results = [evaluate_case(case, Path.cwd(), model, output_dir, args.font, args.minimum_font_size, args.mode) for case in cases]
    (output_dir / "metrics.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for result in results:
        heuristic = result.get("heuristic", {})
        yolo = result.get("yolo", {})
        suffix = ""
        if heuristic:
            suffix += f" heuristic IoU={heuristic['iou']:.3f}"
        if yolo:
            suffix += f" YOLO IoU={yolo['iou']:.3f}"
        if result.get("heuristic_ms") is not None:
            suffix += f" heuristic_time={result['heuristic_ms']:.1f}ms"
        if result.get("yolo_ms") is not None:
            suffix += f" yolo_device={result.get('yolo_runtime_device', result.get('yolo_device'))}"
            suffix += f" yolo_time={result['yolo_ms']:.1f}ms"
        suffix += f" image_read={result['image_read_ms']:.1f}ms bubble_render={result['bubble_render_ms']:.1f}ms total={result['total_ms']:.1f}ms"
        print(f"image={Path(result['image']).name}{suffix} detection={result['detection_image']}")
    print(f"metrics={output_dir / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
