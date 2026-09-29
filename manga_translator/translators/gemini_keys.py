import asyncio
import logging
import time
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Optional, Tuple

from manga_translator.key_pool import KeyInfo, KeyModelPool, ModelInfo, mask_key, parse_pool_values

logger = logging.getLogger("gemini_keys")

class GeminiRetryExhausted(RuntimeError):
    """A Gemini page reached a terminal retry-policy failure."""


class GeminiBlockedResponse(RuntimeError):
    """Gemini returned a prompt or candidate policy block; do not retry it."""


class GeminiRequestBudget:
    """Shared per-page budget for all Gemini API requests."""

    def __init__(self, limit: int = 4):
        self.limit = max(1, limit)
        self.used = 0
        self.rate_limited_keys = set()
        self.transient_retry_used = False
        self.output_retry_used = False
        self.split_used = False

    def consume(self) -> None:
        if self.used >= self.limit:
            raise GeminiRetryExhausted(
                f"Gemini request budget exhausted ({self.used}/{self.limit} requests used)."
            )
        self.used += 1

    def claim_retry(self, retry_type: str) -> bool:
        if retry_type == "transient":
            if self.transient_retry_used:
                return False
            self.transient_retry_used = True
            return True
        if retry_type == "output":
            if self.output_retry_used:
                return False
            self.output_retry_used = True
            return True
        if retry_type == "split":
            if self.split_used:
                return False
            self.split_used = True
            return True
        raise ValueError(f"Unknown Gemini retry type: {retry_type}")


_CURRENT_RETRY_BUDGET: ContextVar[Optional[GeminiRequestBudget]] = ContextVar(
    "gemini_retry_budget", default=None
)


def get_current_retry_budget() -> GeminiRequestBudget:
    budget = _CURRENT_RETRY_BUDGET.get()
    if budget is None:
        budget = GeminiRequestBudget()
        _CURRENT_RETRY_BUDGET.set(budget)
    return budget


def parse_gemini_keys(val: Any) -> List[str]:
    return parse_pool_values(val)


def parse_gemini_models(val: Any) -> List[str]:
    return parse_pool_values(val, strip_prefix="models/")


class GeminiKeyInfo(KeyInfo):
    pass


class GeminiModelInfo(ModelInfo):
    pass


DEFAULT_GEMINI_MODELS = [
    "gemini-1.5-flash-002",
]


