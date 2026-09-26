import cv2
import numpy as np

from manga_translator.config import Config
from manga_translator.geometry.panels import infer_panel_constraints
from manga_translator.rendering.layout.models import (
    FreeTextZone,
    OriginalLayoutProfile,
    PageObstacleMap,
    PanelConstraint,
    PlacementMode,
)
from manga_translator.rendering.layout.obstacles import build_page_obstacle_map
from manga_translator.rendering.layout.ownership import build_free_text_ownership_zones
from manga_translator.rendering.layout.solver import (
    _clamp_free_text_translation,
    _free_text_hard_valid,
    _free_text_typography_candidates,
    apply_shape_aware_bubble_layout,
    build_original_layout_profile,
)
from manga_translator.rendering import get_default_eng_font
from manga_translator.utils import Context, TextBlock


def _region(bounds, text="TRANSLATED TEXT"):
    x1, y1, x2, y2 = bounds
    return TextBlock(
        lines=[[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]],
        texts=["原文"],
        translation=text,
        target_lang="ENG",
        font_size=14,
    )


def _page_with_two_by_two_panels():
    image = np.full((300, 300, 3), 255, dtype=np.uint8)
    cv2.line(image, (150, 0), (150, 299), (0, 0, 0), 3)
    cv2.line(image, (0, 150), (299, 150), (0, 0, 0), 3)
    return image


def test_panel_inference_assigns_source_centroid_to_nearest_frame_cell():
    image = _page_with_two_by_two_panels()
    upper_right = _region((215, 50, 235, 75))
    lower_left = _region((40, 220, 60, 245))

    constraints = infer_panel_constraints(image, [upper_right, lower_left])

    first = constraints[id(upper_right)]
    second = constraints[id(lower_left)]
    assert first.source == second.source == "cv"
    assert first.confidence > 0.5 and second.confidence > 0.5
    assert first.bounds[0] > 150 and first.bounds[1] == 0
    assert first.bounds[2] == 300 and first.bounds[3] < 150
    assert second.bounds[0] == 0 and second.bounds[1] > 150
    assert second.bounds[2] < 150 and second.bounds[3] == 300


def test_ocr_box_crossing_panel_lines_uses_the_frame_around_its_center():
    image = np.full((300, 300, 3), 255, dtype=np.uint8)
    cv2.line(image, (0, 100), (299, 100), (0, 0, 0), 3)
    cv2.line(image, (0, 200), (299, 200), (0, 0, 0), 3)
    region = _region((120, 60, 180, 240))

    constraint = infer_panel_constraints(image, [region])[id(region)]

    assert constraint.source == "cv"
    assert 100 < constraint.bounds[1] < 110
    assert 190 < constraint.bounds[3] < 200


def test_ownership_zone_receives_its_panel_constraint():
    image = _page_with_two_by_two_panels()
    region = _region((215, 50, 235, 75))
    region.placement_mode = PlacementMode.FREE_TEXT
    obstacles = build_page_obstacle_map([region], image.shape[:2])

    zones = build_free_text_ownership_zones([region], obstacles, image=image)

    constraint = zones[id(region)].panel_constraint
    assert constraint is region._panel_constraint
    assert constraint.bounds[0] > 150
    assert constraint.source == "cv"


def test_typography_wraps_inside_panel_and_150_percent_source_height():
    region = _region((95, 60, 130, 78), "THESE PEOPLE ARE LIKELY THE CASTE TOP GUARDS")
    profile = build_original_layout_profile(region, np.ones((180, 220), dtype=np.uint8))
    panel = PanelConstraint("left", (0, 0, 150, 180), confidence=0.9, source="cv", margin=4)

    candidates = _free_text_typography_candidates(
        region.translation, profile, Config(), (180, 220), panel_constraint=panel
    )

    assert candidates
    assert all(candidate.font_size <= profile.font_size for candidate in candidates)
    assert all(max(line.width for line in candidate.lines) <= 142 for candidate in candidates)
    assert any(len(candidate.lines) > 1 for candidate in candidates)
    assert all(max(line.y + line.height for line in candidate.lines) <= profile.block_height * 1.5 for candidate in candidates)
    assert candidates[0].qa["panel_constraint"]["source"] == "cv"


