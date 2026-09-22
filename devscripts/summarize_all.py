#!/usr/bin/env python3
"""Batch summarize all mangas via the manga-image-translator API.

Supports targeting translated, original, or all mangas, and forces summary
re-generation to overwrite existing summaries.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _normalize_base_url(url: str) -> str:
    url = (url or "http://127.0.0.1:8000").strip()
    return url.rstrip("/")


def _resolve_server_url(url: str, explicit: bool = False) -> str:
    """Normalize base URL and probe alternative port (8000 <-> 5003) if default isn't reachable."""
    normalized = _normalize_base_url(url)
    if explicit:
        return normalized

    # Probe the provided URL
    try:
        req = urllib.request.Request(f"{normalized}/api/status", method="GET")
        with urllib.request.urlopen(req, timeout=1.5):
            return normalized
    except Exception:
        pass

    # Try fallback between 8000 and 5003
    candidates = ["http://127.0.0.1:8000", "http://127.0.0.1:5003", "http://localhost:8000", "http://localhost:5003"]
    for candidate in candidates:
        if candidate == normalized:
            continue
        try:
            req = urllib.request.Request(f"{candidate}/api/status", method="GET")
            with urllib.request.urlopen(req, timeout=1.5):
                return candidate
        except Exception:
            continue

    return normalized


