"""OCR stage calls used by MangaTranslator."""

import os
import time

from .config import Config
from .utils import Context


async def run_ocr(owner, config: Config, ctx: Context, *, dispatch_ocr, finish_textlines):
    current_time = time.time()
    owner._model_usage_timestamps[("ocr", config.ocr.ocr)] = current_time

    if owner.verbose:
        image_subfolder = owner._get_image_subfolder()
        if image_subfolder:
            if owner.result_sub_folder:
                ocr_result_dir = os.path.join(owner.result_root, owner.result_sub_folder, image_subfolder, "ocrs")
            else:
                ocr_result_dir = os.path.join(owner.result_root, image_subfolder, "ocrs")
            os.makedirs(ocr_result_dir, exist_ok=True)
        else:
            ocr_result_dir = os.path.join(owner.result_root, owner.result_sub_folder, "ocrs")
            os.makedirs(ocr_result_dir, exist_ok=True)
    else:
        ocr_result_dir = None

    old_ocr_dir = os.environ.get("MANGA_OCR_RESULT_DIR")
    if ocr_result_dir:
        os.environ["MANGA_OCR_RESULT_DIR"] = ocr_result_dir

    try:
        textlines = await owner._mps_call(
            dispatch_ocr,
            config.ocr.ocr,
            ctx.img_rgb,
            ctx.textlines,
            config.ocr,
            owner.device,
            owner.verbose,
        )
    finally:
        if old_ocr_dir is not None:
            os.environ["MANGA_OCR_RESULT_DIR"] = old_ocr_dir
        elif "MANGA_OCR_RESULT_DIR" in os.environ:
            del os.environ["MANGA_OCR_RESULT_DIR"]

    return finish_textlines(textlines, config, ctx)


async def run_ocr_batch(
    owner,
    pages: list[tuple[Context, Config]],
    *,
    dispatch_ocr_batch,
    finish_textlines,
    logger,
):
    if not pages:
        return []
    if len({config.ocr.ocr for _, config in pages}) != 1:
        return [await owner._run_ocr(config, ctx) for ctx, config in pages]
    logger.info(
        "OCR model batch pages=%d backend=%s device=%s",
        len(pages), pages[0][1].ocr.ocr, owner.device,
    )
    for _, config in pages:
        owner._model_usage_timestamps[("ocr", config.ocr.ocr)] = time.time()
    outputs = await owner._mps_call(
        dispatch_ocr_batch,
        pages[0][1].ocr.ocr,
        [(ctx.img_rgb, ctx.textlines, config.ocr) for ctx, config in pages],
        owner.device,
        owner.verbose,
    )
    if len(outputs) != len(pages):
        raise RuntimeError("OCR batch returned a different number of page results than inputs")
    return [
        finish_textlines(textlines, config, ctx)
        for (ctx, config), textlines in zip(pages, outputs)
    ]


def finish_ocr_textlines(textlines, config: Config, ctx: Context, *, analyze_typography):
    analyze_typography(textlines, ctx.img_rgb)
    new_textlines = []
    for textline in textlines:
        if textline.text.strip():
            if config.render.font_color_fg:
                textline.fg_r, textline.fg_g, textline.fg_b = config.render.font_color_fg
            if config.render.font_color_bg:
                textline.bg_r, textline.bg_g, textline.bg_b = config.render.font_color_bg
            new_textlines.append(textline)
    return new_textlines
