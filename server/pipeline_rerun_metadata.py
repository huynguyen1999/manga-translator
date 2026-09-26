"""Recover source identity and typography from translation checkpoints."""

import numpy as np


def restore_source_metadata(regions, sources):
    by_id = {}

    def ids(region):
        return [
            str(value) for value in (
                getattr(region, "region_id", ""),
                getattr(region, "group_id", ""),
                *(getattr(region, "source_region_ids", []) or []),
            ) if value
        ]

    for source in sources:
        for source_id in ids(source):
            by_id.setdefault(source_id, source)

    for index, region in enumerate(regions):
        source = next((by_id[key] for key in ids(region) if key in by_id), None)
        if source is None and index < len(sources):
            candidate = sources[index]
            if (
                getattr(region, "text", "") == getattr(candidate, "text", "")
                and np.array_equal(
                    np.asarray(getattr(region, "lines", [])),
                    np.asarray(getattr(candidate, "lines", [])),
                )
            ):
                source = candidate
        if source is None:
            continue
        if not getattr(region, "region_id", ""):
            region.region_id = getattr(source, "region_id", "")
        source_size = getattr(region, "source_font_size", None)
        if not source_size or source_size <= 0:
            source_size = getattr(source, "source_font_size", None)
            if not source_size or source_size <= 0:
                source_size = getattr(source, "font_size", None)
            if source_size and source_size > 0:
                region.source_font_size = source_size
        for key in ("source_region_ids", "source_regions", "source_geometry", "source_text_snapshot", "bubble_id"):
            if not getattr(region, key, None) and getattr(source, key, None) is not None:
                setattr(region, key, getattr(source, key))
