"""Hard local placement geometry for free-text regions."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from .models import PageObstacleMap, PanelConstraint
from .source_profile import _effective_source_font_size





def _build_free_text_placement_domain(
    region: Any,
    source_mask: np.ndarray,
    damage_mask: np.ndarray,
    obstacles: PageObstacleMap,
    panel: Optional[PanelConstraint],
    distance: Optional[int] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Build pixels reachable from this region through safe panel space."""
    height, width = obstacles.panel_mask.shape[:2]
    distance = distance if distance is not None else max(1, int(round(64 * max(height, width) / 2048)))
    source = np.asarray(source_mask) > 0
    damage = np.asarray(damage_mask) > 0
    if source.shape != (height, width):
        source = cv2.resize(source.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST) > 0
    if damage.shape != (height, width):
        damage = cv2.resize(damage.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST) > 0

    reported_font = getattr(region, "_source_profile", None)
    reported_font = reported_font.font_size if reported_font is not None else (
        getattr(region, "source_font_size", None) or getattr(region, "font_size", 12)
    )
    font_size = max(8.0, float(_effective_source_font_size(region, reported_font)))
    clearance = max(2, min(12, int(round(font_size * 0.2))))

    usable = obstacles.panel_mask > 0
    if panel is not None:
        left, top, right, bottom = panel.bounds
        margin = max(0, int(panel.margin))
        safe_panel = np.zeros((height, width), dtype=bool)
        left, top, right, bottom = left + margin, top + margin, right - margin, bottom - margin
        if right > left and bottom > top:
            safe_panel[max(0, top):min(height, bottom), max(0, left):min(width, right)] = True
        usable &= safe_panel
        if panel.mask is not None and panel.mask.shape[:2] == (height, width):
            usable &= panel.mask > 0

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (clearance * 2 + 1, clearance * 2 + 1))
    foreign_text = (obstacles.text_mask > 0) & ~source
    if np.any(foreign_text):
        usable &= cv2.dilate(foreign_text.astype(np.uint8), kernel) == 0
    usable &= obstacles.protected_bubble_mask == 0

    anchors = (source | damage) & usable
    ys, xs = np.nonzero(anchors)
    domain = np.zeros((height, width), dtype=np.uint8)
    if not len(xs):
        return domain, {
            "bbox": [], "area_px": 0, "anchor_area_px": 0,
            "foreign_text_clearance_px": clearance, "geodesic_limit_px": distance,
        }

    # Restrict wavefront work to the only box reachable within the path-length cap.
    x1, x2 = max(0, int(xs.min()) - distance), min(width, int(xs.max()) + distance + 1)
    y1, y2 = max(0, int(ys.min()) - distance), min(height, int(ys.max()) + distance + 1)
    local_usable = usable[y1:y2, x1:x2]
    reached = anchors[y1:y2, x1:x2].copy()
    frontier = reached.copy()
    wave = np.ones((3, 3), dtype=np.uint8)
    for _ in range(distance):
        frontier = (cv2.dilate(frontier.astype(np.uint8), wave) > 0) & local_usable & ~reached
        if not np.any(frontier):
            break
        reached |= frontier
    domain[y1:y2, x1:x2] = reached.astype(np.uint8)
    dys, dxs = np.nonzero(domain)
    return domain, {
        "bbox": [int(dxs.min()), int(dys.min()), int(dxs.max() + 1), int(dys.max() + 1)],
        "area_px": int(len(dxs)),
        "anchor_area_px": int(len(xs)),
        "foreign_text_clearance_px": clearance,
        "geodesic_limit_px": distance,
    }
