"""Canonical mask construction for inpainting and text erasure with residual recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

from .config import Config
from .rendering.bubble_layout import prepare_bubble_masks


@dataclass
class MaskMetrics:
    """Quality and diagnostic metrics for mask construction."""
    known_text_coverage: float = 1.0
    detector_rescue_pixels: int = 0
    bubble_residual_pixels: int = 0
    protected_edge_violations: int = 0
    residual_candidate_pixels_rejected: int = 0
    source_text_ink_coverage: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "known_text_coverage": float(self.known_text_coverage),
            "detector_rescue_pixels": int(self.detector_rescue_pixels),
            "bubble_residual_pixels": int(self.bubble_residual_pixels),
            "protected_edge_violations": int(self.protected_edge_violations),
            "residual_candidate_pixels_rejected": int(self.residual_candidate_pixels_rejected),
            "source_text_ink_coverage": float(self.source_text_ink_coverage),
        }


@dataclass
class MaskBundle:
    """Structured bundle of all inpainting and erasure masks."""
    text_mask: np.ndarray
    detector_cleanup_mask: np.ndarray
    bubble_cleanup_mask: np.ndarray
    protected_edge_mask: np.ndarray
    final_inpaint_mask: np.ndarray
    bubble_residual_mask: np.ndarray = field(default=None)
    metrics: Optional[MaskMetrics] = None

    def __post_init__(self):
        if self.bubble_residual_mask is None:
            self.bubble_residual_mask = np.zeros_like(self.final_inpaint_mask)

    @property
    def detector_rescue_mask(self) -> np.ndarray:
        return self.detector_cleanup_mask


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
        if points is None and hasattr(textline, "lines"):
            points = textline.lines
        if points is not None:
            cv2.fillPoly(geometry, [np.asarray(points, dtype=np.int32)], 255)

    mask = np.asarray(detector_mask)
    if mask.ndim == 3:
        mask = cv2.cvtColor(mask, cv2.COLOR_RGB2GRAY)
    if mask.shape != fallback.shape:
        mask = cv2.resize(mask, (fallback.shape[1], fallback.shape[0]), interpolation=cv2.INTER_NEAREST)
    mask = np.where(mask > 0, 255, 0).astype(np.uint8)
    return cv2.bitwise_and(mask, geometry)


build_detector_rescue_mask = build_detector_cleanup_mask


def recover_bubble_residual_text(
    image: np.ndarray,
    text_regions: List[Any],
    bubble_detections: Optional[List[Any]],
    text_mask: np.ndarray,
    detector_rescue_mask: np.ndarray,
    protected_edges: np.ndarray,
    config: Optional[Config] = None,
) -> Tuple[np.ndarray, int]:
    """
    Recover text-like ink (such as un-OCRed Japanese punctuation !?, kana fragments,
    antialiased edges) inside speech bubbles using dual global/adaptive thresholding,
    orientation-aware text envelopes, and connected-component filtering.

    Returns:
        Tuple[bubble_residual_mask, rejected_candidate_pixels_count]
    """
    image_shape = image.shape[:2]
    residual_mask = np.zeros(image_shape, dtype=np.uint8)
    rejected_pixels = 0

    if image is None or not np.size(image):
        return residual_mask, rejected_pixels

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image.copy()

    # Pre-build known seed (text mask + detector rescue + textline geometry)
    known_seed = np.bitwise_or(
        np.where(text_mask > 0, 255, 0).astype(np.uint8),
        np.where(detector_rescue_mask > 0, 255, 0).astype(np.uint8),
    )
    for region in (text_regions or []):
        lines = getattr(region, "lines", None)
        if lines is not None:
            for line_pts in lines:
                cv2.fillPoly(known_seed, [np.asarray(line_pts, dtype=np.int32)], 255)

    # Process each region with speech bubble association
    processed_interiors = set()
    for region in (text_regions or []):
        interior = getattr(region, "_bubble_interior", None)
        if interior is None or not np.any(interior):
            mask = getattr(region, "_bubble_mask", None)
            if mask is not None and np.any(mask):
                component = (np.asarray(mask) > 0).astype(np.uint8)
                contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                restore = np.zeros_like(component)
                cv2.drawContours(restore, contours, -1, 1, cv2.FILLED)
                pad_size = int(getattr(getattr(config, "bubble_detection", None), "padding", 9))
                pad_size = max(3, pad_size)
                interior = cv2.erode(restore, np.ones((pad_size, pad_size), np.uint8))
        if interior is None or not np.any(interior):
            continue

        interior_bytes = interior.tobytes()
        if interior_bytes in processed_interiors:
            continue
        processed_interiors.add(interior_bytes)

        safe_interior = (interior > 0).astype(np.uint8)
        # Exclude protected bubble edges from safe search area
        if np.any(protected_edges):
            safe_interior = cv2.bitwise_and(safe_interior, (protected_edges == 0).astype(np.uint8))

        if not np.any(safe_interior):
            continue

        interior_pixels = gray[safe_interior > 0]
        mean_brightness = float(np.mean(interior_pixels)) if len(interior_pixels) else 255.0
        std_brightness = float(np.std(interior_pixels)) if len(interior_pixels) else 0.0

        # Mode classification: clean white/light bubble vs textured/artwork bubble
        is_clean_bubble = (mean_brightness >= 225.0 and std_brightness <= 45.0)

        # 1. Dual Thresholding for Candidate Foreground Ink
        # Global threshold
        dark_thresh = 220 if is_clean_bubble else 200
        dark_global = (gray < dark_thresh).astype(np.uint8) * 255

        # Adaptive thresholding for antialiased gray edges and thin strokes
        block_size = 21
        c_val = 5 if is_clean_bubble else 8
        dark_adaptive = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block_size, c_val
        )

        candidate_ink = cv2.bitwise_or(dark_global, dark_adaptive)
        candidate_ink = cv2.bitwise_and(candidate_ink, (safe_interior * 255).astype(np.uint8))

        # 2. Derive glyph geometry & Orientation-Aware Text Envelope
        font_size = getattr(region, "source_font_size", None) or getattr(region, "font_size", 0)
        if font_size <= 0:
            lines = getattr(region, "lines", None)
            if lines is not None and len(lines) > 0:
                line_heights = []
                line_widths = []
                for lp in lines:
                    arr = np.asarray(lp, dtype=np.int32)
                    x, y, w, h = cv2.boundingRect(arr)
                    line_widths.append(w)
                    line_heights.append(h)
                direction_hint = getattr(region, "direction", "auto")
                if direction_hint == "v" or (direction_hint == "auto" and max(line_heights or [0]) > max(line_widths or [0])):
                    font_size = float(np.median(line_widths)) if line_widths else 24.0
                else:
                    font_size = float(np.median(line_heights)) if line_heights else 24.0
            else:
                font_size = 24.0
        glyph_h = max(12.0, float(font_size))

        direction = getattr(region, "direction", "auto")
        if direction == "auto":
            # Inspect region lines aspect ratio
            region_lines = getattr(region, "lines", None)
            if region_lines is not None and len(region_lines):
                total_h = max([cv2.boundingRect(np.asarray(lp, dtype=np.int32))[3] for lp in region_lines])
                total_w = max([cv2.boundingRect(np.asarray(lp, dtype=np.int32))[2] for lp in region_lines])
                direction = "v" if total_h >= 1.2 * total_w else "h"
            else:
                direction = "v"

        # Orientation-aware dilation kernel for text envelope
        if direction == "v":
            ky = int(round(glyph_h * 1.6))
            kx = int(round(glyph_h * 0.45))
        else:
            kx = int(round(glyph_h * 1.6))
            ky = int(round(glyph_h * 0.45))

        ky = max(3, ky | 1)
        kx = max(3, kx | 1)
        envelope_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky))

        # Local seed for this bubble
        local_seed = cv2.bitwise_and(known_seed, (safe_interior * 255).astype(np.uint8))
        if not np.any(local_seed):
            # If no seed inside safe interior, seed with textline points of region
            lines = getattr(region, "lines", None)
            if lines is not None:
                for lp in lines:
                    cv2.fillPoly(local_seed, [np.asarray(lp, dtype=np.int32)], 255)
                local_seed = cv2.bitwise_and(local_seed, (safe_interior * 255).astype(np.uint8))

        text_envelope = cv2.dilate(local_seed, envelope_kernel)
        text_envelope = cv2.bitwise_and(text_envelope, (safe_interior * 255).astype(np.uint8))

        # 3. Connected Component Feature Analysis & Filtering
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            (candidate_ink > 0).astype(np.uint8), connectivity=8
        )

        safe_interior_area = float(np.count_nonzero(safe_interior))
        max_component_area = min(0.45 * safe_interior_area, 6.0 * (glyph_h ** 2))

        bubble_recovered = np.zeros(image_shape, dtype=np.uint8)

        for label_idx in range(1, num_labels):
            x, y, w, h, area = stats[label_idx]
            cx, cy = centroids[label_idx]

            comp_mask = (labels == label_idx).astype(np.uint8)

            # Hard reject: intersects protected bubble edges
            if np.any(protected_edges) and np.any(np.bitwise_and(comp_mask, (protected_edges > 0).astype(np.uint8))):
                rejected_pixels += area
                continue

            # Hard reject: touches image boundaries
            if x == 0 or y == 0 or (x + w) >= image_shape[1] or (y + h) >= image_shape[0]:
                rejected_pixels += area
                continue

            # Hard reject: excessively large component (artwork intrusion)
            if area > max_component_area:
                # Unless it heavily overlaps known seed
                seed_overlap = np.count_nonzero(np.bitwise_and(comp_mask, (local_seed > 0).astype(np.uint8)))
                if seed_overlap < 0.3 * area:
                    rejected_pixels += area
                    continue

            # Check overlap with seed and envelope
            is_in_seed = np.any(np.bitwise_and(comp_mask, (local_seed > 0).astype(np.uint8)))
            is_in_envelope = np.any(np.bitwise_and(comp_mask, (text_envelope > 0).astype(np.uint8)))

            if is_in_seed:
                bubble_recovered = cv2.bitwise_or(bubble_recovered, (comp_mask * 255).astype(np.uint8))
            elif is_in_envelope:
                if is_clean_bubble:
                    # Clean bubble: accept punctuation, kana fragments, and small kanji strokes
                    bubble_recovered = cv2.bitwise_or(bubble_recovered, (comp_mask * 255).astype(np.uint8))
                else:
                    # Textured bubble: conservative check (moderate aspect ratio and size)
                    aspect = max(w / max(1, h), h / max(1, w))
                    if area <= 2.5 * (glyph_h ** 2) and aspect <= 6.0:
                        bubble_recovered = cv2.bitwise_or(bubble_recovered, (comp_mask * 255).astype(np.uint8))
                    else:
                        rejected_pixels += area
            else:
                rejected_pixels += area

        # 4. Post-Mask Residual Sanity Check (controlled second-chance expansion)
        current_bubble_mask = cv2.bitwise_or(local_seed, bubble_recovered)
        remaining_ink = cv2.bitwise_and(candidate_ink, cv2.bitwise_not(current_bubble_mask))
        if is_clean_bubble and np.any(remaining_ink):
            num_rem, labels_rem, stats_rem, centroids_rem = cv2.connectedComponentsWithStats(
                (remaining_ink > 0).astype(np.uint8), connectivity=8
            )
            # Find closest distance to current_bubble_mask
            dist_map = cv2.distanceTransform((cv2.bitwise_not(current_bubble_mask) > 0).astype(np.uint8), cv2.DIST_L2, 5)
            for l_idx in range(1, num_rem):
                rx, ry, rw, rh, rarea = stats_rem[l_idx]
                rcx, rcy = centroids_rem[l_idx]
                rc_mask = (labels_rem == l_idx).astype(np.uint8)

                # Distance from current mask
                min_d = float(dist_map[int(round(rcy)), int(round(rcx))])
                if min_d <= 1.25 * glyph_h and rarea <= 1.5 * (glyph_h ** 2) and rarea >= 4:
                    if not (np.any(protected_edges) and np.any(np.bitwise_and(rc_mask, (protected_edges > 0).astype(np.uint8)))):
                        bubble_recovered = cv2.bitwise_or(bubble_recovered, (rc_mask * 255).astype(np.uint8))

        # Modest glyph-size-relative morphological smoothing for the recovered residual text
        res_r = max(2, int(round(glyph_h * 0.12)))
        res_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (res_r * 2 + 1, res_r * 2 + 1))
        bubble_recovered = cv2.dilate(bubble_recovered, res_kernel)
        bubble_recovered = cv2.bitwise_and(bubble_recovered, (safe_interior * 255).astype(np.uint8))

        residual_mask = cv2.bitwise_or(residual_mask, bubble_recovered)

    # Strictly protect bubble outer borders
    if np.any(protected_edges):
        residual_mask[protected_edges > 0] = 0

    return residual_mask, rejected_pixels


def create_mask_sources_overlay(
    img_rgb: np.ndarray,
    bundle: MaskBundle,
) -> np.ndarray:
    """Create a diagnostic comparison overlay showing all mask sources in distinct colors."""
    if img_rgb is None:
        return np.zeros((100, 100, 3), dtype=np.uint8)

    # Dim original image to provide clear background contrast
    base = img_rgb.copy().astype(np.float32)
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    base_gray = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB).astype(np.float32) * 0.55 + base * 0.45

    overlay = base_gray.copy()

    # Layer 1: Text Mask -> Green (0, 255, 0)
    if bundle.text_mask is not None and np.any(bundle.text_mask):
        mask_t = bundle.text_mask > 0
        overlay[mask_t] = overlay[mask_t] * 0.4 + np.array([0, 230, 80], dtype=np.float32) * 0.6

    # Layer 2: Detector Rescue Mask -> Cyan (0, 220, 255)
    if bundle.detector_cleanup_mask is not None and np.any(bundle.detector_cleanup_mask):
        mask_d = bundle.detector_cleanup_mask > 0
        overlay[mask_d] = overlay[mask_d] * 0.3 + np.array([0, 210, 255], dtype=np.float32) * 0.7

    # Layer 3: Bubble Residual Mask -> Magenta / Violet (255, 60, 220)
    if bundle.bubble_residual_mask is not None and np.any(bundle.bubble_residual_mask):
        mask_r = bundle.bubble_residual_mask > 0
        overlay[mask_r] = overlay[mask_r] * 0.2 + np.array([255, 50, 220], dtype=np.float32) * 0.8

    # Layer 4: Protected Bubble Edge -> Bright Red (255, 0, 0)
    if bundle.protected_edge_mask is not None and np.any(bundle.protected_edge_mask):
        mask_e = bundle.protected_edge_mask > 0
        overlay[mask_e] = overlay[mask_e] * 0.1 + np.array([255, 20, 20], dtype=np.float32) * 0.9

    result = np.clip(overlay, 0, 255).astype(np.uint8)

    # Draw final inpaint mask contour in white
    if bundle.final_inpaint_mask is not None and np.any(bundle.final_inpaint_mask):
        contours, _ = cv2.findContours(
            bundle.final_inpaint_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        cv2.drawContours(result, contours, -1, (255, 255, 255), 1)

    return result


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
            bubble_residual_mask=empty,
            metrics=MaskMetrics(),
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

    # 4. Detector rescue mask (erases detector boxes missed by OCR)
    detector_rescue = build_detector_cleanup_mask(
        detector_textlines,
        detector_mask,
        image.shape,
    )

    # 5. Speech bubble residual text recovery (recovers missed punctuation !?, kana, antialiased strokes)
    bubble_residual, rejected_candidate_pixels = recover_bubble_residual_text(
        image=image,
        text_regions=text_regions,
        bubble_detections=bubble_detections,
        text_mask=text_mask,
        detector_rescue_mask=detector_rescue,
        protected_edges=protected_edges,
        config=config,
    )

    # 6. Compose masks in fixed explicit order:
    # recovery layers union -> small gap closing -> modest text dilation -> protected edge zeroing
    final_inpaint = cv2.bitwise_or(text_mask, detector_rescue)
    final_inpaint = cv2.bitwise_or(final_inpaint, bubble_residual)
    final_inpaint = cv2.bitwise_or(final_inpaint, bubble_cleanup)

    # Close small internal gaps
    close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    final_inpaint = cv2.morphologyEx(final_inpaint, cv2.MORPH_CLOSE, close_k)

    # Modest text dilation relative to glyph size
    median_font_size = 20.0
    if text_regions:
        font_sizes = [
            getattr(r, "source_font_size", None) or getattr(r, "font_size", 0)
            for r in text_regions
            if (getattr(r, "source_font_size", None) or getattr(r, "font_size", 0)) > 0
        ]
        if font_sizes:
            median_font_size = float(np.median(font_sizes))

    dilate_r = max(1, min(4, int(round(median_font_size * 0.08))))
    if dilate_r > 0:
        dilate_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_r * 2 + 1, dilate_r * 2 + 1))
        final_inpaint = cv2.dilate(final_inpaint, dilate_k)

    # Compute protected edge violations before invariant zeroing
    edge_violations = int(np.count_nonzero(np.bitwise_and(final_inpaint > 0, protected_edges > 0)))

    # Hard invariant: protected bubble edge is never erased
    if np.any(protected_edges):
        final_inpaint[protected_edges > 0] = 0

    # Strict invariant validation assertion
    assert not np.any(np.logical_and(final_inpaint > 0, protected_edges > 0)), (
        "Invariant violated: final_inpaint_mask intersects protected_bubble_edge"
    )

    # Compute QA metrics
    known_ink_pixels = 0
    covered_known_ink = 0
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    for r in (text_regions or []):
        lines = getattr(r, "lines", None)
        if lines is not None:
            r_geom = np.zeros(image_shape, dtype=np.uint8)
            for lp in lines:
                cv2.fillPoly(r_geom, [np.asarray(lp, dtype=np.int32)], 255)
            r_ink = np.bitwise_and(gray < 220, r_geom > 0)
            total_r_ink = np.count_nonzero(r_ink)
            if total_r_ink > 0:
                known_ink_pixels += total_r_ink
                covered_known_ink += np.count_nonzero(np.bitwise_and(r_ink, final_inpaint > 0))

    source_ink_cov = float(covered_known_ink / max(1, known_ink_pixels)) if known_ink_pixels > 0 else 1.0

    metrics = MaskMetrics(
        known_text_coverage=source_ink_cov,
        detector_rescue_pixels=int(np.count_nonzero(detector_rescue)),
        bubble_residual_pixels=int(np.count_nonzero(bubble_residual)),
        protected_edge_violations=edge_violations,
        residual_candidate_pixels_rejected=rejected_candidate_pixels,
        source_text_ink_coverage=source_ink_cov,
    )

    return MaskBundle(
        text_mask=text_mask,
        detector_cleanup_mask=detector_rescue,
        bubble_cleanup_mask=bubble_cleanup,
        protected_edge_mask=protected_edges,
        final_inpaint_mask=final_inpaint,
        bubble_residual_mask=bubble_residual,
        metrics=metrics,
    )
