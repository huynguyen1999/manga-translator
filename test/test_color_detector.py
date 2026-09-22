import unittest
import numpy as np
from PIL import Image, ImageDraw

from manga_translator.colorization.detector import is_image_colored, distance_from_grayscale
from manga_translator.mode.local import parse_exclude_spec, is_page_color_excluded


class TestColorDetector(unittest.TestCase):

    def test_pure_grayscale(self):
        img = Image.new('RGB', (800, 1200), (255, 255, 255))
        d = ImageDraw.Draw(img)
        # Black lines and screentones
        for y in range(0, 1200, 20):
            d.line([(0, y), (800, y)], fill=(30, 30, 30), width=2)
        for x in range(0, 800, 40):
            d.line([(x, 0), (x, 1200)], fill=(120, 120, 120), width=1)

        is_colored, details = is_image_colored(img)
        self.assertFalse(is_colored, f"Pure B&W should not be colored: {details}")
        self.assertAlmostEqual(details['dist_gray'], 0.0, places=2)

    def test_yellowed_aged_paper_scan(self):
        # Scanned page with aged paper tint (warm sepia/yellowish tint)
        img = Image.new('RGB', (800, 1200), (242, 232, 205))
        d = ImageDraw.Draw(img)
        # Black manga lines
        for y in range(100, 1100, 30):
            d.line([(50, y), (750, y)], fill=(25, 25, 20), width=3)
        for x in range(100, 700, 50):
            d.line([(x, 100), (x, 1100)], fill=(40, 35, 30), width=2)

        is_colored, details = is_image_colored(img)
        self.assertFalse(is_colored, f"Yellowed paper scan must not be detected as colored: {details}")

    def test_color_cover_with_white_margins(self):
        # Manga cover: 80% white space, 20% colored character
        img = Image.new('RGB', (800, 1200), (255, 255, 255))
        d = ImageDraw.Draw(img)
        # Character with red clothes and blue hair
        d.rectangle([200, 300, 600, 600], fill=(220, 50, 40))   # Red jacket
        d.rectangle([300, 150, 500, 300], fill=(50, 110, 210))  # Blue hair
        d.rectangle([320, 220, 480, 280], fill=(250, 215, 185)) # Skin tone

        is_colored, details = is_image_colored(img)
        self.assertTrue(is_colored, f"Color cover with white margins should be detected as colored: {details}")

    def test_pastel_illustration(self):
        # Soft pastel color spread
        img = Image.new('RGB', (800, 1200), (245, 240, 235))
        d = ImageDraw.Draw(img)
        # Sky and foliage
        d.rectangle([50, 50, 750, 400], fill=(160, 200, 245))  # Pastel sky
        d.rectangle([50, 700, 750, 1150], fill=(130, 210, 140)) # Pastel green grass

        is_colored, details = is_image_colored(img)
        self.assertTrue(is_colored, f"Pastel illustration should be detected as colored: {details}")

    def test_detection_disabled(self):
        img = Image.new('RGB', (400, 400), (255, 0, 0)) # Vibrant red
        is_colored, details = is_image_colored(img, color_threshold=-1)
        self.assertFalse(is_colored, "When threshold <= 0, detection should be disabled")


class TestExcludePageSpec(unittest.TestCase):

    def test_parse_exclude_spec(self):
        pages, patterns = parse_exclude_spec("1, 3, 5-8, *cover*, 002.png")
        self.assertEqual(pages, {1, 3, 5, 6, 7, 8})
        self.assertEqual(patterns, ["*cover*", "002.png"])

    def test_is_page_color_excluded(self):
        pages, patterns = parse_exclude_spec("1, 5-7, *cover*")
        self.assertTrue(is_page_color_excluded("/manga/page001.jpg", 1, pages, patterns))
        self.assertFalse(is_page_color_excluded("/manga/page002.jpg", 2, pages, patterns))
        self.assertTrue(is_page_color_excluded("/manga/page006.jpg", 6, pages, patterns))
        self.assertTrue(is_page_color_excluded("/manga/front_cover.jpg", 20, pages, patterns))
        self.assertFalse(is_page_color_excluded("/manga/page008.jpg", 8, pages, patterns))


if __name__ == '__main__':
    unittest.main()
