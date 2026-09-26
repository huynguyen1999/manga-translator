"""Opt-in layout diagnostics; production solving never imports this module."""

from typing import Any, Dict, List, Union

import cv2
import numpy as np

from .geometry import BubbleGeometry, compute_placement_target
from .models import BubbleLayoutGroup, FreeTextZone, PageObstacleMap, PlacementMode
from .source_profile import build_original_layout_profile


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

        panel = getattr(raw_zone, "panel_constraint", None)
        if panel is not None and panel.source == "cv":
            x1, y1, x2, y2 = panel.bounds
            cv2.rectangle(debug, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)), (255, 0, 255), 2)
            cv2.putText(
                debug,
                f"PANEL {panel.source} {panel.confidence:.2f}",
                (max(2, x1 + 3), max(14, y1 + 14)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (255, 0, 255),
                1,
                cv2.LINE_AA,
            )

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


def create_placement_zones_visualization(
    img_rgb: np.ndarray,
    groups: List[BubbleLayoutGroup],
) -> np.ndarray:
    """Create a diagnostic debug visualization showing placement zones, centroids, and boundaries."""
    if img_rgb is None:
        return np.zeros((100, 100, 3), dtype=np.uint8)
    vis = img_rgb.copy()
    zone_colors = [
        (255, 120, 0),    # Blue-orange palette
        (0, 200, 100),
        (220, 50, 220),
        (255, 200, 0),
        (50, 180, 255),
        (180, 100, 255),
    ]

    for g_idx, group in enumerate(groups):
        interior = group.interior
        if interior is None or not np.any(interior):
            continue

        # Draw bubble safe interior boundary
        int_cnts, _ = cv2.findContours(interior.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(vis, int_cnts, -1, (180, 180, 180), 1)

        # Draw each placement zone
        for z_idx, (region, zone) in enumerate(zip(group.regions, group.zones)):
            color = zone_colors[z_idx % len(zone_colors)]
            if zone is not None and np.any(zone):
                # Tint zone area
                tint = np.zeros_like(vis)
                tint[zone > 0] = color
                cv2.addWeighted(tint, 0.25, vis, 1.0, 0, vis)

                # Zone contour
                z_cnts, _ = cv2.findContours((zone > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(vis, z_cnts, -1, color, 2)

            # Draw original source polygon & centroid
            lines = getattr(region, "lines", None)
            if lines is not None and len(lines):
                cv2.polylines(vis, [np.asarray(line, np.int32) for line in lines], True, (0, 0, 255), 1)

            profile = build_original_layout_profile(region, interior)
            if profile is not None:
                cx, cy = int(round(profile.centroid[0])), int(round(profile.centroid[1]))
                cv2.circle(vis, (cx, cy), 4, (0, 0, 255), -1)
                label = f"Z{z_idx+1}"
                cv2.putText(vis, label, (cx + 6, cy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2)

            # Draw target capacity center (+) for zone
            target_mask = zone if (zone is not None and np.any(zone)) else interior
            if target_mask is not None and np.any(target_mask):
                target_geom = compute_placement_target(
                    BubbleGeometry(target_mask),
                    int(getattr(region, "font_size", 12) or 12),
                    source_profile=profile,
                    is_single_region=(len(group.regions) == 1),
                )
                tcx = int(round(target_geom.center_x + target_geom.bbox[0] * 0))
                # Add geom offset if BubbleGeometry was cropped
                bg = BubbleGeometry(target_mask)
                tcx = int(round(target_geom.center_x + bg.x_offset))
                tcy = int(round(target_geom.center_y + bg.y_offset))
                # Draw cross (+) in magenta
                cv2.drawMarker(vis, (tcx, tcy), (255, 0, 255), cv2.MARKER_CROSS, 8, 1, cv2.LINE_AA)

            # Draw placed lines, band slots, centers, and transition connectors if solver ran
            placed_lines = None
            if hasattr(region, "layout_segments") and region.layout_segments:
                seg_lines = region.layout_segments[0].get("lines", [])
                if seg_lines:
                    placed_lines = seg_lines

            if placed_lines:
                prev_cx, prev_cy = None, None
                all_lx = [int(pl["x"]) for pl in placed_lines]
                all_ly = [int(pl["y"]) for pl in placed_lines]
                all_rx = [int(pl["x"]) + int(pl["width"]) for pl in placed_lines]
                all_by = [int(pl["y"]) + int(pl["height"]) for pl in placed_lines]
                bx1, by1, bx2, by2 = min(all_lx), min(all_ly), max(all_rx), max(all_by)

                # Draw block bounding box
                cv2.rectangle(vis, (bx1, by1), (bx2, by2), (0, 255, 255), 1)
                # Draw block center (x)
                bcx = (bx1 + bx2) // 2
                bcy = (by1 + by2) // 2
                cv2.drawMarker(vis, (bcx, bcy), (0, 255, 255), cv2.MARKER_TILTED_CROSS, 7, 1, cv2.LINE_AA)

                for l_idx, pl in enumerate(placed_lines):
                    lx, ly, lw, lh = int(pl["x"]), int(pl["y"]), int(pl["width"]), int(pl["height"])
                    # Slot bounding box
                    cv2.rectangle(vis, (lx, ly), (lx + lw, ly + lh), color, 1)
                    # Line center dot
                    cx_i = lx + lw // 2
                    cy_i = ly + lh // 2
                    cv2.circle(vis, (cx_i, cy_i), 3, (0, 255, 0), -1)

                    # Transition connector from previous line
                    if prev_cx is not None and prev_cy is not None:
                        # Draw connector line from previous center to current center
                        cv2.line(vis, (prev_cx, prev_cy), (cx_i, cy_i), (0, 255, 255), 1, cv2.LINE_AA)
                    prev_cx, prev_cy = cx_i, cy_i

        # Draw lobe graph features if present
        if group.lobe_graph is not None:
            for neck in group.lobe_graph.necks:
                nx, ny = neck["center"]
                cv2.circle(vis, (nx, ny), 3, (255, 255, 0), -1)

    return vis
