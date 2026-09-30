import ctypes
from types import SimpleNamespace

import freetype
import numpy as np
import pytest

from manga_translator.rendering import get_default_eng_font, text_render


def test_glyph_pixels_and_metrics_survive_reusing_the_loaded_slot():
    face = text_render.get_cached_font(get_default_eng_font())
    face.set_pixel_sizes(0, 32)
    face.load_char("A")
    loaded = face.glyph
    bitmap = loaded.bitmap
    assert bitmap.rows > 0 and bitmap.width > 0
    assert bitmap.pitch == bitmap.width
    assert bitmap.pixel_mode == freetype.FT_PIXEL_MODE_GRAY
    expected_rows, expected_width = bitmap.rows, bitmap.width
    expected_pixels = bytes(bitmap.buffer)
    expected_metrics = (
        loaded.advance.x, loaded.advance.y, loaded.bitmap_left, loaded.bitmap_top,
        loaded.metrics.vertBearingX, loaded.metrics.vertBearingY,
        loaded.metrics.horiBearingX, loaded.metrics.horiBearingY,
        loaded.metrics.horiAdvance, loaded.metrics.vertAdvance,
    )

    glyph = text_render.Glyph(loaded)
    face.load_char("B")

    assert glyph.bitmap.buffer == expected_pixels
    np.testing.assert_array_equal(
        glyph.bitmap.array,
        np.frombuffer(expected_pixels, dtype=np.uint8).reshape(expected_rows, expected_width),
    )
    assert not glyph.bitmap.array.flags.writeable
    assert (
        glyph.advance.x, glyph.advance.y, glyph.bitmap_left, glyph.bitmap_top,
        glyph.metrics.vertBearingX, glyph.metrics.vertBearingY,
        glyph.metrics.horiBearingX, glyph.metrics.horiBearingY,
        glyph.metrics.horiAdvance, glyph.metrics.vertAdvance,
    ) == expected_metrics


def _loaded_glyph(bitmap):
    metrics = SimpleNamespace(
        vertBearingX=1, vertBearingY=2, horiBearingX=3, horiBearingY=4,
        horiAdvance=5, vertAdvance=6,
    )
    return SimpleNamespace(
        bitmap=bitmap, advance=SimpleNamespace(x=7, y=8), bitmap_left=9,
        bitmap_top=10, metrics=metrics,
    )


@pytest.mark.parametrize(
    "rows,width,pitch,pixel_mode,buffer,native_buffer,expected_array",
    [
        (0, 0, 0, freetype.FT_PIXEL_MODE_GRAY, [], None, None),
        (2, 2, 2, freetype.FT_PIXEL_MODE_GRAY, [11, 12, 13, 14], "null", (2, 2)),
        (2, 2, 3, freetype.FT_PIXEL_MODE_GRAY, [1, 2, 0, 3, 4, 0], b"abcdef", None),
        (2, 2, -2, freetype.FT_PIXEL_MODE_GRAY, [5, 6, 7, 8], b"abcd", (2, 2)),
        (2, 2, 2, freetype.FT_PIXEL_MODE_MONO, [9, 10, 11, 12], b"abcd", (2, 2)),
        (2, 2, 2, freetype.FT_PIXEL_MODE_GRAY, [13, 14, 15, 16], "unsupported", (2, 2)),
    ],
    ids=["empty", "null-buffer", "padded", "negative-pitch", "non-gray", "binding-representation"],
)
def test_unsupported_bitmaps_keep_the_binding_copy_behavior(
    rows, width, pitch, pixel_mode, buffer, native_buffer, expected_array,
):
    bitmap = SimpleNamespace(
        rows=rows, width=width, pitch=pitch, pixel_mode=pixel_mode, buffer=buffer,
    )
    native_storage = None
    if native_buffer == "null":
        bitmap._FT_Bitmap = SimpleNamespace(buffer=ctypes.POINTER(ctypes.c_ubyte)())
    elif isinstance(native_buffer, bytes):
        native_storage = (ctypes.c_ubyte * len(native_buffer)).from_buffer_copy(native_buffer)
        bitmap._FT_Bitmap = SimpleNamespace(
            buffer=ctypes.cast(native_storage, ctypes.POINTER(ctypes.c_ubyte)),
        )
    elif native_buffer == "unsupported":
        bitmap._FT_Bitmap = SimpleNamespace(buffer="not a ctypes pointer")

    glyph = text_render.Glyph(_loaded_glyph(bitmap))

    assert glyph.bitmap.buffer == bytes(buffer)
    if expected_array is None:
        assert glyph.bitmap.array is None
    else:
        assert glyph.bitmap.array.shape == expected_array
        assert not glyph.bitmap.array.flags.writeable
