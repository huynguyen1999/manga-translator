from types import SimpleNamespace

import numpy as np
import pytest

from manga_translator.config import Config
from manga_translator.rendering import get_default_eng_font, text_render
from manga_translator.rendering.layout import free_text_context, free_text_stages
from manga_translator.rendering.layout.models import (
    BandSlot,
    FreeTextDamageTarget,
    LayoutCandidate,
    OriginalLayoutProfile,
    PanelConstraint,
    PlacedLine,
)


def _candidate_key(candidate):
    return (
        candidate.font_size,
        candidate.qa["selected_tier"],
        tuple((line.text, line.x, line.y, line.width, line.height) for line in candidate.lines),
        candidate.penalty,
    )


def test_145px_typography_yields_old_winner_before_generating_lower_tiers(monkeypatch):
    text_render.set_font(get_default_eng_font())
    profile = OriginalLayoutProfile(
        font_size=145, line_count=2, lines=[], centroid=(250.0, 180.0),
        bbox=(90, 100, 410, 260), block_width=320, block_height=160, occupancy=0.7,
    )
    target = FreeTextDamageTarget(
        mask=np.ones((1, 1), np.uint8), centroid_x=250.0, centroid_y=180.0,
        bbox=(70, 80, 430, 280), area=72000, width=360, height=200,
        source_centroid=profile.centroid,
    )
    config = Config()
    config.render.font_size = None
    config.render.font_size_minimum = 12
    config.render.no_hyphenation = True
    args = ("A LONGER TRANSLATED CAPTION THAT NEEDS TO FIT CLEANLY", profile, config, (600, 500))

    eager = free_text_stages._free_text_typography_candidates(*args, target=target)
    eager = free_text_context._source_height_candidates(eager, profile, target, None)
    assert len(eager) == 93
    old_winner = min(free_text_stages._group_typography_stages(eager)[0], key=lambda item: item.penalty)
    assert (old_winner.font_size, [line.text for line in old_winner.lines]) == (
        35, ["A LONGER", "TRANSLATED", "CAPTION THAT", "NEEDS TO", "FIT CLEANLY"],
    )

    raw_stages, stats = free_text_stages._free_text_typography_candidates(
        *args, target=target, lazy_stages=True,
    )
    lazy_stages = free_text_context._source_height_candidates((raw_stages, stats), profile, target, None)
    result = free_text_stages._search_first_viable_stage(
        lazy_stages, lambda _candidate: (None, None, None, None, None, (0, 0), (0, 0, 0, 0), 0, 0, 0),
        {}, profile, (0, 0), 0, 0, None, None, None,
        lambda _radius: [(0, 0)], lambda *_args, **_kwargs: [],
        lambda candidate, *_args: SimpleNamespace(typography_candidate=candidate, score=candidate.penalty),
        validate_results=lambda items: items,
    )

    assert result
    new_winner = min(result, key=lambda item: item.score).typography_candidate
    assert _candidate_key(new_winner) == _candidate_key(old_winner)
    assert stats["candidate_count"] == 2

    # Exhaustive callers and later retries can still request every remaining tier.
    lazy_candidates = [candidate for stage in lazy_stages for candidate in stage]
    assert [_candidate_key(item) for item in lazy_candidates] == [_candidate_key(item) for item in eager]
    assert stats["candidate_count"] == len(eager)

    replay = [candidate for stage in lazy_stages for candidate in stage]
    assert [_candidate_key(item) for item in replay] == [_candidate_key(item) for item in eager]

    region = SimpleNamespace(
        _free_text_source_mask=np.ones((1, 1), np.uint8), _free_text_attempt_qa={},
        bg_colors=None, target_lang="en_US", get_font_colors=lambda: ((0, 0, 0), (255, 255, 255)),
    )
    zone = SimpleNamespace(ownership_mask=np.ones((1, 1), np.uint8), damage_target=target, panel_constraint=None)
    obstacles = SimpleNamespace(text_mask=np.zeros((1, 1), np.uint8))
    monkeypatch.setattr(free_text_context, "build_original_layout_profile", lambda *_args: profile)
    monkeypatch.setattr(free_text_context, "is_preserved_region", lambda _region: False)
    monkeypatch.setattr(free_text_context, "fg_bg_compare", lambda fg, bg: (fg, bg))
    monkeypatch.setattr(free_text_context.stroke, "get_text_stroke_width", lambda *_args: 0)
    context = free_text_context.FreeTextSolveContext()
    context.prepare(region, zone, obstacles, config, (600, 500), args[0], region._free_text_source_mask)
    coarse_sizes = context._coarse_font_sizes(145, 12)
    assert region._free_text_attempt_qa == {
        "typography_candidates": 2,
        "attempted_font_sizes": coarse_sizes[:coarse_sizes.index(35) + 1],
    }


