"""Compatibility imports for batch resource management."""

from server.batch_resource_policy import (
    BatchResourceManager,
    MODEL_EXECUTOR_CONCURRENCY,
    _resource_family,
    _stage_resource,
    stage_resource_limits,
)
