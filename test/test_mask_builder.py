"""Unit and regression tests for canonical mask generation and bubble residual text recovery."""

import asyncio
import unittest
import numpy as np
import cv2

from manga_translator.config import Config, BubbleDetectionConfig
from manga_translator.utils import TextBlock
from manga_translator.detection.bubble import BubbleDetection
from manga_translator.mask_builder import (
    MaskBundle,
    MaskMetrics,
    build_detector_cleanup_mask,
    build_detector_rescue_mask,
    recover_bubble_residual_text,
    build_inpaint_masks,
    create_mask_sources_overlay,
)


class MaskBuilderTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(
            bubble_detection=BubbleDetectionConfig(
                enabled=True,
                padding=9,
            )
        )

    def test_mask_bundle_initialization_and_properties(self):
        h, w = 100, 100
        empty = np.zeros((h, w), dtype=np.uint8)
        bundle = MaskBundle(
            text_mask=empty,
            detector_cleanup_mask=empty,
            bubble_cleanup_mask=empty,
            protected_edge_mask=empty,
            final_inpaint_mask=empty,
        )
        self.assertEqual(bundle.detector_rescue_mask.shape, (h, w))
        self.assertEqual(bundle.bubble_residual_mask.shape, (h, w))

        metrics = MaskMetrics(
            known_text_coverage=0.99,
            detector_rescue_pixels=50,
            bubble_residual_pixels=120,
            protected_edge_violations=0,
            residual_candidate_pixels_rejected=10,
            source_text_ink_coverage=0.99,
        )
        metrics_dict = metrics.to_dict()
        self.assertEqual(metrics_dict["detector_rescue_pixels"], 50)
        self.assertEqual(metrics_dict["bubble_residual_pixels"], 120)

    def test_detector_rescue_mask_retains_filtered_boxes(self):
        h, w = 200, 200
        detector_mask = np.zeros((h, w), dtype=np.uint8)
        # Detector marked a box
        detector_mask[40:120, 60:140] = 255

        class DummyTextline:
            pts = np.array([[60, 40], [140, 40], [140, 120], [60, 120]], dtype=np.int32)

        # Build rescue mask
        rescue = build_detector_rescue_mask([DummyTextline()], detector_mask, (h, w))
        self.assertGreater(np.count_nonzero(rescue), 0)
        self.assertEqual(np.count_nonzero(rescue[40:120, 60:140]), (120 - 40) * (140 - 60))
        # Pixels outside the box should be zero
        self.assertEqual(np.count_nonzero(rescue[:40, :]), 0)

    def test_bubble_residual_recovery_japanese_punctuation_regression_fixture(self):
        """
        Regression Fixture:
        A vertical Japanese speech bubble with recognized main text (膣内に)
        and an un-OCRed trailing !? punctuation mark.
        Assert that recover_bubble_residual_text and build_inpaint_masks successfully
        recover the !? into bubble_residual_mask and final_inpaint_mask.
        """
        h, w = 300, 300
        img = np.full((h, w, 3), 255, dtype=np.uint8)

        # 1. Draw speech bubble border (oval)
        center = (150, 150)
        axes = (80, 110)
        cv2.ellipse(img, center, axes, 0, 0, 360, (0, 0, 0), 4)

        # 2. Draw vertical Japanese characters in the bubble
        # Main characters (top part of column: y=70 to y=190)
        cv2.putText(img, "|", (145, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
        cv2.putText(img, "|", (145, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)

        # Trailing punctuation "!?" at the bottom of the column (y=205 to y=235)
        cv2.putText(img, "!", (140, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 3)
        cv2.putText(img, "?", (155, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 3)

        # Bubble mask from detector
        bubble_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.ellipse(bubble_mask, center, axes, 0, 0, 360, 255, -1)

        # OCR only recognized the top characters (y=70 to y=180)
        ocr_lines = np.array([[[130, 70], [170, 70], [170, 185], [130, 185]]], dtype=np.int32)
        text_block = TextBlock(
            lines=ocr_lines,
            texts=["膣内に"],
            translation="INSIDE",
            font_size=28,
            direction="v",
        )
        text_block._bubble_mask = bubble_mask.copy()

        # Text mask only covers OCR recognized area
        text_mask = np.zeros((h, w), dtype=np.uint8)
        text_mask[70:185, 130:170] = (img[70:185, 130:170, 0] < 200).astype(np.uint8) * 255

        # Run build_inpaint_masks
        bundle = asyncio.run(build_inpaint_masks(
            image=img,
            detector_textlines=[],
            detector_mask=None,
            text_regions=[text_block],
            bubble_detections=[BubbleDetection(bubble_mask, 0.95)],
            config=self.config,
            text_mask=text_mask,
        ))

        # Check: !? was in region y=200..230, x=135..165
        punct_roi_final = bundle.final_inpaint_mask[200:230, 135:165]
        punct_roi_res = bundle.bubble_residual_mask[200:230, 135:165]

        self.assertGreater(np.count_nonzero(punct_roi_res), 20, "Bubble residual mask should recover missed !?")
        self.assertGreater(np.count_nonzero(punct_roi_final), 20, "Final inpaint mask must cover missed !?")
        self.assertGreater(bundle.metrics.bubble_residual_pixels, 0)
        self.assertGreaterEqual(bundle.metrics.source_text_ink_coverage, 0.95)

        # Assert protected edge invariant: no mask pixel on the bubble border
        border_overlap = np.logical_and(bundle.final_inpaint_mask > 0, bundle.protected_edge_mask > 0)
        self.assertEqual(np.count_nonzero(border_overlap), 0, "Final inpaint mask must not intersect protected edge")

    def test_protected_bubble_edge_strict_invariant(self):
        """Ensure that even if candidate ink extends to the speech bubble boundary, protected edge remains 0."""
        h, w = 200, 200
        img = np.full((h, w, 3), 255, dtype=np.uint8)
        cv2.circle(img, (100, 100), 60, (0, 0, 0), 3)

        bubble_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(bubble_mask, (100, 100), 60, 255, -1)

        # Draw dark strokes extending all the way to the border
        cv2.line(img, (100, 100), (160, 100), (0, 0, 0), 5)

        lines = np.array([[[80, 80], [120, 80], [120, 120], [80, 120]]], dtype=np.int32)
        block = TextBlock(lines=lines, texts=["A"], translation="A", font_size=20)
        block._bubble_mask = bubble_mask

        bundle = asyncio.run(build_inpaint_masks(
            image=img,
            detector_textlines=[],
            detector_mask=None,
            text_regions=[block],
            bubble_detections=[BubbleDetection(bubble_mask, 0.9)],
            config=self.config,
        ))

        # Check intersection with protected edge
        self.assertEqual(np.count_nonzero(np.logical_and(bundle.final_inpaint_mask > 0, bundle.protected_edge_mask > 0)), 0)

    def test_orientation_aware_text_envelope_geometry(self):
        """Test vertical vs horizontal text envelope expansion."""
        h, w = 300, 300
        img = np.full((h, w, 3), 255, dtype=np.uint8)
        bubble_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.rectangle(bubble_mask, (30, 30), (270, 270), 255, -1)

        # Vertical text line
        v_lines = np.array([[[140, 100], [160, 100], [160, 180], [140, 180]]], dtype=np.int32)
        v_block = TextBlock(lines=v_lines, texts=["あ\nい\nう"], font_size=20, direction="v")
        v_block._bubble_mask = bubble_mask.copy()

        # Place trailing punctuation at (150, 205)
        cv2.circle(img, (150, 205), 4, (0, 0, 0), -1)

        text_mask = np.zeros((h, w), dtype=np.uint8)
        text_mask[100:180, 140:160] = 255

        bundle = asyncio.run(build_inpaint_masks(
            image=img,
            detector_textlines=[],
            detector_mask=None,
            text_regions=[v_block],
            bubble_detections=[BubbleDetection(bubble_mask, 0.9)],
            config=self.config,
            text_mask=text_mask,
        ))

        # Dot at (150, 205) should be recovered in vertical expansion
        self.assertGreater(bundle.final_inpaint_mask[205, 150], 0)

    def test_mask_sources_overlay_generation(self):
        h, w = 150, 150
        img = np.full((h, w, 3), 240, dtype=np.uint8)
        text_mask = np.zeros((h, w), dtype=np.uint8)
        text_mask[30:60, 30:60] = 255
        rescue_mask = np.zeros((h, w), dtype=np.uint8)
        rescue_mask[60:90, 30:60] = 255
        res_mask = np.zeros((h, w), dtype=np.uint8)
        res_mask[90:120, 30:60] = 255
        edge_mask = np.zeros((h, w), dtype=np.uint8)
        edge_mask[20:25, 20:130] = 255
        final_mask = np.bitwise_or(text_mask, rescue_mask)
        final_mask = np.bitwise_or(final_mask, res_mask)

        bundle = MaskBundle(
            text_mask=text_mask,
            detector_cleanup_mask=rescue_mask,
            bubble_cleanup_mask=np.zeros((h, w), dtype=np.uint8),
            protected_edge_mask=edge_mask,
            final_inpaint_mask=final_mask,
            bubble_residual_mask=res_mask,
        )

        overlay = create_mask_sources_overlay(img, bundle)
        self.assertEqual(overlay.shape, (h, w, 3))
        self.assertEqual(overlay.dtype, np.uint8)
        # Overlay should not be identical to original image
        self.assertFalse(np.array_equal(overlay, img))


if __name__ == "__main__":
    unittest.main()
