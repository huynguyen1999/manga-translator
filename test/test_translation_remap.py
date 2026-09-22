import unittest
from manga_translator.pipeline.translation_remap import remap_translations
from manga_translator.utils import TextBlock


class TranslationRemapTest(unittest.TestCase):
    def test_exact_1_to_1_mapping(self):
        old_r = TextBlock(
            lines=[[[10, 10], [100, 10], [100, 50], [10, 50]]],
            texts=["こんにちは"],
            translation="Hello",
            region_id="reg-1",
        )
        new_r = TextBlock(
            lines=[[[10, 10], [100, 10], [100, 50], [10, 50]]],
            texts=["こんにちは"],
            region_id="reg-1-new",
            source_region_ids=["reg-1"],
        )

        result = remap_translations([old_r], [new_r])
        self.assertEqual(result.total_new, 1)
        self.assertEqual(result.matched, 1)
        self.assertEqual(result.unmatched, 0)
        self.assertEqual(result.remapped_regions[0].translation, "Hello")
        self.assertEqual(result.remapped_regions[0].translation_remap["status"], "exact")
        self.assertFalse(result.remapped_regions[0].review_required)

    def test_shifted_geometry_mapping(self):
        old_r = TextBlock(
            lines=[[[10, 10], [100, 10], [100, 50], [10, 50]]],
            texts=["こんにちは"],
            translation="Hello world",
            region_id="reg-1",
        )
        # Slightly adjusted coordinates from new detector
        new_r = TextBlock(
            lines=[[[12, 11], [102, 11], [102, 52], [12, 52]]],
            texts=["こんにちは！"],
            region_id="reg-2",
        )

        result = remap_translations([old_r], [new_r])
        self.assertEqual(result.matched, 1)
        self.assertEqual(result.remapped_regions[0].translation, "Hello world")
        self.assertGreater(result.remapped_regions[0].translation_remap["confidence"], 0.70)

    def test_split_1_to_n_handling(self):
        old_r = TextBlock(
            lines=[[[10, 10], [100, 10], [100, 100], [10, 100]]],
            texts=["私は学校に行きます"],
            translation="I am going to school.",
            region_id="reg-old",
        )
        # Split into two lines / regions by new detector
        new_r1 = TextBlock(
            lines=[[[10, 10], [100, 10], [100, 50], [10, 50]]],
            texts=["私は学校に"],
            region_id="reg-new-1",
        )
        new_r2 = TextBlock(
            lines=[[[10, 55], [100, 55], [100, 100], [10, 100]]],
            texts=["行きます"],
            region_id="reg-new-2",
        )

        result = remap_translations([old_r], [new_r1, new_r2])
        self.assertEqual(result.total_new, 2)
        self.assertEqual(result.needs_review, 2)
        # Primary gets translation, secondary requires manual alignment
        primary = next(r for r in result.remapped_regions if r.translation)
        secondary = next(r for r in result.remapped_regions if not r.translation)
        self.assertEqual(primary.translation, "I am going to school.")
        self.assertTrue(primary.review_required)
        self.assertTrue(secondary.review_required)
        self.assertEqual(primary.translation_remap["status"], "ambiguous_split")

    def test_merge_n_to_1_handling(self):
        old_r1 = TextBlock(
            lines=[[[10, 10], [100, 10], [100, 45], [10, 45]]],
            texts=["ライン１"],
            translation="Line one.",
            region_id="reg-1",
        )
        old_r2 = TextBlock(
            lines=[[[10, 50], [100, 50], [100, 90], [10, 90]]],
            texts=["ライン２"],
            translation="Line two.",
            region_id="reg-2",
        )
        # Merged by new merge pass into single text region
        new_merged = TextBlock(
            lines=[[[10, 10], [100, 10], [100, 90], [10, 90]]],
            texts=["ライン１ ライン２"],
            region_id="reg-merged",
        )

        result = remap_translations([old_r1, old_r2], [new_merged])
        self.assertEqual(result.total_new, 1)
        self.assertEqual(result.matched, 1)
        self.assertEqual(result.remapped_regions[0].translation, "Line one.\nLine two.")
        self.assertEqual(result.remapped_regions[0].translation_remap["status"], "merged")
        self.assertCountEqual(result.remapped_regions[0].translation_remap["old_region_ids"], ["reg-1", "reg-2"])
        self.assertTrue(result.remapped_regions[0].review_required)

    def test_unmatched_new_and_removed_old(self):
        old_r = TextBlock(
            lines=[[[10, 10], [50, 10], [50, 50], [10, 50]]],
            texts=["消えた"],
            translation="Disappeared.",
            region_id="reg-disappeared",
        )
        new_r = TextBlock(
            lines=[[[300, 300], [400, 300], [400, 400], [300, 400]]],
            texts=["新しい"],
            region_id="reg-new",
        )

        result = remap_translations([old_r], [new_r])
        self.assertEqual(result.total_new, 1)
        self.assertEqual(result.matched, 0)
        self.assertEqual(result.unmatched, 1)
        self.assertEqual(result.remapped_regions[0].translation, "")
        self.assertEqual(result.remapped_regions[0].translation_remap["status"], "unmatched")
        self.assertTrue(result.remapped_regions[0].review_required)


if __name__ == "__main__":
    unittest.main()
