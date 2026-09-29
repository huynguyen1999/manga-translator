"""Isolated in-process workers for resource-independent batch stages."""

from server.batch_resource_policy import batch_stage_executor_count
from server.in_process_executor import InProcessExecutorInstance
from server.instance import Executors


def create_batch_stage_executors(resource_limits, executors):
    template = next(
        (item for item in executors.list if isinstance(item, InProcessExecutorInstance)),
        None,
    )
    if template is None:
        return executors

    pool = Executors()
    for offset in range(batch_stage_executor_count(resource_limits)):
        pool.register(InProcessExecutorInstance(
            worker_id=len(executors.list) + offset,
            translator_params=dict(template.translator_params),
            model_executor=template._model_executor,
        ))
    return pool
