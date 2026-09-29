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


def test_suppress_overlapping_bubbles_filters_lower_confidence_superset():
    from manga_translator.detection.bubble import suppress_overlapping_bubbles

    mask_small = np.zeros((200, 200), np.uint8)
    mask_small[50:150, 50:150] = 255  # 100x100 area = 10000

    mask_large = np.zeros((200, 200), np.uint8)
    mask_large[20:180, 20:180] = 255  # 160x160 area = 25600, fully contains small

    b_high_conf = BubbleDetection(mask_small, 0.85)
    b_low_conf = BubbleDetection(mask_large, 0.35)

    kept = suppress_overlapping_bubbles([b_high_conf, b_low_conf])
    assert len(kept) == 1
    assert kept[0].confidence == 0.85


def test_group_regions_prunes_overlapping_bubble_mask_portions():
    # Bubble 1 (high conf): covers lower half [50:100, 0:100]
    # Bubble 2 (low conf): covers both upper [0:50, 0:100] and lower [50:100, 0:100]
    mask1 = np.zeros((100, 100), np.uint8)
    mask1[50:100, 0:100] = 255

    mask2 = np.zeros((100, 100), np.uint8)
    mask2[0:100, 0:100] = 255

    # Region 1 is in upper half [10:30, 10:30]
    # Region 2 is in lower half [60:80, 60:80]
    r1 = TextBlock([[[10, 10], [30, 10], [30, 30], [10, 30]]], texts=["top"])
    r2 = TextBlock([[[10, 60], [30, 60], [30, 80], [10, 80]]], texts=["bottom"])

    b1 = BubbleDetection(mask1, 0.80)
    b2 = BubbleDetection(mask2, 0.40)

    results = group_regions_by_bubbles([r1, r2], [b1, b2])
    by_text = {r.text: r for r in results}

    # r2 was assigned to bubble_0 (b1)
    assert by_text["bottom"].bubble_id == "bubble_0"
    # r1 was assigned to bubble_1 (b2), but its mask had b1 subtracted, so it does not contain the lower half
    assert by_text["top"].bubble_id == "bubble_1"
    assert np.all(by_text["top"]._bubble_mask[50:100, :] == 0)
