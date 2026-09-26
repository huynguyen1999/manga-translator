import cv2
import numpy as np

from manga_translator.detection.bubble import BubbleDetection
from manga_translator.rendering.grouping import group_regions_by_bubbles
from manga_translator.utils import TextBlock


def test_bubble_association_uses_confidence_for_near_equal_coverage():
    lines = [[[10, 10], [30, 10], [30, 30], [10, 30]]]
    region = TextBlock(lines, texts=["source"], translation="translated")
    exact = np.zeros((50, 50), np.uint8)
    cv2.fillPoly(exact, [np.asarray(lines[0], np.int32)], 1)
    almost_exact = exact.copy()
    almost_exact[10, 10] = 0

    group_regions_by_bubbles(
        [region], [BubbleDetection(exact, 0.58), BubbleDetection(almost_exact, 0.86)]
    )

    assert region.bubble_id == "bubble_1"
