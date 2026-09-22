#!/usr/bin/env python3
"""Measure peak memory during a synthetic professional-mode translation run.

No model weights are needed.  All LLM calls are mocked.

Usage::

    python devscripts/mem_probe_run.py            # default 100 pages
    python devscripts/mem_probe_run.py --pages 50
    python devscripts/mem_probe_run.py --pages 10 --chunk-size 5

Results are printed to stdout as labelled checkpoints showing traced KiB and
(if psutil is installed) RSS MiB.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import tempfile
from pathlib import Path

# Ensure the repo root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from manga_translator import Context
from manga_translator.config import TranslatorConfig
from manga_translator.mem_probe import log_checkpoint
from manga_translator.professional_translation import (
    ProfessionalTranslator,
    translate_professionally,
)


def _make_png_bytes(w: int = 8, h: int = 8) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color=(100, 150, 200)).save(buf, "PNG")
    return buf.getvalue()


async def run(n_pages: int, chunk_size: int) -> None:
    print(f"\n=== mem_probe_run: {n_pages} pages, chunk_size={chunk_size} ===\n")
    log_checkpoint("start")

    config = TranslatorConfig(
        translator="deepseek",
        target_lang="ENG",
        translation_quality="professional",
        translation_batch_size=chunk_size,
    )

    # Build synthetic (ctx, config) pairs — no real images in ctx.input
    pairs = []
    for i in range(n_pages):
        ctx = Context()
        ctx.text_regions = []
        ctx.debug_folder = f"synthetic-page-{i:04d}"
        ctx.image_context = {
            "subfolder": ctx.debug_folder,
            "file_md5": f"md5-{i:04d}",
            "request_id": None,
        }
        cfg = type("Config", (), {
            "page_order": i + 1,
            "translator": config,
            "render": type("R", (), {
                "uppercase": False,
                "lowercase": False,
                "transform_text_case": None,
                "alignment": "auto",
                "direction": "auto",
            })(),
        })()
        pairs.append((ctx, cfg))

    log_checkpoint(f"contexts built ({n_pages} pages)")

    # Mock the LLM call
    import json, re as _re
    async def fake_json_request(stage: str, prompt: str):
        ids = _re.findall(r'"id":\s*"([^"\\]+)"', prompt)
        if stage in ("analysis", "analysis-window"):
            return ({"stories": [{"start_page": 1, "end_page": n_pages, "confidence": 1.0,
                                   "summary": "synthetic", "characters": [], "relationships": [],
                                   "glossary": {}, "voice_notes": "", "continuity": "",
                                   "ambiguities": []}]}, "mock")
        if stage == "analysis-consolidation":
            data = json.loads(prompt.rsplit("\n", 1)[-1])
            stories = data[0].get("stories") if data else []
            return ({"stories": stories}, "mock")
        return ({"regions": [{"id": rid, "translation": f"OK-{rid}", "confidence": 1.0,
                               "review_reasons": []} for rid in ids]}, "mock")

    engine = ProfessionalTranslator(config)
    engine._json_request = fake_json_request

    log_checkpoint("before analyze")
    analysis = await engine.analyze(
        [{"number": i + 1, "regions": []} for i in range(n_pages)],
        None,
    )
    log_checkpoint("after analyze")

    for si, story in enumerate(analysis["stories"]):
        story_pages = [{"number": i + 1, "regions": [{"id": f"r{i}", "source": "日本語"}]}
                       for i in range(n_pages)]
        await engine.localize_story(story, story_pages, si + 1, len(analysis["stories"]))
        log_checkpoint(f"after localize story {si + 1}")
        await engine.edit_story(story, story_pages, si + 1, len(analysis["stories"]))
        log_checkpoint(f"after edit story {si + 1}")

    log_checkpoint("translation phase complete")

    # Simulate deferred-load render phase: load + release one page at a time
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for i, (ctx, _) in enumerate(pairs):
            img_path = tmp_path / f"page-{i:04d}.png"
            img_path.write_bytes(_make_png_bytes())
            with Image.open(img_path) as img:
                ctx.input = img.convert("RGB")
            # Simulate render work (just clear immediately)
            ctx.input = None
        log_checkpoint(f"after rendering all {n_pages} pages (deferred, one-at-a-time)")

    log_checkpoint("done")
    print("\nNote: 'traced_kb' tracks Python-side allocations via tracemalloc.")
    print("      'rss_mb' is total process RSS (requires psutil).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pages", type=int, default=100, help="Number of synthetic pages (default: 100)")
    parser.add_argument("--chunk-size", type=int, default=5, help="Translation chunk size (default: 5)")
    args = parser.parse_args()
    asyncio.run(run(args.pages, args.chunk_size))


if __name__ == "__main__":
    main()
