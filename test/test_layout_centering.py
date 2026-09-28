import numpy as np

from manga_translator.rendering.layout import centering
from manga_translator.rendering.layout.geometry import BubbleGeometry
from manga_translator.rendering.layout.models import BandSlot, PlacedLine, PlacementTarget
from manga_translator.rendering.layout.validation import _bbox_validate


def _reference_center_layout_block(lines, geom, target, font_size, stroke_width=0, margin=2.0, max_search_radius=24):
    if not lines:
        return lines

    block_left = min(line.x for line in lines)
    block_right = max(line.x + line.width for line in lines)
    block_top = min(line.y for line in lines)
    block_bottom = max(line.y + line.height for line in lines)
    ideal_dx = int(round(target.center_x - (block_left + block_right) / 2.0))
    ideal_dy = int(round(target.center_y - (block_top + block_bottom) / 2.0))

    sx1, sy1, sx2, sy2 = geom.safe_bounding_box(font_size, stroke_width, margin)
    min_dy, max_dy = sy1 - block_top, sy2 - block_bottom
    min_dx, max_dx = sx1 - block_left, sx2 - block_right

    offsets = []
    seen = set()
    for radius in range(0, max_search_radius + 1, 2):
        for step_x in range(-radius, radius + 1, 2):
            for step_y in range(-radius, radius + 1, 2):
                if max(abs(step_x), abs(step_y)) == radius:
                    dx, dy = ideal_dx + step_x, ideal_dy + step_y
                    if min_dx <= dx <= max_dx and min_dy <= dy <= max_dy:
                        if (dx, dy) not in seen:
                            seen.add((dx, dy))
                            offsets.append((dx, dy))
    offsets.sort(key=lambda offset: (offset[0] - ideal_dx) ** 2 + (offset[1] - ideal_dy) ** 2)

    h, w = geom.shape
    for dx, dy in offsets:
        if (
            block_left + dx < 0 or block_top + dy < 0
            or block_right + dx > w or block_bottom + dy > h
        ):
            continue
        translated = [
            PlacedLine(
                text=line.text,
                y=line.y + dy,
                x=line.x + dx,
                width=line.width,
                height=line.height,
                slot=BandSlot(
                    left=line.slot.left + dx,
                    right=line.slot.right + dx,
                    y_start=line.slot.y_start + dy,
                    y_end=line.slot.y_end + dy,
                ),
            )
            for line in lines
        ]
        valid, _ = _bbox_validate(translated, geom, font_size, stroke_width, margin)
        if valid:
            return translated
    return lines


def _make_lines(x, y):
    return [
        PlacedLine("first", y, x, 10, 6, BandSlot(x - 2, x + 12, y - 1, y + 7)),
        PlacedLine("second", y + 9, x + 3, 14, 7, BandSlot(x + 1, x + 18, y + 8, y + 17)),
    ]


def _fingerprint(lines):
    return tuple(
        (
            line.text, line.x, line.y, line.width, line.height,
            None if line.slot is None else (
                line.slot.left, line.slot.right, line.slot.y_start, line.slot.y_end,
            ),
        )
        for line in lines
    )


def _target(geom, center_x, center_y):
    return PlacementTarget(
        mask=np.zeros(geom.shape, dtype=np.uint8),
        center_x=center_x,
        center_y=center_y,
        bbox=(0, 0, geom.shape[1], geom.shape[0]),
        preferred_center_x=center_x,
        preferred_center_y=center_y,
    )


def _geometry(kind):
    if kind == "rectangle":
        mask = np.ones((80, 100), dtype=np.uint8)
    elif kind == "ellipse":
        yy, xx = np.ogrid[:80, :100]
        mask = (((xx - 50) / 45) ** 2 + ((yy - 40) / 34) ** 2 <= 1).astype(np.uint8)
    else:
        mask = np.ones((80, 100), dtype=np.uint8)
        mask[34:46, 34:66] = 0
    return BubbleGeometry(mask)


def test_centering_matches_reference_for_varied_masks_and_placements():
    cases = [
        ("rectangle", (15, 14), (50, 40)),
        ("ellipse", (14, 13), (34, 36)),
        # The desired center falls inside the hole, forcing ordered search trials.
        ("ring", (44, 34), (50, 40)),
    ]
    for mask_kind, origin, target_center in cases:
        geom = _geometry(mask_kind)
        reference_lines = _make_lines(*origin)
        actual_lines = _make_lines(*origin)
        target = _target(geom, *target_center)

        expected = _reference_center_layout_block(reference_lines, geom, target, 12)
        actual = centering._center_layout_block(actual_lines, geom, target, 12)

        assert _fingerprint(actual) == _fingerprint(expected)
        if expected is reference_lines:
            assert actual is actual_lines
        else:
            assert actual is not actual_lines
    assert centering._relative_search_offsets(24) is centering._relative_search_offsets(24)


def test_only_winning_translation_materializes_lines_and_slots(monkeypatch):
    geom = _geometry("ring")
    lines = _make_lines(44, 34)
    target = _target(geom, 50, 40)
    expected = _reference_center_layout_block(_make_lines(44, 34), geom, target, 12)
    assert expected[0].x != lines[0].x or expected[0].y != lines[0].y

    line_count = 0
    slot_count = 0

    def make_line(*args, **kwargs):
        nonlocal line_count
        line_count += 1
        return PlacedLine(*args, **kwargs)

    def make_slot(*args, **kwargs):
        nonlocal slot_count
        slot_count += 1
        return BandSlot(*args, **kwargs)

    monkeypatch.setattr(centering, "PlacedLine", make_line)
    monkeypatch.setattr(centering, "BandSlot", make_slot)
    actual = centering._center_layout_block(lines, geom, target, 12)

    assert _fingerprint(actual) == _fingerprint(expected)
    assert line_count == slot_count == len(lines)


def test_failed_search_returns_original_lines_by_identity():
    geom = _geometry("ring")
    lines = _make_lines(40, 30)

    result = centering._center_layout_block(lines, geom, _target(geom, 50, 40), 1000)

    assert result is lines
