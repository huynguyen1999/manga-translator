import cv2
import numpy as np
from types import SimpleNamespace

from manga_translator.config import Config, RenderConfig
from manga_translator.rendering import get_default_eng_font, text_render
from manga_translator.rendering.layout import (
    BubbleGeometry,
    PlacementMode,
    build_lobe_graph,
    build_free_text_ownership_zones,
    build_page_obstacle_map,
    classify_placement_modes,
    layout_page,
)
from manga_translator.rendering.layout.models import (
    BandSlot,
    FreeTextZone,
    LayoutCandidate,
    OriginalLayoutProfile,
    PageObstacleMap,
    PanelConstraint,
    PlacedLine,
)
from manga_translator.rendering.layout.free_text_solver import (
    _solve_free_text_region,
    _source_height_candidates,
)
from manga_translator.rendering.layout.source_profile import _effective_source_font_size
from manga_translator.rendering.layout.solver import (
    RowGeometry,
    _build_region_layout_plan,
    _candidate_data,
    _compression_severity,
    _cropped_masks_overlap,
    _dp_word_break_rows,
    _apply_layout_candidate,
)
import manga_translator.rendering.layout.solver as layout_solver
import manga_translator.rendering.layout.engine as layout_engine
import manga_translator.rendering.layout.free_text_solver as free_text_solver
import manga_translator.rendering.layout.ownership as layout_ownership
from manga_translator.rendering.bubble_layout import group_regions_by_bubbles
from manga_translator.detection.bubble import BubbleDetection
from manga_translator.utils import Context, TextBlock


def _region(x, y, text, region_id=None):
    return TextBlock(
        [[[x, y], [x + 30, y], [x + 30, y + 20], [x, y + 20]]],
        texts=[text], translation=text, target_lang="ENG", region_id=region_id,
    )


def test_production_geometry_detects_connected_lobes():
    mask = np.zeros((120, 180), np.uint8)
    cv2.circle(mask, (45, 60), 35, 1, -1)
    cv2.circle(mask, (135, 60), 35, 1, -1)
    cv2.rectangle(mask, (45, 52), (135, 68), 1, -1)

    graph = build_lobe_graph(mask)

    assert len(graph.lobe_masks) == 2
    assert graph.adjacency == [(0, 1)]
    assert BubbleGeometry(mask).has_safe_pixels(12)


def test_grouped_regions_keep_source_identity_and_geometry():
    mask = np.zeros((100, 140), np.uint8)
    cv2.rectangle(mask, (5, 5), (130, 95), 1, -1)
    regions = [_region(20, 20, "A", "A"), _region(20, 55, "B", "B")]

    grouped = group_regions_by_bubbles(regions, [BubbleDetection(mask, 0.9)], group=True)

    assert len(grouped) == 1
    assert grouped[0].region_id == "bubble_0"
    assert grouped[0].source_region_ids == ["A", "B"]
    assert [item["id"] for item in grouped[0].source_regions] == ["A", "B"]
    assert [item["reading_order"] for item in grouped[0].source_regions] == [0, 1]
    assert grouped[0].source_regions[1]["polygons"] == regions[1].lines.tolist()


def test_bubble_association_keeps_regions_separate_by_default():
    mask = np.ones((100, 140), np.uint8)
    regions = [_region(20, 20, "A", "A"), _region(20, 55, "B", "B")]

    associated = group_regions_by_bubbles(regions, [BubbleDetection(mask, 0.9)])

    assert [region.region_id for region in associated] == ["A", "B"]
    assert all(getattr(region, "_bubble_mask", None) is not None for region in associated)


def test_layout_page_assigns_ids_modes_and_disjoint_free_text_zones():
    image = np.full((100, 160, 3), 255, np.uint8)
    first, second = _region(10, 30, "FIRST", "first"), _region(90, 30, "SECOND", "second")
    ctx = Context(img_rgb=image, text_regions=[first, second], mask=np.zeros((100, 160), np.uint8))
    config = SimpleNamespace(render=SimpleNamespace(font_size_minimum=8, font_size=12, font_size_offset=0, line_spacing=0, no_hyphenation=False))

    result = layout_page(ctx, config, "fonts/anime_ace.ttf")
    assert ctx.page_geometry is None
    assert ctx._free_text_zones is None
    assert all(region._free_text_zone is None for region in ctx.text_regions)
    classify_placement_modes(ctx.text_regions)
    zones = build_free_text_ownership_zones(
        ctx.text_regions, build_page_obstacle_map(ctx.text_regions, image.shape[:2]), ctx.mask
    )

    assert set(result.regions) == {"first", "second"}
    assert all(region.placement_mode is PlacementMode.FREE_TEXT for region in ctx.text_regions)
    assert ctx._free_text_layout_debug is None
    assert not hasattr(first, "_free_text_glyph_mask")
    assert not np.any(zones[id(first)].ownership_mask & zones[id(second)].ownership_mask)


