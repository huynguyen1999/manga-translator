import asyncio
from types import SimpleNamespace

import cv2
import numpy as np

from manga_translator.config import Config, RenderConfig, Renderer
from manga_translator.pipeline.run import deserialize_textblocks, serialize_regions
from manga_translator.rendering import get_default_eng_font, render_page, text_render
from manga_translator.rendering.bubble_layout import encode_safe_shape
from manga_translator.rendering.serialization import encode_rendered_box
from manga_translator.rendering.placement_geometry import _points_for_rect
from manga_translator.rendering.layout.frozen import (
    hydrate_layout,
    layout_input_fingerprints,
    serialize_frozen_layout,
)
from manga_translator.rendering.layout.models import (
    LayoutCandidate,
    OriginalLayoutProfile,
    PageLayoutResult,
    PlacementMode,
    PlacedLine,
    RegionLayout,
)
from manga_translator.rendering.layout.engine import _region_layout
from manga_translator.rendering.layout.regions import prepare_regions
from manga_translator.rendering.layout.solver import (
    build_original_layout_profile,
    build_region_font_policy,
    _free_text_typography_candidates,
    _hyphenation_variants,
    _select_free_text_joint_candidates,
)
from manga_translator.rendering.layout.validation import validate_layout
from manga_translator.utils import Context, TextBlock, resolve_render_content


def _region(source, translation="", region_id="region-a", font_size=24):
    return TextBlock(
        lines=[[[10, 10], [90, 10], [90, 40], [10, 40]]],
        texts=[source],
        translation=translation,
        font_size=font_size,
        target_lang="ENG",
        region_id=region_id,
    )


def test_missing_translation_never_falls_back_to_japanese_source():
    region = _region("足が震える．．．っ")

    assert resolve_render_content(region) == ""
    assert region.get_translation_for_rendering() == ""
    assert region._translation_incomplete


def test_preserved_numeric_is_exact_and_ignores_case_transform_across_freeze():
    region = _region("(48)", translation="corrupted")
    region.translation_policy = "preserve"
    region.layout_segments = [{
        "x": 10, "y": 10, "width": 80, "height": 30, "font_size": 24,
        "text": "(48)", "lines": [{"text": "(48)", "x": 10, "y": 10, "width": 40, "height": 24}],
    }]
    ctx = Context(img_rgb=np.full((100, 120, 3), 255, np.uint8), text_regions=[region])
    config = Config(render=RenderConfig(uppercase=True))
    font = get_default_eng_font()

    prepare_regions(ctx.text_regions)
    doc = serialize_frozen_layout(ctx, config, font)
    frozen = doc["regions"][0]
    assert frozen["render_content"] == "(48)"
    assert frozen["segments"][0]["lines"][0]["text"] == "(48)"

    restored = deserialize_textblocks(serialize_regions([region]))[0]
    resumed = Context(img_rgb=ctx.img_rgb.copy(), text_regions=[restored])
    inputs = layout_input_fingerprints(resumed.text_regions, config, font, image_shape=ctx.img_rgb.shape)
    hydrate_layout(resumed, doc, inputs["fingerprint"])
    assert resolve_render_content(restored) == "(48)"


def test_source_provenance_survives_layout_mutation_and_retry():
    region = _region("原文", "TRANSLATION", font_size=30)
    prepare_regions([region])
    region.text = "MUTATED"
    region.lines[:] = 0
    region.font_size = 12
    prepare_regions([region])

    assert region.source_text_snapshot == "原文"
    assert region.source_font_size == 30
    assert region.source_geometry["bbox"] == [10, 10, 90, 40]


def test_free_text_prefers_source_minus_one_or_two_and_never_auto_enlarges():
    text_render.set_font(get_default_eng_font())
    profile = OriginalLayoutProfile(
        font_size=24, line_count=2, lines=[], centroid=(60, 40),
        bbox=(10, 10, 110, 70), block_width=100, block_height=60, occupancy=0.5,
    )
    config = Config(render=RenderConfig(font_size_minimum=8))

    candidates = _free_text_typography_candidates("NORMAL FREE TEXT", profile, config, (120, 160))

    assert candidates
    assert all(candidate.font_size <= 24 for candidate in candidates)
    assert any(candidate.font_size in {22, 23} for candidate in candidates)


