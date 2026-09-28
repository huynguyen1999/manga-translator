"""Bubble line-block centering and horizontal alignment."""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional, Tuple

from .geometry import BubbleGeometry
from .models import BandSlot, PlacedLine, PlacementTarget


@lru_cache(maxsize=16)
def _relative_search_offsets(max_search_radius: int) -> Tuple[Tuple[int, int], ...]:
    offsets = (
        (x, y)
        for radius in range(0, max_search_radius + 1, 2)
        for x in range(-radius, radius + 1, 2)
        for y in range(-radius, radius + 1, 2)
        if max(abs(x), abs(y)) == radius
    )
    return tuple(sorted(offsets, key=lambda offset: offset[0] ** 2 + offset[1] ** 2))


def _translation_fits_safe_mask(lines: List[PlacedLine], dx: int, dy: int, safe, shape) -> bool:
    h, w = shape
    for line in lines:
        x, y, width, height = line.x + dx, line.y + dy, line.width, line.height
        if x < 0 or y < 0 or x + width > w or y + height > h:
            return False
        for sx, sy in (
            (x, y), (x + width - 1, y), (x, y + height - 1), (x + width - 1, y + height - 1),
            (x + width // 2, y + height // 2), (x + width // 4, y + height // 2),
            (x + 3 * width // 4, y + height // 2),
        ):
            if not safe[max(0, min(h - 1, sy)), max(0, min(w - 1, sx))]:
                return False
    return True


def _center_layout_block(
    lines: List[PlacedLine],
    geom: BubbleGeometry,
    target: PlacementTarget,
    font_size: int,
    stroke_width: int = 0,
    margin: float = 2.0,
    max_search_radius: int = 24,
) -> Optional[List[PlacedLine]]:
    """Translate the entire finished text block towards the PlacementTarget center.

    Searches nearby legal integer translations (dx, dy) and validates candidate
    glyphs against the safe mask, picking the valid placement closest to ideal.
    """
    if not lines:
        return lines

    block_left = min(line.x for line in lines)
    block_right = max(line.x + line.width for line in lines)
    block_top = min(line.y for line in lines)
    block_bottom = max(line.y + line.height for line in lines)

    block_cx = (block_left + block_right) / 2.0
    block_cy = (block_top + block_bottom) / 2.0

    ideal_dx = int(round(target.center_x - block_cx))
    ideal_dy = int(round(target.center_y - block_cy))

    # Determine safe mask bounding box to bound translations early
    sx1, sy1, sx2, sy2 = geom.safe_bounding_box(font_size, stroke_width, margin)
    min_dy = sy1 - block_top
    max_dy = sy2 - block_bottom
    min_dx = sx1 - block_left
    max_dx = sx2 - block_right

    h, w = geom.shape
    safe = None
    for offset_x, offset_y in _relative_search_offsets(max_search_radius):
        dx, dy = ideal_dx + offset_x, ideal_dy + offset_y
        if not (min_dx <= dx <= max_dx and min_dy <= dy <= max_dy):
            continue
        if (block_left + dx < 0 or block_top + dy < 0
                or block_right + dx > w or block_bottom + dy > h):
            continue

        if safe is None:
            safe = geom.safe_pixels(font_size, stroke_width, margin)
        if not _translation_fits_safe_mask(lines, dx, dy, safe, (h, w)):
            continue

        return [PlacedLine(
            text=ln.text, y=ln.y + dy, x=ln.x + dx, width=ln.width, height=ln.height,
            slot=BandSlot(left=ln.slot.left + dx, right=ln.slot.right + dx,
                          y_start=ln.slot.y_start + dy, y_end=ln.slot.y_end + dy),
        ) for ln in lines]

    return lines


def _optimize_x(
    lines: List[PlacedLine],
    lam1: float = 1.0,
    lam2: float = 0.5,
    lam3: float = 0.2,
    source_center_x: Optional[float] = None,
    zone_center_x: Optional[float] = None,
) -> List[PlacedLine]:
    """Greedy coordinate descent on line X positions.

    All cost terms operate on *line centers* (x + width / 2), never on left
    edges, so lines of different widths stay visually aligned.
    """
    if not lines:
        return lines

    n = len(lines)
    bounds: List[Tuple[int, int, float, int]] = []
    for line in lines:
        slot = line.slot
        L = slot.left
        R_w = max(slot.left, slot.right - line.width)
        bounds.append((L, R_w, slot.center, line.width))

    def _center(x: float, w: int) -> float:
        return x + w / 2.0

    xs = [max(b[0], min(b[1], int(round(b[2] - b[3] / 2.0))))
          for b in bounds]

    denom = lam1 + lam2 + 4.0 * lam3
    for _ in range(4):
        for i in range(n):
            L, R_w, c, w_i = bounds[i]
            if L >= R_w:
                continue
            c_prev = _center(xs[i - 1], bounds[i - 1][3]) if i > 0 else _center(xs[i], w_i)
            c_next = _center(xs[i + 1], bounds[i + 1][3]) if i < n - 1 else _center(xs[i], w_i)

            target = c
            if zone_center_x is not None:
                target = 0.50 * zone_center_x + 0.50 * target
            if source_center_x is not None:
                target = 0.70 * target + 0.30 * source_center_x
            num = lam1 * target + lam2 * c_prev + 2.0 * lam3 * (c_next + c_prev)
            X_opt = num / denom
            x_opt = X_opt - w_i / 2.0
            xs[i] = max(L, min(R_w, int(round(x_opt))))

    return [
        PlacedLine(text=line.text, y=line.y, x=xs[i],
                   width=line.width, height=line.height, slot=line.slot)
        for i, line in enumerate(lines)
    ]


def _x_cost(center_x: float, c: float, c_prev: float, c_next: float,
            lam1: float, lam2: float, lam3: float) -> float:
    """Cost on line centers: slot center attraction + jitter + curvature."""
    return (
        lam1 * (center_x - c) ** 2
        + lam2 * (center_x - c_prev) ** 2
        + lam3 * (c_next - 2.0 * center_x + c_prev) ** 2
    )
