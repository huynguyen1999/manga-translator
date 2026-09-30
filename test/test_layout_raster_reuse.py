from types import SimpleNamespace

import numpy as np
import pytest

from manga_translator.config import Config
from manga_translator.rendering import get_default_eng_font, text_render
from manga_translator.rendering.layout import candidate_footprint
from manga_translator.rendering.layout.models import LayoutCandidate, PlacedLine
from manga_translator.rendering.layout.profiling import reset_solver_profile


@pytest.mark.parametrize("size", [17, 65, 97, 241, 473])
def test_large_binary_scope_dilation_preserves_every_pixel(size):
    import cv2
    from manga_translator.rendering.layout.ownership import _dilate_binary_source
    source = np.zeros((280, 440), np.uint8)
    source[0:35, 0:82] = 1
    cv2.fillPoly(source, [np.array([[130, 42], [317, 72], [308, 213], [140, 205]])], 1)
    source[265:, 410:] = 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    np.testing.assert_array_equal(_dilate_binary_source(source, kernel), cv2.dilate(source, kernel))


def test_binary_scope_dilation_falls_back_for_ambiguous_counts(monkeypatch):
    import cv2
    from manga_translator.rendering.layout.ownership import _dilate_binary_source
    source = np.zeros((70, 90), np.uint8)
    source[20, 30] = 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (65, 65))
    expected = cv2.dilate(source, kernel)
    monkeypatch.setattr(cv2, "filter2D", lambda *args, **kwargs: np.full(source.shape, 0.5))
    np.testing.assert_array_equal(_dilate_binary_source(source, kernel), expected)


def _candidate(x=10, y=20, line_x=12):
    return LayoutCandidate(
        font_size=20, y_origin=y, line_spacing=0.0,
        lines=[PlacedLine("CACHE", y, line_x, 68, 20)],
        penalty=0.0, glyph_clearance_p5=0.0,
        layout_bounds=(x, y, x + 80, y + 30),
    )


class _Region:
    bg_colors = None
    direction = "hr"
    target_lang = "ENG"

    def __init__(self, fg=(0, 0, 0), bg=(255, 255, 255)):
        self.colors = (fg, bg)

    def get_font_colors(self):
        return self.colors


def _install_filter_stubs(monkeypatch, hard_valid_calls):
    from manga_translator.rendering import placement_geometry
    from manga_translator.rendering.layout import free_text_search, render_output_validation

    monkeypatch.setattr(
        placement_geometry,
        "_points_for_rect",
        lambda _region, bounds, _width, _height: np.asarray(
            [[bounds[0], bounds[1]], [bounds[2], bounds[1]],
             [bounds[2], bounds[3]], [bounds[0], bounds[3]]], dtype=np.float32,
        ),
    )

    def valid(*_args):
        hard_valid_calls.append(1)
        return True

    monkeypatch.setattr(free_text_search, "_free_text_hard_valid", valid)
    real_warp = render_output_validation._warped_alpha_crop
    warp_calls = []

    def tracked_warp(*args):
        warp_calls.append(1)
        return real_warp(*args)

    monkeypatch.setattr(render_output_validation, "_warped_alpha_crop", tracked_warp)
    return warp_calls


def test_cached_glyph_pixels_are_immutable_and_compositor_pixels_match():
    text_render.set_font(get_default_eng_font())
    text_render.get_char_glyph.cache_clear()
    glyph = text_render.get_char_glyph("G", 32, 0)
    same_glyph = text_render.get_char_glyph("G", 32, 0)

    assert glyph.bitmap.array is same_glyph.bitmap.array
    assert not glyph.bitmap.array.flags.writeable
    np.testing.assert_array_equal(
        glyph.bitmap.array,
        np.frombuffer(glyph.bitmap.buffer, dtype=np.uint8).reshape(glyph.bitmap.rows, glyph.bitmap.width),
    )

    reset_solver_profile()
    region = _Region()
    config = Config()
    candidate = _candidate()
    cached = candidate_footprint.get_candidate_render_box(region, candidate, config)
    candidate_copy = _candidate()
    profile = reset_solver_profile()
    fresh = candidate_footprint.get_candidate_render_box(region, candidate_copy, config)

    np.testing.assert_array_equal(cached, fresh)
    assert profile.compositor_cache_misses == 1


def test_validated_footprint_reuses_same_placement_and_rewarps_shifted_placement(monkeypatch):
    text_render.set_font(get_default_eng_font())
    profile = reset_solver_profile()
    valid_calls = []
    warp_calls = _install_filter_stubs(monkeypatch, valid_calls)
    zone = SimpleNamespace(rejections={})
    accepted = candidate_footprint.filter_renderable_candidates(
        _Region(), [_candidate()], Config(), (80, 120), zone, object(), np.zeros((80, 120), bool),
    )
    retained = candidate_footprint.filter_renderable_candidates(
        _Region(), [_candidate()], Config(), (80, 120), zone, object(), np.zeros((80, 120), bool),
    )

    assert len(accepted) == len(retained) == 1
    assert warp_calls == [1]
    assert len(valid_calls) == 2
    np.testing.assert_array_equal(accepted[0]._render_footprint[1], retained[0]._render_footprint[1])

    shifted = _candidate(x=18, y=23, line_x=20)
    shifted_result = candidate_footprint.filter_renderable_candidates(
        _Region(), [shifted], Config(), (80, 120), zone, object(), np.zeros((80, 120), bool),
    )
    assert len(shifted_result) == 1
    assert len(warp_calls) == 2
    assert accepted[0]._render_box is shifted_result[0]._render_box
    assert accepted[0]._render_footprint[0] != shifted_result[0]._render_footprint[0]
    assert profile.compositor_cache_hits == 2
    assert profile.compositor_cache_misses == 1


def test_relative_geometry_and_contrast_changes_miss_compositor_cache():
    text_render.set_font(get_default_eng_font())
    profile = reset_solver_profile()
    config = Config()
    baseline = candidate_footprint.get_candidate_render_box(_Region(), _candidate(), config)
    moved_glyph = candidate_footprint.get_candidate_render_box(
        _Region(), _candidate(line_x=13), config,
    )
    changed_contrast = candidate_footprint.get_candidate_render_box(
        _Region(fg=(0, 0, 200)), _candidate(), config,
    )

    assert profile.compositor_cache_misses == 3
    assert not np.array_equal(baseline, moved_glyph)
    assert not np.array_equal(baseline, changed_contrast)
