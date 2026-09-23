import numpy as np

from manga_translator.config import OcrConfig
from manga_translator.ocr.batching import recognize_ctc_batch
from manga_translator.utils import Quadrilateral


class _FakeModel:
    dictionary = ["A"]

    def __init__(self):
        self.batch_sizes = []

    def decode(self, images, widths, offset, verbose=False):
        self.batch_sizes.append(len(images))
        return [[(0, 0.0, 0.2, 0.3, 0.4, 0.8, 0.8, 0.8)] for _ in images]


class _FakeOCR:
    use_gpu = False
    device = "cpu"

    def __init__(self):
        self.model = _FakeModel()

    @staticmethod
    def _generate_text_direction(lines):
        return [(line, "h") for line in lines]


def _line():
    return Quadrilateral(
        np.array([[5, 5], [25, 5], [25, 15], [5, 15]]), "", 1.0
    )


def test_ctc_microbatch_maps_predictions_to_each_page():
    ocr = _FakeOCR()
    image = np.full((32, 32, 3), 255, dtype=np.uint8)
    config = OcrConfig(prob=0.1, ignore_bubble=0)

    outputs = recognize_ctc_batch(
        ocr,
        [(image, [_line()], config), (image, [_line()], config)],
    )

    assert ocr.model.batch_sizes == [2]
    assert [[line.text for line in page] for page in outputs] == [["A"], ["A"]]


def test_ctc_microbatch_keeps_the_existing_sixteen_crop_limit():
    ocr = _FakeOCR()
    image = np.full((32, 32, 3), 255, dtype=np.uint8)
    config = OcrConfig(prob=0.1, ignore_bubble=0)

    outputs = recognize_ctc_batch(
        ocr,
        [(image, [_line() for _ in range(9)], config), (image, [_line() for _ in range(8)], config)],
    )

    assert ocr.model.batch_sizes == [16, 1]
    assert [len(page) for page in outputs] == [9, 8]
