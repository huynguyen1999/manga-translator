import os
import sys
import unittest
import asyncio
import numpy as np
import cv2
from PIL import ImageFont
from shapely.geometry import Polygon
from shapely import affinity
from unittest.mock import AsyncMock

# Add repository root to sys.path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from manga_translator.utils import TextBlock, rotate_polygons
from manga_translator.rendering import text_render
from manga_translator.rendering import resize_regions_to_font_size, render, render_page
from manga_translator.rendering.ballon_extractor import safe_ballon_bounds
from manga_translator.config import Config, Renderer
from manga_translator.rendering.text_render_pillow_eng import merge_seg_eng

class TestRenderMitigations(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        font_file = os.path.join(repo_root, 'fonts', 'comic shanns 2.ttf')
        text_render.set_font(font_file)

    def test_calc_horizontal_strict_width(self):
        """Verify that calc_horizontal with allow_width_expansion=False does not exceed max_width."""
        text = "This is a long sentence that would normally trigger height overflow and width expansion."
        max_width = 150
        max_height = 50
        font_size = 24
        
        # When expansion is disallowed:
        lines, widths = text_render.calc_horizontal(
            font_size, text, max_width, max_height, allow_width_expansion=False
        )
        for w in widths:
            self.assertLessEqual(w, max_width + font_size, f"Line width {w} exceeded max_width {max_width}")

    def test_horizontal_wrap_hyphenates_and_honors_newlines(self):
        lines, _ = text_render.calc_horizontal(
            24,
            "Sales Department: New Graduate Hire\nI'll have you all the way tonight.",
            60,
            500,
            hyphenate=True,
            allow_width_expansion=False,
        )
        self.assertIn("-", "".join(lines), "Overlong words must show a visible hyphen")
        first_break = next(i for i, line in enumerate(lines) if line.startswith("I"))
        self.assertTrue(any(line.startswith("Sales") for line in lines[:first_break]))
        self.assertTrue(any(line.startswith("I'll") for line in lines[first_break:]))

    def test_pillow_wrap_hyphenates_overlong_words(self):
        font = ImageFont.truetype(os.path.join(repo_root, 'fonts', 'comic shanns 2.ttf'), 24)
        lines = merge_seg_eng("Pneumonoultramicroscopicsilicovolcanoconiosis", font, 70)
        self.assertTrue(any(line.endswith("-") for line in lines))
        self.assertGreater(len(lines), 1)

    def test_resize_regions_center_origin(self):
        """Verify that resize_regions_to_font_size keeps the center symmetric when expanding."""
        img = np.zeros((1000, 1000, 3), dtype=np.uint8)
        # Box from x: 400..600, y: 400..500. Center is (500, 450)
        lines = [[[400, 400], [600, 400], [600, 500], [400, 500]]]
        block = TextBlock(
            lines=lines,
            texts=["short"],
            translation="This is a much longer translated text that requires multiple rows of text.",
            font_size=20,
            direction="h"
        )
        block.target_lang = "ENG"
        orig_center = block.center.copy()
        
        dst_points_list = resize_regions_to_font_size(img, [block], font_size_fixed=None, font_size_offset=0, font_size_minimum=10)
        dst_points = dst_points_list[0]
        
        # Calculate new center from dst_points
        new_center = dst_points[0].mean(axis=0)
        # Check that center remained close (within 1.5 pixel rounding)
        np.testing.assert_allclose(new_center, orig_center, atol=1.5, err_msg="Center shifted after expansion!")

    def test_resize_regions_boundary_clipping(self):
        """Verify that dst_points do not exceed image boundaries even with large text expansion."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        # Near edge: x: 180..200
        lines = [[[180, 10], [199, 10], [199, 50], [180, 50]]]
        block = TextBlock(
            lines=lines,
            texts=["a"],
            translation="Super long translation that expands very significantly beyond the image frame.",
            font_size=30,
            direction="h"
        )
        block.target_lang = "ENG"
        
        dst_points_list = resize_regions_to_font_size(img, [block], font_size_fixed=None, font_size_offset=0, font_size_minimum=10)
        dst_points = dst_points_list[0]
        
        self.assertTrue(np.all(dst_points[..., 0] >= 0))
        self.assertTrue(np.all(dst_points[..., 0] < 200))
        self.assertTrue(np.all(dst_points[..., 1] >= 0))
        self.assertTrue(np.all(dst_points[..., 1] < 200))

    def test_horizontal_center_padding_in_render(self):
        """Verify that render() pads symmetrically when region.alignment == 'center'."""
        img = np.zeros((500, 500, 3), dtype=np.uint8)
        # Wide bounding box (r_orig = 300 / 100 = 3.0)
        lines = [[[100, 100], [400, 100], [400, 200], [100, 200]]]
        block = TextBlock(
            lines=lines,
            texts=["test"],
            translation="Hi",
            font_size=20,
            direction="h",
            alignment="center"
        )
        block.target_lang = "ENG"
        block.set_font_colors([255, 255, 255], [0, 0, 0])
        dst_points = np.array(lines).reshape(-1, 4, 2)
        
        rendered = render(img.copy(), block, dst_points, hyphenate=False, line_spacing=0, disable_font_border=False)
        # Check rendered image inside the box [100:200, 100:400]
        sub = rendered[100:200, 100:400]
        # Find where text is drawn (non-zero)
        coords = np.argwhere(sub.sum(axis=-1) > 0)
        self.assertGreater(len(coords), 0, "No text was rendered")
        min_x = coords[:, 1].min()
        max_x = coords[:, 1].max()
        
        left_margin = min_x
        right_margin = 300 - max_x
        # For centered text, left_margin and right_margin should be balanced
        ratio = left_margin / max(right_margin, 1)
        self.assertGreater(left_margin, 20, "Text is flush left instead of centered!")
        self.assertGreater(right_margin, 20, "Text is flush right instead of centered!")
        self.assertAlmostEqual(ratio, 1.0, delta=0.5, msg=f"Margins unbalanced: left={left_margin}, right={right_margin}")

    def test_put_text_horizontal_expands_at_readable_floor(self):
        """Verify that crowded text expands its canvas instead of being clipped."""
        text = "This is a sentence that must wrap into multiple lines to fill the bubble without overflowing."
        target_w = 140
        target_h = 130
        box = text_render.put_text_horizontal(
            font_size=32,
            text=text,
            width=target_w,
            height=target_h,
            alignment='center',
            reversed_direction=False,
            fg=(0, 0, 0),
            bg=(255, 255, 255),
            lang='en_US',
            hyphenate=True,
            line_spacing=0,
            font_size_minimum=10
        )
        self.assertIsNotNone(box)
        h, w = box.shape[:2]
        self.assertTrue(w > target_w or h > target_h)
        self.assertGreater(np.count_nonzero(box[:, :, 3]), 0)

    def test_horizontal_short_text_keeps_native_size_padding(self):
        box = text_render.put_text_horizontal(
            font_size=32,
            text="Hi",
            width=300,
            height=100,
            alignment='center',
            reversed_direction=False,
            fg=(0, 0, 0),
            bg=(255, 255, 255),
            lang='en_US',
            hyphenate=False,
            line_spacing=0,
        )
        self.assertEqual(box.shape[:2], (100, 300))
        ys, xs = np.where(box[:, :, 3] > 0)
        self.assertLessEqual(xs.max() - xs.min() + 1, 50)
        self.assertLessEqual(ys.max() - ys.min() + 1, 40)

    def test_horizontal_disabled_border_keeps_transparent_padding(self):
        box = text_render.put_text_horizontal(
            font_size=24,
            text="Hi",
            width=120,
            height=60,
            alignment='center',
            reversed_direction=False,
            fg=(0, 0, 0),
            bg=None,
            lang='en_US',
            hyphenate=False,
            line_spacing=0,
        )
        self.assertEqual(box.shape[:2], (60, 120))
        self.assertEqual(int(box[0, 0, 3]), 0)
        self.assertGreater(np.count_nonzero(box[:, :, 3]), 0)

    def test_vertical_bubble_detection_and_safety_margin(self):
        """Verify that bubble detection finds the speech bubble and applies inner safety padding."""
        img = np.full((600, 600, 3), 128, dtype=np.uint8)
        # Speech bubble: ellipse centered at (300, 300) with rx=100 (x: 200..400) and ry=180 (y: 120..480)
        cv2.ellipse(img, (300, 300), (100, 180), 0, 0, 360, (0, 0, 0), 4)
        cv2.ellipse(img, (300, 300), (96, 176), 0, 0, 360, (255, 255, 255), -1)

        # Narrow Japanese vertical dialogue inside bubble
        lines = [[[285, 180], [315, 180], [315, 420], [285, 420]]]
        block = TextBlock(
            lines=lines,
            texts=["あいうえお"],
            translation="What are you doing here right now?",
            font_size=24,
            direction="h"
        )
        block.target_lang = "ENG"

        dst_points_list = resize_regions_to_font_size(img, [block], font_size_fixed=None, font_size_offset=0, font_size_minimum=10)
        dst_points = dst_points_list[0]

        min_x = dst_points[..., 0].min()
        max_x = dst_points[..., 0].max()
        min_y = dst_points[..., 1].min()
        max_y = dst_points[..., 1].max()

        # Check that the box expanded beyond the initial 30px strip
        self.assertGreater(max_x - min_x, 50, "Box failed to expand into speech bubble!")
        # Check that the box stays safely inside the bubble boundary (x: 200..400, y: 120..480) with safety margins
        self.assertGreaterEqual(min_x, 200, f"Box spilled outside left bubble edge: {min_x} < 200")
        self.assertLessEqual(max_x, 400, f"Box spilled outside right bubble edge: {max_x} > 400")
        self.assertGreaterEqual(min_y, 120, f"Box spilled outside top bubble edge: {min_y} < 120")
        self.assertLessEqual(max_y, 480, f"Box spilled outside bottom bubble edge: {max_y} > 480")

    def test_safe_ballon_bounds_are_inset_and_clamped(self):
        img = np.full((120, 180, 3), 128, dtype=np.uint8)
        cv2.ellipse(img, (90, 60), (70, 45), 0, 0, 360, (0, 0, 0), 4)
        cv2.ellipse(img, (90, 60), (66, 41), 0, 0, 360, (255, 255, 255), -1)

        bounds = safe_ballon_bounds(img, [82, 48, 16, 24])
        self.assertIsNotNone(bounds)
        x1, y1, x2, y2 = bounds
        self.assertGreaterEqual(x1, 0)
        self.assertGreaterEqual(y1, 0)
        self.assertLessEqual(x2, img.shape[1])
        self.assertLessEqual(y2, img.shape[0])
        self.assertGreater(x2 - x1, 16)
        self.assertGreater(y2 - y1, 24)

    def test_aspect_ratio_fallback_multi_line_wrapping(self):
        """Verify that proportional aspect-ratio fallback expands width reasonably and does not blow up."""
        img = np.full((600, 600, 3), 200, dtype=np.uint8)
        # Narrow vertical strip without a distinct bubble boundary
        lines = [[[285, 100], [315, 100], [315, 400], [285, 400]]]
        block = TextBlock(
            lines=lines,
            texts=["あいうえお"],
            translation="We need to find a place to stay tonight before it gets dark.",
            font_size=20,
            direction="h"
        )
        block.target_lang = "ENG"

        dst_points_list = resize_regions_to_font_size(img, [block], font_size_fixed=None, font_size_offset=0, font_size_minimum=10)
        dst_points = dst_points_list[0]

        w = dst_points[..., 0].max() - dst_points[..., 0].min()
        h = dst_points[..., 1].max() - dst_points[..., 1].min()

        # Height was 300. With ~0.85 ratio, target width is ~255.
        self.assertGreater(w, 100, "Width did not expand to natural bubble proportion")
        self.assertLessEqual(w, 320, f"Width expanded too aggressively: {w} > 320")

    def test_constrained_horizontal_placement_avoids_crash(self):
        """Verify that tight obstacles or narrow spaces do not cause ValueError or crash."""
        img = np.zeros((1000, 1000, 3), dtype=np.uint8)
        block = TextBlock(
            lines=[[[100, 100], [130, 100], [130, 250], [100, 250]]],
            texts=['秋奈やってみて'],
            translation='Akina, give it a try.',
            font_size=20,
            direction='h'
        )
        block.target_lang = 'ENG'
        obs1 = TextBlock(
            lines=[[[70, 100], [98, 100], [98, 250], [70, 250]]],
            texts=['test1'], translation='test1', font_size=20, direction='h'
        )
        obs1.target_lang = 'ENG'
        obs2 = TextBlock(
            lines=[[[132, 100], [200, 100], [200, 250], [132, 250]]],
            texts=['test2'], translation='test2', font_size=20, direction='h'
        )
        obs2.target_lang = 'ENG'

        # Should not raise ValueError
        dst_points_list = resize_regions_to_font_size(
            img, [block, obs1, obs2], font_size_fixed=None, font_size_offset=0, font_size_minimum=10
        )
        self.assertEqual(len(dst_points_list), 3)
        self.assertGreater(block.font_size, 0)

    def test_overlapping_title_regions_are_flagged_and_rendered_with_fallback(self):
        img = np.full((500, 500, 3), 255, np.uint8)
        regions = [
            TextBlock(
                lines=[[[80, 60], [130, 60], [130, 360], [80, 360]]],
                texts=["large title"],
                translation="I will teach you everything",
                font_size=72,
                direction="h",
                target_lang="ENG",
            ),
            TextBlock(
                lines=[[[115, 80], [165, 80], [165, 380], [115, 380]]],
                texts=["overlapping title"],
                translation="You cannot escape",
                font_size=72,
                direction="h",
                target_lang="ENG",
            ),
        ]

        resize_regions_to_font_size(
            img, regions, font_size_fixed=None, font_size_offset=0, font_size_minimum=10
        )

        self.assertFalse(any(getattr(region, "_render_suppressed", False) for region in regions))
        self.assertTrue(all(getattr(region, "review_required", False) for region in regions))
        self.assertTrue(all(region.review_reason == "text_does_not_fit" for region in regions))

    def test_flagged_title_regions_render_on_output_and_flag_review(self):
        import asyncio
        from manga_translator.config import Config
        from manga_translator.manga_translator import MangaTranslator
        from manga_translator.utils import Context

        original = np.full((500, 500, 3), 255, np.uint8)
        cv2.rectangle(original, (80, 60), (165, 380), (0, 0, 0), -1)
        regions = [
            TextBlock(
                lines=[[[80, 60], [130, 60], [130, 360], [80, 360]]],
                texts=["large title"], translation="I will teach you everything",
                font_size=72, direction="h", target_lang="ENG",
            ),
            TextBlock(
                lines=[[[115, 80], [165, 80], [165, 380], [115, 380]]],
                texts=["overlapping title"], translation="You cannot escape",
                font_size=72, direction="h", target_lang="ENG",
            ),
        ]
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.font_path = os.path.join(repo_root, 'fonts', 'comic shanns 2.ttf')
        translator._model_usage_timestamps = {}
        ctx = Context(
            img_rgb=original,
            img_inpainted=np.full_like(original, 255),
            text_regions=regions,
            render_mask=None,
            _bubble_layout_ready=True,
        )

        output = asyncio.run(translator._run_text_rendering(Config(), ctx))

        selected = np.zeros(original.shape[:2], np.uint8)
        for region in regions:
            cv2.fillPoly(selected, [np.asarray(region.lines[0], np.int32)], 1)
        # Rendered output contains rendered text pixels and differs from original
        self.assertFalse(np.array_equal(output[selected > 0], original[selected > 0]))
        self.assertTrue(all(getattr(region, "review_required", False) for region in regions))

    def _render_lifecycle_fixture(self, region):
        original = np.zeros((40, 40, 3), dtype=np.uint8)
        original[10:30, 10:30] = (0, 0, 255)
        inpainted = np.full_like(original, 255)
        ctx = type("RenderContext", (), {})()
        ctx.img_rgb = original
        ctx.img_inpainted = inpainted
        ctx.text_regions = [region]
        ctx.render_mask = None
        config = Config()
        config.render.renderer = Renderer.default
        return ctx, config, original

    def _lifecycle_region(self, translation="TRANSLATED", region_id="lifecycle"):
        return TextBlock(
            lines=[[[10, 10], [30, 10], [30, 30], [10, 30]]],
            texts=["原文"],
            translation=translation,
            font_size=12,
            target_lang="ENG",
            region_id=region_id,
        )

    def test_render_page_restores_suppressed_free_text_without_dispatching_it(self):
        region = self._lifecycle_region()
        region._render_suppressed = True
        ctx, config, original = self._render_lifecycle_fixture(region)
        draw = AsyncMock(side_effect=lambda canvas, regions, *args, **kwargs: canvas)

        with unittest.mock.patch("manga_translator.rendering.dispatch", draw):
            output = asyncio.run(render_page(ctx, config))

        draw.assert_awaited_once()
        self.assertEqual(draw.await_args.args[1], [])
        np.testing.assert_array_equal(output[10:30, 10:30], original[10:30, 10:30])

    def test_render_page_keeps_suppressed_bubble_source_and_never_draws_translation(self):
        region = self._lifecycle_region(region_id="suppressed-bubble")
        region._render_suppressed = True
        region._bubble_restore = np.zeros((40, 40), dtype=np.uint8)
        region._bubble_restore[10:30, 10:30] = 1
        ctx, config, original = self._render_lifecycle_fixture(region)
        draw = AsyncMock(side_effect=lambda canvas, regions, *args, **kwargs: canvas)

        with unittest.mock.patch("manga_translator.rendering.dispatch", draw):
            output = asyncio.run(render_page(ctx, config))

        self.assertEqual(draw.await_args.args[1], [])
        np.testing.assert_array_equal(output[10:30, 10:30], original[10:30, 10:30])

    def test_render_page_still_draws_review_required_legacy_fallback(self):
        region = self._lifecycle_region(region_id="review-fallback")
        region.review_required = True
        region._render_suppressed = False
        ctx, config, _original = self._render_lifecycle_fixture(region)

        async def draw(canvas, regions, *args, **kwargs):
            self.assertEqual(regions, [region])
            canvas[15, 15] = (0, 255, 0)
            return canvas

        with unittest.mock.patch("manga_translator.rendering.dispatch", AsyncMock(side_effect=draw)) as mocked:
            output = asyncio.run(render_page(ctx, config))

        self.assertEqual(mocked.await_count, 1)
        np.testing.assert_array_equal(output[15, 15], (0, 255, 0))

    def test_render_page_restores_untranslated_review_region(self):
        region = self._lifecycle_region(translation="", region_id="untranslated-review")
        region.review_required = True
        ctx, config, original = self._render_lifecycle_fixture(region)
        draw = AsyncMock(side_effect=lambda canvas, regions, *args, **kwargs: canvas)

        with unittest.mock.patch("manga_translator.rendering.dispatch", draw):
            output = asyncio.run(render_page(ctx, config))

        self.assertEqual(draw.await_args.args[1], [])
        np.testing.assert_array_equal(output[10:30, 10:30], original[10:30, 10:30])

    def test_render_page_renders_frozen_free_text_once(self):
        region = self._lifecycle_region(region_id="frozen-free-text")
        region._layout_frozen = True
        region.layout_segments = [{"x": 10, "y": 10, "width": 20, "height": 20, "lines": []}]
        ctx, config, _original = self._render_lifecycle_fixture(region)
        dispatch_mock = AsyncMock(side_effect=lambda canvas, regions, *args, **kwargs: canvas)
        frozen_calls = []

        def draw_frozen(canvas, rendered_region, _font):
            frozen_calls.append(rendered_region)
            canvas[15, 15] = (255, 0, 0)
            return canvas

        with (
            unittest.mock.patch("manga_translator.rendering.dispatch", dispatch_mock),
            unittest.mock.patch("manga_translator.rendering._render_frozen_region", draw_frozen),
        ):
            output = asyncio.run(render_page(ctx, config))

        self.assertEqual(dispatch_mock.await_args.args[1], [])
        self.assertEqual(frozen_calls, [region])
        np.testing.assert_array_equal(output[15, 15], (255, 0, 0))

    def test_speech_bubble_blank_space_utilization_and_ellipse_containment(self):
        """Verify that text rendered into a tall speech bubble makes good use of blank space and fits elliptical bounds."""
        w, h = 180, 276
        text = "It was made of rubber, but now it's just my mouth."
        box = text_render.put_text_horizontal(
            font_size=24,
            text=text,
            width=w,
            height=h,
            alignment='center',
            reversed_direction=False,
            fg=(0, 0, 0),
            bg=(255, 255, 255),
            lang='en_US',
            hyphenate=True,
            line_spacing=0,
        )
        self.assertEqual(box.shape[:2], (h, w))
        ys, xs = np.where(box[:, :, 3] > 0)
        self.assertGreater(len(xs), 0, "No text pixels rendered")

        text_w = xs.max() - xs.min() + 1
        text_h = ys.max() - ys.min() + 1

        # Blank space utilization: text should utilize >50% of the bubble height (previously ~39%)
        height_utilization = text_h / float(h)
        self.assertGreater(height_utilization, 0.50, f"Text failed to utilize vertical bubble space: {height_utilization:.1%}")

        # Elliptical containment: all rendered text pixels should lie within the bubble ellipse
        cx, cy = w / 2.0, h / 2.0
        norm_dist = ((xs - cx) / (w / 2.0))**2 + ((ys - cy) / (h / 2.0))**2
        max_dist = norm_dist.max()
        self.assertLessEqual(max_dist, 1.0, f"Rendered text pixels spilled outside ellipse: {max_dist:.3f} > 1.0")

    def test_balloon_extractor_vertical_strip_enlargement(self):
        """Verify that enlarge_window expands horizontally sufficiently for vertical text."""
        from manga_translator.rendering.ballon_extractor import enlarge_window
        rect = [595, 100, 625, 320]  # w=30, h=220
        win = enlarge_window(rect, 1000, 1000, ratio=1.8, aspect_ratio=220/30)
        win_w = win[2] - win[0]
        self.assertGreaterEqual(win_w, 150, f"Window width {win_w} too narrow for vertical bubble detection")
        # Ensure symmetric expansion
        self.assertLess(win[0], 595)
        self.assertGreater(win[2], 625)

    def test_speech_bubble_obstacle_does_not_repel_text_outside_bubble(self):
        """Verify that an adjacent obstacle (e.g. SFX) does not repel text out of its speech bubble."""
        img = np.full((600, 600, 3), 128, dtype=np.uint8)
        # Bubble at center (300, 300) with rx=90, ry=150 -> x: 210..390, y: 150..450
        cv2.ellipse(img, (300, 300), (90, 150), 0, 0, 360, (0, 0, 0), 4)
        cv2.ellipse(img, (300, 300), (86, 146), 0, 0, 360, (255, 255, 255), -1)

        bubble_block = TextBlock(
            lines=[[[285, 180], [315, 180], [315, 420], [285, 420]]],
            texts=["いい景色だなあ"],
            translation="The scenery here is nice, so I'd like to come next!",
            font_size=20,
            direction="h"
        )
        bubble_block.target_lang = "ENG"

        # Adjacent SFX obstacle immediately to the right of the bubble
        sfx_block = TextBlock(
            lines=[[[400, 160], [430, 160], [430, 300], [400, 300]]],
            texts=["ああああ"],
            translation="Ahhh~",
            font_size=20,
            direction="h"
        )
        sfx_block.target_lang = "ENG"

        dst_points_list = resize_regions_to_font_size(
            img, [bubble_block, sfx_block], font_size_fixed=None, font_size_offset=0, font_size_minimum=10
        )
        bubble_pts = dst_points_list[0]
        min_x = bubble_pts[..., 0].min()
        max_x = bubble_pts[..., 0].max()

        # The bubble is x: 210..390. The text MUST remain strictly within x: 210..390
        # and NOT get repelled to the left (< 210) to avoid the SFX on the right!
        self.assertGreaterEqual(min_x, 210, f"Text was pushed left outside the bubble: {min_x} < 210")
        self.assertLessEqual(max_x, 390, f"Text spilled outside right bubble edge: {max_x} > 390")

    def test_tall_narrow_bubble_adaptive_wrapping(self):
        """Verify that text in a tall vertical bubble wraps adaptively into multiple narrow lines."""
        text = "THE SCENERY HERE IS NICE, SO I'D LIKE TO COME NEXT! ♡"
        w, h = 120, 260
        box = text_render.put_text_horizontal(
            font_size=24,
            text=text,
            width=w,
            height=h,
            alignment='center',
            reversed_direction=False,
            fg=(0, 0, 0),
            bg=(255, 255, 255),
            lang='en_US',
            hyphenate=True,
            line_spacing=0,
            font_size_minimum=10
        )
        self.assertIsNotNone(box)
        ys, xs = np.where(box[:, :, 3] > 0)
        self.assertGreater(len(xs), 0)
        # All pixels must lie within the ellipse
        cx, cy = w / 2.0, h / 2.0
        norm_dist = ((xs - cx) / (w / 2.0))**2 + ((ys - cy) / (h / 2.0))**2
        self.assertLessEqual(norm_dist.max(), 1.0)
        # Vertical space utilization should be substantial
        text_h = ys.max() - ys.min() + 1
        self.assertGreater(text_h / float(h), 0.50)

    def test_bubble_detection_with_boundary_touching_tail(self):
        """Verify that a speech bubble with a pointy tail touching the crop window border is not rejected as an unbordered leak."""
        from manga_translator.rendering import _detect_bubble_rect
        img = np.full((600, 600, 3), 128, dtype=np.uint8)
        # Create white bubble centered at (310, 300) with a tail pointing left touching x=200
        cv2.ellipse(img, (310, 300), (90, 150), 0, 0, 360, (0, 0, 0), 4)
        cv2.ellipse(img, (310, 300), (86, 146), 0, 0, 360, (255, 255, 255), -1)
        tail_pts = np.array([[240, 290], [200, 300], [240, 310]], dtype=np.int32)
        cv2.fillPoly(img, [tail_pts], (255, 255, 255))
        cv2.polylines(img, [tail_pts], False, (0, 0, 0), 2)

        block = TextBlock(
            lines=[[[295, 200], [325, 200], [325, 400], [295, 400]]],
            texts=['テキスト'],
            translation='Testing tail detection',
            font_size=20,
            direction='h'
        )
        block.target_lang = 'ENG'

        base_rect = [295, 200, 325, 400]
        bubble_rect = _detect_bubble_rect(img, block, base_rect, [block])
        self.assertNotEqual(bubble_rect, base_rect, "Failed to detect bubble with boundary-touching tail")
        self.assertLess(bubble_rect[0], 260)
        self.assertGreater(bubble_rect[2], 360)

    def test_placement_rects_favors_fine_nudges_over_massive_displacement(self):
        """Verify that obstacle avoidance nudges candidates by small steps rather than jumping to far extremities."""
        from manga_translator.rendering import _find_horizontal_placement
        block = TextBlock(
            lines=[[[300, 100], [330, 100], [330, 300], [300, 300]]],
            texts=['テキスト'],
            translation='Nudge test should not jump far away',
            font_size=18,
            direction='h'
        )
        block.target_lang = 'ENG'

        # Obstacle slightly touches right edge: x: 380..420
        obstacle = [380, 100, 420, 300]
        placement = _find_horizontal_placement(
            block,
            [300, 100, 330, 300],
            (600, 600, 3),
            target_font_size=18,
            font_size_minimum=10,
            text=block.translation,
            hyphenate=True,
            line_spacing=0,
            obstacles=[obstacle],
            is_bubble=False
        )
        self.assertIsNotNone(placement)
        placed_rect = placement[1]
        # Must clear obstacle on the right
        self.assertLessEqual(placed_rect[2], 380)
        # Must not make an unprovoked massive jump to the far left
        self.assertGreaterEqual(placed_rect[0], 200)

    def test_seg_eng_preserves_case(self):
        from manga_translator.rendering.text_render_eng import seg_eng
        words = seg_eng("Hello World! This is a test.")
        self.assertEqual(words, ["Hello", "World!", "This", "is a", "test."])

        words_lower = seg_eng("hello world! this is a test.")
        self.assertEqual(words_lower, ["hello", "world!", "this", "is a", "test."])

        words_upper = seg_eng("HELLO WORLD! THIS IS A TEST.")
        self.assertEqual(words_upper, ["HELLO", "WORLD!", "THIS", "IS A", "TEST."])

    def test_rendering_transforms_casing(self):
        import asyncio
        from types import SimpleNamespace
        from manga_translator.manga_translator import MangaTranslator
        from manga_translator.config import Config, RenderConfig

        translator = object.__new__(MangaTranslator)
        translator._model_usage_timestamps = {}
        translator.font_path = ''

        block = TextBlock(
            lines=[[[10, 10], [50, 10], [50, 50], [10, 50]]],
            texts=['テスト'],
            translation='Hello World!',
        )
        ctx = SimpleNamespace(
            img_inpainted=np.zeros((100, 100, 3), dtype=np.uint8),
            img_rgb=np.zeros((100, 100, 3), dtype=np.uint8),
            text_regions=[block],
            render_mask=None,
        )

        # Test uppercase
        config_upper = Config(render=RenderConfig(uppercase=True, renderer='none'))
        asyncio.run(translator._run_text_rendering(config_upper, ctx))
        self.assertEqual(block.translation, "HELLO WORLD!")

        # Test lowercase
        config_lower = Config(render=RenderConfig(lowercase=True, renderer='none'))
        asyncio.run(translator._run_text_rendering(config_lower, ctx))
        self.assertEqual(block.translation, "hello world!")


if __name__ == '__main__':
    unittest.main()
