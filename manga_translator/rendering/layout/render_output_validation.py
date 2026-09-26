"""Final render-footprint checks shared by layout validation."""

from typing import Any, Callable

import cv2
import numpy as np

from ...utils import resolve_render_content
from .models import LayoutDiagnostics, PageLayoutResult, PlacementMode
from .obstacles import _region_source_mask, build_page_obstacle_map
from .raster import _cropped_masks_overlap


def _segment_bounds(segment, lines):
    bounds = [segment.get(key) for key in ("x", "y", "width", "height")]
    if all(value is not None for value in bounds):
        x, y, width, height = map(int, bounds)
        return (x, y, x + width, y + height)
    if not lines:
        return None
    return (
        min(int(line["x"] if isinstance(line, dict) else line.x) for line in lines),
        min(int(line["y"] if isinstance(line, dict) else line.y) for line in lines),
        max(int(line["x"] if isinstance(line, dict) else line.x) + int(line["width"] if isinstance(line, dict) else line.width) for line in lines),
        max(int(line["y"] if isinstance(line, dict) else line.y) + int(line["height"] if isinstance(line, dict) else line.height) for line in lines),
    )


def _region_render_boxes(region, layout, image_shape):
    """Rebuild the exact RGBA crops the renderer will composite."""
    from .. import fg_bg_compare, get_default_eng_font, text_render
    from ..line_breaking import render_positioned_lines
    from ..placement_geometry import _points_for_rect
    from ..serialization import decode_rendered_box
    from ..stroke import get_text_stroke_width

    raw_segments = getattr(region, "_bubble_segments", None) or getattr(region, "layout_segments", None) or []
    if not raw_segments and getattr(region, "_bubble_box", None) is not None:
        bounds = getattr(region, "layout_bounds", None)
        if bounds and len(bounds) == 4:
            raw_segments = [{"bounds": bounds, "box": region._bubble_box}]
    if not raw_segments and layout is not None and layout.lines:
        raw_segments = [{"lines": layout.lines}]
    if not raw_segments:
        return [], None

    if not text_render.FONT_SELECTION:
        text_render.set_font(get_default_eng_font())
    fg, bg = fg_bg_compare(*region.get_font_colors())
    qa = getattr(region, "_solver_qa", {}) or {}
    masks = []
    for segment in raw_segments:
        lines = segment.get("lines") or []
        bounds = segment.get("bounds")
        if bounds is None:
            bounds = _segment_bounds(segment, lines)
        elif len(bounds) == 4:
            bounds = tuple(map(int, bounds))
        if bounds is None or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            return [], "invalid frozen segment bounds"

        box = segment.get("box")
        if box is None and lines:
            line_boxes = [
                {
                    "text": str(line.get("text", "")) if isinstance(line, dict) else line.text,
                    "x": int(line.get("x", 0)) if isinstance(line, dict) else int(line.x),
                    "y": int(line.get("y", 0)) if isinstance(line, dict) else int(line.y),
                    "width": int(line.get("width", 0)) if isinstance(line, dict) else int(line.width),
                    "height": int(line.get("height", 0)) if isinstance(line, dict) else int(line.height),
                }
                for line in lines
            ]
            font_size = max(1, int(segment.get("font_size", getattr(region, "font_size", 1)) or 1))
            stroke_width = qa.get("layout_stroke_width") or get_text_stroke_width(
                font_size, bg, getattr(region, "bg_colors", None)
            )
            box = render_positioned_lines(
                line_boxes, list(bounds), font_size, fg, bg,
                float(getattr(region, "line_spacing", 0.0) or 0.0),
                getattr(region, "target_lang", "ENG") or "ENG",
                getattr(region, "direction", "") == "hr",
                stroke_width=stroke_width,
            )
        if box is None:
            box = decode_rendered_box(segment.get("rendered_png"))
        if box is None and not lines and len(raw_segments) == 1:
            box = getattr(region, "_bubble_box", None)
        if box is None or box.ndim != 3 or box.shape[2] != 4 or not np.any(box[:, :, 3]):
            return [], "frozen segment has no visible raster"

        points = _points_for_rect(region, bounds, image_shape[1], image_shape[0])
        masks.append((box, points))
    return masks, None


