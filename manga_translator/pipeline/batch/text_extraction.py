"""Batched detection, OCR, and text-region extraction."""

import json
from typing import Awaitable, Callable, List

from PIL import Image

from manga_translator.config import Colorizer, Config, Detector, Ocr
from manga_translator.rendering.paragraph_coalescing import coalesce_free_text_regions
from manga_translator.utils import Context, load_image


async def extract_text_batch(
    owner, images_with_configs: List[tuple[Image.Image, Config]], batch_size: int,
    on_progress: Callable[[str, int], Awaitable[None]] | None, *, load_dictionary_fn, apply_dictionary_fn,
) -> List[Context]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if not images_with_configs:
        return []

    contexts = []
    for image, config in images_with_configs:
        if config.colorizer.colorizer != Colorizer.none or config.upscale.upscale_ratio:
            raise ValueError("Batched text extraction expects colorization and upscaling to be disabled")
        ctx = Context(input=image, result_documents={}, textlines=[], text_regions=[])
        ctx.img_colorized = ctx.upscaled = image
        ctx.img_rgb, ctx.img_alpha = load_image(image)
        contexts.append(ctx)

    def groups(indices, section):
        grouped = {}
        for index in indices:
            key = json.dumps(getattr(images_with_configs[index][1], section).dict(), sort_keys=True, default=str)
            grouped.setdefault(key, []).append(index)
        return grouped.values()

    progress_counts = {"detection": 0, "ocr": 0, "textline_merge": 0}

    async def report(stage, count=1):
        for _ in range(count if on_progress is not None else 0):
            await on_progress(stage, progress_counts[stage])
            progress_counts[stage] += 1

    for indexes in groups(range(len(contexts)), "detector"):
        for start in range(0, len(indexes), batch_size):
            group = indexes[start:start + batch_size]
            configs = [images_with_configs[index][1] for index in group]
            pages = [contexts[index] for index in group]
            if len(group) > 1 and configs[0].detector.detector in {Detector.default, Detector.dbconvnext}:
                outputs = await owner._run_detection_batch(configs, pages)
            else:
                outputs = []
                for index, config, ctx in zip(group, configs, pages):
                    owner._set_image_context(config, images_with_configs[index][0])
                    outputs.append(await owner._run_detection(config, ctx))
            for ctx, output in zip(pages, outputs):
                ctx.textlines, ctx.mask_raw, ctx.mask = output
            await report("detection", len(group))

    recognized = [index for index, ctx in enumerate(contexts) if ctx.textlines]
    await report("ocr", len(contexts) - len(recognized))
    for indexes in groups(recognized, "ocr"):
        for start in range(0, len(indexes), batch_size):
            group = indexes[start:start + batch_size]
            configs = [images_with_configs[index][1] for index in group]
            pages = [contexts[index] for index in group]
            if len(group) > 1 and configs[0].ocr.ocr in {Ocr.ocr48px, Ocr.ocr48px_ctc, Ocr.mocr}:
                outputs = await owner._run_ocr_batch(list(zip(pages, configs)))
            else:
                outputs = []
                for index, config, ctx in zip(group, configs, pages):
                    owner._set_image_context(config, images_with_configs[index][0])
                    outputs.append(await owner._run_ocr(config, ctx))
            for ctx, textlines in zip(pages, outputs):
                ctx.textlines = textlines
            await report("ocr", len(group))

    pre_dict = load_dictionary_fn(owner.pre_dict)
    for ctx, (_, config) in zip(contexts, images_with_configs):
        if ctx.textlines:
            owner._set_image_context(config, ctx.input)
            ctx.text_regions = await owner._run_textline_merge(config, ctx)
            if ctx.text_regions:
                await owner._detect_speech_bubbles(config, ctx, report_progress=False)
                ctx.text_regions = coalesce_free_text_regions(ctx.text_regions, ctx.img_rgb)
                for region in ctx.text_regions:
                    region.text = apply_dictionary_fn(region.text, pre_dict)
        await report("textline_merge")
    return contexts
