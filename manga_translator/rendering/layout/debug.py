"""Opt-in layout diagnostics; production solving never imports this module."""

from typing import Any, Dict, List, Union

import cv2
import numpy as np

from .models import FreeTextZone, PageObstacleMap, PlacementMode


def create_free_text_layout_debug(
    image: np.ndarray,
    regions: List[Any],
    obstacles: PageObstacleMap,
    zones: Dict[int, Union[np.ndarray, FreeTextZone]],
) -> np.ndarray:
    """Rich debug overlay: BLUE (source), MAGENTA (damage), BRIGHT (core), GREEN (zone), RED (bubble), YELLOW (halo), CYAN (visual), WHITE (block)."""
    debug = image.copy()
    h, w = debug.shape[:2]

    for region in regions or []:
        if getattr(region, "placement_mode", None) is not PlacementMode.FREE_TEXT:
            continue
        rid = id(region)
        source = getattr(region, "_free_text_source_mask", np.zeros((h, w), np.uint8)) > 0
        raw_zone = zones.get(rid)
        if isinstance(raw_zone, FreeTextZone):
            zone_mask = raw_zone.ownership_mask > 0
            damage_mask = raw_zone.coverage_target_mask > 0
            core_mask = raw_zone.core_damage_mask > 0
        elif raw_zone is not None:
            zone_mask = raw_zone > 0
            damage_mask = source
            core_mask = source
        else:
            zone_mask = np.zeros((h, w), bool)
            damage_mask = source
            core_mask = source

        tint = np.zeros_like(debug)
        # BLUE: original source footprint
        tint[source] = (255, 0, 0)
        # GREEN: ownership territory
        tint[zone_mask] = (0, 180, 0)
        # MAGENTA: actual damage mask
        tint[damage_mask] = (220, 0, 220)
        # BRIGHT MAGENTA / WHITE: high weight damage core
        tint[core_mask] = (255, 120, 255)
        debug = cv2.addWeighted(debug, 1.0, tint, 0.22, 0)

        # Outlines
        for mask, color, thickness in (
            (obstacles.bubble_mask, (0, 0, 255), 2),              # RED: speech bubbles
            (obstacles.protected_bubble_mask, (0, 255, 255), 1),  # YELLOW: bubble safety halo
            (zone_mask, (0, 200, 0), 1),                          # GREEN: ownership boundary
            (damage_mask, (200, 0, 200), 1),                      # MAGENTA: damage boundary
            (source, (255, 100, 0), 1),                           # BLUE: source boundary
        ):
            contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(debug, contours, -1, color, thickness)

        # CYAN: candidate visual footprint (with stroke)
        vis_mask = getattr(region, "_free_text_visual_mask", None)
        if vis_mask is not None:
            contours, _ = cv2.findContours(vis_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(debug, contours, -1, (255, 255, 0), 1)

        # WHITE: selected glyph mask & block bounds
        glyph = getattr(region, "_free_text_glyph_mask", None)
        if glyph is not None:
            contours, _ = cv2.findContours(glyph.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(debug, contours, -1, (255, 255, 255), 1)

        bounds = getattr(region, "layout_bounds", None)
        if bounds is not None and len(bounds) == 4:
            x1, y1, x2, y2 = [int(v) for v in bounds]
            cv2.rectangle(debug, (x1, y1), (x2, y2), (255, 255, 255), 1)

        target = raw_zone.damage_target if isinstance(raw_zone, FreeTextZone) else None
        if target is not None:
            damage_pt = (int(round(target.centroid_x)), int(round(target.centroid_y)))
            cv2.drawMarker(debug, damage_pt, (255, 0, 255), cv2.MARKER_CROSS, 9, 1, cv2.LINE_AA)
            glyph_centroid = getattr(region, "_solver_qa", {}).get("ink_centroid")
            if glyph_centroid:
                glyph_pt = (int(round(glyph_centroid[0])), int(round(glyph_centroid[1])))
                cv2.drawMarker(debug, glyph_pt, (255, 255, 0), cv2.MARKER_TILTED_CROSS, 9, 1, cv2.LINE_AA)
                cv2.arrowedLine(debug, damage_pt, glyph_pt, (255, 255, 255), 1, cv2.LINE_AA, tipLength=0.2)
            qa = getattr(region, "_solver_qa", {}) or {}
            label = (
                f"FREE {getattr(region, 'region_id', '')} "
                f"font:{getattr(region, 'font_size', 0)} "
                f"lines:{len(getattr(region, 'layout_segments', [{}])[0].get('lines', [])) if getattr(region, 'layout_segments', None) else 0} "
                f"cov:{qa.get('damage_coverage', 0.0):.0%}"
            )
            cv2.putText(debug, label, (damage_pt[0] + 6, damage_pt[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1, cv2.LINE_AA)

    return debug

