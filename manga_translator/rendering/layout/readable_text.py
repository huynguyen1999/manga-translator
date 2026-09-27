"""Shared readable-size policy and bounded long-token wrapping."""

import math
import regex

from .hard_line_breaks import HARD_LINE_BREAK, precompute_widths


def readable_font_minimum(config, image_shape):
    configured = config.font_size_minimum
    if configured == -1:
        configured = round(sum(image_shape[:2]) / 200)
    return max(1, int(configured), math.ceil(12 * max(image_shape[:2]) / 2048))


def _grapheme_boundaries(text):
    clusters = regex.findall(r"\X", text)
    boundaries, offset = [], 0
    for cluster in clusters[:-1]:
        offset += len(cluster)
        boundaries.append(offset)
    return boundaries


def _split_existing_breaks(token):
    parts, remaining = [], token
    while True:
        boundaries = _grapheme_boundaries(remaining)
        natural = next(
            (i for i in boundaries if remaining[i - 1] in "-‐/" and any(c.isalnum() for c in remaining[i:])),
            None,
        )
        if natural is None:
            parts.append(remaining)
            break
        parts.append(remaining[:natural])
        remaining = remaining[natural:]
    return parts


def _single_hyphen_split(subtoken, width, width_limit, hyphenator, allow_emergency):
    boundaries = _grapheme_boundaries(subtoken)
    if not boundaries:
        return None
    dictionary = []
    core = subtoken.rstrip(".,!?;:…'\"”’)]-")
    min_letters = 2 if sum(c.isalpha() for c in core) <= 5 else 3
    if hyphenator is not None and core.isalpha():
        try:
            syllables = hyphenator.syllables(core.lower())
        except (ValueError, KeyError):
            syllables = []
        if "".join(syllables) == core.lower():
            offset = 0
            for syllable in syllables[:-1]:
                offset += len(syllable)
                if offset in boundaries:
                    dictionary.append(offset)
    for strategy, positions in (("dictionary", dictionary), ("emergency", boundaries)):
        if strategy == "emergency" and not allow_emergency:
            continue
        valid = []
        for index in positions:
            if sum(c.isalpha() for c in subtoken[:index]) < min_letters or sum(c.isalpha() for c in subtoken[index:]) < min_letters:
                continue
            if not any(c.isalnum() for c in subtoken[index:]):
                continue
            continuation = "-" if subtoken[index - 1].isalpha() else ""
            left, right = subtoken[:index] + continuation, subtoken[index:]
            left_w, right_w = width(left), width(right)
            if left_w <= width_limit and right_w <= width_limit:
                valid.append(((max(left_w, right_w), abs(left_w - right_w), -index), left, right, strategy))
        if valid:
            _, left, right, strategy = min(valid, key=lambda item: item[0])
            return left, right, strategy
    return None


def split_oversized_words(words, font_size, width_limit, language, allow_emergency=True):
    """Preserve tokens, inserting at most one forced hyphen per oversized sub-token."""
    from .. import text_render

    def width(text):
        return precompute_widths([text], font_size)[0][0]

    output, changes = [], []
    hyphenator = text_render.select_hyphenator(language)
    for original in words:
        if original == HARD_LINE_BREAK or not any(c.isalpha() for c in original) or width(original) <= width_limit:
            output.append(original)
            continue
        parts = _split_existing_breaks(original)
        fragments, strategies = [], []
        introduced_hyphens = 0
        feasible = True
        for part_index, part in enumerate(parts):
            if part_index:
                strategies.append("existing_break")
            if any(c.isalpha() for c in part) and width(part) > width_limit:
                if introduced_hyphens >= 1:
                    feasible = False
                    break
                split_pair = _single_hyphen_split(part, width, width_limit, hyphenator, allow_emergency)
                if split_pair is None:
                    feasible = False
                    break
                left, right, strategy = split_pair
                fragments.extend((left, right))
                strategies.append(strategy)
                introduced_hyphens += 1
            else:
                fragments.append(part)
        if not feasible or len(fragments) < 2:
            output.append(original)
            continue
        for index, fragment in enumerate(fragments):
            if index:
                output.append(HARD_LINE_BREAK)
            output.append(fragment)
        changes.append({"word": original, "fragments": fragments, "strategies": strategies})
    return output, changes


def centered_candidate_key(candidate):
    error = candidate.qa.get("center_error_px", float("inf"))
    centered = error <= candidate.font_size / 2
    return (not centered, -candidate.font_size if centered else error, candidate.penalty)
