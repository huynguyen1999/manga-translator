"""Forced-paragraph wrapping for free-text candidates."""

from .models import LayoutCandidate, PlacedLine


def explicit_break_candidate(
    words, font_size, max_width, max_height, line_height, space_width,
    line_spacing, preferred_width, target_height, target_ar, profile, target,
    panel_meta,
):
    from .free_text_typography import _free_text_typography_score, _free_text_wrap_candidate
    from .line_breaking import HARD_LINE_BREAK, _precompute_widths

    paragraphs = [[]]
    for word in words:
        if word == HARD_LINE_BREAK:
            paragraphs.append([])
        else:
            paragraphs[-1].append(word)
    row, lines = 0, []
    for paragraph in paragraphs:
        if not paragraph:
            row += 1
            continue
        widths, _ = _precompute_widths(paragraph, font_size)
        if max(widths, default=0) > max_width:
            return None
        count, used_width = 1, 0
        for width in widths:
            next_width = width + (space_width if used_width else 0)
            if used_width and used_width + next_width > max_width:
                count, used_width = count + 1, width
            else:
                used_width += next_width
        wrapped = _free_text_wrap_candidate(
            paragraph, widths, space_width, font_size, line_spacing, count,
            preferred_width, max_line_width=max_width,
        )
        if wrapped is None:
            return None
        lines.extend(
            PlacedLine(
                text=line.text, y=line.y + row * line_height, x=line.x,
                width=line.width, height=line.height, slot=line.slot,
            )
            for line in wrapped.lines
        )
        row += len(wrapped.lines)
    if not lines:
        return None
    candidate_height = max(line.y + line.height for line in lines)
    if candidate_height > max_height or row * line_height > max_height + line_height:
        return None
    candidate = LayoutCandidate(
        font_size=font_size, y_origin=0, line_spacing=line_spacing,
        lines=lines, penalty=0.0, glyph_clearance_p5=0.0,
        status="free_text_typography", valid=True,
    )
    candidate.penalty = _free_text_typography_score(
        candidate, profile, target,
        target_width_override=preferred_width,
        target_height_override=target_height,
    )
    candidate.qa = {
        "font_size": font_size, "line_spacing": line_spacing,
        "lines": [line.text for line in lines],
        "line_widths": [line.width for line in lines],
        "typography_score": candidate.penalty, "explicit_line_breaks": True,
        "target_width": preferred_width, "target_height": target_height,
        "target_aspect_ratio": target_ar,
    }
    if panel_meta is not None:
        candidate.qa["panel_constraint"] = panel_meta
    return candidate
