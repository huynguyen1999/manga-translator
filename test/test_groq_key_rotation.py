import asyncio
import importlib.util
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

_file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "manga_translator", "translators", "groq_keys.py"))
_spec = importlib.util.spec_from_file_location("groq_keys", _file_path)
groq_keys = importlib.util.module_from_spec(_spec)
sys.modules["groq_keys"] = groq_keys
_spec.loader.exec_module(groq_keys)

GroqKeyInfo = groq_keys.GroqKeyInfo
GroqModelInfo = groq_keys.GroqModelInfo
GroqKeyManager = groq_keys.GroqKeyManager
mask_key = groq_keys.mask_key
parse_groq_keys = groq_keys.parse_groq_keys
parse_groq_models = groq_keys.parse_groq_models


class TestGroqKeyRotation(unittest.TestCase):
    def test_mask_key(self):
        self.assertEqual(mask_key(""), "")
        self.assertEqual(mask_key("gsk_1234567890abcdef"), "gsk_12...cdef")
        self.assertEqual(mask_key("short"), "sh...rt")
        self.assertEqual(mask_key("ab"), "***")

    def test_parse_groq_keys(self):
        # Empty
        self.assertEqual(parse_groq_keys(""), [])
        self.assertEqual(parse_groq_keys(None), [])

        # Single key
        self.assertEqual(parse_groq_keys("key1"), ["key1"])

        # Comma-separated
        self.assertEqual(
            parse_groq_keys("key1, key2, key3"),
            ["key1", "key2", "key3"],
        )

        # Space-separated
        self.assertEqual(
            parse_groq_keys("key1 key2 key3"),
            ["key1", "key2", "key3"],
        )

        # Semicolon and newline separated
        self.assertEqual(
            parse_groq_keys("key1; key2\nkey3\r\nkey4"),
            ["key1", "key2", "key3", "key4"],
        )

        # JSON array
        self.assertEqual(
            parse_groq_keys('["key1", "key2", "key3"]'),
            ["key1", "key2", "key3"],
        )

        # List input
        self.assertEqual(
            parse_groq_keys(["key1", "key2, key3"]),
            ["key1", "key2", "key3"],
        )

        # Deduplication preserving order
        self.assertEqual(
            parse_groq_keys("key1, key2, key1, key3, key2"),
            ["key1", "key2", "key3"],
        )

    def test_parse_groq_models(self):
        self.assertEqual(parse_groq_models(""), [])
        self.assertEqual(parse_groq_models(None), [])
        self.assertEqual(
            parse_groq_models("llama-3.3-70b-versatile, mixtral-8x7b-32768"),
            ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
        )
        self.assertEqual(
            parse_groq_models('["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]'),
            ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        )

    def test_round_robin_rotation(self):
        keys = ["keyA", "keyB", "keyC"]
        km = GroqKeyManager(keys)

        self.assertEqual(km.total_keys, 3)
        self.assertEqual(km.valid_keys_count, 3)
        self.assertEqual(km.available_keys_count, 3)

        # Successive calls should cycle through keys
        seq = [km.get_next_key() for _ in range(6)]
        self.assertEqual(seq, ["keyA", "keyB", "keyC", "keyA", "keyB", "keyC"])

    def test_rate_limit_and_cooldown(self):
        keys = ["key1", "key2"]
        km = GroqKeyManager(keys, default_cooldown=10.0)

        # Initial key
        k1 = km.get_next_key()
        self.assertEqual(k1, "key1")

        # Mark k1 as rate limited for 10 seconds
        km.mark_rate_limited("key1", cooldown_seconds=10.0)
        self.assertEqual(km.available_keys_count, 1)

        # Next key must be key2
        k2 = km.get_next_key()
        self.assertEqual(k2, "key2")

        # Calling again should still return key2 since key1 is cooling down
        k3 = km.get_next_key()
        self.assertEqual(k3, "key2")

        # When all keys are cooling down and allow_cooldown=False, returns None
        km.mark_rate_limited("key2", cooldown_seconds=10.0)
        self.assertEqual(km.available_keys_count, 0)
        self.assertIsNone(km.get_next_key(allow_cooldown=False))

        # When allow_cooldown=True, returns the one with shortest cooldown
        self.assertIsNotNone(km.get_next_key(allow_cooldown=True))

    def test_invalid_key(self):
        keys = ["good_key", "bad_key"]
        km = GroqKeyManager(keys)

        # Mark bad_key permanently invalid
        km.mark_invalid("bad_key", "401 Unauthorized")
        self.assertEqual(km.valid_keys_count, 1)
        self.assertEqual(km.available_keys_count, 1)

        # Should only ever return good_key
        for _ in range(4):
            self.assertEqual(km.get_next_key(), "good_key")

    def test_target_pair_rotation(self):
        keys = ["key1", "key2"]
        models = ["modelA", "modelB"]
        km = GroqKeyManager(keys=keys, models=models)

        # Should cycle through key/model pairs
        pairs = [km.get_next_target() for _ in range(4)]
        self.assertEqual(len(pairs), 4)
        for k, m in pairs:
            self.assertIn(k, keys)
            self.assertIn(m, models)

    def test_execute_with_retry(self):
        keys = ["key1", "key2"]
        km = GroqKeyManager(keys=keys, default_cooldown=5.0)

        calls = []

        async def mock_api(key, model, client):
            calls.append((key, model))
            if key == "key1":
                # Simulate RateLimitError
                err = Exception("Rate limit exceeded 429")
                err.status_code = 429
                raise err
            return f"success with {key}"

        with patch.object(km, "get_groq_client", return_value=MagicMock()):
            result = asyncio.run(km.execute_with_retry(mock_api))

        self.assertEqual(result, "success with key2")
        self.assertIn(("key1", km.current_model), calls)
        self.assertIn(("key2", km.current_model), calls)


class TestGroqTranslatorIntegration(unittest.TestCase):
    @patch.dict(os.environ, {"GROQ_API_KEYS": "gsk_test1, gsk_test2", "GROQ_MODEL": "llama-3.3-70b-versatile"})
    def test_groq_translator_init_and_rotation(self):
        mock_groq = MagicMock()
        with patch.dict(sys.modules, {"groq": mock_groq}):
            import manga_translator.translators.groq as groq_mod
            groq_mod.groq = mock_groq
            from manga_translator.translators.groq import GroqTranslator

            translator = GroqTranslator(check_groq_key=True)
            self.assertEqual(translator.key_manager.total_keys, 2)
            self.assertEqual(translator.key_manager.get_all_keys(), ["gsk_test1", "gsk_test2"])

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"translated": "你好"}'))]
        mock_response.usage = MagicMock(total_tokens=42)

        mock_client1 = MagicMock()
        mock_client1.chat.completions.create = AsyncMock(side_effect=Exception("429 rate limit"))

        mock_client2 = MagicMock()
        mock_client2.chat.completions.create = AsyncMock(return_value=mock_response)

        def mock_get_client(key):
            return mock_client1 if key == "gsk_test1" else mock_client2

        translator.key_manager.get_groq_client = mock_get_client

        res = asyncio.run(translator._request_translation("Simplified Chinese", "Hello"))
        self.assertEqual(res, "你好")
        self.assertEqual(translator.token_count, 42)


if __name__ == "__main__":
    unittest.main()
