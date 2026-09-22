import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server.manga_summary import (
    _summary_completion,
    chunk_transcript,
    dismiss_summary_job,
    generate_synopsis,
    get_summary_chunk_limit,
    group_pages,
    load_summary,
    region_texts,
    rename_summary,
    save_summary,
    source_snapshot,
    synopsis_status,
    list_summary_jobs,
    update_summary_job,
    resolve_summary_model,
    summary_error_details,
    is_page_text_extracted,
    DEFAULT_SUMMARY_CHUNK_LIMITS,
)


class TestMangaSummary(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="manga_summary_"))
        for folder, name, text in (
            ("page_10", "10.png", "last"),
            ("page_2", "2.png", "middle"),
        ):
            page = self.root / folder
            page.mkdir()
            (page / "final.png").write_bytes(b"png")
            (page / "input.png").write_bytes(b"png")
            (page / "meta.json").write_text(json.dumps({"mangaTitle": "Series", "originalName": name}), encoding="utf-8")
            (page / "text_regions.json").write_text(json.dumps([{"original_text": text}]), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_pages_are_naturally_ordered_and_fingerprinted(self):
        pages = group_pages(self.root, "Series")
        self.assertEqual([page["name"] for page in pages], ["2.png", "10.png"])
        snapshot = source_snapshot(pages)
        self.assertEqual(snapshot["texts"], ["middle", "last"])
        self.assertFalse(snapshot["missing"])

    def test_pages_follow_persistent_order_before_filename(self):
        for folder, order in (("page_10", 1), ("page_2", 2)):
            meta_path = self.root / folder / "meta.json"
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            metadata["pageOrder"] = order
            meta_path.write_text(json.dumps(metadata), encoding="utf-8")

        self.assertEqual([page["name"] for page in group_pages(self.root, "Series")], ["10.png", "2.png"])

    def test_stale_status_and_rename(self):
        pages = group_pages(self.root, "Series")
        fingerprint = source_snapshot(pages)["fingerprint"]
        save_summary(self.root, "Series", {"mangaTitle": "Series", "summary": "A plot", "sourceFingerprint": fingerprint})
        self.assertFalse(synopsis_status(self.root, "Series", pages)["stale"])
        (self.root / "page_2" / "text_regions.json").write_text(json.dumps([{"original_text": "changed"}]), encoding="utf-8")
        self.assertTrue(synopsis_status(self.root, "Series", pages)["stale"])
        rename_summary(self.root, "Series", "Renamed")
        self.assertEqual(synopsis_status(self.root, "Renamed")["summary"], "A plot")

    def test_summary_without_source_fingerprint_is_not_stale(self):
        pages = group_pages(self.root, "Series")
        save_summary(self.root, "Series", {"mangaTitle": "Series", "summary": "A plot"})
        self.assertFalse(synopsis_status(self.root, "Series", pages)["stale"])

    def test_fingerprint_is_stable_when_folder_or_extraction_flag_changes(self):
        pages1 = [
            {"folder": "folder_a", "name": "1.png", "textRegions": [{"original_text": "hello"}]},
            {"folder": "folder_b", "name": "2.png", "textRegions": []},
        ]
        pages2 = [
            {"folder": "diff_folder_a", "name": "1.png", "textRegions": [{"original_text": "hello"}]},
            {"folder": "diff_folder_b", "name": "2.png", "textRegions": []},
        ]
        self.assertEqual(source_snapshot(pages1)["fingerprint"], source_snapshot(pages2)["fingerprint"])

    def test_chunking_keeps_boundaries(self):
        chunks = chunk_transcript(["[Page 1]\none", "[Page 2]\ntwo", "[Page 3]\nthree"], lambda value: len(value), limit=18)
        self.assertEqual(chunks, ["[Page 1]\none", "[Page 2]\ntwo", "[Page 3]\nthree"])

    def test_region_texts_accepts_pipeline_ocr_fields(self):
        self.assertEqual(region_texts([{"text_raw": "OCR text"}]), ["OCR text"])

    def test_region_texts_ignores_current_text_without_original_text(self):
        self.assertEqual(region_texts([{"text": "translated text", "translation": "translated text"}]), [])

    def test_is_page_text_extracted_for_original_and_translated_manga(self):
        # 1. Translated manga page with empty text is extracted if group has text (e.g. cover page)
        translated_cover = {
            "meta": {"sourceType": "translated"},
            "textRegions": [],
        }
        self.assertTrue(is_page_text_extracted(translated_cover, has_group_text=True))
        # But if the whole group has no text, it needs extraction
        self.assertFalse(is_page_text_extracted(translated_cover, has_group_text=False))

        # 2. Original manga page without text_regions.json and empty textRegions is NOT extracted
        raw_original_page = {
            "meta": {"sourceType": "original"},
            "textRegions": [],
            "path": self.root / "non_existent_folder",
        }
        self.assertFalse(is_page_text_extracted(raw_original_page, has_group_text=True))
        self.assertFalse(is_page_text_extracted(raw_original_page, has_group_text=False))

        # 3. Original manga page with non-empty textRegions is extracted
        ocr_extracted_page = {
            "meta": {"sourceType": "original"},
            "textRegions": [{"original_text": "Hello"}],
            "path": self.root / "non_existent_folder",
        }
        self.assertTrue(is_page_text_extracted(ocr_extracted_page))

        # 4. Original manga page with text_regions.json on disk is extracted via disk fallback
        disk_page = {
            "meta": {"sourceType": "original"},
            "textRegions": [],
            "path": self.root / "page_2",
        }
        self.assertTrue(is_page_text_extracted(disk_page))

    def test_summary_error_details_include_request_and_transport_cause(self):
        cause = ConnectionError("TLS handshake failed")
        error = RuntimeError("Connection error.")
        error.request = SimpleNamespace(
            method="POST",
            url="https://api.groq.com/openai/v1/chat/completions?debug=1",
        )
        error.__cause__ = cause

        detail = summary_error_details(error)

        self.assertIn("request=POST https://api.groq.com/openai/v1/chat/completions", detail)
        self.assertIn("ConnectionError: TLS handshake failed", detail)
        self.assertNotIn("debug=1", detail)

    def test_summary_job_status_is_persisted_with_manga(self):
        update_summary_job(self.root, "Series", "generating", provider="deepseek", model="deepseek-reasoner")
        self.assertEqual(synopsis_status(self.root, "Series")["jobStatus"], "generating")
        self.assertEqual(synopsis_status(self.root, "Series")["model"], "deepseek-reasoner")

        update_summary_job(
            self.root,
            "Series",
            "generating",
            stage="ocr",
            progress=42,
            message="Reading page 2 of 5",
        )
        status = synopsis_status(self.root, "Series")
        self.assertEqual(status["jobStage"], "ocr")
        self.assertEqual(status["jobProgress"], 42)
        self.assertEqual(status["jobMessage"], "Reading page 2 of 5")

        update_summary_job(self.root, "Series", "error", "Provider unavailable")
        status = synopsis_status(self.root, "Series")
        self.assertEqual(status["jobStatus"], "error")
        self.assertEqual(status["jobError"], "Provider unavailable")

    def test_cached_pages_and_dismissible_job_listing(self):
        status = synopsis_status(self.root, "Series")
        self.assertFalse(status["missingPages"])
        update_summary_job(
            self.root,
            "Series",
            "generating",
            stage="concatenating",
            progress=70,
            current_page=2,
            page_count=2,
            pages_with_text=2,
            extraction_required=False,
        )
        jobs = list_summary_jobs(self.root)
        self.assertEqual(jobs[0]["jobStage"], "concatenating")
        self.assertFalse(jobs[0]["jobExtractionRequired"])
        dismiss_summary_job(self.root, "Series")
        self.assertEqual(list_summary_jobs(self.root), [])

    def test_queued_job_is_listed_before_generation(self):
        update_summary_job(self.root, "Series", "queued", message="Waiting for an available worker")
        jobs = list_summary_jobs(self.root)
        self.assertEqual(jobs[0]["status"], "queued")
        self.assertEqual(jobs[0]["jobMessage"], "Waiting for an available worker")

    def test_queued_job_with_existing_summary_is_reconciled_and_not_runnable(self):
        save_summary(
            self.root,
            "Series",
            {
                "mangaTitle": "Series",
                "summary": "Existing summary content",
                "jobStatus": "queued",
                "jobMessage": "Waiting for an available worker",
            },
        )
        from server.manga_summary import list_runnable_summary_jobs, reconcile_summary_jobs
        runnable = list_runnable_summary_jobs(self.root)
        self.assertEqual(runnable, [])

        reconcile_summary_jobs(self.root)
        saved = load_summary(self.root, "Series")
        self.assertEqual(saved["jobStatus"], "ready")
        self.assertEqual(saved["jobStage"], "complete")
        self.assertEqual(saved["jobProgress"], 100)


class TestMangaSummaryPerformance(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_synopsis_keeps_response_content_visible(self):
        calls = []

        class Completions:
            async def create(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))]
                )

        client = SimpleNamespace(
            with_options=lambda **_kwargs: SimpleNamespace(
                chat=SimpleNamespace(completions=Completions())
            )
        )

        self.assertEqual(
            await _summary_completion(client, "ENG", "text", False, "deepseek-flash", "deepseek"),
            "summary",
        )
        self.assertEqual(calls[0]["extra_body"], {"thinking": {"type": "disabled"}})

        prompt = calls[0]["messages"][0]["content"]
        for expected in (
            "OVERVIEW",
            "SETTING AND PREMISE",
            "KEY CHARACTERS AND RELATIONSHIPS",
            "ENDING AND UNRESOLVED THREADS",
            "ENTITIES AND CONCEPTS",
            "COLLECTION OVERVIEW",
            "never exceed 3,000 words",
            "do not quote dialogue or narrate speaker-by-speaker exchanges",
            "Do not treat scene changes, flashbacks, or time skips as story boundaries",
            "silently remove duplicated events and paragraphs",
        ):
            self.assertIn(expected, prompt)

    async def test_merge_prompt_deduplicates_without_joining_unrelated_stories(self):
        calls = []

        class Completions:
            async def create(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))]
                )

        client = SimpleNamespace(
            with_options=lambda **_kwargs: SimpleNamespace(
                chat=SimpleNamespace(completions=Completions())
            )
        )

        await _summary_completion(client, "ENG", "partials", True, "gemini-test", "gemini")

        prompt = calls[0]["messages"][0]["content"]
        self.assertIn("Deduplicate overlapping events", prompt)
        self.assertIn("never combine unrelated stories or invent continuity", prompt)
        self.assertIn("keep every detail attached to the correct story", prompt)

    async def test_summary_completion_handles_missing_response_message(self):
        class Completions:
            async def create(self, **_kwargs):
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=None, finish_reason="SAFETY")]
                )

        client = SimpleNamespace(
            with_options=lambda **_kwargs: SimpleNamespace(
                chat=SimpleNamespace(completions=Completions())
            )
        )

        with self.assertRaisesRegex(RuntimeError, "Gemini returned no text"):
            await _summary_completion(client, "ENG", "text", False, "gemini-test", "gemini")

    async def test_summary_chunks_are_generated_concurrently(self):
        started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        max_active = 0
        models = []

        class Completions:
            async def create(self, **kwargs):
                nonlocal active, max_active
                models.append(kwargs["model"])
                active += 1
                max_active = max(max_active, active)
                if active == 2:
                    started.set()
                await release.wait()
                active -= 1
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="partial"))]
                )

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions()),
            with_options=lambda **_kwargs: client,
            close=lambda: None,
        )

        fake_openai = SimpleNamespace(AsyncOpenAI=lambda **_kwargs: client)
        with patch("server.manga_summary.openai", fake_openai):
            task = asyncio.create_task(
                generate_synopsis(["a" * 2000, "b" * 2000, "c" * 2000], "ENG", len, "deepseek-flash", chunk_limit=2500)
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            release.set()
            result = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(result, "partial")
        self.assertGreaterEqual(max_active, 2)
        self.assertEqual(set(models), {"deepseek-flash"})

    async def test_synopsis_uses_groq_and_gemini_endpoints(self):
        from manga_translator.translators import keys

        calls = []
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))]
        )

        class Completions:
            async def create(self, **_kwargs):
                return response

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions()),
            with_options=lambda **_kwargs: client,
            close=lambda: None,
        )

        def make_client(**kwargs):
            calls.append(kwargs)
            return client

        with patch.object(keys, "GROQ_MODEL", "groq-test"), patch.object(keys, "GROQ_API_KEY", "groq-key"), \
             patch.object(keys, "GEMINI_MODEL", "gemini-test"), patch.object(keys, "GEMINI_API_KEY", "gemini-key"), \
             patch("server.manga_summary.openai", SimpleNamespace(AsyncOpenAI=make_client)):
            self.assertEqual(resolve_summary_model("groq")[:2], ("groq", "groq-test"))
            self.assertEqual(resolve_summary_model("gemini")[:2], ("gemini", "gemini-test"))
            self.assertEqual(await generate_synopsis(["text"], "ENG", len, "groq"), "summary")
            self.assertEqual(await generate_synopsis(["text"], "ENG", len, "gemini"), "summary")

        self.assertEqual(calls, [
            {
                "api_key": "groq-key",
                "base_url": "https://api.groq.com/openai/v1",
                "max_retries": 0,
            },
            {
                "api_key": "gemini-key",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "max_retries": 0,
            },
        ])

    def test_legacy_deepseek_synopsis_models_use_current_model(self):
        self.assertEqual(resolve_summary_model("deepseek-chat")[1], "deepseek-flash")
        self.assertEqual(resolve_summary_model("deepseek-reasoner")[1], "deepseek-flash")

    def test_provider_specific_chunk_limits(self):
        self.assertEqual(get_summary_chunk_limit("deepseek"), 64_000)
        self.assertEqual(get_summary_chunk_limit("gemini"), 120_000)
        self.assertEqual(get_summary_chunk_limit("groq"), 3_500)
        self.assertEqual(get_summary_chunk_limit("unknown"), 3_500)

    def test_provider_chunk_limits_env_overrides(self):
        import os
        with patch.dict(os.environ, {
            "SUMMARY_CHUNK_LIMIT_DEEPSEEK": "50000",
            "SUMMARY_CHUNK_LIMIT_GEMINI": "80000",
            "SUMMARY_CHUNK_LIMIT_GROQ": "4000",
            "SUMMARY_CHUNK_LIMIT_DEFAULT": "5000",
        }):
            self.assertEqual(get_summary_chunk_limit("deepseek"), 50_000)
            self.assertEqual(get_summary_chunk_limit("gemini"), 80_000)
            self.assertEqual(get_summary_chunk_limit("groq"), 4_000)
            self.assertEqual(get_summary_chunk_limit("other"), 5_000)


