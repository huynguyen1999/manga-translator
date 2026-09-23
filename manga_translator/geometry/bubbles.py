"""Bubble geometry shared by mask construction and layout."""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import cv2
import numpy as np


@dataclass
class PreparedBubbleGeometry:
    """Bubble masks cropped to one source bubble's bounding box."""

    bubble_id: int
    bbox: Tuple[int, int, int, int]
    mask: np.ndarray
    interior: np.ndarray
    protected_edge: np.ndarray
    cleanup_mask: np.ndarray
    center: Tuple[int, int]
    safe_bbox: Tuple[int, int, int, int]
    interior_page: np.ndarray


@dataclass
class PageGeometry:
    """All detector bubbles prepared once for one page."""

    image_id: int
    image_shape: Tuple[int, int]
    padding: int
    protected_edge_mask: np.ndarray
    bubbles: Dict[int, PreparedBubbleGeometry] = field(default_factory=dict)
    region_bubbles: Dict[int, int] = field(default_factory=dict)

    def matches(self, image: np.ndarray, regions: List[object], padding: int) -> bool:
        if self.image_id != id(image) or self.image_shape != image.shape[:2] or self.padding != int(padding):
            return False
        current = {
            id(region): id(mask)
            for region in (regions or [])
            if (mask := getattr(region, "_bubble_mask", None)) is not None
        }
        return current == self.region_bubbles


def prepare_page_geometry(
    image: np.ndarray,
    regions: List[object],
    padding: int = 9,
    *,
    return_cleanup: bool = True,
    profile=None,
):
    """Prepare each bubble once; keep geometry crops local and share safe interiors."""
    image_shape = image.shape[:2]
    geometry = PageGeometry(
        id(image), image_shape, int(padding),
        protected_edge_mask=np.zeros(image_shape, dtype=np.uint8),
    )
    combined = np.zeros(image_shape, np.uint8) if return_cleanup else None
    gray = None
    prepared = {}
    contour_count = distance_transform_count = 0

    for region in regions or []:
        mask = getattr(region, "_bubble_mask", None)
        if mask is None:
            continue
        key = id(mask)
        geometry.region_bubbles[id(region)] = key
        if key in prepared:
            bubble = prepared[key]
            if bubble is None:
                continue
        else:
            if not np.any(mask):
                prepared[key] = None
                continue
            if gray is None:
                gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            bx, by, bw, bh = cv2.boundingRect(np.asarray(mask))
            halo = max(8, int(padding))
            x0, y0 = max(0, bx - halo), max(0, by - halo)
            x1 = min(image_shape[1], bx + bw + halo)
            y1 = min(image_shape[0], by + bh + halo)
            component = (np.asarray(mask)[y0:y1, x0:x1] > 0).astype(np.uint8)
            contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            contour_count += len(contours)
            restore = np.zeros_like(component)
            cv2.drawContours(restore, contours, -1, 1, cv2.FILLED)
            interior = cv2.erode(restore, np.ones((max(3, padding), max(3, padding)), np.uint8))
            if not np.any(interior):
                prepared[key] = None
                continue

            search_size = max(3, min(15, int(padding) | 1))
            search_band = restore - cv2.erode(restore, np.ones((search_size, search_size), np.uint8))
            outline = (((gray[y0:y1, x0:x1] < 220) & (search_band > 0)).astype(np.uint8) * 255)
            protected_edge = cv2.dilate(
                outline,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            )
            x, y, w, h = cv2.boundingRect(interior)
            dist = cv2.distanceTransform(interior.astype(np.uint8), cv2.DIST_L2, 5)
            distance_transform_count += 1
            if np.any(dist):
                cy, cx = np.unravel_index(int(np.argmax(dist)), dist.shape)
                center = (int(cx + x0), int(cy + y0))
            else:
                center = ((x + x + w) // 2 + x0, (y + y + h) // 2 + y0)
            cleanup = (interior * 255).astype(np.uint8)
            bbox = (x0, y0, x1, y1)
            interior_page = np.zeros(image_shape, dtype=np.uint8)
            interior_page[y0:y1, x0:x1] = interior
            bubble = PreparedBubbleGeometry(
                bubble_id=key,
                bbox=bbox,
                mask=component.copy(),
                interior=interior.copy(),
                protected_edge=protected_edge.copy(),
                cleanup_mask=cleanup.copy(),
                center=center,
                safe_bbox=(x + x0, y + y0, x + w + x0, y + h + y0),
                interior_page=interior_page,
            )
            prepared[key] = bubble
            geometry.bubbles[key] = bubble
            edge_crop = geometry.protected_edge_mask[y0:y1, x0:x1]
            cv2.bitwise_or(edge_crop, protected_edge, dst=edge_crop)
            if return_cleanup:
                cleanup_crop = combined[y0:y1, x0:x1]
                cv2.bitwise_or(cleanup_crop, bubble.cleanup_mask, dst=cleanup_crop)

        region._bubble_interior = bubble.interior_page
        region.bubble_bounds = list(bubble.safe_bbox)
        region.layout_bounds = list(region.bubble_bounds)
        region._bubble_center = bubble.center
        if hasattr(region, "_bubble_geometry_cache"):
            delattr(region, "_bubble_geometry_cache")

    if profile is not None:
        workload = profile.setdefault("workload", {})
        workload["unique_bubble_contours"] = contour_count
        workload["distance_transform_calls"] = distance_transform_count
        workload["unique_bubble_count"] = len(geometry.bubbles)
    if return_cleanup:
        combined[geometry.protected_edge_mask > 0] = 0
    return geometry, combined


def compose_bubble_cleanup(page_geometry: PageGeometry, shape: Tuple[int, int]) -> np.ndarray:
    """Compose the compatibility cleanup mask from prepared bubble crops."""
    combined = np.zeros(shape, np.uint8)
    for bubble in page_geometry.bubbles.values():
        x1, y1, x2, y2 = bubble.bbox
        combined[y1:y2, x1:x2] = cv2.bitwise_or(
            combined[y1:y2, x1:x2], bubble.cleanup_mask
        )
    combined[page_geometry.protected_edge_mask > 0] = 0
    return combined
