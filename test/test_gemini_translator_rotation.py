import asyncio
import importlib.util
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure repo root is on sys.path
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import types
_mt_pkg = types.ModuleType("manga_translator")
_mt_pkg.__path__ = [os.path.abspath(os.path.join(_repo_root, "manga_translator"))]
sys.modules["manga_translator"] = _mt_pkg

_trans_pkg = types.ModuleType("manga_translator.translators")
_trans_pkg.__path__ = [os.path.abspath(os.path.join(_repo_root, "manga_translator", "translators"))]
sys.modules["manga_translator.translators"] = _trans_pkg

for _pkg in ["langcodes", "py3langid", "einops"]:
    if _pkg not in sys.modules:
        try:
            __import__(_pkg)
        except ImportError:
            sys.modules[_pkg] = MagicMock()

# Import translators modules cleanly via importlib
_file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "manga_translator", "translators", "gemini_keys.py"))
_spec = importlib.util.spec_from_file_location("gemini_keys", _file_path)
gemini_keys = importlib.util.module_from_spec(_spec)
sys.modules["gemini_keys"] = gemini_keys
_spec.loader.exec_module(gemini_keys)

GeminiKeyManager = gemini_keys.GeminiKeyManager
mask_key = gemini_keys.mask_key
parse_gemini_keys = gemini_keys.parse_gemini_keys


