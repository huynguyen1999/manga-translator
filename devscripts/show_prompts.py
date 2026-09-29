#!/usr/bin/env python3
"""Inspect prompt templates and formatted LLM payloads for:
1. Manga Synopsis (server summary generation)
2. Manga Small Summary for Translation (professional Story Analysis & Localization Guide)
3. Manga Translation Chunk (professional single-pass translation with story guide context)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from manga_translator.professional_panels import (
    build_page_panel_structure,
    format_page_transcript_for_analysis,
    format_synopsis_transcript,
    group_transcript_panel_texts,
)
from manga_translator.professional_prompts import (
    PROFESSIONAL_ANALYSIS_SYSTEM_PROMPT,
    PROFESSIONAL_SYSTEM_PROMPT,
    build_analysis_consolidation_prompt,
    build_analysis_prompt,
    build_synopsis_system_prompt,
    build_translation_prompt,
)


# --- Built-in Sample Data ---

SAMPLE_PAGES: list[dict[str, Any]] = [
    {
        "page": 1,
        "number": 1,
        "name": "001",
        "panels": [
            {
                "panel_id": "p1_01",
                "panel_order": 1,
                "xyxy": [50, 50, 950, 450],
                "regions": [
                    {"id": "r1", "japanese": "おい、聞いたか？昨日の転校生の話…", "confidence": 0.95},
                    {"id": "r2", "japanese": "ああ、なんでも超能力が使えるらしいぜ。", "confidence": 0.92},
                ],
            },
            {
                "panel_id": "p1_02",
                "panel_order": 2,
                "xyxy": [50, 470, 950, 900],
                "regions": [
                    {"id": "r3", "japanese": "バッカじゃないの！そんなの都市伝説に決まってるでしょ！", "confidence": 0.98},
                ],
            },
        ],
        "unassigned_regions": [
            {"id": "r4", "japanese": "第1話：運命の出会い", "confidence": 0.99},
        ],
        "regions": [
            {"id": "r1", "japanese": "おい、聞いたか？昨日の転校生の話…", "confidence": 0.95, "panel_index": 0},
            {"id": "r2", "japanese": "ああ、なんでも超能力が使えるらしいぜ。", "confidence": 0.92, "panel_index": 0},
            {"id": "r3", "japanese": "バッカじゃないの！そんなの都市伝説に決まってるでしょ！", "confidence": 0.98, "panel_index": 1},
            {"id": "r4", "japanese": "第1話：運命の出会い", "confidence": 0.99},
        ],
    },
    {
        "page": 2,
        "number": 2,
        "name": "002",
        "panels": [
            {
                "panel_id": "p2_01",
                "panel_order": 1,
                "xyxy": [60, 60, 480, 500],
                "regions": [
                    {"id": "r5", "japanese": "（…でも、あの時の光は一体…？）", "confidence": 0.89},
                ],
            },
            {
                "panel_id": "p2_02",
                "panel_order": 2,
                "xyxy": [500, 60, 940, 500],
                "regions": [
                    {"id": "r6", "japanese": "待たせたな、蓮。", "confidence": 0.96},
                    {"id": "r7", "japanese": "遅いよ、カイト！先生もう来ちゃうよ！", "confidence": 0.94},
                ],
            },
        ],
        "unassigned_regions": [],
        "regions": [
            {"id": "r5", "japanese": "（…でも、あの時の光は一体…？）", "confidence": 0.89, "panel_index": 0},
            {"id": "r6", "japanese": "待たせたな、蓮。", "confidence": 0.96, "panel_index": 1},
            {"id": "r7", "japanese": "遅いよ、カイト！先生もう来ちゃうよ！", "confidence": 0.94, "panel_index": 1},
        ],
    },
]

SAMPLE_STORY_GUIDE: dict[str, Any] = {
    "start_page": 1,
    "end_page": 2,
    "confidence": 0.95,
    "summary": "High school students Ren and Kaito discuss the mysterious transfer student rumored to possess supernatural abilities. Ren remains skeptical despite witnessing a strange light earlier.",
    "characters": [
        {
            "name": "Ren",
            "voice": {
                "register": "casual/informal",
                "politeness": "direct",
                "traits": "skeptical, lively, expressive",
                "sentence_style": "punctuated with exclamation, tsundere undertones",
                "pronoun_notes": "uses standard female first-person",
            },
        },
        {
            "name": "Kaito",
            "voice": {
                "register": "casual masculine",
                "politeness": "blunt",
                "traits": "calm, laid-back childhood friend",
                "sentence_style": "short phrases, relaxed",
            },
        },
    ],
    "relationships": "Ren and Kaito are close classmates and childhood friends.",
    "glossary": {"超能力": "psychic powers / supernatural abilities", "都市伝説": "urban legend"},
    "honorific_policy": {
        "default": "omit in English when relationship implied",
        "rules": [
            {"form": "先生", "strategy": "translate as teacher", "reason": "standard academic context"}
        ],
    },
    "localization_conventions": {
        "dialect_strategy": "natural contemporary colloquial English",
        "slang_strategy": "light teen colloquialisms",
        "profanity_strategy": "match source intensity",
    },
}

SAMPLE_PREVIOUS_CHUNK_CONTEXT: str = (
    "[Page 0]\n"
    "[PANEL p0_01]\n"
    "A strange glowing meteor descended over the academy grounds during the storm last night."
)


# --- ANSI Color Helpers ---

class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BG_BLUE = "\033[44m"
    BG_DARK = "\033[48;5;236m"


def colorize(text: str, *styles: str, enabled: bool = True) -> str:
    if not enabled or not styles:
        return text
    prefix = "".join(styles)
    return f"{prefix}{text}{Color.RESET}"


def box_title(title: str, subtitle: str = "", width: int = 80, enabled: bool = True) -> str:
    line = "━" * width
    res = [
        colorize(f"┏{line}┓", Color.CYAN, Color.BOLD, enabled=enabled),
        colorize(f"┃  {title.ljust(width - 4)}  ┃", Color.WHITE, Color.BOLD, enabled=enabled),
    ]
    if subtitle:
        res.append(colorize(f"┃  {subtitle.ljust(width - 4)}  ┃", Color.DIM, Color.CYAN, enabled=enabled))
    res.append(colorize(f"┗{line}┛", Color.CYAN, Color.BOLD, enabled=enabled))
    return "\n".join(res)


def section_header(title: str, width: int = 80, enabled: bool = True) -> str:
    dashes = "─" * max(4, width - len(title) - 6)
    return colorize(f"\n┌─ [ {title} ] {dashes}", Color.YELLOW, Color.BOLD, enabled=enabled)


# --- Data Loading Helpers ---

def load_pages_from_dir(dir_path: Path) -> list[dict[str, Any]]:
    """Load pages, text regions, and panel detections from a local results directory."""
    pages = []
    page_dirs = [d for d in dir_path.iterdir() if d.is_dir() and not d.name.startswith(".")]
    # Natural sort
    page_dirs.sort(key=lambda p: [int(s) if s.isdigit() else s.lower() for s in p.name.split("_")])

    for idx, p_dir in enumerate(page_dirs, 1):
        regions_file = p_dir / "text_regions.json"
        panels_file = p_dir / "panel_detections.json"
        meta_file = p_dir / "meta.json"

        regions = []
        if regions_file.is_file():
            try:
                regions = json.loads(regions_file.read_text(encoding="utf-8"))
            except Exception:
                regions = []

        panels = []
        if panels_file.is_file():
            try:
                panels = json.loads(panels_file.read_text(encoding="utf-8"))
            except Exception:
                panels = []

        meta = {}
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = {}

        page_entry = {
            "page": idx,
            "number": idx,
            "name": meta.get("name") or p_dir.name,
            "regions": regions,
            "panels": panels,
            "path": str(p_dir),
        }
        pages.append(page_entry)

    return pages


def fetch_pages_from_server(server_url: str, manga_query: str) -> tuple[str, list[dict[str, Any]]]:
    """Fetch manga group and its pages from running server API."""
    base_url = server_url.rstrip("/")
    query = urlencode({"search": manga_query, "limit": 100})
    req = Request(f"{base_url}/api/results/groups?{query}", headers={"Accept": "application/json"})
    with urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    groups = data.get("groups", [])
    matches = [g for g in groups if manga_query.casefold() in (g.get("title") or "").casefold()]
    exact = [g for g in matches if (g.get("title") or "").casefold() == manga_query.casefold()]
    selected = exact[0] if exact else (matches[0] if matches else None)

    if not selected:
        titles = ", ".join(g.get("title", g.get("id", "?")) for g in groups[:10])
        raise ValueError(f"No manga matching '{manga_query}'. Available: {titles}")

    group_id = selected["id"]
    group_title = selected.get("title", str(group_id))

    # Fetch pages
    pages = []
    offset = 0
    while True:
        p_query = urlencode({"limit": 200, "offset": offset})
        req = Request(f"{base_url}/api/manga/{quote(str(group_id), safe='')}/pages?{p_query}", headers={"Accept": "application/json"})
        with urlopen(req, timeout=30) as resp:
            p_data = json.loads(resp.read().decode("utf-8"))
        items = p_data.get("items", [])
        pages.extend(items)
        if p_data.get("nextOffset") is None:
            break
        offset = p_data["nextOffset"]

    # Fetch full pipeline case data per page
    detailed_pages = []
    for idx, page in enumerate(pages, 1):
        ref = page.get("id") or page.get("folder")
        case_req = Request(f"{base_url}/api/pipeline-cases/{quote(str(ref), safe='')}/data", headers={"Accept": "application/json"})
        try:
            with urlopen(case_req, timeout=15) as c_resp:
                case_data = json.loads(c_resp.read().decode("utf-8"))
                artifacts = case_data.get("artifacts", {})
                regions = artifacts.get("text_regions") or artifacts.get("ocr") or []
                panels = artifacts.get("bubble_detections", {}).get("panels") or []
        except Exception:
            regions = page.get("textRegions", [])
            panels = page.get("panels", [])

        detailed_pages.append({
            "page": idx,
            "number": idx,
            "name": page.get("name") or str(ref),
            "regions": regions,
            "panels": panels,
            "path": page.get("folder"),
        })

    return group_title, detailed_pages


# --- Prompt Builders & Formatters ---

def build_synopsis_prompt_data(
    pages: list[dict[str, Any]],
    target_lang: str = "ENG",
    merge: bool = False,
) -> dict[str, Any]:
    """Construct Manga Synopsis prompt payload."""
    instruction = (
        "Combine the supplied partial summaries into one spoiler-inclusive synopsis. Deduplicate overlapping "
        "events and recurring explanations, preserve causal order within each story, keep every detail attached "
        "to the correct story, and never combine unrelated stories or invent continuity between them."
        if merge
        else "Create a detailed, spoiler-inclusive synopsis from the supplied original manga dialogue and narration."
    )
    system_prompt = build_synopsis_system_prompt(instruction, target_lang)

    # Build snapshot entries for transcript
    entries = []
    for page in pages:
        regions = page.get("regions", [])
        panels = page.get("panels", [])
        panel_groups = group_transcript_panel_texts(regions, panels)
        texts = [
            str(r.get("original_text") or r.get("japanese") or r.get("source") or r.get("text") or "").strip()
            for r in regions
            if str(r.get("original_text") or r.get("japanese") or r.get("source") or r.get("text") or "").strip()
        ]
        entries.append({
            "name": str(page.get("name") or page.get("number") or page.get("page")),
            "texts": texts,
            "panel_groups": panel_groups,
        })

    snapshot = {"entries": entries}
    formatted_transcript_pages = format_synopsis_transcript(snapshot)
    user_transcript = "\n\n".join(formatted_transcript_pages)

    return {
        "title": "Manga Synopsis Prompt",
        "description": "Used by backend manga synopsis generator to produce comprehensive spoiler-inclusive story synopsis, overview, characters, premise, and unresolved threads.",
        "target_language": target_lang,
        "is_merge_consolidation": merge,
        "system_prompt": system_prompt,
        "user_prompt": user_transcript,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_transcript},
        ],
    }


def build_story_summary_prompt_data(
    pages: list[dict[str, Any]],
    manual_ranges: list[tuple[int, int]] | None = None,
    auto_within_manual: bool = True,
) -> dict[str, Any]:
    """Construct Manga Small Summary for Translation (Story Analysis) prompt payload."""
    system_prompt = PROFESSIONAL_ANALYSIS_SYSTEM_PROMPT

    page_blocks = [
        format_page_transcript_for_analysis(
            build_page_panel_structure(page.get("number", page.get("page", 1)), page.get("regions", []), page.get("panels"))
        )
        for page in pages
    ]
    page_text = "\n\n".join(page_blocks)

    ranges = manual_ranges or []
    user_prompt = build_analysis_prompt(page_text, ranges, auto_within_manual)

    forced_instruction = (
        f"Use these exact story ranges: {ranges}."
        if ranges and not auto_within_manual
        else (f"Detect additional story breaks, but never cross these manual ranges: {ranges}." if ranges else "Detect the story ranges yourself.")
    )
    sample_partials = [{"stories": [{"start_page": 1, "end_page": len(pages), "summary": "Sample partial story summary..."}]}]
    consolidation_prompt = build_analysis_consolidation_prompt(forced_instruction, sample_partials)

    return {
        "title": "Manga Small Summary for Translation (Story Analysis & Localization Guide)",
        "description": "Stage 1 of Professional Translation: Analyzes manga narrative structure before translation to extract story bounds, character voice profiles, honorific policies, slang/dialect strategies, relationships, and glossaries.",
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "consolidation_prompt": consolidation_prompt,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }


def build_translation_chunk_prompt_data(
    pages: list[dict[str, Any]],
    guide: dict[str, Any] | None = None,
    previous_context: str | None = None,
    chunk_size: int = 5,
) -> dict[str, Any]:
    """Construct Manga Translation Chunk prompt payload."""
    system_prompt = PROFESSIONAL_SYSTEM_PROMPT
    chunk = pages[:chunk_size]

    payload = [
        build_page_panel_structure(page.get("number", page.get("page", 1)), page.get("regions", []), page.get("panels"))
        for page in chunk
    ]
    story_guide = json.dumps(guide or SAMPLE_STORY_GUIDE, ensure_ascii=False)
    prev = previous_context if previous_context is not None else SAMPLE_PREVIOUS_CHUNK_CONTEXT

    user_prompt = build_translation_prompt(payload, story_guide, prev)

    return {
        "title": "Manga Translation Chunk Prompt",
        "description": "Stage 2 of Professional Translation: Translates a chunk of pages/panels in a single LLM pass, guided by the Story Summary & Localization Guide, ensuring character voice, terminology, and balloon continuity.",
        "chunk_size": len(chunk),
        "total_pages": len(pages),
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }


# --- Renderers ---

def render_pretty(prompt_data: dict[str, Any], enabled: bool = True) -> str:
    lines = []
    lines.append(box_title(prompt_data["title"], prompt_data.get("description", ""), enabled=enabled))

    # Details
    meta_parts = []
    for k in ("target_language", "chunk_size", "total_pages", "is_merge_consolidation"):
        if k in prompt_data:
            meta_parts.append(f"{k}: {prompt_data[k]}")
    if meta_parts:
        lines.append(colorize("Config: " + " | ".join(meta_parts), Color.DIM, enabled=enabled))

    # System Prompt
    lines.append(section_header("SYSTEM PROMPT (Role & Localization Instructions)", enabled=enabled))
    lines.append(colorize(prompt_data["system_prompt"], Color.GREEN, enabled=enabled))

    # User Prompt
    lines.append(section_header("USER PROMPT (Payload / Transcript / Context)", enabled=enabled))
    lines.append(prompt_data["user_prompt"])

    # Optional Consolidation Prompt
    if "consolidation_prompt" in prompt_data:
        lines.append(section_header("MULTI-WINDOW CONSOLIDATION PROMPT (When context > 50k tokens)", enabled=enabled))
        lines.append(colorize(prompt_data["consolidation_prompt"], Color.MAGENTA, enabled=enabled))

    lines.append("\n" + colorize("═" * 80, Color.CYAN, Color.DIM, enabled=enabled) + "\n")
    return "\n".join(lines)


def render_markdown(prompt_data: dict[str, Any]) -> str:
    lines = [
        f"# {prompt_data['title']}",
        "",
        f"> {prompt_data.get('description', '')}",
        "",
    ]
    lines.append("## System Prompt")
    lines.append("```text")
    lines.append(prompt_data["system_prompt"])
    lines.append("```")
    lines.append("")
    lines.append("## User Prompt")
    lines.append("```text")
    lines.append(prompt_data["user_prompt"])
    lines.append("```")
    lines.append("")
    if "consolidation_prompt" in prompt_data:
        lines.append("## Multi-Window Consolidation Prompt")
        lines.append("```text")
        lines.append(prompt_data["consolidation_prompt"])
        lines.append("```")
        lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# --- Main CLI ---

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Manga Synopsis, Small Summary (Story Analysis), and Translation Chunk prompts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # View all 3 prompts with sample manga data
  python3 devscripts/show_prompts.py

  # View only Manga Synopsis prompt
  python3 devscripts/show_prompts.py --prompt synopsis

  # View only Manga Small Summary for Translation (Story Analysis)
  python3 devscripts/show_prompts.py --prompt summary

  # View only Manga Translation Chunk prompt
  python3 devscripts/show_prompts.py --prompt chunk

  # View prompts for a local manga folder in result/
  python3 devscripts/show_prompts.py --dir result/SampleManga

  # Fetch live pages from running server
  python3 devscripts/show_prompts.py --server http://127.0.0.1:8000 --manga "My Manga Title"

  # Export as Markdown or JSON
  python3 devscripts/show_prompts.py --format markdown --output prompts.md
  python3 devscripts/show_prompts.py --format json
""",
    )
    parser.add_argument(
        "-p", "--prompt",
        choices=["all", "synopsis", "summary", "analysis", "chunk", "translation"],
        default="all",
        help="Which prompt to display (default: all). 'summary'/'analysis' is the story analysis guide.",
    )
    parser.add_argument(
        "-l", "--target-lang",
        default="ENG",
        help="Target language code for synopsis/translation (default: ENG).",
    )
    parser.add_argument(
        "-c", "--chunk-size",
        type=int,
        default=5,
        help="Chunk size (pages per translation batch, default: 5).",
    )
    parser.add_argument(
        "-r", "--story-ranges",
        help="Manual story ranges (e.g. '1-10,11-20') for story analysis.",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Display the synopsis multi-chunk merge consolidation prompt variant.",
    )
    parser.add_argument(
        "--dir", "--manga-dir",
        dest="manga_dir",
        type=Path,
        help="Path to a local manga result folder containing page subdirectories.",
    )
    parser.add_argument(
        "--server",
        default="",
        help="Server base URL (e.g. http://127.0.0.1:8000) to fetch manga from.",
    )
    parser.add_argument(
        "--manga",
        default="",
        help="Manga title to search on server when using --server.",
    )
    parser.add_argument(
        "--format",
        choices=["pretty", "markdown", "json"],
        default="pretty",
        help="Output format (default: pretty terminal view).",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Write output to file instead of stdout.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable color in terminal output.",
    )

    args = parser.parse_args(argv)

    # Determine pages data source
    pages = SAMPLE_PAGES
    source_name = "Built-in Sample Manga (2 pages with panels & speech bubbles)"

    if args.manga_dir:
        if not args.manga_dir.is_dir():
            print(f"Error: Directory not found: {args.manga_dir}", file=sys.stderr)
            return 1
        pages = load_pages_from_dir(args.manga_dir)
        source_name = f"Local directory: {args.manga_dir} ({len(pages)} pages)"
        if not pages:
            print(f"Warning: No valid page directories found in {args.manga_dir}, using sample data.", file=sys.stderr)
            pages = SAMPLE_PAGES

    elif args.server and args.manga:
        try:
            title, pages = fetch_pages_from_server(args.server, args.manga)
            source_name = f"Server {args.server} - Manga '{title}' ({len(pages)} pages)"
        except Exception as exc:
            print(f"Error fetching from server: {exc}", file=sys.stderr)
            return 1

    # Parse manual story ranges if provided
    manual_ranges = None
    if args.story_ranges:
        try:
            from manga_translator.professional_translation import parse_story_ranges
            manual_ranges = parse_story_ranges(args.story_ranges, len(pages))
        except Exception as exc:
            print(f"Error parsing story ranges: {exc}", file=sys.stderr)
            return 1

    # Build prompt items
    prompt_items = []
    target_prompt = args.prompt.lower()

    if target_prompt in ("all", "synopsis"):
        prompt_items.append(build_synopsis_prompt_data(pages, target_lang=args.target_lang, merge=args.merge))

    if target_prompt in ("all", "summary", "analysis"):
        prompt_items.append(build_story_summary_prompt_data(pages, manual_ranges=manual_ranges))

    if target_prompt in ("all", "chunk", "translation"):
        prompt_items.append(build_translation_chunk_prompt_data(pages, chunk_size=args.chunk_size))

    # Format output
    color_enabled = not args.no_color and sys.stdout.isatty() and args.output is None

    if args.format == "json":
        output_content = json.dumps(
            {
                "source": source_name,
                "prompts": prompt_items,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    elif args.format == "markdown":
        output_parts = [f"# Manga Translation Prompts Report\n\n**Data Source:** {source_name}\n\n---\n"]
        for item in prompt_items:
            output_parts.append(render_markdown(item))
        output_content = "\n".join(output_parts)
    else:  # pretty
        output_parts = [
            colorize(f"\n[ Manga Prompt Inspector ]  Source: {source_name}\n", Color.BOLD, Color.CYAN, enabled=color_enabled)
        ]
        for item in prompt_items:
            output_parts.append(render_pretty(item, enabled=color_enabled))
        output_content = "\n".join(output_parts)

    # Emit
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_content, encoding="utf-8")
        print(f"Wrote {len(prompt_items)} prompt(s) to {args.output}")
    else:
        print(output_content, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
