import copy
from functools import cached_property

import cv2
import numpy as np


def group_regions_by_bubbles(regions, detections, minimum_overlap: float = 0.35, group: bool = False):
    """Associate OCR regions with bubbles; merge only when requested."""
    if not detections:
        for index, region in enumerate(regions):
            if not getattr(region, "region_id", None):
                region.region_id = f"region_{index}"
            if not getattr(region, "source_region_ids", None):
                region.source_region_ids = [str(region.region_id)]
            if not getattr(region, "source_regions", None):
                region.source_regions = [_source_region_snapshot(region, index)]
        return list(regions)
    for index, region in enumerate(regions):
        region._bubble_source_order = index
        if not getattr(region, "region_id", None):
            region.region_id = f"region_{index}"
        if not getattr(region, "source_region_ids", None):
            region.source_region_ids = [str(region.region_id)]
        if not getattr(region, "source_regions", None):
            region.source_regions = [_source_region_snapshot(region, index)]
    assignments = {}
    for index, region in enumerate(regions):
        polygon = np.zeros(detections[0].mask.shape, np.uint8)
        cv2.fillPoly(polygon, [np.asarray(line, np.int32) for line in region.lines], 1)
        area = max(1, int(np.count_nonzero(polygon)))
        scores = [np.count_nonzero(polygon & (detection.mask > 0)) / area for detection in detections]
        if scores and max(scores) >= minimum_overlap:
            candidates = [i for i, score in enumerate(scores) if score >= minimum_overlap]
            detection_index = max(
                candidates,
                key=lambda i: scores[i] * detections[i].confidence,
            )
            assignments.setdefault(detection_index, []).append((index, region))

    result = []
    assigned = {index for members in assignments.values() for index, _ in members}
    for index, region in enumerate(regions):
        if index not in assigned:
            result.append(region)
    for detection_index, members in assignments.items():
        members.sort(key=lambda item: item[0])
        other_higher_masks = [
            detections[j].mask for j in assignments.keys()
            if j != detection_index and detections[j].confidence >= detections[detection_index].confidence
        ]
        assigned_mask = _clean_assigned_bubble_mask(detections[detection_index].mask, members, other_higher_masks)

        if not group:
            for _, region in members:
                region._bubble_mask = assigned_mask
                region.bubble_id = f"bubble_{detection_index}"
                region._bubble_detection_confidence = detections[detection_index].confidence
                result.append(region)
            continue

        preserved = [item for item in members if getattr(item[1], "translation_policy", None) == "preserve"]
        for _, region in preserved:
            region._bubble_mask = assigned_mask
            region.bubble_id = f"bubble_{detection_index}"
            region._bubble_detection_confidence = detections[detection_index].confidence
            result.append(region)
        members = [item for item in members if getattr(item[1], "translation_policy", None) != "preserve"]

        if len(members) == 1:
            region = members[0][1]
            region._bubble_mask = assigned_mask
            region.bubble_id = f"bubble_{detection_index}"
            region._bubble_detection_confidence = detections[detection_index].confidence
            result.append(region)
        elif len(members) > 1:
            region = copy.copy(members[0][1])
            for name, descriptor in vars(type(region)).items():
                if isinstance(descriptor, cached_property):
                    region.__dict__.pop(name, None)
            region.lines = np.concatenate([item.lines for _, item in members])
            region.texts = [item.text for _, item in members]
            region.text = "\n".join(region.texts)
            region.group_id = f"bubble_{detection_index}"
            region.bubble_id = f"bubble_{detection_index}"
            region.region_id = region.group_id
            region.source_region_ids = [
                str(getattr(item, "region_id", f"source_{source_index}"))
                for source_index, item in members
            ]
            region.group_members = list(region.source_region_ids)
            region.source_regions = [
                _source_region_snapshot(item, source_index) for source_index, item in members
            ]
            region._bubble_mask = assigned_mask
            region._bubble_detection_confidence = detections[detection_index].confidence
            result.append(region)
    return sorted(result, key=lambda item: getattr(item, "_bubble_source_order", 0))


def _clean_assigned_bubble_mask(mask: np.ndarray, members: list, other_masks: list[np.ndarray] | None = None) -> np.ndarray:
    """Trim assigned bubble mask by subtracting overlapping higher-confidence masks and keeping relevant components."""
    if mask is None or not np.any(mask):
        return mask
    clean_mask = mask.copy()
    if other_masks:
        for other in other_masks:
            if other is not None and other.shape == clean_mask.shape:
                clean_mask[other > 0] = 0
    binary = (clean_mask > 0).astype(np.uint8)
    num_labels, labels, _, _ = cv2.connectedComponentsWithStats(binary, 8)
    if num_labels <= 2:
        return clean_mask if np.any(clean_mask) else mask
    reg_mask = np.zeros_like(binary, dtype=np.uint8)
    for _, reg in members:
        lines = getattr(reg, "lines", None)
        if lines is not None and len(lines):
            cv2.fillPoly(reg_mask, [np.asarray(line, np.int32) for line in lines], 1)
        elif getattr(reg, "xyxy", None) is not None:
            x1, y1, x2, y2 = [int(v) for v in reg.xyxy]
            reg_mask[max(0, y1):min(binary.shape[0], y2), max(0, x1):min(binary.shape[1], x2)] = 1
    kept = np.zeros_like(binary, dtype=np.uint8)
    for l in range(1, num_labels):
        comp = (labels == l)
        if np.any(comp & (reg_mask > 0)):
            kept |= comp.astype(np.uint8)
    if np.any(kept):
        return kept * 255
    return clean_mask if np.any(clean_mask) else mask


def _source_region_snapshot(region, reading_order: int):
    """Capture source identity and geometry before a bubble group is merged."""
    lines = np.asarray(getattr(region, "lines", []))
    return {
        "id": str(getattr(region, "region_id", f"source_{reading_order}")),
        "polygons": lines.tolist(),
        "bbox": np.asarray(getattr(region, "xyxy", [0, 0, 0, 0])).tolist(),
        "centroid": np.asarray(getattr(region, "center", [0, 0])).astype(float).tolist(),
        "source_text": str(getattr(region, "text", "") or ""),
        "reading_order": int(reading_order),
    }