def test_enabled_bubble_detection_keeps_unmatched_regions_on_free_text_path(monkeypatch):
    image = np.full((100, 140, 3), 255, np.uint8)
    region = _region(30, 30, "UNMATCHED CAPTION", "caption")
    ctx = Context(
        img_rgb=image, text_regions=[region], mask=np.zeros(image.shape[:2], np.uint8),
        bubble_detections=[BubbleDetection(np.zeros(image.shape[:2], np.uint8), 0.9)],
    )
    config = Config()
    config.bubble_detection.enabled = True

    result = layout_engine.layout_page(ctx, config, "fonts/anime_ace.ttf")

    assert result.regions["caption"].placement_mode is PlacementMode.FREE_TEXT
    assert getattr(region, "_bubble_interior", None) is None


def test_saved_detections_keep_unmatched_regions_free_when_detection_is_disabled():
    image = np.full((100, 140, 3), 80, np.uint8)
    cv2.rectangle(image, (10, 10), (90, 80), (255, 255, 255), -1)
    region = _region(30, 30, "UNMATCHED CAPTION", "caption")
    ctx = Context(
        img_rgb=image, text_regions=[region], mask=np.zeros(image.shape[:2], np.uint8),
        bubble_detections=[BubbleDetection(np.zeros(image.shape[:2], np.uint8), 0.9)],
    )
    config = Config()
    config.bubble_detection.enabled = False

    result = layout_engine.layout_page(ctx, config, "fonts/anime_ace.ttf")

    assert result.regions["caption"].placement_mode is PlacementMode.FREE_TEXT
    assert getattr(region, "_bubble_interior", None) is None


def test_unplaceable_free_text_is_suppressed_with_review_reason(monkeypatch):
    image = np.full((100, 140, 3), 255, np.uint8)
    region = _region(30, 30, "UNPLACEABLE CAPTION", "caption")
    ctx = Context(img_rgb=image, text_regions=[region], mask=np.zeros(image.shape[:2], np.uint8))
    config = Config()
    config.bubble_detection.enabled = True
    monkeypatch.setattr(layout_solver, "_solve_free_text_region", lambda *_args, **_kwargs: None)

    layout_engine.layout_page(ctx, config, "fonts/anime_ace.ttf")

    assert region._render_suppressed
    assert region.review_required
    assert region.review_reason == "no_valid_layout"


def test_empty_free_text_ownership_skips_candidate_search(monkeypatch):
    region = _region(20, 20, "DUPLICATE")
    shape = (100, 120)
    region._free_text_source_mask = np.ones(shape, dtype=np.uint8)
    empty = np.zeros(shape, dtype=np.uint8)
    zone = FreeTextZone(
        source_bbox=(20, 20, 50, 40), ownership_mask=empty,
        obstacle_mask=empty, coverage_target_mask=empty,
        coverable_damage_mask=empty, coverage_weight_map=np.zeros(shape, dtype=np.float32),
        core_damage_mask=empty,
    )
    obstacles = PageObstacleMap(empty, empty, empty, np.ones(shape, dtype=np.uint8))

    def unexpected_search(*_args, **_kwargs):
        raise AssertionError("empty ownership must short-circuit before typography search")

    monkeypatch.setattr(free_text_solver, "_free_text_typography_candidates", unexpected_search)

    assert _solve_free_text_region(
        region, zone, obstacles, Config(), shape, solver_margin=2.0, solver_max_y_trials=8,
    ) is None


def test_free_text_damage_scope_uses_geometry_calibrated_font_size(monkeypatch):
    text = "うああっ♡あハァっ！チンコぉッ奥くるぅっ」"
    region = _region(0, 0, text)
    region.lines = np.asarray([[[0, 0], [272, 0], [272, 283], [0, 283]]], dtype=np.int32)
    region.font_size = 272
    region.source_font_size = 272
    region.source_regions = [{
        "bbox": [0, 0, 272, 283],
        "source_text": text,
    }]
    region.placement_mode = PlacementMode.FREE_TEXT
    shapes = []
    original_dilate = cv2.dilate

    def capture_dilate(source, kernel, *args, **kwargs):
        shapes.append(kernel.shape)
        return original_dilate(source, kernel, *args, **kwargs)

    monkeypatch.setattr(layout_ownership.cv2, "dilate", capture_dilate)
    layout_ownership._extract_region_damage_masks(
        [region], (300, 300), inpaint_mask=np.ones((300, 300), dtype=np.uint8),
    )

    radius = max(3, round(_effective_source_font_size(region, 272) * 1.5))
    assert shapes == [(radius * 2 + 1, radius * 2 + 1)]


