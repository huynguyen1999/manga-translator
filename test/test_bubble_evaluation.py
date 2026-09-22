import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

from devscripts.evaluate_bubbles import (
    DEFAULT_MODEL_NAME,
    MODEL_REGISTRY,
    boundary_f1,
    candidate_outside,
    evaluate_case,
    expand_input_paths,
    main,
    mask_iou,
    resolve_model,
    score_mask,
)


class BubbleEvaluationTests(unittest.TestCase):
    def test_directory_input_expands_sorted_images_and_skips_generated_outputs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "b.webp").write_bytes(b"image")
            (root / "a.PNG").write_bytes(b"image")
            (root / "notes.txt").write_text("ignore")
            (root / "a-bubble-detection.png").write_bytes(b"generated")
            paths = expand_input_paths([root], root)
            self.assertEqual([path.name for path in paths], ["a.PNG", "b.webp"])

    def test_empty_directory_input_is_rejected(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "no image files"):
                expand_input_paths([Path(directory)])

    def test_model_registry_contains_existing_and_manga109_models(self):
        self.assertEqual(DEFAULT_MODEL_NAME, "yolov8m")
        self.assertEqual(MODEL_REGISTRY["manga109"].repo_id, "juithealien/manga109-segmentation-bubble")
        self.assertEqual(MODEL_REGISTRY["manga109"].remote_filename, "best.pt")

    def test_unknown_model_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown model"):
            resolve_model("missing-model")

    def test_local_model_resolves_without_download(self):
        with TemporaryDirectory() as directory:
            with patch("devscripts.evaluate_bubbles.MODEL_DIR", Path(directory)):
                target = Path(directory) / "yolov8m_seg-speech-bubble.pt"
                target.write_bytes(b"local")
                with patch("devscripts.evaluate_bubbles._download_from_huggingface") as download:
                    spec, path, download_ms = resolve_model("yolov8m")
                download.assert_not_called()
                self.assertEqual(spec.name, "yolov8m")
                self.assertEqual(path, target)
                self.assertIsNone(download_ms)

    def test_cached_remote_model_skips_download(self):
        with TemporaryDirectory() as directory:
            with patch("devscripts.evaluate_bubbles.MODEL_DIR", Path(directory)):
                target = Path(directory) / "manga109" / "best.pt"
                target.parent.mkdir()
                target.write_bytes(b"cached")
                with patch("devscripts.evaluate_bubbles._download_from_huggingface") as download:
                    spec, path, download_ms = resolve_model("manga109")
                download.assert_not_called()
                self.assertEqual(spec.name, "manga109")
                self.assertEqual(path, target)
                self.assertIsNone(download_ms)

    def test_missing_remote_model_is_downloaded_and_timed(self):
        with TemporaryDirectory() as directory:
            with patch("devscripts.evaluate_bubbles.MODEL_DIR", Path(directory)):
                downloaded = Path(directory) / "cached" / "best.pt"
                downloaded.parent.mkdir()
                downloaded.write_bytes(b"weights")
                with patch("devscripts.evaluate_bubbles._download_from_huggingface", return_value=downloaded):
                    spec, path, download_ms = resolve_model("manga109")
                self.assertEqual(spec.name, "manga109")
                self.assertTrue(path.is_file())
                self.assertEqual(path.read_bytes(), b"weights")
                self.assertIsNotNone(download_ms)

    def test_download_failure_is_readable(self):
        with TemporaryDirectory() as directory:
            with patch("devscripts.evaluate_bubbles.MODEL_DIR", Path(directory)):
                with patch("devscripts.evaluate_bubbles._download_from_huggingface", side_effect=OSError("offline")):
                    with self.assertRaisesRegex(RuntimeError, "could not download model 'manga109'"):
                        resolve_model("manga109")

    def test_identical_masks_score_perfectly(self):
        mask = np.zeros((40, 40), np.uint8)
        cv2.ellipse(mask, (20, 20), (12, 15), 0, 0, 360, 255, -1)
        result = score_mask(mask, mask)
        self.assertEqual(result.iou, 1.0)
        self.assertEqual(result.boundary_f1, 1.0)
        self.assertEqual(result.candidate_outside, 0.0)

    def test_shifted_mask_reports_outside_pixels(self):
        reference = np.zeros((30, 30), np.uint8)
        candidate = np.zeros_like(reference)
        reference[5:20, 5:20] = 255
        candidate[5:20, 7:22] = 255
        self.assertLess(mask_iou(candidate, reference), 1.0)
        self.assertGreater(candidate_outside(candidate, reference), 0.0)
        self.assertLess(boundary_f1(candidate, reference, tolerance=0), 1.0)

    def test_empty_masks_are_well_defined(self):
        empty = np.zeros((10, 10), np.uint8)
        result = score_mask(empty, empty)
        self.assertEqual(result.iou, 1.0)
        self.assertEqual(result.boundary_f1, 1.0)

    def test_case_writes_detection_image(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "page.png"
            cv2.imwrite(str(image_path), np.full((20, 20, 3), 255, np.uint8))
            output = root / "output"
            output.mkdir()
            result = evaluate_case(
                {"image": "page.png", "reference": {"polygon": [[2, 2], [17, 2], [17, 17], [2, 17]]}},
                root, None, output, "", 12, mode="heuristic",
            )
            self.assertTrue(Path(result["detection_image"]).is_file())
            self.assertIsNotNone(result["heuristic_ms"])
            self.assertIsNotNone(result["image_read_ms"])
            self.assertIsNotNone(result["bubble_render_ms"])
            self.assertIsNotNone(result["total_ms"])

    def test_heuristic_mode_does_not_resolve_a_model(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "page.png"
            cv2.imwrite(str(image_path), np.full((20, 20, 3), 255, np.uint8))
            output = root / "output"
            with patch("devscripts.evaluate_bubbles.resolve_model") as resolve, patch(
                "sys.argv",
                ["evaluate_bubbles.py", "--input", str(image_path), "--mode", "heuristic", "--output-dir", str(output)],
            ):
                self.assertEqual(main(), 0)
            resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
