"""Pure typography and ink-geometry helpers for free-text layout."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from ...config import Config

import numpy as np

from .. import get_default_eng_font, text_render
from .line_breaking import HARD_LINE_BREAK, _PUNCT_STRIP, _phrase_break_penalty, _precompute_widths, _word_core
from .hard_line_break_layout import explicit_break_candidate
from .models import BandSlot, FreeTextDamageTarget, LayoutCandidate, OriginalLayoutProfile, PanelConstraint, PlacedLine
from .raster import _candidate_cropped_visual_masks, _render_line_alpha
from .text_normalization import split_layout_words


def _free_text_words(text: str) -> List[str]:
    """Tokenize translated free text without inventing word fragments."""
    words: List[str] = []
    for word in split_layout_words(text):
        if word == HARD_LINE_BREAK:
            words.append(word)
            continue
        if words and not word.strip(_PUNCT_STRIP):
            words[-1] += word
        else:
            words.append(word)
    return words

_FREE_TEXT_MAX_LINE_SPACING = 0.25


def _free_text_line_spacing(value: Optional[float]) -> float:
    """Keep free-text leading typographic, never a source-height control."""
    return min(_FREE_TEXT_MAX_LINE_SPACING, max(0.0, float(value or 0.0)))


def _free_text_font_metrics(font_size: int) -> Dict[str, int]:
    """Return the active face's metric height and natural baseline advance."""
    try:
        from manga_translator.rendering import text_render

        if not text_render.FONT_SELECTION:
            text_render.set_font(get_default_eng_font())
        face = text_render.FONT_SELECTION[0]
        face.set_pixel_sizes(0, font_size)
        ascender = face.size.ascender >> 6
        descender = face.size.descender >> 6
        metric_height = max(1, ascender - descender)
        natural_advance = max(metric_height, face.size.height >> 6)
    except Exception:
        metric_height = max(1, int(math.ceil(font_size * 1.15)))
        natural_advance = metric_height
    return {
        "font_metric_height": int(metric_height),
        "natural_advance": int(natural_advance),
    }

def _free_text_line_height(font_size: int, line_spacing: float) -> int:
    metrics = _free_text_font_metrics(font_size)
    gap = int(round(font_size * _free_text_line_spacing(line_spacing)))
    return metrics["natural_advance"] + gap


