from types import SimpleNamespace

import numpy as np

from manga_translator.rendering.layout.free_text_constraints import _build_free_text_placement_domain
from manga_translator.rendering.layout.free_text_search import (
    _free_text_hard_valid,
    _free_text_ink_overflow_in_crop,
)
from manga_translator.rendering.layout.models import FreeTextZone, PageObstacleMap, PanelConstraint


def _region_and_masks(shape=(80, 80), source_box=(10, 24, 15, 29)):
    region = SimpleNamespace(font_size=10, source_font_size=10, lines=[])
    source = np.zeros(shape, dtype=np.uint8)
    x1, y1, x2, y2 = source_box
    source[y1:y2, x1:x2] = 1
    return region, source


def _obstacles(source, *, panel_mask=None, protected=None, text=None):
    shape = source.shape
    return PageObstacleMap(
        bubble_mask=np.zeros(shape, dtype=np.uint8),
        protected_bubble_mask=protected if protected is not None else np.zeros(shape, dtype=np.uint8),
        text_mask=text if text is not None else source.copy(),
        panel_mask=panel_mask if panel_mask is not None else np.ones(shape, dtype=np.uint8),
    )


def _zone(domain, panel=None):
    shape = domain.shape
    empty = np.zeros(shape, dtype=np.uint8)
    return FreeTextZone(
        source_bbox=(10, 24, 15, 29),
        ownership_mask=np.ones(shape, dtype=np.uint8),
        obstacle_mask=empty,
        coverage_target_mask=empty,
        coverable_damage_mask=empty,
        coverage_weight_map=np.ones(shape, dtype=np.float32),
        core_damage_mask=empty,
        panel_constraint=panel,
        placement_domain_mask=domain,
    )


def _candidate_visual(shape, point):
    visual = np.zeros(shape, dtype=bool)
    visual[point[1], point[0]] = True
    return visual


def test_domain_keeps_local_placement_and_excludes_text_bubble_clearance_and_panel_edge():
    region, source = _region_and_masks()
    text = source.copy()
    text[24:29, 22:24] = 1
    protected = np.zeros(source.shape, dtype=np.uint8)
    protected[18:32, 32:42] = 1
    panel = PanelConstraint("left", (4, 4, 60, 70), margin=4)
    obstacles = _obstacles(source, protected=protected, text=text)
    domain, qa = _build_free_text_placement_domain(region, source, source, obstacles, panel, distance=64)
    zone = _zone(domain, panel)

    local_box = (13, 24, 16, 27)
    assert _free_text_hard_valid(
        local_box, _candidate_visual((3, 3), (1, 1)), zone, obstacles, text.astype(bool) & ~source.astype(bool)
    )
    # A translated line may extend beyond its source's reach while its center stays local.
    zone.domain_center_only = True
    assert _free_text_hard_valid(
        (13, 24, 22, 27), _candidate_visual((3, 9), (8, 1)), zone, obstacles,
        text.astype(bool) & ~source.astype(bool),
    )
    zone.domain_center_only = False
    for point in ((20, 26), (34, 26), (56, 26)):
        box = (point[0] - 1, point[1] - 1, point[0] + 2, point[1] + 2)
        visual = _candidate_visual((3, 3), (1, 1))
        assert not _free_text_hard_valid(
            box, visual, zone, obstacles, text.astype(bool) & ~source.astype(bool)
        )

    assert qa["foreign_text_clearance_px"] == 2
    assert qa["geodesic_limit_px"] == 64
    assert qa["area_px"] == int(np.count_nonzero(domain))
    assert len(qa["bbox"]) == 4


def test_geodesic_cap_stops_nearby_target_behind_long_corridor():
    shape = (100, 80)
    region, source = _region_and_masks(shape, (7, 18, 12, 23))
    usable = np.ones(shape, dtype=np.uint8)
    usable[:61, 29:32] = 0  # The only route around the wall runs below y=60.
    obstacles = _obstacles(source, panel_mask=usable)
    panel = PanelConstraint("page", (0, 0, shape[1], shape[0]))
    domain, qa = _build_free_text_placement_domain(region, source, source, obstacles, panel, distance=64)
    zone = _zone(domain, panel)
    other_text = np.zeros(shape, dtype=bool)

    assert _free_text_hard_valid(
        (11, 18, 14, 21), _candidate_visual((3, 3), (1, 1)), zone, obstacles, other_text
    )
    # Euclidean separation is 40 px, but the only panel-safe path is over 80 px.
    far_box = (49, 18, 52, 21)
    far_visual = _candidate_visual((3, 3), (1, 1))
    assert not _free_text_hard_valid(far_box, far_visual, zone, obstacles, other_text)
    assert _free_text_ink_overflow_in_crop(
        far_box, far_visual, obstacles, other_text, placement_domain_mask=domain
    ) == 1.0
    assert qa["geodesic_limit_px"] == 64


def test_hard_constraint_rejection_order_and_labels_are_stable():
    shape = (10, 10)
    source = np.zeros(shape, dtype=np.uint8)
    crop = (2, 2, 4, 4)
    visual = np.ones((2, 2), dtype=bool)

    def rejected(reason, *, candidate_crop=crop, candidate_visual=visual, domain=None,
                 panel=None, protected=None, other=None, panel_mask=None):
        zone = _zone(np.ones(shape, dtype=np.uint8) if domain is None else domain, panel)
        obstacles = _obstacles(
            source,
            panel_mask=np.ones(shape, dtype=np.uint8) if panel_mask is None else panel_mask,
            protected=protected,
        )
        other_text = np.zeros(shape, dtype=bool) if other is None else other
        assert not _free_text_hard_valid(
            candidate_crop, candidate_visual, zone, obstacles, other_text,
        )
        assert zone.rejections == {reason: 1}

    rejected("page_bounds", candidate_crop=(-1, 2, 1, 4))
    rejected("empty_raster", candidate_visual=np.zeros((2, 2), dtype=bool))
    rejected("local_domain", domain=np.zeros(shape, dtype=np.uint8))
    rejected("panel_bounds", panel=PanelConstraint("panel", (0, 0, 10, 10), margin=3))

    panel_shape = np.ones(shape, dtype=np.uint8)
    panel_shape[2:4, 2:4] = 0
    rejected("panel_mask", panel=PanelConstraint("panel", (0, 0, 10, 10), mask=panel_shape))

    protected = np.zeros(shape, dtype=np.uint8)
    protected[2:4, 2:4] = 1
    rejected("protected_bubble", protected=protected)

    other = np.zeros(shape, dtype=bool)
    other[2:4, 2:4] = True
    rejected("other_text", other=other)

    page_panel = np.ones(shape, dtype=np.uint8)
    page_panel[2:4, 2:4] = 0
    rejected("panel_mask", panel_mask=page_panel)
