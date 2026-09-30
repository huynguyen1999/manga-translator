from types import SimpleNamespace

import numpy as np
import pytest

from manga_translator.rendering import text_render
from manga_translator.rendering.layout.geometry import BubbleGeometry
from manga_translator.rendering.layout.profiling import reset_solver_profile
from manga_translator.rendering.layout.search_budget import (
    PageSearchBudget, SearchDeadlineReached, get_search_budget,
    page_search_budget, small_text_rescue_minimum,
)
from manga_translator.rendering.layout.solve_core import solve_layout


def test_unique_font_trials_and_direct_default():
    text_render.set_font("fonts/anime_ace.ttf")
    profile = reset_solver_profile()
    result = solve_layout(BubbleGeometry(np.ones((90, 220), np.uint8)),
                          ["A", "SIMPLE", "TEST"], 25, 8, margin=0)
    assert result is not None
    assert len(profile.attempted_font_sizes) == len(set(profile.attempted_font_sizes))
    assert get_search_budget() is None


def test_initial_y_search_stays_within_three_trial_bound():
    text_render.set_font("fonts/anime_ace.ttf")
    profile = reset_solver_profile()
    with page_search_budget():
        result = solve_layout(BubbleGeometry(np.ones((90, 220), np.uint8)),
                              ["A", "SIMPLE", "TEST"], 25, 8, margin=0, top_k=2)
    assert result and all(candidate.valid for candidate in result)
    assert profile.y_origins_tested <= profile.spacing_tested * 2


def test_deadline_and_context_reset():
    with page_search_budget() as budget:
        budget.deadline_seconds = 0
        with pytest.raises(SearchDeadlineReached):
            budget.check_dp()
        assert solve_layout(BubbleGeometry(np.ones((90, 220), np.uint8)), ["TEST"], 20, 8) is None
        assert budget.exhausted
    assert get_search_budget() is None


def test_soft_targets_keep_unresolved_and_conflicting_work(monkeypatch):
    from manga_translator.rendering.layout import search_budget
    clock = [0.0]
    monkeypatch.setattr(search_budget, "perf_counter", lambda: clock[0])
    budget = PageSearchBudget(started=0)
    clock[0] = 5
    assert not budget.may_continue(validated=True)
    assert budget.may_continue()
    with budget.searching("expanded"):
        assert budget.may_continue(validated=True)
        clock[0] = 10
        assert not budget.may_continue(validated=True)
        assert budget.may_continue(validated=True, conflicting=True)
        assert budget.may_continue()
    clock[0] = 30
    assert not budget.may_continue(conflicting=True)


def test_queue_wait_is_consumed_once_when_page_context_is_reused(monkeypatch):
    from manga_translator.config import Config
    from manga_translator.rendering.layout import engine
    ctx = SimpleNamespace(img_rgb=np.full((60, 100, 3), 255, np.uint8), text_regions=[],
                          _layout_queue_wait_ms=125.0, _collect_layout_profile=True)
    def no_search(*args):
        ctx._solver_profile = {}
    monkeypatch.setattr(engine, "_run_search", no_search)
    monkeypatch.setattr(engine, "validate_layout", lambda *args: None)
    assert engine.layout_page(ctx, Config()).timings["queue_wait_ms"] == 125.0
    assert ctx._solver_profile["queue_wait_ms"] == 125.0
    assert engine.layout_page(ctx, Config()).timings["queue_wait_ms"] == 0.0
    assert ctx._solver_profile["queue_wait_ms"] == 0.0


def test_plan_expansion_merges_by_region_identity():
    from manga_translator.rendering.layout.page_search import merge_bubble_plans
    first, second = object(), object()
    old_candidate, new_candidate = object(), object()
    old = SimpleNamespace(region=second, candidates=[old_candidate])
    expanded_first = SimpleNamespace(region=first, candidates=[new_candidate])
    expanded_second = SimpleNamespace(region=second, candidates=[])
    merged = merge_bubble_plans([old], [expanded_first, expanded_second])
    assert merged[0].candidates == [new_candidate]
    assert merged[1].candidates == [old_candidate]


def test_deadline_retains_completed_valid_candidates(monkeypatch):
    text_render.set_font("fonts/anime_ace.ttf")
    profile = reset_solver_profile()
    with page_search_budget() as budget:
        monkeypatch.setattr(budget, "expired", lambda: profile.fonts_tested >= 2)
        result = solve_layout(BubbleGeometry(np.ones((90, 220), np.uint8)), ["TEST"], 20, 8, margin=0)
        assert result is not None and result.valid
        assert profile.fonts_tested == 2