def test_free_text_candidates_can_use_more_height_but_never_exceed_source_cap():
    text_render.set_font(get_default_eng_font())
    profile = OriginalLayoutProfile(
        font_size=24, line_count=1, lines=[], centroid=(60, 40),
        bbox=(10, 10, 110, 70), block_width=100, block_height=60, occupancy=0.5,
    )
    candidates = _free_text_typography_candidates(
        "ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN",
        profile, Config(render=RenderConfig(font_size_minimum=8)), (120, 160),
    )
    heights = [max(line.y + line.height for line in candidate.lines) for candidate in candidates]

    assert heights
    assert max(heights) > profile.block_height
    assert max(heights) <= profile.block_height * 1.5


def test_free_text_joint_search_solves_dense_collision_component():
    text_render.set_font(get_default_eng_font())

    def candidate(x, y, penalty=0):
        return LayoutCandidate(
            font_size=20, y_origin=0, line_spacing=0, penalty=penalty,
            glyph_clearance_p5=0, status="free_text", lines=[
                PlacedLine(text="A", x=x, y=y, width=18, height=20),
            ],
        )

    regions = [_region("source", "A", f"free-{index}") for index in range(6)]
    candidate_pools = [
        [candidate(20, 20), candidate(80, 20, 1), *[candidate(120 + i * 25, 20, i + 2) for i in range(6)]],
        [candidate(20, 20, i) for i in range(8)],
        *[[candidate(250 + index * 40, 120, i) for i in range(8)] for index in range(4)],
    ]
    plans = {id(region): pool for region, pool in zip(regions, candidate_pools)}

    chosen = _select_free_text_joint_candidates(regions, plans, (400, 600))

    assert len(chosen) == len(regions)
    assert chosen[id(regions[0])].lines[0].x == 80


def test_unknown_long_name_gets_bounded_visual_midpoint_hyphen_rescue(monkeypatch):
    text_render.set_font(get_default_eng_font())
    monkeypatch.setattr(text_render, "select_hyphenator", lambda _language: None)

    variants = _hyphenation_variants(["Nakamotonakahon"], 0, "en_US", 24)

    assert 1 <= len(variants) <= 3
    assert all(variant.left.endswith("-") for variant in variants)
    assert all(len(variant.left[:-1]) >= 3 and len(variant.right) >= 3 for variant in variants)


def test_duplicate_source_ownership_suppresses_second_render_region():
    first = _region("一", "ONE", "first")
    second = _region("二", "TWO", "second")
    first.source_region_ids = second.source_region_ids = ["source-a"]
    for region, text in ((first, "ONE"), (second, "TWO")):
        region.layout_segments = [{
            "x": 10, "y": 10, "width": 60, "height": 24, "font_size": 24,
            "text": text, "lines": [{"text": text, "x": 10, "y": 10, "width": 40, "height": 24}],
        }]
    ctx = Context(img_rgb=np.full((80, 100, 3), 255, np.uint8), text_regions=[first, second])

    doc = serialize_frozen_layout(ctx, Config(), get_default_eng_font())

    assert doc["render_ownership"] == {"source-a": "first"}
    assert doc["regions"][1]["render_suppressed"]
    assert second.review_required


def test_free_text_cleanup_underlay_repairs_owned_residual_before_render():
    image = np.full((80, 100, 3), 255, np.uint8)
    image[30:50, 45:55] = 0
    cleanup = np.zeros(image.shape[:2], np.uint8)
    cleanup[28:52, 43:57] = 1
    region = _region("字", "TEXT")
    region.free_text_cleanup_mask = encode_safe_shape(cleanup)
    ctx = Context(img_rgb=np.full_like(image, 255), img_inpainted=image, text_regions=[region])
    config = Config(render=RenderConfig(renderer=Renderer.none))

    output = asyncio.run(render_page(ctx, config, get_default_eng_font()))

    assert output[40, 50].mean() > 240


