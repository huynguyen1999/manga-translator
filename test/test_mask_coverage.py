import asyncio
import unittest

import numpy as np

from manga_translator.mask_refinement import dispatch
from manga_translator.utils import TextBlock


class MaskCoverageTests(unittest.TestCase):
    def test_selected_polygon_covers_detector_mask_holes(self):
        image = np.full((120, 120, 3), 255, dtype=np.uint8)
        mask = np.zeros((120, 120), dtype=np.uint8)
        image[45:65, 55:58] = 0
        mask[50:55, 56:57] = 255
        image[30:40, 90:100] = 0
        mask[30:40, 90:100] = 255
        block = TextBlock([[[40, 40], [80, 40], [80, 70], [40, 70]]],
                          texts=['dialogue'], translation='Hello', font_size=14,
                          fg_color=(0, 0, 0), bg_color=(255, 255, 255))

        result = asyncio.run(dispatch([block], image, mask))

        selected_black = (image[:, :, 0] == 0)
        selected_black[:40] = False
        selected_black[70:] = False
        selected_black[:, :40] = False
        selected_black[:, 80:] = False
        self.assertEqual(np.count_nonzero(selected_black & (result == 0)), 0)
        self.assertEqual(result[42, 42], 0, 'Do not erase blank bubble space')
        self.assertFalse(result[30:40, 90:100].any())

    def test_small_source_marks_survive_refinement_without_erasing_other_text(self):
        image = np.full((120, 120, 3), 255, dtype=np.uint8)
        mask = np.zeros((120, 120), dtype=np.uint8)
        # Isolated small marks are rejected by the connected-component filter.
        for y in (30, 45, 60):
            image[y:y+2, 35:37] = 0
            mask[y:y+2, 35:37] = 255
        # Unselected lettering (e.g. a sound effect) must be preserved.
        mask[30:40, 90:100] = 255
        image[30:40, 90:100] = 0
        block = TextBlock([[[25, 20], [50, 20], [50, 80], [25, 80]]],
                          texts=['dialogue'], translation='Hello', font_size=14)
        result = asyncio.run(dispatch([block], image, mask))
        for y in (30, 45, 60):
            self.assertTrue(np.all(result[y:y+2, 35:37] == 255))
        self.assertFalse(result[30:40, 90:100].any())
        self.assertEqual(result[24, 28], 0, 'Do not erase a full text rectangle')

    def test_none_raw_mask_does_not_raise(self):
        image = np.full((120, 120, 3), 255, dtype=np.uint8)
        block = TextBlock([[[25, 20], [50, 20], [50, 80], [25, 80]]],
                          texts=['dialogue'], translation='Hello', font_size=14)
        result = asyncio.run(dispatch([block], image, None))
        self.assertIsNotNone(result)
        self.assertEqual(result.shape, (120, 120))

