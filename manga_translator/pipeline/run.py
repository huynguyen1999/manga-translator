"""Pipeline stage manifests and serialized result documents."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from ..config import Config
from ..mask_builder import build_inpaint_masks
from .stages import PipelineStage, downstream_stages
from .serialization import (
    _json_default,
    deserialize_textblocks,
    deserialize_textlines,
    serialize_editor_regions,
    serialize_regions,
)
from .cpu import CPU_PRIORITY_BACKGROUND, run_cpu_stage
from ..utils import Context, dump_image, load_image
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
    "textline_merge": ("text_regions_merged.json", "textline_merge_debug.json"),
    "bubble_detection": ("bubble_mask.png", "bubble_detections.json"),
    "translation": ("translations.json", "translation_detail.json"),
    "layout": ("layout.json",),
    "mask_generation": (
        "text_mask.png", "bubble_mask.png", "detector_rescue_mask.png",
        "bubble_residual_mask.png", "protected_bubble_edge.png", "mask_final.png",
        "inpaint_mask.png", "profiling.json",
    ),
    "inpainting": ("inpainted.jpg", "inpainted.png"),
    "rendering": ("final.jpg", "final.png", "text_regions.json", "meta.json"),
}

logger = get_logger("pipeline")

CHECKPOINT_STAGE_IDS = {
    PipelineStage.UPSCALE.value: "upscaling",
    PipelineStage.TEXT_GROUPING.value: "textline_merge",
    PipelineStage.FINALIZE.value: "rendering",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        precomputed_inpainting: np.ndarray | None = None,
        stage_already_running: bool = False,
    ) -> dict[str, Any]:
        from .retry import execute_retry_stage

        return await execute_retry_stage(
            self,
            stage_id,
            new_config,
            translator,
            runtime=sys.modules[__name__],
            defer_bubble_detection=defer_bubble_detection,
            precomputed_ocr=precomputed_ocr,
            precomputed_upscale=precomputed_upscale,
            precomputed_detection=precomputed_detection,
            precomputed_bubbles=precomputed_bubbles,
            precomputed_inpainting=precomputed_inpainting,
            stage_already_running=stage_already_running,
        )

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
