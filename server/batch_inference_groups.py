"""Selection of compatible pages for batched model stages."""

from typing import Any

from manga_translator.config import Detector, Inpainter, Ocr
from manga_translator.pipeline.stages import fingerprint


def find_ocr_group(scheduler, batch: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compatible: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        item_id = item.get("id")
        if (
            item.get("status") != "queued"
            or item.get("pipelineStage") != "ocr"
            or not item_id
            or (batch["id"], item_id) in scheduler._running_items
        ):
            continue
        config = scheduler._config_for(batch, item)
        if config.ocr.ocr not in {Ocr.ocr48px, Ocr.ocr48px_ctc, Ocr.mocr}:
            continue
        key = fingerprint(config.ocr.dict())
        compatible.setdefault(key, []).append(item)
    group = next((pages for pages in compatible.values() if len(pages) > 1), [])
    return group[:scheduler._inference_page_limit("ocr")]


def find_page_inference_group(
    scheduler, batch: dict[str, Any], items: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]] | None:
    for stage_id in ("upscaling", "detection", "bubble_detection", "inpainting"):
        compatible: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            item_id = item.get("id")
            if (
                item.get("status") != "queued"
                or item.get("pipelineStage") != stage_id
                or not item_id
                or (batch["id"], item_id) in scheduler._running_items
            ):
                continue
            config = scheduler._config_for(batch, item)
            if stage_id == "bubble_detection":
                if not config.bubble_detection.enabled:
                    continue
                settings = config.bubble_detection.dict()
            elif stage_id == "inpainting":
                if config.inpainter.inpainter != Inpainter.default:
                    continue
                settings = config.inpainter.dict()
            elif stage_id == "detection":
                if config.detector.detector not in {Detector.default, Detector.dbconvnext}:
                    continue
                settings = config.detector.dict()
            else:
                settings = config.upscale.dict()
            compatible.setdefault(fingerprint(settings), []).append(item)
        group = next((pages for pages in compatible.values() if len(pages) > 1), [])
        if group:
            return stage_id, group[:scheduler._inference_page_limit(stage_id)]
    return None
