import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from manga_translator.key_pool import KeyInfo, KeyModelPool, ModelInfo, mask_key, parse_pool_values

try:
    import groq
except ImportError:
    groq = None

logger = logging.getLogger("groq_keys")

def parse_groq_keys(val: Any) -> List[str]:
    return parse_pool_values(val)


def parse_groq_models(val: Any) -> List[str]:
    return parse_pool_values(val)


class GroqKeyInfo(KeyInfo):
    pass


class GroqModelInfo(ModelInfo):
    pass


DEFAULT_GROQ_MODELS = [
    "mixtral-8x7b-32768",
]


class GroqKeyManager(KeyModelPool):
    """Manages Groq key/model pools and caches API clients."""

    provider_name = "Groq"
    pool_logger = logger
    key_info_type = GroqKeyInfo
    model_info_type = GroqModelInfo
    key_parser = staticmethod(parse_groq_keys)
    model_parser = staticmethod(parse_groq_models)
    default_models = DEFAULT_GROQ_MODELS

    def __init__(
        self,
        keys: Any = None,
        models: Any = None,
        logger_instance: Optional[logging.Logger] = None,
        default_cooldown: float = 60.0,
    ):
        self._groq_clients: Dict[str, Any] = {}
        super().__init__(keys, models, logger_instance, default_cooldown)

    def get_status_summary(self) -> str:
        """Returns a string summary of keys and models status for error reporting."""
        with self._lock:
            key_summaries = []
            for k in self._key_infos:
                if not k.is_valid:
                    status = f"invalid ({k.last_error or 'disabled'})"
                elif k.is_cooling_down():
                    status = f"cooling down ({k.remaining_cooldown():.1f}s remaining, last error: {k.last_error or 'rate limit'})"
                else:
                    status = "available"
                key_summaries.append(f"{k.masked}: {status}")

            model_summaries = []
            for m in self._model_infos:
                if not m.is_valid:
                    status = f"invalid ({m.last_error or 'disabled'})"
                elif m.is_cooling_down():
                    status = f"cooling down ({m.remaining_cooldown():.1f}s remaining)"
                else:
                    status = "available"
                model_summaries.append(f"{m.model}: {status}")

            return f"Keys: [{', '.join(key_summaries)}], Models: [{', '.join(model_summaries)}]"

    def mark_rate_limited(
        self,
        key: str,
        model: Optional[str] = None,
        cooldown_seconds: Optional[float] = None,
        error_msg: str = "",
    ) -> None:
        """
        Put a (key, model) target or key into temporary cooldown due to 429 rate limits.
        """
        cooldown = cooldown_seconds if cooldown_seconds is not None else self.default_cooldown
        now = time.time()
        cd_until = now + cooldown

        with self._lock:
            info_key = self._key_map.get(key)
            if info_key:
                info_key.fail_count += 1
                info_key.last_error = error_msg or "Rate limit / Quota exceeded"

            if model:
                info_model = self._model_map.get(model)
                if info_model:
                    info_model.fail_count += 1
                    info_model.last_error = error_msg or "Rate limit / Quota exceeded"

                self._pair_cooldowns[(key, model)] = cd_until

                valid_models = [m.model for m in self._model_infos if m.is_valid]
                if valid_models and all(self._pair_cooldowns.get((key, m), 0.0) > now for m in valid_models):
                    if info_key:
                        info_key.cooldown_until = min(self._pair_cooldowns.get((key, m), cd_until) for m in valid_models)

                valid_keys = [k.key for k in self._key_infos if k.is_valid]
                if valid_keys and all(self._pair_cooldowns.get((k, model), 0.0) > now for k in valid_keys):
                    if info_model:
                        info_model.cooldown_until = min(self._pair_cooldowns.get((k, model), cd_until) for k in valid_keys)

                self.logger.warning(
                    f"Groq target [key: {mask_key(key)}, model: {model}] hit rate limit ({error_msg or '429'}). "
                    f"Cooling down for {cooldown:.0f}s. Rotating to next target..."
                )
            else:
                if info_key:
                    info_key.cooldown_until = cd_until
                for m in self._model_infos:
                    self._pair_cooldowns[(key, m.model)] = cd_until

                avail = sum(1 for k in self._key_infos if k.is_available())
                self.logger.warning(
                    f"Groq API key [{mask_key(key)}] hit rate limit ({error_msg or '429'}). "
                    f"Cooling down for {cooldown:.0f}s. Remaining active keys: {avail}/{len(self._key_infos)}"
                )

    def get_groq_client(self, key: Optional[str] = None) -> Any:
        """
        Get or create an AsyncGroq client for the given key (or next available key).
        """
        if not key:
            key = self.get_next_key(allow_cooldown=True)
            if not key:
                raise ValueError("No valid Groq API keys available in the pool.")

        with self._lock:
            if key not in self._groq_clients:
                if groq is None:
                    raise ImportError("The 'groq' package is required. Run `pip install groq`.")
                self._groq_clients[key] = groq.AsyncGroq(api_key=key)
            return self._groq_clients[key]

    @staticmethod
    def is_rate_limit_error(exc: Exception) -> bool:
        """Check if an exception represents a 429 / rate limit / quota exceeded error."""
        if not exc:
            return False

        if groq is not None:
            err_cls = getattr(groq, "RateLimitError", None)
            if isinstance(err_cls, type) and isinstance(exc, err_cls):
                return True

        code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if code == 429:
            return True

        msg = str(exc).lower()
        keywords = [
            "429",
            "rate_limit_exceeded",
            "rate limit",
            "ratelimit",
            "too many requests",
            "tokens per minute",
            "requests per minute",
            "tpm",
            "rpm",
            "quota",
        ]
        return any(k in msg for k in keywords)

    @staticmethod
    def is_invalid_key_error(exc: Exception) -> bool:
        """Check if an exception represents an invalid API key / authentication error."""
        if not exc:
            return False

        if groq is not None:
            err_cls = getattr(groq, "AuthenticationError", None)
            if isinstance(err_cls, type) and isinstance(exc, err_cls):
                return True

        code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        msg = str(exc).lower()

        if code in (401, 403):
            return True

        keywords = [
            "invalid_api_key",
            "invalid api key",
            "api key not valid",
            "unauthorized",
            "authentication",
            "permission_denied",
        ]
        return any(k in msg for k in keywords)

    @staticmethod
    def is_model_not_found_error(exc: Exception) -> bool:
        """Check if an exception represents a 404 / Model Not Found / Decommissioned model error."""
        if not exc:
            return False

        if groq is not None:
            err_cls = getattr(groq, "NotFoundError", None)
            if isinstance(err_cls, type) and isinstance(exc, err_cls):
                return True

        code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if code == 404:
            return True

        msg = str(exc).lower()
        keywords = [
            "model_not_found",
            "model not found",
            "does not exist",
            "decommissioned",
            "unsupported model",
            "unknown model",
        ]
        return any(k in msg for k in keywords)

    async def execute_with_retry(
        self,
        fn: Callable[..., Any],
        max_retries_per_key: int = 1,
    ) -> Any:
        """
        Execute an async function `fn(key, model, client)` with automatic key and model rotation
        upon rate limits or quota exhaustion.
        """
        import inspect

        total_targets = self.valid_targets_count
        if not total_targets:
            raise RuntimeError("No valid Groq API keys/models are configured.")

        sig = inspect.signature(fn)
        is_target_fn = len(sig.parameters) >= 3

        attempts = 0
        max_attempts = total_targets * max(1, max_retries_per_key)
        last_error: Optional[Exception] = None

        while attempts < max_attempts:
            if is_target_fn:
                key, model = self.get_next_target(allow_cooldown=False)
                if not key or not model:
                    # Try with cooldown if none available immediately
                    key, model = self.get_next_target(allow_cooldown=True)
                    if not key or not model:
                        break
            else:
                key = self.get_next_key(allow_cooldown=False) or self.get_next_key(allow_cooldown=True)
                model = self.current_model
                if not key:
                    break

            client = self.get_groq_client(key)
            attempts += 1
            try:
                if is_target_fn:
                    result = await fn(key, model, client)
                else:
                    result = await fn(key, client)
                self.mark_success(key, model=model)
                return result
            except Exception as e:
                last_error = e
                if self.is_rate_limit_error(e):
                    self.mark_rate_limited(key, model=model, error_msg=str(e))
                    continue
                if self.is_model_not_found_error(e):
                    if model:
                        self.mark_model_invalid(model, str(e))
                    continue
                if self.is_invalid_key_error(e):
                    self.mark_invalid(key, str(e))
                    continue
                self.logger.error(f"Unexpected error calling Groq API [key: {mask_key(key)}, model: {model}]: {e}")
                raise e

        status_summary = self.get_status_summary()
        err_msg = f"All {total_targets} Groq API targets failed or exceeded rate limits."
        if last_error:
            err_msg += f" Last error: [{type(last_error).__name__}] {last_error}."
        err_msg += f" (Pool status: {status_summary})"
        raise RuntimeError(err_msg) from last_error
