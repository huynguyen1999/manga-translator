"""Inspect saved pages and run temporary, review-only pipeline previews."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen


ID_PREFIXES = {
    "detection": "detection",
    "ocr": "ocr_region",
    "bubble_detections": "speech_bubble",
    "text_regions_merged": "text_region",
    "text_regions": "text_region",
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


def rerun_case(origin: str, ref: str, start: str, *, patch=None, render_output=None) -> dict:
    page = request_json(origin, f"/api/results/{quote(ref, safe='')}")
    page_id = page.get("id") or ref
    mode = RERUN_MODES[start]
    result = request_json(
        origin,
        "/api/pipeline-cases/preview",
        method="POST",
        body={"pageId": page_id, "mode": mode, **(patch or {})},
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    get = subparsers.add_parser("get", aliases=["inspect"], help="Get saved page details as JSON")
    get.add_argument("url", help="Saved page URL, e.g. /gallery/pages/<folder>")
    get.add_argument("--output", "-o", help="Write JSON to a file instead of stdout")
    rerun = subparsers.add_parser("rerun", help="Preview a rerun in temporary files")
    rerun.add_argument("url", help="Saved page URL, e.g. /gallery/pages/<folder>")
    rerun.add_argument(
        "--from", dest="start", choices=RERUN_MODES, default="layout",
        help="Earliest stage to rerun: input, detection, bubble_detection, translation, or layout",
    )
    rerun.add_argument("--render-output", help="Save the rendered review image here")
    rerun.add_argument(
        "--patch", help="JSON file with settingsOverrides and/or replacement artifacts"
    )
    rerun.add_argument("--output", "-o", help="Write JSON to a file instead of stdout")
    args = parser.parse_args(argv)

    try:
        origin, ref = page_reference(args.url)
        patch = None
        if args.command == "rerun" and args.patch:
            with open(args.patch, encoding="utf-8") as source:
                patch = json.load(source)
            if not isinstance(patch, dict) or set(patch) - {"settingsOverrides", "artifacts"}:
                raise ValueError("Patch JSON must contain only settingsOverrides and/or artifacts objects")
            if any(not isinstance(patch.get(key, {}), dict) for key in ("settingsOverrides", "artifacts")):
                raise ValueError("settingsOverrides and artifacts must be JSON objects")
        result = (
            inspect_case(origin, ref)
            if args.command in {"get", "inspect"}
            else rerun_case(origin, ref, args.start, patch=patch, render_output=args.render_output)
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
