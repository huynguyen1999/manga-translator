import asyncio
import importlib.util
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

_file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "manga_translator", "translators", "gemini_keys.py"))
_spec = importlib.util.spec_from_file_location("gemini_keys", _file_path)
gemini_keys = importlib.util.module_from_spec(_spec)
sys.modules["gemini_keys"] = gemini_keys
_spec.loader.exec_module(gemini_keys)

GeminiKeyInfo = gemini_keys.GeminiKeyInfo
GeminiModelInfo = gemini_keys.GeminiModelInfo
GeminiKeyManager = gemini_keys.GeminiKeyManager
GeminiRequestBudget = gemini_keys.GeminiRequestBudget
GeminiRetryExhausted = gemini_keys.GeminiRetryExhausted
mask_key = gemini_keys.mask_key
parse_gemini_keys = gemini_keys.parse_gemini_keys
parse_gemini_models = gemini_keys.parse_gemini_models


class TestGeminiKeyRotation(unittest.TestCase):
    def test_mask_key(self):
        self.assertEqual(mask_key(""), "")
        self.assertEqual(mask_key("AIzaSy1234567890"), "AIzaSy...7890")
        self.assertEqual(mask_key("short"), "sh...rt")
        self.assertEqual(mask_key("ab"), "***")

    def test_parse_gemini_keys(self):
        # Empty
        self.assertEqual(parse_gemini_keys(""), [])
        self.assertEqual(parse_gemini_keys(None), [])

        # Single key
        self.assertEqual(parse_gemini_keys("key1"), ["key1"])

        # Comma-separated
        self.assertEqual(
            parse_gemini_keys("key1, key2, key3"),
            ["key1", "key2", "key3"],
        )

        # Space-separated
        self.assertEqual(
            parse_gemini_keys("key1 key2 key3"),
            ["key1", "key2", "key3"],
        )

        # Semicolon and newline separated
        self.assertEqual(
            parse_gemini_keys("key1; key2\nkey3\r\nkey4"),
            ["key1", "key2", "key3", "key4"],
        )

        # JSON array
        self.assertEqual(
            parse_gemini_keys('["key1", "key2", "key3"]'),
            ["key1", "key2", "key3"],
        )

        # List input
        self.assertEqual(
            parse_gemini_keys(["key1", "key2, key3"]),
            ["key1", "key2", "key3"],
        )

        # Deduplication preserving order
        self.assertEqual(
            parse_gemini_keys("key1, key2, key1, key3, key2"),
            ["key1", "key2", "key3"],
        )

    def test_round_robin_rotation(self):
        keys = ["keyA", "keyB", "keyC"]
        km = GeminiKeyManager(keys)

        self.assertEqual(km.total_keys, 3)
        self.assertEqual(km.valid_keys_count, 3)
        self.assertEqual(km.available_keys_count, 3)

        # Successive calls should cycle through keys
        seq = [km.get_next_key() for _ in range(6)]
        self.assertEqual(seq, ["keyA", "keyB", "keyC", "keyA", "keyB", "keyC"])

    def test_rate_limit_and_cooldown(self):
        keys = ["key1", "key2"]
        km = GeminiKeyManager(keys, default_cooldown=10.0)

        # Initial key
        k1 = km.get_next_key()
        self.assertEqual(k1, "key1")

        # Mark k1 as rate limited for 10 seconds
        km.mark_rate_limited("key1", cooldown_seconds=10.0)
        self.assertEqual(km.available_keys_count, 1)

        # Next key must be key2
        k2 = km.get_next_key()
        self.assertEqual(k2, "key2")

        # Next call should still return key2 because key1 is cooling down
        k3 = km.get_next_key()
        self.assertEqual(k3, "key2")

        # Now mark key2 also rate limited
        km.mark_rate_limited("key2", cooldown_seconds=20.0)
        self.assertEqual(km.available_keys_count, 0)

        # No keys available without cooldown
        self.assertIsNone(km.get_next_key(allow_cooldown=False))

        # With allow_cooldown=True, returns key1 because it has the earlier cooldown
        best = km.get_next_key(allow_cooldown=True)
        self.assertEqual(best, "key1")

        # Test expiration
        info1 = km._key_map["key1"]
        info1.cooldown_until = time.time() - 1.0  # expired
        self.assertTrue(info1.is_available())
        self.assertEqual(km.get_next_key(), "key1")

    def test_invalid_key_handling(self):
        keys = ["key1", "key2"]
        km = GeminiKeyManager(keys)

        km.mark_invalid("key1", reason="400 API_KEY_INVALID")
        self.assertEqual(km.total_keys, 2)
        self.assertEqual(km.valid_keys_count, 1)

        # All calls should now only return key2
        self.assertEqual(km.get_next_key(), "key2")
        self.assertEqual(km.get_next_key(), "key2")

        # If key2 also marked invalid
        km.mark_invalid("key2", reason="403 PERMISSION_DENIED")
        self.assertEqual(km.valid_keys_count, 0)
        self.assertIsNone(km.get_next_key(allow_cooldown=True))

    def test_error_classification(self):
        class MockError(Exception):
            def __init__(self, message, code=None, status=None):
                super().__init__(message)
                self.code = code
                self.status = status

        # 429 / rate limits
        err_429 = MockError("Too Many Requests", code=429)
        self.assertTrue(GeminiKeyManager.is_rate_limit_error(err_429))

        err_resource = MockError("RESOURCE_EXHAUSTED", status="RESOURCE_EXHAUSTED")
        self.assertTrue(GeminiKeyManager.is_rate_limit_error(err_resource))

        err_quota_msg = MockError("Quota exceeded for quota metric...")
        self.assertTrue(GeminiKeyManager.is_rate_limit_error(err_quota_msg))

        # Invalid key errors
        err_invalid = MockError("API_KEY_INVALID", code=400)
        self.assertTrue(GeminiKeyManager.is_invalid_key_error(err_invalid))

        err_perm = MockError("PERMISSION_DENIED: caller not allowed", code=403)
        self.assertTrue(GeminiKeyManager.is_invalid_key_error(err_perm))

        # Normal errors
        err_other = MockError("Network timeout", code=504)
        self.assertFalse(GeminiKeyManager.is_rate_limit_error(err_other))
        self.assertFalse(GeminiKeyManager.is_invalid_key_error(err_other))

    def test_context_caching_per_key(self):
        km = GeminiKeyManager(["key1", "key2"])
        km.set_cached_content("key1", "cache_obj_1")
        km.set_cached_content("key2", "cache_obj_2")

        self.assertEqual(km.get_cached_content("key1"), "cache_obj_1")
        self.assertEqual(km.get_cached_content("key2"), "cache_obj_2")
        self.assertIsNone(km.get_cached_content("key3"))

        km.clear_cached_contents()
        self.assertIsNone(km.get_cached_content("key1"))

    def test_async_failover_execution(self):
        keys = ["key1", "key2", "key3"]
        km = GeminiKeyManager(keys, default_cooldown=10.0)

        attempts_record = []

        class RateLimitException(Exception):
            def __init__(self):
                super().__init__("429 Resource has been exhausted")
                self.code = 429

        async def dummy_api_call(key, client):
            attempts_record.append(key)
            if key == "key1":
                raise RateLimitException()
            return f"success_with_{key}"

        result = asyncio.run(km.execute_with_retry(dummy_api_call))
        self.assertEqual(result, "success_with_key2")
        self.assertEqual(attempts_record, ["key1", "key2"])
        # key1 should be in cooldown
        self.assertTrue(km._key_map["key1"].is_cooling_down())
        self.assertFalse(km._key_map["key2"].is_cooling_down())

    def test_single_rate_limited_key_fails_without_forced_retries(self):
        km = GeminiKeyManager(["key1"], default_cooldown=60.0)
        attempts_record = []

        class RateLimitException(Exception):
            code = 429

        async def dummy_api_call(key, client):
            attempts_record.append(key)
            raise RateLimitException("429 RESOURCE_EXHAUSTED")

        with self.assertRaisesRegex(RuntimeError, "All 1 Gemini API keys"):
            asyncio.run(km.execute_with_retry(dummy_api_call))
        self.assertEqual(attempts_record, ["key1"])

    def test_request_budget_stops_at_limit(self):
        budget = GeminiRequestBudget(limit=4)
        for _ in range(4):
            budget.consume()
        with self.assertRaises(GeminiRetryExhausted):
            budget.consume()

    def test_dynamic_add_keys(self):
        km = GeminiKeyManager(["key1"])
        self.assertEqual(km.total_keys, 1)

        added = km.add_keys("key2, key3")
        self.assertEqual(added, 2)
        self.assertEqual(km.total_keys, 3)

        # Adding duplicates should not increment
        added_dup = km.add_keys(["key1", "key4"])
        self.assertEqual(added_dup, 1)
        self.assertEqual(km.total_keys, 4)

    def test_client_caching(self):
        km = GeminiKeyManager(["test_key_1", "test_key_2"])

        with patch("google.genai.Client") as mock_genai, patch("openai.OpenAI") as mock_openai:
            mock_genai.side_effect = lambda api_key: MagicMock(name=f"GenAI_{api_key}")
            mock_openai.side_effect = lambda api_key, base_url: MagicMock(name=f"OpenAI_{api_key}")

            client1 = km.get_genai_client("test_key_1")
            client1_again = km.get_genai_client("test_key_1")
            self.assertIs(client1, client1_again)

            client2 = km.get_genai_client("test_key_2")
            self.assertIsNot(client1, client2)

            o1 = km.get_openai_client("test_key_1")
            o1_again = km.get_openai_client("test_key_1")
            self.assertIs(o1, o1_again)

    def test_parse_gemini_models(self):
        self.assertEqual(parse_gemini_models(""), [])
        self.assertEqual(parse_gemini_models(None), [])
        self.assertEqual(
            parse_gemini_models("gemini-1.5-flash-002, gemini-3.5-flash-lite; models/gemini-3.1-flash-lite"),
            ["gemini-1.5-flash-002", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        )
        self.assertEqual(
            parse_gemini_models("gemini-3.5-flash-lite gemini-3.1-flash-lite"),
            ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        )
        self.assertEqual(
            parse_gemini_models('["gemini-1.5-flash-002", "gemini-3.5-flash-lite"]'),
            ["gemini-1.5-flash-002", "gemini-3.5-flash-lite"]
        )
        self.assertEqual(
            parse_gemini_models(["models/gemini-3.5-flash-lite", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]),
            ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        )

    def test_model_rotation_and_target_selection(self):
        keys = ["k1"]
        models = ["gemini-1.5-flash-002", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        km = GeminiKeyManager(keys=keys, models=models)

        self.assertEqual(km.total_models, 3)
        self.assertEqual(km.valid_models_count, 3)

        # Successive get_next_target calls should cycle through the models
        targets = [km.get_next_target() for _ in range(6)]
        expected = [
            ("k1", "gemini-1.5-flash-002"),
            ("k1", "gemini-3.5-flash-lite"),
            ("k1", "gemini-3.1-flash-lite"),
            ("k1", "gemini-1.5-flash-002"),
            ("k1", "gemini-3.5-flash-lite"),
            ("k1", "gemini-3.1-flash-lite"),
        ]
        self.assertEqual(targets, expected)

    def test_target_level_rate_limiting_and_jumping(self):
        keys = ["k1"]
        models = ["gemini-1.5-flash-002", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        km = GeminiKeyManager(keys=keys, models=models, default_cooldown=30.0)

        # Use first target (k1, gemini-1.5-flash-002)
        k, m = km.get_next_target()
        self.assertEqual((k, m), ("k1", "gemini-1.5-flash-002"))

        # Mark this specific target rate limited
        km.mark_rate_limited(k, model=m, cooldown_seconds=30.0)

        # Key k1 should still have available models!
        self.assertTrue(km.is_target_available("k1", "gemini-3.5-flash-lite"))
        self.assertFalse(km.is_target_available("k1", "gemini-1.5-flash-002"))

        # Next target should jump directly to gemini-3.5-flash-lite
        k2, m2 = km.get_next_target()
        self.assertEqual((k2, m2), ("k1", "gemini-3.5-flash-lite"))

        # Next target jumps to gemini-3.1-flash-lite
        k3, m3 = km.get_next_target()
        self.assertEqual((k3, m3), ("k1", "gemini-3.1-flash-lite"))

        # Next target should cycle back to gemini-3.5-flash-lite (skipping cooling gemini-1.5-flash-002)
        k4, m4 = km.get_next_target()
        self.assertEqual((k4, m4), ("k1", "gemini-3.5-flash-lite"))

    def test_model_invalidation_on_not_found(self):
        keys = ["k1"]
        models = ["invalid-model", "gemini-3.5-flash-lite"]
        km = GeminiKeyManager(keys=keys, models=models)

        # Mark invalid-model as not found
        km.mark_model_invalid("invalid-model", "404 NOT_FOUND")
        self.assertEqual(km.valid_models_count, 1)

        # get_next_target should always return gemini-3.5-flash-lite
        t1 = km.get_next_target()
        t2 = km.get_next_target()
        self.assertEqual(t1, ("k1", "gemini-3.5-flash-lite"))
        self.assertEqual(t2, ("k1", "gemini-3.5-flash-lite"))

    def test_model_context_caching_separation(self):
        km = GeminiKeyManager(keys=["k1"], models=["gemini-1.5-flash-002", "gemini-3.5-flash-lite"])
        cache_m1 = MagicMock(name="cache_m1")
        cache_m2 = MagicMock(name="cache_m2")

        km.set_cached_content("k1", cache_m1, model="gemini-1.5-flash-002")
        km.set_cached_content("k1", cache_m2, model="gemini-3.5-flash-lite")

        self.assertIs(km.get_cached_content("k1", model="gemini-1.5-flash-002"), cache_m1)
        self.assertIs(km.get_cached_content("k1", model="gemini-3.5-flash-lite"), cache_m2)


if __name__ == "__main__":
    unittest.main()
