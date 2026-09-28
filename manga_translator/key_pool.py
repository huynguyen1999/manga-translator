"""Shared API-key and model-pool behavior for hosted translators."""

import json
import logging
import re
import threading
import time
from typing import Any, Callable, Optional, Tuple


def mask_key(key: str) -> str:
    if not key:
        return ""
    key = str(key).strip()
    if len(key) <= 8:
        return key[:2] + "..." + key[-2:] if len(key) > 4 else "***"
    return f"{key[:6]}...{key[-4:]}"


def parse_pool_values(value: Any, strip_prefix: str = "") -> list[str]:
    """Parse delimited or JSON key/model settings, preserving first-seen order."""
    if not value:
        return []

    candidates: list[str] = []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            if isinstance(item, str):
                candidates.extend(parse_pool_values(item, strip_prefix))
            elif item is not None:
                candidates.append(str(item).strip())
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return []
        if cleaned.startswith("[") and cleaned.endswith("]"):
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, list):
                    return parse_pool_values(parsed, strip_prefix)
            except Exception:
                pass
        candidates.extend(
            part.strip().strip("'\"")
            for part in re.split(r"[\r\n,;\s]+", cleaned)
            if part.strip().strip("'\"")
        )
    else:
        candidates.append(str(value).strip())

    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        item = candidate.strip().strip("'\"")
        if strip_prefix and item.startswith(strip_prefix):
            item = item[len(strip_prefix):]
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


class _PoolInfo:
    def _init_state(self) -> None:
        self.is_valid = True
        self.cooldown_until = 0.0
        self.fail_count = 0
        self.success_count = 0
        self.last_used = 0.0
        self.last_error = ""

    def is_cooling_down(self) -> bool:
        return self.is_valid and time.time() < self.cooldown_until

    def is_available(self) -> bool:
        return self.is_valid and time.time() >= self.cooldown_until

    def remaining_cooldown(self) -> float:
        return max(0.0, self.cooldown_until - time.time())

    def __repr__(self) -> str:
        label = self.masked if hasattr(self, "masked") else self.model
        return f"<{type(self).__name__} {label} valid={self.is_valid} cooldown={self.remaining_cooldown():.1f}s>"


class KeyInfo(_PoolInfo):
    def __init__(self, key: str):
        self.key = key
        self.masked = mask_key(key)
        self._init_state()


class ModelInfo(_PoolInfo):
    def __init__(self, model: str):
        self.model = model.strip()
        self._init_state()


