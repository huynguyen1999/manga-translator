import os
import re
from typing import List, Optional, Dict, Any

from ..config import TranslatorConfig

try:
    import openai
except ImportError:
    openai = None

import asyncio
import time
from .common import MissingAPIKeyException
from .deepseek import DeepseekTranslator
from .keys import (
    OPENROUTER_API_KEY,
    OPENROUTER_API_BASE,
    OPENROUTER_MODEL,
    OPENROUTER_PROVIDER_SORT,
)
from .tokenizers.token_counters import deepseekTokenCounter


class OpenRouterTranslator(DeepseekTranslator):
    """
    OpenRouter translator implementation with support for smart multi-provider routing.
    By default, requests are routed to the cheapest available provider (`provider.sort: "price"`).
    Default model: `deepseek/deepseek-v4-flash-0731`.
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
        resolved_model = model or os.getenv('OPENROUTER_MODEL', OPENROUTER_MODEL)
        self.model = resolved_model
        _config_key = config_key or (f"{self._CONFIG_KEY}.{self.model}" if self.model else self._CONFIG_KEY)

        # Call grandparent/parent init
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

        default_headers = {
            'HTTP-Referer': 'https://github.com/zyddnys/manga-image-translator',
            'X-Title': 'Manga Image Translator',
        }

        self.client = openai.AsyncOpenAI(
            api_key=resolved_key or 'dummy',
            base_url=resolved_base,
            default_headers=default_headers,
        ) if openai else None

        self.token_count = 0
        self.token_count_last = 0
        self.config = None

    def count_tokens(self, text: str) -> int:
        if self.tokenizer is not None:
            try:
                return self.tokenizer.count_tokens(text)
            except Exception:
                pass
        return len(text.encode('utf-8'))

    def _build_provider_config(self) -> Dict[str, Any]:
        cfg = {'order': ['reka', 'inceptron'], 'allow_fallbacks': False}
        # Allow config override if specified in chatgpt_config/gpt_config
        custom_provider = self._config_get('provider', None) if hasattr(self, '_config_get') else None
        if isinstance(custom_provider, dict):
            cfg = {}
            cfg.update(custom_provider)
        if 'sort' not in cfg:
            cfg['sort'] = self.provider_sort
        return cfg

    async def _request_translation(self, to_lang: str, prompt: str) -> str:
        system_message = self._CHAT_SYSTEM_TEMPLATE.format(to_lang=to_lang)
        messages = [
            {'role': 'system', 'content': system_message},
        ]
        lang_chat_samples = self.get_chat_sample(to_lang)
        if lang_chat_samples:
            messages.append({'role': 'user', 'content': lang_chat_samples[0]})
            messages.append({'role': 'assistant', 'content': lang_chat_samples[1]})
        messages.append({'role': 'user', 'content': prompt})

        provider_config = self._build_provider_config()

        is_json_mode = getattr(self, "_professional_json_mode", False)
        kwargs = {
            'model': self.model,
            'messages': messages,
            'max_tokens': self._MAX_TOKENS,
            'temperature': self.temperature,
            'top_p': self.top_p,
            'extra_body': {
                'provider': provider_config,
            },
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

            # Record usage
            usage = getattr(response, 'usage', None)
            total_tokens = getattr(usage, 'total_tokens', None) if usage is not None else None
            if total_tokens is None and isinstance(usage, dict):
                total_tokens = usage.get('total_tokens')

            if total_tokens is None:
                self.logger.warning("Response does not contain usage information")
                self.token_count_last = 0
            else:
                self.token_count += total_tokens
                self.token_count_last = total_tokens

            # Extract response content
            for choice in response.choices:
                if hasattr(choice, 'text') and choice.text:
                    return choice.text
                if isinstance(choice, dict) and 'text' in choice and choice['text']:
                    return choice['text']

            first_choice = response.choices[0] if response.choices else None
            msg = getattr(first_choice, 'message', None) if first_choice is not None else None
            if msg is None and isinstance(first_choice, dict):
                msg = first_choice.get('message')

            if msg is not None:
                reasoning = getattr(msg, 'reasoning_content', None) or getattr(msg, 'reasoning', None)
                if reasoning is None and isinstance(msg, dict):
                    reasoning = msg.get('reasoning_content') or msg.get('reasoning')
                if reasoning:
                    self.logger.debug(
                        "-- OpenRouter Reasoning --\n" +
                        str(reasoning) +
                        "\n--------------------------\n"
                    )

                content = getattr(msg, 'content', None)
                if content is None and isinstance(msg, dict):
                    content = msg.get('content')
                if content is not None:
                    return content

            return response.choices[0].message.content

        except Exception as e:
            self.logger.error(f"Error in OpenRouter _request_translation: {str(e)}")
            raise