def test_short_panel_rewraps_wider_before_reducing_source_font():
    region = _region((95, 60, 130, 260), "THESE PEOPLE ARE LIKELY THE CASTE TOP GUARDS")
    profile = build_original_layout_profile(region, np.ones((300, 500), dtype=np.uint8))
    panel = PanelConstraint("short", (0, 40, 430, 210), confidence=0.9, source="cv", margin=4)

    candidates = _free_text_typography_candidates(
        region.translation, profile, Config(), (300, 500), panel_constraint=panel
    )

    assert candidates
    assert any(candidate.font_size == profile.font_size for candidate in candidates)
    assert any(max(line.width for line in candidate.lines) > profile.block_width for candidate in candidates)
    assert all(max(line.y + line.height for line in candidate.lines) <= 162 for candidate in candidates)


def test_panel_search_keeps_intermediate_font_fallbacks():
    profile = OriginalLayoutProfile(55, 1, [], (783, 532), (693, 398, 873, 666), 180, 268, 0.2)
    panel = PanelConstraint("strip", (0, 427, 1280, 627), confidence=0.9, source="cv", margin=12)

    candidates = _free_text_typography_candidates(
        "THERE'S NO MAN IN THE WORLD WHO WOULD REFUSE...", profile, Config(), (1808, 1280), panel_constraint=panel
    )

    sizes = {candidate.font_size for candidate in candidates}
    assert candidates[0].font_size == profile.font_size
    assert 24 in sizes
    assert min(sizes) < 24

    unconstrained = _free_text_typography_candidates(
        "THERE'S NO MAN IN THE WORLD WHO WOULD REFUSE...", profile, Config(), (1808, 1280)
    )
    assert 24 in {candidate.font_size for candidate in unconstrained}
    short_profile = OriginalLayoutProfile(28, 1, [], (150, 50), (0, 0, 300, 100), 300, 100, 0.2)
    short_text = _free_text_typography_candidates("SHORT LINE", short_profile, Config(), (400, 400))
    assert short_text[0].font_size == short_profile.font_size


def test_hard_valid_checks_whole_paragraph_box_even_when_glyphs_fit():
    panel = PanelConstraint("right", (50, 0, 100, 100), confidence=0.9, source="cv", margin=2)
    zone = FreeTextZone(
        source_bbox=(60, 30, 70, 40),
        ownership_mask=np.ones((100, 120), dtype=np.uint8),
        obstacle_mask=np.zeros((100, 120), dtype=np.uint8),
        coverage_target_mask=np.zeros((100, 120), dtype=np.uint8),
        coverable_damage_mask=np.zeros((100, 120), dtype=np.uint8),
        coverage_weight_map=np.ones((100, 120), dtype=np.float32),
        core_damage_mask=np.zeros((100, 120), dtype=np.uint8),
        panel_constraint=panel,
    )
    obstacles = PageObstacleMap(
        bubble_mask=np.zeros((100, 120), dtype=np.uint8),
        protected_bubble_mask=np.zeros((100, 120), dtype=np.uint8),
        text_mask=np.zeros((100, 120), dtype=np.uint8),
        panel_mask=np.ones((100, 120), dtype=np.uint8),
    )
    visual = np.zeros((20, 20), dtype=bool)
    visual[5:12, 7:12] = True

    assert not _free_text_hard_valid(
        (48, 30, 68, 50), visual, zone, obstacles, obstacles.text_mask.astype(bool),
        block_bbox=(48, 30, 68, 50),
    )
    assert _free_text_hard_valid(
        (50, 30, 70, 50), visual, zone, obstacles, obstacles.text_mask.astype(bool),
        block_bbox=(54, 32, 80, 48),
    )


