from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from manga_translator.config import Config
from manga_translator.rendering.layout.engine import layout_page
from manga_translator.rendering.layout.models import PlacementMode
from manga_translator.utils import Context


@pytest.mark.parametrize(
    ("state", "detections", "done", "geometry_suggests_bubble", "expected_inference", "expected_mode"),
    [
        (None, None, False, True, True, PlacementMode.BUBBLE),
        ("completed_empty", [], True, True, True, PlacementMode.BUBBLE),
        ("completed_with_results", [object()], True, False, False, PlacementMode.BUBBLE),
        ("failed", [], True, True, True, PlacementMode.BUBBLE),
        ("completed_empty", [], True, False, True, PlacementMode.FREE_TEXT),
    ],
    ids=["unknown", "completed-empty", "detected-bubble", "detector-failed", "panel-only"],
)
def test_layout_routes_bubble_detection_states(
    state, detections, done, geometry_suggests_bubble, expected_inference, expected_mode,
):
    shape = (20, 20)
    region = SimpleNamespace(
        region_id="region_0", lines=[], xyxy=[5, 5, 15, 15], center=[10, 10],
        text="source", translation="translation", layout_segments=[],
    )
    ctx = Context(img_rgb=np.zeros((*shape, 3), dtype=np.uint8), text_regions=[region])
    ctx.bubble_detections = detections
    ctx.panel_detections = [object()] if not geometry_suggests_bubble else []
    ctx._bubble_detection_done = done
    ctx.bubble_detection_state = state
    ctx._geometry_suggests_bubble = geometry_suggests_bubble

    def apply_layout(context, _config, **kwargs):
        assert kwargs["infer_bubbles"] is expected_inference
        if context.bubble_detections:
            context.text_regions[0]._bubble_mask = np.ones(shape, dtype=np.uint8)
        elif kwargs["infer_bubbles"] and context._geometry_suggests_bubble:
            context.text_regions[0]._bubble_interior = np.ones(shape, dtype=np.uint8)

    with patch("manga_translator.rendering.layout.solver.apply_shape_aware_bubble_layout", apply_layout):
        layout_page(ctx, Config(), font_path="unused-font")

    assert region.placement_mode is expected_mode
    assert ctx.layout.regions["region_0"].placement_mode is expected_mode