class KeyModelPool:
    """Common key/model storage and round-robin selection for API providers."""

    provider_name: str
    pool_logger: logging.Logger
    key_info_type: type[KeyInfo]
    model_info_type: type[ModelInfo]
    key_parser: Callable[[Any], list[str]]
    model_parser: Callable[[Any], list[str]]
    default_models: list[str]

    def __init__(
        self,
        keys: Any = None,
        models: Any = None,
        logger_instance: Optional[logging.Logger] = None,
        default_cooldown: float = 60.0,
    ):
        self.logger = logger_instance or self.pool_logger
        self.default_cooldown = default_cooldown
        self._lock = threading.Lock()
        self._key_infos: list[KeyInfo] = []
        self._key_map: dict[str, KeyInfo] = {}
        self._current_key_index = -1
        self._model_infos: list[ModelInfo] = []
        self._model_map: dict[str, ModelInfo] = {}
        self._current_model_index = -1
        self._current_target_index = -1
        self._pair_cooldowns: dict[tuple[str, str], float] = {}

        if keys:
            self.add_keys(keys)
        if models:
            self.add_models(models)
        elif not self._model_infos:
            self.add_models(self.default_models)

    def add_keys(self, keys: Any) -> int:
        parsed = self.key_parser(keys)
        added = 0
        with self._lock:
            for key in parsed:
                if key not in self._key_map:
                    info = self.key_info_type(key)
                    self._key_infos.append(info)
                    self._key_map[key] = info
                    added += 1
        if added:
            self.logger.debug(
                f"Added {added} {self.provider_name} API key(s) to pool. Total keys: {len(self._key_infos)}"
            )
        return added

    def add_models(self, models: Any) -> int:
        parsed = self.model_parser(models)
        added = 0
        with self._lock:
            for model in parsed:
                if model not in self._model_map:
                    info = self.model_info_type(model)
                    self._model_infos.append(info)
                    self._model_map[model] = info
                    added += 1
        if added:
            self.logger.debug(
                f"Added {added} {self.provider_name} model(s) to pool. Total models: {len(self._model_infos)}"
            )
        return added

    @property
    def total_keys(self) -> int:
        with self._lock:
            return len(self._key_infos)

    @property
    def valid_keys_count(self) -> int:
        with self._lock:
            return sum(1 for key in self._key_infos if key.is_valid)

    @property
    def available_keys_count(self) -> int:
        with self._lock:
            return sum(1 for key in self._key_infos if key.is_available())

    @property
    def total_models(self) -> int:
        with self._lock:
            return len(self._model_infos)

    @property
    def valid_models_count(self) -> int:
        with self._lock:
            return sum(1 for model in self._model_infos if model.is_valid)

    @property
    def available_models_count(self) -> int:
        with self._lock:
            return sum(1 for model in self._model_infos if model.is_available())

    @property
    def valid_targets_count(self) -> int:
        with self._lock:
            valid_keys = sum(1 for key in self._key_infos if key.is_valid)
            valid_models = sum(1 for model in self._model_infos if model.is_valid)
            return max(valid_keys, 1) * max(valid_models, 1)

    def get_all_keys(self) -> list[str]:
        with self._lock:
            return [info.key for info in self._key_infos]

    def get_all_models(self) -> list[str]:
        with self._lock:
            return [info.model for info in self._model_infos]

    @property
    def current_model(self) -> Optional[str]:
        with self._lock:
            if 0 <= self._current_model_index < len(self._model_infos):
                return self._model_infos[self._current_model_index].model
            valid = [model.model for model in self._model_infos if model.is_valid]
            return valid[0] if valid else None

    def _set_current_key_index(self, index: int) -> None:
        self._current_key_index = index
        if hasattr(self, "_current_index"):
            self._current_index = index

    def get_next_key(self, allow_cooldown: bool = False) -> Optional[str]:
        with self._lock:
            if not self._key_infos:
                return None
            valid_keys = [key for key in self._key_infos if key.is_valid]
            if not valid_keys:
                return None

            n = len(self._key_infos)
            for offset in range(1, n + 1):
                index = (self._current_key_index + offset) % n
                candidate = self._key_infos[index]
                if candidate.is_available():
                    self._set_current_key_index(index)
                    candidate.last_used = time.time()
                    return candidate.key

            if allow_cooldown:
                best = min(valid_keys, key=lambda key: key.cooldown_until)
                self._set_current_key_index(self._key_infos.index(best))
                best.last_used = time.time()
                return best.key
            return None

    def get_next_model(self, allow_cooldown: bool = False) -> Optional[str]:
        with self._lock:
            if not self._model_infos:
                return None
            valid_models = [model for model in self._model_infos if model.is_valid]
            if not valid_models:
                return None

            n = len(self._model_infos)
            for offset in range(1, n + 1):
                index = (self._current_model_index + offset) % n
                candidate = self._model_infos[index]
                if candidate.is_available():
                    self._current_model_index = index
                    candidate.last_used = time.time()
                    return candidate.model

            if allow_cooldown:
                best = min(valid_models, key=lambda model: model.cooldown_until)
                self._current_model_index = self._model_infos.index(best)
                best.last_used = time.time()
                return best.model
            return None

    def get_next_target(self, allow_cooldown: bool = False) -> Tuple[Optional[str], Optional[str]]:
        with self._lock:
            valid_keys = [key for key in self._key_infos if key.is_valid]
            valid_models = [model for model in self._model_infos if model.is_valid]
            if not valid_keys or not valid_models:
                return None, None

            num_keys = len(valid_keys)
            num_models = len(valid_models)
            ordered_pairs = []
            seen_pairs = set()
            for index in range(num_keys * num_models):
                key = valid_keys[index % num_keys]
                model = valid_models[index % num_models]
                pair = (key.key, model.model)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    ordered_pairs.append((key, model))
            for key in valid_keys:
                for model in valid_models:
                    pair = (key.key, model.model)
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        ordered_pairs.append((key, model))

            n = len(ordered_pairs)
            now = time.time()
            for offset in range(1, n + 1):
                index = (self._current_target_index + offset) % n
                candidate_key, candidate_model = ordered_pairs[index]
                pair_cooldown = self._pair_cooldowns.get(
                    (candidate_key.key, candidate_model.model), 0.0
                )
                if candidate_key.is_available() and candidate_model.is_available() and pair_cooldown <= now:
                    self._current_target_index = index
                    self._set_current_key_index(self._key_infos.index(candidate_key))
                    self._current_model_index = self._model_infos.index(candidate_model)
                    candidate_key.last_used = now
                    candidate_model.last_used = now
                    return candidate_key.key, candidate_model.model

            if allow_cooldown:
                def remaining_cooldown(pair):
                    key, model = pair
                    key_cd = max(0.0, key.cooldown_until - now)
                    model_cd = max(0.0, model.cooldown_until - now)
                    pair_cd = max(
                        0.0, self._pair_cooldowns.get((key.key, model.model), 0.0) - now
                    )
                    return max(key_cd, model_cd, pair_cd)

                best_key, best_model = min(ordered_pairs, key=remaining_cooldown)
                self._set_current_key_index(self._key_infos.index(best_key))
                self._current_model_index = self._model_infos.index(best_model)
                best_key.last_used = now
                best_model.last_used = now
                return best_key.key, best_model.model
            return None, None

    def mark_invalid(self, key: str, reason: str = "") -> None:
        with self._lock:
            info = self._key_map.get(key)
            if info:
                info.is_valid = False
                info.last_error = reason or "Invalid API Key"
                available = sum(1 for item in self._key_infos if item.is_available())
                self.logger.error(
                    f"{self.provider_name} API key [{info.masked}] is invalid ({reason or 'unauthorized'}). "
                    f"Disabling key. Remaining valid keys: {available}/{len(self._key_infos)}"
                )

    def mark_model_invalid(self, model: str, reason: str = "") -> None:
        with self._lock:
            info = self._model_map.get(model)
            if info:
                info.is_valid = False
                info.last_error = reason or "Model not found / unsupported"
                valid = sum(1 for item in self._model_infos if item.is_valid)
                self.logger.warning(
                    f"{self.provider_name} model [{model}] is invalid or not available "
                    f"({reason or 'not found'}). Disabling model for session. "
                    f"Remaining valid models: {valid}/{len(self._model_infos)}"
                )

    def mark_success(self, key: str, model: Optional[str] = None) -> None:
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
