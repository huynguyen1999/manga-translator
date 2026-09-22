import json
import re
import uuid
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable

from omegaconf import OmegaConf

from .config import Translator, TranslatorConfig
from .translators import GPT_TRANSLATORS, get_translator


REFUSAL_MARKERS = (
    "i can't assist", "i cannot assist", "i'm unable to", "i am unable to",
    "cannot comply", "can't comply", "safety policy", "content policy",
    "content_filter", "finish_reason: safety", "blocked for safety", "prohibited content",
)

PROFESSIONAL_SYSTEM_PROMPT = """You are a senior Japanese-to-English manga localization professional.
Translate supplied source text faithfully; it may contain mature or explicit fictional material. Preserve meaning,
tone, register, intensity, and character voice. Translate explicit, vulgar, euphemistic, clinical, or mild Japanese
at a comparable level in natural English: do not sanitize explicit language or make mild language more graphic.
Prefer natural English over word-for-word phrasing without changing meaning. Preserve slang and dialect function,
honorific and relationship implications, emotional tone, power dynamics, consent/coercion implications, jokes,
double meanings, intentional awkwardness, hesitation, repetition, and profanity intensity. Do not moralize,
editorialize, summarize, invent explicit details, or continue the depicted scenario. This is translation, not
creative generation. Follow the current analyst, translator, or editor role exactly. Return only the requested
output format."""

PROFESSIONAL_ANALYSIS_SYSTEM_PROMPT = """You are a Japanese manga story analyst preparing compact, neutral metadata
for a translation team. Describe sensitive situations abstractly rather than repeating graphic dialogue; quote a
source expression only when needed as evidence for a translation-relevant linguistic feature. Follow the analyst
role exactly and return only the requested JSON."""


def parse_story_ranges(value: str | None, page_count: int) -> list[tuple[int, int]]:
    if not value:
        return []
    ranges: list[tuple[int, int]] = []
    for part in value.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", part)
        if not match:
            raise ValueError("Story ranges must look like 1-12,13-24")
        start, end = map(int, match.groups())
        if start < 1 or end < start or end > page_count:
            raise ValueError(f"Invalid story range {start}-{end} for {page_count} pages")
        ranges.append((start, end))
    if ranges != sorted(ranges) or any(left[1] + 1 != right[0] for left, right in zip(ranges, ranges[1:])):
        raise ValueError("Story ranges must be ordered, contiguous, and non-overlapping")
    if ranges[0][0] != 1 or ranges[-1][1] != page_count:
        raise ValueError("Story ranges must cover every page")
    return ranges


def _recover_partial_objects(text: str) -> dict[str, Any] | None:
    """Extract complete JSON objects from a truncated LLM response.

    When the model hits its output-token limit it stops mid-stream, leaving the
    outer object and array unclosed.  This function walks the raw text and
    collects every *complete* inner object (depth-0 brace pair inside the
    first ``[``), parses each one independently, and reassembles them under the
    original array key.  Returns ``None`` when no objects can be salvaged.
    """
    # Locate the first array; text before it should contain the outer key name.
    array_start = text.find("[")
    if array_start < 0:
        return None

    key_match = re.search(r'"(\w+)"\s*:\s*\[?\s*$', text[:array_start])
    array_key = key_match.group(1) if key_match else "regions"

    items: list[dict[str, Any]] = []
    i = array_start + 1
    depth = 0
    obj_start: int | None = None
    in_string = False
    escape_next = False

    while i < len(text):
        ch = text[i]
        if escape_next:
            escape_next = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape_next = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "{":
                if depth == 0:
                    obj_start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and obj_start is not None:
                    fragment = text[obj_start : i + 1]
                    try:
                        item = json.loads(re.sub(r",\s*([}\]])", r"\1", fragment))
                        items.append(item)
                    except json.JSONDecodeError:
                        pass
                    obj_start = None
        i += 1

    if not items:
        return None
    return {array_key: items, "_partial": True}