def test_final_collision_gate_suppresses_free_text_over_a_bubble():
    text_render.set_font(get_default_eng_font())
    bubble = _region("吹き出し", region_id="bubble")
    bubble.placement_mode = PlacementMode.BUBBLE
    bubble._bubble_mask = np.zeros((100, 120), np.uint8)
    cv2.circle(bubble._bubble_mask, (60, 50), 30, 1, -1)
    free = _region("外字", "OVERLAP", "free")
    free.placement_mode = PlacementMode.FREE_TEXT
    line = PlacedLine(text="OVERLAP", x=35, y=40, width=55, height=24)
    result = PageLayoutResult(regions={
        "free": RegionLayout(
            region_id="free", placement_mode=PlacementMode.FREE_TEXT,
            font_size=24, lines=[line],
        ),
    })
    ctx = Context(img_rgb=np.full((100, 120, 3), 255, np.uint8), text_regions=[bubble, free])

    diagnostics = validate_layout(ctx, result)

    assert free._render_suppressed
    assert diagnostics.metrics["regions"]["free"]["bubble_overlap_pixels"] > 0


def test_final_collision_gate_suppresses_bubble_bubble_collision():
    first = _region("吹き出し", "FIRST", "bubble-a")
    second = _region("吹き出し", "SECOND", "bubble-b")
    first.placement_mode = second.placement_mode = PlacementMode.BUBBLE
    lines = [PlacedLine(text="A", x=30, y=30, width=30, height=24)]
    result = PageLayoutResult(regions={
        region.region_id: RegionLayout(
            region_id=region.region_id,
            placement_mode=PlacementMode.BUBBLE,
            font_size=24,
            lines=lines,
        )
        for region in (first, second)
    })
    ctx = Context(img_rgb=np.full((100, 120, 3), 255, np.uint8), text_regions=[first, second])

    diagnostics = validate_layout(ctx, result)

    assert diagnostics.errors.count("render collision between bubble-a and bubble-b") == 1
    assert second._render_suppressed and second.review_required


def test_final_collision_gate_avoids_restored_text_from_suppressed_region():
    drawable = _region("first source", "VISIBLE", "drawable")
    drawable.placement_mode = PlacementMode.BUBBLE
    box = np.zeros((20, 40, 4), np.uint8)
    box[:, :, 3] = 255
    drawable.layout_bounds = [20, 15, 60, 35]
    drawable._bubble_box = box
    drawable.layout_segments = [{
        "x": 20, "y": 15, "width": 40, "height": 20, "font_size": 20,
        "lines": [], "rendered_png": encode_rendered_box(box),
    }]
    suppressed = _region("second source", "UNPLACED", "suppressed")
    suppressed.placement_mode = PlacementMode.BUBBLE
    suppressed._render_suppressed = True
    suppressed.review_required = True
    result = PageLayoutResult(regions={
        "drawable": RegionLayout(region_id="drawable", placement_mode=PlacementMode.BUBBLE),
    })
    ctx = Context(
        img_rgb=np.full((100, 120, 3), 255, np.uint8),
        text_regions=[drawable, suppressed],
    )

    diagnostics = validate_layout(ctx, result)

    assert any("render overlaps restored source text for region drawable" in error for error in diagnostics.errors)
    assert drawable._render_suppressed and drawable.review_required


def test_final_collision_gate_uses_raster_fallback_and_its_full_outline():
    regions = [_region("吹き出し", text, region_id) for text, region_id in (("FIRST", "a"), ("SECOND", "b"))]
    result_regions = {}
    for region, x in zip(regions, (20, 35)):
        region.placement_mode = PlacementMode.BUBBLE
        # This is the exact antialiased render crop, including its thick outline.
        box = np.zeros((24, 24, 4), np.uint8)
        box[4:20, 4:20, 3] = 255
        region.layout_bounds = [x, 20, x + 24, 44]
        region._bubble_box = box
        region._bubble_points = _points_for_rect(region, region.layout_bounds, 120, 100)
        region.layout_segments = [{
            "x": x, "y": 20, "width": 24, "height": 24, "font_size": 24,
            "lines": [], "rendered_png": encode_rendered_box(box),
        }]
        result_regions[region.region_id] = RegionLayout(
            region_id=region.region_id, placement_mode=PlacementMode.BUBBLE, font_size=24,
        )
    ctx = Context(img_rgb=np.full((100, 120, 3), 255, np.uint8), text_regions=regions)

    diagnostics = validate_layout(ctx, PageLayoutResult(regions=result_regions))

    assert any("render collision" in error for error in diagnostics.errors)
    assert regions[1]._render_suppressed and regions[1].review_required


