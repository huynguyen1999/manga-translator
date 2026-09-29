"""Unit and integration tests for Chrome Lens summary text extraction (Flow 3)."""

from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
from PIL import Image

from manga_translator.detection.panel import PanelDetection
from server.summary_chrome_lens import (
    extract_page_chrome_lens,
    lens_geometry_to_pixel,
    merge_lens_lines_with_bubbles_and_panels,
    run_chrome_lens_summary_job,
)


class SummaryChromeLensTests(unittest.IsolatedAsyncioTestCase):
    def test_lens_geometry_to_pixel_conversion(self):
        geom = {
            "center_x": 0.5,
            "center_y": 0.4,
            "width": 0.2,
            "height": 0.1,
            "angle_deg": 5.0,
        }
        x, y, w, h, quad, angle = lens_geometry_to_pixel(geom, img_w=1000, img_h=800)
        self.assertEqual(x, 400)
        self.assertEqual(y, 280)
        self.assertEqual(w, 200)
        self.assertEqual(h, 80)
        self.assertEqual(quad, [[400, 280], [600, 280], [600, 360], [400, 360]])
        self.assertEqual(angle, 5.0)

    def test_merge_lens_lines_groups_inside_bubble_and_assigns_panel(self):
        # 1000x1000 image
        # Panel covering whole page: (50, 50, 950, 950)
        panel = PanelDetection(xyxy=[50, 50, 950, 950], polygons=[], confidence=0.9, order_index=1)

        # Bubble mask at (100, 100) to (300, 300)
        bubble_mask = np.zeros((1000, 1000), dtype=np.uint8)
        bubble_mask[100:300, 100:300] = 255
        bubble = SimpleNamespace(mask=bubble_mask, confidence=0.95)

        lines_data = [
            # Line 1 inside bubble
            {
                "text": "Hello",
                "geometry": {"center_x": 0.2, "center_y": 0.15, "width": 0.1, "height": 0.04},
            },
            # Line 2 inside same bubble
            {
                "text": "World!",
                "geometry": {"center_x": 0.2, "center_y": 0.22, "width": 0.1, "height": 0.04},
            },
            # Line 3 outside bubble (free text) inside panel
            {
                "text": "*SFX*",
                "geometry": {"center_x": 0.7, "center_y": 0.7, "width": 0.1, "height": 0.05},
            },
        ]

        regions = merge_lens_lines_with_bubbles_and_panels(
            lines_data, [bubble], [panel], (1000, 1000)
        )

        self.assertEqual(len(regions), 2)
        # First region is bubble (grouped text "Hello\nWorld!")
        bubble_reg = regions[0]
        self.assertEqual(bubble_reg["original_text"], "Hello\nWorld!")
        self.assertEqual(bubble_reg["panel_index"], 0)
        self.assertEqual(len(bubble_reg["lines"]), 2)

        # Second region is free text "*SFX*"
        sfx_reg = regions[1]
        self.assertEqual(sfx_reg["original_text"], "*SFX*")
        self.assertEqual(sfx_reg["panel_index"], 0)

    async def test_extract_page_chrome_lens_with_mock_api(self):
        mock_api = SimpleNamespace(
            process_image=AsyncMock(return_value={
                "line_blocks": [
                    {
                        "text": "Chapter 1",
                        "geometry": {"center_x": 0.5, "center_y": 0.2, "width": 0.3, "height": 0.05},
                    }
                ]
            })
        )
        mock_detector = SimpleNamespace(
            detect_joint=Mock(return_value=(
                [],
                [PanelDetection(xyxy=[0, 0, 100, 100], polygons=[], confidence=0.9, order_index=1)],
            ))
        )

        image = Image.new("RGB", (100, 100), "white")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        regions, p_docs, b_docs = await extract_page_chrome_lens(
            image_bytes, mock_api, bubble_detector=mock_detector
        )

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["original_text"], "Chapter 1")
        self.assertEqual(len(p_docs), 1)
        self.assertEqual(len(b_docs), 0)
        mock_api.process_image.assert_awaited_once()

    async def test_run_chrome_lens_summary_job_flow(self):
        with tempfile.TemporaryDirectory() as temporary:
            img_path_1 = Path(temporary) / "page-1.png"
            img_path_2 = Path(temporary) / "page-2.png"
            Image.new("RGB", (100, 100), "white").save(img_path_1)
            Image.new("RGB", (100, 100), "white").save(img_path_2)

            pages = [
                {"name": "page-1.png", "path": img_path_1, "sourceType": "original"},
                {"name": "page-2.png", "path": img_path_2, "sourceType": "original"},
            ]

            job = SimpleNamespace(
                pages=pages,
                store=object(),
                group_value="OriginalManga",
                clean_title="OriginalManga",
                refresh_text=False,
                has_group_text=False,
                extraction_required=True,
                pages_with_text=0,
                pause_event=None,
            )

            runtime = SimpleNamespace(
                summary_log=Mock(),
                update_summary_job_for=AsyncMock(),
                is_page_text_extracted=lambda *_a, **_kw: False,
                empty_device_cache=Mock(),
                get_store=lambda: None,
            )

            mock_regions = [{"original_text": "Extracted line", "x": 0, "y": 0, "width": 50, "height": 20}]
            mock_panels = [{"xyxy": [0, 0, 100, 100], "order": 1}]
            mock_bubbles = []

            with patch("server.summary_chrome_lens.extract_page_chrome_lens", AsyncMock(return_value=(mock_regions, mock_panels, mock_bubbles))):
                with patch("server.summary_ocr.persist_summary_ocr", AsyncMock()):
                    pages_with_text, failed, ocr_errors = await run_chrome_lens_summary_job(job, runtime)

            self.assertEqual(pages_with_text, 2)
            self.assertEqual(len(failed), 0)
            self.assertEqual(len(ocr_errors), 0)
            self.assertEqual(pages[0]["textRegions"], mock_regions)
            self.assertEqual(pages[0]["panel_detections"], mock_panels)
            runtime.update_summary_job_for.assert_awaited()


if __name__ == "__main__":
    unittest.main()
