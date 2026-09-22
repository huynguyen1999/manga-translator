import asyncio
import json
import logging
import re
import threading
import time
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

logger = logging.getLogger("gemini_keys")

T = TypeVar("T")


class GeminiRetryExhausted(RuntimeError):
    """A Gemini page reached a terminal retry-policy failure."""


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


def mask_key(key: str) -> str:
    """
    Mask an API key for safe logging (e.g., 'AIzaSy...4x9Z').
    """
    if not key:
        return ""
    key = str(key).strip()
    if len(key) <= 8:
        return key[:2] + "..." + key[-2:] if len(key) > 4 else "***"
    return f"{key[:6]}...{key[-4:]}"


def parse_gemini_keys(val: Any) -> List[str]:
    """
    Parses a string, list, or JSON structure containing one or more Gemini API keys.
    Supports:
    - List or tuple of strings: ['key1', 'key2']
    - Delimited string: 'key1, key2', 'key1; key2', 'key1\\nkey2'
    - JSON list string: '["key1", "key2"]'
    - Single key string: 'AIzaSy...'

    Returns a deduplicated list of non-empty API keys preserving the original order.
    """
    if not val:
        return []

    raw_candidates: List[str] = []

    if isinstance(val, (list, tuple, set)):
        for item in val:
            if isinstance(item, str):
                raw_candidates.extend(parse_gemini_keys(item))
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
                    return parse_gemini_keys(parsed_json)
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

    # Deduplicate while preserving order
    seen = set()
    result = []
    for k in raw_candidates:
        k = k.strip().strip("'\"")
        if k and k not in seen:
            seen.add(k)
            result.append(k)

    return result


def parse_gemini_models(val: Any) -> List[str]:
    """
    Parses a string, list, or JSON structure containing one or more Gemini model names.
    Supports:
    - Space-separated string: 'gemini-3.5-flash-lite gemini-3.1-flash-lite'
    - Delimited string: 'gemini-1.5-flash-002, gemini-3.5-flash-lite', '...; ...'
    - List or tuple of strings: ['gemini-1.5-flash-002', 'gemini-3.5-flash-lite']
    - JSON list string: '["gemini-1.5-flash-002", "gemini-3.5-flash-lite"]'
    - Normalizes by stripping 'models/' prefix and quotes/whitespace.

    Returns a deduplicated list of non-empty model names preserving the original order.
    """
    if not val:
        return []

    raw_candidates: List[str] = []

    if isinstance(val, (list, tuple, set)):
        for item in val:
            if isinstance(item, str):
                raw_candidates.extend(parse_gemini_models(item))
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
                    return parse_gemini_models(parsed_json)
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
    for m in raw_candidates:
        m = m.strip().strip("'\"")
        if m.startswith("models/"):
            m = m[len("models/"):]
        if m and m not in seen:
            seen.add(m)
            result.append(m)

    return result


class GeminiKeyInfo:
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
        return f"<GeminiKeyInfo {self.masked} valid={self.is_valid} cooldown={self.remaining_cooldown():.1f}s>"


class GeminiModelInfo:
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
        return f"<GeminiModelInfo {self.model} valid={self.is_valid} cooldown={self.remaining_cooldown():.1f}s>"


DEFAULT_GEMINI_MODELS = [
    "gemini-1.5-flash-002",
]