def test_final_font_readability_floor_requests_review():
    region = _region("原文", "VERY SMALL", "small-font", font_size=24)
    prepare_regions([region])
    region.placement_mode = PlacementMode.FREE_TEXT
    region.font_size = 9
    region.layout_segments = [{
        "x": 10, "y": 10, "width": 60, "height": 12, "font_size": 9,
        "lines": [{"text": "VERY SMALL", "x": 10, "y": 10, "width": 60, "height": 9}],
    }]
    layout = _region_layout(region, "test-font")
    result = PageLayoutResult(regions={"small-font": layout})
    ctx = Context(img_rgb=np.full((100, 120, 3), 255, np.uint8), text_regions=[region])

    validate_layout(ctx, result)

    assert layout.calibrated_font_size == 24
    assert region.review_required
    assert region._render_suppressed
    assert region.review_reason == "font_below_readability_floor"


def test_rotated_placement_is_not_clamped_into_a_distorted_page_quad():
    region = SimpleNamespace(angle=45)

    points = _points_for_rect(region, [0, 0, 20, 20], 100, 100)

    assert points.min() < 0


def test_invalid_frozen_segment_restores_source_and_requests_review():
    original = np.full((80, 100, 3), 255, np.uint8)
    original[10:40, 10:70] = 0
    region = _region("原文", "VISIBLE TRANSLATION", "invalid-frozen")
    region._layout_frozen = True
    region.layout_segments = [{"x": 10, "y": 10, "width": 0, "height": 20, "lines": []}]
    ctx = Context(img_rgb=original, img_inpainted=np.full_like(original, 255), text_regions=[region])

    output = asyncio.run(render_page(ctx, Config(render=RenderConfig(renderer=Renderer.default)), get_default_eng_font()))

    assert region.review_required and region._render_suppressed
    np.testing.assert_array_equal(output[10:40, 10:70], original[10:40, 10:70])


def test_unchanged_filtered_translation_restores_source_without_redrawing():
    original = np.full((80, 100, 3), 255, np.uint8)
    original[10:40, 10:70] = 0
    region = _region("PAL", "PAL", "unchanged")
    region.review_required = True
    region.review_reason = "Translation identical to original"
    ctx = Context(img_rgb=original, img_inpainted=np.full_like(original, 255), text_regions=[region])

    output = asyncio.run(render_page(ctx, Config(render=RenderConfig(renderer=Renderer.default)), get_default_eng_font()))

    assert region._render_suppressed
    np.testing.assert_array_equal(output[10:40, 10:70], original[10:40, 10:70])


