"""OCR and source-text helpers used by the manga summary workflow."""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import Request

from manga_translator.config import Config



def summary_target_language(pages: list[dict]) -> str:
    """Use the target saved on the naturally last page, as the UI does."""
    if pages and all(
        page.get("meta", {}).get("sourceType") == "original"
        for page in pages
    ):
        return "ENG"
    valid_languages = {
        "CHS", "CHT", "CSY", "NLD", "ENG", "FRA", "DEU", "HUN", "ITA", "JPN",
        "KOR", "POL", "PTB", "ROM", "RUS", "ESP", "TRK", "UKR", "VIN", "ARA",
        "CNR", "SRP", "HRV", "THA", "IND", "FIL",
    }
    if pages:
        settings = pages[-1].get("meta", {}).get("settings", {})
        if isinstance(settings, dict):
            value = settings.get("targetLanguage") or settings.get("target_lang")
            if isinstance(value, str) and value.strip():
                value = value.strip().upper()
                if value in valid_languages:
                    return value
    return "ENG"


def safe_setting(value, allowed: set[str], default: str) -> str:
    value = value.value if hasattr(value, "value") else value
    return value if isinstance(value, str) and value in allowed else default


def ocr_config(page: dict, target_language: str, safe_setting_fn) -> Config:
    from manga_translator.config import Detector, Inpainter, Ocr, Renderer, Translator

    settings = page.get("meta", {}).get("settings", {})
    settings = settings if isinstance(settings, dict) else {}
    detector = safe_setting_fn(
        settings.get("textDetector") or settings.get("detector"),
        {item.value for item in Detector},
        Detector.default.value,
    )
    ocr = safe_setting_fn(settings.get("ocr"), {item.value for item in Ocr}, Ocr.ocr48px_ctc.value)
    try:
        detection_size = max(256, min(8192, int(settings.get("detectionResolution", 2560))))
    except (TypeError, ValueError):
        detection_size = 2560
    try:
        box_threshold = float(settings.get("customBoxThreshold", 0.45))
    except (TypeError, ValueError):
        box_threshold = 0.45
    try:
        unclip_ratio = float(settings.get("customUnclipRatio", 2.3))
    except (TypeError, ValueError):
        unclip_ratio = 2.3

    source_type = page.get("meta", {}).get("sourceType")
    config = Config(
        original_name=page["name"],
        manga_title=page.get("meta", {}).get("mangaTitle"),
        detector={
            "detector": detector,
            "detection_size": detection_size,
            "box_threshold": box_threshold,
            "unclip_ratio": unclip_ratio,
        },
        ocr={"ocr": ocr},
        translator={
            "translator": Translator.original.value,
            "target_lang": "ENG" if source_type == "original" else target_language,
            "no_text_lang_skip": True,
        },
        render={"renderer": Renderer.none.value},
        inpainter={"inpainter": Inpainter.original.value},
        colorizer={"colorizer": "none"},
        upscale={"upscale_ratio": None},
    )
    config._web_frontend_optimized = False
    return config