class GeminiKeyManager(KeyModelPool):
    """Manages Gemini pools, API clients, and per-target context caches."""

    provider_name = "Gemini"
    pool_logger = logger
    key_info_type = GeminiKeyInfo
    model_info_type = GeminiModelInfo
    key_parser = staticmethod(parse_gemini_keys)
    model_parser = staticmethod(parse_gemini_models)
    default_models = DEFAULT_GEMINI_MODELS

    def __init__(
        self,
        keys: Any = None,
        models: Any = None,
        logger_instance: Optional[logging.Logger] = None,
        default_cooldown: float = 60.0,
    ):
        self._current_index = -1  # Backward compatibility alias
        self._genai_clients: Dict[str, Any] = {}
        self._openai_clients: Dict[Tuple[str, str], Any] = {}
        self._cached_contents: Dict[Any, Any] = {}
        super().__init__(keys, models, logger_instance, default_cooldown)

    def get_target_cooldown(self, key: str, model: str) -> float:
        """Get remaining cooldown seconds for a (key, model) target."""
        with self._lock:
            now = time.time()
            k_info = self._key_map.get(key)
            m_info = self._model_map.get(model)
            k_cd = max(0.0, (k_info.cooldown_until - now)) if k_info else 0.0
            m_cd = max(0.0, (m_info.cooldown_until - now)) if m_info else 0.0
            p_cd = max(0.0, (self._pair_cooldowns.get((key, model), 0.0) - now))
            return max(k_cd, m_cd, p_cd)

    def is_target_available(self, key: str, model: str) -> bool:
        """Check if a specific (key, model) target is available (valid and not cooling down)."""
        with self._lock:
            now = time.time()
            k_info = self._key_map.get(key)
            m_info = self._model_map.get(model)
            if not k_info or not k_info.is_valid:
                return False
            if not m_info or not m_info.is_valid:
                return False
            if k_info.cooldown_until > now:
                return False
            if m_info.cooldown_until > now:
                return False
            pair_cd = self._pair_cooldowns.get((key, model), 0.0)
            if pair_cd > now:
                return False
            return True

    def mark_rate_limited(
        self,
        key: str,
        model: Optional[str] = None,
        cooldown_seconds: Optional[float] = None,
    ) -> None:
        """
        Put a (key, model) target or key into temporary cooldown due to 429 / RESOURCE_EXHAUSTED / quota limit.
        """
        cooldown = cooldown_seconds if cooldown_seconds is not None else self.default_cooldown
        now = time.time()
        cd_until = now + cooldown

        with self._lock:
            info_key = self._key_map.get(key)
            if info_key:
                info_key.fail_count += 1
                info_key.last_error = "Rate limit / Quota exceeded"

            if model:
                info_model = self._model_map.get(model)
                if info_model:
                    info_model.fail_count += 1
                    info_model.last_error = "Rate limit / Quota exceeded"

                self._pair_cooldowns[(key, model)] = cd_until

                # Check if ALL models for this key are cooling down
                valid_models = [m.model for m in self._model_infos if m.is_valid]
                if valid_models and all(self._pair_cooldowns.get((key, m), 0.0) > now for m in valid_models):
                    if info_key:
                        info_key.cooldown_until = min(self._pair_cooldowns.get((key, m), cd_until) for m in valid_models)

                # Check if ALL keys for this model are cooling down
                valid_keys = [k.key for k in self._key_infos if k.is_valid]
                if valid_keys and all(self._pair_cooldowns.get((k, model), 0.0) > now for k in valid_keys):
                    if info_model:
                        info_model.cooldown_until = min(self._pair_cooldowns.get((k, model), cd_until) for k in valid_keys)

                self.logger.warning(
                    f"Gemini target [key: {mask_key(key)}, model: {model}] hit rate limit/quota. "
                    f"Cooling down for {cooldown:.0f}s. Jumping to next target..."
                )
            else:
                if info_key:
                    info_key.cooldown_until = cd_until
                for m in self._model_infos:
                    self._pair_cooldowns[(key, m.model)] = cd_until

                avail = sum(1 for k in self._key_infos if k.is_available())
                self.logger.warning(
                    f"Gemini API key [{mask_key(key)}] hit rate limit or quota exceeded. "
                    f"Cooling down for {cooldown:.0f}s. Remaining active keys: {avail}/{len(self._key_infos)}"
                )

    def get_genai_client(self, key: Optional[str] = None) -> Any:
        """
        Get or create a google.genai.Client for the given key (or next available key).
        """
        if not key:
            key = self.get_next_key(allow_cooldown=True)
            if not key:
                raise ValueError("No valid Gemini API keys available in the pool.")

        with self._lock:
            if key not in self._genai_clients:
                from google import genai
                self._genai_clients[key] = genai.Client(api_key=key)
            return self._genai_clients[key]

    def get_openai_client(
        self,
        key: Optional[str] = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/",
    ) -> Any:
        """
        Get or create an OpenAI client pointing to Gemini endpoint for the given key.
        """
        if not key:
            key = self.get_next_key(allow_cooldown=True)
            if not key:
                raise ValueError("No valid Gemini API keys available in the pool.")

        cache_key = (key, base_url)
        with self._lock:
            if cache_key not in self._openai_clients:
                from openai import OpenAI
                self._openai_clients[cache_key] = OpenAI(api_key=key, base_url=base_url)
            return self._openai_clients[cache_key]

    def get_cached_content(self, key: str, model: Optional[str] = None) -> Any:
        """Retrieve Context Cache object for a specific key and optional model."""
        with self._lock:
            if model:
                return self._cached_contents.get((key, model)) or self._cached_contents.get(key)
            return self._cached_contents.get(key)

    def set_cached_content(self, key: str, cache_obj: Any, model: Optional[str] = None) -> None:
        """Store Context Cache object for a specific key and optional model."""
        with self._lock:
            if model:
                self._cached_contents[(key, model)] = cache_obj
            self._cached_contents[key] = cache_obj

    def clear_cached_contents(self) -> None:
        """Clear all cached content objects."""
        with self._lock:
            self._cached_contents.clear()

    @staticmethod
    def is_rate_limit_error(exc: Exception) -> bool:
        """Check if an exception represents a 429 / rate limit / quota exceeded error."""
        if not exc:
            return False

        # Status code attribute
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code == 429:
            return True

        # Status string
        status = str(getattr(exc, "status", "")).upper()
        if "RESOURCE_EXHAUSTED" in status:
            return True

        # Error message text
        msg = str(exc).lower()
        keywords = [
            "429",
            "resource_exhausted",
            "quota exceeded",
            "quota_exceeded",
            "rate limit",
            "ratelimit",
            "too many requests",
            "resource has been exhausted",
        ]
        return any(k in msg for k in keywords)

    @staticmethod
    def is_model_rate_limit_error(exc: Exception) -> bool:
        """Check whether a rate-limit message is explicitly scoped to a model."""
        if not exc:
            return False
        msg = str(exc).lower()
        return any(k in msg for k in [
            "per model",
            "model quota",
            "quota exceeded for gemini-",
            "quota exceeded for model",
        ])

    @staticmethod
    def is_invalid_key_error(exc: Exception) -> bool:
        """Check if an exception represents an invalid API key / authentication error."""
        if not exc:
            return False

        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        msg = str(exc).lower()

        if code in (400, 401, 403):
            if any(k in msg for k in ["api_key_invalid", "api key not valid", "invalid api key", "permission_denied", "forbidden", "unregistered"]):
                return True

        keywords = [
            "api_key_invalid",
            "api key not valid",
            "invalid api key",
            "api_key expired",
        ]
        return any(k in msg for k in keywords)

    @staticmethod
    def is_model_not_found_error(exc: Exception) -> bool:
        """Check if an exception represents a 404 / Model Not Found / Unsupported model error."""
        if not exc:
            return False

        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code == 404:
            return True

        status = str(getattr(exc, "status", "")).upper()
        if "NOT_FOUND" in status:
            return True

        msg = str(exc).lower()
        keywords = [
            "not found",
            "not_found",
            "is not found for api version",
            "does not exist",
            "is not supported",
            "publisher model",
            "unsupported model",
            "unknown model",
            "invalid model",
            "model_not_found",
        ]
        return any(k in msg for k in keywords)

    async def _execute_key_only_with_retry(
        self,
        fn: Callable[..., Any],
        max_retries_per_key: int,
    ) -> Any:
        total_keys = self.valid_keys_count
        if not total_keys:
            raise RuntimeError("No valid Gemini API keys are configured.")
        attempts = 0
        max_attempts = total_keys * max(1, max_retries_per_key)

        while attempts < max_attempts:
            key = self.get_next_key(allow_cooldown=False)
            if not key:
                break

            client = self.get_genai_client(key)
            attempts += 1
            try:
                result = await fn(key, client)
                self.mark_success(key)
                return result
            except Exception as e:
                if self.is_rate_limit_error(e):
                    self.mark_rate_limited(key)
                    continue
                if self.is_invalid_key_error(e):
                    self.mark_invalid(key, str(e))
                    continue
                raise e

        raise RuntimeError(f"All {total_keys} Gemini API keys failed or exceeded rate limits.")

    async def _execute_target_with_retry(
        self,
        fn: Callable[..., Any],
        max_retries_per_key: int,
    ) -> Any:
        total_targets = self.valid_targets_count
        if not total_targets:
            raise RuntimeError("No valid Gemini API keys/models are configured.")
        attempts = 0
        max_attempts = total_targets * max(1, max_retries_per_key)

        while attempts < max_attempts:
            key, model = self.get_next_target(allow_cooldown=False)
            if not key or not model:
                break

            client = self.get_genai_client(key)
            attempts += 1
            try:
                result = await fn(key, model, client)
                self.mark_success(key, model=model)
                return result
            except Exception as e:
                if self.is_rate_limit_error(e):
                    self.mark_rate_limited(key, model=model if self.is_model_rate_limit_error(e) else None)
                    continue
                if self.is_model_not_found_error(e):
                    self.mark_model_invalid(model, str(e))
                    continue
                if self.is_invalid_key_error(e):
                    self.mark_invalid(key, str(e))
                    continue
                raise e

        raise RuntimeError(f"All {total_targets} Gemini API targets failed or exceeded rate limits.")

    async def execute_with_retry(
        self,
        fn: Callable[..., Any],
        max_retries_per_key: int = 1,
    ) -> Any:
        """
        Execute an async function `fn` with automatic key & model rotation
        upon rate limits or quota exhaustion.
        Supports both fn(key, client) and fn(key, model, client).
        """
        import inspect

        sig = inspect.signature(fn)
        if len(sig.parameters) < 3:
            return await self._execute_key_only_with_retry(fn, max_retries_per_key)
        return await self._execute_target_with_retry(fn, max_retries_per_key)
