import os
import unittest
from unittest.mock import patch

from manga_translator.config import Translator
from manga_translator.translators import get_translator
from manga_translator.translators.dash import DashTranslator
from server.manga_summary import resolve_summary_model, get_summary_chunk_limit


class TestDashTranslator(unittest.TestCase):
    def test_dash_translator_defaults_and_env_resolution(self):
        with patch.dict(os.environ, {
            "DASHSCOPE_API_KEY": "sk-test-dash-key",
            "DASHSCOPE_API_BASE": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "DASHSCOPE_MODEL": "deepseek-v3",
        }, clear=False):
            translator = DashTranslator(check_openai_key=True)
            self.assertEqual(translator.model, "deepseek-v3")
            self.assertEqual(str(translator.client.base_url).rstrip("/"), "https://dashscope.aliyuncs.com/compatible-mode/v1")
            self.assertEqual(translator.client.api_key, "sk-test-dash-key")

    def test_dash_translator_custom_model_and_key(self):
        translator = DashTranslator(
            check_openai_key=False,
            api_key="custom-key",
            api_base="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            model="deepseek-r1",
        )
        self.assertEqual(translator.model, "deepseek-r1")
        self.assertEqual(str(translator.client.base_url).rstrip("/"), "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(translator.client.api_key, "custom-key")

    def test_dash_translator_resolves_deepseek_alias_to_deepseek_v3(self):
        translator = DashTranslator(
            check_openai_key=False,
            api_key="custom-key",
            model="deepseek",
        )
        self.assertEqual(translator.model, "deepseek-v3")

    def test_get_translator_instantiates_dash(self):
        translator = get_translator(Translator.dash, check_openai_key=False, api_key="dummy")
        self.assertIsInstance(translator, DashTranslator)

    def test_dash_translator_resolves_dash_alias_to_deepseek_v3(self):
        translator = DashTranslator(
            check_openai_key=False,
            api_key="custom-key",
            model="dash",
        )
        self.assertEqual(translator.model, "deepseek-v3")

    def test_deepseek_translator_falls_back_when_given_dash_alias(self):
        from manga_translator.translators.deepseek import DeepseekTranslator
        translator = DeepseekTranslator(
            check_openai_key=False,
            api_key="custom-key",
            model="dash",
        )
        self.assertEqual(translator.model, "deepseek-chat")

    def test_dash_summary_model_resolution(self):
        from manga_translator.translators import keys

        with patch.object(keys, "DASH_MODEL", "deepseek-v3"), \
             patch.object(keys, "DASH_API_KEY", "test-dash-key"), \
             patch.object(keys, "DASH_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"):
            self.assertEqual(
                resolve_summary_model("dash"),
                ("dash", "deepseek-v3", "test-dash-key", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
            self.assertEqual(
                resolve_summary_model("dashscope"),
                ("dash", "deepseek-v3", "test-dash-key", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
            self.assertEqual(
                resolve_summary_model("dash:deepseek-r1"),
                ("dash", "deepseek-r1", "test-dash-key", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
            self.assertEqual(
                resolve_summary_model("deepseek:dash"),
                ("dash", "deepseek-v3", "test-dash-key", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
            self.assertEqual(
                resolve_summary_model("deepseek-v3"),
                ("dash", "deepseek-v3", "test-dash-key", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
            self.assertEqual(
                resolve_summary_model("deepseek:deepseek-v3"),
                ("dash", "deepseek-v3", "test-dash-key", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )

    def test_dash_chunk_limit(self):
        self.assertEqual(get_summary_chunk_limit("dash"), 64_000)
        self.assertEqual(get_summary_chunk_limit("dashscope"), 64_000)


if __name__ == "__main__":
    unittest.main()
