"""Glyph measurement helpers for horizontal text layout."""

from . import text_render as _text_render
from .text_render import CJK_Compatibility_Forms_translate, get_char_glyph


def select_hyphenator(language):
    return _text_render.select_hyphenator(language)


def get_char_offset_x(font_size: int, cdpt: str):
    c, _ = CJK_Compatibility_Forms_translate(cdpt, 0)
    glyph = get_char_glyph(c, font_size, 0)
    bitmap = glyph.bitmap
    if bitmap.rows * bitmap.width == 0 or len(bitmap.buffer) != bitmap.rows * bitmap.width:
        return glyph.advance.x >> 6
    return glyph.metrics.horiAdvance >> 6


def get_string_width(font_size: int, text: str):
    return sum(get_char_offset_x(font_size, char) for char in text)
