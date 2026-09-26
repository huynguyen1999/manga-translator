#!/usr/bin/env python3
"""Export one saved pipeline artifact for every page in a manga."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


STEPS = (
    "detection",
    "ocr",
    "bubble_detections",
    "text_regions_merged",
    "translations",
    "translation_remap",
    "layout",
    "text_regions",
)


def get_json(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach server: {error.reason}") from error


def export_step(server: str, manga: str, step: str, sort_order: str) -> dict:
    query = urlencode({"search": manga, "limit": 500})
    groups = get_json(f"{server}/api/results/groups?{query}").get("groups", [])
    matches = [group for group in groups if manga.casefold() in (group.get("title") or "").casefold()]
    exact = [group for group in matches if (group.get("title") or "").casefold() == manga.casefold()]
    matches = exact or matches
    if len(matches) != 1:
        titles = ", ".join(group.get("title", group.get("id", "?")) for group in matches)
        reason = "no matching manga" if not matches else f"multiple matches: {titles}"
        raise ValueError(reason)

    group = matches[0]
    pages = []
    offset = 0
    while True:
        query = urlencode({"limit": 500, "offset": offset})
        result = get_json(f"{server}/api/manga/{quote(str(group['id']), safe='')}/pages?{query}")
        items = result.get("items", [])
        pages.extend(items)
        if result.get("nextOffset") is None:
            break
        offset = result["nextOffset"]
    if sort_order == "desc":
        pages.reverse()

    output_pages = []
    for page in pages:
        ref = page.get("id") or page.get("folder")
        case = get_json(f"{server}/api/pipeline-cases/{quote(str(ref), safe='')}/data")
        output_pages.append({
            "page": page,
            "artifact": case.get("artifacts", {}).get(step),
        })
    return {"manga": group.get("title", manga), "step": step, "sortOrder": sort_order, "pages": output_pages}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manga", required=True, help="Manga title (exact or unique partial match)")
    parser.add_argument("--step", required=True, choices=STEPS, help="Saved pipeline artifact to export")
    parser.add_argument("--sort-order", choices=("asc", "desc"), default="asc")
    parser.add_argument("--server-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, help="Write JSON to a file instead of stdout")
    args = parser.parse_args(argv)

    try:
        result = export_step(args.server_url.rstrip("/"), args.manga, args.step, args.sort_order)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"export_pipeline_step: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
