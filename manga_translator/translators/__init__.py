import asyncio
import threading
from typing import Optional, List

try:
    import py3langid as langid
except ImportError:
    langid = None

from .common import *
from .baidu import BaiduTranslator
from .deepseek import DeepseekTranslator
from .youdao import YoudaoTranslator
from .deepl import DeeplTranslator
from .caiyun import CaiyunTranslator
from .chatgpt import OpenAITranslator
from .sugoi import SugoiTranslator
from .none import NoneTranslator
from .original import OriginalTranslator
from .sakura import SakuraTranslator
from .groq import GroqTranslator
from .gemini import GeminiTranslator
from .gemini_keys import GeminiRequestBudget, _CURRENT_RETRY_BUDGET
from .custom_openai import CustomOpenAiTranslator
from .openrouter import OpenRouterTranslator
from .structured import translate_structured
from ..config import Translator, TranslatorConfig, TranslatorChain
from ..utils import Context
from ..utils.model_cache import (
    get_cached_model, get_model_cache, model_operation, remove_cached_model,
    unload_cached_model,
)

OFFLINE_TRANSLATORS = {
    Translator.sugoi: SugoiTranslator,
}

GPT_TRANSLATORS = {
    Translator.chatgpt: OpenAITranslator,
    Translator.deepseek: DeepseekTranslator,
    Translator.groq: GroqTranslator,
    Translator.custom_openai: CustomOpenAiTranslator,
    Translator.openrouter: OpenRouterTranslator,
    Translator.gemini: GeminiTranslator,
}


TRANSLATORS = {
    Translator.youdao: YoudaoTranslator,
    Translator.baidu: BaiduTranslator,
    Translator.deepl: DeeplTranslator,
    Translator.caiyun: CaiyunTranslator,
    Translator.none: NoneTranslator,
    Translator.original: OriginalTranslator,
    Translator.sakura: SakuraTranslator,
    **GPT_TRANSLATORS,
    **OFFLINE_TRANSLATORS,
}
translator_cache = {}
_OFFLINE_TRANSLATOR_LOCK = threading.Lock()

def get_translator(key: Translator, *args, **kwargs) -> CommonTranslator:
    if key not in TRANSLATORS:
        raise ValueError(f'Could not find translator for: "{key}". Choose from the following: %s' % ','.join(TRANSLATORS))
    return get_cached_model('translator', translator_cache, key, lambda: TRANSLATORS[key](*args, **kwargs))


async def _translate_with_context(
    key, translator, from_lang: str, to_lang: str, queries: List[str], use_mtpe: bool, args: Optional[Context]
) -> List[str]:
    if key == "gemini":
        budget = args.get('_gemini_retry_budget') if args is not None else None
        if budget is None:
            budget = GeminiRequestBudget()
            if args is not None:
                args['_gemini_retry_budget'] = budget
        token = _CURRENT_RETRY_BUDGET.set(budget)
        try:
            return await translator.translate(from_lang, to_lang, queries, use_mtpe)
        finally:
            _CURRENT_RETRY_BUDGET.reset(token)

    return await translator.translate(from_lang, to_lang, queries, use_mtpe)

async def prepare(chain: TranslatorChain):
    for key, tgt_lang in chain.chain:
        async def prepare_one():
            translator = get_translator(key)
            translator.supports_languages('auto', tgt_lang, fatal=True)
            if isinstance(translator, OfflineTranslator):
                await translator.download()
        if key in OFFLINE_TRANSLATORS:
            await _run_offline_operation(prepare_one)
        else:
            await prepare_one()


@model_operation
async def _offline_operation(operation):
    return await operation()


async def _run_offline_operation(operation):
    # ponytail: one lock covers the current sole offline backend; split by model if more need parallelism.
    acquire = asyncio.create_task(asyncio.to_thread(_OFFLINE_TRANSLATOR_LOCK.acquire))
    try:
        await asyncio.shield(acquire)
    except asyncio.CancelledError:
        await acquire
        _OFFLINE_TRANSLATOR_LOCK.release()
        raise
    try:
        return await _offline_operation(operation)
    finally:
        _OFFLINE_TRANSLATOR_LOCK.release()


async def _dispatch_one(key, tgt_lang, queries, translator_config, use_mtpe, args, device, unload=False):
    async def translate_one():
        translator = get_translator(key)
        if isinstance(translator, OfflineTranslator):
            await translator.load('auto', tgt_lang, device)
        if translator_config:
            translator.parse_args(translator_config)
        translated = await _translate_with_context(key, translator, 'auto', tgt_lang, queries, use_mtpe, args)
        if args is not None:
            if isinstance(translator, OfflineTranslator):
                args['offline_model'] = getattr(translator, 'model_name', translator.__class__.__name__)
            if hasattr(translator, 'key_manager'):
                model = getattr(translator.key_manager, 'current_model', None)
                if model:
                    args['gemini_model'] = model
            model = getattr(translator, 'model_name', None) or getattr(translator, 'model', None) or getattr(translator, 'MODEL', None)
            if isinstance(model, str):
                args['translator_model'] = model
        if unload:
            await translator.unload(device)
        return translated

    # API clients keep worker-local state and never hold the shared model lane.
    if key in OFFLINE_TRANSLATORS:
        return await _run_offline_operation(translate_one)
    return await translate_one()

