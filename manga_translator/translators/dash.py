import os
from typing import Optional

from .deepseek import DeepseekTranslator


class DashTranslator(DeepseekTranslator):
    """Dash LLM / DashScope translator backend with OpenAI compatibility."""

    DEFAULT_MODEL = "deepseek-v3"
    DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(
        self,
        check_openai_key: bool = True,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        config_key: Optional[str] = None,
    ):
        resolved_model = (
            model
            or os.getenv("DASH_MODEL")
            or os.getenv("DASHSCOPE_MODEL")
            or self.DEFAULT_MODEL
        )
        if resolved_model in {"deepseek", "deepseek-chat", "dash", "dashscope", "dash-llm", "dash_llm"}:
            resolved_model = self.DEFAULT_MODEL

        resolved_key = (
            api_key
            if api_key is not None
            else (os.getenv("DASH_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "")
        )
        resolved_base = (
            api_base
            or os.getenv("DASH_API_BASE")
            or os.getenv("DASHSCOPE_API_BASE")
            or self.DEFAULT_API_BASE
        )

        super().__init__(
            check_openai_key=check_openai_key,
            api_key=resolved_key,
            api_base=resolved_base,
            model=resolved_model,
            config_key=config_key or f"dash.{resolved_model}",
            missing_key_msg="Please set DASHSCOPE_API_KEY or DASH_API_KEY before using the Dash LLM translator.",
            fallback_to_openai_key=False,
        )
