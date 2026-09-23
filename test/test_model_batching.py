import asyncio
from types import SimpleNamespace

import cv2
import numpy as np

from manga_translator.detection.bubble import BubbleDetector
from manga_translator.detection.common import dbnet_detect_batch
from manga_translator.detection.default import DefaultDetector


class _ArrayTensor:
    def __init__(self, value):
        self.value = value

    def detach(self):
        return self

    def float(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.value


def test_dbnet_pages_share_one_model_call_and_keep_their_canvas_sizes():
    images = [np.zeros((100, 200, 3), np.uint8), np.zeros((200, 100, 3), np.uint8)]
    calls = []

    def forward(batch, device):
        calls.append((batch.shape, device))
        prediction = np.zeros((2, 1, 128, 128), np.float32)
        mask = np.ones_like(prediction)
        return prediction, mask

    results = dbnet_detect_batch(
        images,
        forward,
        "cpu",
        256,
        0.3,
        0.5,
        2.3,
        interpolation=cv2.INTER_LINEAR,
        blur_before_resize=False,
    )

    assert calls == [((2, 256, 256, 3), "cpu")]
    assert [result[1].shape for result in results] == [(128, 256), (256, 128)]
    assert all(result[0] == [] for result in results)


def test_detector_batch_wrapper_preserves_per_page_filtering_and_border_removal():
    detector = DefaultDetector.__new__(DefaultDetector)
    seen_shapes = []

    async def detect_batch(images, *_args):
        seen_shapes.extend(image.shape[:2] for image in images)
        return [
            ([], np.zeros(image.shape[:2], np.uint8), None)
            for image in images
        ]

    detector._detect_batch = detect_batch
    images = [np.zeros((20, 30, 3), np.uint8), np.zeros((32, 18, 3), np.uint8)]

    results = asyncio.run(detector.detect_batch(
        images, 256, 0.3, 0.5, 2.3, False, False, False
    ))

    assert seen_shapes == [(400, 400), (400, 400)]
    assert [result[1].shape for result in results] == [(20, 30), (32, 18)]


def test_bubble_detector_predicts_pages_together_and_maps_masks_per_page():
    class FakeModel:
        def __init__(self):
            self.kwargs = None

        def predict(self, **kwargs):
            self.kwargs = kwargs
            detected = SimpleNamespace(
                masks=SimpleNamespace(data=[_ArrayTensor(np.ones((10, 12), np.float32))]),
                boxes=SimpleNamespace(conf=[0.9]),
            )
            empty = SimpleNamespace(masks=None, boxes=None)
            return [detected, empty]

    detector = BubbleDetector.__new__(BubbleDetector)
    detector.model = FakeModel()
    detector.device = "cpu"
    detector.confidence = 0.25
    detector.mask_threshold = 0.5
    detector.image_size = 64
    images = [np.zeros((10, 12, 3), np.uint8), np.zeros((8, 9, 3), np.uint8)]

    results = detector.detect_batch(images)

    assert detector.model.kwargs["batch"] == 2
    assert len(detector.model.kwargs["source"]) == 2
    assert len(results[0]) == 1
    assert results[0][0].mask.shape == (10, 12)
    assert np.all(results[0][0].mask == 255)
    assert results[1] == []
