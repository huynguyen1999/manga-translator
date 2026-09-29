from unittest.mock import patch

import numpy as np
import pytest

from manga_translator.config import Config
from manga_translator.rendering.layout.engine import layout_page
from manga_translator.utils import Context


@pytest.mark.parametrize(
    ("detection_done", "expected_inference"),
    [(True, False), (False, True)],
)
def test_layout_respects_empty_bubble_detection_state(detection_done, expected_inference):
    ctx = Context(
        img_rgb=np.zeros((20, 20, 3), dtype=np.uint8),
        text_regions=[],
        bubble_detections=[],
    )
    ctx._bubble_detection_done = detection_done

    with patch("manga_translator.rendering.layout.engine.apply_shape_aware_bubble_layout") as apply_layout:
        layout_page(ctx, Config(), font_path="unused-font")

    assert apply_layout.call_args.kwargs["infer_bubbles"] is expected_inference
