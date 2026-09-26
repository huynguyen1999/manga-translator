import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from manga_translator.detection_stage import run_detection, run_detection_batch


class _DetectionSettings:
    def __init__(self, **overrides):
        self.detector = "default"
        self.detection_size = 1024
        self.text_threshold = 0.5
        self.box_threshold = 0.7
        self.unclip_ratio = 2.0
        self.det_invert = False
        self.det_gamma_correct = False
        self.det_rotate = False
        self.det_auto_rotate = False
        self.__dict__.update(overrides)

    def dict(self):
        return vars(self)


class DetectionStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_detection_dispatch_and_batch_guard_are_preserved(self):
        outputs = [["page-a"], ["page-b"]]
        owner = SimpleNamespace(
            device="cpu",
            verbose=False,
            _model_usage_timestamps={},
            _mps_call=AsyncMock(return_value=outputs),
        )
        settings = _DetectionSettings()
        configs = [SimpleNamespace(detector=settings) for _ in outputs]
        contexts = [SimpleNamespace(img_rgb=f"image-{index}") for index in range(2)]
        dispatch = object()

        await run_detection(owner, configs[0], contexts[0], dispatch_detection=dispatch)
        self.assertEqual(owner._mps_call.await_args.args[0], dispatch)
        self.assertEqual(owner._mps_call.await_args.args[1:3], ("default", "image-0"))
        owner._mps_call.reset_mock()

        result = await run_detection_batch(
            owner,
            configs,
            contexts,
            dispatch_detection_batch=dispatch,
            logger=SimpleNamespace(info=lambda *args: None),
        )

        self.assertEqual(result, outputs)
        self.assertEqual(owner._mps_call.await_args.args[0], dispatch)
        self.assertEqual(owner._mps_call.await_args.args[2], ["image-0", "image-1"])
        self.assertIn(("detection", "default"), owner._model_usage_timestamps)
        with self.assertRaisesRegex(ValueError, "settings must match"):
            await run_detection_batch(
                owner,
                [configs[0], SimpleNamespace(detector=_DetectionSettings(text_threshold=0.9))],
                contexts,
                dispatch_detection_batch=dispatch,
                logger=SimpleNamespace(info=lambda *args: None),
            )


if __name__ == "__main__":
    unittest.main()