class TestSummaryTokenizerCache(unittest.TestCase):
    def test_deepseek_tokenizer_is_loaded_once(self):
        from server import main

        factory = main._deepseek_token_count_factory
        factory.cache_clear()
        fake_counter = SimpleNamespace(count_tokens=lambda text: len(text))
        with patch(
            "manga_translator.translators.tokenizers.token_counters.deepseekTokenCounter",
            return_value=fake_counter,
        ) as constructor:
            factory()
            factory()

        self.assertEqual(constructor.call_count, 1)
        factory.cache_clear()


class TestSummaryExtractionProgress(unittest.IsolatedAsyncioTestCase):
    async def test_translator_progress_events_are_forwarded(self):
        from server import main

        page_path = Path(tempfile.mkdtemp(prefix="summary_page_"))
        try:
            (page_path / "input.png").write_bytes(b"png")
            events = []

            async def record(stage):
                events.append(stage)

            async def fake_get_ctx(_request, _config, _image, on_progress=None):
                for stage in ("detection", "ocr", "textline_merge"):
                    on_progress(stage)
                return SimpleNamespace(text_regions=[])

            page = {"path": page_path, "name": "page.png", "id": "page-1"}
            with patch.object(main, "get_ctx", fake_get_ctx), patch.object(main, "_postgres", return_value=None):
                await main._run_summary_ocr(None, page, "ENG", record)

            self.assertEqual(events, ["detection", "ocr", "textline_merge"])
        finally:
            shutil.rmtree(page_path, ignore_errors=True)

    async def test_original_summary_ocr_uses_pre_translation_pipeline(self):
        from PIL import Image
        from server import main

        page_path = Path(tempfile.mkdtemp(prefix="summary_original_page_"))
        try:
            Image.new("RGB", (2, 2), "white").save(page_path / "input.png")
            calls = []

            class Worker:
                async def extract_text(self, _image, config):
                    calls.append((config.translator.target_lang, config.translator.no_text_lang_skip))
                    return SimpleNamespace(
                        text_regions=[
                            SimpleNamespace(
                                xywh=[0, 0, 2, 2],
                                lines=[],
                                text="Hello",
                                font_size=24,
                                angle=0,
                            )
                        ]
                    )

                async def sent(self, *_args):
                    raise AssertionError("summary OCR should stop before translation")

            page = {
                "path": page_path,
                "name": "page.png",
                "id": "page-1",
                "meta": {"sourceType": "original"},
            }
            with patch.object(main, "_postgres", return_value=None):
                regions = await main._run_summary_ocr(
                    None, page, "JPN", worker=Worker()
                )

            self.assertEqual(calls, [("ENG", True)])
            self.assertEqual(regions[0]["original_text"], "Hello")
        finally:
            shutil.rmtree(page_path, ignore_errors=True)

    async def test_regenerate_reuses_cached_ocr_per_page(self):
        from server import main

        root = Path(tempfile.mkdtemp(prefix="summary_regenerate_"))
        original_root = main.RESULT_ROOT
        calls = []
        try:
            for folder in ("page-1", "page-2"):
                page = root / folder
                page.mkdir()
                (page / "final.png").write_bytes(b"png")
                (page / "meta.json").write_text(
                    json.dumps({"mangaTitle": "Series", "originalName": "page.png"}),
                    encoding="utf-8",
                )
            (root / "page-1" / "text_regions.json").write_text(
                json.dumps([{"original_text": "cached"}]), encoding="utf-8"
            )

            async def fake_ocr(_request, page, *_args, **_kwargs):
                calls.append(page["folder"])
                return [{"original_text": "fresh"}]

            with patch.object(main, "RESULT_ROOT", root), \
                 patch.object(main, "_postgres", return_value=None), \
                 patch.object(main, "_run_summary_ocr", fake_ocr), \
                 patch.object(main, "generate_synopsis", AsyncMock(return_value="summary")):
                await main._generate_manga_summary(
                    None,
                    main.MangaSummaryRequest(mangaTitle="Series", regenerate=True),
                    object(),
                )

            self.assertEqual(calls, ["page-2"])
        finally:
            main.RESULT_ROOT = original_root
            shutil.rmtree(root, ignore_errors=True)

    async def test_refresh_text_replaces_cached_ocr_and_summary(self):
        from PIL import Image
        from server import main

        root = Path(tempfile.mkdtemp(prefix="summary_refresh_"))
        original_root = main.RESULT_ROOT
        calls = []
        try:
            page = root / "page-1"
            page.mkdir()
            Image.new("RGB", (2, 2), "white").save(page / "input.png")
            (page / "final.png").write_bytes(b"png")
            (page / "meta.json").write_text(
                json.dumps({
                    "mangaTitle": "Series",
                    "originalName": "page.png",
                    "sourceType": "original",
                }),
                encoding="utf-8",
            )
            (page / "text_regions.json").write_text(
                json.dumps([{"original_text": "old"}]), encoding="utf-8"
            )
            second_page = root / "page-2"
            second_page.mkdir()
            Image.new("RGB", (2, 2), "white").save(second_page / "input.png")
            (second_page / "final.png").write_bytes(b"png")
            (second_page / "meta.json").write_text(
                json.dumps({
                    "mangaTitle": "Series",
                    "originalName": "page-2.png",
                    "sourceType": "original",
                }),
                encoding="utf-8",
            )
            (second_page / "text_regions.json").write_text(
                json.dumps([{"original_text": "old 2"}]), encoding="utf-8"
            )

            class Worker:
                async def extract_text(self, *_args):
                    calls.append(True)
                    return SimpleNamespace(
                        text_regions=[
                            SimpleNamespace(
                                xywh=[0, 0, 2, 2],
                                lines=[],
                                text="fresh",
                                font_size=24,
                                angle=0,
                            )
                        ]
                    )

            with patch.object(main, "RESULT_ROOT", root), \
                 patch.object(main, "_postgres", return_value=None), \
                 patch.object(main, "generate_synopsis", AsyncMock(return_value="summary")):
                await main._generate_manga_summary(
                    None,
                    main.MangaSummaryRequest(
                        mangaTitle="Series", refreshText=True
                    ),
                    Worker(),
                )

            saved_regions = json.loads((page / "text_regions.json").read_text(encoding="utf-8"))
            self.assertEqual(calls, [True, True])
            self.assertEqual(saved_regions[0]["original_text"], "fresh")
            self.assertEqual(load_summary(root, "Series")["summary"], "summary")
        finally:
            main.RESULT_ROOT = original_root
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
