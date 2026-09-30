import json
from pathlib import Path

import numpy as np

from manga_translator.rendering import text_render
from manga_translator.rendering.layout.engine import _region_layout
from manga_translator.rendering.layout.models import PageLayoutResult, PlacementMode
from manga_translator.rendering.layout.validation import validate_layout
from manga_translator.utils import Context, TextBlock


def test_saved_491582cc_collision_remains_diagnostic_and_suppressed():
    saved = json.loads((Path(__file__).parent / "fixtures/layout_491582cc_collision.json").read_text())
    text_render.set_font("fonts/anime_ace.ttf")
    regions = []
    for item in saved:
        region = TextBlock(lines=np.array(item["source_lines"]), texts=[item["source_text"]],
                           translation=item["translation"], font_size=item["font_size"], target_lang="ENG")
        region.region_id = item["region_id"]
        region.source_region_ids = [region.region_id]
        region.layout_segments = item["segments"]
        region.layout_bounds = item["layout_bounds"]
        region._solver_qa = {"layout_stroke_width": item["layout_stroke_width"]}
        region.placement_mode = PlacementMode.FREE_TEXT
        regions.append(region)
    result = PageLayoutResult(regions={region.region_id: _region_layout(region, "fonts/anime_ace.ttf")
                                      for region in regions})
    ctx = Context(img_rgb=np.full((1791, 1280, 3), 255, np.uint8), text_regions=regions)
    diagnostics = validate_layout(ctx, result)
    assert any("collision" in error for error in diagnostics.errors)
    assert regions[1]._render_suppressed and regions[1].review_required
