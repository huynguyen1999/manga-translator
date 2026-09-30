"""Invariant free-text preparation reused across placement-domain retries."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ...config import Config
from ...utils import is_preserved_region
from .. import fg_bg_compare, stroke
from .free_text_stages import _free_text_typography_candidates, _group_typography_stages
from .models import CandidateRaster, FreeTextDamageTarget, LayoutCandidate, OriginalLayoutProfile
from .profiling import get_solver_profile
from .source_profile import build_original_layout_profile


class _SourceHeightStageProvider:
    """Cache source-height-adjusted stages so placement retries reuse them."""

    def __init__(self, stages, stats, profile, target, panel, budget=None):
        self._source = iter(stages)
        self.stats = stats
        self._profile = profile
        self._target = target
        self._panel = panel
        self._budget = budget
        self._stages = []
        self._finished = False
        self.on_generate = lambda _elapsed: None

    def _stage_at(self, index):
        while len(self._stages) <= index and not self._finished:
            started = perf_counter()
            if self._budget is not None and self._budget.expired():
                self._finished = True
                self.on_generate(perf_counter() - started)
                break
            try:
                raw_stage = next(self._source)
            except StopIteration:
                self._finished = True
                self.on_generate(perf_counter() - started)
                break
            stage = _source_height_candidate_list(
                raw_stage, self._profile, self._target, self._panel,
            )
            self.on_generate(perf_counter() - started)
            if stage:
                self._stages.append(stage)
        return self._stages[index] if index < len(self._stages) else None

    def first_stage(self):
        return self._stage_at(0) or []

    def __getitem__(self, index):
        if index < 0:
            raise IndexError(index)
        stage = self._stage_at(index)
        if stage is None:
            raise IndexError(index)
        return stage

    def __iter__(self):
        index = 0
        while (stage := self._stage_at(index)) is not None:
            yield stage
            index += 1


def _source_height_candidate_list(candidates, profile, target, panel):
    minimum = max(1, int(math.ceil(profile.block_height)))
    maximum = max(minimum, max((max(line.y + line.height for line in candidate.lines) for candidate in candidates), default=0))
    if panel is not None:
        _, top, _, bottom = panel.bounds
        margin = max(0, int(panel.margin))
        maximum = min(maximum, bottom - top - margin * 2)
        minimum = min(minimum, max(1, bottom - margin - max(top + margin, int(profile.bbox[1]))))
    if maximum <= 0: return []
    minimum = min(minimum, maximum)
    fitted = []
    for candidate in candidates:
        top = min(line.y for line in candidate.lines)
        bottom = max(line.y + line.height for line in candidate.lines)
        content_height = bottom - top
        if content_height > maximum: continue
        height = max(minimum, content_height)
        offset = -top
        for line in candidate.lines:
            line.y += offset
            line.slot.y_start += offset
            line.slot.y_end += offset
        candidate.layout_bounds = (
            min(line.x for line in candidate.lines), 0,
            max(line.x + line.width for line in candidate.lines), height,
        )
        fitted.append(candidate)
    return fitted


def _source_height_candidates(candidates, profile, target, panel, budget=None):
    if isinstance(candidates, tuple) and len(candidates) == 2:
        return _SourceHeightStageProvider(*candidates, profile, target, panel, budget)
    return _source_height_candidate_list(candidates, profile, target, panel)


@dataclass
class FreeTextSolveContext:
    initialized: bool = False
    profile: Optional[OriginalLayoutProfile] = None
    target: Optional[FreeTextDamageTarget] = None
    typography: List[LayoutCandidate] = field(default_factory=list)
    stage_groups: Any = field(default_factory=list)
    damage_centroid: Tuple[float, float] = (0.0, 0.0)
    target_width: float = 0.0
    target_height: float = 0.0
    stroke_width: int = 0
    source: Optional[np.ndarray] = None
    other_text: Optional[np.ndarray] = None
    candidate_rasters: Dict[int, CandidateRaster] = field(default_factory=dict)
    prepared_candidates: Dict[int, Tuple[Any, ...]] = field(default_factory=dict)
    tested_font_sizes: set[int] = field(default_factory=set)
    stage_extensions: Dict[Tuple[Tuple[int, ...], bool], Any] = field(default_factory=dict)
    _config: Optional[Config] = None
    _zone: Any = None
    _image_shape: Optional[Tuple[int, int]] = None
    _text: str = ""
    _language: str = "en_US"
    _preserve: bool = False
    coarse_font_search: bool = False
    search_budget: Any = None
    minimum_font_size: Optional[int] = None
    readable_minimum: Optional[int] = None
    rescue_minimum: Optional[int] = None

    def _track_stages(self, provider, stats, region, *, append_candidates):
        prof = get_solver_profile()
        counted = listed = recorded_sizes = 0
        annotated = 0

        def record_generated(elapsed):
            nonlocal counted, listed, annotated, recorded_sizes
            prof.ft_typography_ms += elapsed * 1000.0
            current = stats["candidate_count"]
            prof.free_text_typography_candidates += current - counted
            counted = current
            tested_sizes = stats.get("tested_font_sizes", [])
            sizes = tested_sizes[recorded_sizes:]
            self.tested_font_sizes.update(sizes)
            prof.fonts_tested += len(sizes)
            prof.attempted_font_sizes.extend(size for size in sizes if size not in prof.attempted_font_sizes)
            recorded_sizes = len(tested_sizes)
            if self.rescue_minimum is not None and self.readable_minimum is not None:
                for candidate in stats["candidates"][annotated:]:
                    if candidate.font_size < self.readable_minimum:
                        candidate.qa.update({
                            "small_text_rescue": True,
                            "rescue_minimum": self.rescue_minimum,
                        })
            annotated = len(stats["candidates"])
            if append_candidates:
                self.typography.extend(stats["candidates"][listed:])
            listed = len(stats["candidates"])
            qa = getattr(region, "_free_text_attempt_qa", None)
            if qa is not None:
                qa["typography_candidates"] = len(self.typography) if append_candidates else current
                qa["attempted_font_sizes"] = sorted(self.tested_font_sizes, reverse=True)

        provider.on_generate = record_generated

    @staticmethod
    def _coarse_font_sizes(preferred: int, minimum: int) -> List[int]:
        sizes = {max(minimum, preferred - offset) for offset in (0, 1, 2, 3)}
        sizes.update(range(preferred - 6, minimum, -4))
        sizes.add(minimum)
        return sorted(sizes, reverse=True)

    def stages_for_sizes(self, region, zone, sizes, *, only_emergency=False):
        key = (tuple(sizes), only_emergency)
        if key in self.stage_extensions:
            return self.stage_extensions[key]
        normal_by_size = None
        if only_emergency:
            normal_by_size = {}
            for candidate in self.typography:
                normal_by_size.setdefault(candidate.font_size, []).append(candidate)
        generated = _free_text_typography_candidates(
            self._text, self.profile, self._config, self._image_shape,
            target=self.target, panel_constraint=zone.panel_constraint,
            language=self._language, preserve=self._preserve, lazy_stages=True,
            font_sizes=list(sizes), include_emergency=False, only_emergency=only_emergency,
            normal_candidates_by_size=normal_by_size,
            minimum_font_size=self.minimum_font_size, budget=self.search_budget,
        )
        provider = _source_height_candidates(
            generated, self.profile, self.target, zone.panel_constraint, self.search_budget,
        )
        if isinstance(provider, _SourceHeightStageProvider):
            self._track_stages(provider, provider.stats, region, append_candidates=True)
        else:
            provider = _group_typography_stages(provider)
        self.stage_extensions[key] = provider
        return provider

    def search_with_fallback(self, region, zone, config, image_shape, profile, search, force_exhaustive):
        results = search(self.stage_groups, force_exhaustive)
        if not self.coarse_font_search:
            return results
        if self.search_budget is not None and (
            self.search_budget.initial or self.search_budget.expired()
        ):
            return results
        from .readable_text import readable_font_minimum
        self.readable_minimum = readable_font_minimum(config.render, image_shape)
        minimum = self.readable_minimum
        if self.search_budget is not None and self.search_budget.phase == "rescue":
            from .search_budget import small_text_rescue_minimum
            self.rescue_minimum = small_text_rescue_minimum(region, config.render, image_shape)
            if self.rescue_minimum is not None:
                minimum = min(minimum, self.rescue_minimum)
        self.minimum_font_size = minimum
        preferred = max(minimum, int(config.render.font_size or round(profile.font_size)))
        full_sizes = list(range(preferred, minimum - 1, -1))
        tested = set(self.tested_font_sizes)
        if force_exhaustive:
            missing_sizes = [size for size in full_sizes if size not in tested]
            if missing_sizes and not (self.search_budget is not None and self.search_budget.expired()):
                results.extend(search(self.stages_for_sizes(region, zone, missing_sizes)))
            if not results and not (self.search_budget is not None and self.search_budget.expired()):
                results = search(self.stages_for_sizes(region, zone, full_sizes, only_emergency=True))
        elif results:
            viable_size = results[0].typography_candidate.font_size
            higher = [size for size in tested if size > viable_size]
            upper_bound = min(higher) if higher else preferred + 1
            refine_sizes = [size for size in range(upper_bound - 1, viable_size, -1) if size not in tested]
            if refine_sizes:
                refined = search(self.stages_for_sizes(region, zone, refine_sizes))
                if refined:
                    results = refined
        else:
            missing_sizes = [size for size in full_sizes if size not in tested]
            if missing_sizes and not (self.search_budget is not None and self.search_budget.expired()):
                results = search(self.stages_for_sizes(region, zone, missing_sizes))
            if not results and not (self.search_budget is not None and self.search_budget.expired()):
                emergency = self.stages_for_sizes(region, zone, full_sizes, only_emergency=True)
                results = search(emergency)
        region._free_text_attempt_qa["typography_candidates"] = len(self.typography)
        region._free_text_attempt_qa["attempted_font_sizes"] = sorted(self.tested_font_sizes, reverse=True)
        return results

    def prepare(self, region, zone, obstacles, config: Config, image_shape, text, source_mask, *, full_font_search=False):
        if self.initialized:
            if self.profile is not None:
                get_solver_profile().free_text_regions += 1
            return self.profile

        self.profile = build_original_layout_profile(region, zone.ownership_mask)
        self.initialized = True
        if self.profile is None:
            return None
        profile = self.profile
        self._config, self._zone = config, zone
        self._image_shape, self._text = image_shape, text
        self._language = getattr(region, "target_lang", "en_US")
        self._preserve = is_preserved_region(region)
        from .search_budget import get_search_budget, small_text_rescue_minimum
        self.search_budget = get_search_budget()
        prof = get_solver_profile()
        prof.free_text_regions += 1
        if config.render.font_size is not None and config.render.font_size > 0:
            region.calibrated_font_size = int(config.render.font_size)
            region._font_policy_diagnostics = {"explicit_font_size": True}
        self.target = zone.damage_target
        target = self.target
        t_topo0 = perf_counter()
        from .readable_text import readable_font_minimum
        self.readable_minimum = readable_font_minimum(config.render, image_shape)
        minimum = self.readable_minimum
        if self.search_budget is not None and self.search_budget.phase == "rescue":
            self.rescue_minimum = small_text_rescue_minimum(region, config.render, image_shape)
            if self.rescue_minimum is not None:
                minimum = min(minimum, self.rescue_minimum)
        self.minimum_font_size = minimum
        preferred = max(minimum, int(config.render.font_size or round(profile.font_size)))
        font_sizes = list(range(preferred, minimum - 1, -1)) if full_font_search else self._coarse_font_sizes(preferred, minimum)
        generated = _free_text_typography_candidates(
            text, profile, config, image_shape, target=target,
            panel_constraint=zone.panel_constraint,
            language=self._language, preserve=self._preserve, lazy_stages=True,
            font_sizes=font_sizes, include_emergency=full_font_search,
            minimum_font_size=minimum, budget=self.search_budget,
        )
        adjusted = _source_height_candidates(
            generated, profile, target, zone.panel_constraint, self.search_budget,
        )
        if isinstance(adjusted, _SourceHeightStageProvider):
            self.stage_groups = adjusted
            self.coarse_font_search = not full_font_search
            stats = adjusted.stats
            self._track_stages(adjusted, stats, region, append_candidates=False)
            first_stage = adjusted.first_stage()
            self.typography = stats["candidates"] if first_stage else []
        else:
            prof.ft_typography_ms += (perf_counter() - t_topo0) * 1000.0
            self.typography = adjusted
            prof.free_text_typography_candidates += len(self.typography)
            self.stage_groups = _group_typography_stages(self.typography)
        if not self.typography:
            return profile
        self.damage_centroid = profile.centroid if target is None or target.area == 0 else (target.centroid_x, target.centroid_y)
        self.target_width = target.width if target is not None else profile.block_width
        self.target_height = target.height if target is not None else profile.block_height
        stroke_font_size = (
            max(candidate.font_size for candidate in first_stage)
            if isinstance(adjusted, _SourceHeightStageProvider)
            else max(candidate.font_size for candidate in self.typography)
        )
        self.stroke_width = stroke.get_text_stroke_width(
            stroke_font_size,
            fg_bg_compare(*region.get_font_colors())[1], region.bg_colors,
        )
        self.source = source_mask.astype(bool)
        self.other_text = obstacles.text_mask.astype(bool) & ~self.source
        return profile
