"""Pipeline stage manifests and serialized result documents."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from ..config import Config
from ..mask_builder import build_inpaint_masks
from .stages import PipelineStage, downstream_stages
from .cpu import CPU_PRIORITY_BACKGROUND, run_cpu_stage
from ..utils import Context, Quadrilateral, TextBlock, dump_image, load_image
from ..utils.image_storage import find_asset, save_jpeg
from ..utils.device_memory import log_memory_stats
from ..utils.log import get_logger


STAGES = [
    ("input", "Input"),
    ("colorization", "Colorization"),
    ("upscaling", "Upscaling"),
    ("detection", "Detection"),
    ("ocr", "OCR"),
    ("bubble_detection", "Bubble detection"),
    ("textline_merge", "Text-line merge"),
    ("translation", "Translation"),
    ("mask_generation", "Mask generation"),
    ("layout", "Layout"),
    ("inpainting", "Inpainting"),
    ("rendering", "Rendering / final"),
]

PROGRESS_TO_STAGE = {
    "colorizing": "colorization",
    "upscaling": "upscaling",
    "detection": "detection",
    "ocr": "ocr",
    "textline_merge": "textline_merge",
    "bubble-detection": "bubble_detection",
    "translating": "translation",
    "layout": "layout",
    "mask-generation": "mask_generation",
    "inpainting": "inpainting",
    "rendering": "rendering",
}

STAGE_ARTIFACTS = {
    "input": ("input.jpg", "input.png"),
    "colorization": ("colorized.png",),
    "upscaling": ("upscaled.png",),
    "detection": ("mask_raw.png", "detection.json", "bubble_mask.png"),
    "ocr": ("ocr.json",),
    "textline_merge": ("text_regions_merged.json",),
    "bubble_detection": ("bubble_mask.png", "bubble_detections.json"),
    "translation": ("translations.json", "translation_detail.json"),
    "layout": ("layout.json",),
    "mask_generation": (
        "text_mask.png", "bubble_mask.png", "detector_rescue_mask.png",
        "bubble_residual_mask.png", "protected_bubble_edge.png", "mask_final.png",
        "inpaint_mask.png", "profiling.json",
    ),
    "inpainting": ("inpainted.jpg", "inpainted.png"),
    "rendering": ("final.jpg", "final.png", "text_regions.json"),
}

logger = get_logger("pipeline")

CHECKPOINT_STAGE_IDS = {
    PipelineStage.UPSCALE.value: "upscaling",
    PipelineStage.TEXT_GROUPING.value: "textline_merge",
    PipelineStage.FINALIZE.value: "rendering",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any):
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_default(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_default(item) for key, item in value.items()}
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def serialize_regions(regions) -> list[dict[str, Any]]:
    """Keep useful OCR/translation/layout facts without serializing model objects."""
    from ..rendering.bubble_layout import encode_safe_shape
    result = []
    for index, region in enumerate(regions or []):
        item: dict[str, Any] = {"index": index}
        for key in (
            "region_id", "source_region_ids", "source_regions", "group_id", "group_members", "bubble_id",
            "text", "text_raw", "translation", "confidence", "font_size", "source_font_size",
            "calibrated_font_size", "angle", "direction", "alignment", "target_lang", "source_lang",
            "bubble_bounds", "layout_bounds", "layout_segments", "review_required", "review_reason",
            "placement_mode", "line_spacing", "letter_spacing", "font_family", "bold", "italic",
            "provenance", "source_style", "source_line_styles", "retention", "retention_reason",
            "translation_policy",
        ):
            value = getattr(region, key, None)
            if value is None and key == "confidence":
                value = getattr(region, "prob", None)
            if value is not None:
                item[key] = _json_default(value)
        fg_col, bg_col = region.get_font_colors() if hasattr(region, "get_font_colors") else (getattr(region, "fg_color", None), getattr(region, "bg_color", None))
        if fg_col is not None:
            item["fg_color"] = _json_default(fg_col)
        if bg_col is not None:
            item["bg_color"] = _json_default(bg_col)
        interior = getattr(region, "_bubble_interior", None)
        if interior is not None and np.any(interior):
            item["bubble_safe_shape"] = encode_safe_shape(interior)
        elif getattr(region, "bubble_safe_shape", None):
            item["bubble_safe_shape"] = region.bubble_safe_shape
        for key in ("xywh", "pts", "lines"):
            value = getattr(region, key, None)
            if value is not None:
                item[key] = _json_default(value)
        result.append(item)
    return result


def serialize_editor_regions(regions) -> list[dict[str, Any]]:
    """Minimal editor payload for resumed runs that skip the normal save path."""
    from ..rendering.bubble_layout import encode_safe_shape, encode_rendered_box
    result = []
    for index, region in enumerate(regions or []):
        xywh = getattr(region, "xywh", [0, 0, 0, 0])
        xywh = _json_default(xywh)
        result.append({
            "id": getattr(region, "group_id", f"bubble_{index}"),
            "x": xywh[0],
            "y": xywh[1],
            "width": xywh[2],
            "height": xywh[3],
            "lines": _json_default(getattr(region, "lines", [])),
            "original_text": getattr(region, "text", ""),
            "translation": getattr(region, "translation", ""),
            "confidence": _json_default(getattr(region, "confidence", getattr(region, "prob", None))),
            "font_size": getattr(region, "font_size", 24),
            "font_family": getattr(region, "font_family", ""),
            "fg_color": _json_default(getattr(region, "fg_colors", (0, 0, 0))),
            "bg_color": _json_default(getattr(region, "bg_colors", (0, 0, 0))),
            "alignment": getattr(region, "alignment", getattr(region, "_alignment", "auto")),
            "line_spacing": getattr(region, "line_spacing", 1.0),
            "letter_spacing": getattr(region, "letter_spacing", 1.0),
            "bold": bool(getattr(region, "bold", False)),
            "italic": bool(getattr(region, "italic", False)),
            "target_lang": getattr(region, "target_lang", ""),
            "direction": getattr(region, "direction", "h"),
            "layout_segments": [
                {**_json_default(segment), "rendered_png": encode_rendered_box(box["box"])}
                for segment, box in zip(getattr(region, "layout_segments", []),
                                        getattr(region, "_bubble_segments", []))
            ],
            "bubble_safe_shape": encode_safe_shape(getattr(region, "_bubble_interior", None)),
            "review_required": bool(getattr(region, "review_required", False)),
            "review_reason": getattr(region, "review_reason", None),
            "provenance": getattr(region, "provenance", None),
            "translation_remap": getattr(region, "translation_remap", None),
            "translation_source": getattr(region, "translation_source", None),
        })
    return result


ACTIVE_RUNS: dict[str, PipelineRun] = {}
_DOCUMENT_SAVER: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None


def set_document_saver(
    saver: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
) -> None:
    global _DOCUMENT_SAVER
    _DOCUMENT_SAVER = saver


async def save_result_documents(
    folder: str,
    documents: dict[str, Any],
    result_root: str | Path | None = None,
) -> None:
    if _DOCUMENT_SAVER is not None:
        await _DOCUMENT_SAVER(folder, documents)
        return
    # Keep the macOS filesystem-only server useful without requiring PostgreSQL.
    from server.constants import SERVER_RESULT_ROOT

    root = Path(result_root or SERVER_RESULT_ROOT).resolve()
    destination = (root / folder).resolve()
    if destination.parent != root:
        raise ValueError(f"Invalid result folder: {folder}")
    destination.mkdir(parents=True, exist_ok=True)
    for name, payload in documents.items():
        if Path(name).name != name or not name.endswith(".json"):
            raise ValueError(f"Invalid result document name: {name}")
        temporary = destination / f".{name}.{os.getpid()}.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        os.replace(temporary, destination / name)


def deserialize_textlines(data: list[dict[str, Any]]) -> list[Quadrilateral]:
    textlines = []
    for item in data or []:
        pts = item.get("pts")
        if pts is None and "lines" in item:
            pts = item["lines"]
        if pts is not None:
            pts_arr = np.array(pts, dtype=np.int32)
            if len(pts_arr.shape) == 3:
                pts_arr = pts_arr[0]
            txt = str(item.get("text") or item.get("text_raw") or "")
            prob = float(item.get("confidence") or item.get("prob") or 1.0)
            line = Quadrilateral(pts_arr, txt, prob)
            if item.get("source_style") is not None:
                line.source_style = item["source_style"]
            textlines.append(line)
    return textlines


def deserialize_textblocks(data: list[dict[str, Any]]) -> list[TextBlock]:
    blocks = []
    for item in data or []:
        lines = item.get("lines")
        if lines is None and "pts" in item:
            lines = [item["pts"]]
        if lines is None:
            lines = []
        texts = [str(item.get("text") or item.get("text_raw") or item.get("original_text") or "")]
        tb = TextBlock(
            lines=lines,
            texts=texts,
            translation=str(item.get("translation") or ""),
            font_size=float(item.get("font_size", -1) or -1),
            angle=float(item.get("angle", 0) or 0),
            fg_color=tuple(item.get("fg_color") or (0, 0, 0)),
            bg_color=tuple(item.get("bg_color") or (0, 0, 0)),
            line_spacing=float(item.get("line_spacing", 1.0) or 1.0),
            letter_spacing=float(item.get("letter_spacing", 1.0) or 1.0),
            font_family=str(item.get("font_family") or ""),
            bold=bool(item.get("bold", False)),
            italic=bool(item.get("italic", False)),
            direction=str(item.get("direction") or "auto"),
            alignment=str(item.get("alignment") or "auto"),
            target_lang=str(item.get("target_lang") or ""),
            prob=float(item.get("confidence") or item.get("prob") or 1.0),
        )
        tb.region_id = str(item.get("region_id") or "")
        for key in ("source_style", "source_line_styles"):
            if key in item:
                setattr(tb, key, item[key])
        for key in (
            "group_id", "group_members", "source_region_ids", "source_regions", "bubble_id",
            "source_font_size", "calibrated_font_size", "placement_mode",
            "bubble_bounds", "layout_bounds", "layout_segments",
            "bubble_safe_shape", "review_required", "review_reason",
            "retention", "retention_reason", "translation_policy",
        ):
            if key in item and item[key] is not None:
                setattr(tb, key, item[key])
        if not getattr(tb, "group_id", None):
            tb.group_id = str(item.get("id") or tb.region_id or "")
        blocks.append(tb)
    return blocks


class PipelineRun:
    def __init__(self, result_root: str | Path, folder: str, image, config):
        self.path = Path(result_root).resolve() / folder
        self.path.mkdir(parents=True, exist_ok=True)
        self.active_stage: str | None = None
        self.started: dict[str, float] = {}
        self.documents: dict[str, Any] = {}
        self.ctx: Context | None = None
        self.translator: Any = None
        self.memory_batch_id: str | None = None
        self.memory_page_id: str | None = folder
        self._memory_starts: dict[str, dict[str, Any]] = {}
        self.config: Any = config
        colorizer = getattr(getattr(config, "colorizer", None), "colorizer", None)
        colorization_enabled = getattr(colorizer, "value", colorizer) != "none"
        upscaling_enabled = bool(getattr(getattr(config, "upscale", None), "upscale_ratio", None))
        config_dict = config.dict() if hasattr(config, "dict") else {}
        now = _now()
        self.manifest = {
            "version": 1,
            "kind": "pipeline-run",
            "folder": folder,
            "status": "running",
            "createdAt": now,
            "updatedAt": now,
            "source": {
                "filename": getattr(config, "original_name", None) or f"{folder}.png",
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
            },
            "config": config_dict,
            "stages": [],
        }
        for stage_id, label in STAGES:
            if stage_id == "input":
                status, reason = "completed", None
            elif stage_id == "colorization" and not colorization_enabled:
                status, reason = "skipped", "Disabled in translation settings"
            elif stage_id == "upscaling" and not upscaling_enabled:
                status, reason = "skipped", "Disabled in translation settings"
            else:
                status, reason = "pending", None
            item = {"id": stage_id, "label": label, "status": status}
            if reason:
                item["reason"] = reason
            self.manifest["stages"].append(item)
        ACTIVE_RUNS[folder] = self
        self._write()

    @classmethod
    def from_folder(cls, result_root: str | Path, folder: str) -> PipelineRun | None:
        if folder in ACTIVE_RUNS:
            return ACTIVE_RUNS[folder]
        return None

    @classmethod
    def get_or_load(cls, result_root: str | Path, folder: str) -> PipelineRun | None:
        if folder in ACTIVE_RUNS:
            return ACTIVE_RUNS[folder]
        return cls.from_folder(result_root, folder)

    @classmethod
    def from_documents(
        cls,
        result_root: str | Path,
        folder: str,
        documents: dict[str, Any],
    ) -> PipelineRun | None:
        manifest = documents.get("pipeline_manifest.json")
        if not isinstance(manifest, dict):
            return None
        run = cls.__new__(cls)
        run.path = Path(result_root).resolve() / folder
        run.active_stage = None
        run.started = {}
        run.memory_batch_id = None
        run.memory_page_id = folder
        run._memory_starts = {}
        run.manifest = manifest
        stages = {stage.get("id"): stage for stage in manifest.get("stages", [])}
        manifest["stages"] = [
            stages.pop(stage_id, {"id": stage_id, "label": label, "status": "pending"})
            for stage_id, label in STAGES
        ] + list(stages.values())
        run.documents = {
            name: payload for name, payload in documents.items()
            if name != "pipeline_manifest.json"
        }
        run.ctx = None
        run.translator = None
        run.config = manifest.get("config", {})
        ACTIVE_RUNS[folder] = run
        return run

    def _write(self):
        if getattr(self, "manifest", None) is not None:
            self.manifest["updatedAt"] = _now()

    async def checkpoint(self):
        folder_name = getattr(self, "path", None).name if getattr(self, "path", None) is not None else (getattr(self, "manifest", {}) or {}).get("folder")
        if not folder_name:
            return
        manifest = getattr(self, "manifest", None) or {}
        documents = getattr(self, "documents", None) or {}
        await save_result_documents(
            folder_name,
            json.loads(json.dumps(
                {"pipeline_manifest.json": manifest, **documents},
                default=_json_default,
            )),
            self.path.parent,
        )

    def _stage(self, stage_id: str) -> dict[str, Any]:
        return next(item for item in self.manifest["stages"] if item["id"] == stage_id)

    def _memory_identity(self) -> tuple[str | None, str | None]:
        ctx = self.ctx
        image_context = getattr(ctx, "image_context", None) if ctx is not None else None
        page_id = getattr(self, "memory_page_id", None) or (
            image_context.get("file_md5") if isinstance(image_context, dict) else None
        ) or getattr(self, "manifest", {}).get("folder")
        batch_id = getattr(self, "memory_batch_id", None) or (getattr(ctx, "batch_id", None) if ctx is not None else None)
        return batch_id, page_id

    def _memory_begin(self, stage_id: str) -> None:
        starts = getattr(self, "_memory_starts", None)
        if starts is None:
            starts = self._memory_starts = {}
        if stage_id not in starts:
            batch_id, page_id = self._memory_identity()
            starts[stage_id] = log_memory_stats(
                f"{stage_id}:start",
                device=getattr(getattr(self, "translator", None), "device", None),
                batch_id=batch_id,
                page_id=page_id,
            )

    def _memory_end(self, stage_id: str) -> None:
        starts = getattr(self, "_memory_starts", {})
        before = starts.pop(stage_id, None) if starts else None
        if before is None:
            return
        batch_id, page_id = self._memory_identity()
        log_memory_stats(
            f"{stage_id}:end",
            device=getattr(getattr(self, "translator", None), "device", None),
            batch_id=batch_id,
            page_id=page_id,
            before=before,
        )

    def _document(self, name: str) -> Any | None:
        return self.documents.get(name)

    def _artifacts(self, stage_id: str) -> list[str]:
        return [
            name for name in STAGE_ARTIFACTS.get(stage_id, ())
            if (self.path / name).is_file() or name in self.documents
        ]

    def _finish(self, stage_id: str, status: str = "completed", reason: str | None = None):
        self._memory_end(stage_id)
        stage = self._stage(stage_id)
        if stage.get("status") == "pending":
            stage["startedAt"] = _now()
        stage["status"] = status
        if reason:
            stage["reason"] = reason
        stage["finishedAt"] = _now()
        if stage_id in self.started:
            stage["durationMs"] = round((time.monotonic() - self.started[stage_id]) * 1000)
        artifacts = self._artifacts(stage_id)
        if artifacts:
            stage["artifacts"] = artifacts
        if self.active_stage == stage_id:
            self.active_stage = None

    async def begin_stage(self, stage_id: str, config: Config) -> None:
        """Persist a stage as running before it enters a shared model batch."""
        stage = self._stage(stage_id)
        self.config = config
        self.manifest["config"] = config.dict() if hasattr(config, "dict") else dict(config)
        stage["status"] = "running"
        stage["startedAt"] = _now()
        self.started[stage_id] = time.monotonic()
        self._memory_begin(stage_id)
        self.refresh()
        await self.checkpoint()

    async def fail_stage(self, stage_id: str, reason: str) -> None:
        self._finish(stage_id, "failed", reason)
        await self.checkpoint()

    def _begin(self, stage_id: str):
        stage = self._stage(stage_id)
        if stage["status"] == "skipped":
            return
        if self.active_stage and self.active_stage != stage_id:
            self._finish(self.active_stage)
        stage["status"] = "running"
        stage["startedAt"] = _now()
        self.started[stage_id] = time.monotonic()
        self.active_stage = stage_id
        self._memory_begin(stage_id)

    def stage_for_progress(self, state: str) -> str | None:
        stage_id = PROGRESS_TO_STAGE.get(state)
        return stage_id if any(stage["id"] == stage_id for stage in self.manifest["stages"]) else None

    def cancel(self, reason: str = "Pipeline stopped by user"):
        self._terminal("cancelled", reason, self.active_stage)
        self.release_runtime()
        self.refresh()

    def _terminal(self, status: str, reason: str, failed_stage: str | None = None):
        if self.active_stage:
            self._finish(self.active_stage, status if failed_stage == self.active_stage else "completed", reason if failed_stage == self.active_stage else None)
        found = failed_stage is None
        for stage in self.manifest["stages"]:
            if stage["id"] == failed_stage:
                found = True
                self._finish(stage["id"], status, reason)
            elif found and stage["status"] == "pending":
                self._finish(stage["id"], "unavailable", reason)
        self.manifest["status"] = "partial" if status != "cancelled" else "cancelled"
        self.manifest["error"] = reason

    def progress(self, state: str, finished: bool = False):
        stage_id = self.stage_for_progress(state)
        if stage_id:
            self._begin(stage_id)
        elif state == "saving":
            if self.active_stage == "rendering":
                self._finish("rendering")
        elif state == "skip-no-regions":
            self._terminal("completed", "No text regions detected")
        elif state == "skip-no-text":
            self._terminal("completed", "No text remained after OCR")
        elif state == "error-translating":
            self._terminal("failed", "Translation returned no usable text", "translation")
        elif state == "cancelled":
            self._terminal("cancelled", "Translation cancelled", self.active_stage)
        if self.manifest["status"] == "running" and finished and state not in {"skip-no-regions", "skip-no-text", "error-translating", "cancelled"}:
            if self.active_stage:
                self._finish(self.active_stage)
            for stage in self.manifest["stages"]:
                if stage["status"] == "pending":
                    self._finish(stage["id"], "skipped", "No work was reported for this phase")
            self.manifest["status"] = "completed"
            self.manifest.pop("error", None)
        self.refresh()

    def fail(self, reason: str):
        self._terminal("failed", reason, self.active_stage)
        self.manifest["status"] = "failed"
        self.refresh()

    def write_json(self, filename: str, payload: Any):
        self.documents[filename] = payload
        self._write()

    def record_translation(
        self,
        config: Any,
        request: list[dict[str, Any]],
        response: Any,
        ctx: Context | None = None,
    ) -> None:
        translator_config = getattr(config, "translator", None)
        translator = getattr(getattr(translator_config, "translator", None), "value", None)
        translator = translator or str(getattr(translator_config, "translator", "unknown"))
        model = ctx.get("translator_model") if ctx is not None else None
        if not model and ctx is not None:
            model = getattr(ctx, "offline_model", None) or getattr(ctx, "gemini_model", None)
        detail = {
            "processedAt": _now(),
            "translator": {
                "name": translator,
                "model": model,
                "sourceLanguage": getattr(translator_config, "source_lang", "auto"),
                "targetLanguage": getattr(translator_config, "target_lang", None),
            },
            "request": request,
            "response": response,
        }
        if ctx is not None and getattr(ctx, "translation_duration_ms", None) is not None:
            detail["durationMs"] = ctx.translation_duration_ms
        if ctx is not None and getattr(ctx, "translation_started_at", None) is not None:
            detail["startedAt"] = ctx.translation_started_at
        if ctx is not None and getattr(ctx, "translation_finished_at", None) is not None:
            detail["finishedAt"] = ctx.translation_finished_at
        self.write_json("translation_detail.json", detail)

    def refresh(self):
        for stage in self.manifest["stages"]:
            artifacts = self._artifacts(stage["id"])
            if artifacts:
                stage["artifacts"] = artifacts
        self._write()

    def cleanup_completed_artifacts(self) -> None:
        if self.manifest.get("status") != "completed":
            return
        for name in (
            "colorized.png",
            "upscaled.png",
            "bboxes.png",
            "bboxes_unfiltered.png",
            "inpaint_input.png",
            "mask_raw.png",
            "mask_final.png",
        ):
            (self.path / name).unlink(missing_ok=True)

    def release_runtime(self, preserve_output: bool = False):
        translator = getattr(self, "translator", None)
        batch_id, page_id = self._memory_identity()
        before = log_memory_stats(
            "run.release_runtime:before",
            device=getattr(translator, "device", None),
            batch_id=batch_id,
            page_id=page_id,
        )
        ctx = getattr(self, "ctx", None)
        if ctx is not None and hasattr(ctx, "cleanup_runtime"):
            ctx.cleanup_runtime(preserve_output=preserve_output)
        log_memory_stats(
            "run.release_runtime:after",
            device=getattr(translator, "device", None),
            batch_id=batch_id,
            page_id=page_id,
            before=before,
        )
        self.ctx = None
        self.translator = None
        if translator is not None and getattr(translator, "_pipeline_run", None) is self:
            translator._pipeline_run = None
        manifest = getattr(self, "manifest", None)
        folder = manifest.get("folder") if isinstance(manifest, dict) else None
        if folder and ACTIVE_RUNS.get(folder) is self:
            ACTIVE_RUNS.pop(folder, None)

    def _ensure_context(self) -> Context:
        if self.ctx is None:
            self.ctx = Context()
        ctx = self.ctx
        if getattr(ctx, "input", None) is None:
            input_path = next((self.path / f"input{suffix}" for suffix in (".jpg", ".jpeg", ".png") if (self.path / f"input{suffix}").is_file()), None)
            if input_path is not None:
                ctx.input = Image.open(input_path).convert("RGB")
        if getattr(ctx, "img_colorized", None) is None:
            if (self.path / "colorized.png").is_file():
                ctx.img_colorized = Image.open(self.path / "colorized.png").convert("RGB")
            elif getattr(ctx, "input", None) is not None:
                ctx.img_colorized = ctx.input
        if getattr(ctx, "upscaled", None) is None:
            if (self.path / "upscaled.png").is_file():
                ctx.upscaled = Image.open(self.path / "upscaled.png").convert("RGB")
            elif getattr(ctx, "img_colorized", None) is not None:
                ctx.upscaled = ctx.img_colorized
            elif getattr(ctx, "input", None) is not None:
                ctx.upscaled = ctx.input
        if getattr(ctx, "upscaled", None) is not None and (getattr(ctx, "img_rgb", None) is None or getattr(ctx, "img_alpha", None) is None):
            ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)
        return ctx

    async def retry_stage(
        self,
        stage_id: str,
        new_config: dict | Config,
        translator,
        *,
        defer_bubble_detection: bool = False,
        precomputed_ocr: list | None = None,
        precomputed_upscale: Image.Image | None = None,
        precomputed_detection: tuple[list, np.ndarray | None, np.ndarray | None] | None = None,
        precomputed_bubbles: list | None = None,
        stage_already_running: bool = False,
    ) -> dict[str, Any]:
        valid_stages = {
            "colorization",
            "upscaling",
            "detection",
            "ocr",
            "textline_merge",
            "bubble_detection",
            "translation",
            "mask_generation",
            "layout",
            "inpainting",
            "rendering",
        }
        if stage_id not in valid_stages:
            raise ValueError(f"Stage '{stage_id}' is not retryable")

        if isinstance(new_config, dict):
            base_config = dict(self.manifest.get("config", {}) or {})
            base_config.update(new_config)
            config = Config.parse_obj(base_config)
        else:
            config = new_config

        self.config = config
        self.manifest["config"] = config.dict() if hasattr(config, "dict") else dict(config)

        stage = self._stage(stage_id)
        if stage_already_running:
            if stage.get("status") != "running":
                raise RuntimeError(f"Stage {stage_id} was not marked running before batched inference")
        else:
            stage["status"] = "running"
        stage["startedAt"] = _now()
        self.started[stage_id] = time.monotonic()
        self._memory_begin(stage_id)
        self.refresh()

        ctx = self._ensure_context()

        try:
            if stage_id == "colorization":
                if ctx.input is None:
                    raise RuntimeError("No input image available for colorization")
                ctx.img_colorized = await translator._run_colorizer(config, ctx)
                colorized = np.array(ctx.img_colorized)
                if len(colorized.shape) == 3 and colorized.shape[2] == 3:
                    colorized = cv2.cvtColor(colorized, cv2.COLOR_RGB2BGR)
                cv2.imwrite(str(self.path / "colorized.png"), colorized)

            elif stage_id == "upscaling":
                if ctx.img_colorized is None:
                    ctx.img_colorized = ctx.input
                if ctx.img_colorized is None:
                    raise RuntimeError("No image available for upscaling")
                ctx.upscaled = (
                    precomputed_upscale
                    if precomputed_upscale is not None
                    else await translator._run_upscaling(config, ctx)
                )
                ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)
                upscaled = np.array(ctx.upscaled)
                if len(upscaled.shape) == 3 and upscaled.shape[2] == 3:
                    upscaled = cv2.cvtColor(upscaled, cv2.COLOR_RGB2BGR)
                cv2.imwrite(str(self.path / "upscaled.png"), upscaled)

            elif stage_id == "detection":
                if ctx.img_rgb is None:
                    raise RuntimeError("No image canvas available for detection")
                if precomputed_detection is None:
                    ctx.textlines, ctx.mask_raw, ctx.mask = await translator._run_detection(config, ctx)
                else:
                    ctx.textlines, ctx.mask_raw, ctx.mask = precomputed_detection
                if ctx.mask_raw is not None:
                    cv2.imwrite(str(self.path / "mask_raw.png"), ctx.mask_raw)
                canvas = np.asarray(ctx.upscaled)
                if len(canvas.shape) == 3 and canvas.shape[2] == 3:
                    canvas = cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR)
                cv2.imwrite(str(self.path / "original_canvas.png"), canvas)
                self.write_json("detection.json", serialize_regions(ctx.textlines))

            elif stage_id == "ocr":
                if ctx.img_rgb is None:
                    raise RuntimeError("No image canvas available for OCR")
                if not getattr(ctx, "textlines", None):
                    detection = self._document("detection.json")
                    if detection is not None:
                        ctx.textlines = deserialize_textlines(detection)
                if not getattr(ctx, "textlines", None):
                    ctx.textlines = []
                    self.write_json("ocr.json", [])
                else:
                    ctx.textlines = (
                        precomputed_ocr
                        if precomputed_ocr is not None
                        else await translator._run_ocr(config, ctx)
                    )
                    self.write_json("ocr.json", serialize_regions(ctx.textlines))

            elif stage_id == "textline_merge":
                if ctx.img_rgb is None:
                    raise RuntimeError("No image canvas available for textline merge")
                if not getattr(ctx, "textlines", None):
                    source = self._document("ocr.json") or self._document("detection.json")
                    if source is not None:
                        ctx.textlines = deserialize_textlines(source)
                if not getattr(ctx, "textlines", None):
                    raise RuntimeError("No textlines available to merge")
                ctx.text_regions = await translator._run_textline_merge(config, ctx)
                bubble_data = self._document("bubble_detections.json")
                if bubble_data is not None:
                    from ..detection.bubble import deserialize_bubble_detections
                    from ..rendering.bubble_layout import group_regions_by_bubbles

                    ctx.bubble_detections = deserialize_bubble_detections(
                        bubble_data, ctx.img_rgb.shape
                    )
                    ctx.text_regions = group_regions_by_bubbles(
                        ctx.text_regions,
                        ctx.bubble_detections,
                        group=bool(getattr(config.bubble_detection, "group_regions", False)),
                    )
                elif not defer_bubble_detection:
                    await translator._detect_speech_bubbles(config, ctx, report_progress=False)
                ctx._bubble_detection_done = True
                self.write_json("text_regions_merged.json", serialize_regions(ctx.text_regions))

            elif stage_id == "bubble_detection":
                if ctx.img_rgb is None:
                    raise RuntimeError("No image canvas available for bubble detection")
                if not getattr(ctx, "text_regions", None):
                    merged = self._document("text_regions_merged.json")
                    if merged is not None:
                        ctx.text_regions = deserialize_textblocks(merged)
                await translator._detect_speech_bubbles(
                    config,
                    ctx,
                    report_progress=False,
                    precomputed_detections=precomputed_bubbles,
                )
                detections = getattr(ctx, "bubble_detections", None) or []
                from ..detection.bubble import serialize_bubble_detections

                self.write_json("bubble_detections.json", serialize_bubble_detections(detections))
                if ctx.text_regions:
                    self.write_json("text_regions_merged.json", serialize_regions(ctx.text_regions))
                if detections:
                    bubble_mask = np.zeros(ctx.img_rgb.shape[:2], np.uint8)
                    for detection in detections:
                        bubble_mask = np.maximum(bubble_mask, np.asarray(detection.mask, dtype=np.uint8))
                    cv2.imwrite(str(self.path / "bubble_mask.png"), bubble_mask)

            elif stage_id == "translation":
                if not getattr(ctx, "text_regions", None):
                    merged = self._document("text_regions_merged.json")
                    if merged is not None:
                        ctx.text_regions = deserialize_textblocks(merged)
                if not getattr(ctx, "text_regions", None):
                    raise RuntimeError("No text regions available for translation")
                translation_request = [
                    {"index": index, "text": getattr(region, "text", "")}
                    for index, region in enumerate(ctx.text_regions)
                ]
                ctx.text_regions = await translator._run_text_translation(config, ctx)
                self.record_translation(
                    config,
                    translation_request,
                    [
                        {"index": index, "translation": getattr(region, "translation", "")}
                        for index, region in enumerate(ctx.text_regions or [])
                    ],
                    ctx,
                )
                self.write_json("translations.json", serialize_regions(ctx.text_regions if isinstance(ctx.text_regions, list) else []))

            elif stage_id == "mask_generation":
                if ctx.img_rgb is None:
                    raise RuntimeError("No image canvas available for mask generation")
                if not getattr(ctx, "text_regions", None):
                    merged = self._document("text_regions_merged.json")
                    if merged is not None:
                        ctx.text_regions = deserialize_textblocks(merged)
                if getattr(ctx, "mask_raw", None) is None:
                    mask_raw_path = self.path / "mask_raw.png"
                    if mask_raw_path.is_file():
                        ctx.mask_raw = cv2.imread(str(mask_raw_path), cv2.IMREAD_GRAYSCALE)
                if not getattr(ctx, "bubble_detections", None):
                    bubble_data = self._document("bubble_detections.json")
                    if bubble_data is not None:
                        from ..detection.bubble import deserialize_bubble_detections

                        ctx.bubble_detections = deserialize_bubble_detections(
                            bubble_data, ctx.img_rgb.shape
                        )
                bundle = await run_cpu_stage(
                    build_inpaint_masks,
                    image=ctx.img_rgb,
                    detector_textlines=getattr(ctx, "textlines", None),
                    detector_mask=getattr(ctx, "mask_raw", None),
                    text_regions=ctx.text_regions or [],
                    bubble_detections=getattr(ctx, "bubble_detections", None),
                    config=config,
                    page_geometry=getattr(ctx, "page_geometry", None),
                    priority=CPU_PRIORITY_BACKGROUND,
                )
                ctx.mask_bundle = bundle
                ctx.mask_profile = bundle.profile
                ctx.page_geometry = bundle.page_geometry
                ctx.text_mask = bundle.text_mask
                ctx.bubble_mask = bundle.bubble_cleanup_mask
                ctx.detector_rescue_mask = bundle.detector_rescue_mask
                ctx.bubble_residual_mask = bundle.bubble_residual_mask
                ctx.protected_edge_mask = bundle.protected_edge_mask
                ctx.mask = bundle.final_inpaint_mask
                ctx.inpaint_mask = bundle.final_inpaint_mask
                cv2.imwrite(str(self.path / "text_mask.png"), ctx.text_mask)
                cv2.imwrite(str(self.path / "bubble_mask.png"), ctx.bubble_mask)
                cv2.imwrite(str(self.path / "mask_final.png"), ctx.mask)
                cv2.imwrite(str(self.path / "detector_rescue_mask.png"), ctx.detector_rescue_mask)
                cv2.imwrite(str(self.path / "bubble_residual_mask.png"), ctx.bubble_residual_mask)
                cv2.imwrite(str(self.path / "protected_bubble_edge.png"), ctx.protected_edge_mask)
                self.write_json("profiling.json", bundle.profile)
                ctx.cleanup_mask_diagnostics()
                bundle = None

            elif stage_id == "layout":
                from ..rendering.layout import layout_page
                from ..rendering.layout.frozen import serialize_frozen_layout

                if getattr(ctx, "inpaint_mask", None) is None:
                    mask_final_path = self.path / "mask_final.png"
                    if mask_final_path.is_file():
                        ctx.inpaint_mask = cv2.imread(str(mask_final_path), cv2.IMREAD_GRAYSCALE)
                        ctx.mask = ctx.inpaint_mask
                if not getattr(ctx, "text_regions", None):
                    source = self._document("translations.json") or self._document("text_regions_merged.json")
                    if source is not None:
                        ctx.text_regions = deserialize_textblocks(source)
                if not getattr(ctx, "text_regions", None):
                    raise RuntimeError("No text regions available for layout")
                bubble_data = self._document("bubble_detections.json")
                if bubble_data is not None:
                    from ..detection.bubble import deserialize_bubble_detections
                    from ..rendering.bubble_layout import restore_bubble_assignments

                    ctx.bubble_detections = deserialize_bubble_detections(bubble_data, ctx.img_rgb.shape)
                    if ctx.bubble_detections:
                        restore_bubble_assignments(ctx.text_regions, ctx.bubble_detections)
                    ctx._bubble_detection_done = True
                else:
                    bubble_data = []
                transform = getattr(getattr(config, "render", None), "transform_text_case", None)
                if transform:
                    for region in ctx.text_regions:
                        if getattr(region, "translation", None):
                            region.translation = transform(region.translation)
                await run_cpu_stage(
                    layout_page,
                    ctx,
                    config,
                    getattr(translator, "font_path", None)
                    or getattr(getattr(config, "render", None), "font_path", None),
                    priority=CPU_PRIORITY_BACKGROUND,
                )
                font_path = (
                    getattr(translator, "font_path", None)
                    or getattr(getattr(config, "render", None), "font_path", None)
                )
                if not font_path:
                    from ..rendering import get_default_eng_font
                    font_path = get_default_eng_font()
                self.write_json("layout.json", serialize_frozen_layout(
                    ctx, config, font_path, bubble_data
                ))

            elif stage_id == "inpainting":
                if ctx.img_rgb is None:
                    raise RuntimeError("No image canvas available for inpainting")
                if getattr(ctx, "mask", None) is None:
                    mask_final_path = self.path / "mask_final.png"
                    if mask_final_path.is_file():
                        ctx.mask = cv2.imread(str(mask_final_path), cv2.IMREAD_GRAYSCALE)
                if not getattr(ctx, "text_regions", None):
                    merged = self._document("text_regions_merged.json")
                    if merged is not None:
                        ctx.text_regions = deserialize_textblocks(merged)
                ctx.img_inpainted = await translator._run_inpainting(config, ctx)
                if ctx.img_inpainted is not None:
                    save_jpeg(ctx.img_inpainted, self.path / "inpainted.jpg")
                    (self.path / "inpainted.png").unlink(missing_ok=True)

            elif stage_id == "rendering":
                from ..rendering.layout.frozen import hydrate_layout, layout_input_fingerprints

                if getattr(ctx, "img_inpainted", None) is None:
                    inpainted_path = find_asset(self.path, "inpainted")
                    if inpainted_path is not None:
                        with Image.open(inpainted_path) as image:
                            ctx.img_inpainted = np.array(image.convert("RGB"))
                    elif ctx.img_rgb is not None:
                        ctx.img_inpainted = ctx.img_rgb.copy()
                if not getattr(ctx, "text_regions", None):
                    translations = self._document("translations.json")
                    if translations is not None:
                        ctx.text_regions = deserialize_textblocks(translations)
                if ctx.img_inpainted is None:
                    raise RuntimeError("No inpainted canvas available for rendering")
                layout_data = self._document("layout.json")
                if layout_data is not None:
                    bubble_data = self._document("bubble_detections.json")
                    if bubble_data is not None:
                        from ..detection.bubble import deserialize_bubble_detections
                        from ..rendering.bubble_layout import restore_bubble_assignments

                        ctx.bubble_detections = deserialize_bubble_detections(bubble_data, ctx.img_rgb.shape)
                        if ctx.bubble_detections:
                            restore_bubble_assignments(ctx.text_regions or [], ctx.bubble_detections)
                        ctx._bubble_detection_done = True
                    else:
                        bubble_data = []
                    font_path = (
                        getattr(translator, "font_path", None)
                        or getattr(getattr(config, "render", None), "font_path", None)
                    )
                    if not font_path:
                        from ..rendering import get_default_eng_font
                        font_path = get_default_eng_font()
                    mask_path = self.path / "mask_final.png"
                    if mask_path.is_file():
                        ctx.inpaint_mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
                    input_fingerprints = layout_data.get("input_fingerprints", {}) if isinstance(layout_data, dict) else {}
                    inputs = layout_input_fingerprints(
                        ctx.text_regions or [], config, font_path, bubble_data,
                        getattr(ctx.img_rgb, "shape", None),
                        getattr(ctx, "inpaint_mask", None),
                        input_fingerprints.get("mask") if getattr(ctx, "inpaint_mask", None) is None else None,
                    )
                    hydrate_layout(ctx, layout_data, inputs["fingerprint"])
                    ctx._bubble_detection_done = True
                    ctx._bubble_layout_ready = True
                ctx.img_rendered = await translator._run_text_rendering(config, ctx)
                ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
                self.write_json("text_regions.json", serialize_editor_regions(ctx.text_regions))
                final_img = np.array(ctx.result)
                save_jpeg(final_img, self.path / "final.jpg")

            self._finish(stage_id, "completed")
            await self.checkpoint()
            return {
                "status": "ok",
                "stage": stage_id,
                "durationMs": stage.get("durationMs", 0),
                "manifest": self.manifest,
            }
        except Exception as exc:
            self._finish(stage_id, "failed", str(exc))
            await self.checkpoint()
            raise

    async def retry_from_stage(
        self,
        stage_id: str,
        new_config: dict | Config,
        translator,
    ) -> dict[str, Any]:
        self.manifest["createdAt"] = _now()
        stage_ids = [stage["id"] for stage in self.manifest["stages"]]
        if stage_id == "colorization":
            selected = PipelineStage.COLORIZATION
        else:
            canonical_id = {
                "upscale": PipelineStage.UPSCALE.value,
                "upscaling": PipelineStage.UPSCALE.value,
                "text_grouping": PipelineStage.TEXT_GROUPING.value,
                "textline_merge": PipelineStage.TEXT_GROUPING.value,
            }.get(stage_id, stage_id)
            try:
                selected = PipelineStage(canonical_id)
            except ValueError as exc:
                raise ValueError(f"Stage '{stage_id}' is not retryable") from exc
        retry_ids = [
            CHECKPOINT_STAGE_IDS.get(stage.value, stage.value)
            for stage in downstream_stages(selected)
        ]
        retry_ids = list(dict.fromkeys(
            candidate for candidate in retry_ids
            if candidate in stage_ids and candidate != "input"
        ))
        result = None
        for current_stage in retry_ids:
            if self._stage(current_stage).get("status") == "skipped":
                continue
            result = await self.retry_stage(current_stage, new_config, translator)
        self.manifest["status"] = "completed"
        self.manifest.pop("error", None)
        self.manifest.pop("waitingFor", None)
        self._write()
        await self.checkpoint()
        return result or {"status": "ok", "stage": stage_id, "manifest": self.manifest}
