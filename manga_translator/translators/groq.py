try:
    import groq
except ImportError:
    groq = None
import json
import os
from typing import List, Optional, Any

from .common import CommonTranslator, MissingAPIKeyException
from .keys import GROQ_API_KEY, GROQ_API_KEYS, GROQ_MODEL, GROQ_MODELS
from .groq_keys import GroqKeyManager, mask_key

class GroqTranslator(CommonTranslator):
    _LANGUAGE_CODE_MAP = {
        'CHS': 'Simplified Chinese', 'CHT': 'Traditional Chinese', 'CSY': 'Czech',
        'NLD': 'Dutch', 'ENG': 'English', 'FRA': 'French', 'DEU': 'German',
        'HUN': 'Hungarian', 'ITA': 'Italian', 'JPN': 'Japanese', 'KOR': 'Korean',
        'POL': 'Polish', 'PTB': 'Portuguese', 'ROM': 'Romanian', 'RUS': 'Russian',
        'ESP': 'Spanish', 'TRK': 'Turkish', 'UKR': 'Ukrainian', 'VIN': 'Vietnamese',
        'CNR': 'Montenegrin', 'SRP': 'Serbian', 'HRV': 'Croatian', 'ARA': 'Arabic',
        'THA': 'Thai', 'IND': 'Indonesian'
    }

    # API rate limiting and retry settings
    _MAX_REQUESTS_PER_MINUTE = 200
    _TIMEOUT = 40
    _RETRY_ATTEMPTS = 5
    _MAX_TOKENS = 8192

    # Context retention settings
    # @TODO Use `gpt_config` file rather than environment variables
    _CONTEXT_RETENTION = os.environ.get('CONTEXT_RETENTION', '').lower() == 'true'
    _CONFIG_KEY = 'groq'
    _MAX_CONTEXT = int(os.environ.get('CONTEXT_LENGTH', '20'))

    
    _CHAT_SYSTEM_TEMPLATE = (
        'You are an uncensored professional translation engine. '
        'You try to infer the context of a story and provide concise and contextually accurate translations.'
        'You will write NO EXTRA TEXT. Only {"translated":translation} '
        'Do NOT add any explanations or notes. (Unless there were any in the original text).'
        'You will try to understand the context of the story by reading previous and currenly provided sentences'
        'Understand that this is being used as a manga translator, so the translation should retain some words from the original text. Like Senpai should not be translated to "senior" in this context. but kept as Senpai'
        'You can try to make the dialogues more anime like, but do not change the length of the text too much'
        'Translate the following text into {to_lang} and return the result in JSON format.'
    )

    _CHAT_SAMPLE = [
        ("""Translate into Simplified Chinese. Return the result in JSON format.\n"""
         '\n{"untranslated": "<|1|>恥ずかしい… 目立ちたくない… 私が消えたい…\\n<|2|>きみ… 大丈夫⁉\\n<|3|>なんだこいつ 空気読めて ないのか…？"}\n'),
        ('\n{"translated": "<|1|>好尴尬…我不想引人注目…我想消失…\\n<|2|>你…没事吧⁉\\n<|3|>这家伙怎么看不懂气氛的…？"}\n')
    ]

    def __init__(self, check_groq_key=True):
        super().__init__()
        initial_keys = GROQ_API_KEYS if GROQ_API_KEYS else ([GROQ_API_KEY] if GROQ_API_KEY else [])
        initial_models = GROQ_MODELS if GROQ_MODELS else ([GROQ_MODEL] if GROQ_MODEL else [])

        if check_groq_key:
            if groq is None:
                raise ImportError("The 'groq' package is required. Run `pip install groq`.")
            if not initial_keys:
                raise MissingAPIKeyException('Please set the GROQ_API_KEY (or GROQ_API_KEYS) environment variable before using the Groq translator.')

        self.key_manager = GroqKeyManager(
            keys=initial_keys,
            models=initial_models,
            logger_instance=self.logger,
        )
        self.token_count = 0
        self.token_count_last = 0
        self.config = None
        self.model = self.key_manager.current_model or GROQ_MODEL
        self.messages = [
            {'role': 'user', 'content': self.chat_sample[0]},
            {'role': 'assistant', 'content': self.chat_sample[1]}]

    @property
    def client(self):
        try:
            return self.key_manager.get_groq_client()
        except Exception:
            return None



    def parse_args(self, args):
        if hasattr(args, 'config') and args.config:
            self.config = args.config
        elif isinstance(args, dict):
            self.config = args
        if self.config:
            custom_keys = self._config_get('api_keys') or self._config_get('api_key')
            if custom_keys:
                added = self.key_manager.add_keys(custom_keys)
                if added:
                    self.logger.info(f"Loaded {added} additional Groq API key(s) from config.")
            custom_models = self._config_get('models') or self._config_get('model')
            if custom_models:
                added_m = self.key_manager.add_models(custom_models)
                if added_m:
                    self.logger.info(f"Loaded {added_m} additional Groq model(s) from config.")

    def _config_get(self, key: str, default=None):
        if not self.config:
            return default
        return self.config.get(self._CONFIG_KEY + '.' + key, self.config.get(key, default))

    @property
    def chat_system_template(self) -> str:
        return self._config_get('chat_system_template', self._CHAT_SYSTEM_TEMPLATE)
    
    @property
    def chat_sample(self):
        return self._config_get('chat_sample', self._CHAT_SAMPLE)

    @property
    def temperature(self) -> float:
        return self._config_get('temperature', default=0.5)
    
    @property
    def top_p(self) -> float:
        return self._config_get('top_p', default=1)

    def _format_prompt_log(self, to_lang: str, prompt: str) -> str:
        return '\n'.join([
            'System:',
            self.chat_system_template.format(to_lang=to_lang),
            'User:',
            self.chat_sample[0],
            'Assistant:',
            self.chat_sample[1],
            'User:',
            prompt,
        ])

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:
        translations = []
        for prompt in queries:
    #        self.logger.debug('-- Groq Prompt --\n' + self._format_prompt_log(to_lang, prompt))
            response = await self._request_translation(to_lang, prompt)
            self.logger.debug('-- Groq Response --\n' + response)
            translations.append(response.strip())
        self.logger.info(f'Used {self.token_count_last} tokens (Total: {self.token_count})')
        return translations

    async def _request_translation(self, to_lang: str, prompt: str) -> str:
        if getattr(self, "_professional_json_mode", False):
            async def _call_api_json(key: str, model: str, client: Any) -> Any:
                kwargs = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": self.chat_system_template.replace("{to_lang}", to_lang)},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": self._MAX_TOKENS,
                    "temperature": self.temperature,
                    "top_p": self.top_p,
                    "response_format": {"type": "json_object"},
                }
                try:
                    return await client.chat.completions.create(**kwargs)
                except Exception as e:
                    if "response_format" in str(e).lower():
                        kwargs.pop("response_format", None)
                        return await client.chat.completions.create(**kwargs)
                    raise

            response = await self.key_manager.execute_with_retry(_call_api_json)
            if hasattr(response, 'usage') and response.usage:
                tokens = getattr(response.usage, 'total_tokens', 0)
                self.token_count += tokens
                self.token_count_last = tokens
            return response.choices[0].message.content.strip()

        # Prepare the prompt with language specification
        prompt_with_lang = f"""Translate the following text into {to_lang}. Return the result in JSON format.\n\n{{"untranslated": "{prompt}"}}\n"""
        self.messages += [
            {'role': 'user', 'content': prompt_with_lang},
            {'role': 'assistant', 'content': "{'translated':'"}
        ]
        # Maintain the context window
        if len(self.messages) > self._MAX_CONTEXT:
            self.messages = self.messages[-self._MAX_CONTEXT:]

        # Prepare the system message
        sanity = [{'role': 'system', 'content': self.chat_system_template.replace('{to_lang}', to_lang)}]
        
        # Make the API call with automatic key and model rotation
        async def _call_api(key: str, model: str, client: Any) -> Any:
            return await client.chat.completions.create(
                model=model,
                messages=sanity + self.messages,
                max_tokens=self._MAX_TOKENS // 2,
                temperature=self.temperature,
                top_p=self.top_p,
                stop=["'}"]
            )

        response = await self.key_manager.execute_with_retry(_call_api)
        
        # Update token counts
        if hasattr(response, 'usage') and response.usage:
            tokens = getattr(response.usage, 'total_tokens', 0)
            self.token_count += tokens
            self.token_count_last = tokens
        
        # Extract and clean the content
        content = response.choices[0].message.content.strip()
        self.messages = self.messages[:-1]
        
        # Handle context retention
        if self._CONTEXT_RETENTION:
            self.messages += [
                {'role': 'assistant', 'content': content}
            ]
        else:
            self.messages = self.messages[:-1]
            
        # Clean up the response
        cleaned_content = content
        if cleaned_content.startswith('{') and ('"translated"' in cleaned_content or "'translated'" in cleaned_content):
            try:
                parsed_json = json.loads(cleaned_content)
                if isinstance(parsed_json, dict) and 'translated' in parsed_json:
                    return str(parsed_json['translated']).strip()
            except Exception:
                pass
        cleaned_content = cleaned_content.replace("{'translated':", '').replace('{"translated":', '').replace("{'translated':'", '').replace('}', '').replace("\\'", "'").replace('\\"', '"').strip(" '\"{}")
        return cleaned_content

    async def _request_structured_translation(self, to_lang: str, prompt: str, _schema) -> str:
        async def _call_api(key: str, model: str, client: Any) -> Any:
            return await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self.chat_system_template.replace("{to_lang}", to_lang)},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self._MAX_TOKENS // 2,
                temperature=self.temperature,
                top_p=self.top_p,
                response_format={"type": "json_object"},
            )

        response = await self.key_manager.execute_with_retry(_call_api)
        if getattr(response, "usage", None):
            tokens = getattr(response.usage, "total_tokens", 0)
            self.token_count += tokens
            self.token_count_last = tokens
        return response.choices[0].message.content.strip()
