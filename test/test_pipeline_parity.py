import asyncio
import unittest
import numpy as np
import cv2

from manga_translator.config import Config, RenderConfig, BubbleDetectionConfig, DetectorConfig, OcrConfig
from manga_translator.utils import Context, TextBlock
from manga_translator.mask_builder import build_inpaint_masks, build_detector_cleanup_mask, MaskBundle
from manga_translator.detection.bubble import BubbleDetection, serialize_bubble_detections, deserialize_bubble_detections
from manga_translator.pipeline.run import serialize_regions, deserialize_textblocks
from manga_translator.rendering.layout import layout_page
from manga_translator.rendering import render_page, get_default_eng_font
from devscripts.pipeline_step_runner import run_fast_placement_and_render


class PipelineParityTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(
            render=RenderConfig(
                font_size_minimum=0,
                font_size=None,
                font_size_offset=0,
                line_spacing=None,
                no_hyphenation=False,
            ),
            bubble_detection=BubbleDetectionConfig(
                enabled=True,
                model="yolov8m",
                confidence=0.25,
                mask_threshold=0.5,
                padding=9,
                group_regions=False,
            ),
        )
        self.font_path = get_default_eng_font()

    def test_canonical_config_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.render.font_size_minimum, 0)
        self.assertEqual(cfg.ocr.min_text_length, 1)
        self.assertTrue(cfg.translator.no_text_lang_skip)
        self.assertTrue(cfg.bubble_detection.enabled)
        self.assertEqual(cfg.bubble_detection.model, "yolov8m")
        self.assertFalse(cfg.bubble_detection.group_regions)

    def test_bubble_detection_lossless_serialization_roundtrip(self):
        mask = np.zeros((200, 200), dtype=np.uint8)
        cv2.circle(mask, (100, 100), 50, 255, -1)
        bd = BubbleDetection(mask=mask, confidence=0.95)
        serialized = serialize_bubble_detections([bd])
        self.assertEqual(len(serialized), 1)
        self.assertEqual(serialized[0]["confidence"], 0.95)
        self.assertTrue(len(serialized[0]["polygons"]) > 0 or len(serialized[0]["polygon"]) > 0)

        restored = deserialize_bubble_detections(serialized, (200, 200))
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].confidence, 0.95)
        self.assertTrue(np.count_nonzero(restored[0].mask) > 0)
        # Check IoU between original and restored mask
        intersection = np.logical_and(mask > 0, restored[0].mask > 0)
        union = np.logical_or(mask > 0, restored[0].mask > 0)
        iou = np.sum(intersection) / np.sum(union)
        self.assertGreater(iou, 0.90)

    def test_region_serialization_lossless_roundtrip(self):
        lines = np.array([[[30, 40], [170, 40], [170, 160], [30, 160]]], dtype=np.int32)
        block = TextBlock(
            lines=lines,
            texts=["美味い肉だ！"],
            translation="THIS MEAT IS DELICIOUS!",
            font_size=45,
            target_lang="ENG",
        )
        block.region_id = "r1"
        block.source_region_ids = ["s1", "s2"]
        block.source_font_size = 45
        block.calibrated_font_size = 28
        interior = np.zeros((300, 300), dtype=np.uint8)
        cv2.circle(interior, (100, 100), 70, 255, -1)
        block._bubble_interior = interior

        serialized = serialize_regions([block])
        self.assertEqual(len(serialized), 1)
        self.assertEqual(serialized[0]["region_id"], "r1")
        self.assertEqual(serialized[0]["source_font_size"], 45)
        self.assertEqual(serialized[0]["calibrated_font_size"], 28)
        self.assertIn("bubble_safe_shape", serialized[0])

        restored = deserialize_textblocks(serialized)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].region_id, "r1")
        self.assertEqual(restored[0].source_region_ids, ["s1", "s2"])
        self.assertEqual(restored[0].source_font_size, 45)
        self.assertEqual(restored[0].calibrated_font_size, 28)
        self.assertEqual(restored[0].translation, "THIS MEAT IS DELICIOUS!")

    def test_build_inpaint_masks_produces_consistent_mask_bundle(self):
        img = np.full((300, 300, 3), 255, dtype=np.uint8)
        lines = np.array([[[50, 50], [150, 50], [150, 90], [50, 90]]], dtype=np.int32)
        block = TextBlock(lines=lines, texts=["TEXT"], translation="TEXT", font_size=20)
        bubble_mask = np.zeros((300, 300), dtype=np.uint8)
        cv2.ellipse(bubble_mask, (100, 100), (60, 40), 0, 0, 360, 255, -1)
        block._bubble_mask = bubble_mask

        bundle = asyncio.run(build_inpaint_masks(
            image=img,
            detector_textlines=[],
            detector_mask=None,
            text_regions=[block],
            bubble_detections=[BubbleDetection(bubble_mask, 0.9)],
            config=self.config,
        ))

        self.assertIsInstance(bundle, MaskBundle)
        self.assertEqual(bundle.final_inpaint_mask.shape, (300, 300))
        self.assertIsNotNone(getattr(block, "_bubble_interior", None))

    def test_this_meat_is_delicious_layout_and_render_parity(self):
        """Verify that Studio layout_page + render_page matches run_fast_placement_and_render."""
        h, w = 400, 400
        img = np.full((h, w, 3), 255, dtype=np.uint8)
        # Draw a black border speech bubble
        cv2.ellipse(img, (200, 200), (90, 70), 0, 0, 360, (0, 0, 0), 3)

        bubble_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.ellipse(bubble_mask, (200, 200), (90, 70), 0, 0, 360, 255, -1)

        # Create two identical contexts
        lines = np.array([[[150, 170], [250, 170], [250, 230], [150, 230]]], dtype=np.int32)
        block1 = TextBlock(
            lines=lines,
            texts=["美味い肉だ！"],
            translation="THIS MEAT IS DELICIOUS!",
            font_size=50,
            target_lang="ENG",
        )
        block1._bubble_mask = bubble_mask.copy()
        ctx1 = Context(img_rgb=img.copy(), img_inpainted=img.copy(), text_regions=[block1])

        block2 = TextBlock(
            lines=lines,
            texts=["美味い肉だ！"],
            translation="THIS MEAT IS DELICIOUS!",
            font_size=50,
            target_lang="ENG",
        )
        block2._bubble_mask = bubble_mask.copy()
        ctx2 = Context(img_rgb=img.copy(), img_inpainted=img.copy(), text_regions=[block2])

        # Runner execution path
        runner_output, _ = run_fast_placement_and_render(
            ctx=ctx1,
            config=self.config,
            font_path=self.font_path,
            enable_bubble_layout=True,
        )

        # Studio execution path (layout_page then render_page)
        layout_page(ctx2, self.config, self.font_path)
        studio_output = asyncio.run(render_page(ctx2, self.config, self.font_path))

        # Check font sizes match
        self.assertEqual(block1.font_size, block2.font_size)
        # Check placed lines count matches
        self.assertEqual(len(block1.layout_segments[0]["lines"]), len(block2.layout_segments[0]["lines"]))

        # Check placed line texts match
        runner_lines = [line["text"] for line in block1.layout_segments[0]["lines"]]
        studio_lines = [line["text"] for line in block2.layout_segments[0]["lines"]]
        self.assertEqual(runner_lines, studio_lines)

        # Check that DELICIOUS is not hyphenated awkwardly
        for line in studio_lines:
            self.assertNotIn("DELI-", line)

        # Check RGB pixel exact match
        np.testing.assert_array_equal(runner_output, studio_output)


if __name__ == "__main__":
    unittest.main()
