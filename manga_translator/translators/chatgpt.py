import re
import os
import asyncio
import time
import string
from typing import List, Dict
from rich.console import Console  
from .config_gpt import ConfigGPT
from .common import CommonTranslator, MissingAPIKeyException, VALID_LANGUAGES
from .keys import OPENAI_API_KEY, OPENAI_HTTP_PROXY, OPENAI_API_BASE, OPENAI_MODEL, OPENAI_GLOSSARY_PATH
from .constants import (
    SUSPICIOUS_HALLUCINATION_SYMBOLS,
    RE_INDEX_TAG,
    RE_INDEX_LINE_PREFIX,
    RE_INDEX_LINE_START,
    RE_CONSECUTIVE_NEWLINES,
    RE_THINK_TAGS,
    REGEX_ESCAPE_CHARS,
    KATAKANA_SMALL_TO_NORMAL,
)
from ..utils.model_cache import get_model_cache

try:
    import openai
except ImportError:
    openai = None


_client_cache = {}

def _get_openai_client(api_key: str, base_url: str, proxy: str):
    cache_key = (api_key, base_url, proxy)
    cache = get_model_cache('openai_client', _client_cache)
    if cache_key not in cache:
        client_args = {
            "api_key": api_key,
            "base_url": base_url,
        }
        if proxy:
            from httpx import AsyncClient
            client_args["http_client"] = AsyncClient(proxies={
                "all://*openai.com": f"http://{proxy}"
            })
        cache[cache_key] = openai.AsyncOpenAI(**client_args)
    return cache[cache_key]


def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def normalize_japanese(text: str) -> str:
    result = ""
    for char in text:
        if char in KATAKANA_SMALL_TO_NORMAL:
            char = KATAKANA_SMALL_TO_NORMAL[char]
        if 0x30A0 <= ord(char) <= 0x30FF:
            result += chr(ord(char) - 0x60)
        else:
            result += char
    return result


def japanese_levenshtein_distance(s1: str, s2: str) -> int:
    return levenshtein_distance(normalize_japanese(s1), normalize_japanese(s2))


def normalize_term(term: str) -> str:
    term = re.sub(r'[^\w\s]', '', term).lower()
    return normalize_japanese(term)


def partial_match(text: str, term: str) -> bool:
    return normalize_term(term) in normalize_term(text)


def is_japanese_similar(text: str, term: str, threshold: int = 2) -> bool:
    normalized_text = normalize_term(text)
    normalized_term = normalize_term(term)
    if len(normalized_term) <= 2:
        threshold = 0
    elif len(normalized_term) <= 4:
        threshold = 1
    return japanese_levenshtein_distance(normalized_text, normalized_term) <= threshold