def test_candidate_collisions_match_page_masks_using_only_overlapping_crops():
    def candidate(x):
        return LayoutCandidate(
            font_size=12,
            y_origin=30,
            line_spacing=0.0,
            lines=[PlacedLine("TEXT", y=30, x=x, width=35, height=16)],
            penalty=0.0,
            glyph_clearance_p5=0.0,
            status="free_text",
        )

    shape = (90, 180)
    first = _candidate_data(candidate(20), shape)
    for x in (35, 56, 80):
        second = _candidate_data(candidate(x), shape)
        full_first = np.zeros(shape, dtype=bool)
        full_second = np.zeros(shape, dtype=bool)
        x1, y1, x2, y2 = first[0]
        full_first[y1:y2, x1:x2] = first[1]
        x1, y1, x2, y2 = second[0]
        full_second[y1:y2, x1:x2] = second[1]
        assert _cropped_masks_overlap(first[0], first[1], second[0], second[1]) == bool(np.any(full_first & full_second))
        assert first[1].size < shape[0] * shape[1]


def test_compression_thresholds_and_explicit_break_only_split_at_chosen_word():
    assert _compression_severity(0.90) == "satisfactory"
    assert _compression_severity(0.89) == "mild"
    assert _compression_severity(0.84) == "substantial"

    rows = [
        RowGeometry(0, 20, [BandSlot(0, 100, 0, 20)]),
        RowGeometry(24, 20, [BandSlot(0, 100, 24, 44)]),
    ]
    normal = _dp_word_break_rows(["INTER-", "VIEW"], [36, 24], 2, rows, 20)
    rescue = _dp_word_break_rows(
        ["INTER-", "VIEW"], [36, 24], 2, rows, 20, forced_break_after=0
    )

    assert any(len(lines) == 1 and lines[0].text == "INTER- VIEW" for lines in normal)
    assert any([line.text for line in lines] == ["INTER-", "VIEW"] for lines in rescue)


def test_hyphenation_variant_uses_dictionary_breaks_and_preserves_source_compounds(monkeypatch):
    class Dictionary:
        def syllables(self, word):
            if word == "already":
                return ["al", "read", "y"]
            return ["un", "char", "ac", "ter", "is", "tic", "ally"]

    monkeypatch.setattr(layout_solver.text_render, "select_hyphenator", lambda _lang: Dictionary())
    monkeypatch.setattr(
        layout_solver, "_precompute_widths",
        lambda words, _size: ([len(word) * 10 for word in words], 3),
    )

    variant = layout_solver._hyphenation_variant(
        ["SAY", "UNCHARACTERISTICALLY,", "NOW"], 1, "en_US", 32
    )
    assert variant is not None
    assert variant[1].endswith("-")
    assert variant[2].endswith(",")
    assert variant[1][:-1] + variant[2][:-1] == "UNCHARACTERISTICALLY"
    assert layout_solver._hyphenation_variant(["ANOTHER"], 0, "en_US", 32) is not None
    assert layout_solver._hyphenation_variant(["already!"], 0, "en_US", 28) == ["al-", "ready!"]
    assert layout_solver._hyphenation_variant(["SELF-DEFENSE"], 0, "en_US", 32) is None


def test_multiglyph_source_box_does_not_become_the_font_size():
    region = SimpleNamespace(source_regions=[{
        "source_text": "そんな断る男とかいないだろ．．．",
        "bbox": [693, 398, 873, 666],
    }])

    assert _effective_source_font_size(region, 180) == 55


def test_free_text_source_height_yields_to_shorter_panel():
    text_render.set_font(get_default_eng_font())
    profile = OriginalLayoutProfile(
        font_size=20, line_count=1, lines=[], centroid=(20, 50),
        bbox=(0, 0, 40, 100), block_width=40, block_height=100,
        occupancy=1.0,
    )
    candidate = LayoutCandidate(
        20, 0, 0, [PlacedLine("TEXT", 0, 0, 30, 40, BandSlot(0, 30, 0, 40))], 0, 0,
    )

    fitted = _source_height_candidates(
        [candidate], profile, None, PanelConstraint("panel", (0, 0, 50, 60))
    )

    assert fitted == [candidate]
    assert candidate.layout_bounds == (0, 0, 30, 60)


def test_long_word_does_not_lower_adaptive_font_target():
    narrow_bubble = np.ones((120, 64), dtype=np.uint8)

    assert layout_solver._estimate_adaptive_font_size(
        narrow_bubble, "A INTERVIEWER", 8
    ) >= 20


