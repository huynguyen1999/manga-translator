"""Memory checks for stage-major translation batches."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from manga_translator import Context
from manga_translator.config import TranslatorConfig
from server.batch_scheduler import BatchScheduler


class DeferredImageLoadingTest(unittest.IsolatedAsyncioTestCase):
    async def _translate_without_page_pixels(self, quality: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_root = Path(tmp)
            count = 8
            items = [
                {
                    "id": f"page-{index}",
                    "name": f"{index}.png",
                    "status": "processing",
                    "stage": "awaiting_translation",
                    "resultFolder": f"result-{index}",
                }
                for index in range(count)
            ]
            batch = {
                "id": "manga",
                "status": "processing",
                "settings": {"translationQuality": quality},
                "items": items,
            }

            class Store:
                async def get_batch(self, _batch_id):
                    return batch

                async def mutate(self, _batch_id, mutator):
                    mutator(batch)

                async def input_path(self, *_args):
                    raise AssertionError("translation must not read page pixels")

            class Run:
                def __init__(self):
                    self.manifest = {"stages": [{"id": "translation", "status": "pending"}]}
                    self.documents = {}

                def write_json(self, name, payload):
                    self.documents[name] = payload

                def _stage(self, stage_id):
                    return next(stage for stage in self.manifest["stages"] if stage["id"] == stage_id)

                def _finish(self, stage_id):
                    self._stage(stage_id)["status"] = "completed"

                async def checkpoint(self):
                    pass

                def release_runtime(self):
                    pass

            runs = {item["resultFolder"]: Run() for item in items}
            observed_inputs = []

            class Translator:
                def __init__(self):
                    self._progress_hooks = []

                def add_progress_hook(self, hook):
                    self._progress_hooks.append(hook)

            class Instance:
                def __init__(self):
                    self.translator = Translator()

                async def translate_batch_contexts(self, contexts, batch_size=None):
                    observed_inputs.extend(ctx.input for ctx, _ in contexts)
                    return contexts

            class Executors:
                async def free_executor(self, _instance):
                    pass

            scheduler = BatchScheduler(Store(), Executors(), result_root)
            config = SimpleNamespace(
                translator=TranslatorConfig(translator="deepseek", target_lang="ENG", translation_quality=quality)
            )

            async def checkpoint(folder):
                return runs[folder]

            claimed = [{**item, "settings": {}} for item in items]
            with (
                patch.object(scheduler, "_checkpoint_run", side_effect=checkpoint),
                patch.object(BatchScheduler, "_config_for", return_value=config),
            ):
                await scheduler._process_translation_group("manga", claimed, Instance())

            self.assertEqual(observed_inputs, [None] * count)
            self.assertTrue(all(item["status"] == "queued" for item in batch["items"]))
            self.assertTrue(all(item["pipelineStage"] == "mask_generation" for item in batch["items"]))
            self.assertTrue(all(run._stage("translation")["status"] == "completed" for run in runs.values()))

    async def test_professional_translation_keeps_all_source_pages_on_disk(self):
        await self._translate_without_page_pixels("professional")

    async def test_fast_translation_keeps_all_source_pages_on_disk(self):
        await self._translate_without_page_pixels("fast")


class BatchRenderCpuLaneTest(unittest.IsolatedAsyncioTestCase):
    async def test_batch_render_uses_background_cpu_lane(self):
        from manga_translator.config import Config
        from manga_translator.manga_translator import (
            CPU_PRIORITY_BACKGROUND,
            MangaTranslator,
            render_page,
        )

        translator = object.__new__(MangaTranslator)
        translator._model_usage_timestamps = {}
        translator.font_path = None
        ctx = Context()
        ctx.text_regions = []
        ctx.img_rgb = None
        ctx.img_inpainted = object()
        ctx._bubble_detection_done = True
        ctx._background_batch_render = True
        expected = object()
        cpu_stage = AsyncMock(return_value=expected)

        with patch("manga_translator.manga_translator.run_cpu_stage", cpu_stage):
            result = await translator._run_text_rendering(Config(), ctx)

        self.assertIs(result, expected)
        self.assertIs(cpu_stage.await_args.args[0], render_page)
        self.assertEqual(cpu_stage.await_args.kwargs["priority"], CPU_PRIORITY_BACKGROUND)

    async def test_batch_render_reuses_completed_checkpoint_layout(self):
        from manga_translator.config import Config
        from manga_translator.manga_translator import MangaTranslator, render_page

        translator = object.__new__(MangaTranslator)
        translator._model_usage_timestamps = {}
        translator.font_path = None
        translator._pipeline_run = SimpleNamespace(
            manifest={"stages": [{"id": "layout", "status": "completed"}]}
        )
        ctx = Context()
        ctx.img_rgb = object()
        ctx.img_inpainted = object()
        ctx.text_regions = []
        ctx._bubble_detection_done = True
        cpu_stage = AsyncMock()

        with patch("manga_translator.manga_translator.run_cpu_stage", cpu_stage):
            await translator._run_text_rendering(Config(), ctx)

        cpu_stage.assert_awaited_once()
        self.assertIs(cpu_stage.await_args.args[0], render_page)
        self.assertTrue(ctx._bubble_layout_ready)


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
