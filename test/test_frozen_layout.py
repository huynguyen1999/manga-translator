import asyncio
from copy import deepcopy

import cv2
import numpy as np
import pytest

from manga_translator.config import BubbleDetectionConfig, Config, RenderConfig
from manga_translator.detection.bubble import (
    BubbleDetection,
    deserialize_bubble_detections,
    serialize_bubble_detections,
)
from manga_translator.pipeline.run import deserialize_textblocks, serialize_regions
from manga_translator.rendering import get_default_eng_font, render_page
from manga_translator.rendering.bubble_layout import group_regions_by_bubbles, restore_bubble_assignments
from manga_translator.rendering.layout import layout_page
from manga_translator.rendering.layout.frozen import (
    LAYOUT_ALGORITHM_REVISION,
    hydrate_layout,
    layout_input_fingerprints,
    serialize_frozen_layout,
)
from manga_translator.pipeline.stages import fingerprint
from manga_translator.rendering.layout.solver import _shared_bubble_groups
from manga_translator.utils import Context, TextBlock


def _region(text="THIS MEAT IS DELICIOUS!", translation=None, region_id="region-a"):
    return TextBlock(
        lines=np.array([[[90, 105], [230, 105], [230, 150], [90, 150]]], dtype=np.int32),
        texts=[text],
        translation=translation or text,
        font_size=44,
        target_lang="ENG",
        region_id=region_id,
    )


def test_frozen_layout_survives_context_rebuild_without_reflow(monkeypatch):
    image = np.full((300, 320, 3), 255, dtype=np.uint8)
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.ellipse(mask, (160, 130), (105, 72), 0, 0, 360, 255, -1)
    region = _region()
    region._bubble_mask = mask
    ctx = Context(img_rgb=image.copy(), img_inpainted=image.copy(), text_regions=[region])
    config = Config(
        render=RenderConfig(font_size_minimum=0, font_size=None, line_spacing=0),
        bubble_detection=BubbleDetectionConfig(enabled=True, padding=9),
    )
    font_path = get_default_eng_font()
    translation_checkpoint = serialize_regions(ctx.text_regions)

    layout_page(ctx, config, font_path)
    region._hyphenation_diagnostics = {
        "calibrated_target": 32,
        "normal_font_size": 23,
        "final_font_size": 30,
        "font_ratio_before_rescue": 0.7188,
        "font_ratio_after_rescue": 0.9375,
        "hyphenation_rescue_selected": True,
        "introduced_hyphen_count": 1,
        "introduced_hyphen_words": ["UNCHARACTERISTICALLY"],
    }
    bubble_doc = []
    layout_doc = serialize_frozen_layout(ctx, config, font_path, bubble_doc)
    assert layout_doc["regions"][0]["hyphenation"] == region._hyphenation_diagnostics
    uninterrupted = asyncio.run(render_page(ctx, config, font_path))

    restored_region = deserialize_textblocks(translation_checkpoint)[0]
    resumed = Context(img_rgb=image.copy(), img_inpainted=image.copy(), text_regions=[restored_region])
    inputs = layout_input_fingerprints(
        resumed.text_regions, config, font_path, bubble_doc, image.shape
    )
    hydrate_layout(resumed, layout_doc, inputs["fingerprint"])
    assert restored_region._hyphenation_diagnostics == region._hyphenation_diagnostics
    assert not hasattr(restored_region, "_bubble_box")

    def fail_if_reflow(*_args, **_kwargs):
        raise AssertionError("rendering must consume the frozen line placements")

    monkeypatch.setattr("manga_translator.rendering._expand_horizontal_region", fail_if_reflow)
    resumed_output = asyncio.run(render_page(resumed, config, font_path))
    np.testing.assert_array_equal(uninterrupted, resumed_output)
    assert [line["text"] for line in restored_region.layout_segments[0]["lines"]] == [
        line["text"] for line in region.layout_segments[0]["lines"]
    ]
    assert all("-" not in line["text"] for line in region.layout_segments[0]["lines"])

    restored_region.translation = "CHANGED AFTER LAYOUT"
    changed_inputs = layout_input_fingerprints(
        resumed.text_regions, config, font_path, bubble_doc, image.shape
    )
    with pytest.raises(ValueError, match="stale"):
        hydrate_layout(resumed, layout_doc, changed_inputs["fingerprint"])


def test_hydration_keeps_source_font_size_for_repeat_fingerprint():
    image = np.full((300, 320, 3), 255, dtype=np.uint8)
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.ellipse(mask, (160, 130), (105, 72), 0, 0, 360, 255, -1)
    region = _region()
    region._bubble_mask = mask
    config = Config(
        render=RenderConfig(font_size_minimum=0, font_size=None, line_spacing=0),
        bubble_detection=BubbleDetectionConfig(enabled=True, padding=9),
    )
    font_path = get_default_eng_font()
    translations = serialize_regions([region])

    layout_ctx = Context(img_rgb=image.copy(), img_inpainted=image.copy(), text_regions=[region])
    layout_page(layout_ctx, config, font_path)
    layout_doc = serialize_frozen_layout(layout_ctx, config, font_path, [])
    assert layout_doc["regions"][0]["source_font_size"] == 44
    # A render retry can encounter a v2 artifact created before source_font_size was persisted.
    layout_doc["regions"][0].pop("source_font_size")

    restored_region = deserialize_textblocks(translations)[0]
    render_ctx = Context(img_rgb=image.copy(), img_inpainted=image.copy(), text_regions=[restored_region])
    inputs = layout_input_fingerprints(
        render_ctx.text_regions, config, font_path, [], image.shape
    )
    hydrate_layout(render_ctx, layout_doc, inputs["fingerprint"])

    repeated_inputs = layout_input_fingerprints(
        render_ctx.text_regions, config, font_path, [], image.shape
    )
    assert repeated_inputs["fingerprint"] == layout_doc["input_fingerprint"]
    hydrate_layout(render_ctx, layout_doc, repeated_inputs["fingerprint"])


def test_layout_algorithm_revision_invalidates_cached_layout():
    image = np.full((300, 320, 3), 255, dtype=np.uint8)
    region = _region()
    config = Config(render=RenderConfig(font_size_minimum=0))
    font_path = get_default_eng_font()
    ctx = Context(img_rgb=image, text_regions=[region])
    layout_doc = serialize_frozen_layout(ctx, config, font_path, [])
    stale_doc = deepcopy(layout_doc)
    stale_inputs = dict(stale_doc["input_fingerprints"])
    assert stale_inputs.pop("algorithm") == LAYOUT_ALGORITHM_REVISION
    stale_doc["input_fingerprint"] = fingerprint(stale_inputs)

    current = layout_input_fingerprints([region], config, font_path, [], image.shape)
    with pytest.raises(ValueError, match="stale"):
        hydrate_layout(ctx, stale_doc, current["fingerprint"])


def test_fingerprint_normalizes_region_provenance_before_layout_retry():
    image = np.full((300, 320, 3), 255, dtype=np.uint8)
    region = TextBlock(
        lines=np.array([[[90, 105], [230, 105], [230, 150], [90, 150]]], dtype=np.int32),
        texts=["THIS MEAT IS DELICIOUS!"],
        translation="THIS MEAT IS DELICIOUS!",
        font_size=44,
        target_lang="ENG",
    )
    # Text-line merging assigns region_id after TextBlock construction, leaving provenance empty.
    region.region_id = "region-a"
    config = Config(render=RenderConfig(font_size_minimum=0, font_size=None, line_spacing=0))
    font_path = get_default_eng_font()
    translations = serialize_regions([region])

    layout_region = deserialize_textblocks(translations)[0]
    layout_ctx = Context(img_rgb=image.copy(), img_inpainted=image.copy(), text_regions=[layout_region])
    layout_page(layout_ctx, config, font_path)
    layout_doc = serialize_frozen_layout(layout_ctx, config, font_path, [])

    render_region = deserialize_textblocks(translations)[0]
    inputs = layout_input_fingerprints([render_region], config, font_path, [], image.shape)
    assert inputs["fingerprint"] == layout_doc["input_fingerprint"]


def test_unsolved_legacy_fallback_is_painted_from_saved_pixels():
    image = np.full((220, 240, 3), 255, dtype=np.uint8)
    region = _region("test", "KEEP THIS LINE", "fallback-region")
    region.layout_bounds = [100, 100, 160, 130]
    region._bubble_box = np.zeros((30, 60, 4), dtype=np.uint8)
    cv2.putText(region._bubble_box, "TEST", (3, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0, 255), 1)
    region._bubble_points = np.array([[[100, 100], [160, 100], [160, 130], [100, 130]]], dtype=np.int64)
    ctx = Context(img_rgb=image.copy(), img_inpainted=image.copy(), text_regions=[region])
    config = Config(render=RenderConfig(font_size_minimum=0))
    font_path = get_default_eng_font()
    translations = serialize_regions(ctx.text_regions)

    layout_doc = serialize_frozen_layout(ctx, config, font_path)
    assert layout_doc["regions"][0]["segments"][0]["rendered_png"]
    uninterrupted = asyncio.run(render_page(ctx, config, font_path))

    restored = deserialize_textblocks(translations)[0]
    resumed = Context(img_rgb=image.copy(), img_inpainted=image.copy(), text_regions=[restored])
    inputs = layout_input_fingerprints(resumed.text_regions, config, font_path, image_shape=image.shape)
    hydrate_layout(resumed, layout_doc, inputs["fingerprint"])
    resumed_output = asyncio.run(render_page(resumed, config, font_path))
    np.testing.assert_array_equal(uninterrupted, resumed_output)


def test_bubble_identity_survives_region_checkpoint():
    mask = np.zeros((240, 320), dtype=np.uint8)
    cv2.ellipse(mask, (160, 120), (130, 95), 0, 0, 360, 255, -1)
    regions = [
        _region("上の台詞", "WE WON'T BE OF MUCH USE TO YOU.", "region-a"),
        _region("下の台詞", "MY WIFE WILL HANDLE IT.", "region-b"),
    ]
    detections = [BubbleDetection(mask=mask, confidence=0.9)]
    associated = group_regions_by_bubbles(regions, detections, group=False)
    detection_doc = serialize_bubble_detections(detections)
    restored = deserialize_textblocks(serialize_regions(associated))
    restored_detections = deserialize_bubble_detections(detection_doc, mask.shape)
    restore_bubble_assignments(restored, restored_detections)

    assert [region.bubble_id for region in restored] == ["bubble_0", "bubble_0"]
    assert len(_shared_bubble_groups(restored)) == 1
    assert len(_shared_bubble_groups(restored)[0].regions) == 2