def test_page_font_baseline_sets_solver_target_and_diagnostics(monkeypatch):
    calls = []
    candidate = LayoutCandidate(25, 0, 0, [PlacedLine("SHORT", 0, 0, 50, 25)], 0, 0)

    def solve(**kwargs):
        calls.append(kwargs)
        return candidate

    monkeypatch.setattr(layout_solver, "solve_layout", solve)
    monkeypatch.setattr(layout_solver, "_estimate_adaptive_font_size", lambda *_: 16)
    monkeypatch.setattr(layout_solver, "build_original_layout_profile", lambda *_: None)
    monkeypatch.setattr(layout_solver, "fg_bg_compare", lambda fg, bg: (fg, bg))

    region = SimpleNamespace(
        translation="SHORT", source_font_size=32, target_lang="en_US",
        placement_mode=PlacementMode.BUBBLE, direction="hr",
        get_font_colors=lambda: ((0, 0, 0), (255, 255, 255)),
        get_translation_for_rendering=lambda: "SHORT",
    )
    config = SimpleNamespace(render=SimpleNamespace(
        font_size_minimum=12, font_size=None, font_size_offset=0,
        line_spacing=0, no_hyphenation=False,
    ))

    plan = _build_region_layout_plan(
        region, np.ones((100, 180), dtype=np.uint8), config, (100, 180),
        2.0, 4, None, 1, page_font_baseline=28,
    )

    assert calls[0]["font_size_max"] == 32
    assert region.calibrated_font_size == 32
    assert plan.candidates[0].qa["page_font_baseline"] == 28
    assert plan.candidates[0].qa["region_geometric_target"] == 32
    assert plan.candidates[0].qa["consistency_floor"] == 29


def test_explicit_font_setting_keeps_the_90_percent_floor():
    region = SimpleNamespace(source_font_size=30)
    policy = layout_solver.build_region_font_policy(
        region, adaptive_target=18, page_baseline=None, minimum=8,
        render_config=SimpleNamespace(font_size=30, font_size_offset=0),
    )
    assert policy.preferred_size == 30
    assert policy.consistency_floor == 27


def test_page_font_baseline_uses_dialogue_bubbles_and_ignores_preserved_text(monkeypatch):
    monkeypatch.setattr(
        layout_solver, "_estimate_adaptive_font_size",
        lambda _mask, text, _minimum: {"ordinary": 20, "second": 30}.get(text, 100),
    )
    mask = np.ones((40, 60), dtype=np.uint8)

    def region(text, policy=None):
        return SimpleNamespace(
            translation=text, translation_policy=policy,
            get_translation_for_rendering=lambda: text,
        )

    groups = [
        SimpleNamespace(interior=mask, regions=[region("ordinary"), region("preserved", "preserve")]),
        SimpleNamespace(interior=mask, regions=[region("second")]),
    ]

    assert layout_solver._page_dialogue_font_baseline(groups, 8) == 27


