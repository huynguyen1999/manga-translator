"""Select already validated results when further page search cannot help."""


def compatible_bubble_subset(plans, image_shape):
    from .solver import _choose_joint_layout

    retained, chosen = [], None
    for plan in plans:
        if not plan.candidates:
            continue
        trial = retained + [plan]
        selection = _choose_joint_layout(trial, image_shape)
        if selection is not None:
            retained, chosen = trial, selection
    return retained, chosen


def merge_bubble_plans(previous, current):
    by_region = {id(plan.region): plan for plan in previous}
    for plan in current:
        old = by_region.pop(id(plan.region), None)
        if old is not None and old is not plan:
            plan.candidates = old.candidates + plan.candidates
    return current + [plan for plan in by_region.values() if plan.candidates]


def _run_search(ctx, config, active_font, timing, layout_debug, profile):
    from .solver import apply_shape_aware_bubble_layout
    from ...detection.bubble_state import bubble_layout_route, record_bubble_layout_route
    try:
        apply_shape_aware_bubble_layout(
            ctx,
            config,
            font_path=active_font,
            infer_bubbles=bubble_layout_route(ctx),
            timing=timing,
            layout_debug=layout_debug,
            page_geometry=getattr(ctx, "page_geometry", None),
        )
        record_bubble_layout_route(ctx)
    finally:
        profile.clear_ephemeral_caches()


def choose_bubble_group(plans, member_count, image_shape):
    from .solver import _choose_joint_layout
    if member_count > 1:
        return _choose_joint_layout(plans, image_shape)
    return (plans[0].candidates[0],) if plans and plans[0].candidates else None