def _warped_alpha_crop(box, points, image_shape):
    height, width = image_shape[:2]
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    outside = bool(
        np.any(points[:, 0] < 0) or np.any(points[:, 0] > width)
        or np.any(points[:, 1] < 0) or np.any(points[:, 1] > height)
    )
    x, y, w, h = cv2.boundingRect(points.astype(np.int32))
    x1, y1, x2, y2 = max(0, x), max(0, y), min(width, x + w), min(height, y + h)
    if x2 <= x1 or y2 <= y1:
        return None, outside
    src = np.array([[0, 0], [box.shape[1], 0], [box.shape[1], box.shape[0]], [0, box.shape[0]]], np.float32)
    dst = points - np.array([x1, y1], np.float32)
    matrix, _ = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if matrix is None:
        matrix, _ = cv2.findHomography(src, dst)
    if matrix is None:
        return None, outside
    alpha = cv2.warpPerspective(
        box[:, :, 3], matrix, (x2 - x1, y2 - y1),
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    if not np.any(alpha):
        return None, outside
    return ((x1, y1, x2, y2), alpha > 0), outside


def validate_render_output(
    ctx: Any,
    result: PageLayoutResult,
    diagnostics: LayoutDiagnostics,
    regions: list,
    regions_by_id: dict,
    region_metrics: dict,
    suppress: Callable[[str, str], None],
) -> None:
    image = getattr(ctx, "img_rgb", None)
    if image is None:
        return
    height, width = image.shape[:2]
    obstacles = build_page_obstacle_map(regions, (height, width))
    restored_source_mask = np.zeros((height, width), dtype=np.uint8)
    for region in regions:
        unchanged = (
            getattr(region, "review_reason", None) == "Translation identical to original"
            and resolve_render_content(region).strip().casefold()
            == str(getattr(region, "text", "") or "").strip().casefold()
        )
        if getattr(region, "_render_suppressed", False) or unchanged:
            cv2.bitwise_or(
                restored_source_mask,
                _region_source_mask(region, (height, width)),
                dst=restored_source_mask,
            )
    visual_masks = {}
    for region in regions:
        region_id = str(getattr(region, "region_id", "") or "")
        if not resolve_render_content(region).strip() or getattr(region, "_render_suppressed", False):
            continue
        layout = result.for_region(region_id)
        boxes, error = _region_render_boxes(region, layout, image.shape)
        if error or not boxes:
            reason = error or "translated region has no renderable text"
            diagnostics.errors.append(f"render output unavailable for region {region_id}: {reason}")
            suppress(region_id, reason)
            continue
        region_visuals = []
        mode = getattr(region, "placement_mode", None)
        for box, points in boxes:
            footprint, outside = _warped_alpha_crop(box, points, image.shape)
            if outside:
                diagnostics.errors.append(f"render outside page for region {region_id}")
                suppress(region_id, "rotated layout outside page bounds")
            if footprint is None:
                diagnostics.errors.append(f"render rasterization failed for region {region_id}")
                suppress(region_id, "render rasterization failed")
                continue
            crop_box, visual = footprint
            region_visuals.append(footprint)
            x1, y1, x2, y2 = crop_box
            if np.any(visual & (restored_source_mask[y1:y2, x1:x2] > 0)):
                diagnostics.errors.append(f"render overlaps restored source text for region {region_id}")
                suppress(region_id, "final layout overlaps restored source text")
            if mode is PlacementMode.FREE_TEXT or mode == PlacementMode.FREE_TEXT.value:
                overlap = int(np.count_nonzero(
                    visual & (obstacles.protected_bubble_mask[y1:y2, x1:x2] > 0)
                ))
                region_metrics.setdefault(region_id, {})["bubble_overlap_pixels"] += overlap
                if overlap:
                    diagnostics.errors.append(f"free text overlaps protected bubble for region {region_id}")
                    suppress(region_id, "final layout overlaps a protected speech bubble")

            panel = getattr(region, "_panel_constraint", None)
            if panel is not None:
                allowed = np.zeros((y2 - y1, x2 - x1), dtype=bool)
                panel_mask = getattr(panel, "mask", None)
                if panel_mask is not None and np.asarray(panel_mask).shape[:2] == (height, width):
                    allowed = np.asarray(panel_mask[y1:y2, x1:x2]) > 0
                else:
                    left, top, right, bottom = panel.bounds
                    px1, py1 = max(x1, int(left)), max(y1, int(top))
                    px2, py2 = min(x2, int(right)), min(y2, int(bottom))
                    if px2 > px1 and py2 > py1:
                        allowed[py1 - y1:py2 - y1, px1 - x1:px2 - x1] = True
                if np.any(visual & ~allowed):
                    diagnostics.errors.append(f"layout leaves panel for region {region_id}")
                    suppress(region_id, "final layout leaves its panel")

            if mode is PlacementMode.BUBBLE or mode == PlacementMode.BUBBLE.value:
                bubble = getattr(region, "_bubble_interior", None)
                if bubble is None or not np.any(bubble):
                    bubble = getattr(region, "_bubble_mask", None)
                if bubble is not None and np.asarray(bubble).shape[:2] == (height, width):
                    if np.any(visual & (np.asarray(bubble[y1:y2, x1:x2]) == 0)):
                        diagnostics.errors.append(f"bubble text leaves safe shape for region {region_id}")
                        suppress(region_id, "final layout leaves its bubble")
        if region_visuals:
            visual_masks[region_id] = region_visuals

    ids = list(visual_masks)
    for index, first_id in enumerate(ids):
        for second_id in ids[index + 1:]:
            collides = any(
                _cropped_masks_overlap(first_box, first_mask, second_box, second_mask)
                for first_box, first_mask in visual_masks[first_id]
                for second_box, second_mask in visual_masks[second_id]
            )
            if not collides:
                continue
            diagnostics.errors.append(f"render collision between {first_id} and {second_id}")
            first_mode = getattr(regions_by_id.get(first_id), "placement_mode", None)
            second_mode = getattr(regions_by_id.get(second_id), "placement_mode", None)
            if second_mode is PlacementMode.FREE_TEXT or second_mode == PlacementMode.FREE_TEXT.value:
                suppress(second_id, f"final layout collides with region {first_id}")
            elif first_mode is PlacementMode.FREE_TEXT or first_mode == PlacementMode.FREE_TEXT.value:
                suppress(first_id, f"final layout collides with region {second_id}")
            else:
                suppress(second_id, f"final layout collides with region {first_id}")