def test_bubble_rescue_is_gated_and_splits_only_one_bottleneck(monkeypatch):
    calls = []
    normal_font = {"value": 23}
    rescue = LayoutCandidate(30, 0, 0, [PlacedLine("UNCHARACTER-", 0, 0, 100, 30), PlacedLine("ISTICALLY", 35, 0, 80, 30)], 0, 0)

    def solve(**kwargs):
        calls.append(kwargs)
        return rescue if kwargs.get("forced_break_after") is not None else LayoutCandidate(
            normal_font["value"], 0, 0,
            [PlacedLine("UNCHARACTERISTICALLY", 0, 0, 100, normal_font["value"])], 8, 0,
        )

    monkeypatch.setattr(layout_solver, "solve_layout", solve)
    monkeypatch.setattr(layout_solver, "build_original_layout_profile", lambda *_: None)
    monkeypatch.setattr(layout_solver, "fg_bg_compare", lambda fg, bg: (fg, bg))
    monkeypatch.setattr(layout_solver, "_long_word_pressure", lambda *_: ({
        "longest_word": "UNCHARACTERISTICALLY",
        "longest_word_width": 300,
        "max_usable_row_width": 200,
        "word_pressure_ratio": 1.5,
        "long_word_bottleneck": True,
        "bottleneck_word": "UNCHARACTERISTICALLY",
    }, 0))
    monkeypatch.setattr(layout_solver, "_hyphenation_variant", lambda words, *_: [
        "UNCHARACTER-", "ISTICALLY", *words[1:]
    ])
    monkeypatch.setattr(layout_solver, "_precompute_widths", lambda words, _size: (
        [150 if word in {"UNCHARACTER-", "ISTICALLY"} else 300 for word in words], 10
    ))

    def build(text, no_hyphenation=False, font_size=23, placement_mode=PlacementMode.BUBBLE):
        normal_font["value"] = font_size
        region = SimpleNamespace(
            translation=text,
            target_lang="en_US",
            placement_mode=placement_mode,
            source_font_size=32,
            direction="hr",
            get_font_colors=lambda: ((0, 0, 0), (255, 255, 255)),
        )
        region.get_translation_for_rendering = lambda: region.translation
        cfg = SimpleNamespace(render=SimpleNamespace(
            font_size_minimum=12, font_size=32, font_size_offset=0,
            line_spacing=0, no_hyphenation=no_hyphenation,
        ))
        return _build_region_layout_plan(
            region, np.ones((100, 180), dtype=np.uint8), cfg, (100, 180),
            2.0, 4, None, 1,
        )

    healthy = build("UNCHARACTERISTICALLY", font_size=30)
    assert len(calls) == 1
    assert healthy.candidates[0].qa["font_ratio"] >= 0.90
    assert not healthy.candidates[0].qa["hyphenation_rescue_attempted"]

    calls.clear()
    disabled = build("UNCHARACTERISTICALLY", no_hyphenation=True)
    assert len(calls) == 1
    assert disabled.candidates[0].qa["hyphenation_reason"] == "disabled_by_config"
    assert not disabled.candidates[0].qa["hyphenation_rescue_attempted"]

    calls.clear()
    rescued = build("UNCHARACTERISTICALLY UNBELIEVABLY")
    assert [call.get("forced_break_after") for call in calls] == [None, 0]
    assert rescued.candidates[0] is rescue
    assert rescue.qa["introduced_hyphen_count"] == 1
    assert rescue.qa["introduced_hyphen_words"] == ["UNCHARACTERISTICALLY"]

    monkeypatch.setattr(layout_solver, "_long_word_pressure", lambda *_: ({
        "longest_word": "PARAGRAPH",
        "longest_word_width": 120,
        "max_usable_row_width": 200,
        "word_pressure_ratio": 0.6,
        "long_word_bottleneck": False,
        "bottleneck_word": None,
    }, None))
    calls.clear()
    paragraph = build("A VERY LARGE PARAGRAPH WITH MANY WORDS", font_size=23)
    assert len(calls) == 1
    assert paragraph.candidates[0].qa["hyphenation_reason"] == "no_long_word_bottleneck"

    monkeypatch.setattr(layout_solver, "_long_word_pressure", lambda *_: ({
        "longest_word": "UNCHARACTERISTICALLY",
        "longest_word_width": 300,
        "max_usable_row_width": 200,
        "word_pressure_ratio": 1.5,
        "long_word_bottleneck": True,
        "bottleneck_word": "UNCHARACTERISTICALLY",
    }, 0))
    monkeypatch.setattr(layout_solver, "_hyphenation_variant", lambda *_: None)
    calls.clear()
    no_break = build("UNCHARACTERISTICALLY", font_size=23)
    assert len(calls) == 1
    assert no_break.candidates[0].qa["hyphenation_reason"] == "no_dictionary_breakpoint"
    assert not no_break.candidates[0].qa["hyphenation_rescue_attempted"]

    def fail_pressure(*_):
        raise AssertionError("free text must skip rescue analysis")

    monkeypatch.setattr(layout_solver, "_long_word_pressure", fail_pressure)
    calls.clear()
    free_text = build(
        "UNCHARACTERISTICALLY", font_size=23,
        placement_mode=PlacementMode.FREE_TEXT,
    )
    assert len(calls) == 1
    assert free_text.candidates[0].qa["hyphenation_reason"] == "not_bubble_placement"


def test_long_word_hyphenation_preserves_preferred_font_without_normal_fit():
    text_render.set_font(get_default_eng_font())
    text = "UNCHARACTERISTICALLY"
    mask = np.ones((110, 300), dtype=np.uint8)
    region = TextBlock(
        [[[20, 20], [200, 20], [200, 60], [20, 60]]],
        texts=[text], translation=text, target_lang="en_US",
    )
    region.placement_mode = PlacementMode.BUBBLE
    region._bubble_interior = mask
    config = SimpleNamespace(render=RenderConfig(
        font_size=32, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))

    plan = _build_region_layout_plan(region, mask, config, mask.shape, 2.0, 8, None, 1)
    candidate = plan.candidates[0]

    assert candidate.qa["normal_font_size"] is None
    assert candidate.font_size == 32
    assert candidate.font_size >= 32 * 0.90
    assert candidate.qa["introduced_hyphen_count"] == 1
    assert sum(line.text.endswith("-") for line in candidate.lines) == 1
    assert candidate.qa["hyphenation_rescue_selected"]
    assert _apply_layout_candidate(plan, candidate, mask.shape)
    assert [line["text"] for line in region.layout_segments[0]["lines"]] == [
        line.text for line in candidate.lines
    ]


