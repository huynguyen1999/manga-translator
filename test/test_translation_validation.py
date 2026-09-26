import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from manga_translator.config import Translator
from manga_translator.manga_translator import MangaTranslator


def test_repetition_checker_keeps_async_translator_method():
    translator = MangaTranslator.__new__(MangaTranslator)

    assert asyncio.run(translator._check_repetition_hallucination('aaaaa', silent=True))
    assert asyncio.run(translator._check_repetition_hallucination(
        'one two one two one two one two one two', silent=True
    ))
    assert not asyncio.run(translator._check_repetition_hallucination('A normal sentence.', silent=True))


def test_target_language_ratio_uses_classifier_for_large_pages():
    translator = MangaTranslator.__new__(MangaTranslator)
    regions = [SimpleNamespace(translation='Hello') for _ in range(11)]
    classifier = Mock()
    classifier.classify.return_value = ('en', 0.9)

    with patch('manga_translator.translation_validation.langid', classifier):
        assert asyncio.run(translator._check_target_language_ratio(regions, 'ENG'))

    classifier.classify.assert_called_once_with('Hello' * 11)


def test_target_language_ratio_keeps_small_page_fast_path_after_preserved_filter():
    translator = MangaTranslator.__new__(MangaTranslator)
    regions = [SimpleNamespace(translation='Hello') for _ in range(10)]
    regions.append(SimpleNamespace(translation='preserved', translation_policy='preserve'))
    classifier = Mock()

    with patch('manga_translator.translation_validation.langid', classifier):
        assert asyncio.run(translator._check_target_language_ratio(regions, 'ENG'))

    classifier.classify.assert_not_called()


def test_translation_validation_keeps_disabled_check_fast_path():
    translator = MangaTranslator.__new__(MangaTranslator)
    translator._check_target_language_ratio = AsyncMock(side_effect=AssertionError)
    translator._check_repetition_hallucination = AsyncMock(side_effect=AssertionError)
    config = SimpleNamespace(translator=SimpleNamespace(enable_post_translation_check=False))

    assert asyncio.run(translator._validate_translation('hello', 'Hello', 'ENG', config))


def test_translation_retry_reuses_a_valid_translation():
    translator = MangaTranslator.__new__(MangaTranslator)
    translator._validate_translation = AsyncMock(return_value=True)
    region = SimpleNamespace(text='hello', translation='Hello')
    config = SimpleNamespace(translator=SimpleNamespace(
        post_check_max_retry_attempts=2,
        target_lang='ENG',
        translator=Translator.none,
    ))

    result = asyncio.run(translator._retry_translation_with_validation(region, config, None))

    assert result == 'Hello'
    translator._validate_translation.assert_awaited_once_with(
        'hello', 'Hello', 'ENG', config, ctx=None, silent=True, page_lang_check_result=True
    )
