import unittest
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from manga_translator.config import Translator, TranslatorConfig
from manga_translator.professional_translation import (
    ProfessionalTranslator,
    parse_story_ranges,
    translate_professionally,
    _json_object,
)


class ProfessionalTranslationTest(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_prompt_keeps_honorific_example_as_literal_text(self):
        config = TranslatorConfig(translator="deepseek", target_lang="ENG", translation_quality="professional")
        engine = ProfessionalTranslator(config)
        engine._json_request = AsyncMock(return_value=(
            {"stories": [{"start_page": 1, "end_page": 1, "confidence": 1.0}]}, "deepseek"
        ))

        await engine.analyze([{"number": 1, "regions": [{"source": "こんにちは"}]}], None)

        prompt = engine._json_request.await_args.args[1]
        self.assertIn("honorific_policy {default, rules:[{form, strategy, reason}]}", prompt)

    def test_story_range_validation(self):
        self.assertEqual(parse_story_ranges("1-2,3-5", 5), [(1, 2), (3, 5)])
        for invalid in ("1", "0-2", "1-3,3-5", "2-5", "1-6"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_story_ranges(invalid, 5)

    async def test_refusal_routes_whole_request_to_deepseek(self):
        primary = SimpleNamespace(
            parse_args=lambda _: None,
            _request_translation=AsyncMock(return_value="I cannot assist with that content."),
        )
        fallback = SimpleNamespace(
            parse_args=lambda _: None,
            _request_translation=AsyncMock(return_value='{"regions": []}'),
        )
        config = TranslatorConfig(translator="gemini", target_lang="ENG", translation_quality="professional")
        engine = ProfessionalTranslator(config)

        with patch("manga_translator.professional_translation.get_translator",
                   side_effect=lambda key: fallback if key == Translator.deepseek else primary):
            raw, provider = await engine._request("translation", "prompt")

        self.assertEqual(raw, '{"regions": []}')
        self.assertEqual(provider, "deepseek")
        primary._request_translation.assert_awaited_once_with("English", "prompt")
        fallback._request_translation.assert_awaited_once_with("English", "prompt")

    async def test_sequential_result_keeps_final_translation_and_review_data(self):
        region = SimpleNamespace(text="だめ", region_id=None)
        ctx = SimpleNamespace(text_regions=[region], result_documents={}, manual_review_required=False)
        config = SimpleNamespace(
            page_order=1,
            translator=TranslatorConfig(
                translator="deepseek", target_lang="ENG", translation_quality="professional"
            ),
        )

        async def analyze(_self, _pages, _override):
            return {"stories": [{"start_page": 1, "end_page": 1, "confidence": 1.0}]}

        async def localize(_self, _story, pages, *_metadata):
            item = pages[0]["regions"][0]
            item.update(final="Don't...", confidence=0.8, review_reasons=[], translation_provider="deepseek")

        with (
            patch.object(ProfessionalTranslator, "analyze", analyze),
            patch.object(ProfessionalTranslator, "localize_story", localize),
        ):
            result = await translate_professionally([(ctx, config)])

        self.assertEqual(result, [(ctx, config)])
        self.assertEqual(region.translation, "Don't...")
        self.assertFalse(region.review_required)
        audit = ctx.result_documents["professional_translation.json"]
        self.assertEqual(audit["regions"][0]["source"], "だめ")
        self.assertEqual(audit["regions"][0]["final"], "Don't...")

    async def test_professional_requests_chunk_pages_within_story(self):
        config = TranslatorConfig(
            translator="deepseek", target_lang="ENG", translation_quality="professional",
            translation_batch_size=2,
        )
        calls = []
        prompts = []
        events = []

        async def progress(state):
            events.append(state)

        engine = ProfessionalTranslator(config, progress=progress)

        async def request(stage, prompt):
            calls.append(stage)
            prompts.append(prompt)
            ids = re.findall(r'"id":\s*"([^"\\]+)"', prompt)
            return ({"regions": [{"id": item_id, "translation": "ok", "confidence": 1.0, "review_reasons": []} for item_id in ids]}, "deepseek")

        engine._json_request = request
        pages = [{"number": index, "regions": [{"id": f"r{index}", "source": "日本語"}]} for index in range(1, 6)]
        story = {"start_page": 1, "end_page": 5, "confidence": 1.0, "summary": "A continuous story."}
        await engine.localize_story(story, pages)
        self.assertEqual(calls, ["translation", "translation", "translation"])
        self.assertEqual(events, [
            "translating:1/1:1/3", "translating:1/1:2/3", "translating:1/1:3/3",
        ])
        for prompt in prompts:
            self.assertIn("STORY SUMMARY AND LOCALIZATION GUIDE", prompt)
            self.assertIn("A continuous story.", prompt)
        self.assertIn("clear, grammatical, concise, natural English", prompts[0])
        self.assertIn('"id": "r2"', prompts[1])
        self.assertIn('"translation": "ok"', prompts[1])
        self.assertNotIn('"id": "r1"', prompts[2])
        self.assertIn('"id": "r4"', prompts[2])

    def test_json_object_robust_parsing(self):
        # 1. Closed thinking tags
        raw_closed_think = '<think>I should translate this cleanly.</think>{"regions": [{"id": "1", "translation": "ok"}]}'
        self.assertEqual(_json_object(raw_closed_think), {"regions": [{"id": "1", "translation": "ok"}]})

        # 2. Unclosed thinking tags (token cut-off during reasoning)
        raw_unclosed_think = '<think>Still thinking without closing brace or tag'
        with self.assertRaises(ValueError):
            _json_object(raw_unclosed_think)

        # 3. Unclosed markdown code fence
        raw_unclosed_markdown = '```json\n{"regions": [{"id": "1", "translation": "hello"}]}'
        self.assertEqual(_json_object(raw_unclosed_markdown), {"regions": [{"id": "1", "translation": "hello"}]})

        # 4. Partial cut-off JSON (missing outer closing bracket and brace) recovers complete inner objects
        raw_cutoff = '{"regions": [{"id": "r1", "translation": "first", "confidence": 1.0, "review_reasons": []}, {"id": "r2", "trans'
        recovered = _json_object(raw_cutoff)
        self.assertTrue(recovered.get("_partial"))
        self.assertEqual(len(recovered["regions"]), 1)
        self.assertEqual(recovered["regions"][0]["id"], "r1")

        # 5. Trailing comma handling
        raw_trailing = '{"regions": [{"id": "1", "translation": "trailing"},]}'
        self.assertEqual(_json_object(raw_trailing), {"regions": [{"id": "1", "translation": "trailing"}]})

    async def test_json_request_falls_back_to_deepseek_on_invalid_json(self):
        primary = SimpleNamespace(
            parse_args=lambda _: None,
            _request_translation=AsyncMock(return_value="I am sorry, but here is my opinion instead of JSON."),
        )
        deepseek = SimpleNamespace(
            parse_args=lambda _: None,
            _request_translation=AsyncMock(return_value='{"regions": [{"id": "r1", "translation": "recovered", "confidence": 1.0, "review_reasons": []}]}'),
        )
        config = TranslatorConfig(translator="gemini", target_lang="ENG", translation_quality="professional")
        engine = ProfessionalTranslator(config)

        with patch("manga_translator.professional_translation.get_translator",
                   side_effect=lambda key: deepseek if key == Translator.deepseek else primary):
            data, provider = await engine._json_request("translation", "prompt")

        self.assertEqual(provider, "deepseek")
        self.assertEqual(data["regions"][0]["translation"], "recovered")
        self.assertEqual(primary._request_translation.await_count, 2)
        deepseek._request_translation.assert_awaited_once_with("English", "prompt")

    async def test_professional_json_mode_set_and_cleaned_up(self):
        observed_json_mode = []

        class MockTranslator:
            def parse_args(self, _):
                pass

            async def _request_translation(self, _to_lang, _prompt):
                observed_json_mode.append(getattr(self, "_professional_json_mode", False))
                return '{"regions": []}'

        translator = MockTranslator()
        config = TranslatorConfig(translator="chatgpt", target_lang="ENG", translation_quality="professional")
        engine = ProfessionalTranslator(config)

        with patch("manga_translator.professional_translation.get_translator", return_value=translator):
            await engine._request_with(Translator.chatgpt, "prompt")

        self.assertEqual(observed_json_mode, [True])
        self.assertFalse(hasattr(translator, "_professional_json_mode"))

    async def test_legacy_draft_translator_does_not_change_professional_llm(self):
        config = TranslatorConfig(
            translator="chatgpt",
            draft_translator="gemini",
            target_lang="ENG",
            translation_quality="professional",
        )
        engine = ProfessionalTranslator(config)
        self.assertEqual(engine.primary, Translator.chatgpt)

        requested_keys = []

        class MockTranslator:
            def __init__(self, key):
                self.key = key

            def parse_args(self, _):
                pass

            async def _request_translation(self, _to_lang, _prompt):
                requested_keys.append(self.key)
                return '{"regions": []}'

        with patch("manga_translator.professional_translation.get_translator", side_effect=lambda key: MockTranslator(key)):
            await engine._json_request("translation", "translation prompt")
            await engine._json_request("analysis", "analysis prompt")

        self.assertEqual(requested_keys, [Translator.chatgpt, Translator.chatgpt])

    async def test_pure_offline_translator_runs_end_to_end_in_professional_mode(self):
        config = TranslatorConfig(
            translator="sugoi",
            target_lang="ENG",
            translation_quality="professional",
        )
        engine = ProfessionalTranslator(config)
        self.assertEqual(engine.primary, Translator.sugoi)

        pages = [
            {
                "number": 1,
                "regions": [
                    {"id": "r1", "source": "こんにちは", "region": SimpleNamespace(text="こんにちは")},
                    {"id": "r2", "source": "さようなら", "region": SimpleNamespace(text="さようなら")},
                ],
            }
        ]
        analysis = await engine.analyze(pages, None)
        self.assertEqual(analysis["stories"], [{"start_page": 1, "end_page": 1, "confidence": 1.0}])

        dispatched_calls = []

        async def fake_dispatch_one(key, tgt_lang, queries, cfg, use_mtpe, args, device, unload=False):
            dispatched_calls.append((key, tgt_lang, queries))
            if args is not None:
                args["offline_model"] = "SugoiTranslator"
            return ["Hello", "Goodbye"]

        with patch("manga_translator.professional_translation._dispatch_one", side_effect=fake_dispatch_one):
            await engine.localize_story(analysis["stories"][0], pages)

        self.assertEqual(len(dispatched_calls), 1)
        self.assertEqual(pages[0]["regions"][0]["final"], "Hello")
        self.assertEqual(pages[0]["regions"][1]["final"], "Goodbye")

    async def test_legacy_draft_setting_does_not_override_non_llm_primary(self):
        config = TranslatorConfig(
            translator="sugoi",
            draft_translator="deepseek",
            target_lang="ENG",
            translation_quality="professional",
        )
        engine = ProfessionalTranslator(config)
        self.assertEqual(engine.primary, Translator.sugoi)

        pages = [
            {
                "number": 1,
                "regions": [
                    {"id": "r1", "source": "こんにちは", "region": SimpleNamespace(text="こんにちは")},
                    {"id": "r2", "source": "さようなら", "region": SimpleNamespace(text="さようなら")},
                ],
            }
        ]
        story = {"start_page": 1, "end_page": 1, "confidence": 1.0}

        dispatched_calls = []

        async def fake_dispatch_one(key, tgt_lang, queries, cfg, use_mtpe, args, device, unload=False):
            dispatched_calls.append((key, tgt_lang, queries))
            if args is not None:
                args["offline_model"] = "SugoiTranslator"
            return ["Hello", "Goodbye"]

        with patch("manga_translator.professional_translation._dispatch_one", side_effect=fake_dispatch_one):
            await engine.localize_story(story, pages)

        self.assertEqual(len(dispatched_calls), 1)
        self.assertEqual(dispatched_calls[0][0], Translator.sugoi)
        self.assertEqual(dispatched_calls[0][1], "ENG")
        self.assertEqual(dispatched_calls[0][2], ["こんにちは", "さようなら"])

        self.assertEqual(pages[0]["regions"][0]["final"], "Hello")
        self.assertEqual(pages[0]["regions"][0]["translation_provider"], "sugoi")
        self.assertEqual(pages[0]["regions"][1]["final"], "Goodbye")
        self.assertEqual(pages[0]["regions"][1]["translation_provider"], "sugoi")
        self.assertEqual(engine.provenance[-1], {"stage": "translation", "provider": "sugoi", "model": "SugoiTranslator"})

    def test_legacy_draft_translator_setting_is_still_accepted(self):
        config = TranslatorConfig(
            translator="deepseek",
            draft_translator="gemini",
            target_lang="ENG",
            translation_quality="professional",
        )
        engine = ProfessionalTranslator(config)
        self.assertEqual(config.draft_translator, Translator.gemini)
        self.assertEqual(engine.primary, Translator.deepseek)


    async def test_panel_context_in_translation_and_analysis_prompts(self):
        config = TranslatorConfig(
            translator="deepseek",
            target_lang="ENG",
            translation_quality="professional",
        )
        engine = ProfessionalTranslator(config)
        recorded_prompts = {}

        async def fake_request(stage, prompt):
            recorded_prompts[stage] = prompt
            return ({"stories": [{"start_page": 1, "end_page": 1, "confidence": 1.0}]}, "deepseek")

        engine._json_request = fake_request

        pages = [
            {
                "number": 1,
                "panels": [{"xyxy": [0, 0, 100, 100], "order": 1}],
                "regions": [
                    {"id": "r1", "panel_index": 0, "source": "こんにちは"},
                    {"id": "r2", "source": "ナレーション"},
                ],
            }
        ]

        await engine.analyze(pages, None)
        self.assertIn("[PANEL p1_01 | PANEL ORDER 1]", recorded_prompts["analysis"])
        self.assertIn("[r1 | estimated order 1]\nこんにちは", recorded_prompts["analysis"])
        self.assertIn("[UNASSIGNED REGIONS]", recorded_prompts["analysis"])
        self.assertIn("[r2 | estimated order 1]\nナレーション", recorded_prompts["analysis"])

        story = {"start_page": 1, "end_page": 1, "confidence": 1.0}
        async def fake_translation_request(stage, prompt):
            recorded_prompts[stage] = prompt
            return ({"regions": [{"id": "r1", "translation": "Hello", "confidence": 1.0, "review_reasons": []}, {"id": "r2", "translation": "Narration", "confidence": 1.0, "review_reasons": []}]}, "deepseek")

        engine._json_request = fake_translation_request
        await engine.localize_story(story, pages)
        self.assertIn('"panel_id": "p1_01"', recorded_prompts["translation"])
        self.assertIn('"japanese": "こんにちは"', recorded_prompts["translation"])
        self.assertIn('"unassigned_regions"', recorded_prompts["translation"])


if __name__ == "__main__":
    unittest.main()