def test_long_word_that_cannot_fit_preferred_range_is_flagged_for_review():
    text_render.set_font(get_default_eng_font())
    text = "OH, A JOB INTERVIEWER?"
    # Bubble 110x120 is narrow; INTERVIEWER cannot fit on one line at 27px
    mask = np.ones((140, 130), dtype=np.uint8)
    region = TextBlock(
        [[[10, 10], [120, 10], [120, 130], [10, 130]]],
        texts=[text], translation=text, target_lang="en_US",
    )
    region.placement_mode = PlacementMode.BUBBLE
    region._bubble_interior = mask
    config = SimpleNamespace(render=RenderConfig(
        font_size=None, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))

    plan = _build_region_layout_plan(region, mask, config, mask.shape, 2.0, 8, None, 1, page_font_baseline=27)
    assert plan.candidates
    assert plan.candidates[0].font_size >= 14
    assert plan.candidates[0].qa["font_policy_status"] == "emergency_review"
    assert plan.candidates[0].qa["emergency_compression"] is True
    assert region.review_required is True
    assert region.review_reason == "text_requires_emergency_compression"


def test_saved_long_bubble_translation_renders_with_bounded_review_fallback():
    text_render.set_font(get_default_eng_font())
    text = "How about my breasts-?"
    mask = np.ones((326, 145), dtype=np.uint8)
    region = TextBlock(
        [[[0, 0], [145, 0], [145, 326], [0, 326]]],
        texts=[text], translation=text, target_lang="en_US", font_size=31,
    )
    region.source_font_size = 31
    region.placement_mode = PlacementMode.BUBBLE
    region._bubble_interior = mask
    config = SimpleNamespace(render=RenderConfig(
        font_size=31, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))

    plan = _build_region_layout_plan(region, mask, config, mask.shape, 2.0, 8, None, 1)

    assert plan.candidates[0].font_size >= 16
    assert plan.candidates[0].qa["font_policy_status"] == "emergency_review"
    assert region.review_required is True
    assert region.review_reason == "text_requires_emergency_compression"
    assert _apply_layout_candidate(plan, plan.candidates[0], mask.shape)
    assert region._render_suppressed is False
    assert [line["text"] for line in region.layout_segments[0]["lines"]] == [
        "How", "about", "my", "breasts-?",
    ]


def test_unfittable_bubble_keeps_source_font_when_suppressed():
    text_render.set_font(get_default_eng_font())
    text = "Nakamotonakahon"
    mask = np.ones((100, 70), dtype=np.uint8)
    region = TextBlock(
        [[[22, 36], [48, 36], [48, 63], [22, 63]]],
        texts=[text], translation=text, target_lang="en_US", font_size=6,
    )
    region.source_font_size = 24
    region.placement_mode = PlacementMode.BUBBLE
    region._bubble_interior = mask
    config = SimpleNamespace(render=RenderConfig(
        font_size=None, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))

    plan = _build_region_layout_plan(region, mask, config, mask.shape, 2.0, 8, None, 1)

    assert not plan.candidates
    assert region.font_size == 24
    assert region._render_suppressed is True


def test_healthy_natural_wrapping_beats_hyphenation():
    text_render.set_font(get_default_eng_font())
    text = "HELLO HOW ARE YOU TODAY MY FRIEND"
    mask = np.ones((160, 200), dtype=np.uint8)
    region = TextBlock(
        [[[10, 10], [190, 10], [190, 150], [10, 150]]],
        texts=[text], translation=text, target_lang="en_US",
    )
    region.placement_mode = PlacementMode.BUBBLE
    region._bubble_interior = mask
    config = SimpleNamespace(render=RenderConfig(
        font_size=None, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))

    plan = _build_region_layout_plan(region, mask, config, mask.shape, 2.0, 8, None, 1, page_font_baseline=28)
    candidate = plan.candidates[0]

    assert candidate.qa["hyphenation_rescue_selected"] is False
    assert candidate.qa["introduced_hyphen_count"] == 0
    assert not any(line.text.endswith("-") for line in candidate.lines)