def json_value(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    return value


def repaired_regions(ctx, json_value_fn=json_value) -> list[dict]:
    regions = []
    for index, region in enumerate(getattr(ctx, "text_regions", []) or []):
        xywh = json_value_fn(getattr(region, "xywh", None)) or [0, 0, 0, 0]
        x, y, width, height = (list(xywh) + [0, 0, 0, 0])[:4]
        text = str(getattr(region, "text", "") or "")
        regions.append({
            "id": f"bubble_{index}",
            "x": int(x),
            "y": int(y),
            "width": int(width),
            "height": int(height),
            "lines": json_value_fn(getattr(region, "lines", [])) or [],
            "original_text": text,
            "translation": text,
            "font_size": int(getattr(region, "font_size", 24) or 24),
            "font_family": "Comic Neue",
            "fg_color": [0, 0, 0],
            "bg_color": [255, 255, 255],
            "stroke_width": 2,
            "angle": float(getattr(region, "angle", 0) or 0),
            "direction": "h",
            "alignment": "center",
            "line_spacing": 1,
            "letter_spacing": 1,
            "bold": False,
            "italic": False,
        })
    return regions


def summary_input_file(page: dict, input_file_fn, final_file_fn) -> Path:
    page_path = page.get("path")
    input_path = input_file_fn(page_path) if isinstance(page_path, Path) else None
    if input_path is None or not input_path.is_file():
        input_path = final_file_fn(page_path) if isinstance(page_path, Path) else None
    if input_path is None or not input_path.is_file():
        raise FileNotFoundError("input image is missing")
    return input_path


async def persist_summary_ocr(page: dict, regions: list[dict], *, get_store) -> None:
    store = get_store()
    if store is not None and "id" in page:
        if not await store.update_text_regions(page["id"], regions):
            raise FileNotFoundError("page is missing from PostgreSQL")
    path = page.get("path")
    if isinstance(path, Path):
        path.mkdir(parents=True, exist_ok=True)
        regions_path = path / "text_regions.json"
        temporary = regions_path.with_suffix(".tmp")
        await asyncio.to_thread(
            temporary.write_text,
            json.dumps(regions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await asyncio.to_thread(os.replace, temporary, regions_path)


async def run_summary_ocr(
    request: Request,
    page: dict,
    target_language: str,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
    worker=None,
    overwrite: bool = False,
    *,
    get_context,
    ocr_config_fn,
    input_file_fn,
    repaired_regions_fn,
    persist_regions,
) -> list[dict]:
    input_path = input_file_fn(page)
    image_bytes = await asyncio.to_thread(input_path.read_bytes)
    progress_queue: asyncio.Queue[str | None] | None = asyncio.Queue() if on_progress else None
    consumer: asyncio.Task[None] | None = None
    if progress_queue is not None and on_progress is not None:
        async def consume_progress() -> None:
            while True:
                stage = await progress_queue.get()
                if stage is None:
                    return
                await on_progress(stage)

        consumer = asyncio.create_task(consume_progress())

    def report_progress(stage: str) -> None:
        if progress_queue is not None:
            progress_queue.put_nowait(stage)

    ctx = None
    try:
        if worker is None:
            ctx = await get_context(
                request,
                ocr_config_fn(page, target_language),
                image_bytes,
                report_progress if progress_queue is not None else None,
            )
        else:
            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as opened:
                image = opened.convert("RGB")
            try:
                extract_text = getattr(worker, "extract_text", None)
                if extract_text is None:
                    ctx = await worker.sent(image, ocr_config_fn(page, target_language))
                else:
                    ctx = await extract_text(image, ocr_config_fn(page, target_language))
            finally:
                image.close()
                del image
    finally:
        del image_bytes
        if progress_queue is not None and consumer is not None:
            progress_queue.put_nowait(None)
            await consumer
    regions = repaired_regions_fn(ctx)
    if hasattr(ctx, 'cleanup_all_images'):
        ctx.cleanup_all_images()
    del ctx
    await persist_regions(page, regions)
    return regions


async def run_summary_ocr_batch(
    pages: list[dict],
    target_language: str,
    worker,
    batch_size: int,
    on_progress: Callable[[str, int], Awaitable[None]] | None = None,
    *,
    ocr_config_fn,
    input_file_fn,
    repaired_regions_fn,
    persist_regions,
) -> list[tuple[list[dict] | None, Exception | None]]:
    from PIL import Image

    images = []
    contexts = []
    try:
        for page in pages:
            image_bytes = await asyncio.to_thread(input_file_fn(page).read_bytes)
            with Image.open(io.BytesIO(image_bytes)) as opened:
                images.append(opened.convert("RGB"))
            del image_bytes
        configs = [ocr_config_fn(page, target_language) for page in pages]
        contexts = await worker.extract_text_batch(
            list(zip(images, configs)), batch_size=batch_size, on_progress=on_progress
        )
        if len(contexts) != len(pages):
            raise RuntimeError("Batched summary OCR returned a different number of page results than inputs")

        results = []
        for page, ctx in zip(pages, contexts):
            try:
                regions = repaired_regions_fn(ctx)
                await persist_regions(page, regions)
                results.append((regions, None))
            except Exception as error:
                results.append((None, error))
        return results
    finally:
        for ctx in contexts:
            if hasattr(ctx, "cleanup_all_images"):
                ctx.cleanup_all_images()
        for image in images:
            image.close()
