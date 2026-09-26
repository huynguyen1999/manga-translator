"""Compatibility facade for horizontal text rendering."""

from .text_render_horizontal_composition import (
    _binary_search_font_size,
    _crop_and_position_box,
    _evaluate_bubble_layout_candidates,
    _render_horizontal_lines,
    _score_layout_candidate,
    put_text_horizontal,
)
from .text_render_horizontal_glyphs import put_char_horizontal
from .text_render_horizontal_layout import (
    calc_horizontal,
    get_char_offset_x,
    get_string_width,
    select_hyphenator,
)