def test_tiny_gain_does_not_justify_hyphenation(monkeypatch):
    calls = []
    normal_candidate = LayoutCandidate(24, 0, 0, [PlacedLine("TESTING", 0, 0, 80, 24)], 0, 0)
    rescue_candidate = LayoutCandidate(25, 0, 0, [PlacedLine("TEST-", 0, 0, 40, 25), PlacedLine("ING", 0, 0, 30, 25)], 0, 0)

    def solve(**kwargs):
        calls.append(kwargs)
        return rescue_candidate if kwargs.get("forced_break_after") is not None else normal_candidate

    monkeypatch.setattr(layout_solver, "solve_layout", solve)
    monkeypatch.setattr(layout_solver, "build_original_layout_profile", lambda *_: None)
    monkeypatch.setattr(layout_solver, "fg_bg_compare", lambda fg, bg: (fg, bg))
    monkeypatch.setattr(layout_solver, "_long_word_pressure", lambda *_: ({
        "longest_word": "TESTINGWORD",
        "longest_word_width": 150,
        "max_usable_row_width": 100,
        "word_pressure_ratio": 1.5,
        "long_word_bottleneck": True,
        "bottleneck_word": "TESTINGWORD",
    }, 0))
    monkeypatch.setattr(layout_solver, "_hyphenation_variants", lambda *args, **kwargs: [
        layout_solver.HyphenVariant(words=["TEST-", "ING"], word="TESTINGWORD", left="TEST-", right="ING", breakpoint=4, split_str="TEST-/ING")
    ])
    monkeypatch.setattr(layout_solver, "_hyphenation_variant", lambda words, *_: ["TEST-", "ING"])
    monkeypatch.setattr(layout_solver, "_precompute_widths", lambda words, _size: ([50, 40], 10))

    region = SimpleNamespace(
        translation="TESTINGWORD",
        target_lang="en_US",
        placement_mode=PlacementMode.BUBBLE,
        source_font_size=28,
        direction="hr",
        get_font_colors=lambda: ((0, 0, 0), (255, 255, 255)),
        get_translation_for_rendering=lambda: "TESTINGWORD",
    )
    config = SimpleNamespace(render=SimpleNamespace(
        font_size_minimum=12, font_size=28, font_size_offset=0,
        line_spacing=0, no_hyphenation=False,
    ))

    plan = _build_region_layout_plan(
        region, np.ones((100, 180), dtype=np.uint8), config, (100, 180),
        2.0, 4, None, 1, page_font_baseline=28,
    )

    # 24 -> 25 is only +1px gain (< HYPHEN_MIN_GAIN_PX = 2), so hyphenation should be rejected
    assert plan.candidates[0] is normal_candidate
    assert plan.candidates[0].qa["hyphenation_rescue_selected"] is False


def test_existing_compound_uses_bounded_review_fallback():
    text_render.set_font(get_default_eng_font())
    text = "SELF-DEFENSE"
    mask = np.ones((100, 180), dtype=np.uint8)
    region = TextBlock(
        [[[10, 10], [170, 10], [170, 90], [10, 90]]],
        texts=[text], translation=text, target_lang="en_US",
    )
    region.placement_mode = PlacementMode.BUBBLE
    region._bubble_interior = mask
    config = SimpleNamespace(render=RenderConfig(
        font_size=24, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))

    plan = _build_region_layout_plan(region, mask, config, mask.shape, 2.0, 8, None, 1)
    assert plan.candidates
    assert plan.candidates[0].qa["font_policy_status"] == "emergency_review"
    assert region.review_required is True
    assert region.review_reason == "text_requires_emergency_compression"


def test_numeric_tokens_not_artificially_split():
    text_render.set_font(get_default_eng_font())
    for num_text in ["48", "2026", "12:30", "100%"]:
        pressure, b_idx = layout_solver._long_word_pressure(
            BubbleGeometry(np.ones((60, 60), dtype=np.uint8)), [num_text], 24, 0, 2.0
        )
        assert b_idx is None
        variants = layout_solver._hyphenation_variants([num_text], 0, "en_US", 24)
        assert len(variants) == 0


def test_two_unfittable_long_words_are_flagged_for_review():
    text_render.set_font(get_default_eng_font())
    text = "UNCHARACTERISTICALLY MISUNDERSTANDING"
    mask = np.ones((120, 150), dtype=np.uint8)
    region = TextBlock(
        [[[10, 10], [140, 10], [140, 110], [10, 110]]],
        texts=[text], translation=text, target_lang="en_US",
    )
    region.placement_mode = PlacementMode.BUBBLE
    region._bubble_interior = mask
    config = SimpleNamespace(render=RenderConfig(
        font_size=28, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))

    plan = _build_region_layout_plan(region, mask, config, mask.shape, 2.0, 8, None, 1, page_font_baseline=28)
    assert not plan.candidates
    assert region.review_required is True
    assert region.review_reason == "text_does_not_fit"
    assert region._render_suppressed is True