def _json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    # Strip closed <think>...</think> and unclosed <think>... (due to token cutoff)
    text = re.sub(r"<think>[\s\S]*?(?:</think>|$)", "", text, flags=re.I).strip()
    # Strip closed or unclosed markdown code fences
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)(?:```|$)", text, re.I)
    if fenced:
        candidate = fenced.group(1).strip()
        if "{" in candidate:
            text = candidate

    start = text.find("{")
    if start < 0:
        raise ValueError("Model did not return a JSON object")

    end = text.rfind("}")
    if end > start:
        cleaned = re.sub(r",\s*([}\]])", r"\1", text[start : end + 1])
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    # The response was likely cut off before closing braces or had formatting errors.
    # Salvage every complete object that was successfully emitted.
    recovered = _recover_partial_objects(text[start:])
    if recovered is not None:
        return recovered
    raise ValueError("Model did not return a JSON object")


def _is_refusal(value: BaseException | str) -> bool:
    text = str(value).casefold()
    return any(marker in text for marker in REFUSAL_MARKERS)


def _provider_name(key: Translator) -> str:
    return getattr(key, "value", str(key))


class ProfessionalTranslator:
    """Sequential, auditable Japanese-to-English localization for one prepared batch."""

    def __init__(
        self,
        config: TranslatorConfig,
        progress: Callable[[str], Awaitable[None]] | None = None,
    ):
        if config.target_lang != "ENG":
            raise ValueError("Professional translation currently supports Japanese to English only")
        self.config = config
        self.primary = config.translator
        if self.primary not in GPT_TRANSLATORS:
            raise ValueError("Professional translation requires an AI translator")
        self.provenance: list[dict[str, str]] = []
        self._last_model: str | None = None
        self._progress = progress

    async def _report(self, state: str) -> None:
        if self._progress is not None:
            await self._progress(state)

    async def _request(self, stage: str, prompt: str) -> tuple[str, str]:
        try:
            raw = await self._request_with(self.primary, prompt, stage)
            if _is_refusal(raw):
                raise PermissionError(raw)
            return raw, _provider_name(self.primary)
        except Exception as exc:
            if not _is_refusal(exc) or self.primary == Translator.deepseek:
                raise
            raw = await self._request_with(Translator.deepseek, prompt, stage)
            if _is_refusal(raw):
                raise PermissionError(raw)
            return raw, _provider_name(Translator.deepseek)

    async def _request_with(self, key: Translator, prompt: str, stage: str = "draft") -> str:
        translator = get_translator(key)
        translator.parse_args(self.config)
        request = getattr(translator, "_request_translation", None)
        if request is None:
            raise ValueError(f"{_provider_name(key)} does not support professional prompts")
        system_prompt = PROFESSIONAL_ANALYSIS_SYSTEM_PROMPT if stage.startswith("analysis") else PROFESSIONAL_SYSTEM_PROMPT
        original_config = getattr(translator, "config", None)
        original_cache_flag = getattr(translator, "_canUseCache", None)
        had_instance_system_template = "_CHAT_SYSTEM_TEMPLATE" in getattr(translator, "__dict__", {})
        original_system_template = getattr(translator, "_CHAT_SYSTEM_TEMPLATE", None)
        translator.config = OmegaConf.merge(
            original_config or {},
            {"chat_system_template": system_prompt, "chat_sample": {}},
        )
        # DeepSeek reads the class template directly while the other GPT providers
        # use the config-backed property. Set both paths for the professional call.
        translator._CHAT_SYSTEM_TEMPLATE = system_prompt
        translator._professional_json_mode = True
        if original_cache_flag is not None:
            translator._canUseCache = False
        try:
            raw = await request("English", prompt)
            current_model = getattr(getattr(translator, "key_manager", None), "current_model", None)
            model = current_model or getattr(translator, "model_name", None) or getattr(translator, "model", None) or getattr(translator, "MODEL", None)
            self._last_model = str(model) if model else None
            return raw
        finally:
            translator.config = original_config
            translator.__dict__.pop("_professional_json_mode", None)
            if had_instance_system_template:
                translator._CHAT_SYSTEM_TEMPLATE = original_system_template
            else:
                translator.__dict__.pop("_CHAT_SYSTEM_TEMPLATE", None)
            if original_cache_flag is not None:
                translator._canUseCache = original_cache_flag

    async def _json_request(self, stage: str, prompt: str) -> tuple[dict[str, Any], str]:
        last_error: Exception | None = None
        for _ in range(2):
            try:
                raw, provider = await self._request(stage, prompt)
                data = _json_object(raw)
                provenance = {"stage": stage, "provider": provider}
                if self._last_model:
                    provenance["model"] = self._last_model
                self.provenance.append(provenance)
                return data, provider
            except PermissionError:
                raise
            except Exception as exc:
                last_error = exc

        # If primary failed to return a valid JSON object after attempts and primary is not deepseek,
        # try falling back to DeepSeek before failing the stage.
        if self.primary != Translator.deepseek:
            try:
                raw = await self._request_with(Translator.deepseek, prompt, stage)
                if not _is_refusal(raw):
                    data = _json_object(raw)
                    provider = _provider_name(Translator.deepseek)
                    provenance = {"stage": stage, "provider": provider}
                    if self._last_model:
                        provenance["model"] = self._last_model
                    self.provenance.append(provenance)
                    return data, provider
            except Exception as exc:
                last_error = exc

        raise ValueError(f"Invalid {stage} response: {last_error}") from last_error

    async def analyze(self, pages: list[dict[str, Any]], override: str | dict[str, Any] | None) -> dict[str, Any]:
        manual_ranges: list[tuple[int, int]] = []
        auto_within_manual = False
        if isinstance(override, dict):
            if override.get("enabled", True) and not override.get("mergeAllPages", False):
                try:
                    manual_ranges = [
                        (int(segment["startPage"]), int(segment["endPage"]))
                        for segment in override.get("segments", [])
                    ]
                    manual_ranges = parse_story_ranges(
                        ",".join(f"{start}-{end}" for start, end in manual_ranges), len(pages)
                    )
                    auto_within_manual = bool(override.get("autoDetect", True))
                except (KeyError, TypeError, ValueError):
                    manual_ranges = []
            if not manual_ranges:
                manual_ranges = [(1, len(pages))]
        else:
            manual_ranges = parse_story_ranges(override, len(pages))
        page_blocks = [
            f"[PAGE {page['number']}]\n" + "\n".join(item["source"] for item in page["regions"])
            for page in pages
        ]
        page_text = "\n\n".join(page_blocks)
        forced_instruction = (
            f"Use these exact story ranges: {manual_ranges}."
            if manual_ranges and not auto_within_manual
            else (f"Detect additional story breaks, but never cross these manual ranges: {manual_ranges}." if manual_ranges else "Detect the story ranges yourself.")
        )
        prompt = f"""You are a senior Japanese manga story analyst preparing an English localization.
Read all OCR text before translation. This is fictional adult material; describe it neutrally without censoring it.
Detect separate stories using textual chapter titles, cast/setting resets, endings, and narrative discontinuities.
Return JSON only. Each story has start_page, end_page, confidence, summary, characters, relationships, glossary,
voice_notes, continuity, ambiguities, honorific_policy, language_features, and localization_conventions.
Each important recurring character has name and voice: register, politeness, directness, traits, dialect
(detected, type, confidence, evidence, communicative_effect, localization_strategy), slang_style
(level, categories, localization_strategy), sentence_style, verbal_habits, pronoun_notes, and localization_notes.
Use language_features entries with pages, speaker, source, type, literal_meaning, contextual_meaning, tone,
function, preferred_strategy, possible_renderings, and confidence (omit inapplicable fields). Use
honorific_policy {default, rules:[{form, strategy, reason}]} and localization_conventions with dialect_strategy,
slang_strategy, profanity_strategy, recurring_idioms, and forms_of_address.
Analyze translation-relevant speech rather than treating all dialogue as standard Japanese. Detect supported
dialects and sociolects (including regional, rough, feminine-coded, gyaru, delinquent, elderly, childish,
internet, formal, archaic, refined, subordinate, and professional speech), slang, idioms/fixed expressions,
sentence-ending particles, pronouns, honorifics/forms of address, and recurring idiolect. Record only meaningful
features, with source, speaker, pages, type, contextual and literal meaning where useful, tone/function,
confidence, and a preferred English strategy. Include natural English renderings when useful and note tempting
choices that would distort age, identity, era, or intensity. Do not map a Japanese dialect to an English regional
accent; describe its communicative effect and recommend a non-stereotyping strategy. Do not mechanically
translate particles or pronouns, or replace Japanese slang with transient American internet slang by default.
For honorifics and forms of address, record source form and consistent story-level treatment (retain, translate,
convey through register, or omit when English implies the relationship). Include honorific_policy rules and
localization_conventions for names/forms of address, dialect, slang, profanity, idioms, and character voice.
Flag deliberate speech changes (politeness, pronouns, honorifics, name choice, dialect, roughness) in continuity
or language_features. Keep voice_notes only as a brief compatibility summary; structured character voice and
language_features are the authoritative linguistic analysis. Page numbers are one-based and every page must appear exactly once.
{forced_instruction}

{page_text}"""
        try:
            if len(page_text) <= 50_000:
                analysis, _ = await self._json_request("analysis", prompt)
            else:
                # ponytail: character windows avoid provider-specific token APIs; replace if real limits demand it.
                windows, current = [], []
                for block in page_blocks:
                    if current and len("\n\n".join(current + [block])) > 40_000:
                        windows.append("\n\n".join(current))
                        current = []
                    current.append(block)
                if current:
                    windows.append("\n\n".join(current))
                page_blocks = None  # release — not needed for API calls

                # Capture the sentinel value before releasing it so we can still
                # substitute each window into the base prompt.
                _page_text_sentinel = page_text
                page_text = None  # release full concatenated text

                partials = []
                for window in windows:
                    window_prompt = prompt.replace(_page_text_sentinel, window)
                    partial, _ = await self._json_request("analysis-window", window_prompt)
                    partials.append(partial)
                    window_prompt = None  # release after use
                windows = None  # release all window strings before consolidation

                analysis, _ = await self._json_request(
                    "analysis-consolidation",
                    "Consolidate these ordered manga analysis windows into the requested stories JSON. "
                    "Preserve all structured character voice, honorific_policy, language_features, and "
                    "localization_conventions alongside the existing story fields. Preserve absolute page "
                    "numbers, cover every page exactly once, and obey this boundary rule: "
                    f"{forced_instruction}\n{json.dumps(partials, ensure_ascii=False)}",
                )
                partials = None  # release after consolidation
        except Exception as exc:
            analysis = {"stories": [], "analysis_error": str(exc)}


        stories = analysis.get("stories") if isinstance(analysis.get("stories"), list) else []
        ranges = []
        for story in stories:
            try:
                ranges.append((int(story["start_page"]), int(story["end_page"])))
            except (KeyError, TypeError, ValueError):
                ranges = []
                break
        expected = manual_ranges or [(1, len(pages))]
        valid_auto = (
            bool(ranges) and ranges[0][0] == 1 and ranges[-1][1] == len(pages)
            and all(1 <= start <= end <= len(pages) for start, end in ranges)
            and all(left[1] + 1 == right[0] for left, right in zip(ranges, ranges[1:]))
        )
        within_manual = all(any(start <= story_start and story_end <= end for start, end in manual_ranges) for story_start, story_end in ranges) if manual_ranges else True
        if manual_ranges and not auto_within_manual and ranges != manual_ranges:
            stories = [{"start_page": start, "end_page": end, "confidence": 0.0, "overridden": True,
                        "ambiguities": ["The analyst did not honor the configured story range"]}
                       for start, end in manual_ranges]
        elif (not manual_ranges or auto_within_manual) and (not valid_auto or not within_manual):
            stories = ([
                {"start_page": start, "end_page": end, "confidence": 0.0,
                 "ambiguities": ["Automatic story segmentation could not be validated"]}
                for start, end in expected
            ] if manual_ranges else [{
                "start_page": 1, "end_page": len(pages), "confidence": 0.0,
                "ambiguities": ["Automatic story segmentation could not be validated"],
            }])
        elif manual_ranges and not auto_within_manual:
            for story in stories:
                story["overridden"] = True
        analysis["stories"] = stories
        return analysis

    async def localize_story(
        self,
        story: dict[str, Any],
        pages: list[dict[str, Any]],
        story_index: int = 1,
        story_count: int = 1,
    ) -> None:
        previous = ""
        guide = json.dumps(story, ensure_ascii=False)
        chunk_size = max(1, int(self.config.translation_batch_size))
        chunk_count = (len(pages) + chunk_size - 1) // chunk_size
        for offset in range(0, len(pages), chunk_size):
            chunk_index = offset // chunk_size + 1
            await self._report(f"drafting:{story_index}/{story_count}:{chunk_index}/{chunk_count}")
            chunk = pages[offset:offset + chunk_size]
            payload = [
                {"page": page["number"], "regions": [{"id": item["id"], "japanese": item["source"]} for item in page["regions"]]}
                for page in chunk
            ]
            expected = [item for page in payload for item in page["regions"]]
            if not expected:
                continue
            prompt = f"""You are the first-pass Japanese-to-English translator preparing a working draft for a separate senior editor.
Use the story guide and earlier context to resolve references, speakers, pronouns, and terminology, applying its
character voice, language_features, honorific_policy, and localization_conventions consistently. Stay close to
the Japanese meaning, tone, explicitness, and uncertainty. Preserve meaningful honorifics and cultural
terms. This is an accurate translation draft, not the final polished localization: do not spend this pass
polishing idioms, rhythm, or localization flourishes, and do not invent context. Translate every region in
every supplied page. Return JSON only:
{{"regions":[{{"id":"...","translation":"...","confidence":0.0,"review_reasons":[]}}]}}.
Include every id once.

STORY GUIDE: {guide}
FINALIZED EARLIER ENGLISH: {previous[-8000:]}
CURRENT PAGES: {json.dumps(payload, ensure_ascii=False)}"""
            values: dict[str, dict[str, Any]] = {}
            provider = _provider_name(self.primary)
            draft_failed = False
            try:
                data, provider = await self._json_request("draft", prompt)
                values = self._regions(data, expected)
            except ValueError as exc:
                if "Missing region ids" not in str(exc) or len(chunk) <= 1:
                    # Not a missing-ID error, or already at single-page granularity — fall back.
                    draft_failed = True
                    provider = _provider_name(Translator.deepseek)
                    values = {item["id"]: {
                        "translation": item["japanese"], "confidence": 0.0,
                        "review_reasons": [f"unresolved_translation: {exc}"],
                    } for item in expected}
                else:
                    # Retry each page individually to rescue as many regions as possible.
                    for sub_page in chunk:
                        sub_payload = [{"page": sub_page["number"], "regions": [
                            {"id": item["id"], "japanese": item["source"]}
                            for item in sub_page["regions"]
                        ]}]
                        sub_expected = sub_payload[0]["regions"]
                        if not sub_expected:
                            continue
                        sub_prompt = prompt.replace(
                            json.dumps(payload, ensure_ascii=False),
                            json.dumps(sub_payload, ensure_ascii=False),
                        )
                        try:
                            sub_data, sub_provider = await self._json_request("draft", sub_prompt)
                            sub_values = self._regions(sub_data, sub_expected)
                            provider = sub_provider
                            values.update(sub_values)
                        except Exception as sub_exc:
                            draft_failed = True
                            for item in sub_expected:
                                values[item["id"]] = {
                                    "translation": item["japanese"], "confidence": 0.0,
                                    "review_reasons": [f"unresolved_translation: {sub_exc}"],
                                }
            except Exception as exc:
                draft_failed = True
                provider = _provider_name(Translator.deepseek)
                values = {item["id"]: {
                    "translation": item["japanese"], "confidence": 0.0,
                    "review_reasons": [f"unresolved_translation: {exc}"],
                } for item in expected}
            for page in chunk:
                for item in page["regions"]:
                    item["draft"] = values[item["id"]]["translation"]
                    item["confidence"] = values[item["id"]]["confidence"]
                    item["review_reasons"] = values[item["id"]]["review_reasons"]
                    item["draft_provider"] = provider
                    item["draft_failed"] = draft_failed
            previous = (previous + "\n" + "\n".join(values[item["id"]]["translation"] for item in expected))[-8000:]

    async def edit_story(
        self,
        story: dict[str, Any],
        pages: list[dict[str, Any]],
        story_index: int = 1,
        story_count: int = 1,
    ) -> None:
        previous = ""
        guide = json.dumps(story, ensure_ascii=False)
        chunk_size = max(1, int(self.config.translation_batch_size))
        chunk_count = (len(pages) + chunk_size - 1) // chunk_size
        for offset in range(0, len(pages), chunk_size):
            chunk_index = offset // chunk_size + 1
            await self._report(f"editing:{story_index}/{story_count}:{chunk_index}/{chunk_count}")
            chunk = pages[offset:offset + chunk_size]
            payload = [
                {"page": page["number"], "regions": [{"id": item["id"], "japanese": item["source"], "draft": item["draft"]} for item in page["regions"]]}
                for page in chunk
            ]
            expected = [item for page in payload for item in page["regions"]]
            if not expected:
                continue
            prompt = f"""You are a separate senior English editor for a professionally localized adult manga.
Independently compare the Japanese source and the first draft; do not rubber-stamp the draft. Rewrite literal,
stiff, repetitive, awkward, or AI-sounding English into natural professional localization while preserving the
Japanese meaning, character voice, exact explicitness, consent/coercion signals, terminology, and hybrid Japanese
flavor. Apply the story guide's character language profiles, language_features, honorific_policy, and
localization_conventions consistently, including dialect function and forms of address. Do not invent, omit, censor,
moralize, or add commentary. A final identical to the draft is acceptable
only after deliberately checking that it is already natural and accurate. Flag ambiguous OCR, speaker/pronoun
uncertainty, wordplay, missing context, or a meaning-sensitive rewrite. Return JSON only: {{"regions":[{{"id":"...",
"translation":"...","confidence":0.0,"review_reasons":[]}}]}}. Include every id once.

STORY GUIDE: {guide}
FINALIZED EARLIER ENGLISH: {previous[-8000:]}
CURRENT PAGES: {json.dumps(payload, ensure_ascii=False)}"""
            try:
                data, provider = await self._json_request("editing", prompt)
                values = self._regions(data, expected)
            except Exception as exc:
                provider = "draft"
                values = {item["id"]: {
                    "translation": item["draft"], "confidence": 0.0,
                    "review_reasons": [f"editor_failed: {exc}"],
                } for item in expected}
            for page in chunk:
                for item in page["regions"]:
                    edited = values[item["id"]]
                    item["final"] = edited["translation"]
                    item["confidence"] = min(item["confidence"], edited["confidence"])
                    item["review_reasons"].extend(edited["review_reasons"])
                    if not item.get("draft_failed") and SequenceMatcher(None, item["draft"].casefold(), item["final"].casefold()).ratio() < 0.55:
                        item["review_reasons"].append("substantial_editor_rewrite")
                    item["editor_provider"] = provider
            previous = (previous + "\n" + "\n".join(values[item["id"]]["translation"] for item in expected))[-8000:]

    @staticmethod
    def _regions(data: dict[str, Any], expected: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
        expected_ids = {item["id"] for item in expected}
        found: dict[str, dict[str, Any]] = {}
        for item in data.get("regions", []):
            item_id = str(item.get("id", ""))
            translation = str(item.get("translation", "")).strip()
            if item_id not in expected_ids or not translation or item_id in found:
                continue
            reasons = item.get("review_reasons", [])
            found[item_id] = {
                "translation": translation,
                "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
                "review_reasons": [str(reason) for reason in reasons] if isinstance(reasons, list) else [str(reasons)],
            }
        missing = expected_ids - set(found)
        if missing:
            if data.get("_partial"):
                # Truncation recovery: fill missing regions with the best
                # available fallback text so translated portions still render.
                expected_by_id = {item["id"]: item for item in expected}
                for region_id in missing:
                    src = expected_by_id.get(region_id, {})
                    fallback = src.get("draft") or src.get("japanese") or region_id
                    found[region_id] = {
                        "translation": fallback,
                        "confidence": 0.0,
                        "review_reasons": ["response_truncated"],
                    }
            else:
                raise ValueError(f"Missing region ids: {sorted(missing)}")
        return found


async def translate_professionally(
    contexts_with_configs: list[tuple],
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> list[tuple]:
    ordered = sorted(contexts_with_configs, key=lambda pair: pair[1].page_order or 0)
    config = ordered[0][1].translator
    engine = ProfessionalTranslator(config, progress=progress)
    pages = []
    for number, (ctx, _) in enumerate(ordered, 1):
        regions = []
        for region in ctx.text_regions or []:
            if not getattr(region, "region_id", None):
                region.region_id = uuid.uuid4().hex
            regions.append({"id": region.region_id, "source": region.text})
        pages.append({"number": number, "ctx": ctx, "regions": regions})

    analysis = await engine.analyze(pages, config.story_plan or config.story_page_ranges)
    story_count = len(analysis["stories"])
    for story_index, story in enumerate(analysis["stories"]):
        story_pages = pages[int(story["start_page"]) - 1:int(story["end_page"])]
        await engine.localize_story(story, story_pages, story_index + 1, story_count)
        await engine.edit_story(story, story_pages, story_index + 1, story_count)
        for page in story_pages:
            page["story_index"] = story_index
            if float(story.get("confidence", 0.0)) < 0.7:
                for item in page["regions"]:
                    item["review_reasons"].append("low_confidence_story_boundary")

    by_id = {item["id"]: item for page in pages for item in page["regions"]}
    for page in pages:
        ctx = page["ctx"]
        ctx.result_documents = ctx.result_documents or {}
        render_cfg = getattr(config, "render", None)
        for region in ctx.text_regions or []:
            item = by_id[region.region_id]
            final_text = item["final"]
            if render_cfg and hasattr(render_cfg, "transform_text_case"):
                final_text = render_cfg.transform_text_case(final_text)
            elif render_cfg and getattr(render_cfg, "uppercase", False):
                final_text = final_text.upper()
            elif render_cfg and getattr(render_cfg, "lowercase", False):
                final_text = final_text.lower()
            region.translation = final_text
            region.target_lang = "ENG"
            region.review_required = bool(item["review_reasons"]) or item["confidence"] < 0.7
            region.review_reason = "; ".join(dict.fromkeys(item["review_reasons"]))
            ctx.manual_review_required = bool(getattr(ctx, "manual_review_required", False) or region.review_required)
        ctx.result_documents["professional_translation.json"] = {
            "mode": "professional", "storyPlan": config.story_plan, "analysis": analysis, "storyIndex": page.get("story_index"),
            "regions": page["regions"], "provenance": engine.provenance,
        }
    return ordered
