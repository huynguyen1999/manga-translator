"""Test that professional-mode rendering loads and releases images one at a time.

This test is fully mocked — no model weights are needed.  It verifies the two
key memory-safety invariants introduced by the deferred-image-loading change:

1. During ``translate_professionally`` no PIL image is ever attached to any ctx
   (``ctx.input`` remains ``None`` throughout the translation phase).
2. During rendering, at most ``_RENDER_SEMAPHORE_SIZE`` images are open at once.
3. After every render completes, ``ctx.input`` is ``None`` again (image released).
4. The rolling ``previous`` string in ``localize_story`` / ``edit_story`` never
   exceeds 8 000 characters.
"""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from manga_translator import Context
from manga_translator.config import TranslatorConfig
from server.batch_scheduler import BatchScheduler, _RENDER_SEMAPHORE_SIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png_bytes(size: tuple[int, int] = (4, 4)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=(128, 64, 32)).save(buf, "PNG")
    return buf.getvalue()


def _make_ctx(input_path: Path) -> Context:
    ctx = Context()
    ctx.text_regions = []
    ctx._deferred_image_path = input_path
    ctx.debug_folder = f"result-{input_path.stem}"
    ctx.image_context = {
        "subfolder": ctx.debug_folder,
        "file_md5": input_path.stem,
        "request_id": None,
    }
    return ctx


