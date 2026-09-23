import cv2
import numpy as np
from types import SimpleNamespace

from manga_translator.rendering.layout import (
    BubbleGeometry,
    PlacementMode,
    build_lobe_graph,
    build_free_text_ownership_zones,
    build_page_obstacle_map,
    classify_placement_modes,
    layout_page,
)
from manga_translator.rendering.layout.models import LayoutCandidate, PlacedLine
from manga_translator.rendering.layout.solver import _candidate_data, _cropped_masks_overlap
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
    classify_placement_modes(ctx.text_regions)
    zones = build_free_text_ownership_zones(
        ctx.text_regions, build_page_obstacle_map(ctx.text_regions, image.shape[:2]), ctx.mask
    )

    assert set(result.regions) == {"first", "second"}
    assert all(region.placement_mode is PlacementMode.FREE_TEXT for region in ctx.text_regions)
    assert ctx._free_text_layout_debug is None
    assert not hasattr(first, "_free_text_glyph_mask")
    assert not np.any(zones[id(first)].ownership_mask & zones[id(second)].ownership_mask)


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
