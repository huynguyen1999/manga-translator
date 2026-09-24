
from typing import Tuple, List
import numpy as np
import cv2
import math
from time import perf_counter

from tqdm import tqdm
from shapely.geometry import Polygon
# from sklearn.mixture import BayesianGaussianMixture
# from functools import reduce
# from collections import defaultdict
# from scipy.optimize import linear_sum_assignment

from ..utils import Quadrilateral

COLOR_RANGE_SIGMA = 1.5 # how many stddev away is considered the same color

def save_rgb(fn, img):
    if len(img.shape) == 3 and img.shape[2] == 3:
        cv2.imwrite(fn, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    else:
        cv2.imwrite(fn, img)

def area_overlap(x1, y1, w1, h1, x2, y2, w2, h2):  # returns None if rectangles don't intersect
    x_overlap = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
    y_overlap = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
    return x_overlap * y_overlap

def dist(x1, y1, x2, y2):
    return math.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))

def rect_distance(x1, y1, x1b, y1b, x2, y2, x2b, y2b):
    left = x2b < x1
    right = x1b < x2
    bottom = y2b < y1
    top = y1b < y2
    if top and left:
        return dist(x1, y1b, x2b, y2)
    elif left and bottom:
        return dist(x1, y1, x2b, y2b)
    elif bottom and right:
        return dist(x1b, y1, x2, y2b)
    elif right and top:
        return dist(x1b, y1b, x2, y2)
    elif left:
        return x1 - x2b
    elif right:
        return x2 - x1b
    elif bottom:
        return y1 - y2b
    elif top:
        return y2 - y1b
    else:             # rectangles intersect
        return 0

def extend_rect(x, y, w, h, max_x, max_y, extend_size):
    x1 = max(x - extend_size, 0)
    y1 = max(y - extend_size, 0)
    w1 = min(w + extend_size * 2, max_x - x1)
    h1 = min(h + extend_size * 2, max_y - y1)
    return x1, y1, w1, h1

def complete_mask_fill(text_lines: List[Tuple[int, int, int, int]]):
    for (x, y, w, h) in text_lines:
        final_mask = cv2.rectangle(final_mask, (x, y), (x + w, y + h), (255), -1)
    return final_mask

try:
    from pydensecrf.utils import compute_unary, unary_from_softmax
    import pydensecrf.densecrf as dcrf
except ImportError:
    compute_unary = unary_from_softmax = dcrf = None

def refine_mask(rgbimg, rawmask):
    if dcrf is None or unary_from_softmax is None:
        return rawmask.squeeze() if len(rawmask.shape) == 3 else rawmask
    if len(rawmask.shape) == 2:
        rawmask = rawmask[:, :, None]
    mask_softmax = np.concatenate([cv2.bitwise_not(rawmask)[:, :, None], rawmask], axis=2)
    mask_softmax = mask_softmax.astype(np.float32) / 255.0
    n_classes = 2
    feat_first = mask_softmax.transpose((2, 0, 1)).reshape((n_classes,-1))
    unary = unary_from_softmax(feat_first)
    unary = np.ascontiguousarray(unary)

    d = dcrf.DenseCRF2D(rgbimg.shape[1], rgbimg.shape[0], n_classes)

    d.setUnaryEnergy(unary)
    d.addPairwiseGaussian(sxy=1, compat=3, kernel=dcrf.DIAG_KERNEL,
                            normalization=dcrf.NO_NORMALIZATION)

    d.addPairwiseBilateral(sxy=23, srgb=7, rgbim=rgbimg,
                        compat=20,
                        kernel=dcrf.DIAG_KERNEL,
                        normalization=dcrf.NO_NORMALIZATION)
    Q = d.inference(5)
    res = np.argmax(Q, axis=0).reshape((rgbimg.shape[0], rgbimg.shape[1]))
    crf_mask = np.array(res * 255, dtype=np.uint8)
    return crf_mask

