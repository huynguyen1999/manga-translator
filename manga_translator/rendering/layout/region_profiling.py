"""Opt-in per-region solver timing and workload reports."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Dict, List

from .profiling import SolverProfileStats, get_solver_profile


def _numeric_snapshot(profile: SolverProfileStats) -> Dict[str, Dict[str, float]]:
    return {
        section: {
            name: value for name, value in values.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        for section, values in profile.to_dict().items() if isinstance(values, dict)
    }


def _record_call(profile, region, phase, before, elapsed_ms):
    reports = getattr(profile, "_region_profiles", None)
    if reports is None:
        reports = profile._region_profiles = {}
    region_id = str(getattr(region, "region_id", id(region)))
    report = reports.setdefault(region_id, {
        "region_id": region_id, "elapsed_ms": 0.0,
        "workload": {}, "timings_ms": {}, "calls": [], "placement_attempts": [],
    })
    after = _numeric_snapshot(profile)
    deltas = {}
    for section in ("workload", "timings_ms"):
        values = {
            name: value - before.get(section, {}).get(name, 0)
            for name, value in after.get(section, {}).items()
            if value != before.get(section, {}).get(name, 0)
        }
        deltas[section] = values
        for name, value in values.items():
            report[section][name] = report[section].get(name, 0) + value
    report["elapsed_ms"] += elapsed_ms
    call = {"phase": phase, "elapsed_ms": elapsed_ms, **deltas}
    attempts = (getattr(region, "_free_text_attempt_qa", {}) or {}).get("placement_attempts", [])
    if phase.startswith("free_text") and attempts:
        captured = [{"phase": phase, **item} for item in attempts]
        report["placement_attempts"].extend(captured)
        report["domain_attempts"] = report.get("domain_attempts", 0) + len(captured)
        call["domain_attempts"] = len(captured)
        call["placement_attempts"] = captured
    report["calls"].append(call)


def profiled_region_call(callback, profile_region, phase, enabled, *args, **kwargs):
    """Run normally unless enabled; then attribute aggregate deltas to a region."""
    if not enabled:
        return callback(*args, **kwargs)
    profile = get_solver_profile()
    before = _numeric_snapshot(profile)
    started = perf_counter()
    try:
        return callback(*args, **kwargs)
    finally:
        _record_call(profile, profile_region, phase, before, (perf_counter() - started) * 1000.0)


def profiled_contrast(callback, ctx, regions, obstacles, cleanup, timing, enabled):
    """Time contrast without changing its original batch call or region ordering."""
    if not enabled:
        callback(ctx, regions, regions, obstacles, cleanup)
        return
    started = perf_counter()
    callback(ctx, regions, regions, obstacles, cleanup)
    timing["contrast_ms"] = (perf_counter() - started) * 1000.0
    ctx._solver_profile = region_profile_report(get_solver_profile(), regions, timing["contrast_ms"])


def finalize_layout_timings(timing, ctx, started, queue_wait_ms=None):
    total_ms = (perf_counter() - started) * 1000.0
    if queue_wait_ms is None:
        queue_wait_ms = getattr(ctx, "_layout_queue_wait_ms", 0.0)
    if getattr(ctx, "_collect_layout_profile", False):
        ctx._solver_profile["layout_total_ms"] = total_ms
        ctx._solver_profile["queue_wait_ms"] = queue_wait_ms
    return {**timing, "total_ms": total_ms, "queue_wait_ms": queue_wait_ms}


def region_profile_report(profile: SolverProfileStats, regions: List[Any], contrast_ms: float) -> Dict[str, Any]:
    reports = getattr(profile, "_region_profiles", {})
    output = profile.to_dict()
    output["contrast_ms"] = contrast_ms
    output["regions"] = []
    for region in regions:
        region_id = str(getattr(region, "region_id", ""))
        measured = reports.get(region_id, {})
        workload = dict(measured.get("workload", {}))
        qa = getattr(region, "_solver_qa", {}) or {}
        attempts = measured.get("placement_attempts", [])
        rejections: Dict[str, int] = {}
        for attempt in attempts:
            for reason, count in (attempt.get("rejections", {}) or {}).items():
                rejections[reason] = rejections.get(reason, 0) + int(count or 0)

        mode = getattr(region, "placement_mode", None)
        mode = getattr(mode, "value", mode)
        status = getattr(region, "_solver_status", None)
        if mode == "BUBBLE":
            path = "bubble_shape_aware" if getattr(region, "_solver_applied", False) else "bubble_fallback"
        elif status == "free_text_ideal":
            path = "ideal"
        elif status == "free_text_local":
            path = "local"
        elif workload.get("free_text_full_search_runs", 0):
            path = "exhaustive"
        elif workload.get("free_text_ideal_attempts", 0):
            path = "ideal"
        elif workload.get("free_text_local_search_runs", 0):
            path = "local"
        else:
            path = getattr(region, "_solver_path", None) or "none"

        lines = [
            {key: line.get(key) for key in ("text", "x", "y", "width", "height")}
            for segment in (getattr(region, "layout_segments", None) or [])
            for line in (segment.get("lines", []) or [])
        ]
        output["regions"].append({
            **measured,
            "region_id": region_id,
            "source_region_ids": list(getattr(region, "source_region_ids", []) or []),
            "placement_mode": mode,
            "path": path,
            "status": status,
            "typography_candidates": workload.get("free_text_typography_candidates", workload.get("fonts_tested", 0)),
            "chosen_font_size": getattr(region, "font_size", None),
            "layout_bounds": getattr(region, "layout_bounds", None),
            "chosen_offset": {key: qa[key] for key in ("placement_dx", "placement_dy") if key in qa},
            "rejection_reasons": rejections,
            "rejection_attempts": [
                {"attempt": index, "phase": attempt.get("phase"), "rejections": dict(attempt.get("rejections", {}) or {})}
                for index, attempt in enumerate(attempts)
            ],
            "lines": lines,
        })
    return output
