import numpy as np
import pytest

from manga_translator.rendering.layout.line_breaking import _build_row_slot_table
from manga_translator.rendering.layout.models import BandSlot


class SafeMaskGeometry:
    def __init__(self, safe):
        self._safe = np.asarray(safe, dtype=bool)
        ys, xs = np.nonzero(self._safe)
        self._bbox = (
            (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
            if len(ys)
            else (0, 0, 0, 0)
        )

    def safe_bounding_box(self, font_size, stroke_width, margin):
        return self._bbox

    def safe_pixels(self, font_size, stroke_width, margin):
        return self._safe


def _window_scan_oracle(safe, font_size, min_width):
    safe = np.asarray(safe, dtype=bool)
    ys, xs = np.nonzero(safe)
    if not len(ys):
        return {}
    first_y, stop_y = int(ys.min()), min(safe.shape[0] - font_size + 1, int(ys.max()) + 1 - font_size + 1)
    if stop_y <= max(0, first_y):
        return {}

    table = {}
    for y in range(max(0, first_y), stop_y):
        band = np.all(safe[y : y + font_size], axis=0)
        slots = []
        start = None
        for x, is_safe in enumerate(np.append(band, False)):
            if is_safe and start is None:
                start = x
            elif not is_safe and start is not None:
                if x - start >= min_width:
                    slots.append(BandSlot(start, x, y, y + font_size))
                start = None
        table[y] = slots
    return table


@pytest.mark.parametrize(
    ("safe", "font_size", "min_width", "expected"),
    [
        (
            [
                [1, 1, 1, 0, 0, 1, 1, 1, 0, 0],
                [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                [0, 0, 0, 1, 1, 0, 0, 1, 1, 1],
                [0, 0, 0, 0, 1, 1, 1, 0, 0, 0],
            ],
            1,
            3,
            {
                0: [BandSlot(0, 3, 0, 1), BandSlot(5, 8, 0, 1)],
                1: [],
                2: [BandSlot(7, 10, 2, 3)],
                3: [BandSlot(4, 7, 3, 4)],
            },
        ),
        (
            [
                [0] * 12,
                [0, 0, 0, 1, 1, 1, 0, 0, 1, 1, 0, 0],
                [0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0],
                [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0],
                [0] * 12,
            ],
            2,
            2,
            {
                1: [BandSlot(3, 6, 1, 3)],
                2: [],
                3: [BandSlot(8, 11, 3, 5)],
            },
        ),
        (
            [[0, 0, 0, 0, 0], [0, 0, 1, 1, 0]],
            0,
            3,
            {
                1: [BandSlot(0, 5, 1, 1)],
                2: [BandSlot(0, 5, 2, 2)],
            },
        ),
    ],
)
def test_batched_row_slots_match_window_scan(safe, font_size, min_width, expected):
    geom = SafeMaskGeometry(safe)

    actual = _build_row_slot_table(geom, font_size, min_width=min_width)

    assert actual == expected
    assert actual == _window_scan_oracle(safe, font_size, min_width)


@pytest.mark.parametrize(
    ("safe", "font_size"),
    [([[0, 0], [0, 0]], 1), ([[1, 1], [1, 1]], 3)],
)
def test_row_slots_are_empty_without_valid_safe_height(safe, font_size):
    assert _build_row_slot_table(SafeMaskGeometry(safe), font_size) == {}
