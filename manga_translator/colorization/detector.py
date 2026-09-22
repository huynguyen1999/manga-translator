import cv2
import numpy as np
from PIL import Image, ImageChops, ImageOps
from typing import Tuple, Dict, Any


def distance_from_grayscale(image: Image.Image) -> float:
    """
    Measures the average color difference between an image and its grayscale version.
    """
    try:
        rgb_img = image.convert('RGB')
        gray_rgb = ImageOps.grayscale(rgb_img).convert('RGB')
        diff = ImageChops.difference(rgb_img, gray_rgb)
        return float(np.mean(np.asarray(diff)))
    except Exception:
        return 0.0


def is_image_colored(image: Image.Image, color_threshold: float = 31.0) -> Tuple[bool, Dict[str, Any]]:
    """
    Robust multi-signal detection to check if an image is already colored (e.g. color cover,
    color spread, watercolor illustrations) versus black-and-white (even if scanned from
    yellowed/aged paper or with JPEG chroma artifacts).

    Returns:
        is_colored (bool): True if the image contains genuine color regions.
        details (dict): Diagnostic metrics including distance_from_grayscale, saturation, and reason.
    """
    if color_threshold is not None and color_threshold <= 0:
        return False, {'is_colored': False, 'reason': 'threshold disabled'}

    try:
        # 1. Downscale for fast analysis (<5ms)
        max_dim = 512
        w, h = image.size
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
            img_small = image.resize((new_w, new_h), Image.Resampling.BILINEAR)
        else:
            img_small = image

        rgb_img = img_small.convert('RGB')
        gray_rgb = ImageOps.grayscale(rgb_img).convert('RGB')
        diff = ImageChops.difference(rgb_img, gray_rgb)
        dist_gray = float(np.mean(np.asarray(diff)))

        # 2. HSV color space analysis
        rgb_arr = np.asarray(rgb_img)
        hsv = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2HSV)
        h_chan = hsv[:, :, 0].astype(np.float32)
        s_chan = hsv[:, :, 1].astype(np.float32) / 255.0
        v_chan = hsv[:, :, 2].astype(np.float32) / 255.0

        # Filter out dark ink lines (v < 0.12) and neutral bright paper (v > 0.96 & s < 0.08)
        valid_mask = (v_chan > 0.12) & ~((v_chan > 0.96) & (s_chan < 0.08))
        if np.sum(valid_mask) < 100:
            valid_mask = np.ones_like(v_chan, dtype=bool)

        s_valid = s_chan[valid_mask]
        h_valid = h_chan[valid_mask]

        # In aged paper/yellowed scans, paper saturation typically stays below 0.18 with uniform hue.
        # Genuine colors (clothes, skin, eyes, hair, effects) have S > 0.22.
        sat_pixels = s_valid > 0.22
        color_pixel_ratio = float(np.sum(sat_pixels)) / float(len(s_valid))
        p95_sat = float(np.percentile(s_valid, 95)) if len(s_valid) > 0 else 0.0
        hue_std = float(np.std(h_valid[sat_pixels])) if np.sum(sat_pixels) > 10 else 0.0

        is_colored = False
        reason = 'grayscale'

        # Signal 1: High global color difference
        if dist_gray >= color_threshold:
            is_colored = True
            reason = f'grayscale distance {dist_gray:.1f} >= {color_threshold}'
        # Signal 2: Significant cluster of saturated pixels (e.g. colored characters on white background)
        elif color_pixel_ratio >= 0.015 and p95_sat >= 0.28:
            is_colored = True
            reason = f'color cluster: {color_pixel_ratio*100:.1f}% pixels with S>0.22, p95={p95_sat:.2f}'
        # Signal 3: Moderate color spread with high saturation or diverse hues
        elif dist_gray >= 8.0 and p95_sat >= 0.22 and (color_pixel_ratio >= 0.008 or hue_std > 12.0):
            is_colored = True
            reason = f'color presence: dist={dist_gray:.1f}, p95={p95_sat:.2f}, ratio={color_pixel_ratio*100:.1f}%, hue_std={hue_std:.1f}'

        return is_colored, {
            'is_colored': is_colored,
            'dist_gray': dist_gray,
            'color_pixel_ratio': color_pixel_ratio,
            'p95_sat': p95_sat,
            'hue_std': hue_std,
            'reason': reason
        }

    except Exception as e:
        return False, {'is_colored': False, 'reason': f'error: {e}'}
