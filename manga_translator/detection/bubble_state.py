"""Explicit bubble detection state shared by pipeline hydration and layout."""

from enum import Enum
from typing import Any

from .panel import normalize_class_name


class BubbleDetectionState(str, Enum):
    UNKNOWN = "unknown"
    COMPLETED_WITH_RESULTS = "completed_with_results"
    COMPLETED_EMPTY = "completed_empty"
    FAILED = "failed"


def set_bubble_detection_state(ctx: Any, detections=None, *, failed: bool = False) -> BubbleDetectionState:
    state = (
        BubbleDetectionState.FAILED if failed
        else BubbleDetectionState.UNKNOWN if detections is None
        else BubbleDetectionState.COMPLETED_WITH_RESULTS if detections
        else BubbleDetectionState.COMPLETED_EMPTY
    )
    ctx.bubble_detection_state = state
    return state


def get_bubble_detection_state(ctx: Any) -> BubbleDetectionState:
    state = getattr(ctx, "bubble_detection_state", None)
    if state is not None:
        try:
            return BubbleDetectionState(state)
        except (TypeError, ValueError):
            return BubbleDetectionState.UNKNOWN
    return set_bubble_detection_state(ctx, getattr(ctx, "bubble_detections", None))


def bubble_layout_route(ctx: Any):
    state = get_bubble_detection_state(ctx)
    return state is not BubbleDetectionState.COMPLETED_WITH_RESULTS


def record_bubble_layout_route(ctx: Any):
    state = get_bubble_detection_state(ctx)
    if getattr(ctx, "_collect_layout_profile", False):
        profile = getattr(ctx, "_solver_profile", None)
        if profile is not None:
            profile.update(
                bubble_detection_state=state.value,
                bubble_inference_requested=state is not BubbleDetectionState.COMPLETED_WITH_RESULTS,
            )


def model_class_map(raw_names):
    if isinstance(raw_names, dict):
        classes = {int(key): normalize_class_name(value) for key, value in raw_names.items()}
    elif isinstance(raw_names, (list, tuple)):
        classes = {index: normalize_class_name(name) for index, name in enumerate(raw_names)}
    else:
        classes = {}
    single_balloon = len(classes) == 1 and next(iter(classes.values())) == "balloon"
    return classes, single_balloon


def detection_class_name(classes, class_id, single_balloon):
    return classes.get(class_id, "balloon" if single_balloon else "unknown")


def restore_bubble_detections(ctx: Any, data, image_shape):
    from .bubble import deserialize_bubble_detections

    ctx.bubble_detections = deserialize_bubble_detections(data, image_shape)
    set_bubble_detection_state(ctx, ctx.bubble_detections)
    ctx._bubble_detection_done = True
    return ctx.bubble_detections
