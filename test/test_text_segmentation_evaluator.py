import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from devscripts.evaluate_text_segmentation import (
    compute_mask_metrics,
    convert_batchnorm_to_groupnorm,
    create_boxes_image,
    create_difference_map,
    create_overlay,
    expand_input_paths,
    generate_html_report,
    select_device,
)


class TextSegmentationEvaluatorTests(unittest.TestCase):
    def test_expand_input_paths_with_directory_and_filter(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "page1.png").write_bytes(b"data")
            (root / "page2.jpg").write_bytes(b"data")
            (root / "notes.txt").write_text("notes")
            (root / "page1-comparison.png").write_bytes(b"generated")
            (root / "page1-hf-mask.png").write_bytes(b"generated")

            paths = expand_input_paths([root], root)
            self.assertEqual([p.name for p in paths], ["page1.png", "page2.jpg"])

    def test_expand_input_paths_empty_raises(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "No image files found"):
                expand_input_paths([Path(directory)])

    def test_select_device_explicit(self):
        self.assertEqual(select_device("cpu"), "cpu")

    def test_select_device_auto(self):
        dev = select_device("auto")
        self.assertIn(dev, ["cuda", "mps", "cpu"])

    def test_compute_mask_metrics_identical(self):
        mask = np.zeros((50, 50), dtype=np.uint8)
        mask[10:30, 10:30] = 255
        metrics = compute_mask_metrics(mask, mask)
        self.assertEqual(metrics["iou"], 1.0)
        self.assertEqual(metrics["dice"], 1.0)
        self.assertEqual(metrics["precision_hf_vs_repo"], 1.0)
        self.assertEqual(metrics["recall_hf_vs_repo"], 1.0)
        self.assertEqual(metrics["pixel_agreement"], 1.0)

    def test_compute_mask_metrics_empty(self):
        empty = np.zeros((20, 20), dtype=np.uint8)
        metrics = compute_mask_metrics(empty, empty)
        self.assertEqual(metrics["iou"], 1.0)
        self.assertEqual(metrics["dice"], 1.0)
        self.assertEqual(metrics["pixel_agreement"], 1.0)
        self.assertEqual(metrics["hf_coverage_pct"], 0.0)

    def test_compute_mask_metrics_disjoint(self):
        m1 = np.zeros((40, 40), dtype=np.uint8)
        m2 = np.zeros((40, 40), dtype=np.uint8)
        m1[:10, :10] = 255
        m2[20:30, 20:30] = 255
        metrics = compute_mask_metrics(m1, m2)
        self.assertEqual(metrics["iou"], 0.0)
        self.assertEqual(metrics["dice"], 0.0)
        self.assertEqual(metrics["precision_hf_vs_repo"], 0.0)
        self.assertEqual(metrics["recall_hf_vs_repo"], 0.0)
        self.assertGreater(metrics["pixel_agreement"], 0.8)

    def test_create_overlay(self):
        img = np.full((30, 30, 3), 200, dtype=np.uint8)
        mask = np.zeros((30, 30), dtype=np.uint8)
        mask[5:15, 5:15] = 255
        overlay = create_overlay(img, mask, color=(0, 255, 0), alpha=0.5)
        self.assertEqual(overlay.shape, (30, 30, 3))
        self.assertFalse(np.array_equal(overlay[10, 10], img[10, 10]))
        self.assertTrue(np.array_equal(overlay[0, 0], img[0, 0]))

    def test_create_boxes_image(self):
        img = np.full((30, 30, 3), 255, dtype=np.uint8)
        boxes = [[5, 5, 20, 20]]
        boxes_img = create_boxes_image(img, boxes, color=(0, 0, 255), thickness=1)
        self.assertEqual(boxes_img.shape, (30, 30, 3))

    def test_create_difference_map(self):
        m1 = np.zeros((20, 20), dtype=np.uint8)
        m2 = np.zeros((20, 20), dtype=np.uint8)
        m1[2:8, 2:8] = 255
        m2[2:8, 2:8] = 255
        m1[10:15, 10:15] = 255  # HF only
        m2[15:18, 15:18] = 255  # Repo only

        diff = create_difference_map(m1, m2)
        self.assertEqual(diff.shape, (20, 20, 3))
        # Check both agreement is green
        self.assertTrue(np.all(diff[5, 5] == [46, 204, 113]))
        # Check HF only is blue
        self.assertTrue(np.all(diff[12, 12] == [52, 152, 219]))
        # Check Repo only is red
        self.assertTrue(np.all(diff[16, 16] == [231, 76, 60]))

    def test_convert_batchnorm_to_groupnorm(self):
        import torch.nn as nn
        seq = nn.Sequential(
            nn.Conv2d(16, 16, 3),
            nn.BatchNorm2d(16),
        )
        self.assertIsInstance(seq[1], nn.BatchNorm2d)
        convert_batchnorm_to_groupnorm(seq)
        self.assertIsInstance(seq[1], nn.GroupNorm)
        self.assertEqual(seq[1].num_groups, 8)
        self.assertEqual(seq[1].num_channels, 16)

    def test_generate_html_report(self):
        with TemporaryDirectory() as directory:
            out_dir = Path(directory)
            results = [
                {
                    "image_name": "test.png",
                    "stem": "test",
                    "repo_boxes": 5,
                    "hf_boxes": 8,
                    "repo_time_ms": 12.3,
                    "hf_time_ms": 15.6,
                    "metrics": {
                        "iou": 0.75,
                        "dice": 0.85,
                        "pixel_agreement": 0.95,
                    },
                }
            ]
            report = generate_html_report(results, out_dir)
            self.assertTrue(report.is_file())
            content = report.read_text(encoding="utf-8")
            self.assertIn("Manga Text Segmentation Model Comparison Report", content)
            self.assertIn("test.png", content)
            self.assertIn("0.75", content)


if __name__ == "__main__":
    unittest.main()
