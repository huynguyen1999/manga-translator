"""Conservative, atomic layout and erasure for enclosed dialogue."""
import copy
from functools import cached_property
from typing import List, Tuple, Optional, Dict, Any

import cv2
import numpy as np

from .candidates import (
    _evaluate_candidate_layout,
    _fit_lobe_text,
    calculate_mask_moments,
    calculate_text_moments,
    check_sdf_clearance,
)
from ..geometry.bubbles import compose_bubble_cleanup, prepare_page_geometry
from .grouping import _source_region_snapshot, group_regions_by_bubbles
from .line_breaking import (
    analyze_semantic_breakpoints,
    build_line_slots,
    dp_break_lines_for_lobe,
    optimize_lobe_center_x,
    render_positioned_lines,
)
from .lobes import (
    _centered_or_largest_rect,
    _estimate_adaptive_font_size,
    _has_verified_neck,
    _largest_rect_containing,
    _lobe_rects,
    _stepped_lobe_rects,
    build_geometry_profile,
)
from .serialization import (
    decode_rendered_box,
    decode_safe_shape,
    encode_rendered_box,
    encode_safe_shape,
)









def restore_bubble_assignments(regions, detections):
    """Reconnect persisted region bubble IDs to the reloaded detection masks."""
    by_id = {f"bubble_{index}": detection for index, detection in enumerate(detections or [])}
    missing = []
    for region in regions or []:
        detection = by_id.get(str(getattr(region, "bubble_id", "")))
        if detection is None:
            missing.append(region)
            continue
        region._bubble_mask = detection.mask
        region._bubble_detection_confidence = detection.confidence
    if missing:
        group_regions_by_bubbles(missing, detections, group=False)
    return regions


def prepare_bubble_masks(image, regions, padding: int = 9, profile=None, return_cleanup: bool = True, page_geometry=None):
    """Compatibility wrapper for callers that still request a page cleanup mask."""
    if page_geometry is not None and page_geometry.matches(image, regions, padding):
        if profile is not None:
            workload = profile.setdefault("workload", {})
            workload["unique_bubble_contours"] = 0
            workload["distance_transform_calls"] = 0
            workload["unique_bubble_count"] = len(page_geometry.bubbles)
        if not return_cleanup:
            return None
        return compose_bubble_cleanup(page_geometry, image.shape[:2])
    _, cleanup = prepare_page_geometry(
        image, regions, padding, return_cleanup=return_cleanup, profile=profile,
    )
    return cleanup

