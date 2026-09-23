"""Regression checks for crop-local mask refinement."""

import hashlib
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from manga_translator.mask_refinement import _dilate_components
from manga_translator.mask_refinement import text_mask_utils
from manga_translator.utils import Quadrilateral


class MaskRefinementCropTests(unittest.TestCase):
    def test_component_crop_dilation_matches_page_dilation(self):
        rng = np.random.default_rng(31)
        for height, width, density in ((90, 130, 0.01), (45, 64, 0.2)):
            mask = (rng.random((height, width)) < density).astype(np.uint8) * 255
            mask[0, 0] = mask[-1, -1] = 255
            for size in (1, 3, 8, 21):
                kernel = np.ones((size, size), dtype=np.uint8)
                expected = cv2.dilate(mask, kernel, iterations=1)
                actual = _dilate_components(mask, kernel)
                np.testing.assert_array_equal(actual, expected)

    def test_complete_mask_golden_output_and_crop_workload(self):
        height, width = 180, 260
        image = np.full((height, width, 3), 238, dtype=np.uint8)
        for y in range(height):
            image[y, :, :] = 225 + y % 25
        mask = np.zeros((height, width), dtype=np.uint8)
        textlines = []
        for x1, y1, x2, y2 in ((24, 38, 66, 80), (150, 64, 221, 115)):
            points = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.int32)
            textlines.append(Quadrilateral(points, "", 0))
            cv2.putText(mask, "A7", (x1 + 8, y1 + 31), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 255, 2)
            cv2.putText(mask, "B2", (x1 + 8, y1 + 55), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 255, 2)
        cv2.circle(mask, (10, 160), 2, 255, -1)

        profile = {}
        with patch.object(text_mask_utils, "refine_mask", side_effect=lambda _image, raw: raw.copy()):
            result = text_mask_utils.complete_mask(image, mask, textlines, profile=profile)

        self.assertEqual(
            hashlib.sha256(result.tobytes()).hexdigest(),
            "c7bd996a525ce02f1c19266d2dfbc5d36eb1ece4bc4eec2256d0aaf1b742e215",
        )
        self.assertEqual(profile["workload"]["full_page_textline_masks"], 0)
        self.assertLess(profile["workload"]["shapely_intersections"], 14 * len(textlines))
        self.assertLess(profile["workload"]["crop_pixels_processed"], height * width)
        self.assertIn("bilateral_filter_ms", profile["timings_ms"])


if __name__ == "__main__":
    unittest.main()