def _mask_metrics(mask: np.ndarray, origin: Tuple[int, int] = (0, 0)) -> Dict[str, Any]:
    """Measure actual occupied pixels, rather than their allocated rectangle."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        x, y = origin
        return {
            "bbox": (x, y, x, y),
            "centroid": (float(x), float(y)),
            "width": 0,
            "height": 0,
            "area": 0,
        }
    ox, oy = origin
    return {
        "bbox": (int(xs.min()) + ox, int(ys.min()) + oy, int(xs.max()) + ox + 1, int(ys.max()) + oy + 1),
        "centroid": (ox + float(xs.mean()), oy + float(ys.mean())),
        "width": int(xs.max() - xs.min() + 1),
        "height": int(ys.max() - ys.min() + 1),
        "area": int(len(xs)),
    }


def _free_text_candidate_ink_metrics(candidate: LayoutCandidate) -> Dict[str, Any]:
    """Rasterize one page-independent candidate and report its true ink geometry."""
    if not candidate.lines:
        return _mask_metrics(np.zeros((0, 0), dtype=bool))

    right = max(line.x + line.width for line in candidate.lines) + candidate.font_size + 4
    bottom = max(line.y + line.height for line in candidate.lines) + candidate.font_size + 4
    _, ink_crop, _, _ = _candidate_cropped_visual_masks(
        candidate, 0, (max(1, bottom), max(1, right))
    )
    metrics = _mask_metrics(ink_crop)
    line_boxes = []
    for line in candidate.lines:
        alpha = _render_line_alpha(line, candidate.font_size)
        if alpha is None:
            continue
        line_metrics = _mask_metrics(alpha > 127, (line.x, line.y))
        if line_metrics["area"]:
            line_boxes.append(line_metrics["bbox"])

    line_gaps = [
        max(0, line_boxes[index + 1][1] - line_boxes[index][3])
        for index in range(len(line_boxes) - 1)
    ]
    metric = _free_text_font_metrics(candidate.font_size)
    advances = [candidate.lines[index + 1].y - candidate.lines[index].y for index in range(len(candidate.lines) - 1)]
    metrics.update({
        "ink_bbox": metrics["bbox"],
        "ink_width": metrics["width"],
        "ink_height": metrics["height"],
        "ink_area": metrics["area"],
        "ink_centroid": metrics["centroid"],
        "ink_line_height": int(round(np.mean([b[3] - b[1] for b in line_boxes]))) if line_boxes else 0,
        "font_metric_height": metric["font_metric_height"],
        "baseline_advance": int(round(np.mean(advances))) if advances else metric["natural_advance"],
        "baseline_advance_min": min(advances) if advances else metric["natural_advance"],
        "baseline_advance_max": max(advances) if advances else metric["natural_advance"],
        "visible_gap": max(line_gaps, default=0),
        "max_visible_gap": max(line_gaps, default=0),
    })
    return metrics


def _free_text_wrap_candidate(
    words: List[str],
    widths: List[int],
    space_width: int,
    font_size: int,
    line_spacing: float,
    line_count: int,
    target_width: float,
    max_line_width: Optional[float] = None,
) -> Optional[LayoutCandidate]:
    """Build one ordinary paragraph shape; every word stays atomic."""
    if not words or line_count < 1 or line_count > len(words):
        return None

    states: Dict[Tuple[int, int], Tuple[float, List[Tuple[int, int, int]]]] = {(0, 0): (0.0, [])}
    for used in range(line_count):
        next_states: Dict[Tuple[int, int], Tuple[float, List[Tuple[int, int, int]]]] = {}
        for (start, _), (cost, chunks) in states.items():
            max_end = len(words) - (line_count - used - 1)
            run_width = 0
            for end in range(start + 1, max_end + 1):
                run_width += widths[end - 1]
                if end - start > 1:
                    run_width += space_width
                if max_line_width is not None and run_width > max_line_width:
                    break
                remaining = line_count - used - 1
                remaining_words = len(words) - end
                if remaining_words < remaining:
                    continue

                line_cost = ((run_width - target_width) / max(1.0, float(font_size))) ** 2
                if remaining:
                    line_cost += _phrase_break_penalty(words[end - 1], words[end])
                    if end - start == 1 and len(words[end - 1].strip(_PUNCT_STRIP)) <= 3:
                        line_cost += 18.0
                new_cost = cost + line_cost
                key = (end, used + 1)
                old = next_states.get(key)
                if old is None or new_cost < old[0]:
                    next_states[key] = (new_cost, chunks + [(start, end, run_width)])
        states = next_states

    state = states.get((len(words), line_count))
    if state is None:
        return None

    _, chunks = state
    line_height = _free_text_line_height(font_size, line_spacing)
    max_width = max(chunk[2] for chunk in chunks)
    lines: List[PlacedLine] = []
    for row, (start, end, width) in enumerate(chunks):
        x = int(round((max_width - width) / 2.0))
        slot = BandSlot(left=0, right=max_width, y_start=row * line_height, y_end=row * line_height + font_size)
        lines.append(PlacedLine(
            text=" ".join(words[start:end]),
            y=row * line_height,
            x=x,
            width=width,
            height=font_size,
            slot=slot,
        ))
    return LayoutCandidate(
        font_size=font_size,
        y_origin=0,
        line_spacing=line_spacing,
        lines=lines,
        penalty=0.0,
        glyph_clearance_p5=0.0,
        status="free_text_typography",
        valid=True,
    )


def _free_text_typography_score(
    candidate: LayoutCandidate,
    profile: OriginalLayoutProfile,
    target: Optional[FreeTextDamageTarget] = None,
    target_width_override: Optional[float] = None,
    target_height_override: Optional[float] = None,
) -> float:
    """Score real glyph footprint without page coordinates or obstacle geometry."""
    ink = _free_text_candidate_ink_metrics(candidate)
    widths = [float(line.width) for line in candidate.lines]
    target_width = float(target_width_override or (target.width if target is not None else profile.block_width))
    target_height = float(target_height_override or (target.height if target is not None else profile.block_height))
    target_width = max(1.0, target_width)
    target_height = max(1.0, target_height)
    target_ar = max(0.05, target_width / target_height)
    ink_width = max(1.0, float(ink["ink_width"]))
    ink_height = max(1.0, float(ink["ink_height"]))
    ink_ar = max(0.05, ink_width / ink_height)

    # Tall targets get a stronger height term; wide targets get a stronger width term.
    width_weight, height_weight = (1.35, 1.0) if target_ar >= 1.0 else (1.0, 1.35)
    footprint_penalty = (
        width_weight * abs(ink_width - target_width) / target_width
        + height_weight * abs(ink_height - target_height) / target_height
    ) * 6.0
    aspect_penalty = abs(math.log(ink_ar / target_ar)) * 5.0
    mean_width = max(1.0, float(np.mean(widths)))
    ragged_penalty = float(np.var(widths)) / (mean_width * mean_width) * 8.0
    orphan_penalty = sum(
        12.0 for line in candidate.lines[:-1]
        if len(line.text.split()) == 1 and len(_word_core(line.text)) <= 3
    )
    line_penalty = abs(len(candidate.lines) - profile.line_count) * 1.5
    font_delta = candidate.font_size - profile.font_size
    font_penalty = abs(font_delta) / max(1.0, profile.font_size) * 8.0
    if font_delta > 0:
        font_penalty += font_delta * 8.0
    return footprint_penalty + aspect_penalty + ragged_penalty + orphan_penalty + line_penalty + font_penalty
def _panel_constraint_diagnostics(panel: Optional[PanelConstraint]) -> Optional[Dict[str, Any]]:
    if panel is None:
        return None
    return {
        "id": panel.panel_id,
        "bounds": list(panel.bounds),
        "confidence": float(panel.confidence),
        "source": panel.source,
        "margin": int(panel.margin),
    }


def _free_text_typography_candidates(
    text: str,
    profile: OriginalLayoutProfile,
    config: Config,
    image_shape: Tuple[int, int],
    target: Optional[FreeTextDamageTarget] = None,
    target_height: Optional[int] = None,
    panel_constraint: Optional[PanelConstraint] = None,
) -> List[LayoutCandidate]:
    """Generate frozen paragraph candidates; ``target_height`` is legacy-only."""
    # Keep the old keyword source-compatible, but never turn erased height into leading.
    del target_height
    words = _free_text_words(text)
    if not words:
        return []

    render_cfg = config.render
    minimum = render_cfg.font_size_minimum
    if minimum == -1:
        minimum = round(sum(image_shape) / 200)
    minimum = max(1, int(minimum))
    source_font = max(1, int(round(profile.font_size)))
    if render_cfg.font_size is not None:
        font_sizes = [max(minimum, int(render_cfg.font_size))]
    else:
        preferred = [source_font - 1, source_font - 2, source_font, source_font - 3]
        emergency = range(source_font - 4, minimum - 1, -1)
        font_sizes = list(dict.fromkeys(max(minimum, size) for size in [*preferred, *emergency]))

    candidates: List[LayoutCandidate] = []
    for font_size in font_sizes:
        widths, space_width = _precompute_widths(words, font_size)
        total_width = sum(widths) + max(0, len(words) - 1) * space_width
        line_spacing = _free_text_line_spacing(render_cfg.line_spacing)
        line_height = _free_text_line_height(font_size, line_spacing)
        target_width = float(target.width if target is not None else profile.block_width)
        max_height = max(1.0, profile.block_height * 1.5)
        target_height_value = min(float(target.height if target is not None else profile.block_height) * 1.1, max_height)
        max_width = target_width
        panel_width = None
        panel_meta = None
        if panel_constraint is not None:
            left, top, right, bottom = panel_constraint.bounds
            margin = max(0, int(panel_constraint.margin))
            panel_width = float(right - left - margin * 2)
            max_width = min(max_width, panel_width)
            max_height = min(max_height, bottom - top - margin * 2)
            if max_width <= 0 or max_height <= 0:
                continue
            target_width = min(target_width, float(max_width))
            target_height_value = min(target_height_value, float(max_height))
            panel_meta = _panel_constraint_diagnostics(panel_constraint)
        preferred_width = min(target_width, float(profile.block_width))
        max_width = panel_width if panel_width is not None else min(
            float(image_shape[1]), max(preferred_width, float(max(widths, default=0)))
        )
        target_ar = max(0.05, preferred_width / max(1.0, target_height_value))
        if HARD_LINE_BREAK in words:
            candidate = explicit_break_candidate(
                words, font_size, max_width, max_height, line_height, space_width,
                line_spacing, preferred_width, target_height_value, target_ar,
                profile, target, panel_meta,
            )
            if candidate is not None:
                candidates.append(candidate)
            continue
        preferred_lines = int(round(math.sqrt(total_width / max(1.0, target_ar * line_height))))
        preferred_lines = max(1, min(len(words), preferred_lines))
        line_counts = sorted({
            max(1, min(len(words), count))
            for count in range(min(preferred_lines, profile.line_count) - 2, max(preferred_lines, profile.line_count) + 3)
        })
        max_lines = max(1, int((max_height - font_size) // max(1, line_height)) + 1)
        line_counts = [count for count in line_counts if count <= max_lines]
        for line_count in line_counts:
            wrap_width = max(
                float(font_size * 2),
                total_width / line_count,
                target_ar * line_count * line_height,
            )
            if max_width is not None:
                wrap_width = min(wrap_width, float(max_width))
            candidate = _free_text_wrap_candidate(
                words, widths, space_width, font_size,
                line_spacing, line_count, wrap_width,
                max_line_width=float(max_width) if max_width is not None else None,
            )
            if candidate is None:
                continue
            candidate_width = max(line.x + line.width for line in candidate.lines) - min(line.x for line in candidate.lines)
            candidate_height = max(line.y + line.height for line in candidate.lines) - min(line.y for line in candidate.lines)
            if max_width is not None and candidate_width > max_width:
                continue
            if max_height is not None and candidate_height > max_height:
                continue
            candidate.penalty = _free_text_typography_score(
                candidate, profile, target,
                target_width_override=preferred_width,
                target_height_override=target_height_value,
            )
            ink = _free_text_candidate_ink_metrics(candidate)
            candidate.qa = {
                "font_size": candidate.font_size,
                "line_spacing": candidate.line_spacing,
                "lines": [line.text for line in candidate.lines],
                "line_widths": [line.width for line in candidate.lines],
                "typography_score": candidate.penalty,
                "ink_bbox": list(ink["ink_bbox"]), "ink_width": ink["ink_width"],
                "ink_height": ink["ink_height"], "ink_area": ink["ink_area"],
                "ink_aspect_ratio": ink["ink_width"] / max(1.0, ink["ink_height"]), "ink_line_height": ink["ink_line_height"],
                "font_metric_height": ink["font_metric_height"], "baseline_advance": ink["baseline_advance"],
                "baseline_advance_min": ink["baseline_advance_min"], "baseline_advance_max": ink["baseline_advance_max"],
                "visible_gap": ink["visible_gap"], "baseline_consistent": ink["baseline_advance_min"] == ink["baseline_advance_max"],
                "line_gap_sane": ink["visible_gap"] <= max(4, ink["font_metric_height"] * 0.5), "target_width": preferred_width,
                "target_height": target_height_value, "target_aspect_ratio": target_ar,
            }
            if panel_meta is not None:
                candidate.qa["panel_constraint"] = panel_meta
            candidates.append(candidate)

    candidates.sort(key=lambda candidate: (abs(candidate.font_size - source_font), candidate.penalty))
    unique, seen = [], set()
    for candidate in candidates:
        key = (candidate.font_size, tuple(line.text for line in candidate.lines))
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique
