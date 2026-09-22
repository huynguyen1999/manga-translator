import unittest
from types import SimpleNamespace

import cv2
import numpy as np

from manga_translator.utils import TextBlock
from manga_translator.rendering.bubble_layout import (
    build_line_slots,
    constrain_mask,
    decode_safe_shape,
    encode_safe_shape,
    group_regions_by_bubbles,
    prepare_bubble_masks,
    prepare_bubbles,
    restore_original,
)

CONFIG = SimpleNamespace(font_size_minimum=12, font_size=20, font_size_offset=0,
                         no_hyphenation=False, line_spacing=0)
FONT = "fonts/comic shanns 2.ttf"


def block(x, text="Hello", y=70, h=100):
    return TextBlock([[[x, y], [x+20, y], [x+20, y+h], [x, y+h]]],
                     texts=["source"], translation=text, font_size=20,
                     target_lang="ENG", fg_color=(0, 0, 0), bg_color=(255, 255, 255))


def page():
    image = np.full((260, 300, 3), 80, np.uint8)
    cv2.ellipse(image, (150, 125), (95, 110), 0, 0, 360, (255, 255, 255), -1)
    for x in (120, 160):
        for y in (80, 110, 140):
            cv2.rectangle(image, (x+3, y), (x+12, y+12), (0, 0, 0), -1)
    return image


class BubbleLayoutTests(unittest.TestCase):
    def test_single_stepped_bubble_uses_full_variable_width_interior(self):
        image = np.full((400, 624, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        # One connected speech bubble: a broad lower body with a raised right arm.
        cv2.rectangle(mask, (88, 169), (217, 350), 255, -1)
        cv2.rectangle(mask, (193, 89), (307, 289), 255, -1)
        cv2.ellipse(mask, (153, 285), (65, 81), 0, 0, 360, 255, -1)
        region = TextBlock(
            lines=[
                [[220, 105], [250, 105], [250, 260], [220, 260]],
                [[110, 185], [140, 185], [140, 340], [110, 340]],
            ],
            texts=["upper", "lower"],
            translation="Today, per the client's request, we're going on a drive for a pool date.",
            font_size=22,
            target_lang="ENG",
            fg_color=(0, 0, 0),
            bg_color=(255, 255, 255),
        )
        region._bubble_mask = mask

        prepared = prepare_bubbles(image, [region], FONT, CONFIG)[0]

        self.assertFalse(prepared.review_required, prepared.review_reason)
        self.assertEqual(len(prepared.layout_segments), 2)
        self.assertEqual(
            [segment["text"] for segment in prepared.layout_segments],
            ["Today, per the client's request,", "we're going on a drive for a pool date."],
        )
        self.assertLess(prepared.layout_segments[0]["y"], prepared.layout_segments[1]["y"])
        from manga_translator.rendering import text_render
        fills = []
        for segment in prepared._bubble_segments:
            x1, y1, x2, y2 = segment["bounds"]
            alpha = segment["box"][:, :, 3] > 0
            interior = prepared._bubble_interior[y1:y2, x1:x2] > 0
            self.assertFalse(np.any(alpha & ~interior))
            ys, xs = np.nonzero(alpha)
            self.assertLess(abs((ys.min() + ys.max()) / 2 + y1 - (y1 + y2) / 2), 12)
            center = float(np.median(xs) + x1)
            slots = build_line_slots(prepared._bubble_interior, y1, segment["font_size"], 0, y2,
                                     center, rect=segment["bounds"])
            words = segment["text"].split()
            advance = sum(text_render.get_string_width(segment["font_size"], word) for word in words)
            advance += (len(words) - 1) * text_render.get_string_width(segment["font_size"], " ")
            fills.append(advance / sum(slot["width"] for slot in slots))
        self.assertLess(abs(fills[0] - fills[1]), 0.12)

    def test_touching_bubbles_do_not_reverse_a_clause_or_split_a_broad_lobe(self):
        image = np.full((700, 600, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (420, 150), (140, 135), 0, 0, 360, 255, -1)
        cv2.ellipse(mask, (180, 350), (100, 120), 0, 0, 360, 255, -1)
        cv2.ellipse(mask, (180, 500), (100, 120), 0, 0, 360, 255, -1)
        cv2.rectangle(mask, (270, 220), (340, 290), 255, -1)
        cv2.rectangle(mask, (100, 350), (260, 500), 255, -1)

        # Japanese OCR order can disagree with top-to-bottom English flow.
        lines = np.array([
            [[400, 60], [440, 60], [440, 210], [400, 210]],
            [[160, 500], [200, 500], [200, 550], [160, 550]],
            [[160, 300], [200, 300], [200, 380], [160, 380]],
        ])
        region = TextBlock(
            lines,
            texts=["top", "lower-left", "lower-right"],
            translation="I was able to rent a Magic Mirror Truck, so I borrowed it for the day.",
            font_size=20,
            target_lang="ENG",
            fg_color=(0, 0, 0),
            bg_color=(255, 255, 255),
        )
        region._bubble_mask = mask

        prepared = prepare_bubbles(image, [region], FONT, CONFIG)[0]

        self.assertEqual(len(prepared.layout_segments), 2)
        self.assertEqual(
            " ".join(segment["text"] for segment in prepared.layout_segments),
            region.translation,
        )
        self.assertLess(prepared.layout_segments[0]["y"], prepared.layout_segments[1]["y"])

    def test_shared_bubble_and_complete_cleanup(self):
        image = page()
        groups = prepare_bubbles(image, [block(160, "First"), block(120, "Second")], FONT, CONFIG)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group.translation, "First\nSecond")
        self.assertFalse(group.review_required, group.review_reason)
        mask = constrain_mask(np.zeros(image.shape[:2], np.uint8), groups)
        self.assertEqual(mask[85, 165], 255)
        self.assertEqual(mask[85, 125], 255)
        self.assertEqual(mask[15, 150], 0)
        x1, y1, x2, y2 = map(int, group.layout_bounds)
        self.assertTrue(np.all(group._bubble_interior[y1:y2+1, x1:x2+1]))

    def test_missing_column_flags_review_and_prepares_render(self):
        image = page()
        groups = prepare_bubbles(image, [block(160)], FONT, CONFIG)
        self.assertEqual(groups[0].review_reason, "uncertain_cleanup")
        self.assertTrue(groups[0].review_required)
        self.assertIsNotNone(groups[0]._bubble_box)
        # When translation is empty, original is restored
        groups[0].translation = ""
        cleaned = image.copy()
        cleaned[groups[0]._bubble_interior > 0] = 255
        restored = restore_original(cleaned, image, groups)
        np.testing.assert_array_equal(restored, image)

    def test_suppressed_translation_restores_original_pixels(self):
        image = page()
        region = block(160, "Translated text remains available for review")
        region.review_required = True
        region.review_reason = "text_does_not_fit"
        region._render_suppressed = True
        cleaned = np.full_like(image, 255)

        restored = restore_original(cleaned, image, [region])

        selected = np.zeros(image.shape[:2], np.uint8)
        cv2.fillPoly(selected, [np.asarray(region.lines[0], np.int32)], 1)
        np.testing.assert_array_equal(restored[selected > 0], image[selected > 0])

    def test_overflow_flags_review_reason(self):
        groups = prepare_bubbles(page(), [block(160, "A long translation " * 200), block(120)], FONT, CONFIG)
        self.assertEqual(groups[0].review_reason, "text_does_not_fit")
        self.assertTrue(groups[0].review_required)
        self.assertIsNotNone(groups[0]._bubble_box)

    def test_open_background_is_not_merged(self):
        image = np.full((260, 300, 3), 255, np.uint8)
        groups = prepare_bubbles(image, [block(160), block(120)], FONT, CONFIG)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(not hasattr(r, "group_members") for r in groups))

    def test_supplied_dialogue_crops(self):
        from pathlib import Path
        from manga_translator.rendering import _composite_box_to_image
        fixtures = Path(__file__).parent / "fixtures" / "bubbles"
        for name, boxes in [
            ("three_columns.png", [(172,83,219,379), (125,83,167,340), (72,86,118,374)]),
            ("two_columns.png", [(137,61,185,344), (89,74,133,424)]),
        ]:
            with self.subTest(name=name):
                image = cv2.imread(str(fixtures / name))
                regions = [TextBlock([[[x,y],[x2,y],[x2,y2],[x,y2]]], texts=["source"],
                                     translation=f"Sentence {i+1}.", font_size=20, target_lang="ENG",
                                     fg_color=(0,0,0), bg_color=(255,255,255))
                           for i, (x,y,x2,y2) in enumerate(boxes)]
                groups = prepare_bubbles(image, regions, FONT, CONFIG)
                self.assertEqual(len(groups), 1)
                group = groups[0]
                self.assertFalse(group.review_required, group.review_reason)
                canvas = np.full_like(image, 255)
                rendered = _composite_box_to_image(canvas, group._bubble_box, group._bubble_points)
                lettering = np.any(rendered != 255, axis=2)
                self.assertTrue(lettering.any())
                self.assertFalse(np.any(lettering & (group._bubble_interior == 0)))
                missed = prepare_bubbles(image, regions[:-1], FONT, CONFIG)
                self.assertTrue(missed[0].review_required)

    def test_reviewed_region_restores_original_when_untranslated(self):
        import asyncio
        from manga_translator.manga_translator import MangaTranslator
        from manga_translator.config import Config
        from manga_translator.utils import Context
        image = page()
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.font_path = FONT
        translator._model_usage_timestamps = {}
        config = Config()
        config.render.font_size_minimum = 12
        region = block(160, "")
        region.review_required = True
        region.review_reason = "text_does_not_fit"
        region._bubble_restore = np.zeros(image.shape[:2], np.uint8)
        cv2.fillPoly(region._bubble_restore, [np.asarray(region.lines[0], np.int32)], 1)
        ctx = Context(img_rgb=image, img_inpainted=np.full_like(image, 255),
                      text_regions=[region], render_mask=None, _bubble_layout_ready=True)
        output = asyncio.run(translator._run_text_rendering(config, ctx))
        selected = region._bubble_restore > 0
        np.testing.assert_array_equal(output[selected], image[selected])

    def test_review_region_renders_translation_when_available(self):
        import asyncio
        from manga_translator.manga_translator import MangaTranslator
        from manga_translator.config import Config
        from manga_translator.utils import Context
        image = page()
        region = block(160, "Rendered anyway")
        region.review_required = True
        region.review_reason = "uncertain_cleanup"
        region._bubble_restore = np.zeros(image.shape[:2], np.uint8)
        cv2.fillPoly(region._bubble_restore, [np.asarray(region.lines[0], np.int32)], 1)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.fillPoly(mask, [np.asarray(region.lines[0], np.int32)], 255)
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.font_path = FONT
        translator._model_usage_timestamps = {}
        ctx = Context(img_rgb=image, img_inpainted=np.full_like(image, 255), mask=mask,
                      text_regions=[region], render_mask=None, _bubble_layout_ready=True)

        output = asyncio.run(translator._run_text_rendering(Config(), ctx))
        # Translated text should be rendered rather than left as source image
        self.assertTrue(np.any(output != image))

    def test_detector_result_is_attached_before_batch_translation(self):
        import asyncio
        from unittest.mock import patch
        from manga_translator.config import Config
        from manga_translator.detection.bubble import BubbleDetection
        from manga_translator.manga_translator import MangaTranslator
        from manga_translator.utils import Context

        image = page()
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (150, 125), (95, 110), 0, 0, 360, 255, -1)
        translator = MangaTranslator.__new__(MangaTranslator)
        translator._progress_hooks = []
        translator._pipeline_lab_run = None
        region = block(160)
        ctx = Context(img_rgb=image, text_regions=[region])
        config = Config(bubble_detection={'enabled': True})

        with patch(
            'manga_translator.manga_translator.detect_bubbles',
            return_value=[BubbleDetection(mask, 0.9)],
        ):
            asyncio.run(translator._detect_speech_bubbles(config, ctx))

        self.assertEqual(len(ctx.bubble_detections), 1)
        self.assertIs(ctx.text_regions[0]._bubble_mask, mask)

    def test_detector_bubble_places_english_from_vertical_ocr_region(self):
        image = page()
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (150, 125), (95, 110), 0, 0, 360, 255, -1)
        region = block(160, "Render this English text")
        region._bubble_mask = mask

        prepared = prepare_bubbles(image, [region], FONT, CONFIG)

        self.assertFalse(prepared[0].review_required, prepared[0].review_reason)
        self.assertTrue(np.any(prepared[0]._bubble_box[:, :, 3]))

    def test_connected_lobes_flow_one_translation_across_safe_segments(self):
        import asyncio
        from manga_translator.rendering import dispatch

        image = np.full((280, 360, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (105, 135), (72, 92), 0, 0, 360, 255, -1)
        cv2.ellipse(mask, (255, 135), (72, 92), 0, 0, 360, 255, -1)
        cv2.rectangle(mask, (172, 126), (188, 144), 255, -1)
        regions = [block(90, "Today, we are going for a drive.", 90),
                   block(245, "", 90)]
        for region in regions:
            region._bubble_mask = mask

        prepared = prepare_bubbles(image, regions, FONT, CONFIG)[0]

        self.assertFalse(prepared.review_required, prepared.review_reason)
        self.assertGreaterEqual(len(prepared.layout_segments), 2)
        self.assertEqual(
            " ".join(segment["text"] for segment in prepared.layout_segments).split(),
            prepared.translation.split(),
        )
        for segment in prepared.layout_segments:
            x1, y1 = segment["x"], segment["y"]
            x2, y2 = x1 + segment["width"], y1 + segment["height"]
            self.assertTrue(np.all(prepared._bubble_interior[y1:y2, x1:x2]))
        rendered = asyncio.run(dispatch(image.copy(), [prepared], FONT, font_size_minimum=8))
        for segment in prepared.layout_segments:
            x1, y1 = segment["x"], segment["y"]
            x2, y2 = x1 + segment["width"], y1 + segment["height"]
            self.assertTrue(np.any(rendered[y1:y2, x1:x2] != image[y1:y2, x1:x2]))

    def test_group_safe_shape_round_trips_without_other_bubbles(self):
        mask = np.zeros((80, 100), np.uint8)
        cv2.ellipse(mask, (30, 40), (20, 30), 0, 0, 360, 1, -1)
        encoded = encode_safe_shape(mask)
        np.testing.assert_array_equal(decode_safe_shape(encoded, 80, 100), mask)
        self.assertIsNone(decode_safe_shape(None, 80, 100))

    def test_three_horizontal_oval_lobes_keep_english_reading_order(self):
        image = np.full((260, 600, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        for x in (100, 300, 500):
            cv2.ellipse(mask, (x, 130), (72, 90), 0, 0, 360, 255, -1)
        for x in (173, 373):
            cv2.rectangle(mask, (x, 120), (x + 54, 140), 255, -1)
        region = block(100, "First day. Second day. Third day.", y=80, h=90)
        region._bubble_mask = mask

        prepared = prepare_bubbles(image, [region], FONT, CONFIG)[0]

        self.assertFalse(prepared.review_required, prepared.review_reason)
        self.assertEqual([segment["text"] for segment in prepared.layout_segments],
                         ["First day.", "Second day.", "Third day."])
        self.assertEqual([segment["x"] for segment in prepared.layout_segments],
                         sorted(segment["x"] for segment in prepared.layout_segments))

    def test_lobe_break_does_not_strand_an_article(self):
        image = np.full((280, 360, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (105, 135), (72, 92), 0, 0, 360, 255, -1)
        cv2.ellipse(mask, (255, 135), (72, 92), 0, 0, 360, 255, -1)
        cv2.rectangle(mask, (172, 126), (188, 144), 255, -1)
        region = block(100, "We saw the moon. Then we left.", y=80, h=90)
        region._bubble_mask = mask

        prepared = prepare_bubbles(image, [region], FONT, CONFIG)[0]

        self.assertFalse(prepared.review_required, prepared.review_reason)
        self.assertNotIn(prepared.layout_segments[0]["text"].split()[-1].lower(), {"a", "an", "the"})

    def test_rendering_rehydrates_bubble_detection_after_context_reload(self):
        import asyncio
        from unittest.mock import patch
        from manga_translator.config import Config
        from manga_translator.detection.bubble import BubbleDetection
        from manga_translator.manga_translator import MangaTranslator
        from manga_translator.utils import Context

        image = page()
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (150, 125), (95, 110), 0, 0, 360, 255, -1)
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.font_path = FONT
        translator._model_usage_timestamps = {}
        translator._pipeline_lab_run = None
        region = block(160, "First")
        other = block(120, "Second")
        ctx = Context(img_rgb=image, img_inpainted=None,
                      text_regions=[region, other], render_mask=None)
        config = Config(bubble_detection={'enabled': True})
        with patch(
            'manga_translator.manga_translator.detect_bubbles',
            return_value=[BubbleDetection(mask, 0.9)],
        ):
            output = asyncio.run(translator._run_text_rendering(config, ctx))

        self.assertTrue(ctx._bubble_detection_done)
        self.assertIs(ctx.text_regions[0]._bubble_mask, mask)
        self.assertFalse(ctx.text_regions[0].review_required)
        interior = ctx.text_regions[0]._bubble_interior > 0
        changed = np.any(output != image, axis=2)
        self.assertTrue(np.any(changed & interior))

    def test_missing_inpainted_canvas_still_renders_safe_bubble(self):
        import asyncio
        from manga_translator.manga_translator import MangaTranslator
        from manga_translator.config import Config
        from manga_translator.utils import Context
        image = page()
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.font_path = FONT
        translator._model_usage_timestamps = {}
        config = Config()
        config.render.font_size_minimum = 12
        ctx = Context(img_rgb=image, img_inpainted=None,
                      text_regions=[block(160), block(120)], render_mask=None)

        output = asyncio.run(translator._run_text_rendering(config, ctx))

        self.assertIsNotNone(ctx.img_inpainted)
        self.assertFalse(np.array_equal(output, image))
        self.assertFalse(ctx.text_regions[0].review_required)
        interior = ctx.text_regions[0]._bubble_interior > 0
        self.assertTrue(np.any(output[interior] != image[interior]))

    def test_replanning_after_font_settings_change(self):
        from manga_translator.manga_translator import MangaTranslator
        from manga_translator.config import Config
        from manga_translator.utils import Context
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.font_path = FONT
        config = Config()
        config.render.font_size = 20
        config.render.font_size_minimum = 12
        ctx = Context(img_rgb=page(), text_regions=[block(160), block(120)])
        translator._prepare_bubble_layout(config, ctx)
        self.assertFalse(ctx.text_regions[0].review_required)
        config.render.font_size_minimum = 150
        translator._prepare_bubble_layout(config, ctx)
        self.assertEqual(ctx.text_regions[0].review_reason, "text_does_not_fit")
        self.assertEqual(len(ctx.text_regions), 2)
        self.assertTrue(all(not hasattr(region, "group_members") for region in ctx.text_regions))

    def test_legacy_mask_generation_is_translation_independent(self):
        import asyncio
        from manga_translator.manga_translator import MangaTranslator
        from manga_translator.config import Config
        from manga_translator.utils import Context
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.font_path = FONT
        translator.verbose = False
        translator.kernel_size = 3
        image = page()
        ctx = Context(img_rgb=image, mask_raw=np.zeros(image.shape[:2], np.uint8),
                      text_regions=[block(160)])
        mask = asyncio.run(translator._run_mask_refinement(Config(), ctx))
        self.assertTrue(mask.any())
        self.assertFalse(getattr(ctx.text_regions[0], "review_required", False))

    def test_bubble_protection_keeps_unrelated_active_text_mask(self):
        bubble = block(160)
        bubble._bubble_restore = np.zeros((260, 300), np.uint8)
        bubble._bubble_restore[60:180, 145:225] = 1
        bubble._bubble_interior = bubble._bubble_restore.copy()
        bubble.review_required = True

        ordinary = block(205)
        mask = np.zeros((260, 300), np.uint8)
        cv2.fillPoly(mask, [np.asarray(ordinary.lines[0], np.int32)], 255)

        result = constrain_mask(mask, [bubble, ordinary])

        self.assertTrue(result[ordinary.lines[0, 0, 1], ordinary.lines[0, 0, 0]])

    def test_bubble_mask_is_available_before_translation(self):
        from manga_translator.detection.bubble import BubbleDetection
        image = page()
        detector_mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(detector_mask, (150, 125), (95, 110), 0, 0, 360, 255, -1)
        regions = [block(160, ""), block(120, "")]
        groups = group_regions_by_bubbles(regions, [BubbleDetection(detector_mask, 0.9)])

        bubble_mask = prepare_bubble_masks(image, groups)

        self.assertTrue(bubble_mask.any())
        self.assertTrue(np.all(bubble_mask[detector_mask == 0] == 0))
        self.assertGreater(np.count_nonzero(detector_mask), np.count_nonzero(bubble_mask))

    def test_bubble_grouping_can_be_disabled(self):
        from manga_translator.detection.bubble import BubbleDetection
        image = page()
        detector_mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(detector_mask, (150, 125), (95, 110), 0, 0, 360, 255, -1)
        r1 = TextBlock([[[160, 70], [180, 70], [180, 170], [160, 170]]],
                       texts=["First lobe text"], translation="First lobe translation", font_size=20,
                       target_lang="ENG", fg_color=(0, 0, 0), bg_color=(255, 255, 255))
        r2 = TextBlock([[[120, 70], [140, 70], [140, 170], [120, 170]]],
                       texts=["Second lobe text"], translation="Second lobe translation", font_size=20,
                       target_lang="ENG", fg_color=(0, 0, 0), bg_color=(255, 255, 255))
        regions = [r1, r2]

        # With grouping enabled: merged into 1 region
        grouped = group_regions_by_bubbles(regions, [BubbleDetection(detector_mask, 0.9)], group=True)
        self.assertEqual(len(grouped), 1)
        self.assertIn("First lobe text", grouped[0].text)
        self.assertIn("Second lobe text", grouped[0].text)

        # With grouping disabled: kept as 2 separate regions, both with bubble mask attached
        ungrouped = group_regions_by_bubbles(regions, [BubbleDetection(detector_mask, 0.9)], group=False)
        self.assertEqual(len(ungrouped), 2)
        self.assertEqual(ungrouped[0].text, "First lobe text")
        self.assertEqual(ungrouped[1].text, "Second lobe text")
        self.assertEqual(ungrouped[0].translation, "First lobe translation")
        self.assertEqual(ungrouped[1].translation, "Second lobe translation")
        self.assertIsNotNone(ungrouped[0]._bubble_mask)
        self.assertIsNotNone(ungrouped[1]._bubble_mask)

    def test_open_boundary_is_flagged(self):
        image = np.full((260, 300, 3), 255, np.uint8)
        cv2.rectangle(image, (50,20), (250,235), (0,0,0), 3)
        cv2.line(image, (148,15), (148,25), (255,255,255), 3)
        cv2.rectangle(image, (163,80), (172,92), (0,0,0), -1)
        groups = prepare_bubbles(image, [block(160)], FONT, CONFIG)
        self.assertEqual(groups[0].review_reason, "uncertain_boundary")
        self.assertTrue(groups[0].review_required)
        self.assertIsNotNone(groups[0]._bubble_box)

    def test_adjacent_boxes_are_separate(self):
        image = np.full((260, 300, 3), 80, np.uint8)
        for x in (30, 165):
            cv2.rectangle(image, (x,20), (x+100,230), (255,255,255), -1)
            cv2.rectangle(image, (x+43,80), (x+50,92), (0,0,0), -1)
        groups = prepare_bubbles(image, [block(70), block(205)], FONT, CONFIG)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(not r.review_required for r in groups))

    def test_adaptive_font_sizing_for_large_bubble(self):
        # A large bubble with moderate text should scale up to fill ~45-55% area comfortably
        image = np.full((400, 400, 3), 80, np.uint8)
        cv2.ellipse(image, (200, 200), (140, 160), 0, 0, 360, (255, 255, 255), -1)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (200, 200), (140, 160), 0, 0, 360, 255, -1)
        region = block(200, "I'll make you regret... not choosing me.", y=150)
        region.font_size = 14  # low default OCR font size
        region._bubble_mask = mask
        dynamic_config = SimpleNamespace(font_size_minimum=12, font_size=None, font_size_offset=0,
                                         no_hyphenation=False, line_spacing=0)
        groups = prepare_bubbles(image, [region], FONT, dynamic_config)
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0].review_required, groups[0].review_reason)
        self.assertGreaterEqual(groups[0].font_size, 22)

    def test_proportional_multi_lobe_word_allocation(self):
        # Top lobe is small (~25% area), bottom lobe is large (~75% area)
        image = np.full((450, 300, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (150, 80), (50, 45), 0, 0, 360, 255, -1)   # small top lobe
        cv2.ellipse(mask, (150, 270), (110, 130), 0, 0, 360, 255, -1) # large bottom lobe
        cv2.rectangle(mask, (135, 115), (165, 150), 255, -1)          # connecting neck
        
        sentence = "About another week, I suppose. Don't come crying to me if you find you cannot be satisfied anymore."
        region_top = block(140, sentence, y=55, h=40)
        region_bot = block(140, "part 2", y=200, h=100)
        region_top._bubble_mask = mask
        region_bot._bubble_mask = mask
        
        dynamic_config = SimpleNamespace(font_size_minimum=10, font_size=None, font_size_offset=0,
                                         no_hyphenation=False, line_spacing=0)
        groups = prepare_bubbles(image, [region_top, region_bot], FONT, dynamic_config)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertFalse(group.review_required, group.review_reason)
        self.assertGreaterEqual(len(group.layout_segments), 2)
        top_words = group.layout_segments[0]["text"].split()
        bot_words = group.layout_segments[1]["text"].split()
        # Top lobe should have fewer words than bottom lobe proportionally
        self.assertLess(len(top_words), len(bot_words))
        self.assertGreaterEqual(group.font_size, 12)

    def test_optical_centering_on_medial_peak(self):
        # Teardrop/egg shape where bounding box center is shifted away from medial peak
        image = np.full((300, 300, 3), 80, np.uint8)
        cv2.ellipse(image, (150, 120), (100, 80), 0, 0, 360, (255, 255, 255), -1)
        # Add a downward tail
        pts = np.array([[100, 180], [60, 260], [150, 180]], np.int32)
        cv2.fillPoly(image, [pts], (255, 255, 255))
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (150, 120), (100, 80), 0, 0, 360, 255, -1)
        cv2.fillPoly(mask, [pts], 255)
        
        region = block(150, "Centered text in speech bubble", y=100)
        region._bubble_mask = mask
        groups = prepare_bubbles(image, [region], FONT, CONFIG)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertFalse(group.review_required, group.review_reason)
        # Layout bounds center should be close to y=120 (medial peak), not y=160+ (tail-skewed center)
        layout_cy = (group.layout_bounds[1] + group.layout_bounds[3]) / 2.0
        self.assertLess(abs(layout_cy - 120), 25.0)

    def test_per_lobe_adaptive_font_sizing_preserves_main_lobe_size(self):
        # A multi-lobe bubble with a narrower top lobe and a wide lower lobe
        image = np.full((450, 300, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (150, 90), (50, 65), 0, 0, 360, 255, -1)   # top lobe
        cv2.ellipse(mask, (150, 270), (110, 130), 0, 0, 360, 255, -1) # large bottom lobe
        cv2.rectangle(mask, (135, 125), (165, 160), 255, -1)          # neck

        sentence = "About another week, I suppose. Though there's this weird rumor going around..."
        region = block(140, sentence, y=55, h=40)
        region._bubble_mask = mask

        dynamic_config = SimpleNamespace(font_size_minimum=10, font_size=22, font_size_offset=0,
                                         no_hyphenation=False, line_spacing=0)
        groups = prepare_bubbles(image, [region], FONT, dynamic_config)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertFalse(group.review_required, group.review_reason)
        self.assertEqual(len(group.layout_segments), 2)
        # Main lobe must not be throttled down to ~14px; should be close to target (>= 20px)
        self.assertGreaterEqual(group.font_size, 20)
        # Each segment must have its own font_size recorded
        self.assertIn("font_size", group.layout_segments[0])
        self.assertIn("font_size", group.layout_segments[1])
        # Font sizes must be consistent between lobes (disparity <= 5px)
        self.assertLessEqual(abs(group.layout_segments[0]["font_size"] - group.layout_segments[1]["font_size"]), 5)
        # Punctuation-aware split keeps clauses intact
        self.assertTrue(group.layout_segments[0]["text"].endswith((",", ".")))

    def test_asymmetric_two_lobe_semantic_allocation(self):
        # Asymmetric 2-lobe bubble: shifted-right upper lobe + shifted-left large bottom lobe
        image = np.full((500, 360, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (240, 100), (55, 80), 0, 0, 360, 255, -1)     # upper lobe shifted right
        cv2.ellipse(mask, (140, 310), (105, 120), 0, 0, 360, 255, -1)  # lower lobe shifted left
        cv2.rectangle(mask, (170, 150), (220, 210), 255, -1)           # connecting neck

        text = (
            "We have about 1 week left, right? "
            "I won't be held responsible if you can no longer be satisfied by sex with your girlfriend from now on."
        )
        region = block(230, text, y=60, h=50)
        region._bubble_mask = mask

        dynamic_config = SimpleNamespace(font_size_minimum=10, font_size=None, font_size_offset=0,
                                         no_hyphenation=False, line_spacing=0)
        groups = prepare_bubbles(image, [region], FONT, dynamic_config)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertFalse(group.review_required, group.review_reason)
        self.assertEqual(len(group.layout_segments), 2)

        top_seg = group.layout_segments[0]
        bot_seg = group.layout_segments[1]

        # Semantic break must place sentence 1 in upper lobe and sentence 2 in lower lobe
        self.assertTrue(top_seg["text"].endswith(("right?", "right? ")))
        self.assertTrue(bot_seg["text"].startswith("I won't"))

        # Upper lobe center must be shifted right relative to lower lobe center
        top_cx = top_seg["x"] + top_seg["width"] / 2.0
        bot_cx = bot_seg["x"] + bot_seg["width"] / 2.0
        self.assertGreater(top_cx, 190.0)
        self.assertLess(bot_cx, 180.0)
        self.assertGreater(top_cx - bot_cx, 25.0)

        # Segments must be safely inside bubble interior
        for seg in [top_seg, bot_seg]:
            x1, y1 = seg["x"], seg["y"]
            x2, y2 = x1 + seg["width"], y1 + seg["height"]
            self.assertTrue(np.all(group._bubble_interior[y1:y2, x1:x2]))

    def test_geometry_profiling_and_neck_detection(self):
        from manga_translator.rendering.bubble_layout import build_geometry_profile
        mask = np.zeros((400, 300), np.uint8)
        cv2.ellipse(mask, (200, 80), (50, 50), 0, 0, 360, 255, -1)    # upper right
        cv2.ellipse(mask, (120, 260), (90, 100), 0, 0, 360, 255, -1)  # lower left
        cv2.rectangle(mask, (140, 120), (180, 170), 255, -1)          # neck

        profile = build_geometry_profile(mask)
        self.assertIsNotNone(profile)
        self.assertGreaterEqual(len(profile["peaks"]), 2)
        # Verify smoothed center shift between upper (cx ~ 200) and lower (cx ~ 120)
        y_top_peak = profile["peaks"][1][2] if profile["peaks"][0][2] > profile["peaks"][1][2] else profile["peaks"][0][2]
        y_bot_peak = profile["peaks"][0][2] if profile["peaks"][0][2] > profile["peaks"][1][2] else profile["peaks"][1][2]
        self.assertGreater(profile["center_smooth"][y_top_peak], profile["center_smooth"][y_bot_peak] + 30.0)

    def test_semantic_breakpoints_scoring(self):
        from manga_translator.rendering.bubble_layout import analyze_semantic_breakpoints
        text = "Hello world. How are you, my friend? If you can come, please do."
        words = text.split()
        costs = analyze_semantic_breakpoints(text)
        self.assertEqual(len(costs), len(words) - 1)

        # Index after "world." (before "How") -> sentence end (cost 0.0)
        idx_after_world = words.index("world.")
        self.assertEqual(costs[idx_after_world], 0.0)

        # Index after "you," (before "my") -> clause end (cost 5.0)
        idx_after_you = words.index("you,")
        self.assertEqual(costs[idx_after_you], 5.0)

        # Index after "friend?" (before "If") -> sentence end (cost 0.0)
        idx_after_friend = words.index("friend?")
        self.assertEqual(costs[idx_after_friend], 0.0)

        # Index after "Hello" (before "world.") -> normal word boundary (cost 30.0)
        idx_after_hello = words.index("Hello")
        self.assertEqual(costs[idx_after_hello], 30.0)

    def test_page_level_font_size_consistency(self):
        # Create an image with two speech bubbles: one wide, one narrower
        image = np.full((600, 800, 3), 255, np.uint8)
        mask_wide = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask_wide, (250, 200), (120, 90), 0, 0, 360, 255, -1)
        
        mask_narrow = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask_narrow, (550, 300), (65, 120), 0, 0, 360, 255, -1)

        reg_wide = block(250, text="Indeed, this is quite important.", y=150, h=80)
        reg_wide._bubble_mask = mask_wide

        reg_narrow = block(550, text="I see, so that's how it really is!", y=200, h=180)
        reg_narrow._bubble_mask = mask_narrow

        config = SimpleNamespace(font_size_minimum=10, font_size=None, font_size_offset=0,
                                 no_hyphenation=False, line_spacing=0)
        groups = prepare_bubbles(image, [reg_wide, reg_narrow], FONT, config)
        self.assertEqual(len(groups), 2)
        font_wide = groups[0].font_size
        font_narrow = groups[1].font_size
        # Font disparity between speech bubbles on the same page must remain small (<= 5px or within 15%)
        self.assertLessEqual(abs(font_wide - font_narrow), 5)
        self.assertGreaterEqual(font_narrow, int(font_wide * 0.85))

    def test_narrow_bubble_relaxed_overflow_preserves_target_font(self):
        # A narrow bubble with text that would otherwise be severely shrunk
        image = np.full((500, 400, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (200, 200), (45, 120), 0, 0, 360, 255, -1)

        text = "This is a long sentence that should not be severely shrunk down."
        region = block(200, text=text, y=100, h=200)
        region._bubble_mask = mask

        target_font = 24
        config = SimpleNamespace(font_size_minimum=10, font_size=target_font, font_size_offset=0,
                                 no_hyphenation=False, line_spacing=0)
        groups = prepare_bubbles(image, [region], FONT, config)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        # Font size must stay close to target (>= 85% of target = 20px) rather than dropping to 10px
        self.assertGreaterEqual(group.font_size, int(target_font * 0.85))
        self.assertIsNotNone(getattr(group, '_bubble_box', None))

    def test_calculate_mask_moments_and_aspect_alignment(self):
        from manga_translator.rendering.bubble_layout import calculate_mask_moments, calculate_text_moments
        # Wide ellipse (width > height)
        wide_mask = np.zeros((300, 500), dtype=np.uint8)
        cv2.ellipse(wide_mask, (250, 150), (180, 80), 0, 0, 360, 255, -1)
        (cx_w, cy_w), cov_w = calculate_mask_moments(wide_mask)
        self.assertAlmostEqual(cx_w, 250.0, delta=2.0)
        self.assertAlmostEqual(cy_w, 150.0, delta=2.0)
        self.assertGreater(cov_w[0, 0], cov_w[1, 1] * 2.0)  # mu_xx substantially larger than mu_yy

        # Tall ellipse (height > width)
        tall_mask = np.zeros((500, 300), dtype=np.uint8)
        cv2.ellipse(tall_mask, (150, 250), (70, 180), 0, 0, 360, 255, -1)
        (cx_t, cy_t), cov_t = calculate_mask_moments(tall_mask)
        self.assertAlmostEqual(cx_t, 150.0, delta=2.0)
        self.assertAlmostEqual(cy_t, 250.0, delta=2.0)
        self.assertGreater(cov_t[1, 1], cov_t[0, 0] * 2.0)  # mu_yy substantially larger than mu_xx

        # Text moments calculation
        sample_lines = [
            {"x": 100, "y": 80, "width": 120},
            {"x": 90, "y": 105, "width": 140},
            {"x": 110, "y": 130, "width": 100},
        ]
        (t_cx, t_cy), t_cov = calculate_text_moments(sample_lines, 20)
        self.assertGreater(t_cx, 140.0)
        self.assertLess(t_cx, 180.0)
        self.assertGreater(t_cy, 100.0)
        self.assertLess(t_cy, 130.0)
        self.assertGreater(t_cov[0, 0], 0.0)
        self.assertGreater(t_cov[1, 1], 0.0)

    def test_sdf_boundary_clearance_validation(self):
        from manga_translator.rendering.bubble_layout import check_sdf_clearance
        mask = np.zeros((300, 300), dtype=np.uint8)
        cv2.rectangle(mask, (50, 50), (250, 250), 255, -1)
        dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)

        # Well-centered lines with ample clearance
        safe_lines = [
            {"x": 100, "y": 100, "width": 100},
            {"x": 100, "y": 130, "width": 100},
        ]
        ok_safe, pen_safe = check_sdf_clearance(dist, safe_lines, font_size=20, margin=5.0)
        self.assertTrue(ok_safe)
        self.assertEqual(pen_safe, 0.0)

        # Line crossing outside mask boundary
        overflow_lines = [
            {"x": 20, "y": 100, "width": 100},
        ]
        ok_over, pen_over = check_sdf_clearance(dist, overflow_lines, font_size=20, margin=5.0)
        self.assertFalse(ok_over)
        self.assertEqual(pen_over, float('inf'))

    def test_three_lobe_vertical_continuous_paragraph(self):
        # 3 vertical lobes connected by necks
        image = np.full((650, 320, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (160, 100), (80, 70), 0, 0, 360, 255, -1)    # top lobe
        cv2.ellipse(mask, (160, 300), (100, 90), 0, 0, 360, 255, -1)   # middle lobe
        cv2.ellipse(mask, (160, 500), (85, 75), 0, 0, 360, 255, -1)    # bottom lobe
        cv2.rectangle(mask, (140, 150), (180, 230), 255, -1)           # top neck
        cv2.rectangle(mask, (140, 370), (180, 440), 255, -1)           # bottom neck

        text = (
            "First part of the message. "
            "Then the second longer section goes right in the middle. "
            "Finally the concluding line goes at the bottom."
        )
        region = block(160, text, y=80, h=60)
        region._bubble_mask = mask

        config = SimpleNamespace(font_size_minimum=10, font_size=18, font_size_offset=0,
                                 no_hyphenation=False, line_spacing=0)
        groups = prepare_bubbles(image, [region], FONT, config)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertFalse(group.review_required, group.review_reason)
        self.assertEqual(len(group.layout_segments), 3)
        self.assertIn("First", group.layout_segments[0]["text"])
        self.assertIn("middle", group.layout_segments[1]["text"])
        self.assertIn("bottom", group.layout_segments[2]["text"])
        # Reading order vertically verified
        self.assertLess(group.layout_segments[0]["y"], group.layout_segments[1]["y"])
        self.assertLess(group.layout_segments[1]["y"], group.layout_segments[2]["y"])

    def test_adaptive_font_sizing_fills_speech_bubble_space(self):
        # A tall oval speech bubble (220 wide x 340 tall)
        image = np.full((400, 300, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (150, 200), (90, 140), 0, 0, 360, 255, -1)

        text = "I really want to go to the amusement park together this weekend!"
        region = TextBlock(
            lines=[[[135, 100], [165, 100], [165, 300], [135, 300]]],
            texts=["source"],
            translation=text,
            font_size=16,
            target_lang="ENG",
            fg_color=(0, 0, 0),
            bg_color=(255, 255, 255),
        )
        region._bubble_mask = mask

        config = SimpleNamespace(font_size_minimum=10, font_size=None, font_size_offset=0,
                                 no_hyphenation=False, line_spacing=0)
        groups = prepare_bubbles(image, [region], FONT, config)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertFalse(group.review_required, group.review_reason)
        # Font size should adaptively scale to comfortably fill the bubble (~20-28px)
        self.assertGreaterEqual(group.font_size, 20)
        self.assertIsNotNone(group._bubble_box)
        alpha = group._bubble_box[:, :, 3] > 0
        ys, xs = np.nonzero(alpha)
        text_h = ys.max() - ys.min() + 1
        text_w = xs.max() - xs.min() + 1
        # Text block should fill >40% of the bubble height and width rather than sitting as a tiny slice
        self.assertGreater(text_h, 100)
        self.assertGreater(text_w, 80)

    def test_tall_speech_bubble_avoids_single_word_columns(self):
        # Bubble with ratio < 0.75 (tall)
        image = np.full((500, 250, 3), 255, np.uint8)
        mask = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(mask, (125, 250), (80, 170), 0, 0, 360, 255, -1)

        text = "I will show you what true power looks like right here right now."
        region = TextBlock(
            lines=[[[110, 120], [140, 120], [140, 380], [110, 380]]],
            texts=["source"],
            translation=text,
            font_size=18,
            target_lang="ENG",
            fg_color=(0, 0, 0),
            bg_color=(255, 255, 255),
        )
        region._bubble_mask = mask

        config = SimpleNamespace(font_size_minimum=10, font_size=None, font_size_offset=0,
                                 no_hyphenation=False, line_spacing=0)
        groups = prepare_bubbles(image, [region], FONT, config)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertFalse(group.review_required, group.review_reason)
        # Check rendered box
        self.assertIsNotNone(group._bubble_box)
        alpha = group._bubble_box[:, :, 3] > 0
        ys, xs = np.nonzero(alpha)
        text_w = xs.max() - xs.min() + 1
        # In a 160px wide bubble, text width should use a healthy portion (>70px) and not be squeezed into 1 word
        self.assertGreater(text_w, 70)


if __name__ == "__main__":
    unittest.main()
