"""Use compositor pixels when accepting and combining free-text candidates."""

import numpy as np

from .joint_layout import _candidate_bbox


def filter_renderable_candidates(region, candidates, config, image_shape, zone, obstacles, other_text):
    from .. import fg_bg_compare
    from ..line_breaking import render_positioned_lines
    from ..placement_geometry import _points_for_rect
    from ..stroke import get_text_stroke_width
    from .render_output_validation import _warped_alpha_crop
    from .free_text_search import _free_text_hard_valid

    fg, bg = fg_bg_compare(*region.get_font_colors())
    accepted = []
    for candidate in candidates:
        bounds = _candidate_bbox(candidate)
        stroke = get_text_stroke_width(candidate.font_size, bg, getattr(region, "bg_colors", None))
        lines = [{"text": line.text, "x": line.x, "y": line.y,
                  "width": line.width, "height": line.height} for line in candidate.lines]
        box = render_positioned_lines(lines, list(bounds), candidate.font_size, fg, bg,
            config.render.line_spacing or 0.0, getattr(region, "target_lang", "ENG"),
            getattr(region, "direction", "hr") == "hr", stroke_width=stroke)
        if box is None:
            zone.rejections["rasterization"] = zone.rejections.get("rasterization", 0) + 1
            continue
        points = _points_for_rect(region, bounds, image_shape[1], image_shape[0])
        footprint, outside = _warped_alpha_crop(box, points, image_shape)
        if outside or footprint is None:
            zone.rejections["page_bounds"] = zone.rejections.get("page_bounds", 0) + 1
            continue
        crop, visual = footprint
        if not _free_text_hard_valid(crop, visual, zone, obstacles, other_text, bounds):
            continue
        candidate._render_footprint = footprint
        candidate.qa["layout_stroke_width"] = stroke
        candidate.qa["render_footprint_validated"] = True
        accepted.append(candidate)
    return accepted


def stage_render_validator(region, config, image_shape, zone, obstacles, other_text):
    from .free_text_search import _free_text_shift_candidate
    def validate(results):
        accepted = []
        # ponytail: inspect 64 ranked placements per tier; extend if a real page exhausts this pool.
        for result in sorted(results, key=lambda item: item.score)[:64]:
            candidate = _free_text_shift_candidate(result.typography_candidate, result.dx, result.dy)
            if filter_renderable_candidates(region, [candidate], config, image_shape, zone, obstacles, other_text):
                accepted.append(result)
            if len(accepted) >= 8:
                break
        return accepted
    return validate