def test_lazy_typography_preserves_deferred_emergency_stage_order():
    text_render.set_font(get_default_eng_font())
    profile = OriginalLayoutProfile(
        font_size=145, line_count=2, lines=[], centroid=(250.0, 180.0),
        bbox=(90, 100, 410, 260), block_width=320, block_height=160, occupancy=0.7,
    )
    target = FreeTextDamageTarget(
        mask=np.ones((1, 1), np.uint8), centroid_x=250.0, centroid_y=180.0,
        bbox=(70, 80, 430, 280), area=72000, width=120, height=200,
        source_centroid=profile.centroid,
    )
    config = Config()
    config.render.font_size = None
    config.render.font_size_minimum = 12
    config.render.no_hyphenation = False
    args = ("ABCDEFGHIJKLMN OPQRSTUVWXYZ", profile, config, (600, 500))

    eager = free_text_stages._free_text_typography_candidates(*args, target=target)
    raw_stages, _ = free_text_stages._free_text_typography_candidates(
        *args, target=target, lazy_stages=True,
    )
    lazy = [candidate for stage in raw_stages for candidate in stage]

    emergency_indices = [
        index for index, candidate in enumerate(eager)
        if candidate.qa.get("emergency_word_split")
    ]
    assert emergency_indices
    assert all(candidate.qa.get("emergency_word_split") for candidate in eager[emergency_indices[0]:])
    assert [_candidate_key(item) for item in lazy] == [_candidate_key(item) for item in eager]


def test_source_height_filter_sets_stroke_from_first_viable_stage(monkeypatch):
    profile = OriginalLayoutProfile(
        font_size=20, line_count=1, lines=[], centroid=(5.0, 5.0),
        bbox=(0, 0, 10, 10), block_width=10, block_height=8, occupancy=1.0,
    )
    target = FreeTextDamageTarget(
        mask=np.ones((1, 1), np.uint8), centroid_x=5.0, centroid_y=5.0,
        bbox=(0, 0, 10, 10), area=100, width=10, height=10,
        source_centroid=profile.centroid,
    )

    def candidate(font_size, height):
        return LayoutCandidate(
            font_size, 0, 0,
            [PlacedLine("TEXT", 0, 0, 10, height, BandSlot(0, 10, 0, height))],
            0, 0,
        )

    rejected = candidate(20, 50)
    viable = candidate(10, 20)
    stats = {
        "candidate_count": 2, "highest_font_size": 20, "font_sizes": {20, 10},
        "candidates": [rejected, viable],
    }
    monkeypatch.setattr(
        free_text_context, "_free_text_typography_candidates",
        lambda *_args, **_kwargs: (iter([[rejected], [viable]]), stats),
    )
    monkeypatch.setattr(free_text_context, "build_original_layout_profile", lambda *_args: profile)
    monkeypatch.setattr(free_text_context, "is_preserved_region", lambda _region: False)
    monkeypatch.setattr(free_text_context, "fg_bg_compare", lambda fg, bg: (fg, bg))
    stroke_sizes = []
    monkeypatch.setattr(
        free_text_context.stroke, "get_text_stroke_width",
        lambda font_size, *_args: stroke_sizes.append(font_size) or font_size,
    )

    region = SimpleNamespace(
        _free_text_source_mask=np.ones((1, 1), np.uint8), _free_text_attempt_qa={},
        bg_colors=None, target_lang="en_US",
        get_font_colors=lambda: ((0, 0, 0), (255, 255, 255)),
    )
    panel = PanelConstraint("panel", (0, 0, 10, 40))
    zone = SimpleNamespace(ownership_mask=np.ones((1, 1), np.uint8), damage_target=target, panel_constraint=panel)
    obstacles = SimpleNamespace(text_mask=np.zeros((1, 1), np.uint8))
    context = free_text_context.FreeTextSolveContext()

    context.prepare(region, zone, obstacles, Config(), (100, 100), "TEXT", region._free_text_source_mask)

    assert context.stage_groups[0] == [viable]
    assert context.stroke_width == 10
    assert stroke_sizes == [10]