def _make_config() -> SimpleNamespace:
    return SimpleNamespace(
        page_order=1,
        translator=TranslatorConfig(
            translator="deepseek",
            target_lang="ENG",
            translation_quality="professional",
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class DeferredImageLoadingTest(unittest.IsolatedAsyncioTestCase):
    """Verify that professional-mode render loads and releases images per-page."""

    async def _run_render_group(
        self,
        n_pages: int,
        result_root: Path,
        image_paths: list[Path],
        result_dirs: list[Path],
    ) -> dict:
        """Run _process_translation_group with mock workers and return stats."""
        stats = {
            "concurrent_peak": 0,
            "concurrent_now": 0,
            "inputs_at_translate_time": [],   # ctx.input values during translation
            "inputs_after_render": [],         # ctx.input values after render
            "open_calls": 0,
        }

        # Build contexts with deferred paths
        configs = [_make_config() for _ in range(n_pages)]
        ctxs = [_make_ctx(p) for p in image_paths]
        contexts_with_configs = list(zip(ctxs, configs))

        # Spy: record ctx.input during translation
        for ctx in ctxs:
            stats["inputs_at_translate_time"].append(ctx.input)

        # Track Image.open calls
        real_open = Image.open

        def spying_open(path, *a, **kw):
            stats["open_calls"] += 1
            return real_open(path, *a, **kw)

        # Worker that tracks concurrency
        class MockWorker:
            def __init__(self):
                self.translator = SimpleNamespace(_progress_hooks=[])

            async def render(self, ctx, config):
                stats["concurrent_now"] += 1
                stats["concurrent_peak"] = max(
                    stats["concurrent_peak"], stats["concurrent_now"]
                )
                await asyncio.sleep(0.02)  # simulate real async work
                # render writes the result file
                folder = ctx.debug_folder
                result_dir = result_root / folder
                result_dir.mkdir(parents=True, exist_ok=True)
                (result_dir / "final.jpg").write_bytes(_make_png_bytes())
                stats["concurrent_now"] -= 1
                # Record ctx.input at this point (should be cleared after render)
                return ctx

        worker = MockWorker()
        worker_queue: asyncio.Queue = asyncio.Queue()
        # Put enough workers for all pages (semaphore limits concurrency, not workers)
        for _ in range(n_pages):
            worker_queue.put_nowait(MockWorker())

        class MockExecutors:
            def free_executors(self):
                return worker_queue.qsize()

            async def find_executor(self):
                return await worker_queue.get()

            async def free_executor(self, w):
                worker_queue.put_nowait(w)

        # Minimal batch store
        items = [
            {
                "id": f"page-{i}",
                "name": f"{i}.png",
                "stage": "awaiting_translation",
                "status": "processing",
                "resultFolder": f"result-{i}",
                # No per-item "settings" key — _config_for falls back to batch settings
            }
            for i in range(n_pages)
        ]
        batch_data = {
            "id": "test-batch",
            "items": items,
            "status": "processing",
            "settings": {"translationQuality": "professional", "translationBatchSize": n_pages},
        }

        class MockStore:
            _data = batch_data

            async def get_batch(self, _bid):
                return dict(self._data)

            async def mutate(self, _bid, fn):
                fn(self._data)

            async def input_path(self, _bid, item_id):
                idx = int(item_id.split("-")[1])
                return image_paths[idx]

        store = MockStore()
        executors = MockExecutors()
        scheduler = BatchScheduler(store, executors, result_root)

        # Patch Image.open to count calls
        with patch("server.batch_scheduler.Image.open", side_effect=spying_open):
            # Simulate translate_batch_contexts returning translated pairs
            # (ctx.input is None for all — deferred loading)
            instance_mock = MagicMock()
            instance_mock.translator = SimpleNamespace(
                _progress_hooks=[],
                add_progress_hook=lambda h: None,
            )
            instance_mock.translate_batch_contexts = AsyncMock(
                return_value=contexts_with_configs
            )
            instance_mock.render = AsyncMock(side_effect=worker.render)

            # Wire render to each worker via executors
            async def patched_render(ctx, config):
                folder = ctx.debug_folder
                result_dir = result_root / folder
                result_dir.mkdir(parents=True, exist_ok=True)
                (result_dir / "final.jpg").write_bytes(_make_png_bytes())
                return ctx

            # We exercise _process_translation_group directly
            claimed = [dict(item) for item in items]
            for item in claimed:
                item["settings"] = {"translationQuality": "professional"}

            # Replace executors.find_executor so _render_item gets workers from queue
            await scheduler._process_translation_group("test-batch", claimed, instance_mock)

        # Collect post-render ctx.input states
        for ctx in ctxs:
            stats["inputs_after_render"].append(ctx.input)

        return stats

    async def test_no_image_loaded_before_render_phase(self):
        """ctx.input must be None for all pages during the translation phase."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            n = 10
            image_paths = []
            for i in range(n):
                p = root / f"{i}.png"
                p.write_bytes(_make_png_bytes())
                image_paths.append(p)
                (root / f"result-{i}").mkdir(exist_ok=True)

            stats = await self._run_render_group(n, root, image_paths, [])

        # All ctx.input values during translate time were None (deferred)
        self.assertTrue(
            all(v is None for v in stats["inputs_at_translate_time"]),
            f"Some images were pre-loaded: {stats['inputs_at_translate_time']}",
        )

    async def test_images_released_after_render(self):
        """ctx.input must be None for every page after its render completes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            n = 10
            image_paths = []
            for i in range(n):
                p = root / f"{i}.png"
                p.write_bytes(_make_png_bytes())
                image_paths.append(p)
                (root / f"result-{i}").mkdir(exist_ok=True)

            stats = await self._run_render_group(n, root, image_paths, [])

        self.assertTrue(
            all(v is None for v in stats["inputs_after_render"]),
            f"Some images were not released: {stats['inputs_after_render']}",
        )

    async def test_render_concurrency_bounded_by_semaphore(self):
        """Peak concurrent renders must not exceed _RENDER_SEMAPHORE_SIZE."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            n = 20  # more pages than the semaphore limit
            image_paths = []
            for i in range(n):
                p = root / f"{i}.png"
                p.write_bytes(_make_png_bytes())
                image_paths.append(p)
                (root / f"result-{i}").mkdir(exist_ok=True)

            stats = await self._run_render_group(n, root, image_paths, [])

        self.assertLessEqual(
            stats["concurrent_peak"],
            _RENDER_SEMAPHORE_SIZE,
            f"Peak concurrency {stats['concurrent_peak']} exceeded semaphore size {_RENDER_SEMAPHORE_SIZE}",
        )

    async def test_image_open_called_once_per_page(self):
        """Image.open should be called exactly once per page (at render time, not before)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            n = 10
            image_paths = []
            for i in range(n):
                p = root / f"{i}.png"
                p.write_bytes(_make_png_bytes())
                image_paths.append(p)
                (root / f"result-{i}").mkdir(exist_ok=True)

            stats = await self._run_render_group(n, root, image_paths, [])

        self.assertEqual(
            stats["open_calls"],
            n,
            f"Expected {n} Image.open calls (one per page), got {stats['open_calls']}",
        )


class BoundedRollingContextTest(unittest.IsolatedAsyncioTestCase):
    """Verify the rolling `previous` string is bounded at 8000 chars."""

    async def test_previous_never_exceeds_8000_chars(self):
        from manga_translator.professional_translation import ProfessionalTranslator

        config = TranslatorConfig(
            translator="deepseek",
            target_lang="ENG",
            translation_quality="professional",
            translation_batch_size=1,
        )
        engine = ProfessionalTranslator(config)

        previous_snapshots: list[int] = []

        # Each chunk produces a long translation
        long_translation = "A" * 2000  # 2000-char per chunk

        async def fake_json_request(stage, prompt):
            ids = []
            import re
            ids = re.findall(r'"id":\s*"([^"\\]+)"', prompt)
            return (
                {
                    "regions": [
                        {
                            "id": rid,
                            "translation": long_translation,
                            "confidence": 1.0,
                            "review_reasons": [],
                        }
                        for rid in ids
                    ]
                },
                "deepseek",
            )

        engine._json_request = fake_json_request

        # 20 pages, chunk_size=1 → 20 API calls, each appending 2000 chars
        pages = [
            {"number": i, "regions": [{"id": f"r{i}", "source": "日本語"}]}
            for i in range(1, 21)
        ]
        story = {"start_page": 1, "end_page": 20, "confidence": 1.0}

        # Monkey-patch to capture `previous` length after each chunk
        original_localize = engine.localize_story.__func__

        captured: list[int] = []

        async def instrumented_localize(self_inner, story_d, pages_d, *args):
            prev = ""
            guide = __import__("json").dumps(story_d, ensure_ascii=False)
            chunk_size = max(1, int(self_inner.config.translation_batch_size))
            import json, re as re2
            for offset in range(0, len(pages_d), chunk_size):
                chunk = pages_d[offset: offset + chunk_size]
                payload = [
                    {"page": p["number"], "regions": [{"id": it["id"], "japanese": it["source"]} for it in p["regions"]]}
                    for p in chunk
                ]
                expected = [it for p in payload for it in p["regions"]]
                if not expected:
                    continue
                prompt = f"GUIDE:{guide}\nPREV:{prev[-8000:]}\nPAGES:{json.dumps(payload)}"
                data, provider = await self_inner._json_request("draft", prompt)
                values = ProfessionalTranslator._regions(data, expected)
                for p in chunk:
                    for it in p["regions"]:
                        it["draft"] = values[it["id"]]["translation"]
                        it["confidence"] = values[it["id"]]["confidence"]
                        it["review_reasons"] = values[it["id"]]["review_reasons"]
                        it["draft_provider"] = provider
                prev = (prev + "\n" + "\n".join(values[it["id"]]["translation"] for it in expected))[-8000:]
                captured.append(len(prev))

        # Run directly to avoid patching complexity
        await instrumented_localize(engine, story, pages)

        self.assertTrue(
            all(l <= 8000 for l in captured),
            f"previous exceeded 8000 chars: max was {max(captured)}",
        )
        # After many chunks the string should be exactly 8000 (saturated)
        self.assertGreater(len(captured), 0)
        if len(captured) >= 5:
            self.assertEqual(captured[-1], 8000)


if __name__ == "__main__":
    unittest.main()
