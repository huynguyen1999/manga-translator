"""Bubble geometry is prepared once and shared between pipeline stages."""

import unittest
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np

from manga_translator.geometry.bubbles import prepare_page_geometry
from manga_translator.config import Config, BubbleDetectionConfig
from manga_translator.mask_builder import build_inpaint_masks
from manga_translator.rendering.bubble_layout import prepare_bubble_masks


class BubbleGeometryReuseTests(unittest.TestCase):
    def test_crop_geometry_matches_full_page_reference(self):
        for center, axes in (((70, 60), (42, 45)), ((18, 68), (30, 45))):
            image = np.full((120, 140, 3), 255, dtype=np.uint8)
            cv2.ellipse(image, center, axes, 0, 0, 360, (0, 0, 0), 3)
            mask = np.zeros(image.shape[:2], dtype=np.uint8)
            cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)
            region = SimpleNamespace(_bubble_mask=mask)

            component = (mask > 0).astype(np.uint8)
            contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            restore = np.zeros_like(component)
            cv2.drawContours(restore, contours, -1, 1, cv2.FILLED)
            interior = cv2.erode(restore, np.ones((9, 9), dtype=np.uint8))
            search_band = restore - cv2.erode(restore, np.ones((9, 9), dtype=np.uint8))
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            outline = (((gray < 220) & (search_band > 0)).astype(np.uint8) * 255)
            protected = cv2.dilate(outline, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
            expected_cleanup = (interior * 255).astype(np.uint8)
            expected_cleanup[protected > 0] = 0

            geometry, cleanup = prepare_page_geometry(image, [region], padding=9)
            bubble = geometry.bubbles[id(mask)]
            x0, y0, x1, y1 = bubble.bbox
            np.testing.assert_array_equal(region._bubble_interior, interior)
            np.testing.assert_array_equal(geometry.protected_edge_mask, protected)
            np.testing.assert_array_equal(cleanup, expected_cleanup)
            np.testing.assert_array_equal(bubble.interior, interior[y0:y1, x0:x1])
            np.testing.assert_array_equal(bubble.protected_edge, protected[y0:y1, x0:x1])

    def test_regions_sharing_detection_reuse_page_geometry(self):
        image = np.full((160, 180, 3), 255, dtype=np.uint8)
        mask = np.zeros((160, 180), dtype=np.uint8)
        cv2.ellipse(mask, (90, 80), (55, 65), 0, 0, 360, 255, -1)
        regions = [SimpleNamespace(_bubble_mask=mask), SimpleNamespace(_bubble_mask=mask)]
        profile = {}

        geometry, combined = prepare_page_geometry(image, regions, profile=profile)
        self.assertGreater(np.count_nonzero(combined), 0)
        self.assertEqual(profile["workload"]["unique_bubble_count"], 1)
        self.assertEqual(profile["workload"]["distance_transform_calls"], 1)
        self.assertEqual(len(geometry.bubbles), 1)
        bubble = geometry.bubbles[id(mask)]
        for crop in (bubble.mask, bubble.interior, bubble.protected_edge, bubble.cleanup_mask):
            self.assertLess(crop.size, image.shape[0] * image.shape[1])
            self.assertTrue(crop.flags.owndata)
        self.assertIs(regions[0]._bubble_interior, regions[1]._bubble_interior)
        self.assertIs(bubble, geometry.bubbles[id(mask)])
        self.assertTrue(geometry.matches(image, regions, 9))
        regions[0]._bubble_mask = mask.copy()
        self.assertFalse(geometry.matches(image, regions, 9))
        regions[0]._bubble_mask = mask
        regions[0]._bubble_mask = None
        self.assertFalse(geometry.matches(image, regions, 9))
        regions[0]._bubble_mask = mask

        next_profile = {}
        self.assertIsNone(prepare_bubble_masks(
            image, regions, profile=next_profile, return_cleanup=False, page_geometry=geometry
        ))
        self.assertEqual(next_profile["workload"]["distance_transform_calls"], 0)
        self.assertIs(regions[0]._bubble_interior, regions[1]._bubble_interior)

        bundle = asyncio.run(build_inpaint_masks(
            image=image,
            detector_textlines=[],
            detector_mask=None,
            text_regions=regions,
            bubble_detections=[],
            config=Config(bubble_detection=BubbleDetectionConfig(padding=9)),
            text_mask=np.zeros(image.shape[:2], dtype=np.uint8),
            page_geometry=geometry,
        ))
        self.assertIs(bundle.page_geometry, geometry)
        self.assertEqual(bundle.profile["workload"]["distance_transform_calls"], 0)


if __name__ == "__main__":
    unittest.main()