# TODO: Optionally take in strings instead of TranslatorChain for simplicity
async def dispatch(chain: TranslatorChain, queries: List[str], translator_config: Optional[TranslatorConfig] = None, use_mtpe: bool = False, args:Optional[Context] = None, device: str = 'cpu') -> List[str]:
    if not queries:
        return queries

    if chain.target_lang is not None:
        text_lang = ISO_639_1_TO_VALID_LANGUAGES.get(langid.classify('\n'.join(queries))[0]) if langid is not None else None
        for key, lang in chain.chain:
            queries = await _dispatch_one(
                key, lang, queries, translator_config, use_mtpe, args, device, unload=True
            )
        return queries
    if args is not None:
        args['translations'] = {}
    for key, tgt_lang in chain.chain:
        queries = await _dispatch_one(key, tgt_lang, queries, translator_config, use_mtpe, args, device)
        if args is not None:
            args['translations'][tgt_lang] = queries
    return queries


async def dispatch_structured(
    chain: TranslatorChain,
    items: list[tuple[str, str]],
    translator_config: Optional[TranslatorConfig] = None,
    args: Optional[Context] = None,
    device: str = "cpu",
) -> dict[str, str]:
    """Translate ID-tagged values while preserving IDs through translator chains."""
    values = dict(items)
    for key, target_lang in chain.chain:
        translator = get_translator(key)
        if translator_config:
            translator.parse_args(translator_config)
        if key in GPT_TRANSLATORS:
            values = await translate_structured(translator, target_lang, values.items())
        else:
            translated = await _translate_with_context(
                key, translator, "auto", target_lang, list(values.values()), False, args
            )
            if len(translated) != len(values):
                raise ValueError("Translator returned an incorrect number of results")
            values = dict(zip(values, translated))
        if args is not None:
            model = getattr(translator, "model_name", None) or getattr(translator, "model", None) or getattr(translator, "MODEL", None)
            if isinstance(model, str):
                args["translator_model"] = model
    return values


async def dispatch_batch(chain: TranslatorChain, batch_queries: List[List[str]], translator_config: Optional[TranslatorConfig] = None, use_mtpe: bool = False, args:Optional[Context] = None, device: str = 'cpu') -> List[List[str]]:
    """
    批量翻译调度器，将多个文本列表一次性发送给翻译器
    Args:
        chain: 翻译器链
        batch_queries: 批量查询列表，每个元素是一个字符串列表
        translator_config: 翻译器配置
        use_mtpe: 是否使用机器翻译后编辑
        args: 上下文参数
        device: 设备
    Returns:
        批量翻译结果列表
    """
    if not batch_queries or not any(batch_queries):
        return batch_queries
    
    # 将批量查询平铺为单一列表
    flat_queries = []
    query_mapping = []  # 记录每个查询属于哪个批次
    
    for batch_idx, queries in enumerate(batch_queries):
        for query in queries:
            flat_queries.append(query)
            query_mapping.append(batch_idx)
    
    # 使用现有的翻译调度器处理平铺的查询列表
    flat_results = await dispatch(chain, flat_queries, translator_config, use_mtpe, args, device)
    
    # 将结果重新分组回批量结构
    batch_results = [[] for _ in batch_queries]
    for result, batch_idx in zip(flat_results, query_mapping):
        batch_results[batch_idx].append(result)
    
    return batch_results

LANGDETECT_MAP = {
    'zh-cn': 'CHS',
    'zh-tw': 'CHT',
    'cs': 'CSY',
    'nl': 'NLD',
    'en': 'ENG',
    'fr': 'FRA',
    'de': 'DEU',
    'hu': 'HUN',
    'it': 'ITA',
    'ja': 'JPN',
    'ko': 'KOR',
    'pl': 'POL',
    'pt': 'PTB',
    'ro': 'ROM',
    'ru': 'RUS',
    'es': 'ESP',
    'tr': 'TRK',
    'uk': 'UKR',
    'vi': 'VIN',
    'ar': 'ARA',
    'hr': 'HRV',
    'th': 'THA',
    'id': 'IND',
    'tl': 'FIL'
}

async def unload(key: Translator):
    if key in OFFLINE_TRANSLATORS:
        async def unload_one():
            translator = get_model_cache('translator', translator_cache).get(key)
            if translator is not None and translator.is_loaded():
                await translator.unload(getattr(translator, '_requested_device', None))
            remove_cached_model('translator', translator_cache, key)
        await _offline_operation(unload_one)
        return
    await unload_cached_model('translator', translator_cache, key)
