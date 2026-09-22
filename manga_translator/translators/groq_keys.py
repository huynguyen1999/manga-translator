import asyncio
import json
import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

try:
    import groq
except ImportError:
    groq = None

logger = logging.getLogger("groq_keys")

T = TypeVar("T")


def mask_key(key: str) -> str:
    """
    Mask an API key for safe logging (e.g., 'gsk_12...4x9Z').
    """
    if not key:
        return ""
    key = str(key).strip()
    if len(key) <= 8:
        return key[:2] + "..." + key[-2:] if len(key) > 4 else "***"
    return f"{key[:6]}...{key[-4:]}"


def parse_groq_keys(val: Any) -> List[str]:
    """
    Parses a string, list, or JSON structure containing one or more Groq API keys.
    Supports:
    - List or tuple of strings: ['key1', 'key2']
    - Delimited string: 'key1, key2', 'key1; key2', 'key1\nkey2'
    - JSON list string: '["key1", "key2"]'
    - Single key string: 'gsk_...'

    Returns a deduplicated list of non-empty API keys preserving the original order.
    """
    if not val:
        return []

    raw_candidates: List[str] = []

    if isinstance(val, (list, tuple, set)):
        for item in val:
            if isinstance(item, str):
                raw_candidates.extend(parse_groq_keys(item))
            elif item is not None:
                raw_candidates.append(str(item).strip())
    elif isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            return []
        # Check if it's a JSON array
        if cleaned.startswith("[") and cleaned.endswith("]"):
            try:
                parsed_json = json.loads(cleaned)
                if isinstance(parsed_json, list):
                    return parse_groq_keys(parsed_json)
            except Exception:
                pass

        # Split by comma, semicolon, newline, or whitespace
        parts = re.split(r"[\r\n,;\s]+", cleaned)
        for part in parts:
            part = part.strip().strip("'\"")
            if part:
                raw_candidates.append(part)
    else:
        raw_candidates.append(str(val).strip())

    seen = set()
    result = []
    for k in raw_candidates:
        k = k.strip().strip("'\"")
        if k and k not in seen:
            seen.add(k)
            result.append(k)

    return result


def parse_groq_models(val: Any) -> List[str]:
    """
    Parses a string, list, or JSON structure containing one or more Groq model names.
    Supports:
    - Space/comma/semicolon/newline-separated string: 'llama-3.3-70b-versatile, mixtral-8x7b-32768'
    - List or tuple of strings: ['llama-3.3-70b-versatile', 'mixtral-8x7b-32768']
    - JSON list string: '["llama-3.3-70b-versatile", "mixtral-8x7b-32768"]'

    Returns a deduplicated list of non-empty model names preserving the original order.
    """
    if not val:
        return []

    raw_candidates: List[str] = []

    if isinstance(val, (list, tuple, set)):
        for item in val:
            if isinstance(item, str):
                raw_candidates.extend(parse_groq_models(item))
            elif item is not None:
                raw_candidates.append(str(item).strip())
    elif isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            return []
        if cleaned.startswith("[") and cleaned.endswith("]"):
            try:
                parsed_json = json.loads(cleaned)
                if isinstance(parsed_json, list):
                    return parse_groq_models(parsed_json)
            except Exception:
                pass

        parts = re.split(r"[\r\n,;\s]+", cleaned)
        for part in parts:
            part = part.strip().strip("'\"")
            if part:
                raw_candidates.append(part)
    else:
        raw_candidates.append(str(val).strip())

    seen = set()
    result = []
    for m in raw_candidates:
        m = m.strip().strip("'\"")
        if m and m not in seen:
            seen.add(m)
            result.append(m)

    return result


class GroqKeyInfo:
    """Tracks state and metrics for a single API key."""

    def __init__(self, key: str):
        self.key: str = key
        self.masked: str = mask_key(key)
        self.is_valid: bool = True
        self.cooldown_until: float = 0.0
        self.fail_count: int = 0
        self.success_count: int = 0
        self.last_used: float = 0.0
        self.last_error: str = ""

    def is_cooling_down(self) -> bool:
        return self.is_valid and time.time() < self.cooldown_until

    def is_available(self) -> bool:
        return self.is_valid and time.time() >= self.cooldown_until

    def remaining_cooldown(self) -> float:
        return max(0.0, self.cooldown_until - time.time())

    def __repr__(self) -> str:
        return f"<GroqKeyInfo {self.masked} valid={self.is_valid} cooldown={self.remaining_cooldown():.1f}s>"


class GroqModelInfo:
    """Tracks state and metrics for a single model name."""

    def __init__(self, model: str):
        self.model: str = model.strip()
        self.is_valid: bool = True
        self.cooldown_until: float = 0.0
        self.fail_count: int = 0
        self.success_count: int = 0
        self.last_used: float = 0.0
        self.last_error: str = ""

    def is_cooling_down(self) -> bool:
        return self.is_valid and time.time() < self.cooldown_until

    def is_available(self) -> bool:
        return self.is_valid and time.time() >= self.cooldown_until

    def remaining_cooldown(self) -> float:
        return max(0.0, self.cooldown_until - time.time())

    def __repr__(self) -> str:
        return f"<GroqModelInfo {self.model} valid={self.is_valid} cooldown={self.remaining_cooldown():.1f}s>"


