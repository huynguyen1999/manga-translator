import tempfile
import unittest
from pathlib import Path

from PIL import Image

from server.image_variants import VARIANT_SPECS, generate_image_variants


class ImageVariantTests(unittest.TestCase):
    def test_generates_only_requested_variant(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            Image.new("RGB", (1200, 1800), "white").save(folder / "final.png")
            variants = generate_image_variants(folder, only="cover")
            self.assertEqual(set(variants), {"cover"})
            self.assertTrue((folder / "cover.webp").is_file())
            self.assertFalse((folder / "preview.webp").exists())

    def test_generates_four_non_upscaled_webp_tiers(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            Image.new("RGB", (1200, 1800), "white").save(folder / "final.png")
            page_tiers = generate_image_variants(folder)
            self.assertNotIn("cover", page_tiers)
            first = generate_image_variants(folder, include_cover=True)
            self.assertEqual(set(first), set(VARIANT_SPECS))
            sizes = {}
            for name, (max_width, _) in VARIANT_SPECS.items():
                with Image.open(folder / f"{name}.webp") as image:
                    sizes[name] = image.width
                    self.assertLessEqual(image.width, max_width)
                    self.assertGreater(image.width, 0)
            self.assertEqual(sizes, {"batch": 160, "cover": 480, "preview": 480, "reader": 1200})
            mtimes = {name: (folder / f"{name}.webp").stat().st_mtime_ns for name in VARIANT_SPECS}
            generate_image_variants(folder, include_cover=True)
            self.assertEqual(mtimes, {name: (folder / f"{name}.webp").stat().st_mtime_ns for name in VARIANT_SPECS})


if __name__ == "__main__":
    unittest.main()
