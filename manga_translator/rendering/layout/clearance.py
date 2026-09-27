"""One rendered-text clearance rule for search and final validation."""

import cv2
import numpy as np


def rendered_masks_conflict(first_box, first_mask, first_font, second_box, second_mask, second_font):
    clearance = max(2, round(0.18 * min(max(1, first_font), max(1, second_font))))
    x1 = max(0, min(first_box[0], second_box[0]) - clearance)
    y1 = max(0, min(first_box[1], second_box[1]) - clearance)
    x2 = max(first_box[2], second_box[2]) + clearance
    y2 = max(first_box[3], second_box[3]) + clearance
    if first_box[2] + clearance <= second_box[0] or second_box[2] + clearance <= first_box[0]:
        return False
    if first_box[3] + clearance <= second_box[1] or second_box[3] + clearance <= first_box[1]:
        return False
    first = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
    second = np.zeros_like(first)
    for box, mask, target in ((first_box, first_mask, first), (second_box, second_mask, second)):
        left, top, right, bottom = box
        target[top - y1:bottom - y1, left - x1:right - x1] = mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * clearance + 1, 2 * clearance + 1))
    return bool(np.any(cv2.dilate(first, kernel) & second))
