#!/usr/bin/env python3
"""Export manga images and summary from a running manga-image-translator server into Semantic Search Lab format.

Usage example:
    python3 devscripts/export_manga_for_search.py --manga "Solo Leveling" --output devscripts/data/manga_inputs
    python3 devscripts/export_manga_for_search.py --all --output devscripts/data/manga_inputs
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _normalize_base_url(url: str) -> str:
    url = (url or "http://127.0.0.1:8000").strip()
    return url.rstrip("/")


def _resolve_server_url(url: str) -> str:
    normalized = _normalize_base_url(url)
    candidates = [normalized, "http://127.0.0.1:8000", "http://127.0.0.1:5003", "http://localhost:8000", "http://localhost:5003"]
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            req = urllib.request.Request(f"{candidate}/api/status", method="GET")
            with urllib.request.urlopen(req, timeout=1.5):
                return candidate
        except Exception:
            continue
    return normalized


def _api_get_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8")
        return json.loads(content) if content else {}


def _download_file(url: str, destination: Path, timeout: float = 60.0) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "manga-exporter/1.0"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp, destination.open("wb") as out_file:
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            out_file.write(chunk)


def _safe_folder_name(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name.strip())
    return cleaned or "manga"


def export_single_manga(
    base_url: str,
    group: dict[str, Any],
    output_dir: Path,
    image_type: str = "input",  # "input", "translated", or "auto"
) -> Path:
    group_id = group.get("id") or group.get("groupId")
    manga_title = group.get("title") or group.get("mangaTitle") or group_id or "manga"
    folder_name = _safe_folder_name(manga_title)
    manga_dir = output_dir / folder_name
    manga_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[+] Exporting '{manga_title}' (ID: {group_id}) -> {manga_dir}")

    # 1. Fetch summary
    summary_text = ""
    try:
        summary_url = f"{base_url}/api/results/group/summary?groupId={urllib.parse.quote(str(group_id))}"
        summary_payload = _api_get_json(summary_url)
        summary_text = (summary_payload.get("summary") or "").strip()
    except Exception as err:
        print(f"    [!] Warning: Could not fetch summary for '{manga_title}': {err}")

    if summary_text:
        summary_path = manga_dir / "summary.txt"
        summary_path.write_text(summary_text, encoding="utf-8")
        print(f"    - Saved summary.txt ({len(summary_text)} characters)")
    else:
        print("    - No summary found or summary is empty")

    # 2. Fetch pages list
    pages_url = f"{base_url}/api/manga/{urllib.parse.quote(str(group_id))}/pages?limit=500"
    try:
        pages_payload = _api_get_json(pages_url)
    except Exception as err:
        print(f"    [!] Error fetching pages: {err}")
        return manga_dir

    items = pages_payload.get("items") or pages_payload.get("directories") or []
    print(f"    - Found {len(items)} page(s)")

    for idx, item in enumerate(items, 1):
        urls = item.get("urls") or {}
        image_relative_url = None
        if image_type == "input":
            image_relative_url = urls.get("inputUrl") or urls.get("detailPreviewUrl") or urls.get("readerUrl")
        elif image_type == "translated":
            image_relative_url = urls.get("readerUrl") or urls.get("detailPreviewUrl") or urls.get("inputUrl")
        else:
            image_relative_url = urls.get("inputUrl") or urls.get("readerUrl") or urls.get("detailPreviewUrl")

        if not image_relative_url:
            folder_key = item.get("folder_name") or item.get("id")
            if folder_key:
                image_relative_url = f"/result/{folder_key}/final.png"

        if not image_relative_url:
            print(f"    [!] Skipping page {idx}: no image URL found")
            continue

        if image_relative_url.startswith("http://") or image_relative_url.startswith("https://"):
            full_img_url = image_relative_url
        else:
            full_img_url = f"{base_url}{image_relative_url}"

        # Determine file extension
        parsed_path = urllib.parse.urlparse(full_img_url).path
        suffix = Path(parsed_path).suffix or ".png"
        page_file = manga_dir / f"{idx:03d}{suffix}"

        try:
            _download_file(full_img_url, page_file)
            print(f"    - Downloaded page {idx:03d} -> {page_file.name}")
        except Exception as err:
            print(f"    [!] Failed to download page {idx} from {full_img_url}: {err}")

    return manga_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Export manga data from server for Semantic Search Lab")
    parser.add_argument("--server-url", default="http://127.0.0.1:8000", help="Base URL of running manga translator server")
    parser.add_argument("--manga", help="Manga title or Group ID to export (partial match supported)")
    parser.add_argument("--all", action="store_true", help="Export all mangas from the server")
    parser.add_argument("--output", type=Path, default=Path("devscripts/data/manga_inputs"), help="Target output directory")
    parser.add_argument("--image-type", choices=["input", "translated", "auto"], default="input",
                        help="Which image version to export ('input' source pages or 'translated' pages)")
    args = parser.parse_args()

    if not args.manga and not args.all:
        parser.error("Specify --manga <name_or_id> or --all")

    server_url = _resolve_server_url(args.server_url)
    print(f"Connecting to manga-image-translator server at: {server_url}")

    # Fetch groups
    try:
        search_query = args.manga if (args.manga and not args.all) else ""
        url = f"{server_url}/api/results/groups?limit=500"
        if search_query:
            url += f"&search={urllib.parse.quote(search_query)}"
        res = _api_get_json(url)
        groups = res.get("groups", [])
    except Exception as err:
        sys.exit(f"Error connecting to server: {err}")

    if not groups:
        # Fallback to search query
        print(f"No groups found with search filter '{args.manga}'. Fetching all groups to match...")
        try:
            res = _api_get_json(f"{server_url}/api/results/groups?limit=500")
            all_groups = res.get("groups", [])
            query_lower = (args.manga or "").lower()
            groups = [g for g in all_groups if query_lower in (g.get("title") or "").lower() or query_lower in (g.get("id") or "").lower()]
        except Exception as err:
            sys.exit(f"Error searching groups: {err}")

    if not groups:
        sys.exit("No matching manga groups found on the server.")

    print(f"Found {len(groups)} manga group(s) to export.")
    args.output.mkdir(parents=True, exist_ok=True)

    exported_dirs = []
    for g in groups:
        m_dir = export_single_manga(server_url, g, args.output, image_type=args.image_type)
        exported_dirs.append(m_dir)

    print("\n" + "=" * 60)
    print(f"Successfully exported {len(exported_dirs)} manga(s) to: {args.output.resolve()}")
    print("\nYou can now index them with Semantic Search Lab by running:")
    print(f"python3 devscripts/semantic_search_lab.py index --input {args.output}")
    print("=" * 60)


if __name__ == "__main__":
    main()
