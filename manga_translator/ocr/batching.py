"""Cross-page OCR microbatches for the default CTC recognizer."""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import einops
import numpy as np
import torch

from ..utils import AvgMeter, Quadrilateral, chunks
from ..utils.bubble import is_ignore


def recognize_ctc_batch(ocr, pages: Sequence[tuple[np.ndarray, list, object]]):
    """Recognize several pages in shared 16-crop model calls, preserving page order."""
    text_height = 48
    outputs = []
    crops = []

    for page_index, (image, textlines, config) in enumerate(pages):
        lines = list(ocr._generate_text_direction(textlines))
        is_quadrilaterals = bool(lines) and isinstance(lines[0][0], Quadrilateral)
        outputs.append([] if is_quadrilaterals else textlines)
        for region, direction in lines:
            crop = region.get_transformed_region(image, direction, text_height)
            ignored = (
                1 <= config.ignore_bubble <= 50
                and is_ignore(crop, config.ignore_bubble)
            )
            crops.append((page_index, region, direction, crop, ignored, is_quadrilaterals))

    for indices in chunks(sorted(range(len(crops)), key=lambda i: crops[i][3].shape[1]), 16):
        widths = [crops[index][3].shape[1] for index in indices]
        max_width = (4 * (max(widths) + 7) // 4) + 128
        batch = np.zeros((len(indices), text_height, max_width, 3), dtype=np.uint8)
        for row, index in enumerate(indices):
            crop = crops[index][3]
            batch[row, :, : crop.shape[1], :] = crop

        images = torch.from_numpy(batch).float().sub_(127.5).div_(127.5)
        images = einops.rearrange(images, "N H W C -> N C H W")
        if ocr.use_gpu:
            images = images.to(ocr.device)
        with torch.inference_mode():
            decoded = ocr.model.decode(images, widths, 0, verbose=False)

        for row, single_line in enumerate(decoded):
            page_index, region, _, _, ignored, is_quadrilaterals = crops[indices[row]]
            if ignored or not single_line:
                continue
            characters = []
            metrics = [AvgMeter() for _ in range(7)]
            for char_id, logprob, *colors in single_line:
                character = ocr.model.dictionary[char_id]
                if character == "<SP>":
                    character = " "
                characters.append(character)
                metrics[0](logprob)
                if character != " ":
                    for meter, color in zip(metrics[1:], colors):
                        meter(int(color * 255))
            probability = np.exp(metrics[0]())
            threshold = 0.5 if pages[page_index][2].prob is None else pages[page_index][2].prob
            if probability < threshold:
                continue

            text = "".join(characters)
            foreground = [int(meter()) for meter in metrics[1:4]]
            background = [int(meter()) for meter in metrics[4:7]]
            if is_quadrilaterals:
                region.text = text
                region.prob = probability
                region.fg_r, region.fg_g, region.fg_b = foreground
                region.bg_r, region.bg_g, region.bg_b = background
                outputs[page_index].append(region)
            else:
                region.text.append(text)
                region.update_font_colors(np.array(foreground), np.array(background))

    return outputs
