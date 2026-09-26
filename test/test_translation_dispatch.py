import asyncio
from unittest.mock import AsyncMock, Mock, call, patch

from manga_translator.config import Config, Translator
from manga_translator.manga_translator import MangaTranslator
from manga_translator.translators import dispatch as dispatch_translation
from manga_translator.utils import Context


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
