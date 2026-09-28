"""Panel-level structuring and transcript formatting for manga LLM localization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _extract_region_center(region: Any) -> tuple[float, float] | None:
    """Extract (cx, cy) center coordinates from a region object or dictionary."""
    if hasattr(region, "center"):
        try:
            return float(region.center[0]), float(region.center[1])
        except (IndexError, TypeError, ValueError):
            pass

    if isinstance(region, dict):
        reg_obj = region.get("region")
        if reg_obj is not None and hasattr(reg_obj, "center"):
            try:
                return float(reg_obj.center[0]), float(reg_obj.center[1])
            except (IndexError, TypeError, ValueError):
                pass

        if "center" in region:
            try:
                cx, cy = region["center"]
                return float(cx), float(cy)
            except (IndexError, TypeError, ValueError):
                pass

        if "xywh" in region:
            try:
                x, y, w, h = region["xywh"]
                return float(x + w / 2.0), float(y + h / 2.0)
            except (IndexError, TypeError, ValueError):
                pass

        if "xyxy" in region:
            try:
                x1, y1, x2, y2 = region["xyxy"]
                return float((x1 + x2) / 2.0), float((y1 + y2) / 2.0)
            except (IndexError, TypeError, ValueError):
                pass

    return None


def _extract_panel_xyxy(panel: Any) -> tuple[int, int, int, int] | None:
    """Extract (x1, y1, x2, y2) bounding box from a PanelDetection or dict."""
    if hasattr(panel, "xyxy"):
        try:
            xyxy = panel.xyxy
            if len(xyxy) >= 4:
                return int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])
        except (IndexError, TypeError, ValueError):
            pass

    if isinstance(panel, dict):
        if "xyxy" in panel:
            try:
                xyxy = panel["xyxy"]
                if len(xyxy) >= 4:
                    return int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])
            except (IndexError, TypeError, ValueError):
                pass
        if "xywh" in panel:
            try:
                x, y, w, h = panel["xywh"]
                return int(x), int(y), int(x + w), int(y + h)
            except (IndexError, TypeError, ValueError):
                pass

    return None


def assign_regions_to_panels(
    regions: list[Any],
    panels: list[Any],
) -> tuple[dict[int, list[Any]], list[Any]]:
    """Assign regions to panels by explicit panel_index or geometric containment."""
    panels_grouped: dict[int, list[Any]] = {i: [] for i in range(len(panels))}
    unassigned: list[Any] = []

    panel_boxes = [_extract_panel_xyxy(p) for p in panels]

    for item in regions:
        # Check explicit panel_index if available
        assigned_idx = None
        if isinstance(item, dict):
            if "panel_index" in item and 0 <= item["panel_index"] < len(panels):
                assigned_idx = int(item["panel_index"])
            elif item.get("region") is not None and hasattr(item["region"], "panel_index"):
                pi = item["region"].panel_index
                if 0 <= pi < len(panels):
                    assigned_idx = int(pi)
        elif hasattr(item, "panel_index") and 0 <= item.panel_index < len(panels):
            assigned_idx = int(item.panel_index)

        if assigned_idx is not None:
            panels_grouped[assigned_idx].append(item)
            continue

        # Spatial check
        center = _extract_region_center(item)
        if center is not None:
            cx, cy = center
            inside = [
                (idx, max(0, box[2] - box[0]) * max(0, box[3] - box[1]))
                for idx, box in enumerate(panel_boxes)
                if box is not None and box[0] <= cx <= box[2] and box[1] <= cy <= box[3]
            ]
            if inside:
                best_idx = min(inside, key=lambda it: it[1])[0]
                panels_grouped[best_idx].append(item)
            else:
                unassigned.append(item)
        else:
            unassigned.append(item)

    return panels_grouped, unassigned


def _format_region_dict(item: dict[str, Any], include_translation: bool = False) -> dict[str, Any]:
    formatted: dict[str, Any] = {
        "id": str(item.get("id", "")),
        "japanese": str(item.get("source") or item.get("japanese") or item.get("text", "")),
    }
    if include_translation and "final" in item:
        formatted["translation"] = str(item["final"])
    return formatted


def build_page_panel_structure(
    page_number: int,
    regions: list[dict[str, Any]],
    panels: list[Any] | None = None,
    include_translation: bool = False,
) -> dict[str, Any]:
    """Construct structured page payload.

    When panels exist: {"page": N, "panels": [...], "unassigned_regions": [...]} (unassigned omitted if empty)
    When panels do not exist: {"page": N, "regions": [...]}
    """
    if not panels:
        return {
            "page": page_number,
            "regions": [
                _format_region_dict(r, include_translation) for r in regions
            ],
        }

    panels_grouped, unassigned = assign_regions_to_panels(regions, panels)
    panels_list = []
    for idx, panel in enumerate(panels):
        order = (
            getattr(panel, "order_index", None)
            or (panel.get("order") if isinstance(panel, dict) else None)
            or (idx + 1)
        )
        panel_id = f"p{page_number}_{order:02d}"
        p_regions = panels_grouped.get(idx, [])
        if p_regions:
            panels_list.append({
                "panel_id": panel_id,
                "panel_order": order,
                "regions": [
                    _format_region_dict(r, include_translation)
                    for r in p_regions
                ],
            })

    if not panels_list and unassigned:
        return {
            "page": page_number,
            "regions": [
                _format_region_dict(r, include_translation)
                for r in unassigned
            ],
        }

    page_dict: dict[str, Any] = {
        "page": page_number,
        "panels": panels_list,
    }
    if unassigned:
        page_dict["unassigned_regions"] = [
            _format_region_dict(r, include_translation)
            for r in unassigned
        ]
    return page_dict


def format_page_transcript_for_analysis(page_data: dict[str, Any]) -> str:
    """Format token-efficient hierarchical transcript for Story Analysis."""
    page_num = page_data.get("page", 1)
    panels = page_data.get("panels")
    flat_regions = page_data.get("regions")
    unassigned = page_data.get("unassigned_regions", [])

    lines: list[str] = [f"[PAGE {page_num}]"]

    if panels:
        for panel in panels:
            p_id = panel.get("panel_id", "panel")
            p_order = panel.get("panel_order", 1)
            lines.append(f"\n[PANEL {p_id} | PANEL ORDER {p_order}]")
            for idx, reg in enumerate(panel.get("regions", []), 1):
                r_id = reg.get("id", f"r{idx}")
                text = reg.get("japanese") or reg.get("source") or reg.get("text", "")
                lines.append(f"[{r_id} | estimated order {idx}]\n{text}")

        if unassigned:
            lines.append("\n[UNASSIGNED REGIONS]")
            for idx, reg in enumerate(unassigned, 1):
                r_id = reg.get("id", f"r{idx}")
                text = reg.get("japanese") or reg.get("source") or reg.get("text", "")
                lines.append(f"[{r_id} | estimated order {idx}]\n{text}")
    elif flat_regions:
        for idx, reg in enumerate(flat_regions, 1):
            r_id = reg.get("id", f"r{idx}")
            text = reg.get("japanese") or reg.get("source") or reg.get("text", "")
            lines.append(f"[{r_id} | estimated order {idx}]\n{text}")
    elif unassigned:
        for idx, reg in enumerate(unassigned, 1):
            r_id = reg.get("id", f"r{idx}")
            text = reg.get("japanese") or reg.get("source") or reg.get("text", "")
            lines.append(f"[{r_id} | estimated order {idx}]\n{text}")

    return "\n".join(lines)


def read_page_regions(page: dict[str, Any], read_json_fn: Any = None) -> list[dict[str, Any]]:
    if isinstance(page.get("textRegions"), list) and page["textRegions"]:
        return page["textRegions"]
    path = page.get("path")
    if isinstance(path, (str, Path)):
        p = Path(path) / "text_regions.json"
        if p.is_file():
            if read_json_fn is not None:
                data = read_json_fn(p, [])
            else:
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    data = []
            if isinstance(data, list):
                return data
    return []


def read_page_panels(page: dict[str, Any], read_json_fn: Any = None) -> list[dict[str, Any]]:
    panels = page.get("panel_detections") or page.get("panels")
    if isinstance(panels, list) and panels:
        return panels
    path = page.get("path")
    if isinstance(path, (str, Path)):
        p = Path(path) / "panel_detections.json"
        if p.is_file():
            if read_json_fn is not None:
                data = read_json_fn(p, [])
            else:
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    data = []
            if isinstance(data, list):
                return data
    return []


def group_transcript_panel_texts(
    regions: list[dict[str, Any]],
    panels: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Group raw region texts by detected panels for manga synopsis generation."""
    if not panels or not regions:
        return None

    panels_grouped, unassigned = assign_regions_to_panels(regions, panels)
    panel_groups = []

    for idx, panel in enumerate(panels):
        order = (
            getattr(panel, "order_index", None)
            or (panel.get("order") if isinstance(panel, dict) else None)
            or (idx + 1)
        )
        p_regions = panels_grouped.get(idx, [])
        texts = []
        for r in p_regions:
            val = r.get("original_text") or r.get("text_raw") or r.get("text") or r.get("source")
            if val and str(val).strip():
                texts.append(str(val).strip())
        if texts:
            panel_groups.append({"panel_id": order, "texts": texts})

    unassigned_texts = []
    for r in unassigned:
        val = r.get("original_text") or r.get("text_raw") or r.get("text") or r.get("source")
        if val and str(val).strip():
            unassigned_texts.append(str(val).strip())
    if unassigned_texts:
        panel_groups.append({"panel_id": "unassigned", "texts": unassigned_texts})

    return panel_groups if panel_groups else None


def format_synopsis_transcript(snapshot: dict[str, Any]) -> list[str]:
    """Build formatted page transcripts with panel boundaries for synopsis generation."""
    pages: list[str] = []
    for entry in snapshot.get("entries", []):
        if not entry.get("texts") and not entry.get("panel_groups"):
            continue
        panel_groups = entry.get("panel_groups")
        if panel_groups:
            body_parts: list[str] = []
            for pg in panel_groups:
                p_id = pg.get("panel_id")
                lines = [t for t in pg.get("texts", []) if str(t).strip()]
                if lines:
                    header = f"[PANEL {p_id}]" if p_id != "unassigned" else "[UNASSIGNED]"
                    body_parts.append(f"{header}\n" + "\n".join(lines))
            body = "\n\n".join(body_parts) if body_parts else "\n".join(entry.get("texts", []))
        else:
            body = "\n".join(entry.get("texts", []))
        if body.strip():
            pages.append(f"[Page {entry['name']}]\n{body}")
    return pages
