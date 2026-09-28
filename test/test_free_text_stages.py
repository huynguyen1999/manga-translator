from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from manga_translator.rendering.layout import free_text_stages, free_text_typography, free_text_solver
from manga_translator.rendering.layout.profiling import reset_solver_profile
from manga_translator.rendering.layout.models import (
    BandSlot,
    CandidateRaster,
    FreeTextDamageTarget,
    FreeTextZone,
    LayoutCandidate,
    OriginalLayoutProfile,
    PageObstacleMap,
    PanelConstraint,
    PlacedLine,
    SearchResult,
)


def _profile(font_size=20):
    return OriginalLayoutProfile(
        font_size=font_size, line_count=1, lines=[], centroid=(50, 50),
        bbox=(40, 40, 60, 60), block_width=100, block_height=80, occupancy=1.0,
    )


def _config(font_size=None, minimum=8, no_hyphenation=True):
    return SimpleNamespace(render=SimpleNamespace(
        font_size=font_size, font_size_minimum=minimum, line_spacing=0,
        no_hyphenation=no_hyphenation,
    ))


def _cheap_typography(monkeypatch):
    monkeypatch.setattr(free_text_stages, "_free_text_line_spacing", lambda value: float(value or 0))
    monkeypatch.setattr(free_text_stages, "_free_text_line_height", lambda size, _spacing: size)
    monkeypatch.setattr(free_text_stages, "_free_text_typography_score", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(free_text_stages, "_free_text_candidate_ink_metrics", lambda candidate: {
        "ink_bbox": (0, 0, 10, 10), "ink_width": 10, "ink_height": 10, "ink_area": 100,
        "ink_line_height": 10, "font_metric_height": candidate.font_size,
        "baseline_advance": candidate.font_size, "baseline_advance_min": candidate.font_size,
        "baseline_advance_max": candidate.font_size, "visible_gap": 0,
    })


def _free_text_solver_case(monkeypatch, *, reject_source=False, reject_ideal=False, ideal_rejection=None):
    source = LayoutCandidate(20, 0, 0, [PlacedLine("TEXT", 45, 45, 10, 20, BandSlot(45, 55, 45, 65))], 50, 0,
                             qa={"selected_tier": "source", "font_ratio": 1.0, "introduced_hyphen_count": 0})
    shrink = LayoutCandidate(18, 0, 0, [PlacedLine("TEXT", 45, 45, 10, 18, BandSlot(45, 55, 45, 63))], -50, 0,
                             qa={"selected_tier": "shrink_90", "font_ratio": 0.9, "introduced_hyphen_count": 0})
    attempted = []
    validated = []
    raster = CandidateRaster((40, 40, 42, 42), np.ones((2, 2), bool), np.ones((2, 2), bool),
                             np.ones((2, 2), bool), (40, 40, 42, 42), (40.5, 40.5), 4)
    shape = (100, 100)
    empty = np.zeros(shape, np.uint8)
    zone = FreeTextZone(
        source_bbox=(40, 40, 60, 60), ownership_mask=np.ones(shape, np.uint8), obstacle_mask=empty,
        coverage_target_mask=empty, coverable_damage_mask=empty,
        coverage_weight_map=np.zeros(shape, np.float32), core_damage_mask=empty,
    )
    obstacles = PageObstacleMap(empty, empty, empty, np.ones(shape, np.uint8))
    region = SimpleNamespace(
        _free_text_source_mask=np.ones(shape, np.uint8), bg_colors=None,
        get_font_colors=lambda: ((0, 0, 0), (255, 255, 255)), target_lang="en_US",
    )

    monkeypatch.setattr(free_text_solver, "_render_text", lambda _region: "TEXT")
    monkeypatch.setattr(free_text_solver, "build_original_layout_profile", lambda *_: _profile())
    monkeypatch.setattr(free_text_solver, "_free_text_typography_candidates", lambda *_a, **_k: [source, shrink])
    monkeypatch.setattr(free_text_solver, "_source_height_candidates", lambda candidates, *_: candidates)
    monkeypatch.setattr(free_text_solver.stroke, "get_text_stroke_width", lambda *_: 0)
    monkeypatch.setattr(free_text_solver, "fg_bg_compare", lambda fg, bg: (fg, bg))
    monkeypatch.setattr(free_text_solver, "rasterize_candidate", lambda *_: raster)
    monkeypatch.setattr(free_text_solver, "_candidate_raster_at_offset", lambda *_: (
        (40, 40, 42, 42), raster.ink_crop, raster.visual_crop, raster.block_crop,
    ))
    monkeypatch.setattr(free_text_solver, "_free_text_offset_search", lambda *_: [(0, 0)])
    monkeypatch.setattr(free_text_solver, "_free_text_offset_refine", lambda *_a, **_k: [])
    monkeypatch.setattr(free_text_solver, "_layout_env_enabled", lambda *_: False)
    monkeypatch.setattr(free_text_solver, "_layout_env_disabled", lambda *_: False)
    monkeypatch.setattr(free_text_solver, "_free_text_ink_overflow_from_raster", lambda *_a, **_k: 0.0)

    def result_for(candidate, candidate_raster, *_args):
        attempted.append(candidate.qa["selected_tier"])
        coverage = {key: 1.0 for key in ("c_ink", "c_visual", "c_block", "c_damage", "c_core", "cleanup_mask_coverage")}
        coverage["u_damage"] = 0.0
        result = SearchResult(candidate, candidate_raster, 0, 0, 0, 0, candidate.penalty,
                              coverage, (50, 50), 0, 0, (45, 45, 55, 55))
        if candidate is source and ideal_rejection == "centering":
            result = replace(result, center_dx=3.0)
        elif candidate is source and ideal_rejection == "core":
            coverage["c_core"] = 0.89
        return result

    monkeypatch.setattr(free_text_solver, "_free_text_search_result", result_for)
    from manga_translator.rendering.layout import candidate_footprint
    def filter_candidates(_region, candidates, _config, _shape, zone, *_):
        validated.extend(candidates)
        if reject_ideal and any(c.status == "free_text_ideal" for c in candidates):
            zone.rejections["shadow_probe"] = zone.rejections.get("shadow_probe", 0) + 1
            return []
        accepted = [c for c in candidates if not reject_source or c.font_size < 20]
        for candidate in accepted:
            candidate.qa["render_footprint_validated"] = True
        return accepted
    monkeypatch.setattr(candidate_footprint, "filter_renderable_candidates", filter_candidates)
    reset_solver_profile()
    return region, zone, obstacles, attempted, validated


@pytest.mark.parametrize("reject_source", [False, True])
@pytest.mark.parametrize("early_accept", [False, True])
def test_source_font_search_checks_actual_raster_before_accepting_stage(monkeypatch, reject_source, early_accept):
    region, zone, obstacles, attempted, validated = _free_text_solver_case(
        monkeypatch, reject_source=reject_source,
    )
    selected, _, qa = free_text_solver._solve_free_text_region(
        region, zone, obstacles, _config(), (100, 100), 2, 8,
        allow_early_accept=early_accept,
    )

    assert selected.font_size == (18 if reject_source else 20)
    assert qa["selected_tier"] == ("shrink_90" if reject_source else "source")
    assert attempted and set(attempted) == ({"source", "shrink_90"} if reject_source else {"source"})
    if early_accept and not reject_source:
        assert selected.status == "free_text_ideal"
        assert region._free_text_candidate_pool == [selected]
        assert qa is selected.qa
        assert qa["render_footprint_validated"] is True
        assert len(validated) == 1 and validated[0] is selected
        assert free_text_solver.get_solver_profile().free_text_full_search_runs == 0
        assert free_text_solver.get_solver_profile().free_text_ideal_successes == 1
    elif early_accept:
        assert validated[0].status == "free_text_ideal"
        assert free_text_solver.get_solver_profile().free_text_full_search_runs == 1
        assert free_text_solver.get_solver_profile().free_text_ideal_successes == 0
        assert free_text_solver.get_solver_profile().free_text_full_search_fallbacks == 1


def test_local_lazy_candidate_never_early_returns(monkeypatch):
    region, zone, obstacles, _, _ = _free_text_solver_case(monkeypatch)
    monkeypatch.setattr(free_text_solver, "_layout_env_enabled", lambda _name: True)
    gate_calls = []
    def gate(result, *_args):
        gate_calls.append(result.relative_dx)
        return (len(gate_calls) > 1, 0.0)
    monkeypatch.setattr(free_text_solver, "_free_text_fast_gate", gate)

    selected, _, _ = free_text_solver._solve_free_text_region(
        region, zone, obstacles, _config(), (100, 100), 2, 8,
        allow_early_accept=True,
    )

    assert gate_calls
    assert selected.status == "free_text"
    assert free_text_solver.get_solver_profile().free_text_local_search_successes == 1
    assert free_text_solver.get_solver_profile().free_text_full_search_runs == 1
    assert free_text_solver.get_solver_profile().free_text_full_search_fallbacks == 1


def test_local_search_without_requested_ideal_fast_path_does_not_count_fallback(monkeypatch):
    region, zone, obstacles, _, _ = _free_text_solver_case(monkeypatch)
    monkeypatch.setattr(free_text_solver, "_layout_env_enabled", lambda name: name == "LAYOUT_LAZY_CANDIDATES")
    monkeypatch.setattr(free_text_solver, "_layout_env_disabled", lambda name: name == "LAYOUT_FAST_FREE_TEXT")
    monkeypatch.setattr(free_text_solver, "_free_text_fast_gate", lambda *_args: (True, 0.0))

    selected, _, _ = free_text_solver._solve_free_text_region(
        region, zone, obstacles, _config(), (100, 100), 2, 8,
        allow_early_accept=True,
    )

    profile = free_text_solver.get_solver_profile()
    assert selected.status == "free_text"
    assert profile.free_text_local_search_successes == 1
    assert profile.free_text_full_search_runs == 1
    assert profile.free_text_full_search_fallbacks == 0


@pytest.mark.parametrize("rejection", ["page", "center", "core", "overflow"])
def test_ideal_gate_rejects_each_safety_boundary(monkeypatch, rejection):
    from manga_translator.rendering.layout import candidate_footprint, free_text_search

    shape = (100, 100)
    empty = np.zeros(shape, np.uint8)
    candidate = LayoutCandidate(20, 0, 0, [PlacedLine("A", 45, 45, 10, 10)], 0, 0)
    raster = CandidateRaster((45, 45, 55, 55), np.ones((10, 10), bool),
                             np.ones((10, 10), bool), np.ones((10, 10), bool),
                             (45, 45, 55, 55), (50, 50), 100)
    result = SearchResult(candidate, raster, 0, 0, 0, 0, 0,
                          {"c_core": 1.0}, (50, 50), 0, 0, (45, 45, 55, 55))
    if rejection == "page":
        result = replace(result, dx=-50)
    elif rejection == "center":
        result = replace(result, center_dx=3.0)
    elif rejection == "core":
        result = replace(result, coverage={"c_core": 0.89})
    monkeypatch.setattr(free_text_search, "_free_text_ink_overflow_from_raster",
                        lambda *_args, **_kwargs: 0.1 if rejection == "overflow" else 0.0)
    obstacles = PageObstacleMap(empty, empty, empty, np.ones(shape, np.uint8))

    accepted, _ = candidate_footprint.free_text_fast_gate(result, shape, obstacles, empty)
    assert not accepted


def test_shadow_probe_isolated_from_exhaustive_qa_and_profiling(monkeypatch):
    clean_case = _free_text_solver_case(monkeypatch)
    clean_region, clean_zone, clean_obstacles, _, _ = clean_case
    clean, _, clean_qa = free_text_solver._solve_free_text_region(
        clean_region, clean_zone, clean_obstacles, _config(), (100, 100), 2, 8,
    )
    clean_qa = deepcopy(clean_qa)
    clean_pool = [deepcopy(candidate.qa) for candidate in clean_region._free_text_candidate_pool]
    clean_workload = free_text_solver.get_solver_profile().to_dict()["workload"]

    shadow_case = _free_text_solver_case(monkeypatch, reject_ideal=True)
    region, zone, obstacles, _, _ = shadow_case
    from manga_translator.rendering.layout import candidate_footprint
    monkeypatch.setattr(candidate_footprint, "_candidate_global_glyph_mask", lambda _candidate, shape: np.zeros(shape, bool))
    selected, _, qa = free_text_solver._solve_free_text_region(
        region, zone, obstacles, _config(), (100, 100), 2, 8,
        allow_early_accept=True, shadow_compare=True,
    )

    assert selected.font_size == clean.font_size
    assert qa == clean_qa
    assert [candidate.qa for candidate in region._free_text_candidate_pool] == clean_pool
    assert free_text_solver.get_solver_profile().to_dict()["workload"] == clean_workload
    assert region._free_text_shadow_metrics["workload"]["free_text_ideal_attempts"] == 1
    assert region._free_text_shadow_metrics["comparison"]["fast_accepted"] is False
    assert "shadow_probe" not in zone.rejections
    assert region._free_text_shadow_metrics["workload"]["free_text_ideal_successes"] == 0
    assert region._free_text_shadow_metrics["workload"]["free_text_full_search_fallbacks"] == 1


def test_successful_shadow_probe_preserves_exhaustive_result_and_main_workload(monkeypatch):
    clean_region, clean_zone, clean_obstacles, _, _ = _free_text_solver_case(monkeypatch)
    clean, _, clean_qa = free_text_solver._solve_free_text_region(
        clean_region, clean_zone, clean_obstacles, _config(), (100, 100), 2, 8,
    )
    clean_pool = deepcopy(clean_region._free_text_candidate_pool)
    clean_workload = deepcopy(free_text_solver.get_solver_profile().to_dict()["workload"])
    clean_rejections = deepcopy(clean_zone.rejections)

    region, zone, obstacles, _, _ = _free_text_solver_case(monkeypatch)
    from manga_translator.rendering.layout import candidate_footprint
    monkeypatch.setattr(
        candidate_footprint, "_candidate_global_glyph_mask",
        lambda _candidate, shape: np.ones(shape, bool),
    )
    selected, _, qa = free_text_solver._solve_free_text_region(
        region, zone, obstacles, _config(), (100, 100), 2, 8,
        allow_early_accept=True, shadow_compare=True,
    )

    assert selected is region._free_text_candidate_pool[0]
    assert selected == clean
    assert qa == clean_qa
    assert region._free_text_candidate_pool == clean_pool
    assert free_text_solver.get_solver_profile().to_dict()["workload"] == clean_workload
    assert zone.rejections == clean_rejections
    assert region._free_text_shadow_metrics["workload"]["free_text_ideal_successes"] == 1
    assert region._free_text_shadow_metrics["workload"]["free_text_full_search_fallbacks"] == 0
    assert region._free_text_shadow_metrics["comparison"]["fast_accepted"] is True


def test_shadow_comparison_uses_validated_render_footprints():
    from manga_translator.rendering.layout.candidate_footprint import log_free_text_shadow_comparison

    fast = LayoutCandidate(20, 0, 0, [PlacedLine("A", 0, 0, 1, 1)], 0, 0)
    exhaustive = LayoutCandidate(20, 0, 0, [PlacedLine("A", 0, 0, 1, 1)], 0, 0)
    fast._render_footprint = ((1, 1, 3, 3), np.ones((2, 2), bool))
    exhaustive._render_footprint = ((2, 1, 4, 3), np.ones((2, 2), bool))

    comparison = log_free_text_shadow_comparison(fast, exhaustive, (5, 5))

    assert comparison["rendered_mask_iou"] == pytest.approx(1 / 3)
    assert comparison["footprint_area_delta"] == 0


@pytest.mark.parametrize("rejection", ["centering", "core", "overflow", "compositor"])
def test_rejected_ideal_fast_path_runs_exhaustive_search_once(monkeypatch, rejection):
    from manga_translator.rendering.layout import free_text_search

    region, zone, obstacles, attempted, _ = _free_text_solver_case(
        monkeypatch,
        reject_ideal=rejection == "compositor",
        ideal_rejection=rejection,
    )
    if rejection == "overflow":
        monkeypatch.setattr(
            free_text_search, "_free_text_ink_overflow_from_raster",
            lambda *_args, **_kwargs: 0.1,
        )

    selected, _, _ = free_text_solver._solve_free_text_region(
        region, zone, obstacles, _config(), (100, 100), 2, 8,
        allow_early_accept=True,
    )

    profile = free_text_solver.get_solver_profile()
    assert selected is not None
    assert attempted
    assert profile.free_text_full_search_fallbacks == 1
    assert profile.free_text_full_search_runs == 1


def test_auto_sizes_search_down_to_configured_floor(monkeypatch):
    _cheap_typography(monkeypatch)
    monkeypatch.setattr(free_text_stages, "_precompute_widths", lambda words, _size: ([20] * len(words), 4))

    candidates = free_text_stages._free_text_typography_candidates(
        "ONE TWO", _profile(), _config(), (100, 100),
    )

    tiers = {candidate.qa["selected_tier"] for candidate in candidates}
    assert candidates
    assert max(candidate.font_size for candidate in candidates) == 20
    assert min(candidate.font_size for candidate in candidates) == 8
    assert {"source", "shrink_19", "shrink_8"} <= tiers


def test_candidate_ink_metrics_are_measured_once_and_reused(monkeypatch):
    metrics_by_candidate = {}
    score_inputs = []
    metrics = {
        "ink_bbox": (0, 0, 30, 20), "ink_width": 30, "ink_height": 20,
        "ink_area": 300, "ink_line_height": 18, "font_metric_height": 20,
        "baseline_advance": 20, "baseline_advance_min": 20,
        "baseline_advance_max": 20, "visible_gap": 0,
    }

    def measure(candidate):
        metrics_by_candidate[id(candidate)] = metrics_by_candidate.get(id(candidate), 0) + 1
        return metrics

    def score(*_args, ink_metrics=None, **_kwargs):
        score_inputs.append(ink_metrics)
        return 0.0

    monkeypatch.setattr(free_text_stages, "_free_text_candidate_ink_metrics", measure)
    monkeypatch.setattr(free_text_stages, "_free_text_typography_score", score)
    monkeypatch.setattr(free_text_stages, "_precompute_widths", lambda words, _size: ([20] * len(words), 4))

    candidates = free_text_stages._free_text_typography_candidates(
        "ONE TWO", _profile(), _config(font_size=18, minimum=18), (100, 100),
    )

    assert candidates
    assert len(metrics_by_candidate) == len(candidates)
    assert all(count == 1 for count in metrics_by_candidate.values())
    assert len(score_inputs) == len(candidates)
    assert all(item is metrics for item in score_inputs)


def test_typography_score_reuses_supplied_ink_metrics(monkeypatch):
    metrics = {
        "ink_bbox": (0, 0, 30, 20), "ink_width": 30, "ink_height": 20,
        "ink_area": 300, "ink_line_height": 18, "font_metric_height": 20,
        "baseline_advance": 20, "baseline_advance_min": 20,
        "baseline_advance_max": 20, "visible_gap": 0,
    }
    calls = 0

    def measure(_candidate):
        nonlocal calls
        calls += 1
        return metrics

    candidate = LayoutCandidate(
        20, 0, 0, [PlacedLine("ONE TWO", 0, 0, 60, 20)], 0, 0,
    )
    monkeypatch.setattr(free_text_typography, "_free_text_candidate_ink_metrics", measure)

    measured_score = free_text_typography._free_text_typography_score(candidate, _profile())
    reused_score = free_text_typography._free_text_typography_score(
        candidate, _profile(), ink_metrics=metrics,
    )

    assert measured_score == reused_score
    assert calls == 1


def test_explicit_font_respects_higher_configured_minimum(monkeypatch):
    _cheap_typography(monkeypatch)
    monkeypatch.setattr(free_text_stages, "_precompute_widths", lambda words, _size: ([20] * len(words), 4))

    candidates = free_text_stages._free_text_typography_candidates(
        "ONE TWO", _profile(), _config(font_size=12, minimum=18), (100, 100),
    )

    assert candidates
    assert {candidate.font_size for candidate in candidates} == {18}
    assert {candidate.qa["selected_tier"] for candidate in candidates} == {"source"}
    assert all(candidate.qa["font_ratio"] == 0.9 for candidate in candidates)


def test_saved_narrow_region_prefers_source_width_dictionary_split():
    from manga_translator.rendering import get_default_eng_font, text_render
    text_render.set_font(get_default_eng_font())
    profile = OriginalLayoutProfile(
        font_size=50, line_count=3, lines=[], centroid=(1147, 250.5),
        bbox=(1054, 52, 1240, 449), block_width=186, block_height=397, occupancy=1.0,
    )
    target = FreeTextDamageTarget(
        mask=np.ones((1, 1), np.uint8), centroid_x=1133.5, centroid_y=205.1,
        bbox=(1036, 44, 1267, 468), area=57873, width=231, height=424,
        source_centroid=(1147, 250.5),
    )
    panel = PanelConstraint(
        panel_id="cv", bounds=(109, 0, 1280, 607), confidence=0.86,
        source="cv", margin=6,
    )

    candidates = free_text_stages._free_text_typography_candidates(
        "To hold back from masturbating until today...", profile,
        _config(minimum=11, no_hyphenation=False), (1808, 1280),
        target=target, panel_constraint=panel, language="ENG",
    )
    split = next(
        candidate for candidate in candidates
        if candidate.font_size == 33
        and candidate.qa["introduced_hyphen_words"] == ["masturbating"]
        and not candidate.qa["emergency_word_split"]
    )
    assert [line.text for line in split.lines][3:5] == ["mastur-", "bating"]
    assert max(line.width for line in split.lines) <= profile.block_width
    assert all(candidate.qa["introduced_hyphen_count"] for candidate in candidates if candidate.font_size == 33)


def test_severely_overwide_word_splits_even_when_shape_score_dislikes_extra_lines():
    from manga_translator.rendering import get_default_eng_font, text_render
    text_render.set_font(get_default_eng_font())
    profile = OriginalLayoutProfile(
        font_size=33, line_count=3, lines=[], centroid=(556.5, 683.5),
        bbox=(497, 539, 616, 828), block_width=119, block_height=289, occupancy=1.0,
    )
    target = FreeTextDamageTarget(
        mask=np.ones((1, 1), np.uint8), centroid_x=545, centroid_y=675,
        bbox=(481, 532, 625, 859), area=36752, width=144, height=327,
        source_centroid=profile.centroid,
    )
    panel = PanelConstraint(
        panel_id="cv", bounds=(0, 0, 624, 1237), confidence=0.97,
        source="cv", margin=7,
    )

    candidates = free_text_stages._free_text_typography_candidates(
        "THEY'RE GONNA USE ME TO SHOW OFF THEIR SUPERIORITY.", profile,
        _config(minimum=11, no_hyphenation=False), (1237, 624),
        target=target, panel_constraint=panel, language="ENG",
    )
    same_size = [candidate for candidate in candidates if candidate.font_size == 22]

    assert same_size
    assert all(candidate.qa["wrapping_splits"] for candidate in same_size)
    assert all("SUPERIORITY." not in line.text for candidate in same_size for line in candidate.lines)