DEFAULT_GROQ_MODELS = [
    "mixtral-8x7b-32768",
]


class GroqKeyManager:
    """
    Manages a pool of Groq API keys and models with round-robin rotation,
    rate-limit failover across keys and models, and client caching.
    """

    def __init__(
        self,
        keys: Any = None,
        models: Any = None,
        logger_instance: Optional[logging.Logger] = None,
        default_cooldown: float = 60.0,
    ):
        self.logger = logger_instance or logger
        self.default_cooldown = default_cooldown
        self._lock = threading.Lock()

        # Key pool
        self._key_infos: List[GroqKeyInfo] = []
        self._key_map: Dict[str, GroqKeyInfo] = {}
        self._current_key_index: int = -1

        # Model pool
        self._model_infos: List[GroqModelInfo] = []
        self._model_map: Dict[str, GroqModelInfo] = {}
        self._current_model_index: int = -1
        self._current_target_index: int = -1

        # Specific (key, model) pair cooldowns: (key, model) -> cooldown_until float
        self._pair_cooldowns: Dict[Tuple[str, str], float] = {}

        # Client pools: key -> groq.AsyncGroq
        self._groq_clients: Dict[str, Any] = {}

        if keys:
            self.add_keys(keys)
        if models:
            self.add_models(models)
        elif not self._model_infos:
            self.add_models(DEFAULT_GROQ_MODELS)

    def add_keys(self, keys: Any) -> int:
        """Add new keys to the manager. Returns count of newly added keys."""
        parsed = parse_groq_keys(keys)
        added = 0
        with self._lock:
            for k in parsed:
                if k not in self._key_map:
                    info = GroqKeyInfo(k)
                    self._key_infos.append(info)
                    self._key_map[k] = info
                    added += 1
        if added > 0:
            self.logger.debug(f"Added {added} Groq API key(s) to pool. Total keys: {len(self._key_infos)}")
        return added

    def add_models(self, models: Any) -> int:
        """Add new models to the manager. Returns count of newly added models."""
        parsed = parse_groq_models(models)
        added = 0
        with self._lock:
            for m in parsed:
                if m not in self._model_map:
                    info = GroqModelInfo(m)
                    self._model_infos.append(info)
                    self._model_map[m] = info
                    added += 1
        if added > 0:
            self.logger.debug(f"Added {added} Groq model(s) to pool. Total models: {len(self._model_infos)}")
        return added

    @property
    def total_keys(self) -> int:
        with self._lock:
            return len(self._key_infos)

    @property
    def valid_keys_count(self) -> int:
        with self._lock:
            return sum(1 for k in self._key_infos if k.is_valid)

    @property
    def available_keys_count(self) -> int:
        with self._lock:
            return sum(1 for k in self._key_infos if k.is_available())

    @property
    def total_models(self) -> int:
        with self._lock:
            return len(self._model_infos)

    @property
    def valid_models_count(self) -> int:
        with self._lock:
            return sum(1 for m in self._model_infos if m.is_valid)

    @property
    def available_models_count(self) -> int:
        with self._lock:
            return sum(1 for m in self._model_infos if m.is_available())

    @property
    def valid_targets_count(self) -> int:
        with self._lock:
            v_keys = sum(1 for k in self._key_infos if k.is_valid)
            v_models = sum(1 for m in self._model_infos if m.is_valid)
            return max(v_keys, 1) * max(v_models, 1)

    def get_all_keys(self) -> List[str]:
        with self._lock:
            return [info.key for info in self._key_infos]

    def get_all_models(self) -> List[str]:
        with self._lock:
            return [info.model for info in self._model_infos]

    @property
    def current_model(self) -> Optional[str]:
        with self._lock:
            if 0 <= self._current_model_index < len(self._model_infos):
                return self._model_infos[self._current_model_index].model
            valid = [m.model for m in self._model_infos if m.is_valid]
            return valid[0] if valid else None

    def get_next_key(self, allow_cooldown: bool = False) -> Optional[str]:
        """
        Get the next key using round-robin rotation among available (non-cooling) keys.
        """
        with self._lock:
            if not self._key_infos:
                return None

            valid_keys = [k for k in self._key_infos if k.is_valid]
            if not valid_keys:
                return None

            n = len(self._key_infos)
            for i in range(1, n + 1):
                idx = (self._current_key_index + i) % n
                candidate = self._key_infos[idx]
                if candidate.is_available():
                    self._current_key_index = idx
                    candidate.last_used = time.time()
                    return candidate.key

            if allow_cooldown:
                best = min(valid_keys, key=lambda k: k.cooldown_until)
                self._current_key_index = self._key_infos.index(best)
                best.last_used = time.time()
                return best.key

            return None

    def get_next_model(self, allow_cooldown: bool = False) -> Optional[str]:
        """
        Get the next model using round-robin rotation among available (non-cooling) models.
        """
        with self._lock:
            if not self._model_infos:
                return None

            valid_models = [m for m in self._model_infos if m.is_valid]
            if not valid_models:
                return None

            n = len(self._model_infos)
            for i in range(1, n + 1):
                idx = (self._current_model_index + i) % n
                candidate = self._model_infos[idx]
                if candidate.is_available():
                    self._current_model_index = idx
                    candidate.last_used = time.time()
                    return candidate.model

            if allow_cooldown:
                best = min(valid_models, key=lambda m: m.cooldown_until)
                self._current_model_index = self._model_infos.index(best)
                best.last_used = time.time()
                return best.model

            return None

    def get_next_target(self, allow_cooldown: bool = False) -> Tuple[Optional[str], Optional[str]]:
        """
        Get the next (key, model) target using round-robin rotation among available key/model pairs.
        """
        with self._lock:
            valid_keys = [k for k in self._key_infos if k.is_valid]
            valid_models = [m for m in self._model_infos if m.is_valid]
            if not valid_keys or not valid_models:
                return None, None

            num_keys = len(valid_keys)
            num_models = len(valid_models)

            ordered_pairs: List[Tuple[GroqKeyInfo, GroqModelInfo]] = []
            seen_pairs = set()

            for i in range(num_keys * num_models):
                k = valid_keys[i % num_keys]
                m = valid_models[i % num_models]
                pair_tuple = (k.key, m.model)
                if pair_tuple not in seen_pairs:
                    seen_pairs.add(pair_tuple)
                    ordered_pairs.append((k, m))

            for k in valid_keys:
                for m in valid_models:
                    pair_tuple = (k.key, m.model)
                    if pair_tuple not in seen_pairs:
                        seen_pairs.add(pair_tuple)
                        ordered_pairs.append((k, m))

            n = len(ordered_pairs)
            now = time.time()
            for i in range(1, n + 1):
                idx = (self._current_target_index + i) % n
                cand_k, cand_m = ordered_pairs[idx]
                pair_cd = self._pair_cooldowns.get((cand_k.key, cand_m.model), 0.0)
                if cand_k.is_available() and cand_m.is_available() and pair_cd <= now:
                    self._current_target_index = idx
                    self._current_key_index = self._key_infos.index(cand_k)
                    self._current_model_index = self._model_infos.index(cand_m)
                    cand_k.last_used = now
                    cand_m.last_used = now
                    return cand_k.key, cand_m.model

            if allow_cooldown:
                def pair_rem_cd(p):
                    k, m = p
                    k_cd = max(0.0, k.cooldown_until - now)
                    m_cd = max(0.0, m.cooldown_until - now)
                    p_cd = max(0.0, self._pair_cooldowns.get((k.key, m.model), 0.0) - now)
                    return max(k_cd, m_cd, p_cd)

                best_k, best_m = min(ordered_pairs, key=pair_rem_cd)
                self._current_key_index = self._key_infos.index(best_k)
                self._current_model_index = self._model_infos.index(best_m)
                best_k.last_used = now
                best_m.last_used = now
                return best_k.key, best_m.model

            return None, None

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

    def mark_invalid(self, key: str, reason: str = "") -> None:
        """
        Permanently mark a key as invalid for this session (e.g. 401 Unauthorized / Invalid API Key).
        """
        with self._lock:
            info = self._key_map.get(key)
            if info:
                info.is_valid = False
                info.last_error = reason or "Invalid API Key"
                avail = sum(1 for k in self._key_infos if k.is_available())
                self.logger.error(
                    f"Groq API key [{info.masked}] is invalid ({reason or 'unauthorized'}). "
                    f"Disabling key. Remaining valid keys: {avail}/{len(self._key_infos)}"
                )

    def mark_model_invalid(self, model: str, reason: str = "") -> None:
        """
        Permanently mark a model as invalid for this session (e.g. 404 Model Not Found / Decommissioned).
        """
        with self._lock:
            info = self._model_map.get(model)
            if info:
                info.is_valid = False
                info.last_error = reason or "Model not found / unsupported"
                avail = sum(1 for m in self._model_infos if m.is_valid)
                self.logger.warning(
                    f"Groq model [{model}] is invalid or not available ({reason or 'not found'}). "
                    f"Disabling model for session. Remaining valid models: {avail}/{len(self._model_infos)}"
                )

    def mark_success(self, key: str, model: Optional[str] = None) -> None:
        """Record successful request for a key and optional model."""
        with self._lock:
            info_key = self._key_map.get(key)
            if info_key:
                info_key.success_count += 1
                info_key.fail_count = 0
                info_key.cooldown_until = 0.0

            if model:
                info_model = self._model_map.get(model)
                if info_model:
                    info_model.success_count += 1
                    info_model.fail_count = 0
                    info_model.cooldown_until = 0.0
                self._pair_cooldowns[(key, model)] = 0.0

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
