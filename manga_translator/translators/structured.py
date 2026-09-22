import json
import logging
import re
from typing import Iterable

from pydantic import BaseModel

logger = logging.getLogger("structured_translation")


class StructuredTranslation(BaseModel):
    id: str
    translation: str


class StructuredTranslationResponse(BaseModel):
    translations: list[StructuredTranslation]


class StructuredTranslationError(ValueError):
    def __init__(self, missing: set[str], translated: dict[str, str]):
        super().__init__(f"Structured translation missing valid results for: {', '.join(sorted(missing))}")
        self.missing = missing
        self.translated = translated


def _response_json_schema() -> dict:
    return StructuredTranslationResponse.model_json_schema()


def _extract_json_string(raw: str) -> str:
    if not raw:
        return ""
    text = str(raw).strip()

    # 1. Remove <think>...</think> reasoning tags if present
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()

    # 2. Check for markdown code fences: ```json ... ``` or ``` ... ```
    fence_matches = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if fence_matches:
        for match in fence_matches:
            candidate = match.strip()
            if candidate.startswith(("{", "[")):
                return candidate
        return fence_matches[0].strip()

    # 3. Direct start with { or [
    if text.startswith(("{", "[")):
        return text

    # 4. Search for outermost { ... }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1]

    # 5. Search for outermost [ ... ]
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")
    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        return text[first_bracket : last_bracket + 1]

    return text


def _parse_response(raw: str, expected: set[str]) -> dict[str, str]:
    if not raw:
        return {}

    candidate = _extract_json_string(raw)
    if not candidate:
        return {}

    parsed_json = None
    try:
        parsed_json = json.loads(candidate)
    except Exception:
        cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)
        try:
            parsed_json = json.loads(cleaned)
        except Exception:
            return {}

    found: dict[str, str] = {}

    if isinstance(parsed_json, list):
        for item in parsed_json:
            if isinstance(item, dict):
                item_id = str(item.get("id") or "")
                trans = str(item.get("translation") or item.get("text") or "").strip()
                if item_id in expected and trans and item_id not in found:
                    found[item_id] = trans
        return found

    if isinstance(parsed_json, dict):
        items_list = (
            parsed_json.get("translations")
            or parsed_json.get("regions")
            or parsed_json.get("results")
            or parsed_json.get("data")
        )
        if isinstance(items_list, list):
            for item in items_list:
                if isinstance(item, dict):
                    item_id = str(item.get("id") or "")
                    trans = str(item.get("translation") or item.get("text") or "").strip()
                    if item_id in expected and trans and item_id not in found:
                        found[item_id] = trans
        elif isinstance(items_list, dict):
            for k, v in items_list.items():
                if k in expected and isinstance(v, str) and v.strip() and k not in found:
                    found[k] = v.strip()
        else:
            for k, v in parsed_json.items():
                if k in expected and isinstance(v, str) and v.strip() and k not in found:
                    found[k] = v.strip()

    return found


async def translate_structured(translator, to_lang: str, items: Iterable[tuple[str, str]]) -> dict[str, str]:
    pending = dict(items)
    translated: dict[str, str] = {}
    attempts = max(1, int(getattr(translator, "_RETRY_ATTEMPTS", 3)))

    for attempt in range(attempts):
        if not pending:
            break
        payload = {
            "target_language": to_lang,
            "regions": [{"id": key, "text": text} for key, text in pending.items()],
        }
        prompt = (
            "Translate every region. Return JSON only, matching this schema exactly. "
            "Keep each id unchanged and include one non-empty translation for every id.\n"
            f"Schema: {json.dumps(_response_json_schema(), ensure_ascii=False)}\n"
            f"Input: {json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            request = getattr(translator, "_request_structured_translation", None)
            raw = (
                await request(to_lang, prompt, StructuredTranslationResponse)
                if request is not None
                else await translator._request_translation(to_lang, prompt)
            )
            found = _parse_response(raw, set(pending))
            translated.update(found)
            pending = {key: text for key, text in pending.items() if key not in found}
        except Exception as exc:
            logger.warning(f"Structured translation attempt {attempt + 1}/{attempts} failed: {exc}")
            if attempt == attempts - 1 and not translated:
                raise

    if pending:
        raise StructuredTranslationError(set(pending), translated)
    return translated