class GeminiKeyManager:
    """
    Manages a pool of Gemini API keys and models with round-robin rotation,
    rate-limit failover across keys and models, client caching, and per-target context caching.
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
        self._key_infos: List[GeminiKeyInfo] = []
        self._key_map: Dict[str, GeminiKeyInfo] = {}
        self._current_key_index: int = -1
        self._current_index: int = -1  # Backward compatibility alias

        # Model pool
        self._model_infos: List[GeminiModelInfo] = []
        self._model_map: Dict[str, GeminiModelInfo] = {}
        self._current_model_index: int = -1
        self._current_target_index: int = -1

        # Specific (key, model) pair cooldowns: (key, model) -> cooldown_until float
        self._pair_cooldowns: Dict[Tuple[str, str], float] = {}

        # Client pools
        self._genai_clients: Dict[str, Any] = {}
        self._openai_clients: Dict[Tuple[str, str], Any] = {}

        # Context cache storage per (key, model) or key: (key, model) -> CachedContent
        self._cached_contents: Dict[Any, Any] = {}

        if keys:
            self.add_keys(keys)
        if models:
            self.add_models(models)
        elif not self._model_infos:
            self.add_models(DEFAULT_GEMINI_MODELS)

    def add_keys(self, keys: Any) -> int:
        """Add new keys to the manager. Returns count of newly added keys."""
        parsed = parse_gemini_keys(keys)
        added = 0
        with self._lock:
            for k in parsed:
                if k not in self._key_map:
                    info = GeminiKeyInfo(k)
                    self._key_infos.append(info)
                    self._key_map[k] = info
                    added += 1
        if added > 0:
            self.logger.debug(f"Added {added} Gemini API key(s) to pool. Total keys: {len(self._key_infos)}")
        return added

    def add_models(self, models: Any) -> int:
        """Add new models to the manager. Returns count of newly added models."""
        parsed = parse_gemini_models(models)
        added = 0
        with self._lock:
            for m in parsed:
                if m not in self._model_map:
                    info = GeminiModelInfo(m)
                    self._model_infos.append(info)
                    self._model_map[m] = info
                    added += 1
        if added > 0:
            self.logger.debug(f"Added {added} Gemini model(s) to pool. Total models: {len(self._model_infos)}")
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
        If all valid keys are cooling down and allow_cooldown=True, returns the key
        with the shortest remaining cooldown.
        """
        with self._lock:
            if not self._key_infos:
                return None

            valid_keys = [k for k in self._key_infos if k.is_valid]
            if not valid_keys:
                return None

            # First priority: find available (not cooling down)
            n = len(self._key_infos)
            for i in range(1, n + 1):
                idx = (self._current_key_index + i) % n
                candidate = self._key_infos[idx]
                if candidate.is_available():
                    self._current_key_index = idx
                    self._current_index = idx
                    candidate.last_used = time.time()
                    return candidate.key

            # If no key is completely available right now:
            if allow_cooldown:
                best = min(valid_keys, key=lambda k: k.cooldown_until)
                self._current_key_index = self._key_infos.index(best)
                self._current_index = self._current_key_index
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
        Get the next (key, model) target using round-robin rotation among available
        key/model pairs.
        If all pairs are in cooldown and allow_cooldown=True, returns the pair
        with the shortest remaining cooldown.
        """
        with self._lock:
            valid_keys = [k for k in self._key_infos if k.is_valid]
            valid_models = [m for m in self._model_infos if m.is_valid]
            if not valid_keys or not valid_models:
                return None, None

            # Construct interleaved pair candidates
            num_keys = len(valid_keys)
            num_models = len(valid_models)
            total = max(num_keys, num_models) * 2

            ordered_pairs: List[Tuple[GeminiKeyInfo, GeminiModelInfo]] = []
            seen_pairs = set()

            # First interleave (key_i, model_i)
            for i in range(num_keys * num_models):
                k = valid_keys[i % num_keys]
                m = valid_models[i % num_models]
                pair_tuple = (k.key, m.model)
                if pair_tuple not in seen_pairs:
                    seen_pairs.add(pair_tuple)
                    ordered_pairs.append((k, m))

            # Ensure all Cartesian products are present
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
                    self._current_index = self._current_key_index
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
                self._current_index = self._current_key_index
                self._current_model_index = self._model_infos.index(best_m)
                best_k.last_used = now
                best_m.last_used = now
                return best_k.key, best_m.model

            return None, None

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

    def mark_invalid(self, key: str, reason: str = "") -> None:
        """
        Permanently mark a key as invalid for this session (e.g. 400 API_KEY_INVALID, 403 Forbidden).
        """
        with self._lock:
            info = self._key_map.get(key)
            if info:
                info.is_valid = False
                info.last_error = reason or "Invalid API Key"
                avail = sum(1 for k in self._key_infos if k.is_available())
                self.logger.error(
                    f"Gemini API key [{info.masked}] is invalid ({reason or 'unauthorized'}). "
                    f"Disabling key. Remaining valid keys: {avail}/{len(self._key_infos)}"
                )

    def mark_model_invalid(self, model: str, reason: str = "") -> None:
        """
        Permanently mark a model as invalid for this session (e.g. 404 NOT_FOUND, unsupported model).
        """
        with self._lock:
            info = self._model_map.get(model)
            if info:
                info.is_valid = False
                info.last_error = reason or "Model not found / unsupported"
                avail = sum(1 for m in self._model_infos if m.is_valid)
                self.logger.warning(
                    f"Gemini model [{model}] is invalid or not available ({reason or 'not found'}). "
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
