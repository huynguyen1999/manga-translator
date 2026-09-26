"""Text stroke sizing shared by rendering and layout."""

import numpy as np


def get_text_stroke_width(font_size: int, stroke_color, source_background) -> int:
    if stroke_color is None:
        return 0
    dark_white_outline = (
        source_background is not None
        and np.mean(source_background) < 128
        and np.mean(stroke_color) > 127
    )
    return max(1, int(font_size * 0.07)) + int(dark_white_outline)
