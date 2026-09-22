"""Lightweight memory probe for development and test use only.

Not imported by any production code path.  Use it in tests or devscripts to
measure RSS and tracemalloc-tracked allocations at labelled checkpoints.

Usage::

    from manga_translator.mem_probe import snapshot, log_checkpoint
    log_checkpoint("before analysis")
    ...
    log_checkpoint("after render")
"""

from __future__ import annotations

import tracemalloc
from typing import Any

_tracemalloc_started = False


def _ensure_tracemalloc() -> None:
    global _tracemalloc_started
    if not _tracemalloc_started:
        tracemalloc.start()
        _tracemalloc_started = True


def _rss_mb() -> float | None:
    """Return current process RSS in MiB, or None if psutil is unavailable."""
    try:
        import os
        import psutil  # type: ignore[import-untyped]
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return None


def snapshot() -> dict[str, Any]:
    """Return a dict with rss_mb (if psutil available) and traced_kb."""
    _ensure_tracemalloc()
    current, _ = tracemalloc.get_traced_memory()
    result: dict[str, Any] = {"traced_kb": current / 1024}
    rss = _rss_mb()
    if rss is not None:
        result["rss_mb"] = rss
    return result


def log_checkpoint(label: str) -> dict[str, Any]:
    """Print a labelled memory snapshot and return it."""
    snap = snapshot()
    rss_part = f"  rss={snap['rss_mb']:.1f} MiB" if "rss_mb" in snap else ""
    print(f"[mem_probe] {label}: traced={snap['traced_kb']:.1f} KiB{rss_part}")
    return snap
