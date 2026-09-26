import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from manga_translator.ocr_stage import finish_ocr_textlines, run_ocr, run_ocr_batch


class OCRStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_ocr_restores_environment_and_preserves_textline_finish(self):
        textlines = [SimpleNamespace(text="hello"), SimpleNamespace(text="  ")]
        owner = SimpleNamespace(
            _model_usage_timestamps={},
            verbose=False,
            device="cpu",
            _mps_call=AsyncMock(return_value=textlines),
        )
        config = SimpleNamespace(
            ocr=SimpleNamespace(ocr="test-ocr"),
            render=SimpleNamespace(font_color_fg=(1, 2, 3), font_color_bg=(4, 5, 6)),
        )
        ctx = SimpleNamespace(img_rgb=object(), textlines=[])
        dispatch = object()
        analyze = Mock()
        previous_result_dir = os.environ.get("MANGA_OCR_RESULT_DIR")
        os.environ["MANGA_OCR_RESULT_DIR"] = "prior-ocr-dir"
        try:
            result = await run_ocr(
                owner,
                config,
                ctx,
                dispatch_ocr=dispatch,
                finish_textlines=lambda lines, cfg, context: finish_ocr_textlines(
                    lines, cfg, context, analyze_typography=analyze,
                ),
            )
            restored_result_dir = os.environ.get("MANGA_OCR_RESULT_DIR")
        finally:
            if previous_result_dir is None:
                os.environ.pop("MANGA_OCR_RESULT_DIR", None)
            else:
                os.environ["MANGA_OCR_RESULT_DIR"] = previous_result_dir

        owner._mps_call.assert_awaited_once_with(dispatch, "test-ocr", ctx.img_rgb, [], config.ocr, "cpu", False)
        self.assertEqual(restored_result_dir, "prior-ocr-dir")
        self.assertEqual(result, [textlines[0]])
        self.assertEqual((textlines[0].fg_r, textlines[0].fg_g, textlines[0].fg_b), (1, 2, 3))
        self.assertEqual((textlines[0].bg_r, textlines[0].bg_g, textlines[0].bg_b), (4, 5, 6))
        analyze.assert_called_once_with(textlines, ctx.img_rgb)
        self.assertIn(("ocr", "test-ocr"), owner._model_usage_timestamps)

    async def test_batch_ocr_dispatch_and_backend_mismatch_fallback(self):
        config = SimpleNamespace(ocr=SimpleNamespace(ocr="test-ocr"))
        pages = [
            (SimpleNamespace(img_rgb="image-a", textlines=["a"]), config),
            (SimpleNamespace(img_rgb="image-b", textlines=["b"]), config),
        ]
        owner = SimpleNamespace(
            _model_usage_timestamps={},
            device="cpu",
            verbose=False,
            _mps_call=AsyncMock(return_value=[["ocr-a"], ["ocr-b"]]),
        )
        dispatch = object()
        logger = SimpleNamespace(info=Mock())
        result = await run_ocr_batch(
            owner,
            pages,
            dispatch_ocr_batch=dispatch,
            finish_textlines=lambda lines, _config, _ctx: lines,
            logger=logger,
        )
        self.assertEqual(result, [["ocr-a"], ["ocr-b"]])
        owner._mps_call.assert_awaited_once_with(
            dispatch,
            "test-ocr",
            [("image-a", ["a"], config.ocr), ("image-b", ["b"], config.ocr)],
            "cpu",
            False,
        )

        fallback_owner = SimpleNamespace(_run_ocr=AsyncMock(side_effect=["one", "two"]))
        other_config = SimpleNamespace(ocr=SimpleNamespace(ocr="other-ocr"))
        fallback = await run_ocr_batch(
            fallback_owner,
            [(pages[0][0], config), (pages[1][0], other_config)],
            dispatch_ocr_batch=dispatch,
            finish_textlines=lambda lines, _config, _ctx: lines,
            logger=logger,
        )
        self.assertEqual(fallback, ["one", "two"])
        self.assertEqual(fallback_owner._run_ocr.await_count, 2)


if __name__ == "__main__":
    unittest.main()
