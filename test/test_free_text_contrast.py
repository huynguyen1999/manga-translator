import asyncio

import numpy as np

from manga_translator.config import Config
from manga_translator.rendering import get_default_eng_font, render_page, text_render
from manga_translator.rendering.layout.contrast import apply_free_text_contrast
from manga_translator.rendering.layout.frozen import hydrate_layout, serialize_frozen_layout
from manga_translator.rendering.layout.models import FreeTextZone, PageObstacleMap, PlacementMode
from manga_translator.utils import Context, TextBlock


def _case(background=0):
    shape = (100, 140)
    image = np.full((*shape, 3), background, dtype=np.uint8)
    region = TextBlock(
        lines=[[[45, 35], [65, 35], [65, 65], [45, 65]]],
        texts=["原文"], translation="TEST", font_size=20,
        target_lang="ENG", region_id="contrast-region",
    )
    region.fg_colors = [0, 0, 0]
    region.bg_colors = [255, 255, 255]
    region.placement_mode = PlacementMode.FREE_TEXT
    region.layout_segments = [{
        "x": 40, "y": 40, "width": 70, "height": 25, "font_size": 20,
        "lines": [{"text": "TEST", "x": 40, "y": 40, "width": 70, "height": 20}],
    }]
    region._solver_qa = {"layout_stroke_width": 1}
    empty = np.zeros(shape, dtype=np.uint8)
    domain = np.ones(shape, dtype=np.uint8)
    region._free_text_zone = FreeTextZone(
        source_bbox=(45, 35, 65, 65), ownership_mask=domain,
        obstacle_mask=empty, coverage_target_mask=empty,
        coverable_damage_mask=empty, coverage_weight_map=np.ones(shape, np.float32),
        core_damage_mask=empty, placement_domain_mask=domain,
    )
    region._free_text_source_mask = empty
    obstacles = PageObstacleMap(
        bubble_mask=empty, protected_bubble_mask=empty,
        text_mask=empty, panel_mask=domain,
    )
    return Context(img_rgb=image, text_regions=[region]), region, obstacles


def test_dark_free_text_uses_white_character_margin_and_survives_freeze():
    text_render.set_font(get_default_eng_font())
    ctx, region, obstacles = _case()
    apply_free_text_contrast(ctx, [region], [region], obstacles, np.zeros(ctx.img_rgb.shape[:2], np.uint8))
    assert region._solver_qa["contrast_treatment"] == "white_margin"
    assert region._solver_qa["layout_stroke_width"] == 2
    assert region.fg_colors == [0, 0, 0]
    assert region.bg_colors == [255, 255, 255]

    document = serialize_frozen_layout(ctx, Config(), get_default_eng_font())
    restored = _case()[1]
    hydrate_layout(Context(img_rgb=ctx.img_rgb, text_regions=[restored]), document)
    assert restored._solver_qa["contrast_treatment"] == "white_margin"
    assert restored._solver_qa["layout_stroke_width"] == 2
    rendered = asyncio.run(render_page(
        Context(img_rgb=ctx.img_rgb, img_inpainted=ctx.img_rgb.copy(), text_regions=[restored]),
        Config(),
    ))
    assert rendered[37, 37, 0] == 0  # Background outside the glyphs stays untouched.


def test_light_background_stays_unchanged_and_unsafe_margin_is_not_used(monkeypatch):
    text_render.set_font(get_default_eng_font())
    ctx, region, obstacles = _case(background=255)
    apply_free_text_contrast(ctx, [region], [region], obstacles, None)
    assert "contrast_treatment" not in region._solver_qa

    from manga_translator.rendering.layout import free_text_search
    monkeypatch.setattr(free_text_search, "_free_text_hard_valid", lambda *_args: False)
    ctx, region, obstacles = _case()
    apply_free_text_contrast(ctx, [region], [region], obstacles, None)
    assert "contrast_treatment" not in region._solver_qa
    assert region._solver_qa["layout_stroke_width"] == 1


def test_cleaned_pixels_do_not_trigger_white_margin():
    text_render.set_font(get_default_eng_font())
    ctx, region, obstacles = _case()
    cleanup = np.ones(ctx.img_rgb.shape[:2], np.uint8)
    apply_free_text_contrast(ctx, [region], [region], obstacles, cleanup)
    assert "contrast_treatment" not in region._solver_qa
