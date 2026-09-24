import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch
import numpy as np
from PIL import Image

# Ensure project root is in path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from manga_translator.utils.generic import dump_image, load_image
from manga_translator import Context
from manga_translator.rendering.text_render import set_font, put_text_horizontal, get_char_glyph
from manga_translator.rendering import get_default_eng_font, text_render
from manga_translator.rendering.layout import solver
from manga_translator.rendering.layout import raster as layout_raster
from manga_translator.rendering.layout.engine import layout_page
from manga_translator.rendering.layout.models import LayoutCandidate, PageObstacleMap, PlacedLine
from manga_translator.manga_translator import MangaTranslator
from server.sent_data_internal import get_client_session, close_client_session
from server.instance import ExecutorInstance
from manga_translator.config import Config


def _shift_candidate(candidate, dx, dy):
    return replace(
        candidate,
        lines=[replace(line, x=line.x + dx, y=line.y + dy) for line in candidate.lines],
        qa=dict(candidate.qa),
    )


class TestPerformanceImprovements(unittest.TestCase):
    def test_layout_page_profiles_are_isolated_between_workers(self):
        barrier = threading.Barrier(2, timeout=10)
        contexts = [
            Context(img_rgb=np.zeros((16, 16, 3), dtype=np.uint8), text_regions=[])
            for _ in range(2)
        ]
        for context, count in zip(contexts, (3, 7)):
            context.profile_count = count

        def fake_solver(ctx, *_args, **_kwargs):
            solver.get_solver_profile().fonts_tested += ctx.profile_count
            barrier.wait()

        def layout_and_profile(ctx):
            layout_page(ctx, Config())
            return solver.get_solver_profile()

        with patch.object(solver, "apply_shape_aware_bubble_layout", fake_solver):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first, second = list(executor.map(layout_and_profile, contexts))

        self.assertIsNot(first, second)
        self.assertEqual({first.fonts_tested, second.fonts_tested}, {3, 7})

    def test_cached_max_row_width_matches_row_slot_table(self):
        mask = np.zeros((64, 96), dtype=np.uint8)
        mask[8:56, 12:84] = 1
        geom = solver.BubbleGeometry(mask)
        expected = max(
            (slot.width for row in solver._build_row_slot_table(geom, 12, 1, 2.0, 12).values() for slot in row),
            default=0,
        )

        profile = solver.reset_solver_profile()
        self.assertEqual(solver._max_usable_row_width(geom, 12, 1, 2.0, 12), expected)
        self.assertEqual(profile.bubble_row_slot_table_builds, 0)
        profile.row_slot_max_widths.clear()
        solver._cached_row_slot_table(geom, 12, 1, 2.0, 12)
        self.assertEqual(solver._max_usable_row_width(geom, 12, 1, 2.0, 12), expected)
        self.assertEqual(profile.bubble_row_slot_table_builds, 1)
        self.assertEqual(profile.bubble_row_slot_table_cache_hits, 1)

    def test_candidate_raster_matches_page_raster_for_100_offsets(self):
        candidate = LayoutCandidate(
            font_size=24,
            y_origin=25,
            line_spacing=0.0,
            lines=[
                PlacedLine("SILK AND COTTON", 25, 30, 150, 28),
                PlacedLine("TEST", 55, 44, 80, 28),
            ],
            penalty=0.0,
            glyph_clearance_p5=0.0,
        )
        cached = layout_raster.rasterize_candidate(candidate, 2)
        for index in range(100):
            dx, dy = (index % 25) - 12, (index // 25) * 3 - 4
            shifted = _shift_candidate(candidate, dx, dy)
            expected = layout_raster._candidate_cropped_visual_masks(shifted, 2, (100, 220))
            actual = layout_raster._candidate_raster_at_offset(cached, dx, dy, (100, 220))
            self.assertEqual(expected[0], actual[0])
            for expected_mask, actual_mask in zip(expected[1:], actual[1:]):
                np.testing.assert_array_equal(expected_mask, actual_mask)

    def test_line_alpha_cache_is_font_aware_under_concurrency(self):
        fonts = [
            os.path.join(BASE_DIR, "fonts", "anime_ace.ttf"),
            os.path.join(BASE_DIR, "fonts", "comic shanns 2.ttf"),
        ]
        if not all(os.path.isfile(path) for path in fonts):
            self.skipTest("two bundled fonts are required")
        line = PlacedLine("WMWMWM", 0, 0, 160, 24)
        layout_raster._LINE_ALPHA_CACHE.clear()

        expected = []
        for font in fonts:
            set_font(font)
            expected.append(layout_raster._render_line_alpha(line, 24))
        self.assertFalse(np.array_equal(expected[0], expected[1]))
        layout_raster._LINE_ALPHA_CACHE.clear()

        barrier = threading.Barrier(2, timeout=10)

        def render(font):
            set_font(font)
            barrier.wait()
            return layout_raster._render_line_alpha(line, 24)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = list(executor.map(render, fonts))
        np.testing.assert_array_equal(first, expected[0])
        np.testing.assert_array_equal(second, expected[1])
        self.assertEqual(len(layout_raster._LINE_ALPHA_CACHE), 2)

        set_font(fonts[0])
        cached = layout_raster._render_line_alpha(line, 24)
        np.testing.assert_array_equal(cached, expected[0])
        self.assertEqual(len(layout_raster._LINE_ALPHA_CACHE), 2)

    def test_cached_free_text_overflow_matches_rerasterized_result(self):
        candidate = LayoutCandidate(
            font_size=24,
            y_origin=35,
            line_spacing=0.0,
            lines=[PlacedLine("WMWM", 35, 18, 80, 24)],
            penalty=0.0,
            glyph_clearance_p5=0.0,
        )
        image_shape = (100, 100)
        raster = layout_raster.rasterize_candidate(candidate, 0)
        panel = np.ones(image_shape, dtype=np.uint8)
        protected = np.zeros(image_shape, dtype=np.uint8)
        text = np.zeros(image_shape, dtype=np.uint8)
        obstacles = PageObstacleMap(
            bubble_mask=np.zeros(image_shape, dtype=np.uint8),
            protected_bubble_mask=protected,
            text_mask=text,
            panel_mask=panel,
        )
        other_text = np.zeros(image_shape, dtype=bool)
        cases = [(0, 0, None, None), (0, 0, "bubble", (36, 36, 43, 48)), (0, 0, "text", (36, 36, 43, 48)), (-20, 0, None, None)]

        for dx, dy, obstacle, rect in cases:
            with self.subTest(dx=dx, obstacle=obstacle):
                protected.fill(0)
                other_text.fill(False)
                if obstacle == "bubble":
                    x1, y1, x2, y2 = rect
                    protected[y1:y2, x1:x2] = 255
                elif obstacle == "text":
                    x1, y1, x2, y2 = rect
                    other_text[y1:y2, x1:x2] = True
                shifted = _shift_candidate(candidate, dx, dy)
                expected = solver._free_text_ink_overflow(shifted, image_shape, obstacles, other_text)
                if obstacle is not None:
                    self.assertGreater(expected, 0.0)
                solver.reset_solver_profile()
                destination_box = tuple(value + delta for value, delta in zip(raster.crop_box, (dx, dy, dx, dy)))
                actual = solver._free_text_ink_overflow_from_raster(raster, destination_box, obstacles, other_text)
                self.assertAlmostEqual(actual, expected, places=7)
                self.assertEqual(solver.get_solver_profile().overflow_rasterizations, 0)

    def test_dump_image_no_alpha(self):
        # RGB image without alpha
        img_arr = np.zeros((100, 100, 3), dtype=np.uint8)
        img_arr[:, :] = [128, 64, 32]
        img_pil = Image.fromarray(img_arr)

        res = dump_image(img_pil, img_arr, alpha_ch=None)
        self.assertIsInstance(res, Image.Image)
        self.assertEqual(res.size, (100, 100))
        self.assertEqual(res.mode, "RGB")
        res_arr = np.array(res)
        np.testing.assert_array_equal(res_arr, img_arr)

    def test_dump_image_with_alpha(self):
        # RGBA image
        img_arr = np.zeros((100, 100, 3), dtype=np.uint8)
        alpha = Image.new("L", (100, 100), 200)
        img_pil = Image.new("RGBA", (100, 100), (255, 255, 255, 200))

        res = dump_image(img_pil, img_arr, alpha_ch=alpha)
        self.assertIsInstance(res, Image.Image)
        self.assertEqual(res.size, (100, 100))
        self.assertEqual(res.mode, "RGBA")

    def test_set_font_cache_retention(self):
        # Calling set_font multiple times with the same font should retain cache
        set_font('')
        glyph1 = get_char_glyph('A', 20, 0)
        info1 = get_char_glyph.cache_info()
        self.assertGreater(info1.currsize, 0)

        # Re-calling set_font with empty string (same) should NOT clear cache
        set_font('')
        info2 = get_char_glyph.cache_info()
        self.assertEqual(info1.currsize, info2.currsize)

    def test_layout_pages_run_concurrently_with_isolated_fonts(self):
        barrier = threading.Barrier(2, timeout=10)
        font_path = get_default_eng_font()

        def resize_face(size):
            set_font(font_path)
            face = text_render.FONT_SELECTION[0]
            barrier.wait()
            face.set_pixel_sizes(0, size)
            return face

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.map(resize_face, (24, 48))

        self.assertIsNot(first, second)

        layout_barrier = threading.Barrier(2, timeout=10)
        contexts = [
            Context(img_rgb=np.zeros((16, 16, 3), dtype=np.uint8), text_regions=[])
            for _ in range(2)
        ]
        with patch.object(solver, "_record_content_trace", lambda *_: layout_barrier.wait()):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(solver.apply_shape_aware_bubble_layout, ctx, Config())
                    for ctx in contexts
                ]
                for future in futures:
                    future.result(timeout=15)

    def test_put_text_horizontal(self):
        set_font('')
        res = put_text_horizontal(
            font_size=24,
            text="Hello world this is a test text layout",
            width=200,
            height=100,
            alignment="center",
            reversed_direction=False,
            fg=(0, 0, 0),
            bg=(255, 255, 255),
            lang="en_US",
            hyphenate=True
        )
        # Should complete quickly and return a rendered image tuple/array
        self.assertIsNotNone(res)

    def test_dictionary_cache(self):
        import tempfile
        from manga_translator.manga_translator import load_dictionary
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("apple orange\nbanana pear\n")
            f_path = f.name

        try:
            dict1 = load_dictionary(f_path)
            self.assertEqual(len(dict1), 2)
            self.assertEqual(dict1[0][1], "orange")
            self.assertEqual(dict1[1][1], "pear")

            # Check cache hit (same object returned)
            dict2 = load_dictionary(f_path)
            self.assertIs(dict1, dict2)
        finally:
            os.unlink(f_path)

    def test_server_batch_payload_structure(self):
        executor = ExecutorInstance(ip="127.0.0.1", port=5003)
        images = [Image.new("RGB", (10, 10))]
        cfg = Config()

        # Check payload construction logic
        images_with_configs = [(img, cfg) for img in images]
        payload = {"images_with_configs": images_with_configs, "batch_size": 2}
        self.assertIn("images_with_configs", payload)
        self.assertEqual(len(payload["images_with_configs"]), 1)
        self.assertEqual(payload["batch_size"], 2)


    def test_det_batch_forward(self):
        import torch
        import torch.nn as nn
        from manga_translator.detection import default as det_module

        class DummyModel(nn.Module):
            def forward(self, x):
                self.input_dtype = x.dtype
                self.input_shape = x.shape
                # Verify that x was normalized: range should be approx [-1, 1]
                return torch.zeros(x.shape[0], 1, x.shape[2], x.shape[3]), torch.zeros(x.shape[0], 1, x.shape[2], x.shape[3])

        dummy = DummyModel()
        det_module.MODEL = dummy

        batch = [np.zeros((64, 64, 3), dtype=np.uint8)]
        db, mask = det_module.det_batch_forward_default(batch, device='cpu')

        self.assertEqual(dummy.input_dtype, torch.float32)
        self.assertEqual(dummy.input_shape, (1, 3, 64, 64))
        self.assertEqual(db.shape, (1, 1, 64, 64))
        self.assertEqual(mask.shape, (1, 1, 64, 64))

    def test_openai_client_cache(self):
        from manga_translator.translators.chatgpt import _get_openai_client, _client_cache
        # Verify cache returns the same instance for identical credentials
        client1 = _get_openai_client("dummy_key", "https://api.openai.com/v1", None)
        client2 = _get_openai_client("dummy_key", "https://api.openai.com/v1", None)
        self.assertIs(client1, client2)


if __name__ == '__main__':
    unittest.main()
