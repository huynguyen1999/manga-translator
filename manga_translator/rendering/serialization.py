import base64

import cv2
import numpy as np


def encode_safe_shape(interior, scale_x=1.0, scale_y=1.0):
    """Store one group's safe interior, not the page-wide union mask."""
    if interior is None or not np.any(interior):
        return None
    ys, xs = np.nonzero(interior)
    x, y = int(xs.min()), int(ys.min())
    crop = (interior[y:int(ys.max()) + 1, x:int(xs.max()) + 1] > 0).astype(np.uint8) * 255
    if scale_x != 1.0 or scale_y != 1.0:
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale_x)),
                                 max(1, round(crop.shape[0] * scale_y))), interpolation=cv2.INTER_NEAREST)
    ok, encoded = cv2.imencode('.png', crop)
    if not ok:
        return None
    return {"x": round(x * scale_x), "y": round(y * scale_y),
            "png": base64.b64encode(encoded).decode('ascii')}


def decode_safe_shape(shape, height, width):
    if not isinstance(shape, dict):
        return None
    try:
        if not isinstance(shape.get('png'), str) or len(shape['png']) > 2_000_000:
            return None
        raw = np.frombuffer(base64.b64decode(shape['png'], validate=True), np.uint8)
        crop = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
        x, y = int(shape['x']), int(shape['y'])
        if crop is None or x < 0 or y < 0 or x + crop.shape[1] > width or y + crop.shape[0] > height:
            return None
        interior = np.zeros((height, width), np.uint8)
        interior[y:y + crop.shape[0], x:x + crop.shape[1]] = crop > 0
        return interior
    except (KeyError, ValueError, TypeError):
        return None


def encode_rendered_box(box, scale_x=1.0, scale_y=1.0):
    """Keep the glyph pixels that passed the safe-mask check for editor display."""
    if box is None:
        return None
    if scale_x != 1.0 or scale_y != 1.0:
        box = cv2.resize(box, (max(1, round(box.shape[1] * scale_x)),
                               max(1, round(box.shape[0] * scale_y))), interpolation=cv2.INTER_LINEAR)
    ok, encoded = cv2.imencode('.png', cv2.cvtColor(box, cv2.COLOR_RGBA2BGRA))
    return base64.b64encode(encoded).decode('ascii') if ok else None


def decode_rendered_box(encoded_box):
    if not isinstance(encoded_box, str) or len(encoded_box) > 8_000_000:
        return None
    try:
        raw = np.frombuffer(base64.b64decode(encoded_box, validate=True), np.uint8)
        box = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
        if box is None or box.ndim != 3 or box.shape[2] != 4:
            return None
        return cv2.cvtColor(box, cv2.COLOR_BGRA2RGBA)
    except (ValueError, cv2.error):
        return None
