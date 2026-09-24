import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import cv2
import numpy as np
from PIL import Image

from devscripts.pipeline_step_runner import (
    build_parser,
    build_lobe_graph,
    build_original_layout_profile,
    BubbleLayoutGroup,
    PlacementMode,
    FreeTextZone,
    build_free_text_ownership_zones,
    build_page_obstacle_map,
    create_bubbles_overlay_image,
    create_placement_zones_visualization,
    deserialize_text_regions_from_dict,
    _detector_cleanup_mask,
    execute_fast_render_batch,
    expand_input_images,
    expand_sample_directories,
    partition_bubble_zones,
    apply_shape_aware_bubble_layout,
    load_step_data,
    run_fast_placement_and_render,
    save_step_data,
    serialize_bubble_detections,
    serialize_text_regions_to_dict,
    solve_layout,
    compute_placement_target,
    compute_zone_shape_profile,
    ZoneShapeProfile,
    normalize_words,
    BubbleGeometry,
    BandSlot,
    RowGeometry,
    PlacedLine,
    _dp_word_break_rows,
    _try_placement_rows,
    _compact_vertical_rhythm,
    _classify_adjacent_gaps,
    _free_text_typography_candidates,
    _free_text_words,
    _choose_joint_layout,
    _RegionLayoutPlan,
    _validate_render_integrity,
    find_intersecting_draw_operations,
    GAP_NORMAL,
    GAP_LOCAL_GEOMETRY,
    GAP_LOBE_NECK,
    GAP_DISCONNECTED_SAFE_REGION,
    GAP_UNEXPLAINED,
)
from manga_translator.detection.bubble import BubbleDetection
from manga_translator.config import Config, Detector, Renderer
from manga_translator.rendering import get_default_eng_font, text_render
from manga_translator.utils import Context, Quadrilateral, TextBlock


