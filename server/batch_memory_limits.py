"""Bound inference page batches using current process memory pressure."""

from __future__ import annotations

import asyncio
import os


def inference_page_limit(
    stage_id: str,
    batch_size: int,
    limits: dict[str, int],
    executors,
) -> int:
    limit = min(batch_size, limits[stage_id])
    try:
        import psutil
        process = psutil.Process(os.getpid()).memory_info().rss
        total = psutil.virtual_memory().total
    except Exception:
        return limit
    if total <= 0:
        return limit

    pressure = process / total
    if pressure >= 0.60:
        try:
            loop = asyncio.get_running_loop()
            for instance in executors.list:
                executor = getattr(instance, "_model_executor", None)
                ttl = getattr(getattr(instance, "translator", None), "models_ttl", 0)
                if executor is not None and ttl > 0:
                    loop.create_task(executor.cleanup_models(ttl))
                    break
        except (AttributeError, RuntimeError):
            pass
        return 1
    if pressure >= 0.50:
        return max(1, limit - 1)
    return limit
