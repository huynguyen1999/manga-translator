"""Page-local reuse of exact compositor crops and validated placements."""

import numpy as np

from .. import fg_bg_compare, text_render
from ..line_breaking import render_positioned_lines
from ..stroke import get_text_stroke_width
from .joint_layout import _candidate_bbox
from .profiling import get_solver_profile


def get_candidate_render_box(region, candidate, config, language_default="ENG"):
    """Return the cached RGBA crop for this candidate's exact typography."""
    bounds = tuple(map(int, _candidate_bbox(candidate)))
    fg, bg = fg_bg_compare(*region.get_font_colors())
    stroke = get_text_stroke_width(candidate.font_size, bg, getattr(region, "bg_colors", None))
    lines = [{"text": line.text, "x": line.x, "y": line.y,
              "width": line.width, "height": line.height} for line in candidate.lines]
    spacing = config.render.line_spacing or 0.0
    language = getattr(region, "target_lang", language_default)
    if language_default == "en_US" and not language:
        language = language_default
    horizontal = getattr(region, "direction", "hr") == "hr"
    key = (tuple(text_render.FONT_SELECTION_KEY), candidate.font_size, stroke,
           tuple(map(int, fg)), None if bg is None else tuple(map(int, bg)),
           float(spacing), language, horizontal, bounds[2] - bounds[0], bounds[3] - bounds[1],
           tuple((line["text"], line["x"] - bounds[0], line["y"] - bounds[1],
                  line["width"], line["height"]) for line in lines))
    profile = get_solver_profile()
    crop_key = ("crop", key)
    if getattr(candidate, "_render_box_key", None) == key:
        profile.compositor_cache_hits += 1
        return candidate._render_box
    box = profile.compositor_crops.get(crop_key)
    if box is None:
        profile.compositor_cache_misses += 1
        box = render_positioned_lines(lines, list(bounds), candidate.font_size, fg, bg,
                                      spacing, language, horizontal, stroke_width=stroke)
        if box is not None:
            profile.compositor_crops[crop_key] = box
    else:
        profile.compositor_cache_hits += 1
    candidate._render_box_key, candidate._render_box = key, box
    candidate._render_stroke_width = stroke
    return box


def footprint_cache_key(render_key, bounds, points, image_shape):
    return ("footprint", render_key, bounds,
            tuple(map(float, np.asarray(points, dtype=np.float32).ravel())),
            tuple(image_shape[:2]))
