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
            raw, provider = await engine._request("draft", "prompt")

        self.assertEqual(raw, '{"regions": []}')
        self.assertEqual(provider, "deepseek")
        primary._request_translation.assert_awaited_once_with("English", "prompt")
        fallback._request_translation.assert_awaited_once_with("English", "prompt")

    async def test_sequential_result_keeps_draft_final_and_review_data(self):
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
            item.update(draft="No...", confidence=0.9, review_reasons=[], draft_provider="deepseek")

        async def edit(_self, _story, pages, *_metadata):
            item = pages[0]["regions"][0]
            item.update(final="Don't...", confidence=0.8, editor_provider="deepseek")

        with (
            patch.object(ProfessionalTranslator, "analyze", analyze),
            patch.object(ProfessionalTranslator, "localize_story", localize),
            patch.object(ProfessionalTranslator, "edit_story", edit),
        ):
            result = await translate_professionally([(ctx, config)])

        self.assertEqual(result, [(ctx, config)])
        self.assertEqual(region.translation, "Don't...")
        self.assertFalse(region.review_required)
        audit = ctx.result_documents["professional_translation.json"]
        self.assertEqual(audit["regions"][0]["source"], "だめ")
        self.assertEqual(audit["regions"][0]["draft"], "No...")
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
        story = {"start_page": 1, "end_page": 5, "confidence": 1.0}
        await engine.localize_story(story, pages)
        await engine.edit_story(story, pages)
        self.assertEqual(calls, ["draft", "draft", "draft", "editing", "editing", "editing"])
        self.assertEqual(events, [
            "drafting:1/1:1/3", "drafting:1/1:2/3", "drafting:1/1:3/3",
            "editing:1/1:1/3", "editing:1/1:2/3", "editing:1/1:3/3",
        ])
        self.assertIn("working draft for a separate senior editor", prompts[0])
        self.assertNotIn("natural, idiomatic English that reads as human localization", prompts[0])
        self.assertIn("Independently compare the Japanese source and the first draft", prompts[-1])

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
            data, provider = await engine._json_request("draft", "prompt")

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


if __name__ == "__main__":
    unittest.main()
