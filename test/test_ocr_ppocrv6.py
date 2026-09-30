import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np

from manga_translator.config import Config, Ocr, OcrConfig
from manga_translator.ocr import OCRS, dispatch, get_ocr
from manga_translator.ocr.model_ppocrv6 import (
    ModelPPOCRv6,
    call_ocr_with_policy,
    decode_ctc_greedy,
    estimate_crop_colors,
    is_cpu_ocr,
    prepare_crop_tensor,
)
from manga_translator.ocr_stage import run_ocr
from manga_translator.utils import Context, Quadrilateral
from manga_translator.utils.model_cache import (
    SharedModelExecutor,
    reset_model_executor,
    set_model_executor,
)
from docker_prepare import is_model_selected


class TestPPOCRv6(unittest.IsolatedAsyncioTestCase):
    def test_registry_and_docker_prepare_selection(self):
        self.assertIs(OCRS[Ocr.ppocrv6], ModelPPOCRv6)
        self.assertTrue(is_cpu_ocr(Ocr.ppocrv6))
        self.assertTrue(is_cpu_ocr("ppocrv6"))
        self.assertFalse(is_cpu_ocr(Ocr.ocr48px_ctc))
        self.assertTrue(is_model_selected("ocr", "ppocrv6", {"ppocrv6"}))
        self.assertTrue(is_model_selected("ocr", "ppocrv6", {"ocr.ppocrv6"}))
        self.assertTrue(is_model_selected("ocr", "ppocrv6", {"ppocr"}))
        self.assertFalse(is_model_selected("ocr", "ppocrv6", {"mocr"}))

    def test_prepare_crop_tensor_preserves_aspect_ratio_without_stretching(self):
        crop = np.zeros((24, 60, 3), dtype=np.uint8)
        crop[:, :30] = 255
        tensor, width = prepare_crop_tensor(crop)
        # 24 -> 48 height (2x scale), so 60 -> 120 width
        self.assertEqual(width, 120)
        self.assertEqual(tensor.shape, (3, 48, 120))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertAlmostEqual(float(tensor[:, :, 0].mean()), 1.0, places=4)
        self.assertAlmostEqual(float(tensor[:, :, -1].mean()), -1.0, places=4)

    def test_decode_ctc_greedy_collapses_repeats_and_blanks(self):
        vocab = ["<blank>", "あ", "い", "う", " "]
        # Time steps: blank, 'あ', 'あ', blank, 'い', ' ', 'う'
        probs = np.array(
            [
                [0.95, 0.02, 0.01, 0.01, 0.01],
                [0.01, 0.90, 0.03, 0.03, 0.03],
                [0.02, 0.80, 0.06, 0.06, 0.06],
                [0.92, 0.02, 0.02, 0.02, 0.02],
                [0.01, 0.03, 0.85, 0.05, 0.06],
                [0.02, 0.02, 0.02, 0.04, 0.90],
                [0.01, 0.02, 0.02, 0.92, 0.03],
            ],
            dtype=np.float32,
        )
        text, conf = decode_ctc_greedy(probs, vocab)
        self.assertEqual(text, "あい う")
        expected_conf = (0.90 + 0.85 + 0.90 + 0.92) / 4.0
        self.assertAlmostEqual(conf, expected_conf, places=5)

    def test_estimate_crop_colors_detects_dark_text_on_light_bubble_and_inverted(self):
        # Light bubble (245, 245, 240) with dark navy ink (20, 30, 60) in the center
        light_bubble = np.full((48, 96, 3), (245, 245, 240), dtype=np.uint8)
        light_bubble[12:36, 20:76] = (20, 30, 60)
        fg, bg = estimate_crop_colors(light_bubble)
        self.assertLess(fg[0], 50)
        self.assertGreater(bg[0], 220)

        # Dark bubble (15, 15, 20) with bright white text (240, 240, 245) in the center
        dark_bubble = np.full((48, 96, 3), (15, 15, 20), dtype=np.uint8)
        dark_bubble[12:36, 20:76] = (240, 240, 245)
        fg_inv, bg_inv = estimate_crop_colors(dark_bubble)
        self.assertGreater(fg_inv[0], 210)
        self.assertLess(bg_inv[0], 40)

    def test_recognize_pages_sync_groups_only_identical_widths(self):
        model = ModelPPOCRv6()
        model.vocab = ["<blank>", "A", "B"]
        seen_shapes = []

        class FakeSession:
            def run(self, _outputs, feed):
                x = feed["x"]
                seen_shapes.append(x.shape)
                batch_size = x.shape[0]
                width = x.shape[3]
                # Emit 'A' for width 48, 'B' for width > 48
                token_idx = 1 if width == 48 else 2
                probs = np.zeros((batch_size, 3, 3), dtype=np.float32)
                probs[:, 0, 0] = 0.99
                probs[:, 1, token_idx] = 0.90
                probs[:, 2, 0] = 0.99
                return [probs]

        model.session = FakeSession()
        img = np.full((120, 240, 3), 250, dtype=np.uint8)
        img[10:58, 10:190] = 20

        # Two square regions (48x48 -> width 48) and one wide region (96x48 -> width 96)
        q1 = Quadrilateral(np.array([[10, 10], [58, 10], [58, 58], [10, 58]], dtype=np.int32), "", 0.0)
        q2 = Quadrilateral(np.array([[70, 10], [118, 10], [118, 58], [70, 58]], dtype=np.int32), "", 0.0)
        q3 = Quadrilateral(np.array([[10, 60], [106, 60], [106, 108], [10, 108]], dtype=np.int32), "", 0.0)

        results = model._recognize_pages_sync([(img, [q1, q2, q3], OcrConfig(ocr=Ocr.ppocrv6))], verbose=False)
        self.assertEqual(len(results), 1)
        out_regions = results[0]
        self.assertEqual(len(out_regions), 3)
        self.assertEqual((q1.text, q2.text, q3.text), ("A", "A", "B"))
        # Identical widths (48) should be batched into (2, 3, 48, 48), and width 96 run unpadded as (1, 3, 48, 96)
        self.assertCountEqual(seen_shapes, [(2, 3, 48, 48), (1, 3, 48, 96)])

    async def test_ocr_stage_and_executor_bypass_gpu_locks_for_ppocrv6(self):
        executor = SharedModelExecutor(max_concurrent_calls=1)
        # Lock the single GPU slot to prove ppocrv6 does not block on it
        executor._slots.acquire()
        token = set_model_executor(executor)
        try:
            fake_model = ModelPPOCRv6()
            fake_model._loaded = True
            fake_model._requested_device = "cpu"
            fake_model.recognize = AsyncMock(return_value=[])
            with patch("manga_translator.ocr.get_ocr", return_value=fake_model):
                img = np.zeros((32, 32, 3), dtype=np.uint8)
                out = await dispatch(Ocr.ppocrv6, img, [], OcrConfig(ocr=Ocr.ppocrv6), device="mps")
                self.assertEqual(out, [])
        finally:
            reset_model_executor(token)
            executor._slots.release()
            executor._pool.shutdown(wait=False)

        # Verify run_ocr does not call owner._mps_call when ocr is ppocrv6
        owner = SimpleNamespace(
            _model_usage_timestamps={},
            verbose=False,
            device="mps",
            _mps_call=AsyncMock(side_effect=AssertionError("_mps_call should not be used for ppocrv6")),
        )
        cfg = Config.model_validate({"ocr": {"ocr": "ppocrv6"}})
        ctx = Context()
        ctx.img_rgb = np.zeros((32, 32, 3), dtype=np.uint8)
        ctx.textlines = []
        dispatch_mock = AsyncMock(return_value=[])
        async def fake_cpu_stage(fn, *a, **_kw):
            return await fn(*a)

        with patch("manga_translator.ocr.model_ppocrv6.run_cpu_stage", new=AsyncMock(side_effect=fake_cpu_stage)) as cpu_stage_mock:
            res = await run_ocr(
                owner,
                cfg,
                ctx,
                dispatch_ocr=dispatch_mock,
                finish_textlines=lambda lines, _cfg, _ctx: lines,
            )
            self.assertEqual(res, [])
            cpu_stage_mock.assert_awaited_once()
            owner._mps_call.assert_not_awaited()

    async def test_real_ppocrv6_onnx_session_when_weights_present(self):
        model = ModelPPOCRv6()
        if not model._check_downloaded():
            self.skipTest("PP-OCRv6 model weights not downloaded locally")
        await model.load("mps")
        try:
            self.assertTrue(model.is_loaded())
            self.assertEqual(model.device, "cpu")
            self.assertEqual(model._requested_device, "cpu")
            self.assertGreater(len(model.vocab), 18000)
            crop = np.full((48, 96, 3), 255, dtype=np.uint8)
            q = Quadrilateral(np.array([[0, 0], [96, 0], [96, 48], [0, 48]], dtype=np.int32), "", 0.0)
            res = await model.recognize(crop, [q], OcrConfig(ocr=Ocr.ppocrv6, prob=0.0))
            self.assertIsInstance(res, list)
        finally:
            await model.unload()


if __name__ == "__main__":
    unittest.main()
