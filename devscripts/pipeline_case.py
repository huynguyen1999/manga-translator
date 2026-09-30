"""Inspect saved pages, manage local ./devscripts/data cases, and run stage-focused pipeline reruns."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from devscripts.pipeline_case_local import (  # noqa: E402
    DEFAULT_DATA_DIR,
    STAGE_ALIASES,
    STAGE_ORDER,
    check_local_cases,
    compare_cases as _compare_cases_impl,
    discover_target_images,
    execute_local_pipeline as _execute_local_pipeline_impl,
    inspect_local_case as _inspect_local_case_impl,
    is_saved_case_dir,
    normalize_stage,
    resolve_baseline_dir,
    run_local_cases as _run_local_cases_impl,
)


ID_PREFIXES = {
    "detection": "detection",
    "ocr": "ocr_region",
    "bubble_detections": "speech_bubble",
    "text_regions_merged": "text_region",
    "text_regions": "text_region",
    "translations": "text_region",
    "regions": "text_region",
}


def page_reference(value: str) -> tuple[str, str]:
    """Return (origin, page ref) for copied page-view or result-asset URLs."""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("URL must be an absolute http(s) page or result URL")
    path = [unquote(part) for part in parts.path.split("/") if part]
    if len(path) == 3 and path[:2] == ["gallery", "pages"]:
        return f"{parts.scheme}://{parts.netloc}", path[2]
    if len(path) >= 3 and path[0] in {"result", "api", "results", "pages"}:
        if path[0] == "api" and path[1] in {"results", "pages"}:
            return f"{parts.scheme}://{parts.netloc}", path[2]
        if path[0] == "result" or path[0] in {"results", "pages"}:
            return f"{parts.scheme}://{parts.netloc}", path[1]
    raise ValueError("URL must be a copied /gallery/pages/<page> or /result/<page>/<file> URL")


def request_json(origin: str, path: str, *, method: str = "GET", body=None, timeout: int = 30):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        f"{origin.rstrip('/')}/{path.lstrip('/')}",
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path}: HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach {origin}: {error.reason}") from error


RERUN_MODES = {
    "full": "full",
    "input": "full",
    "detection": "reprocess_text",
    "bubble_detection": "full",
    "translation": "translation_typesetting",
    "layout": "typesetting",
}

ALL_STAGE_CHOICES = tuple(dict.fromkeys([*RERUN_MODES.keys(), *STAGE_ORDER, *STAGE_ALIASES.keys()]))


def rerun_case(
    origin: str, ref: str, start: str, *, patch=None, render_output=None,
    include_layout_profile=False,
) -> dict:
    page = request_json(origin, f"/api/results/{quote(ref, safe='')}")
    page_id = page.get("id") or ref
    mode = RERUN_MODES.get(start) or RERUN_MODES.get(normalize_stage(start), "typesetting")
    body = {"pageId": page_id, "mode": mode, **(patch or {})}
    if include_layout_profile:
        body["includeLayoutProfile"] = True
    result = request_json(
        origin,
        "/api/pipeline-cases/preview",
        method="POST",
        body=body,
        timeout=1800,
    )
    image_data = result.pop("imageBase64", None)
    if not isinstance(image_data, str):
        raise RuntimeError("Temporary rerun response did not include a rendered image")
    if render_output:
        image_path = Path(render_output)
    else:
        with tempfile.NamedTemporaryFile(prefix="pipeline-case-preview-", suffix=".jpg", delete=False) as output:
            image_path = Path(output.name)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(base64.b64decode(image_data, validate=True))
    for name, payload in result.get("artifacts", {}).items():
        add_prompt_ids(Path(name).stem, payload)
    result["pageId"] = page_id
    result["renderFile"] = str(image_path)
    return result


def benchmark_cases(urls: list[str], repeat: int = 1) -> dict:
    """Run read-only saved-page layout previews repeatedly and return one JSON artifact."""
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    runs = []
    for url in urls:
        origin, ref = page_reference(url)
        page = request_json(origin, f"/api/results/{quote(ref, safe='')}")
        page_id = page.get("id") or ref
        for run_index in range(1, repeat + 1):
            started = perf_counter()
            result = request_json(
                origin,
                "/api/pipeline-cases/preview",
                method="POST",
                body={
                    "pageId": page_id,
                    "mode": RERUN_MODES["layout"],
                    "includeLayoutProfile": True,
                },
                timeout=1800,
            )
            elapsed_ms = (perf_counter() - started) * 1000.0
            if not isinstance(result.get("imageBase64"), str):
                raise RuntimeError(f"Preview did not return an image for {url}")
            image = base64.b64decode(result.pop("imageBase64"), validate=True)
            profile = result.get("layoutProfile")
            if not isinstance(profile, dict):
                raise RuntimeError(f"Preview did not return a layout profile for {url}")
            snapshot = (result.get("artifacts") or {}).get("layout.json")
            if not isinstance(snapshot, dict):
                raise RuntimeError(f"Preview did not return artifacts.layout.json for {url}")
            runs.append({
                "url": url,
                "pageId": page_id,
                "run": run_index,
                "preview_elapsed_ms": elapsed_ms,
                "rendered_image_sha256": hashlib.sha256(image).hexdigest(),
                "layout_snapshot": snapshot,
                "layout_snapshot_sha256": hashlib.sha256(
                    json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "layoutProfile": profile,
            })
    return {"benchmark": "pipeline-case-layout-profile", "repeat": repeat, "runs": runs}


def add_prompt_ids(document: str, payload):
    """Give list artifacts a readable page-local ID when the saved row lacks one."""
    prefix = ID_PREFIXES.get(document)
    if not prefix or not isinstance(payload, list):
        return payload
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        existing_id = item.get("id") or item.get("region_id") or item.get("bubble_id")
        if existing_id:
            item.setdefault("id", existing_id)
            continue
        saved_index = item.get("index")
        ordinal = saved_index + 1 if isinstance(saved_index, int) else index + 1
        item["id"] = f"{prefix}_{ordinal}"
    return payload


def inspect_case(origin: str, ref: str) -> dict:
    artifact_ref = quote(ref, safe="")
    case = request_json(origin, f"/api/pipeline-cases/{artifact_ref}/data")
    for name, payload in case.get("artifacts", {}).items():
        add_prompt_ids(name, payload)
    return case


def inspect_local_case(target: str | Path, *, data_dir: str | Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    return _inspect_local_case_impl(target, data_dir=data_dir, add_prompt_ids_fn=add_prompt_ids)


def compare_cases(
    before: str | Path | dict[str, Any],
    after: str | Path | dict[str, Any],
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
) -> dict[str, Any]:
    return _compare_cases_impl(before, after, data_dir=data_dir, add_prompt_ids_fn=add_prompt_ids)


def execute_local_pipeline(image_path: Path, **kwargs) -> dict[str, Any]:
    kwargs.setdefault("add_prompt_ids_fn", add_prompt_ids)
    return _execute_local_pipeline_impl(image_path, **kwargs)


def run_local_cases(targets: list[str | Path], **kwargs) -> dict[str, Any]:
    kwargs.setdefault("add_prompt_ids_fn", add_prompt_ids)
    return _run_local_cases_impl(targets, **kwargs)


def _load_patch_file(patch_path: str | None) -> dict[str, Any] | None:
    if not patch_path:
        return None
    with open(patch_path, encoding="utf-8") as source:
        patch = json.load(source)
    if not isinstance(patch, dict) or set(patch) - {"settingsOverrides", "artifacts"}:
        raise ValueError("Patch JSON must contain only settingsOverrides and/or artifacts objects")
    if any(not isinstance(patch.get(key, {}), dict) for key in ("settingsOverrides", "artifacts")):
        raise ValueError("settingsOverrides and artifacts must be JSON objects")
    return patch


def _is_http_url(value: str) -> bool:
    return urlsplit(value).scheme in {"http", "https"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser(
        "check", aliases=["status"],
        help="Check whether local image(s) or folder(s) have been run before in ./devscripts/data",
    )
    check.add_argument("targets", nargs="+", help="Image file(s), folder(s) of images, or saved case dir(s)")
    check.add_argument(
        "--stage", "--from", dest="stage", choices=ALL_STAGE_CHOICES, default="layout",
        help="Target stage to focus on (default: layout)",
    )
    check.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Baseline data directory (default: ./devscripts/data)")
    check.add_argument("--from-run", help="Specific previous run directory or case.json to check against")
    check.add_argument("--from-baseline", action="store_true", help="Check baseline directory instead of latest stage rerun")
    check.add_argument("--output", "-o", help="Write JSON to a file instead of stdout")

    run_cmd = subparsers.add_parser(
        "run",
        help="Run full pipeline if not run before in ./devscripts/data, else reuse last JSON to rerun from target stage in a separate dir",
    )
    run_cmd.add_argument("targets", nargs="+", help="Image file(s), folder(s) of images (e.g. ~/Downloads/slow), or saved case dir(s)")
    run_cmd.add_argument(
        "--stage", "--from", dest="stage", choices=ALL_STAGE_CHOICES, default="layout",
        help="Target stage to focus on (default: layout)",
    )
    run_cmd.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Baseline data directory (default: ./devscripts/data)")
    run_cmd.add_argument("--output-dir", help="Separate directory to save stage rerun state (never overwrites baseline)")
    run_cmd.add_argument("--from-run", help="Reuse JSON artifacts from a specific previous run directory")
    run_cmd.add_argument("--from-baseline", action="store_true", help="Reuse JSON artifacts from the original baseline run")
    run_cmd.add_argument("--force-full", action="store_true", help="Force a full pipeline run from scratch")
    run_cmd.add_argument("--overwrite-baseline", action="store_true", help="Allow overwriting existing baseline when running full pipeline")
    run_cmd.add_argument("--translator", default="sugoi", help="Translator for full or translation-stage runs (default: sugoi)")
    run_cmd.add_argument("--render-output", help="Optional path to save rendered output image for single-image runs")
    run_cmd.add_argument("--patch", help="JSON file with settingsOverrides and/or replacement artifacts")
    run_cmd.add_argument("--profile", action="store_true", default=True, help="Include layout solver profiling")
    run_cmd.add_argument("--output", "-o", help="Write JSON summary to a file instead of stdout")

    get = subparsers.add_parser("get", aliases=["inspect"], help="Get saved page details (URL or local case) as JSON")
    get.add_argument("url", help="Saved page URL (/gallery/pages/<folder>) or local image/case path")
    get.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Baseline data directory for local cases")
    get.add_argument("--output", "-o", help="Write JSON to a file instead of stdout")

    rerun = subparsers.add_parser("rerun", help="Rerun from a target stage (URL preview or local separate-dir rerun)")
    rerun.add_argument("url", nargs="+", help="Saved page URL or local image/folder/case path(s)")
    rerun.add_argument(
        "--from", "--stage", dest="start", choices=ALL_STAGE_CHOICES, default="layout",
        help="Earliest stage to rerun: input, detection, ocr, bubble_detection, textline_merge, translation, mask_generation, layout, inpainting, or rendering",
    )
    rerun.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Baseline data directory for local cases")
    rerun.add_argument("--output-dir", help="Separate directory to save local stage rerun state")
    rerun.add_argument("--from-run", help="Reuse JSON artifacts from a specific previous run directory")
    rerun.add_argument("--from-baseline", action="store_true", help="Reuse JSON artifacts from the original baseline run")
    rerun.add_argument("--translator", default="sugoi", help="Translator for local translation runs (default: sugoi)")
    rerun.add_argument("--render-output", help="Save the rendered review image here")
    rerun.add_argument("--patch", help="JSON file with settingsOverrides and/or replacement artifacts")
    rerun.add_argument("--profile", action="store_true", help="Include detailed layout solver profiling")
    rerun.add_argument("--output", "-o", help="Write JSON to a file instead of stdout")

    compare = subparsers.add_parser("compare", help="Compare two saved local case directories or case.json files")
    compare.add_argument("before", help="Baseline or previous run directory / case.json")
    compare.add_argument("after", help="New stage rerun directory / case.json")
    compare.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Baseline data directory")
    compare.add_argument("--output", "-o", help="Write comparison JSON to a file instead of stdout")

    benchmark = subparsers.add_parser("benchmark", help="Repeat read-only layout previews for saved page URLs")
    benchmark.add_argument("urls", nargs="+", help="Saved-page URLs from /gallery/pages or /result")
    benchmark.add_argument("--repeat", type=int, default=1, help="Number of previews per page")
    benchmark.add_argument("--output", "-o", help="Write the machine-readable JSON artifact here")

    args = parser.parse_args(argv)

    try:
        if args.command in {"check", "status"}:
            result = check_local_cases(
                args.targets,
                stage=args.stage,
                data_dir=args.data_dir,
                from_run=args.from_run,
                from_baseline=args.from_baseline,
            )
        elif args.command == "run":
            patch = _load_patch_file(args.patch)
            result = run_local_cases(
                args.targets,
                stage=args.stage,
                data_dir=args.data_dir,
                output_dir=args.output_dir,
                from_run=args.from_run,
                from_baseline=args.from_baseline,
                force_full=args.force_full,
                overwrite_baseline=args.overwrite_baseline,
                translator=args.translator,
                patch=patch,
                include_layout_profile=args.profile,
                render_output=args.render_output,
            )
        elif args.command == "compare":
            result = compare_cases(args.before, args.after, data_dir=args.data_dir)
        elif args.command == "benchmark":
            result = benchmark_cases(args.urls, args.repeat)
        elif args.command in {"get", "inspect"}:
            if not _is_http_url(args.url) and Path(args.url).expanduser().exists():
                result = inspect_local_case(args.url, data_dir=args.data_dir)
            else:
                origin, ref = page_reference(args.url)
                result = inspect_case(origin, ref)
        else:
            patch = _load_patch_file(args.patch)
            targets = args.url if isinstance(args.url, list) else [args.url]
            if len(targets) == 1 and (_is_http_url(targets[0]) or not Path(targets[0]).expanduser().exists()):
                origin, ref = page_reference(targets[0])
                result = rerun_case(
                    origin, ref, args.start, patch=patch, render_output=args.render_output,
                    include_layout_profile=args.profile,
                )
            else:
                result = run_local_cases(
                    targets,
                    stage=args.start,
                    data_dir=args.data_dir,
                    output_dir=args.output_dir,
                    from_run=args.from_run,
                    from_baseline=args.from_baseline,
                    translator=args.translator,
                    patch=patch,
                    include_layout_profile=args.profile,
                    render_output=args.render_output,
                )

        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as output:
                output.write(rendered + "\n")
        else:
            print(rendered)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"pipeline_case: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