def test_rescue_floor_respects_sizing_policy():
    config = SimpleNamespace(font_size=None, font_size_minimum=0)
    region = SimpleNamespace(translation_policy=None)
    assert small_text_rescue_minimum(region, config, (2048, 1000)) == 8
    assert small_text_rescue_minimum(region, config, (4096, 1000)) == 16
    config.font_size_minimum = 19
    assert small_text_rescue_minimum(region, config, (2048, 1000)) == 19
    config.font_size = 20
    assert small_text_rescue_minimum(region, config, (2048, 1000)) is None


    config.font_size = None
    region.translation_policy = "preserve"
    assert small_text_rescue_minimum(region, config, (2048, 1000)) is None


def test_rescued_eight_pixel_lettering_is_reviewed_after_freezing(monkeypatch):
    from manga_translator.rendering.layout import validation
    from manga_translator.rendering.layout.models import PageLayoutResult, RegionLayout, PlacedLine

    region = SimpleNamespace(region_id="rescue", translation="TEST", text="source",
        source_region_ids=["rescue"], translation_policy=None, source_font_size=20,
        font_size=8, target_lang="ENG", review_required=False, review_reason=None,
        layout_segments=[{"font_size": 8}], _solver_qa={"small_text_rescue": True, "rescue_minimum": 8})
    result = PageLayoutResult(regions={"rescue": RegionLayout(region_id="rescue",
        lines=[PlacedLine(text="TEST", x=10, y=10, width=30, height=8)])})
    ctx = SimpleNamespace(img_rgb=np.zeros((100, 2048, 3), np.uint8), text_regions=[region])
    monkeypatch.setattr(validation, "validate_render_output", lambda *args: None)
    validation.validate_layout(ctx, result)
    assert not getattr(region, "_render_suppressed", False)
    assert region.review_required and region.review_reason == "small_text_rescue"
    region.font_size = 7
    region.layout_segments[0]["font_size"] = 7
    validation.validate_layout(ctx, PageLayoutResult(regions=result.regions))
    assert region._render_suppressed


def test_page_deadline_keeps_valid_lettering_and_restores_unresolved_source(monkeypatch):
    import asyncio
    from manga_translator.config import Config, RenderConfig
    from manga_translator.rendering import render_page
    from manga_translator.rendering.layout import layout_page, solver
    from manga_translator.rendering.layout.models import LayoutCandidate, PlacedLine
    from manga_translator.utils import Context, TextBlock

    image = np.full((120, 260, 3), 255, np.uint8)
    image[60:80, 180:220] = 31
    regions = [TextBlock(lines=np.array([[[x, y], [x + 40, y], [x + 40, y + 20], [x, y + 20]]]),
                        texts=["source"], translation=text, font_size=14, target_lang="ENG")
               for x, y, text in [(20, 20, "VALID"), (180, 60, "UNRESOLVED")]]
    for index, region in enumerate(regions):
        region.region_id = str(index)

    def solve(region, **kwargs):
        if region is regions[1]:
            return None
        candidate = LayoutCandidate(font_size=14, y_origin=20, line_spacing=0,
            lines=[PlacedLine(text="VALID", x=20, y=20, width=42, height=14)], penalty=0, glyph_clearance_p5=0, valid=True)
        region._free_text_candidate_pool = [candidate]
        get_search_budget().deadline_seconds = 0
        return candidate, SimpleNamespace(), candidate.qa

    monkeypatch.setattr(solver, "_solve_free_text_region", solve)
    ctx = Context(img_rgb=image, img_inpainted=np.full_like(image, 255), text_regions=regions,
                  mask=np.zeros(image.shape[:2], np.uint8))
    config = Config(render=RenderConfig(font_size_minimum=0))
    result = layout_page(ctx, config, "fonts/anime_ace.ttf")
    assert result.diagnostics.metrics["search"]["budget_exhausted"]
    assert regions[1]._render_suppressed and regions[1].review_required
    assert not regions[0]._render_suppressed
    rendered = asyncio.run(render_page(ctx, config, "fonts/anime_ace.ttf"))
    assert np.array_equal(rendered[60:80, 180:220], image[60:80, 180:220])
    assert np.any(rendered[20:40, 20:65] < 255)
