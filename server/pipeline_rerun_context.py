"""Restore a saved page into the context required by a rerun plan."""

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from manga_translator.config import Config
from manga_translator.pipeline.run import deserialize_textblocks
from manga_translator.pipeline.stages import PipelineStage
from manga_translator.rendering.bubble_layout import restore_bubble_assignments
from manga_translator.utils import Context, load_image
from manga_translator.utils.image_storage import find_asset
from server.image_variants import final_file
from server.pipeline_rerun_metadata import restore_source_metadata
from server.pipeline_rerun_plan import PipelineRerunMode, PipelineRerunPlan


async def load_rerun_context(
    result_dir: Path,
    plan: PipelineRerunPlan,
    config: Config,
    database: Any = None,
    record_id: Optional[str] = None,
) -> Tuple[Context, Dict[str, Any]]:
    """Hydrate a Context object from persisted checkpoint artifacts according to the plan."""
    ctx = Context()
    folder = result_dir.name
    ctx.debug_folder = folder
    ctx.image_context = {
        "subfolder": folder,
        "file_md5": folder.split("-")[-1] if "-" in folder else folder,
    }
    ctx.result_documents = {}

    # Load documents from database if available, else from files
    documents: Dict[str, Any] = {}
    if database is not None:
        try:
            documents = await database.get_documents(folder) or {}
        except Exception:
            documents = {}
        try:
            target_id = record_id or folder
            pg_regions = await database.get_text_regions(target_id)
            if not pg_regions and record_id and record_id != folder:
                pg_regions = await database.get_text_regions(folder)
            if pg_regions:
                if "text_regions.json" not in documents:
                    documents["text_regions.json"] = pg_regions
                if "translations.json" not in documents:
                    documents["translations.json"] = pg_regions
        except Exception:
            pass

    def _read_json(name: str) -> Optional[Any]:
        if name in documents:
            return documents[name]
        p = result_dir / name
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    # 1. Canvas / Image loading
    if plan.use_original_input:
        orig_path = (
            find_asset(result_dir, "input")
            or find_asset(result_dir, "original_canvas")
            or find_asset(result_dir, "inpainted")
            or final_file(result_dir)
        )
        if orig_path is None:
            raise RuntimeError("Original input image not found")
        with Image.open(orig_path) as img:
            ctx.input = img.convert("RGB")
            ctx.img_rgb, ctx.img_alpha = load_image(ctx.input)
    else:
        # Reprocess / typesetting / translation rerun
        upscaled_path = find_asset(result_dir, "upscaled") if plan.reuse_upscaled_canvas else None
        orig_path = (
            upscaled_path
            or find_asset(result_dir, "original_canvas")
            or find_asset(result_dir, "input")
            or find_asset(result_dir, "inpainted")
        )
        if orig_path is None:
            raise RuntimeError("Base canvas image not found")
        with Image.open(orig_path) as img:
            ctx.input = img.convert("RGB")
            ctx.img_rgb, ctx.img_alpha = load_image(ctx.input)

    # 2. Inpainted canvas & masks if skipping inpainting
    if not plan.run_inpainting:
        inpainted_path = find_asset(result_dir, "inpainted")
        if inpainted_path:
            with Image.open(inpainted_path) as img:
                ctx.img_inpainted = np.array(img.convert("RGB"))

        inpaint_mask_path = result_dir / "inpaint_mask.png"
        mask_final_path = result_dir / "mask_final.png"
        if inpaint_mask_path.is_file():
            ctx.inpaint_mask = cv2.imread(str(inpaint_mask_path), cv2.IMREAD_GRAYSCALE)
        elif mask_final_path.is_file():
            ctx.inpaint_mask = cv2.imread(str(mask_final_path), cv2.IMREAD_GRAYSCALE)
        ctx.mask = ctx.inpaint_mask
    # 3. Speech bubble detections
    bubble_dets_raw = _read_json("bubble_detections.json")
    if bubble_dets_raw:
        try:
            from manga_translator.detection.bubble import deserialize_bubble_detections
            ctx.bubble_detections = deserialize_bubble_detections(bubble_dets_raw, ctx.img_rgb.shape)
            ctx._bubble_detection_done = True
        except Exception:
            pass

    bubble_mask_path = result_dir / "bubble_mask.png"
    if bubble_mask_path.is_file():
        ctx.bubble_mask = cv2.imread(str(bubble_mask_path), cv2.IMREAD_GRAYSCALE)
    old_translations_doc = (
        _read_json("translations.json")
        or _read_json("text_regions.json")
        or _read_json("text_regions_merged.json")
    )
    old_regions = deserialize_textblocks(old_translations_doc or [])
    if plan.mode == PipelineRerunMode.TYPESETTING:
        saved_regions = _read_json("text_regions.json") or old_translations_doc
        if not saved_regions:
            raise RuntimeError("No saved text regions available for typesetting")
        ctx.text_regions = deserialize_textblocks(saved_regions)
        restore_source_metadata(ctx.text_regions, old_regions)
        for region in ctx.text_regions:
            if not getattr(region, "target_lang", None):
                region.target_lang = config.translator.target_lang
    elif plan.mode == PipelineRerunMode.TRANSLATION_TYPESETTING:
        # Load merged regions or OCR regions as translatable units
        merged_doc = (
            _read_json("text_regions_merged.json")
            or _read_json("ocr.json")
            or _read_json("text_regions.json")
        )
        if not merged_doc:
            raise RuntimeError("No saved OCR / merged regions available for translation")
        ctx.text_regions = deserialize_textblocks(merged_doc)
        for region in ctx.text_regions:
            region.translation = ""
            if not getattr(region, "target_lang", None):
                region.target_lang = config.translator.target_lang

    if ctx.text_regions and ctx.bubble_detections:
        restore_bubble_assignments(ctx.text_regions, ctx.bubble_detections)

    return ctx, {"old_regions": old_regions, "documents": documents}