def _api_request(
    url: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    """Execute an HTTP JSON request using standard library urllib."""
    headers = {"Accept": "application/json"}
    body_bytes = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            content = resp.read().decode("utf-8")
            return code, json.loads(content) if content else {}
    except urllib.error.HTTPError as err:
        error_content = err.read().decode("utf-8", errors="replace")
        try:
            error_json = json.loads(error_content)
        except Exception:
            error_json = {"detail": error_content}
        return err.code, error_json
    except urllib.error.URLError as err:
        raise RuntimeError(f"Failed to connect to server at {url}: {err.reason}") from err


def fetch_manga_groups(
    base_url: str,
    target: str = "all",
    search: str | None = None,
    max_count: int | None = None,
    missing_only: bool = False,
) -> list[dict[str, Any]]:
    """Fetch all manga groups matching target status with pagination handling."""
    normalized_url = _normalize_base_url(base_url)
    all_groups: list[dict[str, Any]] = []
    page_size = 100
    offset = 0

    while True:
        params: dict[str, Any] = {
            "limit": page_size,
            "offset": offset,
        }
        if target in {"translated", "original", "all", "summarized", "review"}:
            params["status"] = target
        if search:
            params["search"] = search

        query_str = urllib.parse.urlencode(params)
        endpoint = f"{normalized_url}/api/results/groups?{query_str}"
        status_code, payload = _api_request(endpoint, method="GET")

        if status_code != 200:
            detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            raise RuntimeError(f"Failed to fetch manga groups (HTTP {status_code}): {detail}")

        groups = payload.get("groups", [])
        if not groups:
            break

        if missing_only:
            groups = [g for g in groups if not g.get("hasSummary")]

        all_groups.extend(groups)
        next_offset = payload.get("nextOffset")

        if max_count is not None and len(all_groups) >= max_count:
            all_groups = all_groups[:max_count]
            break

        if next_offset is None or next_offset <= offset:
            break
        offset = next_offset

    return all_groups


def trigger_summary(
    base_url: str,
    group_id: str | None,
    manga_title: str | None,
    model: str | None = None,
    regenerate: bool = True,
    refresh_text: bool = False,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Request a manga summary generation/regeneration via the API."""
    normalized_url = _normalize_base_url(base_url)
    endpoint = f"{normalized_url}/api/results/group/summary"

    payload: dict[str, Any] = {
        "regenerate": regenerate,
        "refreshText": refresh_text,
    }
    if group_id:
        payload["groupId"] = group_id
    if manga_title:
        payload["mangaTitle"] = manga_title
    if model:
        payload["summaryModel"] = model

    status_code, response_data = _api_request(endpoint, method="POST", data=payload, timeout=timeout)
    if status_code != 200:
        detail = response_data.get("detail", response_data) if isinstance(response_data, dict) else response_data
        raise RuntimeError(f"Failed to request summary (HTTP {status_code}): {detail}")

    return response_data if isinstance(response_data, dict) else {}


def fetch_summary_jobs(base_url: str, limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve active and recent summary jobs from the server."""
    normalized_url = _normalize_base_url(base_url)
    endpoint = f"{normalized_url}/api/results/group/summary/jobs"
    status_code, payload = _api_request(endpoint, method="GET")
    if status_code != 200:
        return []
    return payload if isinstance(payload, list) else []


def fetch_manga_summary_status(
    base_url: str,
    group_id: str | None,
    manga_title: str | None,
) -> dict[str, Any] | None:
    """Fetch status for a specific manga synopsis."""
    normalized_url = _normalize_base_url(base_url)
    params: dict[str, str] = {}
    if group_id:
        params["groupId"] = group_id
    if manga_title:
        params["title"] = manga_title
    if not params:
        return None

    query_str = urllib.parse.urlencode(params)
    endpoint = f"{normalized_url}/api/results/group/summary?{query_str}"
    try:
        status_code, payload = _api_request(endpoint, method="GET")
        if status_code == 200 and isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return None


def wait_for_summaries(
    base_url: str,
    manga_targets: list[dict[str, Any]],
    poll_interval: float = 3.0,
) -> dict[str, int]:
    """Poll summary status until all requested mangas finish."""
    remaining = {m.get("id") or m.get("title"): m for m in manga_targets if m.get("id") or m.get("title")}
    total = len(remaining)
    counts = {"ready": 0, "error": 0}

    print(f"\n[+] Waiting for {total} summary job(s) to complete (polling every {poll_interval}s)...")

    while remaining:
        time.sleep(poll_interval)
        done_keys: list[str] = []

        # Check jobs list
        try:
            jobs = fetch_summary_jobs(base_url)
        except Exception:
            jobs = []
        jobs_by_id = {j.get("groupId"): j for j in jobs if j.get("groupId")}
        jobs_by_title = {j.get("title"): j for j in jobs if j.get("title")}

        for key, manga in list(remaining.items()):
            group_id = manga.get("id")
            title = manga.get("title")

            job = jobs_by_id.get(group_id) or jobs_by_title.get(title)
            status = job.get("status") if job else None

            # Fallback to direct status check if not visible in jobs list
            if status is None:
                detail = fetch_manga_summary_status(base_url, group_id, title)
                if detail:
                    status = detail.get("jobStatus") or ("ready" if detail.get("summary") else None)

            if status == "ready":
                counts["ready"] += 1
                done_keys.append(key)
                print(f"  ✓ [{counts['ready'] + counts['error']}/{total}] Summary completed: {title}")
            elif status == "error":
                counts["error"] += 1
                done_keys.append(key)
                error_msg = job.get("jobError") if job else "Unknown error"
                print(f"  ✗ [{counts['ready'] + counts['error']}/{total}] Summary failed: {title} ({error_msg})")

        for k in done_keys:
            remaining.pop(k, None)

        if remaining:
            print(f"  ... {len(remaining)} remaining in queue/processing...", end="\r", flush=True)

    print(f"\n[+] Finished: {counts['ready']} succeeded, {counts['error']} failed out of {total} total.")
    return counts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch call summarize API for all mangas with overwrite/regeneration support.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-t",
        "--target",
        choices=["translated", "original", "all"],
        default="all",
        help="Target which mangas to summarize: 'translated' (has translated pages), 'original' (only original pages), or 'all'",
    )
    parser.add_argument(
        "-u",
        "--url",
        default=os.getenv("MANGA_SERVER_URL", "http://127.0.0.1:8000"),
        help="Base URL of the manga-image-translator API server",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=None,
        help="Summary model to use (e.g. 'deepseek-flash', 'groq', 'gemini', 'deepseek:deepseek-chat')",
    )
    parser.add_argument(
        "--missing-only",
        "--only-missing",
        "--unsummarized",
        action="store_true",
        dest="missing_only",
        help="Only process mangas that do not have a summary yet (hasSummary is False)",
    )
    parser.add_argument(
        "--no-regenerate",
        action="store_true",
        help="Skip re-generation for mangas that already have an up-to-date summary (by default, regeneration is forced)",
    )
    parser.add_argument(
        "--refresh-text",
        action="store_true",
        help="Force re-extracting OCR text from all pages before summarizing",
    )
    parser.add_argument(
        "-w",
        "--wait",
        action="store_true",
        help="Wait and stream progress until all summary jobs complete",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=3.0,
        help="Polling interval in seconds when --wait is enabled",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Delay in seconds between queueing each manga summary request",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit total number of mangas to process",
    )
    parser.add_argument(
        "--search",
        type=str,
        default=None,
        help="Filter mangas by title substring",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching mangas without submitting summary requests",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print detailed response info",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    explicit_url = False
    if argv is not None:
        explicit_url = any(arg.startswith(("-u", "--url")) for arg in argv)
    elif len(sys.argv) > 1:
        explicit_url = any(arg.startswith(("-u", "--url")) for arg in sys.argv[1:])

    base_url = _resolve_server_url(args.url, explicit=explicit_url)
    regenerate = not args.no_regenerate

    print(f"[*] Connecting to server: {base_url}")
    print(f"[*] Target filter: {args.target}")
    print(f"[*] Overwrite/Regenerate: {regenerate}")
    if args.missing_only:
        print("[*] Missing only: Enabled (only unsummarized mangas)")
    if args.refresh_text:
        print("[*] Refresh OCR Text: Enabled")
    if args.model:
        print(f"[*] Summary Model: {args.model}")

    try:
        mangas = fetch_manga_groups(
            base_url=base_url,
            target=args.target,
            search=args.search,
            max_count=args.limit,
            missing_only=args.missing_only,
        )
    except Exception as err:
        print(f"[-] Error fetching mangas: {err}", file=sys.stderr)
        return 1

    if not mangas:
        filters = [f"target='{args.target}'"]
        if args.search:
            filters.append(f"search='{args.search}'")
        if args.missing_only:
            filters.append("hasSummary=False")
        print(f"[!] No mangas found matching {' and '.join(filters)}")
        return 0

    print(f"[+] Found {len(mangas)} manga group(s) matching criteria:")
    for idx, manga in enumerate(mangas, start=1):
        title = manga.get("title", "Ungrouped")
        page_count = manga.get("count", 0)
        has_summary = "Yes" if manga.get("hasSummary") else "No"
        print(f"  {idx:3d}. {title} ({page_count} pages, existing summary: {has_summary})")

    if args.dry_run:
        print(f"\n[!] Dry run enabled: 0 requests sent.")
        return 0

    print(f"\n[*] Submitting {len(mangas)} summary generation request(s) (regenerate={regenerate})...")
    queued_count = 0
    errors_count = 0
    submitted_targets: list[dict[str, Any]] = []

    for idx, manga in enumerate(mangas, start=1):
        group_id = manga.get("id")
        title = manga.get("title", "Ungrouped")
        try:
            resp = trigger_summary(
                base_url=base_url,
                group_id=group_id,
                manga_title=title,
                model=args.model,
                regenerate=regenerate,
                refresh_text=args.refresh_text,
            )
            job_status = resp.get("jobStatus", "queued")
            job_stage = resp.get("jobStage", "detecting")
            if args.verbose:
                print(f"  [{idx}/{len(mangas)}] Queued '{title}' -> status={job_status}, stage={job_stage}")
            else:
                print(f"  [{idx}/{len(mangas)}] Queued '{title}'")
            queued_count += 1
            submitted_targets.append(manga)
        except Exception as err:
            print(f"  [{idx}/{len(mangas)}] Failed to queue '{title}': {err}", file=sys.stderr)
            errors_count += 1

        if args.delay > 0 and idx < len(mangas):
            time.sleep(args.delay)

    print(f"\n[+] Summary submission complete: {queued_count} queued, {errors_count} failed.")

    if args.wait and submitted_targets:
        wait_for_summaries(base_url, submitted_targets, poll_interval=args.poll_interval)

    return 0 if errors_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
