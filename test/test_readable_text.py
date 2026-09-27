from types import SimpleNamespace

from manga_translator.rendering.layout import readable_text
from manga_translator.rendering.layout.hard_line_breaks import HARD_LINE_BREAK
from manga_translator.rendering.layout.failure_policy import should_restore_source


def test_scaled_floor_and_configured_minimum():
    config = SimpleNamespace(font_size_minimum=0)
    assert readable_text.readable_font_minimum(config, (2048, 1400)) == 12
    assert readable_text.readable_font_minimum(config, (4096, 2800)) == 24
    config.font_size_minimum = 18
    assert readable_text.readable_font_minimum(config, (2048, 1400)) == 18


def test_multiple_long_tokens_compounds_and_combining_characters(monkeypatch):
    from manga_translator.rendering import text_render
    import regex
    monkeypatch.setattr(text_render, "select_hyphenator", lambda _: None)
    monkeypatch.setattr(readable_text, "precompute_widths",
                        lambda words, _: ([len(regex.findall(r"\X", word)) for word in words], 1))
    words = ["THGISABLESS-Happyha", "Onkyou-kuuun", "Féels", "good…"]
    split, changes = readable_text.split_oversized_words(words[:2], 12, 7, "ENG")
    split_short, changes_short = readable_text.split_oversized_words(words[2:], 12, 4, "ENG")
    all_changes = changes + changes_short
    assert len(all_changes) == 4
    assert HARD_LINE_BREAK in split
    assert HARD_LINE_BREAK in split_short
    assert all(len(regex.findall(r"\X", word)) <= 7 for word in split if word != HARD_LINE_BREAK)
    for original, change in zip(words, all_changes):
        assert sum(s != "existing_break" for s in change["strategies"]) <= 1
        reconstructed = ""
        for fragment, strategy in zip(change["fragments"][:-1], change["strategies"]):
            reconstructed += fragment[:-1] if strategy != "existing_break" and fragment.endswith("-") else fragment
        assert reconstructed + change["fragments"][-1] == original
    assert any("existing_break" in change["strategies"] for change in all_changes)
    assert all(sum(c.isalpha() for c in fragment) >= 2
               for change in changes_short for fragment in change["fragments"])


def test_single_long_word_only_hyphenated_once():
    from manga_translator.rendering import get_default_eng_font, text_render
    text_render.set_font(get_default_eng_font())
    # At size=23 with width_limit=98, 'inter-' is 99px and 'terview.' is 128px;
    # must NOT double-hyphenate into ['in-', 'ter-', 'view.'].
    split_23, changes_23 = readable_text.split_oversized_words(["interview."], 23, 98, "ENG", allow_emergency=False)
    assert split_23 == ["interview."]
    assert changes_23 == []
    # At size=22 with width_limit=98, 'inter-' (93px) and 'view.' (72px) both fit with 1 hyphen.
    split_22, changes_22 = readable_text.split_oversized_words(["interview."], 22, 98, "ENG", allow_emergency=False)
    assert split_22 == ["inter-", HARD_LINE_BREAK, "view."]
    assert len(changes_22) == 1 and changes_22[0]["strategies"] == ["dictionary"]


def test_forced_split_keeps_short_words_at_least_two_letters_on_each_side(monkeypatch):
    from manga_translator.rendering import text_render
    monkeypatch.setattr(text_render, "select_hyphenator", lambda _: None)
    monkeypatch.setattr(readable_text, "precompute_widths",
                        lambda words, _: ([len(word) for word in words], 1))

    split, changes = readable_text.split_oversized_words(["abcdef"], 12, 4, "ENG")
    too_short, too_short_changes = readable_text.split_oversized_words(["abcde"], 12, 4, "ENG")

    assert split == ["abc-", HARD_LINE_BREAK, "def"]
    assert changes[0]["strategies"] == ["emergency"]
    assert too_short == ["ab-", HARD_LINE_BREAK, "cde"]
    assert too_short_changes[0]["strategies"] == ["emergency"]


def test_failed_translated_and_preserved_text_restore_source():
    for policy in ("translate", "preserve"):
        assert should_restore_source(SimpleNamespace(translation="１０：５６", translation_policy=policy,
            placement_mode="FREE_TEXT", _solver_status="no_valid_layout"))
    assert not should_restore_source(SimpleNamespace(
        _free_text_attempt_qa={"placement_attempts": [{"failure_stage": "empty_ownership"}]},
    ))
    assert not should_restore_source(
        SimpleNamespace(bubble_id="bubble_12", _solver_status="font_policy_infeasible"),
        active_bubble_ids={"bubble_12"},
    )
    assert should_restore_source(
        SimpleNamespace(bubble_id="bubble_12", _solver_status="font_policy_infeasible"),
        active_bubble_ids=set(),
    )