class TestGeminiTranslatorRotationIntegration(unittest.TestCase):
    def test_keys_export(self):
        with patch.dict(os.environ, {"GEMINI_API_KEYS": "key_alpha, key_beta", "GEMINI_API_KEY": "key_alpha"}):
            parsed = parse_gemini_keys([os.getenv("GEMINI_API_KEYS"), os.getenv("GEMINI_API_KEY")])
            self.assertEqual(parsed, ["key_alpha", "key_beta"])
            primary = parsed[0]
            self.assertEqual(primary, "key_alpha")

    def test_mocked_gemini_rotation_flow(self):
        km = GeminiKeyManager(["key1", "key2", "key3"], default_cooldown=60.0)

        # Mock generate_content response
        mock_response_1 = MagicMock()
        mock_response_1.text = "Translation 1"
        mock_response_1.usage_metadata.prompt_token_count = 10
        mock_response_1.usage_metadata.total_token_count = 20

        mock_response_2 = MagicMock()
        mock_response_2.text = "Translation 2"
        mock_response_2.usage_metadata.prompt_token_count = 12
        mock_response_2.usage_metadata.total_token_count = 24

        call_keys = []

        class RateLimitError(Exception):
            code = 429
            message = "Resource exhausted"

        async def simulated_translate(key, client):
            call_keys.append(key)
            if key == "key1":
                raise RateLimitError("429 RESOURCE_EXHAUSTED")
            return "translated text"

        res = asyncio.run(km.execute_with_retry(simulated_translate))
        self.assertEqual(res, "translated text")
        self.assertEqual(call_keys, ["key1", "key2"])
        self.assertTrue(km._key_map["key1"].is_cooling_down())
        self.assertFalse(km._key_map["key2"].is_cooling_down())

        # Next call should use key3 (round robin past key2, skipping cooling key1)
        next_key = km.get_next_key()
        self.assertEqual(next_key, "key3")

    def test_request_path_does_not_retry_rate_limited_key(self):
        from manga_translator.translators.gemini import GeminiTranslator
        from manga_translator.translators.gemini_keys import (
            GeminiRequestBudget,
            GeminiRetryExhausted,
            GeminiKeyManager as PackageGeminiKeyManager,
            _CURRENT_RETRY_BUDGET,
        )

        translator = GeminiTranslator.__new__(GeminiTranslator)
        translator.logger = MagicMock()
        translator.key_manager = PackageGeminiKeyManager(["key1"], models=["model1"])
        translator._build_chat_request_payload = MagicMock(return_value=({}, []))
        translator._extract_response_text_and_usage = lambda response: ""

        class RateLimitError(Exception):
            code = 429

        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            side_effect=RateLimitError("429 RESOURCE_EXHAUSTED")
        )
        translator.key_manager.get_genai_client = lambda key: client

        budget = GeminiRequestBudget()
        token = _CURRENT_RETRY_BUDGET.set(budget)
        try:
            with self.assertRaises(GeminiRetryExhausted):
                asyncio.run(translator._request_translation("ENG", "prompt"))
        finally:
            _CURRENT_RETRY_BUDGET.reset(token)

        self.assertEqual(client.aio.models.generate_content.await_count, 1)
        self.assertEqual(budget.used, 1)

    def test_request_path_stops_at_four_requests(self):
        from manga_translator.translators.gemini import GeminiTranslator
        from manga_translator.translators.gemini_keys import (
            GeminiRequestBudget,
            GeminiRetryExhausted,
            GeminiKeyManager as PackageGeminiKeyManager,
            _CURRENT_RETRY_BUDGET,
        )

        translator = GeminiTranslator.__new__(GeminiTranslator)
        translator.logger = MagicMock()
        translator.key_manager = PackageGeminiKeyManager(
            ["key1", "key2", "key3", "key4", "key5"], models=["model1"]
        )
        translator._build_chat_request_payload = MagicMock(return_value=({}, []))
        translator._extract_response_text_and_usage = lambda response: ""

        class RateLimitError(Exception):
            code = 429

        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            side_effect=RateLimitError("429 RESOURCE_EXHAUSTED")
        )
        translator.key_manager.get_genai_client = lambda key: client

        budget = GeminiRequestBudget(limit=4)
        token = _CURRENT_RETRY_BUDGET.set(budget)
        try:
            with self.assertRaises(GeminiRetryExhausted):
                asyncio.run(translator._request_translation("ENG", "prompt"))
        finally:
            _CURRENT_RETRY_BUDGET.reset(token)

        self.assertEqual(client.aio.models.generate_content.await_count, 4)
        self.assertEqual(budget.used, 4)

    def test_none_response_text_safety(self):
        # Verify that response with None text does not raise TypeError
        mock_response = MagicMock()
        mock_response.text = None
        mock_response.usage_metadata = None
        mock_response.candidates = []

        resp_text = getattr(mock_response, 'text', None)
        resp_str = resp_text if resp_text is not None else ""
        self.assertEqual(resp_str, "")
        # String concatenation should be safe
        log_text = f"-- GPT Response --\n{resp_str}"
        self.assertIn("-- GPT Response --", log_text)

    def test_models_export_and_defaults(self):
        # Test without GEMINI_MODELS
        with patch("dotenv.load_dotenv"), patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"langcodes": MagicMock()}):
            _keys_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "manga_translator", "translators", "keys.py"))
            _spec_keys = importlib.util.spec_from_file_location("keys", _keys_path)
            keys_module = importlib.util.module_from_spec(_spec_keys)
            _spec_keys.loader.exec_module(keys_module)

            self.assertEqual(keys_module.GEMINI_MODELS, ["gemini-1.5-flash-002"])
            self.assertEqual(keys_module.GEMINI_MODEL, "gemini-1.5-flash-002")

        # Test with GEMINI_MODELS specifying models to jump around
        with patch("dotenv.load_dotenv"), patch.dict(os.environ, {"GEMINI_MODELS": "gemini-1.5-flash-002, gemini-3.5-flash-lite, gemini-3.1-flash-lite"}, clear=True), patch.dict(sys.modules, {"langcodes": MagicMock()}):
            _keys_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "manga_translator", "translators", "keys.py"))
            _spec_keys = importlib.util.spec_from_file_location("keys", _keys_path)
            keys_module = importlib.util.module_from_spec(_spec_keys)
            _spec_keys.loader.exec_module(keys_module)

            self.assertEqual(
                keys_module.GEMINI_MODELS,
                ["gemini-1.5-flash-002", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
            )
            self.assertEqual(keys_module.GEMINI_MODEL, "gemini-1.5-flash-002")

    def test_multi_model_rotation_flow(self):
        models = ["gemini-1.5-flash-002", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        km = GeminiKeyManager(["key1"], models=models, default_cooldown=60.0)

        targets_called = []

        async def simulated_translate(key, model, client):
            targets_called.append((key, model))
            return f"translated_{model}"

        # Call 3 times, should rotate across all 3 models
        for expected_model in models:
            res = asyncio.run(km.execute_with_retry(simulated_translate))
            self.assertEqual(res, f"translated_{expected_model}")

        self.assertEqual(
            targets_called,
            [
                ("key1", "gemini-1.5-flash-002"),
                ("key1", "gemini-3.5-flash-lite"),
                ("key1", "gemini-3.1-flash-lite"),
            ]
        )

    def test_model_quota_failover_jump(self):
        models = ["gemini-1.5-flash-002", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        km = GeminiKeyManager(["key1"], models=models, default_cooldown=60.0)

        called_models = []

        class RateLimitError(Exception):
            code = 429
            message = "Resource exhausted"

        async def simulated_translate(key, model, client):
            called_models.append(model)
            if model == "gemini-1.5-flash-002":
                raise RateLimitError("429 RESOURCE_EXHAUSTED: Quota exceeded for gemini-1.5-flash-002")
            return f"success_with_{model}"

        # Request should fail on gemini-1.5-flash-002 and immediately jump to gemini-3.5-flash-lite
        res = asyncio.run(km.execute_with_retry(simulated_translate))
        self.assertEqual(res, "success_with_gemini-3.5-flash-lite")
        self.assertEqual(called_models, ["gemini-1.5-flash-002", "gemini-3.5-flash-lite"])

        # Next request should proceed directly to gemini-3.1-flash-lite
        called_models.clear()
        res2 = asyncio.run(km.execute_with_retry(simulated_translate))
        self.assertEqual(res2, "success_with_gemini-3.1-flash-lite")
        self.assertEqual(called_models, ["gemini-3.1-flash-lite"])

    def test_model_not_found_failover_jump(self):
        models = ["gemini-nonexistent", "gemini-3.5-flash-lite"]
        km = GeminiKeyManager(["key1"], models=models)

        called_models = []

        class NotFoundError(Exception):
            code = 404
            message = "models/gemini-nonexistent is not found for API version v1beta"

        async def simulated_translate(key, model, client):
            called_models.append(model)
            if model == "gemini-nonexistent":
                raise NotFoundError("404 NOT_FOUND")
            return f"success_with_{model}"

        res = asyncio.run(km.execute_with_retry(simulated_translate))
        self.assertEqual(res, "success_with_gemini-3.5-flash-lite")
        self.assertEqual(called_models, ["gemini-nonexistent", "gemini-3.5-flash-lite"])
        self.assertFalse(km._model_map["gemini-nonexistent"].is_valid)

    def test_parse_response_standard(self):
        from manga_translator.translators.gemini import GeminiTranslator
        translator = GeminiTranslator.__new__(GeminiTranslator)

        # 1. Standard tagged response
        resp = "<|1|> Hello\n<|2|> World"
        parsed = translator._parse_response_standard(resp, ["q1", "q2"])
        self.assertEqual(parsed, ["Hello", "World"])

        # 2. Single query response WITHOUT tag (flash-lite models often do this)
        resp_single = "Just the translated text"
        parsed_single = translator._parse_response_standard(resp_single, ["こんにちは"])
        self.assertEqual(parsed_single, ["Just the translated text"])

        # 3. Tagged response with newline immediately following tag
        resp_newline = "<|1|>\nFirst line\nSecond line\n<|2|>\nAnother line"
        parsed_newline = translator._parse_response_standard(resp_newline, ["q1", "q2"])
        self.assertEqual(len(parsed_newline), 2)
        self.assertIn("First line", parsed_newline[0])

        # 4. Markdown code block wrapping
        resp_md = "```\nHello from markdown\n```"
        parsed_md = translator._parse_response_standard(resp_md, ["q1"])
        self.assertEqual(parsed_md, ["Hello from markdown"])

        # 5. Single-query list/page prefix added by the model
        self.assertEqual(
            translator._clean_single_response("Page 1: Just the translated text", "こんにちは", "ENG"),
            "Just the translated text",
        )
        self.assertEqual(
            translator._clean_single_response("1. Just the translated text", "こんにちは", "ENG"),
            "Just the translated text",
        )
        self.assertEqual(
            translator._clean_single_response("1: Just the translated text", "こんにちは", "ENG"),
            "Just the translated text",
        )
        self.assertEqual(
            translator._clean_single_response("1. Keep this number", "1. Keep this number", "ENG"),
            "1. Keep this number",
        )

    def test_transliterate_kana_fallback(self):
        from manga_translator.translators.gemini import (
            transliterate_kana_fallback,
            contains_cjk,
            contains_kana,
            has_letters_or_digits,
        )

        # Japanese SFX transliteration test
        self.assertEqual(transliterate_kana_fallback("ドキドキ"), "dokidoki")
        self.assertEqual(transliterate_kana_fallback("あ…"), "a…")
        self.assertEqual(transliterate_kana_fallback("きゃあ"), "kyaa")

        # contains_cjk and contains_kana tests
        self.assertTrue(contains_cjk("ドキドキ"))
        self.assertTrue(contains_kana("あ…"))
        self.assertFalse(contains_cjk("dokidoki"))
        self.assertFalse(has_letters_or_digits("……！？---"))
        self.assertTrue(has_letters_or_digits("Hello"))

    def test_system_template_sfx_rules(self):
        from manga_translator.translators.config_gpt import ConfigGPT

        template = ConfigGPT._CHAT_SYSTEM_TEMPLATE
        self.assertNotIn("Preserve original gibberish or sound effects without translation", template)
        self.assertIn("Adapt or translate sound effects (SFX)", template)
    def test_translate_multiline_and_newline_safety(self):
        from manga_translator.translators.gemini import GeminiTranslator
        translator = GeminiTranslator.__new__(GeminiTranslator)
        translator.logger = MagicMock()
        translator.to_lang = "ENG"
        translator.config = {}
        translator._RETRY_ATTEMPTS = 2
        translator.token_count = 0
        translator.token_count_last = 0
        translator._parse_response = translator._parse_response_standard

        def mock_assemble_prompts(from_lang, to_lang, queries):
            yield "prompt text", len(queries)
        translator._assemble_prompts = mock_assemble_prompts

        async def mock_req(to_lang, prompt):
            return "<|1|>\nLine one of speech\nLine two of speech"
        translator._request_translation = mock_req

        result = asyncio.run(translator._translate("JPN", "ENG", ["日本語のセリフ"]))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "Line one of speech Line two of speech")

    def test_translate_fallback_for_untranslated_sfx(self):
        from manga_translator.translators.gemini import GeminiTranslator
        translator = GeminiTranslator.__new__(GeminiTranslator)
        translator.logger = MagicMock()
        translator.to_lang = "ENG"
        translator.config = {}
        translator._RETRY_ATTEMPTS = 1
        translator.token_count = 0
        translator.token_count_last = 0
        translator._parse_response = translator._parse_response_standard

        def mock_assemble_prompts(from_lang, to_lang, queries):
            yield "prompt text", len(queries)
        translator._assemble_prompts = mock_assemble_prompts

        async def mock_req(to_lang, prompt):
            return "<|1|> ドキドキ"
        translator._request_translation = mock_req

        result = asyncio.run(translator._translate("JPN", "ENG", ["ドキドキ"]))
        self.assertEqual(len(result), 1)
        # Should be transliterated to romaji so it does not fail complete()
        self.assertEqual(result[0], "dokidoki")

    def test_adult_dialogue_fallback_and_nsfw_masking(self):
        from manga_translator.translators.gemini import (
            mask_nsfw_text,
            unmask_nsfw_text,
            adult_dictionary_fallback,
        )

        query = "ウソ…私セックス…ホントにセックスしてるっ！"

        # Test masking
        masked, reps = mask_nsfw_text(query)
        self.assertIn("[intimacy]", masked)
        self.assertNotIn("セックス", masked)

        # Test unmasking
        translated_masked = "No way... I'm [intimacy]... really [intimacy]ing!"
        unmasked = unmask_nsfw_text(translated_masked, reps)
        self.assertIn("sex", unmasked)
        self.assertNotIn("[intimacy]", unmasked)

        # Test English adult dictionary fallback
        fallback_eng = adult_dictionary_fallback(query, to_lang="ENG")
        self.assertIn("No way", fallback_eng)
        self.assertIn("sex", fallback_eng)
        self.assertFalse(any(ord(c) > 127 for c in fallback_eng))

        # Test Chinese adult dictionary fallback
        fallback_zh = adult_dictionary_fallback(query, to_lang="CHS")
        self.assertIn("骗人", fallback_zh)
        self.assertIn("做爱", fallback_zh)

    def test_safety_settings_include_civic_integrity(self):
        from manga_translator.translators.gemini import GeminiTranslator
        translator = GeminiTranslator.__new__(GeminiTranslator)
        translator.logger = MagicMock()
        translator.key_manager = MagicMock()
        translator.key_manager.valid_keys = ["mock_key"]

        translator.safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
        ]
        categories = {s["category"] for s in translator.safety_settings}
        self.assertIn("HARM_CATEGORY_SEXUALLY_EXPLICIT", categories)
        self.assertIn("HARM_CATEGORY_CIVIC_INTEGRITY", categories)
        for s in translator.safety_settings:
            self.assertEqual(s["threshold"], "BLOCK_NONE")

    def test_adult_dialogue_fallback_when_gemini_refuses(self):
        from manga_translator.translators.gemini import GeminiTranslator
        translator = GeminiTranslator.__new__(GeminiTranslator)
        translator.logger = MagicMock()
        translator.to_lang = "ENG"
        translator.config = {}
        translator._RETRY_ATTEMPTS = 1
        translator.token_count = 0
        translator.token_count_last = 0
        translator._parse_response = translator._parse_response_standard

        def mock_assemble_prompts(from_lang, to_lang, queries):
            yield "prompt text", len(queries)
        translator._assemble_prompts = mock_assemble_prompts

        # Simulate Gemini returning empty string due to safety block
        async def mock_req(to_lang, prompt):
            return ""
        translator._request_translation = mock_req

        query = "ウソ…私セックス…ホントにセックスしてるっ！"
        result = asyncio.run(translator._translate("JPN", "ENG", [query]))
        self.assertEqual(len(result), 1)
        self.assertIn("No way", result[0])
        self.assertIn("sex", result[0])

    def test_sub_batch_translation_does_not_raise_index_error(self):
        from manga_translator.translators.gemini import GeminiTranslator
        translator = GeminiTranslator.__new__(GeminiTranslator)
        translator.logger = MagicMock()
        translator.to_lang = "ENG"
        translator.config = {}
        translator._RETRY_ATTEMPTS = 1
        translator.token_count = 0
        translator.token_count_last = 0
        translator._parse_response = lambda resp, queries: ["Translation 1", "Translation 2"]

        def mock_assemble_prompts(from_lang, to_lang, queries):
            yield "prompt text", len(queries)
        translator._assemble_prompts = mock_assemble_prompts

        async def mock_req(to_lang, prompt):
            return "<|1|>Translation 1\n<|2|>Translation 2"
        translator._request_translation = mock_req

        # Simulate translating sub-batch of 2 queries within a total batch of 10 queries
        sub_queries = ["Query 1", "Query 2"]
        sub_indices = [0, 1]
        translations = [""] * 10
        total_queries = 10

        success = asyncio.run(translator._translate_batch(
            "JPN", "ENG", sub_queries, sub_indices, translations, total_queries
        ))
        self.assertTrue(success)
        self.assertEqual(translations[0], "Translation 1")
        self.assertEqual(translations[1], "Translation 2")


if __name__ == "__main__":
    unittest.main()