def prepare_bubbles(image, regions, font_path, render_config, group: bool = True, page_target_font=None):
    from . import text_render, _horizontal_layout, _find_horizontal_placement, _points_for_rect, _bounds_from_region, fg_bg_compare

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    custom_mode = any(getattr(region, "_bubble_mask", None) is not None for region in regions)
    # ponytail: flat light interiors only; textured/dark enclosures need a separate
    # validated segmenter before automatic cleanup can safely support them.
    count, labels, stats, _ = cv2.connectedComponentsWithStats((gray >= 220).astype(np.uint8), connectivity=4)
    # A narrow opening can connect a real bubble to the page background. Use a
    # second pass only to identify a review candidate, never to justify erasure.
    closed_count, closed_labels, closed_stats, _ = cv2.connectedComponentsWithStats(
        cv2.morphologyEx((gray >= 220).astype(np.uint8), cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)),
        connectivity=4)
    groups = {}
    custom_masks = {}
    untouched = []
    for index, region in enumerate(regions):
        region._bubble_source_order = index
        if not region.translation:
            untouched.append(region)
            continue
        if custom_mode:
            mask = getattr(region, "_bubble_mask", None)
            if mask is None or not np.any(mask):
                untouched.append(region)
                continue
            label = id(mask)
            custom_masks[label] = (mask > 0).astype(np.uint8)
            groups.setdefault((False, label), []).append((index, region))
            continue
        selected = np.zeros(gray.shape, np.uint8)
        cv2.fillPoly(selected, [np.asarray(line, np.int32) for line in region.lines], 1)
        values = labels[selected > 0]
        votes = np.bincount(values, minlength=count)
        votes[0] = 0
        label = int(votes.argmax())
        x, y, w, h, area = stats[label]  # Reject glyph halos smaller than the source text polygon.
        if not label or votes[label] < max(1, len(values) * .45) or area < np.count_nonzero(selected) * .9:
            untouched.append(region)
            continue
        enclosed = x > 0 and y > 0 and x + w < gray.shape[1] and y + h < gray.shape[0]
        uncertain_boundary = False
        if not enclosed or area < 100:
            closed_votes = np.bincount(closed_labels[selected > 0], minlength=closed_count)
            closed_votes[0] = 0
            alternate = int(closed_votes.argmax())
            ax, ay, aw, ah, aa = closed_stats[alternate]
            if (alternate and closed_votes[alternate] >= len(values) * .45 and aa >= 100
                    and ax > 0 and ay > 0 and ax + aw < gray.shape[1] and ay + ah < gray.shape[0]):
                label = alternate
                uncertain_boundary = True
            else:
                untouched.append(region)
                continue
        groups.setdefault((uncertain_boundary, label), []).append((index, region))

    result = list(untouched)
    if not group:
        # Keep each OCR region independent while reusing the same detected
        # component. The recursive call uses custom-mask mode, so sibling ink
        # cannot make an otherwise valid region look like an uncertain cleanup.
        for (uncertain_boundary, label), members in groups.items():
            component = custom_masks[label] if custom_mode else (
                (closed_labels if uncertain_boundary else labels) == label
            ).astype(np.uint8)
            for index, member in members:
                separate = copy.copy(member)
                separate._bubble_mask = component
                separate._bubble_source_order = index
                prepared_regions = prepare_bubbles(
                    image, [separate], font_path, render_config, group=True,
                    page_target_font=page_target_font,
                )
                for prepared_region in prepared_regions:
                    prepared_region.region_id = getattr(member, "region_id", None)
                    prepared_region.source_region_ids = list(
                        getattr(member, "source_region_ids", []) or [prepared_region.region_id]
                    )
                    prepared_region.source_regions = copy.deepcopy(
                        getattr(member, "source_regions", [])
                    )
                    if getattr(member, "_bubble_mask", None) is not None:
                        prepared_region._bubble_mask = member._bubble_mask
                    elif hasattr(prepared_region, "_bubble_mask"):
                        del prepared_region._bubble_mask
                    if hasattr(prepared_region, "group_members"):
                        del prepared_region.group_members
                    result.append(prepared_region)
        return sorted(result, key=lambda item: getattr(item, "_bubble_source_order", 0))
    text_render.set_font(font_path)

    if page_target_font is None:
        page_target_estimates = []
        for (u_b, lbl), mems in groups.items():
            comp = custom_masks[lbl] if custom_mode else ((closed_labels if u_b else labels) == lbl).astype(np.uint8)
            cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            inter = np.zeros_like(comp)
            cv2.drawContours(inter, cnts, -1, 1, cv2.FILLED)
            inter = cv2.erode(inter, np.ones((7, 7), np.uint8))
            t = "\n".join(r.translation for _, r in mems)
            min_f = render_config.font_size_minimum
            if min_f == -1:
                min_f = round(sum(gray.shape) / 200)
            min_f = max(1, min_f)
            est = _estimate_adaptive_font_size(inter, t, min_f)
            if est > min_f:
                page_target_estimates.append(est)
        page_target_font = int(round(np.percentile(page_target_estimates, 70))) if page_target_estimates else None

    placed_bubble_rects = []
    for group_key, members in groups.items():
        uncertain_boundary, label = group_key
        # Existing pipeline region order is already the source reading order.
        members.sort(key=lambda pair: pair[0])
        region = copy.copy(members[0][1])
        for name, descriptor in vars(type(region)).items():
            if isinstance(descriptor, cached_property):
                region.__dict__.pop(name, None)
        region._bounding_rect = None
        region.angle = 0
        region.lines = np.concatenate([r.lines for _, r in members])
        region.texts = [r.text for _, r in members]
        region.text = "\n".join(region.texts)
        region.translation = "\n".join(r.translation for _, r in members)
        region.group_id = f"bubble_{members[0][0]}"
        region.region_id = region.group_id
        region.source_region_ids = [
            source_id
            for source_index, item in members
            for source_id in (
                getattr(item, "source_region_ids", None)
                or [str(getattr(item, "region_id", f"source_{source_index}"))]
            )
        ]
        region.group_members = list(region.source_region_ids)
        source_regions = []
        for source_index, item in members:
            records = getattr(item, "source_regions", None) or [_source_region_snapshot(item, source_index)]
            for source in records:
                record = copy.deepcopy(source)
                record["reading_order"] = len(source_regions)
                source_regions.append(record)
        region.source_regions = source_regions
        region.review_required = any(getattr(r, "review_required", False) for _, r in members)
        region.review_reason = next(
            (getattr(r, "review_reason", None) for _, r in members if getattr(r, "review_required", False)),
            None,
        )
        component = custom_masks[label] if custom_mode else ((closed_labels if uncertain_boundary else labels) == label).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        interior = np.zeros_like(component)
        cv2.drawContours(interior, contours, -1, 1, cv2.FILLED)
        region._bubble_restore = interior.copy()
        interior = cv2.erode(interior, np.ones((7, 7), np.uint8))
        x, y, w, h = cv2.boundingRect(interior)
        region.bubble_bounds = [x, y, x+w, y+h]
        region.layout_bounds = list(region.bubble_bounds)
        region._bubble_interior = interior
        dist = cv2.distanceTransform(interior.astype(np.uint8), cv2.DIST_L2, 5)
        if np.any(dist):
            flat = int(np.argmax(dist))
            cy, cx = np.unravel_index(flat, dist.shape)
            region._bubble_center = (int(cx), int(cy))
        else:
            region._bubble_center = ((x + x + w) // 2, (y + y + h) // 2)

        selected = np.zeros_like(component)
        cv2.fillPoly(selected, [np.asarray(line, np.int32) for line in region.lines], 1)
        region._bubble_restore = cv2.bitwise_or(region._bubble_restore, selected)
        nearby = cv2.dilate(selected, np.ones((7, 7), np.uint8))
        ink = ((gray < 200) & (interior > 0)).astype(np.uint8)
        n, ink_labels, ink_stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        cleanup = np.zeros_like(component)
        uncertain = False
        if custom_mode:
            cleanup = interior * 255
        else:
            for ink_id in range(1, n):
                pixels = ink_labels == ink_id
                if np.any(nearby[pixels]):
                    cleanup[pixels] = 255
                elif ink_stats[ink_id, cv2.CC_STAT_AREA] >= 3:
                    uncertain = True
            for _, member in members:
                member_selected = np.zeros_like(component)
                cv2.fillPoly(member_selected, [np.asarray(line, np.int32) for line in member.lines], 1)
                if not np.any(ink & member_selected):
                    uncertain = True
            cleanup = cv2.dilate(cleanup, np.ones((3, 3), np.uint8)) * interior
        region._bubble_cleanup = cleanup
        if not custom_mode and (uncertain_boundary or not interior.any() or np.count_nonzero(selected & interior) < .95 * np.count_nonzero(selected)):
            region.review_reason = "uncertain_boundary"
        elif not custom_mode and (uncertain or not cleanup.any()):
            region.review_reason = "uncertain_cleanup"
        elif not custom_mode and not region.horizontal:
            region.review_reason = "text_does_not_fit"

        minimum = render_config.font_size_minimum
        if minimum == -1:
            minimum = round(sum(gray.shape) / 200)
        minimum = max(1, minimum)
        source_target = getattr(region, "calibrated_font_size", None) or region.font_size + render_config.font_size_offset
        target = max(minimum, render_config.font_size or source_target)
        text = region.get_translation_for_rendering()
        if render_config.font_size is None and not getattr(region, "calibrated_font_size", None):
            adaptive_target = _estimate_adaptive_font_size(interior, text, minimum)
            target = max(target, adaptive_target)
            if page_target_font is not None:
                target = max(target, int(round(page_target_font * 0.90)))

        lobe_rects = _lobe_rects(
            interior, region.lines, max(8, minimum), getattr(region, "target_lang", None)
        )
        if len(lobe_rects) > 1:
            target = max(minimum, render_config.font_size or source_target)
        preferred_minimum = max(min(int(np.ceil(target * 0.90)), target - 2), minimum)
        lobe_layout = None
        bubble_center = getattr(region, '_bubble_center', None) or (
            (region.bubble_bounds[0] + region.bubble_bounds[2]) / 2,
            (region.bubble_bounds[1] + region.bubble_bounds[3]) / 2,
        )
        off_center_lobe = bool(lobe_rects) and (
            abs((lobe_rects[0][0] + lobe_rects[0][2]) / 2 - bubble_center[0]) > minimum
            or abs((lobe_rects[0][1] + lobe_rects[0][3]) / 2 - bubble_center[1]) > minimum
        )
        if len(lobe_rects) > 1 or off_center_lobe:
            lobe_layout = _fit_lobe_text(
                region, lobe_rects, text, target, preferred_minimum,
                False, render_config.line_spacing or 0,
            )
            if lobe_layout is None:
                region.review_reason = region.review_reason or "text_does_not_fit"
                region.review_required = True
                region._render_suppressed = False
        if lobe_layout is not None:
            region.font_size, segments = lobe_layout
            region.layout_segments = [
                {
                    "x": segment["bounds"][0],
                    "y": segment["bounds"][1],
                    "width": segment["bounds"][2] - segment["bounds"][0],
                    "height": segment["bounds"][3] - segment["bounds"][1],
                    "text": segment["text"],
                    "font_size": segment.get("font_size", region.font_size),
                    "lines": segment.get("lines", []),
                }
                for segment in segments
            ]
            region._bubble_segments = segments
            if len(segments) == 1 and "box" in segments[0]:
                region._bubble_box = segments[0]["box"]
            region.layout_bounds = [
                min(segment["bounds"][0] for segment in segments),
                min(segment["bounds"][1] for segment in segments),
                max(segment["bounds"][2] for segment in segments),
                max(segment["bounds"][3] for segment in segments),
            ]
            region._bubble_points = _points_for_rect(
                region, region.layout_bounds, image.shape[1], image.shape[0])
            region._bubble_cleanup = interior * 255
        else:
            # Single-lobe bubbles fallback to centered rectangular placement path.
            obstacles = list(placed_bubble_rects)
            for other_key, other_members in groups.items():
                if other_key == group_key:
                    continue
                obstacles.extend(
                    bounds for _, other in other_members
                    if (bounds := _bounds_from_region(other)) is not None
                )
            placement = _find_horizontal_placement(
                region, region.bubble_bounds, image.shape, target, preferred_minimum,
                text, False,
                render_config.line_spacing or 0, obstacles, is_bubble=True)
            if placement is None:
                region.review_reason = region.review_reason or "text_does_not_fit"
                region.review_required = True
                region._render_suppressed = True
        if lobe_layout is None and placement is not None:
            region._render_suppressed = False
            region.font_size, rect = placement
            region.layout_bounds = list(rect)
            region._bubble_points = _points_for_rect(region, rect, image.shape[1], image.shape[0])
            fg, bg = fg_bg_compare(*region.get_font_colors())
            box = text_render.put_text_horizontal(
                region.font_size, region.get_translation_for_rendering(),
                max(1, rect[2] - rect[0]), max(1, rect[3] - rect[1]), region.alignment,
                region.direction == 'hr', fg, bg, region.target_lang,
                False, render_config.line_spacing,
                font_size_minimum=region.font_size)
            if box is not None and np.any(box[:, :, 3]):
                region._bubble_box = box
                region._bubble_cleanup = interior * 255
            if box is None or not np.any(box[:, :, 3]) or box.shape[1] > (rect[2] - rect[0] + 3) or box.shape[0] > (rect[3] - rect[1] + 3):
                region.review_reason = region.review_reason or "text_does_not_fit"
                region.review_required = True
                region._render_suppressed = True
                region.__dict__.pop("_bubble_box", None)
        if not getattr(region, "_render_suppressed", False) and getattr(region, "layout_bounds", None):
            placed_bubble_rects.append(list(region.layout_bounds))
        region.review_required = bool(region.review_reason)
        result.append(region)
    return sorted(result, key=lambda r: r._bubble_source_order)


def constrain_mask(mask, regions):
    mask = mask.copy()
    original = mask.copy()
    preserve = np.zeros_like(mask)
    for region in regions:
        has_trans = bool(getattr(region, "translation", None) and region.translation.strip())
        if getattr(region, "_bubble_interior", None) is None and (not getattr(region, "review_required", False) or has_trans):
            cv2.fillPoly(preserve, [np.asarray(line, np.int32) for line in region.lines], 255)
    for region in regions:
        has_trans = bool(getattr(region, "translation", None) and region.translation.strip())
        interior = getattr(region, "_bubble_interior", None)
        if interior is not None:
            protected = cv2.dilate(region._bubble_restore, np.ones((7, 7), np.uint8))
            mask[protected > 0] = 0
            if not region.review_required or has_trans:
                cleanup = getattr(region, "_bubble_cleanup", None)
                if cleanup is not None and np.any(cleanup):
                    mask = cv2.bitwise_or(mask, cleanup)
                else:
                    mask = cv2.bitwise_or(mask, (interior * 255).astype(np.uint8))
        elif getattr(region, "review_required", False) and not has_trans:
            cv2.fillPoly(mask, [np.asarray(line, np.int32) for line in region.lines], 0)
    return cv2.bitwise_or(mask, cv2.bitwise_and(original, preserve))


def restore_original(canvas, original, regions):
    for region in regions:
        suppressed = getattr(region, "_render_suppressed", False)
        if not getattr(region, "review_required", False) and not suppressed:
            continue
        if not suppressed and getattr(region, "translation", None) and region.translation.strip():
            continue
        interior = getattr(region, "_bubble_restore", None)
        if interior is None:
            lines = getattr(region, "lines", None)
            if lines is not None and len(lines) > 0:
                interior = np.zeros(original.shape[:2], np.uint8)
                cv2.fillPoly(interior, [np.asarray(line, np.int32) for line in lines], 1)
            else:
                continue
        canvas[interior > 0] = original[interior > 0]
    return canvas
