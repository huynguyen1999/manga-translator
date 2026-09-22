"""Canonical mask construction for inpainting and text erasure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Union

import cv2
import numpy as np

from .config import Config
from .rendering.bubble_layout import prepare_bubble_masks


@dataclass
class MaskBundle:
    """Structured bundle of all inpainting and erasure masks."""
    text_mask: np.ndarray
    detector_cleanup_mask: np.ndarray
    bubble_cleanup_mask: np.ndarray
    protected_edge_mask: np.ndarray
    final_inpaint_mask: np.ndarray


def build_detector_cleanup_mask(
    textlines: Optional[List[Any]],
    detector_mask: Optional[np.ndarray],
    image_shape: Tuple[int, ...],
) -> np.ndarray:
    """Keep detector pixels inside detector boxes even when OCR drops a box."""
    fallback = np.zeros(image_shape[:2], dtype=np.uint8)
    if detector_mask is None or not textlines or not np.size(detector_mask):
        return fallback

    geometry = np.zeros_like(fallback)
    for textline in textlines:
        points = getattr(textline, "pts", None)
        if points is not None:
            cv2.fillPoly(geometry, [np.asarray(points, dtype=np.int32)], 255)

    mask = np.asarray(detector_mask)
    if mask.ndim == 3:
        mask = cv2.cvtColor(mask, cv2.COLOR_RGB2GRAY)
    if mask.shape != fallback.shape:
        mask = cv2.resize(mask, (fallback.shape[1], fallback.shape[0]), interpolation=cv2.INTER_NEAREST)
    mask = np.where(mask > 0, 255, 0).astype(np.uint8)
    return cv2.bitwise_and(mask, geometry)


async def build_inpaint_masks(
    image: np.ndarray,
    detector_textlines: Optional[List[Any]],
    detector_mask: Optional[np.ndarray],
    text_regions: List[Any],
    bubble_detections: Optional[List[Any]],
    config: Config,
    text_mask: Optional[np.ndarray] = None,
) -> MaskBundle:
    """Build canonical inpainting masks shared identically between Studio and dev runner."""
    image_shape = image.shape[:2] if image is not None else (100, 100)
    if image is None:
        empty = np.zeros(image_shape, dtype=np.uint8)
        return MaskBundle(
            text_mask=empty,
            detector_cleanup_mask=empty,
            bubble_cleanup_mask=empty,
            protected_edge_mask=empty,
            final_inpaint_mask=empty,
        )

    # 1. Text mask from refinement
    if text_mask is None:
        if text_regions:
            from .mask_refinement import dispatch as dispatch_mask_refinement
            dilation = getattr(config, "mask_dilation_offset", 20)
            kernel_size = getattr(config, "kernel_size", 3)
            ignore_bubble = getattr(getattr(config, "ocr", None), "ignore_bubble", 0)
            text_mask = await dispatch_mask_refinement(
                text_regions,
                image,
                detector_mask if detector_mask is not None else np.zeros(image_shape, dtype=np.uint8),
                dilation_offset=dilation,
                kernel_size=kernel_size,
                ignore_bubble=ignore_bubble,
            )
        else:
            text_mask = np.zeros(image_shape, dtype=np.uint8)

    # 2. Bubble cleanup mask and safe interior / protected edge attachment
    pad = int(getattr(getattr(config, "bubble_detection", None), "padding", 9))
    bubble_cleanup = prepare_bubble_masks(image, text_regions, padding=pad)

    # 3. Protected edge mask
    protected_edges = np.zeros(image_shape, dtype=np.uint8)
    for r in (text_regions or []):
        edge = getattr(r, "_bubble_protected_edge", None)
        if edge is not None and np.any(edge):
            protected_edges = cv2.bitwise_or(protected_edges, np.asarray(edge, dtype=np.uint8))

    # 4. Detector cleanup mask (erases detector boxes missed by OCR)
    detector_cleanup = build_detector_cleanup_mask(
        detector_textlines,
        detector_mask,
        image.shape,
    )

    # 5. Final inpaint mask composition: reference runner composition
    final_inpaint = np.maximum(text_mask, detector_cleanup)
    if np.any(protected_edges):
        final_inpaint[protected_edges > 0] = 0

    return MaskBundle(
        text_mask=text_mask,
        detector_cleanup_mask=detector_cleanup,
        bubble_cleanup_mask=bubble_cleanup,
        protected_edge_mask=protected_edges,
        final_inpaint_mask=final_inpaint,
    )
