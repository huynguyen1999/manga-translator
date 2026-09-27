"""Scale rendered editor geometry into the saved image coordinate space."""

from ..rendering.bubble_layout import encode_rendered_box


def scaled(value, scale):
    return int(round(value * scale))


def scale_segments(segments, boxes, scale_x, scale_y):
    result = []
    for segment, box in zip(segments, boxes):
        item = dict(segment)
        for key, scale in (("x", scale_x), ("width", scale_x), ("y", scale_y), ("height", scale_y)):
            if key in item:
                item[key] = scaled(item[key], scale)
        if "font_size" in item:
            item["font_size"] = scaled(item["font_size"], scale_y)
        for name in ("positioned_lines", "lines"):
            if name in item:
                item[name] = [{**line, "x": scaled(line["x"], scale_x),
                               "y": scaled(line["y"], scale_y)} for line in item[name]]
        item["rendered_png"] = encode_rendered_box(box["box"], scale_x, scale_y)
        result.append(item)
    return result