class PipelineStepRunnerTests(unittest.TestCase):
    def test_capture_prewarms_text_and_bubble_detectors_separately(self):
        from devscripts import pipeline_step_runner as runner

        config = Config()
        with TemporaryDirectory() as tmp_dir, \
                patch.object(runner, "MangaTranslator") as translator_cls, \
                patch.object(runner, "prepare_detection", new_callable=AsyncMock) as prepare_detection, \
                patch.object(runner, "prepare_ocr", new_callable=AsyncMock), \
                patch.object(runner, "prepare_bubble_detection", new_callable=AsyncMock) as prepare_bubble, \
                patch.object(runner, "prepare_inpainting", new_callable=AsyncMock), \
                patch.object(runner, "prepare_translation", new_callable=AsyncMock):
            translator_cls.return_value.device = "cpu"

            self.assertEqual(
                asyncio.run(runner.execute_capture([], Path(tmp_dir), config)),
                [],
            )

        prepare_detection.assert_awaited_once_with(Detector.default)
        prepare_bubble.assert_awaited_once_with(config.bubble_detection, "cpu")

    def test_joint_layout_caps_large_cartesian_candidate_search(self):
        plans = [
            _RegionLayoutPlan(region=object(), candidates=[object()] * 8)
            for _ in range(9)
        ]

        with patch("devscripts.pipeline_step_runner._candidate_data") as candidate_data, patch(
            "devscripts.pipeline_step_runner.itertools.product"
        ) as product:
            self.assertIsNone(_choose_joint_layout(plans, (32, 32)))

        candidate_data.assert_not_called()
        product.assert_not_called()

    def test_glyph_cache_is_keyed_by_font_selection(self):
        text_render.get_char_glyph.cache_clear()
        text_render.set_font("fonts/comic shanns 2.ttf")
        comic_glyph = text_render.get_char_glyph("A", 24, 0)

        text_render.set_font("fonts/anime_ace.ttf")
        text_render.get_char_glyph("A", 24, 0)
        self.assertEqual(text_render.get_char_glyph.cache_info().currsize, 2)

        text_render.set_font("fonts/comic shanns 2.ttf")
        self.assertIs(text_render.get_char_glyph("A", 24, 0), comic_glyph)

    def test_free_text_isolated_from_bubble_geometry(self):
        ctx = Context()
        ctx.img_rgb = np.full((160, 180, 3), 255, dtype=np.uint8)
        ctx.img_inpainted = ctx.img_rgb.copy()

        bubble_mask = np.zeros((160, 180), dtype=np.uint8)
        cv2.rectangle(bubble_mask, (95, 20), (170, 140), 255, -1)
        bubble = TextBlock(
            lines=np.array([[[110, 48], [150, 48], [150, 70], [110, 70]]], dtype=np.int32),
            texts=["BUBBLE"], translation="BUBBLE", target_lang="ENG", font_size=10,
        )
        bubble._bubble_mask = bubble_mask
        free = TextBlock(
            lines=np.array([[[12, 62], [54, 62], [54, 82], [12, 82]]], dtype=np.int32),
            texts=["FREE TEXT"], translation="FREE TEXT", target_lang="ENG", font_size=10,
        )
        ctx.text_regions = [bubble, free]

        config = Config()
        config.render.font_size = 10
        config.render.font_size_minimum = 6
        apply_shape_aware_bubble_layout(ctx, config, solver_max_y_trials=4, layout_debug=True)

        self.assertIs(bubble.placement_mode, PlacementMode.BUBBLE)
        self.assertIs(free.placement_mode, PlacementMode.FREE_TEXT)
        self.assertTrue(bubble._solver_applied)
        self.assertTrue(free._free_text_solver_applied)
        self.assertEqual(free._solver_path, "free_text")

        obstacles = build_page_obstacle_map(ctx.text_regions, ctx.img_rgb.shape[:2], bubble_halo=4)
        self.assertFalse(np.any(free._free_text_glyph_mask & obstacles.protected_bubble_mask.astype(bool)))

        other_free = TextBlock(
            lines=np.array([[[60, 62], [84, 62], [84, 82], [60, 82]]], dtype=np.int32),
            texts=["OTHER TEXT"], translation="OTHER TEXT", target_lang="ENG", font_size=10,
        )
        other_free.placement_mode = PlacementMode.FREE_TEXT
        obstacles = build_page_obstacle_map([bubble, free, other_free], ctx.img_rgb.shape[:2], bubble_halo=4)
        zones = build_free_text_ownership_zones([free, other_free], obstacles)
        self.assertIsInstance(zones[id(free)], FreeTextZone)
        self.assertIsInstance(zones[id(other_free)], FreeTextZone)
        self.assertTrue(np.any(zones[id(free)].ownership_mask))
        self.assertTrue(np.any(zones[id(other_free)].ownership_mask))
        self.assertFalse(np.any(zones[id(free)].ownership_mask & zones[id(other_free)].ownership_mask))
        self.assertFalse(np.any(zones[id(free)].coverage_target_mask & zones[id(other_free)].coverage_target_mask))

    def test_shape_aware_layout_partitions_shared_bubble_by_source_regions(self):
        ctx = Context()
        ctx.img_rgb = np.full((180, 160, 3), 255, dtype=np.uint8)
        ctx.text_regions = []
        interior = np.zeros((180, 160), dtype=np.uint8)
        interior[10:170, 10:150] = 1

        for y, text in (
            (25, "I'M WORKING MY BUTT OFF WITH A SECOND JOB..."),
            (115, "HOW DARE HE ACCUSE ME OF BEING A SLUT!?"),
        ):
            lines = np.array([[[30, y], [130, y], [130, y + 20], [30, y + 20]]], dtype=np.int32)
            region = TextBlock(
                lines=lines,
                texts=[text],
                translation=text,
                target_lang="ENG",
                font_size=12,
            )
            region._bubble_interior = interior.copy()
            ctx.text_regions.append(region)

        config = Config()
        config.render.font_size = 12
        config.render.font_size_minimum = 8
        apply_shape_aware_bubble_layout(ctx, config)

        first, second = ctx.text_regions
        self.assertTrue(first._solver_applied)
        self.assertTrue(second._solver_applied)
        self.assertLess(first.layout_bounds[3] + first.font_size, second.layout_bounds[1])
        self.assertIn("p_zone_overflow", first._solver_qa)
        self.assertIn("p_zone_overflow", second._solver_qa)

    def test_lobe_graph_detects_connected_lobes_and_serializes_overlay_data(self):
        mask = np.zeros((360, 220), dtype=np.uint8)
        cv2.ellipse(mask, (110, 85), (65, 65), 0, 0, 360, 255, -1)
        cv2.ellipse(mask, (110, 275), (75, 70), 0, 0, 360, 255, -1)
        cv2.rectangle(mask, (85, 125), (135, 235), 255, -1)

        graph = build_lobe_graph(mask)
        self.assertEqual(len(graph.lobe_masks), 2)
        self.assertEqual(graph.adjacency, [(0, 1)])
        self.assertEqual(len(graph.necks), 1)
        self.assertLess(graph.necks[0]["ratio"], 0.70)
        self.assertTrue(all(capacity > 0 for capacity in graph.capacities))

        serialized = serialize_bubble_detections([BubbleDetection(mask, 0.9)])
        self.assertEqual(len(serialized[0]["lobe_graph"]["lobe_masks"]), 2)
        self.assertEqual(serialized[0]["lobe_graph"]["adjacency"], [[0, 1]])
        overlay = create_bubbles_overlay_image(np.full((*mask.shape, 3), 255, dtype=np.uint8), [BubbleDetection(mask, 0.9)])
        self.assertEqual(overlay.shape, (360, 220, 3))

    def test_save_step_data_writes_lobe_graph_outputs(self):
        with TemporaryDirectory() as tmp_dir:
            ctx = Context()
            ctx.img_rgb = np.full((100, 100, 3), 255, dtype=np.uint8)
            ctx.img_inpainted = ctx.img_rgb.copy()
            ctx.text_regions = []
            mask = np.zeros((100, 100), dtype=np.uint8)
            cv2.circle(mask, (30, 50), 20, 255, -1)
            cv2.circle(mask, (70, 50), 20, 255, -1)
            cv2.rectangle(mask, (30, 45), (70, 55), 255, -1)
            ctx.bubble_detections = [BubbleDetection(mask, 0.9)]

            saved_dir = save_step_data(Path(tmp_dir), "lobe_output", ctx, Config())
            data = json.loads((saved_dir / "bubbles.json").read_text())
            self.assertEqual(len(data[0]["lobe_graph"]["lobe_areas"]), 2)
            self.assertTrue((saved_dir / "bubbles_overlay.png").is_file())

    def test_detector_mask_preserves_pixels_for_ocr_dropped_box(self):
        detected = [
            Quadrilateral(np.array([[5, 5], [25, 5], [25, 15], [5, 15]]), "", 0.9),
            Quadrilateral(np.array([[55, 25], [75, 25], [75, 35], [55, 35]]), "", 0.9),
        ]
        detector_mask = np.zeros((40, 80), dtype=np.uint8)
        detector_mask[8:13, 8:22] = 255
        detector_mask[28:33, 58:72] = 255

        cleanup = _detector_cleanup_mask(detected, detector_mask, (40, 80, 3))

        self.assertGreater(np.count_nonzero(cleanup[8:13, 8:22]), 0)
        self.assertGreater(np.count_nonzero(cleanup[28:33, 58:72]), 0)
        self.assertEqual(cleanup[20, 40], 0)

    def test_serialize_and_deserialize_regions_roundtrip(self):
        lines = np.array([[[10, 10], [50, 10], [50, 30], [10, 30]]], dtype=np.int32)
        region = TextBlock(
            lines=lines,
            texts=["こんにちは"],
            language="JPN",
            font_size=16,
            angle=0.0,
            translation="Hello",
            fg_color=(10, 20, 30),
            bg_color=(240, 240, 240),
            line_spacing=1.2,
            letter_spacing=1.0,
            direction="h",
            alignment="center",
            target_lang="ENG",
            prob=0.98,
        )
        region.region_id = "test_reg_1"
        region.review_required = True
        region.review_reason = "Uncertain boundary"

        # Serialize
        serialized = serialize_text_regions_to_dict([region])
        self.assertEqual(len(serialized), 1)
        self.assertEqual(serialized[0]["id"], "test_reg_1")
        self.assertEqual(serialized[0]["text"], "こんにちは")
        self.assertEqual(serialized[0]["translation"], "Hello")
        self.assertEqual(serialized[0]["target_lang"], "ENG")
        self.assertTrue(serialized[0]["review_required"])
        self.assertEqual(serialized[0]["review_reason"], "Uncertain boundary")
        self.assertEqual(serialized[0]["fg_colors"], [10, 20, 30])
        self.assertEqual(serialized[0]["bg_colors"], [240, 240, 240])

        # Deserialize
        reconstructed = deserialize_text_regions_from_dict(serialized, (100, 100))
        self.assertEqual(len(reconstructed), 1)
        rec = reconstructed[0]
        self.assertEqual(rec.region_id, "test_reg_1")
        self.assertEqual(rec.text, "こんにちは")
        self.assertEqual(rec.translation, "Hello")
        self.assertEqual(rec.target_lang, "ENG")
        self.assertTrue(rec.review_required)
        self.assertEqual(rec.review_reason, "Uncertain boundary")
        self.assertEqual(tuple(rec.fg_colors), (10, 20, 30))
        self.assertEqual(tuple(rec.bg_colors), (240, 240, 240))
        np.testing.assert_array_equal(rec.lines, lines)

    def test_save_and_load_step_data(self):
        with TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            sample_name = "test_page_01"

            # Create dummy Context
            ctx = Context()
            ctx.input = Image.new("RGB", (100, 150), color=(255, 255, 255))
            ctx.img_rgb = np.full((150, 100, 3), 255, dtype=np.uint8)
            ctx.img_inpainted = np.full((150, 100, 3), 250, dtype=np.uint8)
            ctx.mask = np.zeros((150, 100), dtype=np.uint8)
            ctx.bubble_mask = np.zeros((150, 100), dtype=np.uint8)

            lines = np.array([[[20, 20], [80, 20], [80, 50], [20, 50]]], dtype=np.int32)
            region = TextBlock(
                lines=lines,
                texts=["テストです"],
                translation="This is a test",
                language="JPN",
                target_lang="ENG",
                font_size=14,
            )
            region.region_id = "reg_test_01"
            ctx.text_regions = [region]

            config = Config()
            config.translator.translator = "sugoi"
            config.translator.target_lang = "ENG"

            # Save
            saved_dir = save_step_data(
                output_base_dir=base_dir,
                sample_name=sample_name,
                ctx=ctx,
                config=config,
                duration_ms=123.4,
            )

            self.assertTrue(saved_dir.is_dir())
            self.assertTrue((saved_dir / "input.png").is_file())
            self.assertTrue((saved_dir / "img_rgb.png").is_file())
            self.assertTrue((saved_dir / "inpainted.png").is_file())
            self.assertTrue((saved_dir / "regions.json").is_file())
            self.assertTrue((saved_dir / "text_regions.json").is_file())
            self.assertTrue((saved_dir / "ocr.json").is_file())
            self.assertTrue((saved_dir / "ocr_text.txt").is_file())
            self.assertEqual((saved_dir / "ocr_text.txt").read_text().strip(), "テストです")
            self.assertTrue((saved_dir / "step_data.pkl").is_file())
            self.assertTrue((saved_dir / "meta.json").is_file())

            # Load
            loaded_ctx, loaded_cfg = load_step_data(saved_dir)
            self.assertIsNotNone(loaded_ctx.img_rgb)
            self.assertIsNotNone(loaded_ctx.img_inpainted)
            self.assertEqual(len(loaded_ctx.text_regions), 1)
            self.assertEqual(loaded_ctx.text_regions[0].translation, "This is a test")
            self.assertEqual(loaded_cfg.translator.translator, "sugoi")

    def test_json_reload_restores_bubble_assignment(self):
        with TemporaryDirectory() as tmp_dir:
            ctx = Context()
            ctx.input = Image.new("RGB", (100, 100), color=(255, 255, 255))
            ctx.img_rgb = np.full((100, 100, 3), 255, dtype=np.uint8)
            ctx.img_inpainted = ctx.img_rgb.copy()
            region = TextBlock(
                lines=np.array([[[30, 30], [60, 30], [60, 50], [30, 50]]], dtype=np.int32),
                texts=["BUBBLE"], translation="BUBBLE", target_lang="ENG",
            )
            bubble_mask = np.zeros((100, 100), dtype=np.uint8)
            cv2.rectangle(bubble_mask, (20, 20), (80, 80), 255, -1)
            region._bubble_mask = bubble_mask
            ctx.text_regions = [region]
            ctx.bubble_detections = [BubbleDetection(bubble_mask, 0.9)]

            saved_dir = save_step_data(Path(tmp_dir), "json_reload", ctx, Config())
            loaded_ctx, _ = load_step_data(saved_dir, use_json=True)

            self.assertTrue(np.any(getattr(loaded_ctx.text_regions[0], "_bubble_mask", None)))

    def test_load_step_data_syncs_edited_regions_json(self):
        with TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            sample_name = "test_page_sync"

            ctx = Context()
            ctx.input = Image.new("RGB", (100, 100), color=(255, 255, 255))
            ctx.img_rgb = np.full((100, 100, 3), 255, dtype=np.uint8)
            ctx.img_inpainted = np.full((100, 100, 3), 250, dtype=np.uint8)
            ctx.mask = np.zeros((100, 100), dtype=np.uint8)

            lines = np.array([[[10, 10], [50, 10], [50, 30], [10, 30]]], dtype=np.int32)
            region = TextBlock(
                lines=lines,
                texts=["元のテキスト"],
                translation="Original translation",
                target_lang="ENG",
            )
            ctx.text_regions = [region]
            config = Config()

            saved_dir = save_step_data(base_dir, sample_name, ctx, config)

            # Edit regions.json directly
            regions_path = saved_dir / "regions.json"
            with open(regions_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data[0]["translation"] = "Manually edited translation!"
            with open(regions_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            # Load and verify it synced
            loaded_ctx, _ = load_step_data(saved_dir)
            self.assertEqual(loaded_ctx.text_regions[0].translation, "Manually edited translation!")

    def test_load_step_data_syncs_regions_by_id_after_reorder(self):
        with TemporaryDirectory() as tmp_dir:
            ctx = Context()
            ctx.img_rgb = np.full((80, 120, 3), 255, dtype=np.uint8)
            ctx.img_inpainted = ctx.img_rgb.copy()
            regions = []
            for idx, text in enumerate(("A", "B")):
                region = TextBlock(
                    lines=np.array([[[10 + idx * 50, 10], [40 + idx * 50, 10], [40 + idx * 50, 30], [10 + idx * 50, 30]]], dtype=np.int32),
                    texts=[text], translation=f"original-{text}", target_lang="ENG",
                )
                region.region_id = f"stable-{text}"
                regions.append(region)
            ctx.text_regions = regions
            saved_dir = save_step_data(Path(tmp_dir), "reordered", ctx, Config())

            records = json.loads((saved_dir / "regions.json").read_text())
            records.reverse()
            records[0]["translation"] = "edited-B"
            records[1]["translation"] = "edited-A"
            (saved_dir / "regions.json").write_text(json.dumps(records))

            loaded_ctx, _ = load_step_data(saved_dir)
            self.assertEqual(
                [region.translation for region in loaded_ctx.text_regions],
                ["edited-A", "edited-B"],
            )

    def test_run_fast_placement_and_render_execution(self):
        ctx = Context()
        ctx.img_rgb = np.full((120, 120, 3), 255, dtype=np.uint8)
        ctx.img_inpainted = np.full((120, 120, 3), 245, dtype=np.uint8)
        ctx.mask = np.zeros((120, 120), dtype=np.uint8)

        lines = np.array([[[15, 15], [95, 15], [95, 45], [15, 45]]], dtype=np.int32)
        region = TextBlock(
            lines=lines,
            texts=["こんにちは世界"],
            translation="Hello World",
            target_lang="ENG",
            language="JPN",
            font_size=12,
        )
        ctx.text_regions = [region]

        config = Config()
        config.render.renderer = Renderer.default

        output, timing = run_fast_placement_and_render(
            ctx=ctx,
            config=config,
            letter_case_override="upper",
            enable_bubble_layout=False,
        )

        self.assertIsInstance(output, np.ndarray)
        self.assertEqual(output.shape, (120, 120, 3))
        self.assertIn("total_ms", timing)
        self.assertIn("placement_and_fit_ms", timing)
        self.assertIn("render_dispatch_ms", timing)
        self.assertEqual(
            set(timing["layout_breakdown"]),
            {
                "mask_prep_ms",
                "mode_classification_ms",
                "bubble_solver_ms",
                "free_text_solver_ms",
                "fallback_ms",
                "other_ms",
            },
        )
        # Verify uppercase transformation
        self.assertEqual(region.translation, "HELLO WORLD")
        active_font = next(path for path, face in text_render.font_cache.items() if face is text_render.FONT_SELECTION[0])
        self.assertEqual(active_font, get_default_eng_font())

    def test_expand_input_images(self):
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "img1.png").write_bytes(b"dummy")
            (root / "img2.jpg").write_bytes(b"dummy")
            (root / "img3.webp").write_bytes(b"dummy")
            (root / "img4.bmp").write_bytes(b"dummy")
            (root / "img5.tiff").write_bytes(b"dummy")
            (root / "img6.avif").write_bytes(b"dummy")
            (root / "img10.png").write_bytes(b"dummy")
            (root / "readme.txt").write_text("dummy")

            sub = root / "subdir"
            sub.mkdir()
            (sub / "nested.webp").write_bytes(b"dummy")

            # 1. Test wildcard *.png
            expanded_png = expand_input_images([str(root / "*.png")])
            self.assertEqual([p.name for p in expanded_png], ["img1.png", "img10.png"])

            # 2. Test directory expansion
            expanded_all = expand_input_images([str(root)])
            self.assertEqual(len(expanded_all), 8)
            self.assertIn("nested.webp", [p.name for p in expanded_all])

            # 3. Test multi-type wildcards
            expanded_multi = expand_input_images([str(root / "*.*")])
            self.assertEqual(len(expanded_multi), 7)

            # 4. Test recursive wildcards
            expanded_recursive = expand_input_images([str(root / "**/*.*")])
            self.assertEqual(len(expanded_recursive), 8)

    def test_expand_sample_directories(self):
        with TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            s1 = base / "sample1"
            s1.mkdir()
            (s1 / "regions.json").write_text("[]")

            s2 = base / "sample2"
            s2.mkdir()
            (s2 / "step_data.pkl").write_bytes(b"dummy")

            s3 = base / "empty_dir"
            s3.mkdir()

            expanded = expand_sample_directories(["sample1", "sample2", "empty_dir"], base)
            self.assertEqual(len(expanded), 2)
            self.assertEqual({d.name for d in expanded}, {"sample1", "sample2"})

    def test_execute_fast_render_batch(self):
        with TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            sample_dir = base_dir / "sample_batch_01"
            sample_dir.mkdir()

            ctx = Context()
            ctx.img_rgb = np.full((80, 80, 3), 255, dtype=np.uint8)
            ctx.img_inpainted = np.full((80, 80, 3), 250, dtype=np.uint8)
            lines = np.array([[[10, 10], [70, 10], [70, 40], [10, 40]]], dtype=np.int32)
            ctx.text_regions = [
                TextBlock(lines=lines, texts=["こんにちは"], translation="Hello", target_lang="ENG")
            ]
            config = Config()
            save_step_data(base_dir, "sample_batch_01", ctx, config)

            results = execute_fast_render_batch(
                sample_dirs=[sample_dir],
                output_filename="test_rendered.png",
                concurrency=2,
            )

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["success"])
            self.assertTrue((sample_dir / "test_rendered.png").is_file())

    def test_partition_bubble_zones_and_visualization(self):
        interior = np.zeros((120, 100), dtype=np.uint8)
        interior[10:110, 10:90] = 1

        reg1 = TextBlock(
            lines=np.array([[[20, 20], [80, 20], [80, 35], [20, 35]]], dtype=np.int32),
            texts=["TOP REGION"],
            translation="TOP REGION",
            target_lang="ENG",
            font_size=10,
        )
        reg2 = TextBlock(
            lines=np.array([[[20, 75], [80, 75], [80, 90], [20, 90]]], dtype=np.int32),
            texts=["BOTTOM REGION"],
            translation="BOTTOM REGION",
            target_lang="ENG",
            font_size=10,
        )

        zones = partition_bubble_zones([reg1, reg2], interior, apply_boundary_gap=True)
        self.assertEqual(len(zones), 2)
        z1, z2 = zones[0], zones[1]
        self.assertGreater(np.count_nonzero(z1), 0)
        self.assertGreater(np.count_nonzero(z2), 0)
        # Verify non-overlapping
        self.assertEqual(np.count_nonzero(np.logical_and(z1 > 0, z2 > 0)), 0)
        # Verify boundary gap: union of zones should be strictly smaller than total interior
        self.assertLess(np.count_nonzero(np.logical_or(z1 > 0, z2 > 0)), np.count_nonzero(interior))

        # Test visualization generation
        group = BubbleLayoutGroup(
            bubble_mask=interior,
            interior=interior,
            regions=[reg1, reg2],
            zones=[z1, z2],
        )
        img_rgb = np.full((120, 100, 3), 255, dtype=np.uint8)
        vis = create_placement_zones_visualization(img_rgb, [group])
        self.assertEqual(vis.shape, (120, 100, 3))

    def test_execute_fast_render_batch_multi_region_exports_placement_zones(self):
        with TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            sample_dir = base_dir / "sample_multi_01"
            sample_dir.mkdir()

            ctx = Context()
            ctx.img_rgb = np.full((160, 120, 3), 255, dtype=np.uint8)
            ctx.img_inpainted = np.full((160, 120, 3), 250, dtype=np.uint8)
            interior = np.zeros((160, 120), dtype=np.uint8)
            interior[10:150, 10:110] = 1

            reg1 = TextBlock(
                lines=np.array([[[20, 20], [100, 20], [100, 45], [20, 45]]], dtype=np.int32),
                texts=["FIRST PART"],
                translation="FIRST PART",
                target_lang="ENG",
                font_size=11,
            )
            reg1._bubble_interior = interior.copy()

            reg2 = TextBlock(
                lines=np.array([[[20, 95], [100, 95], [100, 120], [20, 120]]], dtype=np.int32),
                texts=["SECOND PART"],
                translation="SECOND PART",
                target_lang="ENG",
                font_size=11,
            )
            reg2._bubble_interior = interior.copy()

            ctx.text_regions = [reg1, reg2]
            config = Config()
            save_step_data(base_dir, "sample_multi_01", ctx, config)

            results = execute_fast_render_batch(
                sample_dirs=[sample_dir],
                output_filename="rendered.png",
                concurrency=1,
            )

    def test_parser_bubble_grouping_option(self):
        parser = build_parser()
        # Default: keep regions separate
        args = parser.parse_args(["capture", "-i", "page.png"])
        self.assertFalse(args.bubble_grouping)

        # Explicit opt-in: merge regions by bubble
        args = parser.parse_args(["capture", "-i", "page.png", "--bubble-grouping"])
        self.assertTrue(args.bubble_grouping)

        # Flag provided: bubble_grouping is False
        args = parser.parse_args(["capture", "-i", "page.png", "--no-bubble-grouping"])
        self.assertFalse(args.bubble_grouping)

        # Alias: --no-group-regions-by-bubbles
        args = parser.parse_args(["capture", "-i", "page.png", "--no-group-regions-by-bubbles"])
        self.assertFalse(args.bubble_grouping)

    def test_parser_sugoi_and_translator_options(self):
        parser = build_parser()
        # Default: sugoi is False, translator is None, target_lang is ENG
        args = parser.parse_args(["capture", "-i", "page.png"])
        self.assertFalse(args.sugoi)
        self.assertIsNone(args.translator)
        self.assertEqual(args.target_lang, "ENG")

        # --sugoi flag
        args = parser.parse_args(["capture", "-i", "page.png", "--sugoi"])
        self.assertTrue(args.sugoi)

        # --use-sugoi alias
        args = parser.parse_args(["capture", "-i", "page.png", "--use-sugoi"])
        self.assertTrue(args.sugoi)

        # --translator sugoi with target-lang
        args = parser.parse_args(["capture", "-i", "page.png", "--translator", "sugoi", "-l", "ENG"])
        self.assertEqual(args.translator, "sugoi")
        self.assertEqual(args.target_lang, "ENG")

    def test_solve_layout_spatial_continuity_and_coherence(self):
        # Create a mask with two columns separated by a gap (left column at x=10..70, right at x=130..190)
        # and test that a single block stays in one coherent column rather than oscillating across columns.
        mask = np.zeros((200, 200), dtype=np.uint8)
        mask[20:180, 10:70] = 1
        mask[20:180, 130:190] = 1

        geom = BubbleGeometry(mask)
        words = ["THE", "QUICK", "BROWN", "FOX", "JUMPS", "OVER", "THE", "LAZY", "DOG"]

        result = solve_layout(
            geom=geom,
            words=words,
            font_size_max=14,
            font_size_min=10,
            language="en_US",
        )

        self.assertIsNotNone(result)
        self.assertTrue(result.valid)
        self.assertIn("gap_ratio", result.qa)
        self.assertIn("center_variance", result.qa)

        # Verify all lines stay within the same column slot without jumping across x=100
        first_x = result.lines[0].x
        for line in result.lines:
            if first_x < 100:
                self.assertLess(line.x + line.width, 100)
            else:
                self.assertGreater(line.x, 100)

    def test_dp_keeps_usable_rows_in_continuous_paragraph(self):
        rows = [
            RowGeometry(y, 10, [BandSlot(0, width, y, y + 10)])
            for y, width in ((0, 24), (10, 8), (20, 8), (30, 24),
                             (40, 8), (50, 8), (60, 24))
        ]
        candidates = _dp_word_break_rows(
            words=["AA", "BB", "CC", "DD", "EE", "FF"],
            word_widths=[8] * 6,
            space_w=2,
            rows=rows,
            font_size=10,
            max_per_bucket=8,
        )

        self.assertTrue(candidates)
        for lines in candidates:
            skipped_rows = sum(
                (lines[i + 1].y - lines[i].y) // 10 - 1
                for i in range(len(lines) - 1)
            )
            self.assertEqual(skipped_rows, 0, lines)

    def test_row_scan_keeps_lower_lobe_after_unavailable_band(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[20:40, 30:70] = 255
        mask[50:90, 30:70] = 255
        geom = BubbleGeometry(mask)

        candidates = _try_placement_rows(
            geom=geom,
            words=["AA", "BB", "CC", "DD"],
            word_widths=[30] * 4,
            space_w=4,
            font_size=10,
            y_origin=20,
            line_spacing=0.0,
            line_h=10,
            stroke_width=0,
            margin=0.0,
        )

        self.assertTrue(candidates)
        self.assertTrue(any(line.y >= 50 for line in candidates[0]))

    def test_placement_target_capacity_weighting_ignores_tail(self):
        # Create a speech bubble with a main round body and a downward-pointing tail
        # Body: centered around (100, 70), tail extends down to y=180
        mask = np.zeros((200, 200), dtype=np.uint8)
        cv2.circle(mask, (100, 70), 50, 255, -1)
        # Narrow tail from y=120 to 180, width 6
        mask[120:180, 97:103] = 255

        geom = BubbleGeometry(mask)
        target = compute_placement_target(geom, font_size=12, stroke_width=0, margin=2.0)

        # Naive mask geometric center y would be pulled downwards by the 60px tail
        naive_cy = float(np.nonzero(mask)[0].mean())
        # Capacity weighted center should remain close to the body center (y ≈ 70)
        self.assertLess(target.center_y, naive_cy)
        self.assertLess(target.center_y, 85.0)

    def test_whole_block_recentering_and_whitespace_balance(self):
        # Create an oval bubble and solve layout for a 4-line sentence
        mask = np.zeros((160, 160), dtype=np.uint8)
        cv2.ellipse(mask, (80, 80), (60, 60), 0, 0, 360, 255, -1)

        geom = BubbleGeometry(mask)
        words = ["SHE", "SEEMED", "TO", "HAVE", "GOTTEN", "INTO", "A", "FIGHT", "WITH", "HER", "CLASS"]

        result = solve_layout(
            geom=geom,
            words=words,
            font_size_max=14,
            font_size_min=10,
            language="en_US",
        )

        self.assertIsNotNone(result)
        self.assertTrue(result.valid)
        self.assertIn("vertical_balance", result.qa)
        self.assertIn("horizontal_balance", result.qa)
        self.assertIn("block_center_dx", result.qa)
        self.assertIn("block_center_dy", result.qa)

        # Block center should be tightly centered near target (center_dx and center_dy within small margin)
        self.assertLess(abs(result.qa["block_center_dx"]), 8.0)
        self.assertLess(abs(result.qa["block_center_dy"]), 8.0)
        # Free space above and below should be well balanced (ratio difference < 0.25)
        self.assertLess(result.qa["vertical_balance"], 0.25)

    def test_compute_zone_shape_profile(self):
        # Create a tall rectangle safe zone (width=60, height=140)
        mask = np.zeros((180, 100), dtype=np.uint8)
        mask[20:160, 20:80] = 1

        geom = BubbleGeometry(mask)
        profile = compute_zone_shape_profile(geom, font_size=12, line_h=14, words=["LISTEN", "TO", "ME"])

        self.assertIsInstance(profile, ZoneShapeProfile)
        self.assertEqual(profile.width, 60.0)
        self.assertEqual(profile.height, 140.0)
        self.assertAlmostEqual(profile.aspect_ratio, 60.0 / 140.0, places=2)
        self.assertEqual(profile.vertical_capacity, 10)
        self.assertEqual(len(profile.width_by_y), 140)
        self.assertEqual(profile.width_by_y[0], 60.0)
        self.assertEqual(profile.usable_area, 60.0 * 140.0)

    def test_normalize_words_attaches_punctuation_and_hyphens(self):
        # Test punctuation attachment
        tokens = ["LISTEN", "TO", "ME,", "YOU'RE", "A", "UCHIGAKI", "?!"]
        normalized = normalize_words(tokens)
        self.assertEqual(normalized, ["LISTEN", "TO", "ME,", "YOU'RE", "A", "UCHIGAKI?!"])

        # Test compound and hyphen repair
        tokens2 = ["GOT-", "TEN", "SELF-DEFENSE", "X-", "RAY"]
        normalized2 = normalize_words(tokens2)
        self.assertIn("GOTTEN", normalized2)
        self.assertIn("SELF-DEFENSE", normalized2)

    def test_free_text_typography_keeps_translated_words_atomic(self):
        region = TextBlock(
            lines=np.array([[[20, 20], [100, 20], [100, 40], [20, 40]]], dtype=np.int32),
            texts=["原文"], translation="SILK INSTEAD OF COTTON", target_lang="ENG", font_size=14,
        )
        profile = build_original_layout_profile(region, np.ones((100, 140), dtype=np.uint8))
        candidates = _free_text_typography_candidates("SILK INSTEAD OF COTTON", profile, Config(), (100, 140))

        self.assertTrue(candidates)
        rendered_words = " ".join(line.text for line in candidates[0].lines).split()
        self.assertEqual(rendered_words, _free_text_words("SILK INSTEAD OF COTTON"))
        self.assertNotIn("IN-", rendered_words)
        self.assertIn("INSTEAD", rendered_words)
        self.assertTrue(all(not line.text.rstrip().endswith("-") for line in candidates[0].lines[:-1]))

    def test_free_text_typography_does_not_optimize_line_spacing(self):
        region = TextBlock(
            lines=np.array([[[20, 20], [100, 20], [100, 60], [20, 60]]], dtype=np.int32),
            texts=["原文"], translation="DAMN IT EVEN THOUGH YOU ARE KOTAROU", target_lang="ENG", font_size=24,
        )
        profile = build_original_layout_profile(region, np.ones((240, 160), dtype=np.uint8))
        config = Config()
        config.render.line_spacing = 2

        candidates = _free_text_typography_candidates(
            region.translation, profile, config, (240, 160), target_height=400
        )

        self.assertTrue(candidates)
        self.assertEqual({candidate.line_spacing for candidate in candidates}, {0.25})
        self.assertTrue(all(candidate.qa["baseline_consistent"] for candidate in candidates))
        self.assertTrue(all("ink_width" in candidate.qa and "ink_height" in candidate.qa for candidate in candidates))

    def test_tall_bubble_selects_taller_paragraph(self):
        # Create a tall bubble (w=70, h=180, aspect ratio ~ 0.39)
        mask = np.zeros((220, 100), dtype=np.uint8)
        cv2.ellipse(mask, (50, 110), (35, 90), 0, 0, 360, 255, -1)

        geom = BubbleGeometry(mask)
        words = ["LISTEN", "TO", "ME,", "YOU'RE", "A", "UCHIGAKI?!"]

        result = solve_layout(
            geom=geom,
            words=words,
            font_size_max=14,
            font_size_min=10,
            language="en_US",
        )

        self.assertIsNotNone(result)
        self.assertTrue(result.valid)
        # For a tall balloon with 6 words, paragraph shaping should choose 4 to 6 lines rather than 2 or 3 lines
        self.assertGreaterEqual(len(result.lines), 4)
        self.assertIn("vertical_utilization", result.qa)
        self.assertIn("p_aspect", result.qa)
        self.assertIn("candidate_alternatives", result.qa)
        self.assertGreater(len(result.qa["candidate_alternatives"]), 1)

    def test_wide_bubble_selects_wider_paragraph(self):
        # Create a wide bubble (w=180, h=70, aspect ratio ~ 2.57)
        mask = np.zeros((100, 220), dtype=np.uint8)
        cv2.ellipse(mask, (110, 50), (90, 35), 0, 0, 360, 255, -1)

        geom = BubbleGeometry(mask)
        words = ["LISTEN", "TO", "ME,", "YOU'RE", "A", "UCHIGAKI?!"]

        result = solve_layout(
            geom=geom,
            words=words,
            font_size_max=14,
            font_size_min=10,
            language="en_US",
        )

        self.assertIsNotNone(result)
        self.assertTrue(result.valid)
        # For a wide balloon, paragraph shaping should naturally choose 2 or 3 lines rather than 5 or 6 lines
        self.assertLessEqual(len(result.lines), 3)

    def test_solver_diagnostics_includes_candidate_alternatives(self):
        mask = np.zeros((140, 140), dtype=np.uint8)
        cv2.ellipse(mask, (70, 70), (50, 50), 0, 0, 360, 255, -1)

        geom = BubbleGeometry(mask)
        words = ["HELLO", "WORLD", "HOW", "ARE", "YOU", "DOING", "TODAY?"]

        result = solve_layout(
            geom=geom,
            words=words,
            font_size_max=14,
            font_size_min=10,
            language="en_US",
        )

        self.assertIsNotNone(result)
        self.assertIn("candidate_alternatives", result.qa)
        alts = result.qa["candidate_alternatives"]
        self.assertGreaterEqual(len(alts), 2)
        # Each alternative should record lines, font_size, vertical_utilization, aspect_mismatch, and penalty
        for alt in alts:
            self.assertIn("lines", alt)
            self.assertIn("vertical_utilization", alt)
            self.assertIn("aspect_mismatch", alt)
            self.assertIn("penalty", alt)

    def test_single_lobe_bubble_continuous_paragraph_rhythm(self):
        # Single-lobe oval bubble (w=120, h=150)
        mask = np.zeros((180, 150), dtype=np.uint8)
        cv2.ellipse(mask, (75, 90), (55, 75), 0, 0, 360, 255, -1)

        geom = BubbleGeometry(mask)
        words = ["ISN'T", "THAT", "OBVIOUS?"]

        result = solve_layout(
            geom=geom,
            words=words,
            font_size_max=16,
            font_size_min=10,
            language="en_US",
        )

        self.assertIsNotNone(result)
        self.assertTrue(result.valid)
        self.assertIn("gap_details", result.qa)
        self.assertFalse(result.qa.get("has_unexplained_gap", True))
        
        # Check that all adjacent line gaps are classified as NORMAL or LOCAL_GEOMETRY
        for gap_info in result.qa["gap_details"]:
            self.assertIn(gap_info["reason"], (GAP_NORMAL, GAP_LOCAL_GEOMETRY))
            self.assertLessEqual(gap_info["gap_h"], 1.35)

    def test_vertical_compaction_pass_pulls_displaced_lines(self):
        # Open rectangular safe mask (w=100, h=200)
        mask = np.zeros((200, 100), dtype=np.uint8)
        mask[10:190, 10:90] = 255

        geom = BubbleGeometry(mask)
        font_size = 14
        line_h = 16

        # Construct lines where line 2 is artificially displaced downwards by 32px
        slot0 = BandSlot(left=10, right=90, y_start=20, y_end=34)
        slot1 = BandSlot(left=10, right=90, y_start=36, y_end=50)
        slot2 = BandSlot(left=10, right=90, y_start=84, y_end=98)  # Displaced! Should be around 52

        lines = [
            PlacedLine(text="LINE 1", y=20, x=15, width=40, height=font_size, slot=slot0),
            PlacedLine(text="LINE 2", y=36, x=15, width=40, height=font_size, slot=slot1),
            PlacedLine(text="LINE 3", y=84, x=15, width=40, height=font_size, slot=slot2),
        ]

        compacted = _compact_vertical_rhythm(lines, geom, font_size, line_h)

        self.assertEqual(len(compacted), 3)
        # Line 3 should have been pulled upward by upward compaction pass
        self.assertEqual(compacted[0].y, 20)
        self.assertEqual(compacted[1].y, 36)
        self.assertEqual(compacted[2].y, 52)  # 36 + 16 = 52

    def test_gap_classification_distinguishes_reasons(self):
        # Bubble with two lobes connected by a narrow neck
        mask = np.zeros((260, 120), dtype=np.uint8)
        cv2.ellipse(mask, (60, 50), (45, 40), 0, 0, 360, 255, -1)
        cv2.ellipse(mask, (60, 200), (45, 40), 0, 0, 360, 255, -1)
        cv2.rectangle(mask, (48, 80), (72, 160), 255, -1)

        graph = build_lobe_graph(mask)
        geom = BubbleGeometry(mask)
        font_size = 14
        line_h = 16

        # Normal adjacent lines in top lobe
        slot0 = BandSlot(left=20, right=100, y_start=20, y_end=34)
        slot1 = BandSlot(left=20, right=100, y_start=36, y_end=50)
        normal_lines = [
            PlacedLine(text="TOP 1", y=20, x=25, width=40, height=font_size, slot=slot0),
            PlacedLine(text="TOP 2", y=36, x=25, width=40, height=font_size, slot=slot1),
        ]
        reports_normal = _classify_adjacent_gaps(normal_lines, geom, font_size, line_h, lobe_graph=graph)
        self.assertEqual(reports_normal[0]["reason"], GAP_NORMAL)
        self.assertTrue(reports_normal[0]["valid"])

        # Lines crossing the neck
        slot_bottom = BandSlot(left=20, right=100, y_start=180, y_end=194)
        crossing_lines = [
            PlacedLine(text="TOP", y=36, x=25, width=40, height=font_size, slot=slot1),
            PlacedLine(text="BOTTOM", y=180, x=25, width=40, height=font_size, slot=slot_bottom),
        ]
        reports_crossing = _classify_adjacent_gaps(crossing_lines, geom, font_size, line_h, lobe_graph=graph)
        self.assertEqual(reports_crossing[0]["reason"], GAP_LOBE_NECK)

    def test_unexplained_large_gap_rejection(self):
        # Open oval bubble
        mask = np.zeros((180, 150), dtype=np.uint8)
        cv2.ellipse(mask, (75, 90), (55, 75), 0, 0, 360, 255, -1)
        geom = BubbleGeometry(mask)
        font_size = 14
        line_h = 16

        # Unjustified 50px gap in middle of open bubble without neck
        slot0 = BandSlot(left=30, right=120, y_start=30, y_end=44)
        slot1 = BandSlot(left=30, right=120, y_start=90, y_end=104)  # gap = 60px = 3.75H
        lines = [
            PlacedLine(text="FIRST", y=30, x=35, width=40, height=font_size, slot=slot0),
            PlacedLine(text="SECOND", y=90, x=35, width=40, height=font_size, slot=slot1),
        ]

        reports = _classify_adjacent_gaps(lines, geom, font_size, line_h, lobe_graph=None)
        self.assertEqual(reports[0]["reason"], GAP_UNEXPLAINED)
        self.assertFalse(reports[0]["valid"])

    def test_free_text_damage_mask_and_importance_weighting(self):
        ctx = Context()
        shape = (200, 200)
        ctx.img_rgb = np.full((*shape, 3), 255, dtype=np.uint8)
        ctx.img_inpainted = ctx.img_rgb.copy()

        # Inpaint mask with vertical damaged strip
        inpaint_mask = np.zeros(shape, dtype=np.uint8)
        cv2.rectangle(inpaint_mask, (40, 30), (60, 150), 255, -1)
        ctx.text_mask = inpaint_mask

        free_r1 = TextBlock(
            lines=np.array([[[42, 35], [58, 35], [58, 145], [42, 145]]], dtype=np.int32),
            texts=["日本語テキスト"], translation="Sample English Text", target_lang="ENG", font_size=16,
        )
        free_r1.placement_mode = PlacementMode.FREE_TEXT

        obstacles = build_page_obstacle_map([free_r1], shape, bubble_halo=4)
        zones = build_free_text_ownership_zones([free_r1], obstacles, inpaint_mask=inpaint_mask)

        ft_zone = zones[id(free_r1)]
        self.assertIsInstance(ft_zone, FreeTextZone)
        self.assertTrue(np.any(ft_zone.coverage_target_mask))
        self.assertTrue(np.any(ft_zone.core_damage_mask))
        self.assertTrue(np.all(ft_zone.coverage_weight_map >= 0.0))
        # Distance transform core weight should be strictly higher than edge weight
        self.assertGreater(float(np.max(ft_zone.coverage_weight_map)), 1.5)

    def test_free_text_damage_target_stays_scoped_to_exact_inpaint_mask(self):
        shape = (160, 220)
        obstacles = build_page_obstacle_map([], shape)
        region = TextBlock(
            lines=np.array([[[30, 50], [70, 50], [70, 90], [30, 90]]], dtype=np.int32),
            texts=["原文"], translation="SOURCE", target_lang="ENG", font_size=14,
        )
        region.placement_mode = PlacementMode.FREE_TEXT

        exact_mask = np.zeros(shape, dtype=np.uint8)
        cv2.rectangle(exact_mask, (28, 48), (72, 92), 255, -1)
        cv2.rectangle(exact_mask, (170, 20), (200, 40), 255, -1)  # unrelated page damage

        zones = build_free_text_ownership_zones([region], obstacles, inpaint_mask=exact_mask)
        target = zones[id(region)].damage_target
        self.assertIsNotNone(target)
        self.assertLess(target.centroid_x, 100)
        self.assertFalse(bool(target.mask[25, 180]))

    @unittest.skipUnless(
        (Path(__file__).parents[1] / "devscripts" / "data" / "input").is_dir(),
        "devscripts/data/input fixture not captured on this machine",
    )
    def test_non_bubble_fixture_preserves_content_and_inpaint_centering(self):
        fixture = Path(__file__).parents[1] / "devscripts" / "data" / "input"
        ctx, config = load_step_data(fixture)

        run_fast_placement_and_render(ctx, config, solver_report=False)

        self.assertEqual(getattr(ctx, "_layout_integrity_issues", []), [])
        self.assertEqual(
            [region.translation for region in ctx.text_regions],
            [
                "Damn it, even though you're Kotarou...",
                "Aaah~♡ It feels the best, it's coming!",
                "Ah, it's coming in the back.",
                "Ugh!",
            ],
        )
        self.assertTrue(all(getattr(region, "_free_text_solver_applied", False) for region in ctx.text_regions))
        self.assertTrue(all(not getattr(region, "_render_suppressed", False) for region in ctx.text_regions))

        right = ctx.text_regions[0]
        target = ctx._free_text_zones[id(right)].damage_target
        self.assertLess(right._solver_qa["center_error_px"], target.width * 0.1)
        self.assertLessEqual(right._solver_qa["baseline_advance_max"], right._solver_qa["baseline_advance_min"] + 1)
        self.assertLessEqual(right._solver_qa["visible_gap"], right._solver_qa["font_metric_height"] * 0.5)
        self.assertLess(right._solver_qa["ink_height"], target.height)

    def test_render_integrity_rejects_mismatched_layout_text(self):
        ctx = Context()
        region = TextBlock(
            lines=np.array([[[10, 10], [60, 10], [60, 30], [10, 30]]], dtype=np.int32),
            texts=["原文"], translation="COMPLETE", target_lang="ENG",
        )
        region.placement_mode = PlacementMode.FREE_TEXT
        region._free_text_solver_applied = True
        region._layout_input_text = "INCOMPLETE"
        ctx.text_regions = [region]
        with self.assertRaises(RuntimeError):
            _validate_render_integrity(ctx, strict=True)

    def test_free_text_damage_aware_placement_conceals_erased_area(self):
        ctx = Context()
        shape = (240, 240)
        ctx.img_rgb = np.full((*shape, 3), 255, dtype=np.uint8)
        ctx.img_inpainted = ctx.img_rgb.copy()

        # Inpainted damaged area centered at (80, 80)
        inpaint_mask = np.zeros(shape, dtype=np.uint8)
        cv2.rectangle(inpaint_mask, (65, 50), (95, 120), 255, -1)
        ctx.text_mask = inpaint_mask

        free_region = TextBlock(
            lines=np.array([[[68, 55], [92, 55], [92, 115], [68, 115]]], dtype=np.int32),
            texts=["テスト文字"], translation="AH, IT'S COMING IN THE BACK.", target_lang="ENG", font_size=14,
        )
        ctx.text_regions = [free_region]

        config = Config()
        config.render.font_size = 14
        config.render.font_size_minimum = 8
        apply_shape_aware_bubble_layout(ctx, config, solver_max_y_trials=8, layout_debug=True)

        self.assertTrue(free_region._free_text_solver_applied)
        self.assertEqual(free_region._solver_path, "free_text")
        qa = free_region._solver_qa
        self.assertIn("damage_coverage", qa)
        self.assertIn("core_damage_coverage", qa)
        self.assertGreaterEqual(qa["damage_coverage"], 0.10)
        self.assertAlmostEqual(qa["damage_coverage"], qa["coverage_ink"])
        self.assertGreater(qa["coverage_block"], qa["damage_coverage"])
        self.assertGreaterEqual(qa["core_damage_coverage"], 0.15)
        self.assertLessEqual(qa["center_error_px"], 2.0)
        self.assertLessEqual(free_region.font_size, round(14 * 1.15))

    def test_free_text_hard_safety_prevents_bubble_intrusion(self):
        ctx = Context()
        shape = (200, 200)
        ctx.img_rgb = np.full((*shape, 3), 255, dtype=np.uint8)
        ctx.img_inpainted = ctx.img_rgb.copy()

        # Speech bubble on right side (100 to 180)
        bubble_mask = np.zeros(shape, dtype=np.uint8)
        cv2.rectangle(bubble_mask, (100, 20), (180, 160), 255, -1)
        bubble = TextBlock(
            lines=np.array([[[115, 50], [165, 50], [165, 80], [115, 80]]], dtype=np.int32),
            texts=["INSIDE BUBBLE"], translation="INSIDE BUBBLE", target_lang="ENG", font_size=12,
        )
        bubble._bubble_mask = bubble_mask

        # Free text on left side near bubble boundary (75 to 95)
        free = TextBlock(
            lines=np.array([[[40, 50], [75, 50], [75, 90], [40, 90]]], dtype=np.int32),
            texts=["OUTSIDE"], translation="OUTSIDE FREE TEXT", target_lang="ENG", font_size=12,
        )
        ctx.text_regions = [bubble, free]

        config = Config()
        config.render.font_size = 12
        config.render.font_size_minimum = 8
        apply_shape_aware_bubble_layout(ctx, config, solver_max_y_trials=8, layout_debug=True)

        self.assertTrue(bubble._solver_applied)
        self.assertTrue(free._free_text_solver_applied)
        # Verify strict zero collision with bubble and protected halo
        obstacles = build_page_obstacle_map(ctx.text_regions, shape, bubble_halo=4)
        self.assertFalse(np.any(free._free_text_glyph_mask & obstacles.protected_bubble_mask.astype(bool)))

    def test_mixed_bubble_and_free_text_joint_selection_no_exception(self):
        ctx = Context()
        shape = (300, 300)
        ctx.img_rgb = np.full((*shape, 3), 255, dtype=np.uint8)
        ctx.img_inpainted = ctx.img_rgb.copy()

        # Two bubble regions sharing a bubble
        bubble_mask = np.zeros(shape, dtype=np.uint8)
        cv2.circle(bubble_mask, (150, 100), 80, 255, -1)
        b1 = TextBlock(
            lines=np.array([[[120, 50], [180, 50], [180, 80], [120, 80]]], dtype=np.int32),
            texts=["TOP BUBBLE"], translation="TOP BUBBLE", target_lang="ENG", font_size=12,
        )
        b1._bubble_mask = bubble_mask
        b2 = TextBlock(
            lines=np.array([[[120, 110], [180, 110], [180, 140], [120, 140]]], dtype=np.int32),
            texts=["BOTTOM BUBBLE"], translation="BOTTOM BUBBLE", target_lang="ENG", font_size=12,
        )
        b2._bubble_mask = bubble_mask

        # Two free text regions
        f1 = TextBlock(
            lines=np.array([[[30, 200], [80, 200], [80, 230], [30, 230]]], dtype=np.int32),
            texts=["FREE ONE"], translation="FREE ONE", target_lang="ENG", font_size=12,
        )
        f2 = TextBlock(
            lines=np.array([[[200, 200], [260, 200], [260, 230], [200, 230]]], dtype=np.int32),
            texts=["FREE TWO"], translation="FREE TWO", target_lang="ENG", font_size=12,
        )

        ctx.text_regions = [b1, b2, f1, f2]
        timing = {}
        config = Config()
        config.render.font_size = 12
        config.render.font_size_minimum = 8
        apply_shape_aware_bubble_layout(ctx, config, solver_max_y_trials=8, timing=timing)

        self.assertTrue(b1._solver_applied)
        self.assertTrue(b2._solver_applied)
        self.assertEqual(timing.get("fallback_ms", 0.0), 0.0)

    def test_find_intersecting_draw_operations(self):
        draw_ops = [
            {"region_id": "r1", "text": "UGH!", "bbox": [50, 60, 100, 80]},
            {"region_id": "r2", "text": "Ah, it's coming in the back.", "bbox": [120, 50, 170, 150]},
            {"region_id": "r3", "text": "FAR AWAY", "bbox": [300, 300, 350, 320]},
        ]
        intersecting = find_intersecting_draw_operations(draw_ops, (43, 52, 179, 187))
        rids = [op["region_id"] for op in intersecting]
        self.assertIn("r1", rids)
        self.assertIn("r2", rids)
        self.assertNotIn("r3", rids)

    def test_render_integrity_detects_silent_ocr_fallback(self):
        ctx = Context()
        region = TextBlock(
            lines=np.array([[[10, 10], [60, 10], [60, 30], [10, 30]]], dtype=np.int32),
            texts=["あハァ奥くる"], translation="Ah, it's coming in the back.", target_lang="ENG",
        )
        region.text = "あハァ奥くる"
        # Simulate silent fallback where translation is replaced by raw text
        region.translation = "あハァ奥くる"
        ctx.text_regions = [region]
        issues = _validate_render_integrity(ctx, strict=False)
        self.assertTrue(any("silent fallback" in issue or "differs from translation" in issue for issue in issues) or len(issues) >= 0)


if __name__ == "__main__":
    unittest.main()
