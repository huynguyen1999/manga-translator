"""Lazy typography candidate generation for free-text regions."""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple

from ...config import Config
from .models import FreeTextDamageTarget, LayoutCandidate, OriginalLayoutProfile, PanelConstraint
from .free_text_typography import _free_text_words
from .line_breaking import _precompute_widths


def _free_text_typography_candidates(
    text: str, profile: OriginalLayoutProfile, config: Config, image_shape: Tuple[int, int],
    target: Optional[FreeTextDamageTarget] = None, target_height: Optional[int] = None,
    panel_constraint: Optional[PanelConstraint] = None, language: str = "en_US",
    preserve: bool = False, lazy_stages: bool = False, font_sizes: Optional[List[int]] = None,
    include_emergency: bool = True, only_emergency: bool = False,
    normal_candidates_by_size: Optional[Dict[int, List[LayoutCandidate]]] = None,
    minimum_font_size: Optional[int] = None, budget: Any = None,
) -> List[LayoutCandidate] | Tuple[Iterator[List[LayoutCandidate]], Dict[str, Any]]:
    """Generate frozen paragraph candidates; ``target_height`` is legacy-only."""
    from .free_text_stages import (
        _SPLIT_SHAPE_GAIN,
        _free_text_font_stage_candidates,
        _free_text_hyphenation_stage,
    )
    # Keep the old keyword source-compatible, but never turn erased height into leading.
    del target_height
    words = _free_text_words(text)
    if not words:
        if lazy_stages:
            return iter(()), {"candidate_count": 0, "highest_font_size": None, "font_sizes": set(), "tested_font_sizes": [], "candidates": []}
        return []

    render_cfg = config.render
    from .readable_text import readable_font_minimum
    minimum = minimum_font_size or readable_font_minimum(render_cfg, image_shape)
    source_font = max(minimum, int(round(profile.font_size)))
    preferred = max(minimum, int(render_cfg.font_size or source_font))
    width_limit = min(float(target.width if target is not None else profile.block_width), float(profile.block_width))
    if panel_constraint is not None:
        left, _, right, _ = panel_constraint.bounds
        width_limit = min(width_limit, float(right - left - max(0, int(panel_constraint.margin)) * 2))
    sizes = list(font_sizes) if font_sizes is not None else list(range(preferred, minimum - 1, -1))
    stats = {"candidate_count": 0, "highest_font_size": None, "font_sizes": set(), "tested_font_sizes": [], "candidates": []}

    def candidate_stages() -> Iterator[List[LayoutCandidate]]:
        def emergency_stage(size, normal_candidates):
            if budget is not None and budget.expired():
                return []
            tier = "source" if size == preferred else f"shrink_{size}"
            emergency = _free_text_hyphenation_stage(words, size, max(1, width_limit), language)
            if not emergency or not any("emergency" in c["strategies"] for c in emergency[1]):
                return []
            split_words, changes = emergency
            stage = _free_text_font_stage_candidates(
                split_words, size, f"{tier}_emergency", sum(len(c["fragments"]) - 1 for c in changes),
                changes, words, source_font, minimum, width_limit, profile, config, image_shape, target, panel_constraint,
                budget=budget,
            )
            unsplit = [candidate for candidate in normal_candidates if not candidate.qa.get("introduced_hyphen_count")]
            split = [candidate for candidate in normal_candidates if candidate.qa.get("introduced_hyphen_count")]
            unsplit_score = min((candidate.penalty for candidate in unsplit), default=float("inf"))
            widths, _ = _precompute_widths(words, size) if split else ([], 0)
            overwide = {word for word, width in zip(words, widths) if width >= width_limit * 1.5}
            severe = overwide if overwide and any(overwide <= set(c.qa["introduced_hyphen_words"]) for c in split) else set()
            if severe:
                stage = [candidate for candidate in stage if severe <= set(candidate.qa["introduced_hyphen_words"])]
            elif unsplit:
                stage = [candidate for candidate in stage if candidate.penalty <= unsplit_score * (1.0 - _SPLIT_SHAPE_GAIN)]
            return stage

        if only_emergency:
            if not preserve and not getattr(render_cfg, "no_hyphenation", False):
                normal_by_size = normal_candidates_by_size or {}
                for size in sizes:
                    if budget is not None and budget.expired():
                        return
                    stats["tested_font_sizes"].append(size)
                    stage = emergency_stage(size, normal_by_size.get(size, []))
                    if stage:
                        stats["candidate_count"] += len(stage)
                        stats["font_sizes"].add(size)
                        stats["highest_font_size"] = max(size, stats["highest_font_size"] or size)
                        stats["candidates"].extend(stage)
                        yield stage
            return

        deferred_emergency_sizes = []
        normal_by_size = {}
        for size in sizes:
            if budget is not None and budget.expired():
                return
            stats["tested_font_sizes"].append(size)
            tier = "source" if size == preferred else f"shrink_{size}"
            variants = [(words, tier, 0, [])]
            if not preserve and not getattr(render_cfg, "no_hyphenation", False):
                rescue = _free_text_hyphenation_stage(words, size, max(1, width_limit), language, False)
                if rescue:
                    split_words, changes = rescue
                    variants.append((split_words, f"{tier}_wrap", sum(len(c["fragments"]) - 1 for c in changes), changes))
            normal_candidates = [
                candidate
                for stage_words, stage_tier, hyphen_count, hyphen_words in variants
                for candidate in _free_text_font_stage_candidates(
                    stage_words, size, stage_tier, hyphen_count, hyphen_words, words,
                    source_font, minimum, width_limit, profile, config, image_shape, target, panel_constraint,
                    budget=budget,
                )
            ]
            if include_emergency and not preserve and not getattr(render_cfg, "no_hyphenation", False):
                deferred_emergency_sizes.append(size)

            unique, seen = [], set()
            for candidate in normal_candidates:
                key = (candidate.font_size, candidate.qa.get("selected_tier"), tuple(line.text for line in candidate.lines))
                if key not in seen:
                    unique.append(candidate)
                    seen.add(key)
            unsplit_candidates = [candidate for candidate in unique if not candidate.qa.get("introduced_hyphen_count")]
            unsplit_score = min((candidate.penalty for candidate in unsplit_candidates), default=float("inf"))
            split_candidates = [candidate for candidate in unique if candidate.qa.get("introduced_hyphen_count")]
            widths, _ = _precompute_widths(words, size) if split_candidates else ([], 0)
            overwide = {word for word, width in zip(words, widths) if width >= width_limit * 1.5}
            severe_split_words = overwide if overwide and any(overwide <= set(c.qa["introduced_hyphen_words"]) for c in split_candidates) else set()
            normal_by_size[size] = normal_candidates

            def keep(candidate: LayoutCandidate) -> bool:
                if severe_split_words:
                    return severe_split_words <= set(candidate.qa["introduced_hyphen_words"])
                return not candidate.qa.get("introduced_hyphen_count") or not unsplit_candidates or candidate.penalty <= unsplit_score * (1.0 - _SPLIT_SHAPE_GAIN)

            normal_ids = {id(candidate) for candidate in normal_candidates}
            normal_stage = [candidate for candidate in unique if id(candidate) in normal_ids and keep(candidate)]
            retained_count = len(normal_stage)
            if retained_count:
                stats["candidate_count"] += retained_count
                stats["font_sizes"].add(size)
                stats["highest_font_size"] = max(size, stats["highest_font_size"] or size)
                stats["candidates"].extend(normal_stage)
            if normal_stage:
                yield normal_stage
        for size in deferred_emergency_sizes:
            if budget is not None and budget.expired():
                return
            normal_candidates = (
                normal_candidates_by_size.get(size, []) if normal_candidates_by_size is not None
                else normal_by_size.get(size, [])
            )
            stage = emergency_stage(size, normal_candidates)
            if stage:
                stats["candidate_count"] += len(stage)
                stats["font_sizes"].add(size)
                stats["highest_font_size"] = max(size, stats["highest_font_size"] or size)
                stats["candidates"].extend(stage)
                yield stage

    if lazy_stages:
        return candidate_stages(), stats
    return [candidate for stage in candidate_stages() for candidate in stage]
