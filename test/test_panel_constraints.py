import cv2
import numpy as np

from manga_translator.config import Config
from manga_translator.detection.panel import PanelDetection
from manga_translator.geometry.panels import infer_panel_constraints
from manga_translator.rendering.layout.models import (
    FreeTextZone,
    OriginalLayoutProfile,
    PageObstacleMap,
    PanelConstraint,
    PlacementMode,
)
from manga_translator.rendering.layout.obstacles import build_page_obstacle_map
from manga_translator.rendering.layout.obstacles import build_free_text_ownership_zones as build_zones_with_panels
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


def test_saved_panel_association_prefers_smaller_enclosing_panel():
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    region = _region((45, 45, 55, 55))
    panels = [
        PanelDetection([0, 0, 100, 100], [], 0.99, order_index=1),
        PanelDetection([40, 40, 60, 60], [], 0.50, order_index=2),
    ]

    constraint = infer_panel_constraints(image, [region], panel_detections=panels)[id(region)]

    assert constraint.source == "ml"
    assert constraint.bounds == (40, 40, 60, 60)


def test_saved_panel_association_prefers_greater_source_coverage():
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    region = _region((40, 40, 60, 60))
    panels = [
        PanelDetection([39, 39, 56, 65], [], 0.99, order_index=1),
        PanelDetection([43, 39, 61, 65], [], 0.10, order_index=2),
    ]

    constraint = infer_panel_constraints(image, [region], panel_detections=panels)[id(region)]

    assert constraint.bounds == (43, 39, 61, 65)


def test_centroid_fallback_accepts_rotated_source_when_most_pixels_fit():
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    region = TextBlock(
        lines=[[[0, 50], [50, 0], [100, 50], [50, 100]]],
        texts=["rotated"],
        font_size=14,
    )
    panel = PanelDetection([0, 30, 100, 70], [], 0.9, order_index=1)

    constraint = infer_panel_constraints(image, [region], panel_detections=[panel])[id(region)]

    assert constraint.source == "ml"
    assert constraint.bounds == (0, 30, 100, 70)


def test_saved_panel_without_source_overlap_falls_back_to_cv():
    image = _page_with_two_by_two_panels()
    region = _region((215, 50, 235, 75))
    panel = PanelDetection([0, 200, 100, 290], [], 0.99, order_index=1)

    constraint = infer_panel_constraints(image, [region], panel_detections=[panel])[id(region)]

    assert constraint.source == "cv"
    assert constraint.bounds[0] > 150


def test_ownership_zone_receives_saved_ml_panel_constraint():
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    region = _region((45, 45, 55, 55))
    region.placement_mode = PlacementMode.FREE_TEXT
    obstacles = build_page_obstacle_map([region], image.shape[:2])
    panel = PanelDetection([30, 30, 70, 70], [], 0.9, order_index=3)

    zones = build_zones_with_panels(
        [region], obstacles, image=image, panel_detections=[panel]
    )

    assert zones[id(region)].panel_constraint.source == "ml"
    assert zones[id(region)].panel_constraint.bounds == (30, 30, 70, 70)


def test_too_small_source_footprint_does_not_force_unreadable_auto_font():
    region = _region((95, 60, 130, 78), "THESE PEOPLE ARE LIKELY THE CASTE TOP GUARDS")
    profile = build_original_layout_profile(region, np.ones((180, 220), dtype=np.uint8))
    panel = PanelConstraint("left", (0, 0, 150, 180), confidence=0.9, source="cv", margin=4)

    candidates = _free_text_typography_candidates(
        region.translation, profile, Config(), (180, 220), panel_constraint=panel
    )

    assert candidates
    assert min(candidate.font_size for candidate in candidates) >= 2


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


def test_panel_search_stops_at_readable_font_tiers():
    profile = OriginalLayoutProfile(55, 1, [], (783, 532), (693, 398, 873, 666), 180, 268, 0.2)
    panel = PanelConstraint("strip", (0, 427, 1280, 627), confidence=0.9, source="cv", margin=12)

    candidates = _free_text_typography_candidates(
        "THERE'S NO MAN IN THE WORLD WHO WOULD REFUSE...", profile, Config(), (1808, 1280), panel_constraint=panel
    )

    sizes = {candidate.font_size for candidate in candidates}
    assert candidates[0].font_size == profile.font_size
    assert sizes == set(range(11, 56))

    unconstrained = _free_text_typography_candidates(
        "THERE'S NO MAN IN THE WORLD WHO WOULD REFUSE...", profile, Config(), (1808, 1280)
    )
    assert min(candidate.font_size for candidate in unconstrained) == 11
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


def test_unreadable_panel_layout_is_suppressed_for_review():
    image = np.full((240, 300, 3), 255, dtype=np.uint8)
    cv2.line(image, (150, 0), (150, 239), (0, 0, 0), 3)
    region = _region((118, 95, 140, 112), "THESE PEOPLE ARE LIKELY THE CASTE TOP GUARDS")
    inpaint_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillPoly(inpaint_mask, [np.asarray(region.lines[0], dtype=np.int32)], 255)
    ctx = Context(img_rgb=image, text_regions=[region], inpaint_mask=inpaint_mask)
    config = Config()
    config.render.font_size_minimum = 8

    apply_shape_aware_bubble_layout(
        ctx, config, font_path=get_default_eng_font(), solver_max_y_trials=8,
    )

    constraint = region._panel_constraint
    assert constraint.source == "cv"
    assert constraint.bounds[2] < 150
    assert region._render_suppressed
    assert region.review_required
    assert region._solver_status == "no_valid_layout"


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
    assert 11 <= region.font_size <= 55
    assert region._solver_qa["selected_tier"] == f"shrink_{region.font_size}"
    segment = region.layout_segments[0]
    assert segment["height"] <= 268 * 1.5
    assert " ".join(line["text"] for line in segment["lines"]) == text
