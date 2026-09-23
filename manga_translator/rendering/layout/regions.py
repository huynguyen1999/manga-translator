"""Shared region identity and source-geometry preparation."""

import uuid
from typing import Any, List, Optional

import numpy as np


def prepare_regions(regions: Optional[List[Any]]) -> List[Any]:
    """Assign stable IDs and capture source geometry once before layout edits."""
    for region in regions or []:
        region_id = str(getattr(region, "region_id", "") or "")
        if not region_id:
            region.region_id = uuid.uuid4().hex
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

        if not getattr(region, "source_regions", None):
            region.source_regions = [{
                "id": region_id,
                "polygons": np.asarray(getattr(region, "lines", [])).tolist(),
                "bbox": np.asarray(getattr(region, "xyxy", [0, 0, 0, 0])).tolist(),
                "centroid": np.asarray(getattr(region, "center", [0, 0])).astype(float).tolist(),
                "source_text": str(getattr(region, "text", "") or ""),
                "reading_order": 0,
            }]
    return regions or []
