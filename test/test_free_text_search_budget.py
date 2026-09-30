from types import SimpleNamespace

import numpy as np

from manga_translator.rendering.layout import free_text_constraints, free_text_solver, free_text_stages
from manga_translator.rendering.layout.profiling import reset_solver_profile
from manga_translator.rendering.layout.search_budget import page_search_budget


def _candidate(size):
    return SimpleNamespace(font_size=size, qa={})


def test_initial_budget_reaches_smaller_local_stage_and_bounds_offsets(monkeypatch):
    large, small = _candidate(20), _candidate(12)
    offsets = ((0, 0), (4, 0), (40, 0))
    monkeypatch.setattr(free_text_stages, "_FREE_TEXT_LOCAL_OFFSETS", offsets)
    budget = SimpleNamespace(initial=True, expired=lambda: False, may_continue=lambda **kwargs: True)
    visual = np.ones((4, 4), dtype=bool)
    zone = SimpleNamespace(domain_center_only=False, rejections={})
    obstacles = SimpleNamespace(panel_mask=np.ones((40, 40), dtype=np.uint8))
    attempted = []

    def search_offset(candidate, *args):
        dx, dy = args[9:11]
        attempted.append((candidate.font_size, dx, dy))
        if candidate is small:
            return SimpleNamespace(
                typography_candidate=candidate, score=1, dx=dx, dy=dy,
                relative_dx=dx, relative_dy=dy,
            )
        return None

    reset_solver_profile()
    results = free_text_stages._search_first_viable_stage(
        [[large], [small]],
        lambda _candidate: (None, (10, 10, 14, 14), None, visual, None,
                            (12, 12), (10, 10, 14, 14), 0, 0, 0),
        {}, SimpleNamespace(), (0, 0), 1, 1, zone, obstacles, None,
        lambda _radius: [], lambda *_args, **_kwargs: [], search_offset,
        validate_results=lambda results: results,
        domain_bbox=(14, 10, 30, 14), budget=budget,
    )

    assert [(result.typography_candidate.font_size, result.relative_dx) for result in results] == [(12, 4)]
    assert attempted == [(20, 4, 0), (12, 4, 0)]
    assert zone.rejections == {"local_domain": 2, "page_bounds": 2}


def test_expired_budget_keeps_already_validated_candidates():
    first, later = _candidate(20), _candidate(12)
    checks = 0
    validated = []

    def expired():
        nonlocal checks
        checks += 1
        return checks >= 5

    budget = SimpleNamespace(initial=False, expired=expired, may_continue=lambda **kwargs: not expired())
    zone = SimpleNamespace(domain_center_only=False, rejections={})
    obstacles = SimpleNamespace(panel_mask=np.ones((40, 40), dtype=np.uint8))
    visual = np.ones((4, 4), dtype=bool)
    attempted = []

    def search_offset(candidate, *args):
        attempted.append(candidate.font_size)
        return SimpleNamespace(typography_candidate=candidate, score=1)

    def validate(results):
        validated.extend(results)
        return results

    results = free_text_stages._search_first_viable_stage(
        [[first], [later]],
        lambda _candidate: (None, (10, 10, 14, 14), None, visual, None,
                            (12, 12), (10, 10, 14, 14), 0, 0, 0),
        {}, SimpleNamespace(), (0, 0), 1, 1, zone, obstacles, None,
        lambda _radius: [(0, 0)], lambda *_args, **_kwargs: [], search_offset,
        validate_results=validate, collect_stages=True, budget=budget,
    )

    assert attempted == [20]
    assert results == validated and results[0].typography_candidate is first


def test_initial_acceptance_retains_two_and_stops_smaller_stages(monkeypatch):
    first, smaller = _candidate(20), _candidate(12)
    monkeypatch.setattr(free_text_stages, "_FREE_TEXT_LOCAL_OFFSETS", [(0, 0), (1, 0), (2, 0)])
    attempted = []

    def search(candidate, *args):
        attempted.append(candidate.font_size)
        return SimpleNamespace(typography_candidate=candidate, score=1)

    results = free_text_stages._search_first_viable_stage(
        [[first], [smaller]],
        lambda candidate: (None, (0, 0, 4, 4), None, np.ones((4, 4), bool), None,
                            (2, 2), (0, 0, 4, 4), 0, 0, 0),
        {}, None, (0, 0), 1, 1, SimpleNamespace(), None, None,
        lambda radius: [], lambda *args, **kwargs: [], search,
        validate_results=lambda results: results,
        budget=SimpleNamespace(initial=True, expired=lambda: False, may_continue=lambda **kwargs: True),
    )
    assert len(results) == 2
    assert attempted == [20, 20, 20]


def test_preparation_reuse_is_scoped_to_the_page_budget(monkeypatch):
    monkeypatch.setattr(
        free_text_constraints, "_build_free_text_placement_domain",
        lambda *_args, **kwargs: (np.ones((8, 8), dtype=np.uint8), {"geodesic_limit_px": kwargs["distance"]}),
    )

    def solve_once(region, zone, _obstacles, _config, _shape, _margin, _trials, *, preparation, **_options):
        preparation._zone = zone
        from manga_translator.rendering.layout.search_budget import get_search_budget
        preparation.search_budget = get_search_budget()
        candidate = SimpleNamespace(qa={})
        region._free_text_candidate_pool = [candidate]
        return candidate, SimpleNamespace(), candidate.qa

    monkeypatch.setattr(free_text_solver, "_solve_free_text_region_once", solve_once)

    def case():
        region = SimpleNamespace(_free_text_source_mask=np.ones((8, 8), dtype=np.uint8))
        zone = SimpleNamespace(coverable_damage_mask=np.zeros((8, 8), dtype=np.uint8), panel_constraint=None)
        return region, zone, SimpleNamespace()

    region, zone, obstacles = case()
    with page_search_budget():
        free_text_solver._solve_free_text_region(region, zone, obstacles, None, (8, 8), 2, 8)
        prepared = region._free_text_solve_context
        free_text_solver._solve_free_text_region(region, zone, obstacles, None, (8, 8), 2, 8)
        assert region._free_text_solve_context is prepared

    region, zone, obstacles = case()
    free_text_solver._solve_free_text_region(region, zone, obstacles, None, (8, 8), 2, 8)
    prepared = region._free_text_solve_context
    free_text_solver._solve_free_text_region(region, zone, obstacles, None, (8, 8), 2, 8)
    assert region._free_text_solve_context is not prepared
