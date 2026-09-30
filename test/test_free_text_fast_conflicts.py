from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from manga_translator.rendering.layout.clearance import rendered_masks_conflict
from manga_translator.rendering.layout.solver import (
    _free_text_fast_conflict_regions,
    _select_free_text_joint_candidates,
)


def _region_at(x, status="free_text_ideal"):
    candidate = SimpleNamespace(font_size=20, status=status, x=x, penalty=0, qa={}, lines=[])
    return SimpleNamespace(candidate=candidate, translation="A", _free_text_attempt_qa={})


def _data(candidate, _shape):
    return (candidate.x, 0, candidate.x + 4, 4), np.ones((4, 4), dtype=np.uint8), None, None


def test_only_conflicting_fast_regions_rerun():
    first, second, independent = (_region_at(x) for x in (0, 6, 30))
    regions = [first, second, independent]
    plans = {id(region): [region.candidate] for region in regions}
    with patch("manga_translator.rendering.layout.solver._candidate_data", side_effect=_data):
        assert _free_text_fast_conflict_regions(regions, plans, (40, 40)) == {id(first), id(second)}


def test_conflict_chain_reruns_all_fast_participants():
    regions = [_region_at(x) for x in (0, 6, 12)]
    plans = {id(region): [region.candidate] for region in regions}
    with patch("manga_translator.rendering.layout.solver._candidate_data", side_effect=_data):
        assert _free_text_fast_conflict_regions(regions, plans, (40, 40)) == {id(region) for region in regions}


def test_fast_vs_exhaustive_reruns_only_fast_region():
    fast, exhaustive = _region_at(0), _region_at(6, "free_text")
    regions = [fast, exhaustive]
    plans = {id(region): [region.candidate] for region in regions}
    with patch("manga_translator.rendering.layout.solver._candidate_data", side_effect=_data):
        assert _free_text_fast_conflict_regions(regions, plans, (40, 40)) == {id(fast)}


def test_local_pool_reruns_only_when_every_validated_choice_conflicts():
    local, exhaustive = _region_at(0, "free_text_local"), _region_at(0, "free_text")
    alternative = _region_at(30, "free_text_local").candidate
    regions = [local, exhaustive]
    plans = {id(local): [local.candidate, alternative], id(exhaustive): [exhaustive.candidate]}
    with patch("manga_translator.rendering.layout.solver._candidate_data", side_effect=_data):
        assert _free_text_fast_conflict_regions(regions, plans, (40, 40)) == set()

    alternative.x = 1
    with patch("manga_translator.rendering.layout.solver._candidate_data", side_effect=_data):
        assert _free_text_fast_conflict_regions(regions, plans, (40, 40)) == {id(local)}


def test_exhaustive_conflict_keeps_existing_deeper_rerun():
    first, second = _region_at(0, "free_text"), _region_at(6, "free_text")
    regions = [first, second]
    plans = {id(region): [region.candidate] for region in regions}
    with patch("manga_translator.rendering.layout.solver._candidate_data", side_effect=_data):
        assert _free_text_fast_conflict_regions(regions, plans, (40, 40)) == {id(first), id(second)}


def test_joint_selection_after_conflict_rerun_has_rendered_clearance():
    first, second, independent = (_region_at(x) for x in (0, 6, 30))
    regions = [first, second, independent]
    plans = {id(region): [region.candidate] for region in regions}
    with patch("manga_translator.rendering.layout.solver._candidate_data", side_effect=_data):
        rerun_ids = _free_text_fast_conflict_regions(regions, plans, (40, 40))
    assert rerun_ids == {id(first), id(second)}
    first.candidate = _region_at(0, "free_text").candidate
    second.candidate = _region_at(12, "free_text").candidate
    for region in (first, second):
        plans[id(region)] = [region.candidate]
    with patch("manga_translator.rendering.layout.solver._candidate_data", side_effect=_data), patch(
        "manga_translator.rendering.layout.joint_layout._candidate_data", side_effect=_data
    ):
        chosen = _select_free_text_joint_candidates(regions, plans, (40, 40))
    assert len(chosen) == 3
    assert chosen[id(independent)] is independent.candidate
    selected = list(chosen.values())
    for index, first_candidate in enumerate(selected):
        for second_candidate in selected[index + 1:]:
            assert not rendered_masks_conflict(
                *_data(first_candidate, (40, 40))[:2], first_candidate.font_size,
                *_data(second_candidate, (40, 40))[:2], second_candidate.font_size,
            )
