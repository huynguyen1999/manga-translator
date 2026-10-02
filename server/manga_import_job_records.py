"""Filesystem records for durable manga import jobs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def write_record(record_path: Path, details_path: Path, job: dict[str, Any], write_json) -> None:
    if "items" in job:
        write_json(details_path, {"items": job["items"]})
    write_json(record_path, {key: value for key, value in job.items() if key != "items"})


def read_record(record_path: Path, details_path: Path) -> dict[str, Any]:
    job = json.loads(record_path.read_text(encoding="utf-8"))
    try:
        details = json.loads(details_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return job
    job["items"] = details.get("items", [])
    return job


def list_records(records_root: Path, details_root: Path, write_json) -> list[dict[str, Any]]:
    if not records_root.exists():
        return []
    jobs = []
    for path in records_root.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(job, dict):
            continue
        if "items" in job:
            write_json(details_root / f"{path.stem}.json", {"items": job.pop("items")})
            write_json(path, job)
        jobs.append(job)
    return sorted(jobs, key=lambda job: (job.get("acceptedAt", 0), job.get("id", "")), reverse=True)