def test_paragraph_density_does_not_trigger_random_hyphens():
    text_render.set_font(get_default_eng_font())
    text = "I WOULD LIKE TO DISCUSS THIS VERY IMPORTANT MATTER WITH YOU BEFORE WE DECIDE"
    mask = np.ones((200, 200), dtype=np.uint8)
    region = TextBlock(
        [[[10, 10], [190, 10], [190, 190], [10, 190]]],
        texts=[text], translation=text, target_lang="en_US",
    )
    region.placement_mode = PlacementMode.BUBBLE
    region._bubble_interior = mask
    config = SimpleNamespace(render=RenderConfig(
        font_size=None, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))

    plan = _build_region_layout_plan(region, mask, config, mask.shape, 2.0, 8, None, 1, page_font_baseline=26)
    candidate = plan.candidates[0]
    assert candidate.qa["hyphenation_rescue_selected"] is False
    assert candidate.qa["introduced_hyphen_count"] == 0


def test_page_consistency_across_dialogue_bubbles():
    text_render.set_font(get_default_eng_font())
    dialogue_texts = [
        "Hello there!",
        "How have you been doing?",
        "I have been looking for you all day.",
        "Let us meet at the station.",
        "See you soon!",
    ]
    mask = np.ones((150, 150), dtype=np.uint8)
    config = SimpleNamespace(render=RenderConfig(
        font_size=None, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))

    font_sizes = []
    for txt in dialogue_texts:
        reg = TextBlock([[[10, 10], [140, 10], [140, 140], [10, 140]]], texts=[txt], translation=txt, target_lang="en_US")
        reg.placement_mode = PlacementMode.BUBBLE
        reg._bubble_interior = mask
        plan = _build_region_layout_plan(reg, mask, config, mask.shape, 2.0, 8, None, 1, page_font_baseline=27)
        font_sizes.append(plan.candidates[0].font_size)

    median_font = float(np.median(font_sizes))
    assert min(font_sizes) >= median_font * 0.85


def test_different_sized_bubbles_remain_natural():
    text_render.set_font(get_default_eng_font())
    config = SimpleNamespace(render=RenderConfig(
        font_size=None, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))
    
    # Small bubble with dense content forces smaller font
    mask_s = np.ones((70, 70), dtype=np.uint8)
    reg_s = TextBlock([[[5, 5], [65, 5], [65, 65], [5, 65]]], texts=["This is a longer line of text."], translation="This is a longer line of text.", target_lang="en_US")
    reg_s.placement_mode = PlacementMode.BUBBLE
    reg_s._bubble_interior = mask_s
    plan_s = _build_region_layout_plan(reg_s, mask_s, config, mask_s.shape, 2.0, 8, None, 1, page_font_baseline=28)

    # Medium bubble
    mask_m = np.ones((130, 130), dtype=np.uint8)
    reg_m = TextBlock([[[5, 5], [125, 5], [125, 125], [5, 125]]], texts=["I see what you mean."], translation="I see what you mean.", target_lang="en_US")
    reg_m.placement_mode = PlacementMode.BUBBLE
    reg_m._bubble_interior = mask_m
    plan_m = _build_region_layout_plan(reg_m, mask_m, config, mask_m.shape, 2.0, 8, None, 1, page_font_baseline=28)

    # Large bubble
    mask_l = np.ones((220, 220), dtype=np.uint8)
    reg_l = TextBlock([[[5, 5], [215, 5], [215, 215], [5, 215]]], texts=["That is totally unexpected and unbelievable!"], translation="That is totally unexpected and unbelievable!", target_lang="en_US")
    reg_l.placement_mode = PlacementMode.BUBBLE
    reg_l._bubble_interior = mask_l
    plan_l = _build_region_layout_plan(reg_l, mask_l, config, mask_l.shape, 2.0, 8, None, 1, page_font_baseline=28)

    # Sizes should remain natural according to geometry
    assert plan_s.candidates[0].font_size <= plan_m.candidates[0].font_size <= plan_l.candidates[0].font_size


def test_safe_mask_containment_after_rescue():
    text_render.set_font(get_default_eng_font())
    text = "UNCHARACTERISTICALLY"
    mask = np.ones((110, 300), dtype=np.uint8)
    region = TextBlock(
        [[[20, 20], [280, 20], [280, 90], [20, 90]]],
        texts=[text], translation=text, target_lang="en_US",
    )
    region.placement_mode = PlacementMode.BUBBLE
    region._bubble_interior = mask
    config = SimpleNamespace(render=RenderConfig(
        font_size=32, font_size_minimum=8, no_hyphenation=False, line_spacing=0,
    ))

    plan = _build_region_layout_plan(region, mask, config, mask.shape, 2.0, 8, None, 1)
    candidate = plan.candidates[0]
    valid, p5 = layout_solver._validate_glyph_pixels(
        candidate.lines, BubbleGeometry(mask), candidate.font_size, 0, 2.0
    )
    assert valid is True
