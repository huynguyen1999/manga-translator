"""Bubble line-block centering and horizontal alignment."""

from __future__ import annotations

from typing import List, Optional, Tuple

from .geometry import BubbleGeometry
from .models import BandSlot, PlacedLine, PlacementTarget
from .validation import _bbox_validate


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

    # Generate search offsets sorted by Euclidean distance from (ideal_dx, ideal_dy)
    offsets: List[Tuple[int, int]] = []
    seen = set()

    for r in range(0, max_search_radius + 1, 2):
        for step_x in range(-r, r + 1, 2):
            for step_y in range(-r, r + 1, 2):
                if max(abs(step_x), abs(step_y)) == r:
                    cand_dx = ideal_dx + step_x
                    cand_dy = ideal_dy + step_y
                    if min_dx <= cand_dx <= max_dx and min_dy <= cand_dy <= max_dy:
                        if (cand_dx, cand_dy) not in seen:
                            seen.add((cand_dx, cand_dy))
                            offsets.append((cand_dx, cand_dy))

    offsets.sort(key=lambda off: (off[0] - ideal_dx) ** 2 + (off[1] - ideal_dy) ** 2)

    h, w = geom.shape
    best_translated: Optional[List[PlacedLine]] = None

    for dx, dy in offsets:
        # Check overall block bounds first
        new_left = block_left + dx
        new_right = block_right + dx
        new_top = block_top + dy
        new_bottom = block_bottom + dy
        if new_left < 0 or new_top < 0 or new_right > w or new_bottom > h:
            continue

        translated = [
            PlacedLine(
                text=ln.text,
                y=ln.y + dy,
                x=ln.x + dx,
                width=ln.width,
                height=ln.height,
                slot=BandSlot(
                    left=ln.slot.left + dx,
                    right=ln.slot.right + dx,
                    y_start=ln.slot.y_start + dy,
                    y_end=ln.slot.y_end + dy,
                ),
            )
            for ln in lines
        ]

        valid, _ = _bbox_validate(translated, geom, font_size, stroke_width, margin)
        if valid:
            best_translated = translated
            break

    return best_translated if best_translated is not None else lines


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