def test_bubble_obstacle_map_strips_narrow_detector_spikes():
    import numpy as np
    from manga_translator.rendering.layout.models import PlacementMode
    from manga_translator.rendering.layout.obstacles import build_page_obstacle_map

    mask = np.zeros((400, 400), dtype=np.uint8)
    mask[100:300, 100:250] = 1  # Main bubble body (150x200)
    mask[40:100, 170:176] = 1   # Spurious 6px-wide detector spike
    region = SimpleNamespace(placement_mode=PlacementMode.BUBBLE, _bubble_mask=mask, lines=[])
    obstacles = build_page_obstacle_map([region], (400, 400), bubble_halo=4)
    assert obstacles.bubble_mask[200, 175] == 1
    assert obstacles.bubble_mask[60, 173] == 0
    assert obstacles.protected_bubble_mask[60, 173] == 0


def test_centering_precedes_larger_offset_font():
    centered = SimpleNamespace(font_size=16, penalty=20, qa={"center_error_px": 3})
    offset = SimpleNamespace(font_size=17, penalty=1, qa={"center_error_px": 44})
    assert readable_text.centered_candidate_key(centered) < readable_text.centered_candidate_key(offset)


def test_joint_layout_keeps_small_bubble_centered(monkeypatch):
    import numpy as np
    from manga_translator.rendering.layout import joint_layout
    from manga_translator.rendering.layout.solver import _RegionLayoutPlan
    centered = SimpleNamespace(font_size=16, penalty=61.47, qa={"center_error_px": 3.4}, status="ok")
    offset = SimpleNamespace(font_size=17, penalty=57.99, qa={"center_error_px": 41.6}, status="ok")
    monkeypatch.setattr(joint_layout, "_candidate_data", lambda *_: ((0, 0, 1, 1), np.ones((1, 1), bool), (0, 0, 1, 1), (0, 0)))
    monkeypatch.setattr(joint_layout, "_page_font_penalty", lambda *_: 0)
    plan = _RegionLayoutPlan(region=SimpleNamespace(_font_policy_diagnostics={}), candidates=[centered, offset])
    assert joint_layout._choose_joint_layout([plan], (100, 100)) == (centered,)


def test_preserved_numeric_tokens_are_not_split(monkeypatch):
    from manga_translator.rendering import text_render
    monkeypatch.setattr(text_render, "select_hyphenator", lambda _: None)
    words = ["１０：５６", "20260926"]
    split, changes = readable_text.split_oversized_words(words, 24, 1, "ENG")
    assert split == words
    assert changes == []


def test_exact_candidate_footprint_used_for_joint_collision_checks():
    import numpy as np
    from manga_translator.rendering.layout.joint_layout import _candidate_data
    from manga_translator.rendering.layout.models import LayoutCandidate, PlacedLine
    candidate = LayoutCandidate(12, 0, 0, [PlacedLine("X", 5, 5, 10, 12)], 0, 0)
    visual = np.ones((3, 4), dtype=bool)
    candidate._render_footprint = ((4, 4, 8, 7), visual)
    box, mask, _, _ = _candidate_data(candidate, (30, 30))
    assert box == (4, 4, 8, 7)
    assert mask is visual


def test_saved_editor_restores_preserve_policy_from_translation_checkpoint():
    from server.pipeline_rerun_metadata import restore_source_metadata
    from manga_translator.pipeline.serialization import serialize_editor_regions, deserialize_textblocks
    from manga_translator.utils import TextBlock
    source = TextBlock([[[0, 0], [30, 0], [30, 30], [0, 30]]], texts=["１０：５６"],
                       translation="１０：５６", target_lang="ENG", font_size=17)
    source.region_id, source.translation_policy = "clock", "preserve"
    payload = serialize_editor_regions([source])
    assert deserialize_textblocks(payload)[0].translation_policy == "preserve"
    payload[0].pop("translation_policy")  # Legacy editor payload omitted this field.
    restored = deserialize_textblocks(payload)
    restore_source_metadata(restored, [source])
    assert restored[0].translation_policy == "preserve"


def test_expanded_search_reaches_scaled_domain_limit():
    from manga_translator.rendering.layout.free_text_search import _free_text_offset_search
    assert (112, 0) in _free_text_offset_search(112)
    assert (224, 0) in _free_text_offset_search(224)
    assert all(max(abs(x), abs(y)) <= 56 for x, y in _free_text_offset_search(56))