def is_general_similar(text: str, term: str, threshold: int = 2) -> bool:
    normalized_text = normalize_term(text)
    normalized_term = normalize_term(term)
    threshold = max(0, min(len(normalized_term) // 8, 3))

    if len(normalized_text) > len(normalized_term) * 5:
        min_distance = float('inf')
        if len(normalized_term) <= 8:
            window_size = len(normalized_term)
        elif len(normalized_term) <= 16:
            window_size = len(normalized_term) + 1
        else:
            window_size = len(normalized_term) + 2
        for i in range(max(0, len(normalized_text) - window_size + 1)):
            window = normalized_text[i:i + window_size]
            min_distance = min(min_distance, levenshtein_distance(window, normalized_term))
        return min_distance <= threshold

    return levenshtein_distance(normalized_text, normalized_term) <= threshold


class OpenAITranslator(ConfigGPT, CommonTranslator):
    _LANGUAGE_CODE_MAP = VALID_LANGUAGES
    
    # 类级别的标志，用于跟踪是否已经显示过术语表警告
    _glossary_warning_shown = False

    # ---- 关键参数 ----
    _MAX_REQUESTS_PER_MINUTE = 0
    _TIMEOUT = 999                # 每次请求的超时时间
    _RETRY_ATTEMPTS = 2          # 对同一个批次的最大整体重试次数
    _TIMEOUT_RETRY_ATTEMPTS = 3  # 请求因超时被取消后，最大尝试次数
    _RATELIMIT_RETRY_ATTEMPTS = 3# 遇到 429 等限流时的最大尝试次数
    _MAX_SPLIT_ATTEMPTS = 3      # 递归拆分批次的最大层数
    _MAX_TOKENS = 8192           # prompt+completion 的最大 token (可按模型类型调整)

    def __init__(self, check_openai_key=True):
        # ConfigGPT 的初始化
        _CONFIG_KEY = 'chatgpt.' + OPENAI_MODEL
        ConfigGPT.__init__(self, config_key=_CONFIG_KEY)
        CommonTranslator.__init__(self)

        if not OPENAI_API_KEY and check_openai_key:
            raise MissingAPIKeyException('OPENAI_API_KEY environment variable required')

        # 根据代理与基础URL等参数实例化或复用 openai.AsyncOpenAI 客户端
        self.client = _get_openai_client(OPENAI_API_KEY, OPENAI_API_BASE, OPENAI_HTTP_PROXY)
        self.token_count = 0
        self.token_count_last = 0
        self._last_request_ts = 0
        
        # 初始化术语表相关属性
        self.dict_path = OPENAI_GLOSSARY_PATH
        self.glossary_entries = {}
        
        # 检查用户是否明确设置了glossary环境变量
        user_set_glossary = os.getenv('OPENAI_GLOSSARY_PATH') is not None
        
        if os.path.exists(self.dict_path):
            self.glossary_entries = self.load_glossary(self.dict_path)
        elif user_set_glossary:
            # 只有在用户明确设置了环境变量时才显示警告
            if not OpenAITranslator._glossary_warning_shown:
                self.logger.warning(f"The glossary file does not exist: {self.dict_path}")
                OpenAITranslator._glossary_warning_shown = True

        # 添加 rich 的 Console 对象  
        import sys
        mt = sys.modules.get('manga_translator.manga_translator')
        if mt and hasattr(mt, '_global_console') and mt._global_console:
            self.console = mt._global_console
        else:
            self.console = Console()  
        self.prev_context = ""
        # 可选的回退模型（通过环境变量 OPENAI_FALLBACK_MODEL 指定）
        self._fallback_model = os.getenv("OPENAI_FALLBACK_MODEL")

    def set_prev_context(self, text: str = ""):
        self.prev_context = text or ""     

    def parse_args(self, args: CommonTranslator):
        """如果你有外部参数要解析，可在此对 self.config 做更新"""
        self.config = args.chatgpt_config

    async def _ratelimit_sleep(self):
        """
        在请求前先做一次简单的节流 (如果 _MAX_REQUESTS_PER_MINUTE > 0)。
        针对并发请求进行优化。
        """
        if self._MAX_REQUESTS_PER_MINUTE > 0:
            now = time.time()
            delay = 60.0 / self._MAX_REQUESTS_PER_MINUTE
            elapsed = now - self._last_request_ts
            
            # 为并发请求添加额外的随机延迟，避免同时请求
            # Add extra random delay for concurrent requests to avoid simultaneous requests
            import random
            concurrent_jitter = random.uniform(0.1, 0.5)  # 100-500ms的随机延迟
            
            total_delay = delay + concurrent_jitter
            if elapsed < total_delay:
                await asyncio.sleep(total_delay - elapsed)
            self._last_request_ts = time.time()

    def _assemble_prompts(self, from_lang: str, to_lang: str, queries: List[str]):
        """
        原脚本中用来把多个 query 组装到一个 Prompt。
        同时可以做长度控制，如果过长就切分成多个 prompt。
        这里演示一个简单的 chunk 逻辑：
          - 根据字符长度 roughly 判断
          - 也可以用更准确的 tokens 估算
        """

        lang_name = self._LANGUAGE_CODE_MAP.get(to_lang, to_lang) if to_lang in self._LANGUAGE_CODE_MAP else to_lang
        
        MAX_CHAR_PER_PROMPT = self._MAX_TOKENS * 4  # 粗略: 1 token ~ 4 chars
        chunk_queries = []
        current_length = 0
        batch = []

        for q in queries:
            # +10 给一些余量，比如加上 <|1|> 的标记等
            if current_length + len(q) + 10 > MAX_CHAR_PER_PROMPT and batch:
                # 输出当前 batch
                chunk_queries.append(batch)
                batch = []
                current_length = 0
            batch.append(q)
            current_length += len(q) + 10
        if batch:
            chunk_queries.append(batch)

        # 逐个批次生成 prompt
        for this_batch in chunk_queries:
            prompt = ""
            if self.include_template:
                prompt = self.prompt_template.format(to_lang=lang_name)
            # 加上分行内容
            for i, query in enumerate(this_batch):
                prompt += f"\n<|{i+1}|>{query}"
            yield prompt.lstrip(), len(this_batch)

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:
        """
        核心翻译逻辑：
            1. 把 queries 拆成多个 prompt 批次
            2. 对每个批次调用 translate_batch，并将结果写回 translations
        """
        translations = [''] * len(queries)
        # 记录当前处理到 queries 列表的哪个位置
        idx_offset = 0

        # 分批处理
        for prompt, batch_size in self._assemble_prompts(from_lang, to_lang, queries):
            # 实际要翻译的子列表
            batch_queries = queries[idx_offset : idx_offset + batch_size]
            indices = list(range(idx_offset, idx_offset + batch_size))

            # 执行翻译
            success, partial_results = await self._translate_batch(
                from_lang, to_lang, batch_queries, indices, prompt, split_level=0
            )
            # 将结果写入 translations
            for i, r in zip(indices, partial_results):
                translations[i] = r

            idx_offset += batch_size

        return translations

    async def _try_fallback_model(self, to_lang: str, prompt: str, batch_queries: List[str]) -> tuple[bool, List[str]]:
        """
        尝试使用回退模型进行翻译，默认重试3次
        Returns: (success: bool, results: List[str])
        """
        if not self._fallback_model:
            return False, []
            
        fallback_max_attempts = 2  # 默认重试2次（总共3次请求）
        
        for attempt in range(fallback_max_attempts + 1):  # +1 for initial attempt
            if attempt == 0:
                self.logger.warning(f"Trying fallback model '{self._fallback_model}' (request {attempt+1}/3)")
            else:
                self.logger.warning(f"Trying fallback model '{self._fallback_model}' (retry {attempt}/2, request {attempt+1}/3)")
            
            # 禁用译后检测
            try:
                import inspect
                for st in inspect.stack():
                    cfg = st.frame.f_locals.get("config")
                    if cfg and hasattr(cfg, "translator"):
                        cfg.translator.enable_post_translation_check = False
                        break
            except Exception:
                pass

            from importlib import import_module
            keys_mod = import_module("manga_translator.translators.keys")
            original_model_const = getattr(keys_mod, "OPENAI_MODEL", None)

            try:
                # 临时替换常量，使 _request_with_retry 使用回退模型
                setattr(keys_mod, "OPENAI_MODEL", self._fallback_model)

                # 若当前处于 ChatGPT2StageTranslator 第二阶段，需要同步切换 stage2_model
                orig_stage2 = getattr(self, "stage2_model", None)
                if getattr(self, "_is_stage2_translation", False) and hasattr(self, "stage2_model"):
                    self.stage2_model = self._fallback_model

                # 关闭 stage2 标志，强制 _request_translation 走 OPENAI_MODEL
                orig_stage_flag = getattr(self, "_is_stage2_translation", False)
                try:
                    if orig_stage_flag:
                        self._is_stage2_translation = False
                    response_text_fb = await self._request_with_retry(to_lang, prompt)
                finally:
                    if orig_stage_flag:
                        self._is_stage2_translation = orig_stage_flag

                fb_translations = [t.strip() for t in re.split(r'<\|\d+\|>', response_text_fb)]
                if fb_translations and not fb_translations[0]:
                    fb_translations = fb_translations[1:]

                # 检查 fallback 模型是否提供了有效的翻译
                if len(fb_translations) != len(batch_queries):
                    self.logger.warning(f"Fallback output count mismatch: expected {len(batch_queries)}, got {len(fb_translations)}. Fallback failed.")
                    continue  # 继续重试而不是返回成功

                # 检查是否所有翻译都是空的或与原文相同
                valid_translations = 0
                for i, txt in enumerate(fb_translations):
                    if txt and txt.strip() and txt.strip() != batch_queries[i].strip():
                        valid_translations += 1

                if valid_translations == 0:
                    self.logger.warning("Fallback model returned no valid translations (all empty or same as original). Fallback failed.")
                    continue  # 继续重试而不是返回成功

                result_list = []
                for i, txt in enumerate(fb_translations):
                    result_list.append(txt if txt else batch_queries[i])

                self.logger.info(f"Fallback model succeeded on request {attempt+1} with {valid_translations}/{len(batch_queries)} valid translations")
                return True, result_list

            except Exception as fb_err:
                if attempt == 0:
                    self.logger.warning(f"Fallback model request {attempt+1}/3 failed: {fb_err}")
                else:
                    self.logger.warning(f"Fallback model retry {attempt}/2 (request {attempt+1}/3) failed: {fb_err}")
                if attempt < fallback_max_attempts:
                    await asyncio.sleep(1)  # 重试前等待1秒
                else:
                    self.logger.error(f"All fallback model requests failed")

            finally:
                # 恢复常量与 stage2_model
                if original_model_const is not None:
                    setattr(keys_mod, "OPENAI_MODEL", original_model_const)
                if getattr(self, "_is_stage2_translation", False) and hasattr(self, "stage2_model") and orig_stage2 is not None:
                    self.stage2_model = orig_stage2

        return False, []

    def _parse_and_split_translations(self, response_text: str, batch_queries: List[str]) -> tuple[List[str], bool]:
        new_translations = RE_INDEX_TAG.split(response_text)
        merged_single_query = False
        new_translations = [t.strip() for t in new_translations]

        if new_translations and not new_translations[0].strip():
            new_translations = new_translations[1:]

        if len(batch_queries) == 1 and len(new_translations) > 1:
            has_invalid_index = False
            for part in new_translations[1:]:
                index_match = RE_INDEX_TAG.search(part)
                if index_match and int(index_match.group(1)) > 1:
                    has_invalid_index = True
                    break

            if has_invalid_index:
                merged_translation = RE_INDEX_TAG.sub('', response_text).strip()
                new_translations = [merged_translation]
                self.logger.warning("Detected split translations for a single query, merged.")
                merged_single_query = True
        elif new_translations and not new_translations[0].strip():
            new_translations = new_translations[1:]

        return new_translations, merged_single_query

    def _validate_batch_index_prefixes(self, response_text: str, batch_queries: List[str], attempt: int, max_attempts: int) -> bool:
        lines = response_text.strip().split('\n')
        if not lines and len(batch_queries) > 0:
            self.logger.warning(f"[Attempt {attempt+1}/{max_attempts}] Received empty response for non-empty batch. Retrying...")
            return False

        expected_indices = set(range(1, len(batch_queries) + 1))
        found_indices = set()

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = RE_INDEX_LINE_PREFIX.match(line)
            if not match:
                continue

            try:
                current_index = int(match.group(1))
                if current_index not in expected_indices:
                    self.logger.warning(f"[Attempt {attempt+1}/{max_attempts}] Invalid index {current_index} found (expected 1-{len(batch_queries)}). Line: '{line}'. Retrying...")
                    return False
                if current_index in found_indices:
                    self.logger.warning(f"[Attempt {attempt+1}/{max_attempts}] Duplicate index {current_index} detected. Line: '{line}'. Retrying...")
                    return False
                found_indices.add(current_index)
            except ValueError:
                self.logger.warning(f"[Attempt {attempt+1}/{max_attempts}] Could not parse index from prefix. Line: '{line}'. Retrying...")
                return False

        if len(found_indices) != len(batch_queries) or found_indices != expected_indices:
            self.logger.warning(f"[Attempt {attempt+1}/{max_attempts}] Found indices count/set does not match expected ({len(batch_queries)}). Retrying...")
            return False

        return True

    def _validate_batch_content_quality(self, response_text: str, batch_queries: List[str], new_translations: List[str], attempt: int, max_attempts: int) -> bool:
        if any(symbol in response_text for symbol in SUSPICIOUS_HALLUCINATION_SYMBOLS):
            self.logger.warning(f'[attempt {attempt+1}/{max_attempts}] Suspicious symbols detected, skipping the current translation attempt.')
            return False

        empty_translation_errors = [
            i + 1 for i, (source, translation) in enumerate(zip(batch_queries, new_translations))
            if source.strip() and not translation
        ]
        if empty_translation_errors:
            self.logger.warning(f"[Attempt {attempt+1}/{max_attempts}] Empty translation detected for non-empty sources at positions: {empty_translation_errors}. Retrying...")
            return False

        for i, (source, translation) in enumerate(zip(batch_queries, new_translations)):
            is_source_simple = all(char in string.punctuation for char in source)
            is_translation_simple = all(char in string.punctuation for char in translation)
            if is_translation_simple and not is_source_simple:
                self.logger.warning(f"[Attempt {attempt+1}/{max_attempts}] Detected potential merged translation. Source: '{source}', Translation: '{translation}' (index {i+1}). Retrying...")
                return False

        if len(new_translations) != len(batch_queries):
            self.logger.warning(f"[Attempt {attempt+1}/{max_attempts}] Translation count mismatch: got {len(new_translations)} translations for {len(batch_queries)} queries. Retrying...")
            return False

        return True

    async def _attempt_single_batch_request(self, to_lang: str, prompt: str, batch_queries: List[str], attempt: int, max_attempts: int) -> tuple[bool, List[str], str]:
        response_text = await self._request_with_retry(to_lang, prompt)
        new_translations, merged_single_query = self._parse_and_split_translations(response_text, batch_queries)

        if not merged_single_query and not self._validate_batch_index_prefixes(response_text, batch_queries, attempt, max_attempts):
            return False, [], response_text

        if not self._validate_batch_content_quality(response_text, batch_queries, new_translations, attempt, max_attempts):
            return False, [], response_text

        return True, new_translations[:len(batch_queries)], response_text

    async def _split_and_translate_batch(self, from_lang: str, to_lang: str, batch_queries: List[str], batch_indices: List[int], split_level: int) -> tuple[bool, List[str]]:
        self.logger.warning(f"Splitting batch of size {len(batch_queries)} at split_level={split_level}")
        mid = len(batch_queries) // 2
        left_queries = batch_queries[:mid]
        right_queries = batch_queries[mid:]
        left_indices = batch_indices[:mid]
        right_indices = batch_indices[mid:]

        left_prompt, _ = next(self._assemble_prompts(from_lang, to_lang, left_queries))
        right_prompt, _ = next(self._assemble_prompts(from_lang, to_lang, right_queries))

        self.logger.info(f"Starting split translation: left batch size {len(left_queries)}, right batch size {len(right_queries)}")
        try:
            (left_success, left_results), (right_success, right_results) = await asyncio.gather(
                self._translate_batch(from_lang, to_lang, left_queries, left_indices, left_prompt, split_level + 1),
                self._translate_batch(from_lang, to_lang, right_queries, right_indices, right_prompt, split_level + 1),
                return_exceptions=False
            )
        except Exception as e:
            self.logger.error(f"Error during split translation: {e}")
            self.logger.info("Falling back to sequential processing due to split translation error")
            left_success, left_results = await self._translate_batch(
                from_lang, to_lang, left_queries, left_indices, left_prompt, split_level + 1
            )
            right_success, right_results = await self._translate_batch(
                from_lang, to_lang, right_queries, right_indices, right_prompt, split_level + 1
            )

        return (left_success and right_success), (left_results + right_results)

    async def _translate_batch(  
        self,  
        from_lang: str,  
        to_lang: str,  
        batch_queries: List[str],  
        batch_indices: List[int],  
        prompt: str,  
        split_level: int = 0  
    ):  
        """  
        尝试翻译 batch_queries。若失败或返回不完整，则进一步拆分。  
        Attempt to translate batch_queries. If failed or incomplete, further split the batch.  
        """  
        if not batch_queries:  
            return True, []  

        partial_results = [''] * len(batch_queries)  
        response_text = ""
        max_attempts = max(1, self._RETRY_ATTEMPTS + 1)

        for attempt in range(max_attempts):  
            try:  
                ok, res, response_text = await self._attempt_single_batch_request(to_lang, prompt, batch_queries, attempt, max_attempts)
                if ok:
                    for i in range(len(batch_queries)):
                        partial_results[i] = res[i]
                    self.logger.info(f"Batch of size {len(batch_queries)} translated OK at attempt {attempt+1}/{max_attempts} (split_level={split_level}).")
                    return True, partial_results
            except Exception as e:  
                self.logger.warning(f"Batch translate attempt {attempt+1}/{max_attempts} failed with error: {str(e)}")
                if attempt < max_attempts - 1:  
                    await asyncio.sleep(1)  
                else:
                    self.logger.warning("Max attempts reached.")
                    success, fallback_results = await self._try_fallback_model(to_lang, prompt, batch_queries)
                    if success:
                        for i, result in enumerate(fallback_results):
                            partial_results[i] = result
                        self.logger.info("Fallback model succeeded — skipping split logic.")
                        return True, partial_results

        if not any(partial_results):
            success, fallback_results = await self._try_fallback_model(to_lang, prompt, batch_queries)
            if success:
                for i, result in enumerate(fallback_results):
                    partial_results[i] = result
                self.logger.info("Fallback model succeeded — skipping split logic.")
                return True, partial_results

        self.logger.warning("Proceeding to split translation after all retries/fallback failures.")
        if split_level < self._MAX_SPLIT_ATTEMPTS and len(batch_queries) > 1:  
            return await self._split_and_translate_batch(from_lang, to_lang, batch_queries, batch_indices, split_level)

        if len(batch_queries) == 1 and not RE_INDEX_LINE_START.match(response_text):  
            self.logger.error(f"Single query translation failed after max retries due to missing prefix. size={len(batch_queries)}")
        else:  
            self.logger.error(f"Translation failed after max retries and splits. Returning original queries. size={len(batch_queries)}")

        for i in range(len(batch_queries)):   
            partial_results[i] = batch_queries[i]     
            
        return False, partial_results  

    async def _request_with_retry(self, to_lang: str, prompt: str) -> str:
        """
        结合重试、超时、限流处理的请求入口。
        """
        # 这里演示3层重试: 
        #   1) 如果请求超时 => 重新发起(最多 _TIMEOUT_RETRY_ATTEMPTS 次)
        #   2) 如果返回 429 => 也做重试(最多 _RATELIMIT_RETRY_ATTEMPTS 次)
        #   3) 其他错误 => 重试 _RETRY_ATTEMPTS 次
        # 最终失败则抛异常
        # 也可以将下面逻辑整合到 _translate_batch 里，但保持一次请求一次处理也行。

        timeout_attempt = 0
        ratelimit_attempt = 0
        server_error_attempt = 0

        while True:
            await self._ratelimit_sleep()
            started = time.time()
            req_task = asyncio.create_task(self._request_translation(to_lang, prompt))

            try:
                # 等待请求
                while not req_task.done():
                    await asyncio.sleep(0.1)
                    if time.time() - started > self._TIMEOUT:
                        # 超时 => 取消请求并重试
                        timeout_attempt += 1
                        if timeout_attempt > self._TIMEOUT_RETRY_ATTEMPTS:
                            raise TimeoutError(
                                f"OpenAI request timed out after {self._TIMEOUT_RETRY_ATTEMPTS} attempts."
                            )
                        self.logger.warning(f"Request timed out, retrying... (attempt={timeout_attempt})")
                        req_task.cancel()
                        break
                else:
                    # 如果正常完成了
                    return req_task.result()

            except openai.RateLimitError:
                # 限流 => 重试
                ratelimit_attempt += 1
                if ratelimit_attempt > self._RATELIMIT_RETRY_ATTEMPTS:
                    raise
                self.logger.warning(f"Hit RateLimit, retrying... (attempt={ratelimit_attempt})")
                await asyncio.sleep(2)

            except openai.APIError as e:
                # 服务器错误 => 重试
                server_error_attempt += 1
                if server_error_attempt > self._RETRY_ATTEMPTS:
                    self.logger.error("Server error, giving up after several attempts.")
                    raise
                self.logger.warning(f"Server error: {str(e)}. Retrying... (attempt={server_error_attempt})")
                await asyncio.sleep(1)

            except Exception as e:
                self.logger.error(f"Unexpected error in _request_with_retry: {str(e)}")
                raise

    def _build_chat_messages(self, to_lang: str, prompt: str) -> tuple[list, bool]:
        lang_name = self._LANGUAGE_CODE_MAP.get(to_lang, to_lang) if to_lang in self._LANGUAGE_CODE_MAP else to_lang
        messages = [
            {'role': 'system', 'content': self.chat_system_template.format(to_lang=lang_name)},
        ]

        has_glossary = False
        relevant_terms = self.extract_relevant_terms(prompt)
        if relevant_terms:
            has_glossary = True
            glossary_text = "\n".join([f"{term}->{translation}" for term, translation in relevant_terms.items()])
            system_message = self.glossary_system_template.format(glossary_text=glossary_text)
            messages.append({'role': 'system', 'content': system_message})
            self.logger.info(f"Loaded {len(relevant_terms)} relevant terms from the glossary.")

        if self.prev_context:
            messages.append({'role': 'system', 'content': self.prev_context})

        lang_chat_samples = self.get_chat_sample(to_lang)
        if hasattr(self, 'chat_sample') and lang_chat_samples:
            messages.append({'role': 'user', 'content': lang_chat_samples[0]})
            messages.append({'role': 'assistant', 'content': lang_chat_samples[1]})

        messages.append({'role': 'user', 'content': prompt})
        return messages, has_glossary

    def _log_prompt_boxed(self, messages: list, has_glossary: bool):
        if self.verbose_logging:
            prompt_text = "\n".join(f"{m['role'].upper()}:\n{m['content']}" for m in messages)
            self.print_boxed(prompt_text, border_color="cyan", title="GPT Prompt")
            return

        simplified_msgs = []
        for i, m in enumerate(messages):
            if (has_glossary and i == 1) or (i == len(messages) - 1):
                simplified_msgs.append(f"{m['role'].upper()}:\n{m['content']}")
            else:
                simplified_msgs.append(f"{m['role'].upper()}:\n[HIDDEN CONTENT]")
        prompt_text = "\n".join(simplified_msgs)
        self.print_boxed(prompt_text, border_color="cyan", title="GPT Prompt (verbose=False)")

    def _clean_chat_response_text(self, raw_text: str) -> str:
        raw_text = RE_THINK_TAGS.sub('', raw_text)
        cleaned_text = RE_CONSECUTIVE_NEWLINES.sub('\n', raw_text).strip()

        lines = cleaned_text.splitlines()
        min_index_line_index = -1
        max_index_line_index = -1
        has_numeric_prefix = False

        for index, line in enumerate(lines):
            match = RE_INDEX_TAG.search(line)
            if match:
                has_numeric_prefix = True
                current_index = int(match.group(1))
                if current_index == 1:
                    min_index_line_index = index
                if max_index_line_index == -1 or current_index > int(RE_INDEX_TAG.search(lines[max_index_line_index]).group(1)):
                    max_index_line_index = index

        if has_numeric_prefix:
            modified_lines = []
            if min_index_line_index != -1:
                modified_lines.extend(lines[min_index_line_index:])

            if max_index_line_index != -1 and modified_lines:
                modified_lines = modified_lines[:max_index_line_index - min_index_line_index + 1]

            cleaned_text = "\n".join(modified_lines)

        return cleaned_text

    def _record_token_usage(self, response):
        if not hasattr(response, 'usage') or not hasattr(response.usage, 'total_tokens'):
            self.logger.warning("Response does not contain usage information")
            self.token_count_last = 0
        else:
            self.token_count += response.usage.total_tokens
            self.token_count_last = response.usage.total_tokens

    async def _request_translation(self, to_lang: str, prompt: str) -> str:
        """
        实际调用 openai.ChatCompletion 的请求部分。
        集成术语表功能。
        """
        messages, has_glossary = self._build_chat_messages(to_lang, prompt)
        self._log_prompt_boxed(messages, has_glossary)

        is_json_mode = getattr(self, "_professional_json_mode", False)
        kwargs = {
            "model": OPENAI_MODEL,
            "messages": messages,
            "max_completion_tokens": self._MAX_TOKENS if is_json_mode else self._MAX_TOKENS // 2,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "timeout": self._TIMEOUT,
        }
        if is_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as e:
            if is_json_mode and "response_format" in str(e).lower():
                kwargs.pop("response_format", None)
                response = await self.client.chat.completions.create(**kwargs)
            else:
                raise

        if not response.choices:
            raise ValueError("Empty response from OpenAI API")

        raw_content = response.choices[0].message.content or ""
        cleaned_text = raw_content.strip() if is_json_mode else self._clean_chat_response_text(raw_content)
        self._record_token_usage(response)

        self.print_boxed(cleaned_text, border_color="green", title="GPT Response")
        return cleaned_text

    def _fix_prefix_spacing(self, text_to_fix):
        """修复前缀和翻译内容之间的空格问题"""
        lines = text_to_fix.strip().split('\n')
        fixed_lines = []
        
        for line in lines:
            # 匹配 <|数字|> 前缀格式，去除前缀后的多余空格
            # Match <|number|> prefix format and remove extra spaces after prefix
            match = re.match(r'^(<\|\d+\|>)\s+(.*)$', line.strip())
            if match:
                prefix = match.group(1)
                content = match.group(2)
                # 重新组合：前缀 + 内容
                # Recombine: prefix + content (no space in between)
                fixed_line = f"{prefix}{content}"
                fixed_lines.append(fixed_line)
            else:
                fixed_lines.append(line)
        
        return '\n'.join(fixed_lines)

    # ==============修改日志输出方法 (Modify Log Output Method)==============
    def print_boxed(self, text, border_color="blue", title="OpenAITranslator Output"):  
        """将文本框起来并输出到终端"""
        """Box the text and output it to the terminal"""    
        
        # 应用修复
        # Apply the fix
        fixed_text = self._fix_prefix_spacing(text)
        
        # 输出到控制台（带颜色和边框）
        panel = Panel(fixed_text, title=title, border_style=border_color, expand=False)  
        self.console.print(panel)
        
        # 同时输出到日志文件（纯文本格式）
        
        import sys
        mt = sys.modules.get('manga_translator.manga_translator')
        if mt and hasattr(mt, '_log_console') and mt._log_console:
            # 直接输出纯文本，不使用边框
            mt._log_console.print(f"=== {title} ===")
            mt._log_console.print(fixed_text)
            mt._log_console.print("=" * (len(title) + 8))

    # ==============以下是术语表相关函数 (Below are glossary-related functions)==============
    
    def load_glossary(self, path):
        """加载术语表文件 / Load the glossary file"""
        if not os.path.exists(path):
            # 只在第一次检查时显示警告
            if not OpenAITranslator._glossary_warning_shown:
                self.logger.warning(f"The OpenAI glossary file does not exist: {path}")
                OpenAITranslator._glossary_warning_shown = True
            return {}
                
        # 检测文件类型并解析 / Detect the file type and parse it
        dict_type = self.detect_type(path)
        if dict_type == "galtransl":
            return self.load_galtransl_dic(path)
        elif dict_type == "sakura":
            return self.load_sakura_dict(path)
        elif dict_type == "mit":
            return self.load_mit_dict(path)              
        else:
            self.logger.warning(f"Unknown OpenAI glossary format: {path}")
            return {}

    def detect_type(self, dic_path):  
        """  
        检测字典类型（OpenAI专用） / Detect dictionary type (specific to OpenAI).
        """  
        with open(dic_path, encoding="utf8") as f:  
            dic_lines = f.readlines()  
        self.logger.debug(f"Detecting OpenAI dictionary type: {dic_path}")  
        if len(dic_lines) == 0:  
            return "unknown"  

        # 先判断是否为Sakura字典 / First, determine if it is a Sakura dictionary
        is_sakura = True  
        sakura_line_count = 0  
        for line in dic_lines:  
            line = line.strip()  
            if not line or line.startswith("\\\\") or line.startswith("//"):  
                continue  
                
            if "->" in line:  
                sakura_line_count += 1  
            else:  
                is_sakura = False  
                break  
        
        if is_sakura and sakura_line_count > 0:  
            return "sakura"  

        # 判断是否为Galtransl字典 / Determine if it is a Galtransl dictionary
        is_galtransl = True  
        galtransl_line_count = 0  
        for line in dic_lines:  
            line = line.strip()  
            if not line or line.startswith("\\\\") or line.startswith("//"):  
                continue  

            if "\t" in line or "    " in line:  
                galtransl_line_count += 1  
            else:  
                is_galtransl = False  
                break  
        
        if is_galtransl and galtransl_line_count > 0:  
            return "galtransl"  

        # 判断是否为MIT字典（最宽松的格式） / Determine if it is an MIT dictionary (the most lenient format)
        is_mit = True  
        mit_line_count = 0  
        for line in dic_lines:  
            line = line.strip()  
            if not line or line.startswith("#") or line.startswith("//"):  
                continue  
                
            # 排除Sakura格式特征 / Exclude Sakura format characteristics
            if "->" in line:  
                is_mit = False  
                break  
                
            # MIT格式需要能分割出源和目标两部分 / The MIT format needs to be able to split into source and target parts
            parts = line.split("\t", 1)  
            if len(parts) == 1:  # 如果没有制表符，尝试用空格分割 / If there are no tab characters, attempt to split using spaces
                parts = line.split(None, 1)  # None表示任何空白字符 / None represents any whitespace character
            
            if len(parts) >= 2:  # 确保有源和目标两部分 / Ensure there are both source and target parts
                mit_line_count += 1  
            else:  
                is_mit = False  
                break  
        
        if is_mit and mit_line_count > 0:  
            return "mit"  

        return "unknown"  

    @staticmethod
    def _parse_mit_dict_line(line: str) -> tuple:
        comment = ""
        if '#' in line:
            parts = line.split('#', 1)
            line = parts[0].strip()
            comment = "#" + parts[1]
        elif '//' in line:
            parts = line.split('//', 1)
            line = parts[0].strip()
            comment = "//" + parts[1]

        parts = line.split("\t", 1)
        if len(parts) == 1:
            parts = line.split(None, 1)

        if len(parts) < 2:
            return "", "", ""

        src = parts[0].strip().replace('_', ' ')
        dst = parts[1].strip().replace('_', ' ')
        return src, dst, comment

    @staticmethod
    def _suggest_regex_fix(src: str, error_message: str) -> str:
        suggested_fix = src
        for char, escaped in REGEX_ESCAPE_CHARS.items():
            suggested_fix = re.sub(f'(?<!\\\\){re.escape(char)}', escaped, suggested_fix)

        if "unterminated character set" in error_message:
            last_open = suggested_fix.rfind('\\[')
            if last_open != -1 and '\\]' not in suggested_fix[last_open:]:
                suggested_fix += '\\]'
        elif "unbalanced parenthesis" in error_message:
            open_count = suggested_fix.count('\\(')
            close_count = suggested_fix.count('\\)')
            if open_count > close_count:
                suggested_fix += '\\)' * (open_count - close_count)
        return suggested_fix

    def load_mit_dict(self, dic_path):
        """载入MIT格式的字典，返回结构化数据，并验证正则表达式"""
        with open(dic_path, encoding="utf8") as f:
            dic_lines = f.readlines()

        if not dic_lines:
            return {}

        dic_path = os.path.abspath(dic_path)
        dic_name = os.path.basename(dic_path)
        dict_count = 0
        regex_errors = 0
        glossary_entries = {}

        for line_number, line in enumerate(dic_lines, start=1):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue

            src, dst, comment = self._parse_mit_dict_line(line)
            if not src:
                self.logger.debug(f"Skipping lines with a single word: {line}")
                continue

            try:
                re.compile(src)
                entry = f"{dst} {comment}" if comment else dst
                glossary_entries[src] = entry
                dict_count += 1
            except re.error as e:
                regex_errors += 1
                error_message = str(e)
                self.logger.warning(f"Regular expression error on line {line_number}: '{src}' - {error_message}")
                suggested_fix = self._suggest_regex_fix(src, error_message)
                self.logger.info(f"Possible fix suggestions: '{suggested_fix}'")

        self.logger.info(f"Loading MIT format dictionary: {dic_name} containing {dict_count} entries, found {regex_errors} regular expression errors")
        return glossary_entries

    def load_galtransl_dic(self, dic_path):  
        """载入Galtransl格式的字典 / Loading a Galtransl format dictionary"""  
        glossary_entries = {}  
        
        try:  
            with open(dic_path, encoding="utf8") as f:  
                dic_lines = f.readlines()  
            
            if len(dic_lines) == 0:  
                return {}  
                
            dic_path = os.path.abspath(dic_path)  
            dic_name = os.path.basename(dic_path)  
            normalDic_count = 0  
            
            for line in dic_lines:  
                if line.startswith("\\\\") or line.startswith("//") or line.strip() == "":  
                    continue  
                
                # 尝试用制表符分割 / Attempting to split using tabs
                parts = line.split("\t")  
                # 如果分割结果不符合预期，尝试用空格分割 / If the split result is not as expected, try splitting using spaces    
                    
                if len(parts) != 2:  
                    parts = line.split("    ", 1)  # 四个空格 / Four spaces  
                
                if len(parts) == 2:  
                    src, dst = parts[0].strip(), parts[1].strip()  
                    glossary_entries[src] = dst  
                    normalDic_count += 1  
                else:  
                    self.logger.debug(f"Skipping lines that do not conform to the format.: {line.strip()}")  
            
            self.logger.info(f"Loading Galtransl format dictionary: {dic_name} containing {normalDic_count} entries")  
            return glossary_entries  
            
        except Exception as e:  
            self.logger.error(f"Error loading Galtransl dictionary: {e}")  
            return {}  

    def load_sakura_dict(self, dic_path):  
        """载入Sakura格式的字典 / Loading a Sakura format dictionary"""
        glossary_entries = {}  
        
        try:  
            with open(dic_path, encoding="utf8") as f:  
                dic_lines = f.readlines()  
            
            if len(dic_lines) == 0:  
                return {}  
                
            dic_path = os.path.abspath(dic_path)  
            dic_name = os.path.basename(dic_path)  
            dict_count = 0  
            
            for line in dic_lines:  
                line = line.strip()  
                if line.startswith("\\\\") or line.startswith("//") or line == "":  
                    continue  
                
                # Sakura格式使用 -> 分隔源词和目标词 /  
                # Sakura format uses -> to separate source words and target words
                if "->" in line:  
                    parts = line.split("->", 1)  
                    if len(parts) == 2:  
                        src, dst = parts[0].strip(), parts[1].strip()  
                        glossary_entries[src] = dst  
                        dict_count += 1  
                    else:  
                        self.logger.debug(f"Skipping lines that do not conform to the format: {line}")  
                else:  
                    self.logger.debug(f"Skipping lines that do not conform to the format: {line}")  
            
            self.logger.info(f"Loading Sakura format dictionary: {dic_name} containing {dict_count} entries")  
            return glossary_entries  
            
        except Exception as e:  
            self.logger.error(f"Error loading Sakura dictionary: {e}")  
            return {}       
            
    def extract_relevant_terms(self, text):  
        """自动提取和query相关的术语表条目，而不是一次性将术语表载入全部，以防止token浪费和系统提示词权重下降导致的指导效果减弱"""
        relevant_terms = {}  

        for term, translation in self.glossary_entries.items():
            # 1. 精确匹配：同时检查原词和去除空格的变体是否出现在文本中
            if term in text or term.replace(" ", "") in text:
                relevant_terms[term] = translation
                continue

            # 2. 日语特化的相似度匹配
            if any(0x3040 <= ord(c) <= 0x30FF for c in term):
                if is_japanese_similar(text, term):
                    relevant_terms[term] = translation
                    continue

            # 3. 普通编辑距离匹配（非日语文本）
            elif is_general_similar(text, term):
                relevant_terms[term] = translation
                continue

            # 4. 部分匹配
            if partial_match(text, term):
                relevant_terms[term] = translation
                continue

            # 5. 正则表达式匹配
            pattern = re.compile(term, re.IGNORECASE)
            if pattern.search(text):
                relevant_terms[term] = translation

        return relevant_terms
