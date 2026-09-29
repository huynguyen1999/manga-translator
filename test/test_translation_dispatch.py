import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from manga_translator.config import Config, Translator
from manga_translator.manga_translator import MangaTranslator
from manga_translator.translators import _dispatch_one, dispatch as dispatch_translation
from manga_translator.translators import _wait_for_gpt_translation, dispatch_structured
from manga_translator.translators.custom_openai import CustomOpenAiTranslator
from manga_translator.utils import Context
from manga_translator.translation_dispatch import dispatch_with_context


def make_translator():
    translator = MangaTranslator.__new__(MangaTranslator)
    translator.all_page_translations = [{'line': 'previous'}]
    translator.context_size = 1
    translator._build_prev_context = Mock(return_value='history')
    translator._mps_call = AsyncMock(return_value=['translated'])
    translator._report_progress = AsyncMock()
    translator.use_mtpe = False
    translator._gpu_limited_memory = True
    translator.device = 'mps'
    return translator


def test_regular_provider_uses_executor_and_reports_model_metadata():
    translator = make_translator()
    config = Config()
    config.translator.translator = Translator.sugoi
    context = Context(
        offline_model='offline-ocr',
        gemini_model='gemini-flash',
        translator_model='sugoi-v1',
    )

    result = asyncio.run(translator._dispatch_with_context(config, ['source'], context))

    assert result == ['translated']
    translator._build_prev_context.assert_called_once_with()
    args = translator._mps_call.await_args.args
    assert args[0] is dispatch_translation
    assert args[2] == ['source']
    assert args[-1] == 'cpu'
    assert translator._report_progress.await_args_list == [
        call('offline_model:offline-ocr'),
        call('gemini_model:gemini-flash'),
        call('translator_model:sugoi-v1'),
    ]


def test_chatgpt_provider_receives_history_without_using_model_executor():
    translator = make_translator()
    translator.all_page_translations = [{'line': ' '}, {'line': 'previous'}]
    config = Config()
    config.translator.translator = Translator.chatgpt
    context = Context(from_lang='JPN')

    provider = Mock()
    provider.model = 'gpt-test'
    provider._translate = AsyncMock(return_value=['translated'])

    with patch('manga_translator.translators.chatgpt.OpenAITranslator', return_value=provider):
        result = asyncio.run(translator._dispatch_with_context(config, ['source'], context))

    assert result == ['translated']
    provider.parse_args.assert_called_once_with(config.translator)
    provider.set_prev_context.assert_called_once_with('history')
    provider._translate.assert_awaited_once_with('JPN', config.translator.target_lang, ['source'])
    assert context.translator_model == 'gpt-test'
    translator._mps_call.assert_not_awaited()


def test_gpt_structured_request_has_a_sixty_second_timeout():
    config = Config(translator={"translator_chain": "custom_openai:ENG"})
    async def slow_request(*_args, **_kwargs):
        await asyncio.sleep(0.02)
        return "{}"

    provider = SimpleNamespace(
        _RETRY_ATTEMPTS=3,
        _request_translation=AsyncMock(side_effect=slow_request),
        parse_args=Mock(),
    )

    with patch(
        "manga_translator.translators.TRANSLATION_REQUEST_TIMEOUT_SECONDS",
        0.001,
    ), patch("manga_translator.translators.get_translator", return_value=provider):
        with pytest.raises(TimeoutError, match="Translator request exceeded 0.001 seconds"):
            asyncio.run(
                dispatch_structured(
                    config.translator.translator_gen,
                    [("region", "source")],
                    config.translator,
                    Context(),
                )
            )
    provider._request_translation.assert_awaited_once()


def test_gpt_page_request_has_a_sixty_second_timeout():
    config = Config(translator={"translator_chain": "custom_openai:ENG"})

    async def slow_request(*_args, **_kwargs):
        await asyncio.sleep(0.02)
        return ["translated"]

    provider = SimpleNamespace(
        translate=AsyncMock(side_effect=slow_request),
        parse_args=Mock(),
    )
    with patch(
        "manga_translator.translators.TRANSLATION_REQUEST_TIMEOUT_SECONDS",
        0.001,
    ), patch("manga_translator.translators.get_translator", return_value=provider):
        with pytest.raises(TimeoutError, match="Translator request exceeded 0.001 seconds"):
            asyncio.run(
                _dispatch_one(
                    Translator.custom_openai,
                    "ENG",
                    ["source"],
                    config.translator,
                    False,
                    Context(),
                    "cpu",
                )
            )
    provider.translate.assert_awaited_once()


def test_gpt_timeout_cancels_the_active_qwen_request():
    translator = CustomOpenAiTranslator.__new__(CustomOpenAiTranslator)
    translator.model = "Qwen"
    translator.client = SimpleNamespace(base_url="http://localhost")
    translator.config = None
    translator.logger = Mock()
    translator._assemble_prompts = Mock(return_value=[("prompt", 1)])
    translator._format_prompt_log = Mock(return_value="prompt")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    translator._request_translation = AsyncMock(side_effect=hang)

    async def run():
        with patch("manga_translator.translators.TRANSLATION_REQUEST_TIMEOUT_SECONDS", 0.001):
            with pytest.raises(TimeoutError, match="Translator request exceeded 0.001 seconds"):
                await _wait_for_gpt_translation(
                    translator._translate("JPN", "ENG", ["source"])
                )
        assert started.is_set()
        await asyncio.sleep(0)
        assert cancelled.is_set()

    asyncio.run(run())
