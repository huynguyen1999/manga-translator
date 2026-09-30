import os
import re
import asyncio
from contextvars import ContextVar
from typing import List, Optional, Dict, Any

try:
    import openai
except ImportError:
    openai = None

from .common import MissingAPIKeyException
from .deepseek import DeepseekTranslator
from .keys import (
    OPENROUTER_API_KEY,
    OPENROUTER_API_BASE,
    OPENROUTER_MODEL,
    OPENROUTER_MODELS,
    OPENROUTER_PROVIDER_SORT,
)
from .tokenizers.token_counters import deepseekTokenCounter

_REQUEST_MODEL = ContextVar("openrouter_request_model", default=None)
FALLBACK_MODELS = list(OPENROUTER_MODELS.values())


class OpenRouterTranslator(DeepseekTranslator):
    """
    OpenRouter translator implementation with fallback support across free models.
    Default model: `qwen/qwen3.8-27b:free`.
    Fallback chain: qwen -> inkling -> nemotron_ultra -> nemotron_lightning.
    """
    _CONFIG_KEY = 'openrouter'

    def __init__(
        self,
        check_openai_key: bool = True,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        provider_sort: Optional[str] = None,
        config_key: Optional[str] = None,
    ):
        raw_model = model or os.getenv('OPENROUTER_MODEL', OPENROUTER_MODEL)
        self.model = OPENROUTER_MODELS.get(raw_model, raw_model) or OPENROUTER_MODELS['qwen']
        _config_key = config_key or (f"{self._CONFIG_KEY}.{self.model}" if self.model else self._CONFIG_KEY)
        super(DeepseekTranslator, self).__init__(config_key=_config_key)
        try:
            self.tokenizer = deepseekTokenCounter()
        except Exception:
            self.tokenizer = None
        resolved_key = api_key if api_key is not None else os.getenv('OPENROUTER_API_KEY', OPENROUTER_API_KEY)
        if not resolved_key and check_openai_key:
            raise MissingAPIKeyException('Please set the OPENROUTER_API_KEY environment variable before using the OpenRouter translator.')
        resolved_base = api_base or os.getenv('OPENROUTER_API_BASE', OPENROUTER_API_BASE)
        self.provider_sort = provider_sort or os.getenv('OPENROUTER_PROVIDER_SORT', OPENROUTER_PROVIDER_SORT) or 'price'
        default_headers = {'HTTP-Referer': 'https://github.com/zyddnys/manga-image-translator', 'X-Title': 'Manga Image Translator'}
        self.client = openai.AsyncOpenAI(api_key=resolved_key or 'dummy', base_url=resolved_base, default_headers=default_headers) if openai else None
        self.token_count, self.token_count_last, self.config = 0, 0, None

    def _get_fallback_chain(self, start_model: Optional[str] = None) -> List[str]:
        target = OPENROUTER_MODELS.get(start_model, start_model) if start_model else self.model
        chain: List[str] = []
        for m in [target] + FALLBACK_MODELS:
            if m and m not in chain:
                chain.append(m)
        return chain

    def count_tokens(self, text: str) -> int:
        if self.tokenizer is not None:
            try:
                return self.tokenizer.count_tokens(text)
            except Exception:
                pass
        return len(text.encode('utf-8'))

    def _build_provider_config(self) -> Dict[str, Any]:
        cfg = {'order': ['reka', 'inceptron'], 'allow_fallbacks': False}
        custom_provider = self._config_get('provider', None) if hasattr(self, '_config_get') else None
        if isinstance(custom_provider, dict):
            cfg = dict(custom_provider)
        if 'sort' not in cfg:
            cfg['sort'] = self.provider_sort
        return cfg

    async def _request_single_model(self, to_lang: str, prompt: str, model: str) -> str:
        messages = [{'role': 'system', 'content': self._CHAT_SYSTEM_TEMPLATE.format(to_lang=to_lang)}]
        lang_chat_samples = self.get_chat_sample(to_lang)
        if lang_chat_samples:
            messages.extend([{'role': 'user', 'content': lang_chat_samples[0]}, {'role': 'assistant', 'content': lang_chat_samples[1]}])
        messages.append({'role': 'user', 'content': prompt})
        is_json_mode = getattr(self, "_professional_json_mode", False)
        kwargs = {
            'model': model, 'messages': messages, 'max_tokens': self._MAX_TOKENS,
            'temperature': self.temperature, 'top_p': self.top_p, 'extra_body': {'provider': self._build_provider_config()},
        }
        if is_json_mode:
            kwargs['response_format'] = {'type': 'json_object'}
        try:
            try:
                response = await self.client.chat.completions.create(**kwargs)
            except Exception as e:
                if is_json_mode and "response_format" in str(e).lower():
                    kwargs.pop("response_format", None)
                    response = await self.client.chat.completions.create(**kwargs)
                else:
                    raise
            usage = getattr(response, 'usage', None)
            total_tokens = getattr(usage, 'total_tokens', None) if usage is not None else (usage.get('total_tokens') if isinstance(usage, dict) else None)
            if total_tokens is not None:
                self.token_count += total_tokens
                self.token_count_last = total_tokens
            for choice in response.choices:
                text = getattr(choice, 'text', None) or (choice.get('text') if isinstance(choice, dict) else None)
                if text:
                    return text
            first = response.choices[0] if response.choices else None
            msg = getattr(first, 'message', first) if first else None
            if isinstance(msg, dict):
                reasoning = msg.get('reasoning_content') or msg.get('reasoning')
                if reasoning:
                    self.logger.debug(f"-- OpenRouter Reasoning --\n{reasoning}\n--------------------------\n")
                return msg.get('content') or ''
            if msg is not None:
                reasoning = getattr(msg, 'reasoning_content', None) or getattr(msg, 'reasoning', None)
                if reasoning:
                    self.logger.debug(f"-- OpenRouter Reasoning --\n{reasoning}\n--------------------------\n")
                return getattr(msg, 'content', None) or ''
            return response.choices[0].message.content
        except Exception as e:
            self.logger.error(f"Error in OpenRouter _request_single_model ({model}): {str(e)}")
            raise

    async def _request_translation(self, to_lang: str, prompt: str, model: Optional[str] = None) -> str:
        req_model = model or _REQUEST_MODEL.get()
        if req_model:
            return await self._request_single_model(to_lang, prompt, req_model)
        last_exception = None
        for candidate in self._get_fallback_chain():
            try:
                return await self._request_single_model(to_lang, prompt, candidate)
            except Exception as e:
                last_exception = e
                self.logger.warning(f"OpenRouter model {candidate} failed: {e}; falling back to next model")
        if last_exception:
            raise last_exception
        raise RuntimeError("No OpenRouter models available to satisfy request.")

    async def _translate_with_model(self, model: str, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:
        token = _REQUEST_MODEL.set(model)
        try:
            translations = [''] * len(queries)
            success = await self._translate_batch(from_lang, to_lang, queries, list(range(len(queries))), translations, queries)
            if not success or any(not t for t in translations):
                raise RuntimeError(f"OpenRouter model {model} failed to translate batch")
            return translations
        finally:
            _REQUEST_MODEL.reset(token)

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:
        last_exception = None
        for candidate in self._get_fallback_chain():
            try:
                self.logger.info(f"Attempting translation with OpenRouter model: {candidate}")
                return await self._translate_with_model(candidate, from_lang, to_lang, queries)
            except Exception as e:
                last_exception = e
                self.logger.warning(f"OpenRouter model {candidate} failed: {e}; falling back to next model")
        if last_exception:
            raise last_exception
        raise RuntimeError("All OpenRouter models failed to translate.")
