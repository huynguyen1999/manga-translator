"""Batch stage ordering and ready-group selection helpers."""

from __future__ import annotations

from typing import Any

_PRE_TRANSLATION_STAGES = (
    "colorization",
    "upscaling",
    "detection",
    "ocr",
    "bubble_detection",
    "textline_merge",
)
_BATCH_STAGE_ORDER = (
    "initialize",
    *_PRE_TRANSLATION_STAGES,
    "translation",
    "mask_generation",
    "layout",
    "inpainting",
    "rendering",
)
_BATCH_STAGE_ALIASES = {
    "starting": "initialize",
    "queued": "initialize",
    "reserved": "translation",
    "awaiting_translation": "translation",
    "translating": "translation",
    "translation_remap": "translation",
    "analyzing-story": "translation",
    "after-translating": "translation",
    "colorizing": "colorization",
    "bubble-detection": "bubble_detection",
    "mask-generation": "mask_generation",
    "saving": "rendering",
    "downscaling": "rendering",
    "finished": "rendering",
}


def find_ready_translation_group(
    scheduler,
    batch_id: str,
    items: list[dict[str, Any]],
    size: int,
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    if scheduler._current_batch_stage(items) != "translation":
        return None
    if (settings or {}).get("translationQuality") == "professional":
        story_plan = (settings or {}).get("storyPlan")
        segments = story_plan.get("segments") if isinstance(story_plan, dict) else None
        if not segments or not story_plan.get("enabled", True) or story_plan.get("mergeAllPages"):
            segments = [{"startPage": 1, "endPage": len(items)}]
        for segment in segments:
            start, end = segment.get("startPage"), segment.get("endPage")
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                continue
            story_items = items[start - 1:end]
            if len(story_items) != end - start + 1:
                continue
            active_story_items = [item for item in story_items if item.get("status") != "completed"]
            if all(
                item.get("stage") == "awaiting_translation"
                and (batch_id, item.get("id")) not in scheduler._running_items
                for item in active_story_items
            ) and active_story_items:
                return active_story_items
        return None

    uncompleted = [
        item for item in items
        if item.get("status") not in {"completed", "error"} and item.get("id")
    ]
    if not uncompleted:
        return None

    translation_items = [
        item for item in uncompleted
        if scheduler._item_batch_stage(item) == "translation"
    ]

    ready_group = [
        item for item in uncompleted
        if item.get("stage") == "awaiting_translation"
        and (batch_id, item["id"]) not in scheduler._running_items
    ]

    if len(ready_group) >= size:
        return ready_group[:size]

    if len(ready_group) == len(translation_items):
        return ready_group

    return None


def story_plan_for_group(
    story_plan: Any,
    items: list[dict[str, Any]],
    item_ids: list[str],
) -> Any:
    if not isinstance(story_plan, dict) or not story_plan.get("enabled", True) or story_plan.get("mergeAllPages"):
        return story_plan
    positions = {item.get("id"): index + 1 for index, item in enumerate(items)}
    selected = [positions.get(item_id) for item_id in item_ids]
    if not selected or any(position is None for position in selected):
        return story_plan
    local_positions = {position: index + 1 for index, position in enumerate(selected)}

    def local_ranges(ranges: Any) -> list[dict[str, Any]]:
        result = []
        for value in ranges if isinstance(ranges, list) else []:
            if not isinstance(value, dict):
                continue
            range_start, range_end = value.get("startPage"), value.get("endPage")
            if not isinstance(range_start, int) or not isinstance(range_end, int):
                continue
            overlap = [
                local for original, local in local_positions.items()
                if range_start <= original <= range_end
            ]
            if overlap:
                local_range = {
                    **value,
                    "startPage": min(overlap),
                    "endPage": max(overlap),
                }
                if "pageCount" in value:
                    local_range["pageCount"] = len(overlap)
                result.append(local_range)
        return result

    return {
        **story_plan,
        "segments": local_ranges(story_plan.get("segments")),
        "archives": local_ranges(story_plan.get("archives")),
    }


def find_next_queued_item(
    scheduler, batch_id: str, items: list[dict[str, Any]], stage_id: str | None = None
) -> dict[str, Any] | None:
    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue
        if (batch_id, item_id) in scheduler._running_items:
            continue
        if item.get("status") == "queued" and item.get("stage") not in {"awaiting_translation", "reserved"}:
            if stage_id is not None and scheduler._item_batch_stage(item) != stage_id:
                continue
            return item
    return None


def item_batch_stage(item: dict[str, Any]) -> str:
    stage = item.get("retryFromStage") or item.get("pipelineStage") or item.get("stage") or "initialize"
    stage = str(stage)
    if stage.startswith(("drafting:", "editing:")):
        return "translation"
    stage = _BATCH_STAGE_ALIASES.get(stage, stage)
    return stage if stage in _BATCH_STAGE_ORDER else "initialize"


def current_batch_stage(cls, items: list[dict[str, Any]]) -> str | None:
    active = [item for item in items if item.get("status") != "completed"]
    if not active or any(item.get("status") == "error" for item in active):
        return None
    return min(
        (cls._item_batch_stage(item) for item in active),
        key=_BATCH_STAGE_ORDER.index,
    )