def complete_mask(
    img: np.ndarray,
    mask: np.ndarray,
    textlines: List[Quadrilateral],
    keep_threshold=1e-2,
    dilation_offset=0,
    kernel_size=3,
    profile=None,
):
    """Refine text components in per-line crops, without page masks per line."""
    total_start = perf_counter()
    timings = profile.setdefault("timings_ms", {}) if profile is not None else None
    workload = profile.setdefault("workload", {}) if profile is not None else None

    def record(name, started):
        if timings is not None:
            timings[name] = timings.get(name, 0.0) + (perf_counter() - started) * 1000.0

    if not textlines:
        return None

    polys = [Polygon(txtln.pts) for txtln in textlines]
    poly_bounds = [poly.bounds for poly in polys]
    for x, y, w, h in (txtln.aabb.xywh for txtln in textlines):
        cv2.rectangle(mask, (x, y), (x + w, y + h), 0, 1)

    started = perf_counter()
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    record("connected_components_ms", started)
    if workload is not None:
        workload.update({
            "image_pixels": int(img.shape[0] * img.shape[1]),
            "textline_count": len(textlines),
            "connected_component_count": max(0, num_labels - 1),
            "full_page_textline_masks": 0,
            "shapely_intersections": 0,
            "shapely_distances": 0,
            "crop_pixels_processed": 0,
        })

    # Retain component IDs and union bounds; materialize only the needed crop.
    line_labels = [[] for _ in textlines]
    line_bounds = [None for _ in textlines]
    started = perf_counter()
    for label in range(1, num_labels):
        if stats[label, cv2.CC_STAT_AREA] <= 9:
            continue

        x1 = int(stats[label, cv2.CC_STAT_LEFT])
        y1 = int(stats[label, cv2.CC_STAT_TOP])
        w1 = int(stats[label, cv2.CC_STAT_WIDTH])
        h1 = int(stats[label, cv2.CC_STAT_HEIGHT])
        area1 = int(stats[label, cv2.CC_STAT_AREA])
        cc_poly = Polygon(((x1, y1), (x1 + w1, y1), (x1 + w1, y1 + h1), (x1, y1 + h1)))
        ratios = np.zeros(len(textlines), dtype=np.float32)
        for tl_idx, (poly, bounds) in enumerate(zip(polys, poly_bounds)):
            left, top, right, bottom = bounds
            if x1 + w1 <= left or right <= x1 or y1 + h1 <= top or bottom <= y1:
                continue
            if workload is not None:
                workload["shapely_intersections"] += 1
            overlap = poly.intersection(cc_poly).area
            ratios[tl_idx] = overlap / min(area1, poly.area)

        owner = int(np.argmax(ratios))
        if area1 >= polys[owner].area:
            continue
        if ratios[owner] <= keep_threshold:
            centroid = cc_poly.centroid
            distances = []
            for poly in polys:
                if workload is not None:
                    workload["shapely_distances"] += 1
                distances.append(poly.distance(centroid))
            owner = int(np.argmin(distances))
            unit = max(min(textlines[owner].font_size, w1, h1), 10)
            if distances[owner] >= 0.5 * unit:
                continue

        line_labels[owner].append(label)
        old = line_bounds[owner]
        line_bounds[owner] = (
            min(old[0], x1) if old else x1,
            min(old[1], y1) if old else y1,
            max(old[2], x1 + w1) if old else x1 + w1,
            max(old[3], y1 + h1) if old else y1 + h1,
        )
    record("component_assignment_ms", started)

    if not any(line_labels):
        return None

    final_mask = np.zeros_like(mask)
    for i, component_ids in enumerate(tqdm(line_labels, "[mask]")):
        bounds = line_bounds[i]
        if not component_ids or bounds is None:
            continue
        bx1, by1, bx2, by2 = bounds
        text_size = min(bx2 - bx1, by2 - by1, textlines[i].font_size)
        pad = int(text_size * 0.1)
        x1, y1, w1, h1 = extend_rect(bx1, by1, bx2 - bx1, by2 - by1, img.shape[1], img.shape[0], pad)
        dilate_size = max((int((text_size + dilation_offset) * 0.3) // 2) * 2 + 1, 3)
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_size, dilate_size))
        x2, y2, w2, h2 = extend_rect(x1, y1, w1, h1, img.shape[1], img.shape[0], -(-dilate_size // 2))

        owner_lookup = np.zeros(num_labels, dtype=bool)
        owner_lookup[component_ids] = True
        cc_region = np.where(owner_lookup[labels[y1:y1 + h1, x1:x1 + w1]], 255, 0).astype(np.uint8)

        # The 8px halo keeps bilateral-filter values exact inside the crop.
        filter_pad = 17 // 2
        fx1, fy1 = max(0, x1 - filter_pad), max(0, y1 - filter_pad)
        fx2, fy2 = min(img.shape[1], x1 + w1 + filter_pad), min(img.shape[0], y1 + h1 + filter_pad)
        started = perf_counter()
        filtered = cv2.bilateralFilter(img[fy1:fy2, fx1:fx2], 17, 80, 80)
        img_region = np.ascontiguousarray(filtered[y1 - fy1:y1 - fy1 + h1, x1 - fx1:x1 - fx1 + w1])
        record("bilateral_filter_ms", started)
        if workload is not None:
            workload["crop_pixels_processed"] += int(img_region.shape[0] * img_region.shape[1])

        started = perf_counter()
        refined = refine_mask(img_region, np.ascontiguousarray(cc_region))
        record("refine_component_ms", started)
        if refined.ndim == 3:
            refined = refined.squeeze()

        # Preserve unrefined component pixels in the dilation halo, as before.
        crop_labels = labels[y2:y2 + h2, x2:x2 + w2]
        dilated_region = np.where(owner_lookup[crop_labels], 255, 0).astype(np.uint8)
        dx1, dy1 = x1 - x2, y1 - y2
        dilated_region[dy1:dy1 + h1, dx1:dx1 + w1] = refined
        started = perf_counter()
        dilated_region = cv2.dilate(dilated_region, kern)
        record("morphology_ms", started)
        final_mask[y2:y2 + h2, x2:x2 + w2] = cv2.bitwise_or(
            final_mask[y2:y2 + h2, x2:x2 + w2], dilated_region
        )

    started = perf_counter()
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    final_mask = cv2.dilate(final_mask, kern)
    record("compose_ms", started)
    if timings is not None:
        timings["total_ms"] = timings.get("total_ms", 0.0) + (perf_counter() - total_start) * 1000.0
    return final_mask

def unsharp(image):
    gaussian_3 = cv2.GaussianBlur(image, (3, 3), 2.0)
    return cv2.addWeighted(image, 1.5, gaussian_3, -0.5, 0, image)
