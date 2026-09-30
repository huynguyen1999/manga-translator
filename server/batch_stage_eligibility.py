"""Resource-aware eligibility for checkpointed page stages."""
from manga_translator.pipeline.stages import ResourceClass
from server.batch_resource_policy import _resource_family, _stage_resource
from server.batch_group_selection import _BATCH_STAGE_ORDER

def stage_resource_for(scheduler, batch, item, stage_id: str, instance=None) -> ResourceClass:
    config = scheduler._config_for(batch, item)
    if instance is None:
        instance = next(
            (
                candidate
                for candidate in getattr(scheduler.stage_executors, "list", [])
                if getattr(candidate, "translator", None) is not None
            ),
            None,
        )
    translator = getattr(instance, "translator", None)
    device = getattr(translator, "device", None) or "cpu"
    if stage_id == "translation" and getattr(translator, "_gpu_limited_memory", False):
        device = "cpu"
    return _stage_resource(stage_id, config, device)


async def acquire_stage_resource(scheduler, batch, item, stage_id: str, instance=None):
    return await scheduler._acquire_stage_resource(
        stage_id, stage_resource_for(scheduler, batch, item, stage_id, instance)
    )


def can_schedule_stage(scheduler, batch_id, batch, items, stage_id, resource, instance=None) -> bool:
    stage_order = _BATCH_STAGE_ORDER.index(stage_id)
    family = _resource_family(resource)
    if any(
        item.get("status") == "queued"
        and item.get("id")
        and (batch_id, item["id"]) not in scheduler._running_items
        and _BATCH_STAGE_ORDER.index(scheduler._item_batch_stage(item)) < stage_order
        and _resource_family(
            stage_resource_for(
                scheduler,
                batch,
                item,
                scheduler._item_batch_stage(item),
                instance,
            )
        ) == family
        for item in items
    ):
        return False

    active_items = [
        item for item in items
        if item.get("id") and (batch_id, item["id"]) in scheduler._running_items
    ]
    if not active_items and scheduler._current_batch_stage(items) != stage_id:
        return False

    active_stages = []
    for item in active_items:
        active_stage = scheduler._item_batch_stage(item)
        if item.get("stage"):
            runtime_stage = scheduler._item_batch_stage(
                {**item, "retryFromStage": None, "pipelineStage": None}
            )
            if runtime_stage != active_stage:
                active_stage = runtime_stage
        resources = scheduler._resource_manager.active_resources(active_stage)
        tracked = getattr(scheduler, "_running_stage_resources", {}).get(
            (batch_id, item["id"])
        )
        if resources:
            active_stages.extend((active_stage, value) for value in resources)
        elif tracked is not None:
            active_stages.append(tracked)
        else:
            active_stages.append(
                (active_stage, stage_resource_for(scheduler, batch, item, active_stage, instance))
            )

    if any(
        active_stage != stage_id and _resource_family(active_resource) == family
        for active_stage, active_resource in active_stages
    ):
        return False
    if stage_id == "layout" and any(stage == "layout" for stage, _ in active_stages):
        return False
    return scheduler._resource_manager.has_capacity(resource)


def eligible_queued_items(scheduler, batch_id, batch, items, instance=None):
    eligible = []
    for item in items:
        if item.get("status") != "queued" or not item.get("id"):
            eligible.append(item)
            continue
        stage_id = scheduler._item_batch_stage(item)
        resource = stage_resource_for(scheduler, batch, item, stage_id, instance)
        if can_schedule_stage(scheduler, batch_id, batch, items, stage_id, resource, instance):
            eligible.append(item)
        else:
            eligible.append({**item, "status": "blocked"})
    return eligible


def translation_group_is_schedulable(scheduler, batch_id, batch, items, group, instance=None) -> bool:
    return all(
        can_schedule_stage(
            scheduler,
            batch_id,
            batch,
            items,
            "translation",
            stage_resource_for(scheduler, batch, item, "translation", instance),
            instance,
        )
        for item in group
    )
