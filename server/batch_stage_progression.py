"""Select the next pre-translation or full-pipeline stage."""

from __future__ import annotations

from server.batch_group_selection import _BATCH_STAGE_ORDER, _PRE_TRANSLATION_STAGES


def next_prepare_stage(run) -> str | None:
    stages = {stage.get("id"): stage for stage in run.manifest.get("stages", [])}
    return next((stage for stage in _PRE_TRANSLATION_STAGES
                 if stages.get(stage, {}).get("status") == "pending"), None)


def next_batch_stage(run) -> str | None:
    stages = {stage.get("id"): stage for stage in run.manifest.get("stages", [])}
    return next((stage for stage in _BATCH_STAGE_ORDER[1:]
                 if stages.get(stage, {}).get("status") in {
                     "pending", "failed", "running", "interrupted"
                 }), None)
