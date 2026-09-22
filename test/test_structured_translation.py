import json
import unittest

from manga_translator.translators.structured import (
    _extract_json_string,
    _parse_response,
    translate_structured,
    StructuredTranslationError,
)


class StructuredTranslationParsingTest(unittest.TestCase):
    def test_extract_json_from_plain_json(self):
        raw = '{"translations": [{"id": "1", "translation": "Hello"}]}'
        self.assertEqual(_extract_json_string(raw), raw)

    def test_extract_json_from_markdown_fence(self):
        raw = '```json\n{"translations": [{"id": "1", "translation": "Hello"}]}\n```'
        self.assertEqual(_extract_json_string(raw), '{"translations": [{"id": "1", "translation": "Hello"}]}')

    def test_extract_json_with_think_tags(self):
        raw = '<think>I should translate this text into English.</think>\n```json\n{"translations": [{"id": "1", "translation": "Hello"}]}\n```'
        self.assertEqual(_extract_json_string(raw), '{"translations": [{"id": "1", "translation": "Hello"}]}')

    def test_extract_json_with_conversational_text(self):
        raw = 'Sure, here is the translated JSON:\n{"translations": [{"id": "1", "translation": "Hello"}]}\nHope this helps!'
        self.assertEqual(_extract_json_string(raw), '{"translations": [{"id": "1", "translation": "Hello"}]}')

    def test_parse_response_empty_or_invalid(self):
        self.assertEqual(_parse_response("", {"1"}), {})
        self.assertEqual(_parse_response("   ", {"1"}), {})
        self.assertEqual(_parse_response("I cannot translate this.", {"1"}), {})

    def test_parse_response_with_trailing_commas(self):
        raw = '{"translations": [{"id": "1", "translation": "Hello",},],}'
        self.assertEqual(_parse_response(raw, {"1"}), {"1": "Hello"})

    def test_parse_response_dictionary_shape(self):
        raw = '{"1": "Hello", "2": "World"}'
        self.assertEqual(_parse_response(raw, {"1", "2"}), {"1": "Hello", "2": "World"})

    def test_parse_response_list_shape(self):
        raw = '[{"id": "1", "translation": "Hello"}, {"id": "2", "translation": "World"}]'
        self.assertEqual(_parse_response(raw, {"1", "2"}), {"1": "Hello", "2": "World"})


class StructuredTranslationTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_only_missing_ids_and_maps_reordered_results(self):
        class Translator:
            _RETRY_ATTEMPTS = 2

            def __init__(self):
                self.inputs = []

            async def _request_translation(self, _to_lang, prompt):
                payload = json.loads(prompt.split("Input: ", 1)[1])
                ids = [item["id"] for item in payload["regions"]]
                self.inputs.append(ids)
                if len(self.inputs) == 1:
                    return json.dumps({"translations": [{"id": "b", "translation": "B"}]})
                return json.dumps({"translations": [{"id": "a", "translation": "A"}]})

        translator = Translator()
        result = await translate_structured(translator, "ENG", [("a", "one"), ("b", "two")])

        self.assertEqual({"a": "A", "b": "B"}, result)
        self.assertEqual([["a", "b"], ["a"]], translator.inputs)

    async def test_handles_empty_response_on_first_attempt_and_retries(self):
        class Translator:
            _RETRY_ATTEMPTS = 2

            def __init__(self):
                self.attempts = 0

            async def _request_translation(self, _to_lang, _prompt):
                self.attempts += 1
                if self.attempts == 1:
                    return ""
                return '<think>Translating</think>\n```json\n{"translations": [{"id": "x", "translation": "Done"}]}\n```'

        translator = Translator()
        result = await translate_structured(translator, "ENG", [("x", "test")])
        self.assertEqual({"x": "Done"}, result)
        self.assertEqual(2, translator.attempts)

    async def test_raises_structured_translation_error_when_all_retries_fail(self):
        class Translator:
            _RETRY_ATTEMPTS = 2

            async def _request_translation(self, _to_lang, _prompt):
                return ""

        translator = Translator()
        with self.assertRaises(StructuredTranslationError) as ctx:
            await translate_structured(translator, "ENG", [("x", "test")])
        self.assertEqual({"x"}, ctx.exception.missing)


if __name__ == "__main__":
    unittest.main()
