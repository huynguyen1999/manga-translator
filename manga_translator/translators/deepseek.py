import os
import re

from ..config import TranslatorConfig

try:
    import openai
except ImportError:
    openai = None
import asyncio
import time
from typing import List, Optional
from .common import MissingAPIKeyException, TranslationProviderUnavailable
from .common_gpt import CommonGPTTranslator
from .keys import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_API_BASE,
    DEEPSEEK_MODEL,
)
from .tokenizers.token_counters import deepseekTokenCounter


class DeepseekTranslator(CommonGPTTranslator):
    _FAIL_FAST_CONNECTION_ERRORS = False
    _INVALID_REPEAT_COUNT = 0  # 现在这个参数没意义了
    _MAX_REQUESTS_PER_MINUTE = 9999  # 无RPM限制
    _TIMEOUT = 40  # 在重试之前等待服务器响应的时间（秒）
    _RETRY_ATTEMPTS = 3  # 在放弃之前重试错误请求的次数
    _TIMEOUT_RETRY_ATTEMPTS = 3  # 在放弃之前重试超时请求的次数
    _RATELIMIT_RETRY_ATTEMPTS = 3  # 在放弃之前重试速率限制请求的次数

    # 最大令牌数量，用于控制处理的文本长度
    # Maximum token count for controlling the length of text processed
    # 
    # 最大输出长度: 8K
    # MAX OUTPUT TOKENS: 8K
    # -- https://api-docs.deepseek.com/quick_start/pricing
    _MAX_TOKENS = 8000

    # 将每个 prompt 限制为最大输出 tokens 的 50％。
    # （这是一个任意比率，用于解释语言之间的差异。）
    # 
    # Limit each prompt to 50% max output tokens. 
    # (This is an arbitrary ratio to account for variance between languages.)
    _MAX_TOKENS_IN = _MAX_TOKENS // 2

    # 是否返回原始提示，用于控制输出内容
    _RETURN_PROMPT = False

    # 是否包含模板，用于决定是否使用预设的提示模板
    _INCLUDE_TEMPLATE = False

    def __init__(
        self,
        check_openai_key: bool = True,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        config_key: Optional[str] = None,
        missing_key_msg: str = 'DEEPSEEK_API_KEY environment variable required',
        fallback_to_openai_key: bool = True,
    ):
        resolved_model = model or os.getenv('DEEPSEEK_MODEL', DEEPSEEK_MODEL)
        self.model = resolved_model
        _CONFIG_KEY = config_key or ('deepseek.' + self.model)
        CommonGPTTranslator.__init__(self, config_key=_CONFIG_KEY)

        # Initialize the token counter
        self.tokenizer = deepseekTokenCounter()

        if api_key is not None:
            resolved_key = api_key
        elif fallback_to_openai_key and openai and getattr(openai, 'api_key', None):
            resolved_key = openai.api_key
        else:
            resolved_key = os.getenv('DEEPSEEK_API_KEY', DEEPSEEK_API_KEY)

        if not resolved_key and check_openai_key:
            raise MissingAPIKeyException(missing_key_msg)

        self.client = openai.AsyncOpenAI(api_key=resolved_key or 'dummy') if openai else None

        resolved_base = api_base or os.getenv('DEEPSEEK_API_BASE', DEEPSEEK_API_BASE)
        if self.client:
            self.client.base_url = resolved_base
        self.token_count = 0
        self.token_count_last = 0
        self.config = None

    def count_tokens(self, text: str):
        """
        通过字符估计标记很困难，并且因语言而异:
        - 1 个英文字符 ≈ 0.3 个 token。
        - 1 个中文字符 ≈ 0.6 个 token。
        -- https://api-docs.deepseek.com/zh-cn/quick_start/token_usage
        
        因此：使用 deepseek 的 tokenizer 来准确计算 token 的数量。
        
        Estimating tokens by characters is tricky and varies by language:
        - 1 English character ≈ 0.3 token.
        - 1 Chinese character ≈ 0.6 token.
        -- https://api-docs.deepseek.com/quick_start/token_usage
        
        Thus: Use deepseek's tokenizer to accurately count tokens.
        """
        return self.tokenizer.count_tokens(text)


    def _format_prompt_log(self, to_lang: str, prompt: str) -> str:
        prompt = prompt.strip()  
        if to_lang in self.chat_sample:
            return '\n'.join([
                'System:',
                self.chat_system_template.format(to_lang=to_lang),
                'User:',
                self.chat_sample[to_lang][0],
                'Assistant:',
                self.chat_sample[to_lang][1],
                'User:',
                prompt,
            ])
        else:
            return '\n'.join([
                'System:',
                self.chat_system_template.format(to_lang=to_lang),
                'User:',
                prompt,
            ])

    async def _await_translation_with_timeout(self, to_lang: str, prompt: str) -> str:
        request_task = asyncio.create_task(self._request_translation(to_lang, prompt))
        started = time.time()
        timeout_attempt = 0
        while not request_task.done():
            await asyncio.sleep(0.1)
            if time.time() - started > self._TIMEOUT + (timeout_attempt * self._TIMEOUT / 2):
                if timeout_attempt >= self._TIMEOUT_RETRY_ATTEMPTS:
                    raise Exception('deepseek servers did not respond quickly enough.')
                timeout_attempt += 1
                self.logger.warning(f'Restarting request due to timeout. Attempt: {timeout_attempt}')
                request_task.cancel()
                request_task = asyncio.create_task(self._request_translation(to_lang, prompt))
                started = time.time()
        return await request_task

    def _parse_deepseek_batch_response(
        self, response: str, prompt_queries: List[str], query_size: int, attempt: int
    ) -> Optional[List[str]]:
        new_translations = re.split(r'<\|\d+\|>', response)
        new_translations = [t.strip() for t in new_translations]
        if new_translations and not new_translations[0].strip():
            new_translations = new_translations[1:]

        if len(prompt_queries) == 1 and len(new_translations) == 1 and not re.match(r'^\s*<\|\d+\|>', response):
            self.logger.warning(f'Single query response does not contain prefix, retrying...(Attempt {attempt + 1})')
            return None

        if len(new_translations) < query_size:
            new_translations = re.split(r'\n', response)

        if len(new_translations) < query_size:
            return None

        new_translations = new_translations[:query_size] + [''] * (query_size - len(new_translations))
        new_translations = [t.split('\n')[0].strip() for t in new_translations]
        new_translations = [re.sub(r'^\s*<\|\d+\|>\s*', '', t) for t in new_translations]
        return new_translations

    async def _split_or_fail_deepseek_batch(
        self,
        from_lang: str,
        to_lang: str,
        prompt_queries: List[str],
        prompt_query_indices: List[int],
        split_level: int,
        max_split_attempts: int,
        translations: List[str],
        all_queries: List[str],
    ) -> bool:
        if split_level < max_split_attempts:
            if split_level == 0:
                self.logger.warning('Retry limit reached. Starting to split the translation batch.')
            else:
                self.logger.warning('Further splitting the translation batch due to persistent errors.')
            mid_index = len(prompt_queries) // 2
            futures = []
            for sub_queries, sub_indices in [
                (prompt_queries[:mid_index], prompt_query_indices[:mid_index]),
                (prompt_queries[mid_index:], prompt_query_indices[mid_index:]),
            ]:
                if sub_queries:
                    futures.append(self._translate_batch(
                        from_lang, to_lang, sub_queries, sub_indices, translations, all_queries, split_level + 1, max_split_attempts
                    ))
            results = await asyncio.gather(*futures)
            return all(results)

        self.logger.error('Maximum split attempts reached. Unable to translate the following queries:')
        for idx in prompt_query_indices:
            self.logger.error(f'Query: {all_queries[idx]}')
        return False

    async def _translate_batch(
        self,
        from_lang: str,
        to_lang: str,
        prompt_queries: List[str],
        prompt_query_indices: List[int],
        translations: List[str],
        all_queries: List[str],
        split_level: int = 0,
        max_split_attempts: int = 5,
    ) -> bool:
        split_prefix = ' (split)' if split_level > 0 else ''
        prompt, query_size = self._assemble_prompts(from_lang, to_lang, prompt_queries).__next__()
        self.logger.debug(f'-- GPT Prompt{split_prefix} --\n' + self._format_prompt_log(to_lang, prompt))

        server_error_attempt = 0
        retry_attempts = self._RETRY_ATTEMPTS

        for attempt in range(retry_attempts):
            try:
                response = await self._await_translation_with_timeout(to_lang, prompt)
                self.logger.debug(f'-- GPT Response{split_prefix} --\n' + response)

                new_translations = self._parse_deepseek_batch_response(response, prompt_queries, query_size, attempt)
                if new_translations is None:
                    remaining_attempts = retry_attempts - attempt - 1
                    self.logger.warning(f'Incomplete response, remaining {remaining_attempts} time(s) before splitting the translation.')
                    continue

                if any(not t.strip() for t in new_translations):
                    self.logger.warning('Empty translations detected. Resplitting the batch.')
                    break

                for idx, translation in zip(prompt_query_indices, new_translations):
                    translations[idx] = translation

                self.logger.info(f'Batch translated: {len([t for t in translations if t])}/{len(all_queries)} completed.')
                self.logger.debug(f'Completed translations: {[t if t else all_queries[i] for i, t in enumerate(translations)]}')
                return True

            except (openai.APIConnectionError, OSError) as e:
                if self._FAIL_FAST_CONNECTION_ERRORS:
                    raise TranslationProviderUnavailable(
                        f'DeepSeek API is unavailable at {str(self.client.base_url).rstrip("/")}. '
                        'Check the API configuration or select another translator.'
                    ) from e
                server_error_attempt += 1
                if server_error_attempt >= retry_attempts:
                    raise
                self.logger.warning(f'Restarting request due to a connection error. Attempt: {server_error_attempt}')
                await asyncio.sleep(1)
            except openai.APIError:
                server_error_attempt += 1
                if server_error_attempt >= retry_attempts:
                    self.logger.error(
                        'Deepseek encountered a server error, possibly due to high server load. Use a different translator or try again later.')
                    raise
                self.logger.warning(f'Restarting request due to a server error. Attempt: {server_error_attempt}')
                await asyncio.sleep(1)
            except Exception as e:
                self.logger.error(f'Error during translation attempt: {e}')
                if attempt == retry_attempts - 1:
                    raise
                await asyncio.sleep(1)

        return await self._split_or_fail_deepseek_batch(
            from_lang, to_lang, prompt_queries, prompt_query_indices, split_level, max_split_attempts, translations, all_queries
        )

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:  
        translations = [''] * len(queries)  
        self.logger.debug(f'Temperature: {self.temperature}, TopP: {self.top_p}')  

        prompt_queries = queries  
        prompt_query_indices = list(range(len(queries)))  
        await self._translate_batch(from_lang, to_lang, prompt_queries, prompt_query_indices, translations, queries)

        self.logger.debug(translations)  
        if self.token_count_last:  
            self.logger.info(f'Used {self.token_count_last} tokens (Total: {self.token_count})')  
        return translations

    async def _request_translation(self, to_lang: str, prompt: str) -> str:
        system_message = self._CHAT_SYSTEM_TEMPLATE.format(to_lang=to_lang) 
        messages = [  
            {'role': 'system', 'content': system_message},  
        ]  
        lang_chat_samples = self.get_chat_sample(to_lang)
        if lang_chat_samples:
            messages.append({'role': 'user', 'content': lang_chat_samples[0]})
            messages.append({'role': 'assistant', 'content': lang_chat_samples[1]})
        messages.append({"role": "user", "content": prompt})

        is_json_mode = getattr(self, "_professional_json_mode", False)
        kwargs = {
            'model': self.model,
            'messages': messages,
            
            # `max_tokens` only affects output token length. Set to max.
            'max_tokens': self._MAX_TOKENS, 
            
            'temperature': self.temperature,
            'top_p': self.top_p,
        }
        if is_json_mode:
            kwargs['response_format'] = {'type': 'json_object'}

        try:
            try:
                if hasattr(self.client, 'beta') and hasattr(self.client.beta, 'chat') and hasattr(self.client.beta.chat.completions, 'parse'):
                    try:
                        response = await self.client.beta.chat.completions.parse(**kwargs)
                    except (openai.APIConnectionError, OSError):
                        raise
                    except Exception:
                        response = await self.client.chat.completions.create(**kwargs)
                else:
                    response = await self.client.chat.completions.create(**kwargs)
            except Exception as e:
                if is_json_mode and "response_format" in str(e).lower():
                    kwargs.pop("response_format", None)
                    response = await self.client.chat.completions.create(**kwargs)
                else:
                    raise
            
            # 添加错误处理和日志
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
            
            # 获取响应文本
            # Get the response text
            for choice in response.choices:
                if hasattr(choice, 'text') and choice.text:
                    return choice.text
                if isinstance(choice, dict) and 'text' in choice and choice['text']:
                    return choice['text']

            # 如果响应中包含推理内容，记录下来
            # Log reasoning content if available
            first_choice = response.choices[0] if response.choices else None
            msg = getattr(first_choice, 'message', None) if first_choice is not None else None
            if msg is None and isinstance(first_choice, dict):
                msg = first_choice.get('message')

            if msg is not None:
                reasoning = getattr(msg, 'reasoning_content', None)
                if reasoning is None and isinstance(msg, dict):
                    reasoning = msg.get('reasoning_content')
                if reasoning:
                    self.logger.debug("-- GPT Reasoning --\n" +
                                    str(reasoning) +
                                    "\n------------------\n"
                                )
                
                content = getattr(msg, 'content', None)
                if content is None and isinstance(msg, dict):
                    content = msg.get('content')
                if content is not None:
                    return content
                
            # If no response with text is found, return the first response's content (which may be empty)
            # 如果没有找到包含文本的响应，则返回第一个响应的内容（可能为空）
            return response.choices[0].message.content
        
        except Exception as e:
            self.logger.error(f"Error in _request_translation: {str(e)}")
            raise