def test_failed_frozen_compositing_restores_source_and_requests_review(monkeypatch):
    original = np.full((80, 100, 3), 255, np.uint8)
    original[10:40, 10:70] = 0
    region = _region("原文", "VISIBLE TRANSLATION", "failed-composite")
    region._layout_frozen = True
    region.layout_segments = [{
        "x": 10, "y": 10, "width": 60, "height": 30, "font_size": 24,
        "lines": [{"text": "VISIBLE", "x": 10, "y": 10, "width": 50, "height": 24}],
    }]
    ctx = Context(img_rgb=original, img_inpainted=np.full_like(original, 255), text_regions=[region])
    monkeypatch.setattr("manga_translator.rendering._composite_box_to_image", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad transform")))

    output = asyncio.run(render_page(ctx, Config(render=RenderConfig(renderer=Renderer.default)), get_default_eng_font()))

    assert region.review_required and region._render_suppressed
    assert region._render_failure_reason == "ValueError: bad transform"
    np.testing.assert_array_equal(output[10:40, 10:70], original[10:40, 10:70])


def test_explicit_newlines_survive_free_text_tokenization():
    from manga_translator.rendering.layout.free_text_typography import _free_text_words
    from manga_translator.rendering.layout.text_normalization import split_layout_words

    words = _free_text_words("FIRST PARAGRAPH\nSECOND PARAGRAPH")

    assert words == ["FIRST", "PARAGRAPH", "\0", "SECOND", "PARAGRAPH"]
    assert split_layout_words("FIRST\n\nSECOND") == ["FIRST", "\0", "\0", "SECOND"]


def test_bubble_and_free_text_wrappers_keep_forced_newlines():
    from manga_translator.rendering.layout.geometry import BubbleGeometry
    from manga_translator.rendering.layout.solve_core import solve_layout
    from manga_translator.rendering.layout.text_normalization import split_layout_words

    text_render.set_font(get_default_eng_font())
    region = _region("原文", "FIRST\nTWO", "paragraphs")
    profile = OriginalLayoutProfile(
        font_size=24, line_count=1, lines=[], centroid=(60, 40),
        bbox=(10, 10, 170, 70), block_width=160, block_height=60, occupancy=0.5,
    )
    free = _free_text_typography_candidates(
        region.translation, profile, Config(render=RenderConfig(font_size_minimum=8)), (120, 200)
    )
    assert free and all(any(line.text.startswith("FIRST") for line in item.lines) for item in free)
    assert all(any(line.text.startswith("TWO") for line in item.lines) for item in free)
    assert all(next(line.y for line in item.lines if line.text.startswith("TWO")) >
               next(line.y for line in item.lines if line.text.startswith("FIRST")) for item in free)

    bubble = solve_layout(
        BubbleGeometry(np.ones((120, 200), np.uint8)),
        split_layout_words(region.translation),
        font_size_max=20, font_size_min=20, margin=0, max_y_origin_trials=1,
    )
    assert bubble and [line.text for line in bubble.lines] == ["FIRST", "TWO"]


def test_missing_glyphs_are_reported_before_rendering(monkeypatch):
    original = np.full((80, 100, 3), 255, np.uint8)
    original[10:40, 10:70] = 0
    region = _region("原文", "UNSUPPORTED Ж", "missing-glyph")
    ctx = Context(img_rgb=original, img_inpainted=np.full_like(original, 255), text_regions=[region])
    monkeypatch.setattr(text_render, "missing_glyphs", lambda _text: ["Ж"])

    output = asyncio.run(render_page(ctx, Config(render=RenderConfig(renderer=Renderer.default)), get_default_eng_font()))

    assert region._render_suppressed and region.review_required
    assert region.review_reason == "missing_glyph: U+0416"
    np.testing.assert_array_equal(output[10:40, 10:70], original[10:40, 10:70])


def test_owned_untranslated_group_members_are_not_restored_over_inpainting():
    original = np.full((80, 120, 3), 255, np.uint8)
    original[15:35, 15:35] = 0
    original[15:35, 45:65] = 0
    owner = _region("結合", "GROUPED", "group")
    owner.source_region_ids = ["source-a", "source-b"]
    first = _region("日", region_id="source-a")
    second = _region("本", region_id="source-b")
    first.review_required = second.review_required = True
    first.lines = np.array([[[15, 15], [35, 15], [35, 35], [15, 35]]])
    second.lines = np.array([[[45, 15], [65, 15], [65, 35], [45, 35]]])
    ctx = Context(
        img_rgb=original,
        img_inpainted=np.full_like(original, 255),
        text_regions=[owner, first, second],
    )

    output = asyncio.run(render_page(
        ctx, Config(render=RenderConfig(renderer=Renderer.none)), get_default_eng_font()
    ))

    assert np.all(output[15:35, 15:65] == 255)


def test_invalid_tiny_source_font_uses_geometry_and_page_floor():
    region = _region("面接", "OH, AN INTERVIEW?", font_size=5)
    region.lines = np.array([[[10, 10], [30, 10], [30, 90], [10, 90]]])
    region.source_font_size = 5
    policy = build_region_font_policy(
        region=region,
        adaptive_target=18,
        page_baseline=24,
        minimum=4,
        render_config=SimpleNamespace(font_size=None, font_size_offset=0),
    )
    profile = build_original_layout_profile(region, np.ones((100, 80), np.uint8))

    assert policy.preferred_size >= 17
    assert profile.font_size >= 17
