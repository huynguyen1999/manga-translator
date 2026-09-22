import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure repo root is on sys.path
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

sys.modules["manga_translator.manga_translator"] = MagicMock()

for submod in [
    "manga_translator.translators.deepl",
    "manga_translator.translators.groq",
    "manga_translator.translators.chatgpt",
    "manga_translator.translators.sugoi",
    "py3langid",
    "langcodes",
    "einops",
    "loguru",
]:
    if submod not in sys.modules:
        m = MagicMock()
        m.__spec__ = None
        sys.modules[submod] = m

from manga_translator.config import Translator, TranslatorConfig, Config
from manga_translator.translators.common import MissingAPIKeyException
from manga_translator.translators.deepseek import DeepseekTranslator
from manga_translator.translators.openrouter import OpenRouterTranslator
from manga_translator.translators import TRANSLATORS, GPT_TRANSLATORS, translator_cache


class TestOpenRouterTranslator(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        translator_cache.clear()

    def test_enum_and_registry(self):
        """Test that openrouter is registered in the enum and translators dictionary."""
        self.assertIn("openrouter", [t.value for t in Translator])
        self.assertEqual(Translator.openrouter, "openrouter")
        self.assertIn(Translator.openrouter, TRANSLATORS)
        self.assertIn(Translator.openrouter, GPT_TRANSLATORS)
        self.assertEqual(TRANSLATORS[Translator.openrouter], OpenRouterTranslator)
        self.assertTrue(issubclass(OpenRouterTranslator, DeepseekTranslator))

    def test_independent_defaults(self):
        """Test default values for OpenRouter."""
        with patch.dict(os.environ, {}, clear=False):
            tr = OpenRouterTranslator(check_openai_key=False)
            self.assertEqual(tr.model, "deepseek/deepseek-v4-flash-0731")
            self.assertEqual(str(tr.client.base_url).rstrip("/"), "https://openrouter.ai/api/v1")
            self.assertEqual(tr.provider_sort, "price")
            self.assertEqual(
                tr._build_provider_config(),
                {"order": ["reka", "inceptron"], "allow_fallbacks": False, "sort": "price"},
            )

    def test_env_overrides(self):
        """Test environment variable overrides for model, base, key, and provider routing."""
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "sk-or-test-key",
                "OPENROUTER_API_BASE": "https://openrouter.ai/api/v1",
                "OPENROUTER_MODEL": "deepseek/deepseek-v4-flash-0731",
                "OPENROUTER_PROVIDER_SORT": "price",
            },
            clear=True,
        ):
            tr = OpenRouterTranslator(check_openai_key=True)
            self.assertEqual(tr.client.api_key, "sk-or-test-key")
            self.assertEqual(tr.model, "deepseek/deepseek-v4-flash-0731")
            self.assertEqual(str(tr.client.base_url).rstrip("/"), "https://openrouter.ai/api/v1")
            self.assertEqual(tr.provider_sort, "price")
            self.assertEqual(
                tr._build_provider_config(),
                {"order": ["reka", "inceptron"], "allow_fallbacks": False, "sort": "price"},
            )

    def test_missing_key_validation(self):
        """If OPENROUTER_API_KEY is empty and check_openai_key is True, raise MissingAPIKeyException."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=True):
            with self.assertRaises(MissingAPIKeyException) as ctx:
                OpenRouterTranslator(check_openai_key=True)
            self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))

    def test_token_counter_fallback(self):
        """Test that count_tokens falls back gracefully without tokenizer."""
        tr = OpenRouterTranslator(check_openai_key=False)
        tr.tokenizer = None
        count = tr.count_tokens("こんにちは世界")
        self.assertGreater(count, 0)
        self.assertEqual(count, len("こんにちは世界".encode("utf-8")))

    async def test_request_translation_pins_reka_provider(self):
        """Verify _request_translation pins requests to Reka AI through OpenRouter."""
        tr = OpenRouterTranslator(check_openai_key=False, api_key="test-key")

        # Mock chat.completions.create
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "<|1|>Hello World"
        mock_choice.text = None
        mock_response.choices = [mock_choice]
        mock_response.usage.total_tokens = 42

        tr.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await tr._request_translation("ENG", "<|1|>こんにちは世界")
        self.assertEqual(result, "<|1|>Hello World")

        tr.client.chat.completions.create.assert_awaited_once()
        call_kwargs = tr.client.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "deepseek/deepseek-v4-flash-0731")
        self.assertIn("extra_body", call_kwargs)
        self.assertEqual(
            call_kwargs["extra_body"],
            {"provider": {"order": ["reka", "inceptron"], "allow_fallbacks": False, "sort": "price"}},
        )

    async def test_full_translate_flow(self):
        """Verify _translate batches and parses translations correctly."""
        tr = OpenRouterTranslator(check_openai_key=False, api_key="test-key")

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "<|1|>Hello\n<|2|>World"
        mock_choice.text = None
        mock_response.choices = [mock_choice]
        mock_response.usage.total_tokens = 30

        tr.client.chat.completions.create = AsyncMock(return_value=mock_response)

        results = await tr._translate("JPN", "ENG", ["こんにちは", "世界"])
        self.assertEqual(results, ["Hello", "World"])


if __name__ == "__main__":
    unittest.main()
