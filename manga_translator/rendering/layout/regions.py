"""Shared region identity, content, and source-geometry preparation."""

import copy

from typing import Any, List, Optional

import numpy as np


def prepare_regions(regions: Optional[List[Any]]) -> List[Any]:
    """Assign stable IDs and capture source geometry once before layout edits."""
    for index, region in enumerate(regions or []):
        if not hasattr(region, "lines"):
            continue
        region_id = str(getattr(region, "region_id", "") or "")
        if not region_id:
            region.region_id = f"region_{index}"
            region_id = region.region_id

        source_ids = getattr(region, "source_region_ids", None)
        if isinstance(source_ids, str):
            source_ids = [source_ids]
        if not source_ids:
            members = getattr(region, "group_members", None)
            if isinstance(members, str):
                members = [members]
            source_ids = [str(member) for member in members] if members else [region_id]
        region.source_region_ids = [str(member) for member in source_ids]

        if not hasattr(region, "source_text_snapshot"):
            region.source_text_snapshot = str(getattr(region, "text", "") or "")
        if not hasattr(region, "source_geometry"):
            region.source_geometry = {
                "polygons": np.asarray(getattr(region, "lines", [])).tolist(),
                "bbox": np.asarray(getattr(region, "xyxy", [0, 0, 0, 0])).tolist(),
                "centroid": np.asarray(getattr(region, "center", [0, 0])).astype(float).tolist(),
            }

        if not getattr(region, "source_regions", None):
            region.source_regions = [{
                "id": region_id,
                "polygons": np.asarray(getattr(region, "lines", [])).tolist(),
                "bbox": np.asarray(getattr(region, "xyxy", [0, 0, 0, 0])).tolist(),
                "centroid": np.asarray(getattr(region, "center", [0, 0])).astype(float).tolist(),
                "source_text": region.source_text_snapshot,
                "reading_order": 0,
            }]
        if getattr(region, "source_font_size", None) is None:
            region.source_font_size = int(getattr(region, "font_size", 0) or 0)
        # Do not let downstream layout attempts retain mutable references to source facts.
        region.source_region_ids = copy.deepcopy(region.source_region_ids)
        region.source_regions = copy.deepcopy(region.source_regions)
    return regions or []