def test_panel_clamp_preserves_centroid_when_it_is_already_inside():
    panel = PanelConstraint("panel", (50, 20, 150, 120), confidence=0.9, source="cv", margin=5)

    assert _clamp_free_text_translation((20, 20, 60, 40), 45, 30, panel) == (45, 30)
    assert _clamp_free_text_translation((20, 20, 60, 40), -20, 30, panel) == (35, 30)
    assert _clamp_free_text_translation((20, 20, 180, 40), 0, 0, panel) is None


def test_free_text_solver_wraps_and_clamps_inside_detected_panel():
    image = np.full((240, 300, 3), 255, dtype=np.uint8)
    cv2.line(image, (150, 0), (150, 239), (0, 0, 0), 3)
    region = _region((118, 95, 140, 112), "THESE PEOPLE ARE LIKELY THE CASTE TOP GUARDS")
    inpaint_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillPoly(inpaint_mask, [np.asarray(region.lines[0], dtype=np.int32)], 255)
    source_profile = build_original_layout_profile(region, np.ones(image.shape[:2], dtype=np.uint8))
    ctx = Context(img_rgb=image, text_regions=[region], inpaint_mask=inpaint_mask)
    config = Config()
    config.render.font_size_minimum = 8
    source_font_size = region.font_size

    apply_shape_aware_bubble_layout(
        ctx, config, font_path=get_default_eng_font(), solver_max_y_trials=8,
    )

    constraint = region._panel_constraint
    assert constraint.source == "cv"
    assert constraint.bounds[2] < 150
    assert region._free_text_solver_applied
    assert config.render.font_size_minimum <= region.font_size <= source_font_size
    segment = region.layout_segments[0]
    assert segment["height"] <= source_profile.block_height * 1.5
    lines = segment["lines"]
    assert len(lines) > 1
    safe_left = constraint.bounds[0] + constraint.margin
    safe_top = constraint.bounds[1] + constraint.margin
    safe_right = constraint.bounds[2] - constraint.margin
    safe_bottom = constraint.bounds[3] - constraint.margin
    assert min(line["x"] for line in lines) >= safe_left
    assert min(line["y"] for line in lines) >= safe_top
    assert max(line["x"] + line["width"] for line in lines) <= safe_right
    assert max(line["y"] + line["height"] for line in lines) <= safe_bottom


def test_vertical_ocr_translation_uses_geometry_font_and_renders_as_free_text():
    image = np.full((1808, 1280, 3), 255, dtype=np.uint8)
    for x in range(715, 850, 24):
        cv2.rectangle(image, (x, 425), (x + 5, 640), (20, 20, 20), -1)
    text = "THERE'S NO MAN IN THE WORLD WHO WOULD REFUSE..."
    region = TextBlock(
        lines=[[[693, 398], [873, 398], [873, 666], [693, 666]]],
        texts=["そんな断る男とかいないだろ．．．"],
        translation=text,
        target_lang="ENG",
        font_size=180,
    )
    region.region_id = "744ed9b863f34be8b63f1aae6b0321e7"
    region.source_font_size = 180
    region.source_regions = [{
        "id": region.region_id,
        "bbox": [693, 398, 873, 666],
        "source_text": region.text,
    }]
    inpaint_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillPoly(inpaint_mask, [np.asarray(region.lines[0], dtype=np.int32)], 255)
    ctx = Context(img_rgb=image, text_regions=[region], inpaint_mask=inpaint_mask, mask=inpaint_mask)
    config = Config()
    config.render.font_size_minimum = 8

    apply_shape_aware_bubble_layout(
        ctx, config, font_path=get_default_eng_font(), solver_max_y_trials=8,
    )

    assert region._free_text_solver_applied
    assert region._render_suppressed is False
    assert region.font_size == 55
    segment = region.layout_segments[0]
    assert segment["height"] <= 268 * 1.5
    assert [line["text"] for line in segment["lines"]] == [
        "THERE'S NO", "MAN IN THE", "WORLD", "WHO WOULD", "REFUSE...",
    ]
