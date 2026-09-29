import os
import asyncio
from contextvars import ContextVar

from .deepseek import DeepseekTranslator

_REQUEST_MODEL = ContextVar("tokenharbor_request_model", default=None)


class TokenHarborTranslator(DeepseekTranslator):
    FALLBACK_MODEL = "mimo-v2.6-flash:free"
    MODEL_TIMEOUT_SECONDS = 60

    def __init__(self, check_openai_key=True, api_key=None, api_base=None, model=None, config_key=None):
        resolved_model = model or os.getenv("TOKEN_HARBOR_MODEL", "deepseek-v4.1-flash:free")
        resolved_key = api_key if api_key is not None else os.getenv("TOKEN_HARBOR_API_KEY", "")
        resolved_base = api_base or os.getenv("TOKEN_HARBOR_API_BASE", "https://tokenharbor.ai/v1")
        super().__init__(
            check_openai_key=check_openai_key,
            api_key=resolved_key,
            api_base=resolved_base,
            model=resolved_model,
            config_key=config_key or f"tokenharbor.{resolved_model}",
            missing_key_msg="Please set TOKEN_HARBOR_API_KEY before using the Token Harbor translator.",
            fallback_to_openai_key=False,
        )

    async def _request_translation(self, to_lang: str, prompt: str) -> str:
        return await super()._request_translation(to_lang, prompt, model=_REQUEST_MODEL.get())

    async def _translate_with_model(self, model: str, from_lang: str, to_lang: str, queries):
        token = _REQUEST_MODEL.set(model)
        try:
            return await super()._translate(from_lang, to_lang, queries)
        finally:
            _REQUEST_MODEL.reset(token)

    async def _translate(self, from_lang: str, to_lang: str, queries):
        try:
            return await asyncio.wait_for(
                self._translate_with_model(self.model, from_lang, to_lang, queries),
                timeout=self.MODEL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self.logger.warning(
                "Token Harbor model %s timed out after %s seconds; retrying with %s",
                self.model,
                self.MODEL_TIMEOUT_SECONDS,
                self.FALLBACK_MODEL,
            )
            if self.model == self.FALLBACK_MODEL:
                raise
        return await asyncio.wait_for(
            self._translate_with_model(self.FALLBACK_MODEL, from_lang, to_lang, queries),
            timeout=self.MODEL_TIMEOUT_SECONDS,
        )
