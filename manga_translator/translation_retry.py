"""Page translation completeness checks and retries."""

import regex as re

from .config import Translator
from .translation_errors import TranslationFailure
from .translators.common import TranslationProviderUnavailable
from .translators.gemini_keys import GeminiRetryExhausted
from .utils import is_preserved_region


async def translate_page_with_retries(owner, config, ctx, translations=None, *, logger):
    """Require one usable translation per region before any source text is erased."""
    regions = ctx.text_regions
    region_indices = [i for i, region in enumerate(regions) if not is_preserved_region(region)]
    texts = [regions[i].text for i in region_indices]
    has_preserved_regions = len(region_indices) != len(regions)

    if has_preserved_regions and translations is not None and len(translations) == len(regions):
        translations = [translations[i] for i in region_indices]

    def restore_preserved_regions(values):
        if not has_preserved_regions:
            return list(values)
        result = [None] * len(regions)
        for i, region in enumerate(regions):
            if is_preserved_region(region):
                result[i] = region.text
        for i, translated in zip(region_indices, values):
            result[i] = translated
        return result

    if config.translator.translator in (Translator.none, Translator.original):
        translated = translations if translations is not None else await owner._dispatch_with_context(config, texts, ctx)
        return restore_preserved_regions(translated)

    def validation_reason(source, translated):
        if not isinstance(translated, str) or not translated.strip():
            return f"translation is missing or blank (source: {repr(source)})"
        if (source.strip().casefold() == translated.strip().casefold()
                and re.search(r'[\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Han}]', source)
                and source.strip() != '彡'
                and config.translator.target_lang not in ('JPN', 'CHS', 'CHT', 'KOR')):
            return f"unchanged foreign dialogue: {repr(source)}"
        return None

    def validate_translations(values):
        if not isinstance(values, (list, tuple)):
            return False, f"Translations result is not a list or tuple: {type(values)}"
        if len(values) != len(texts):
            return False, f"Translation count mismatch: expected {len(texts)}, got {len(values)}"
        for i, (source, translated) in enumerate(zip(texts, values)):
            reason = validation_reason(source, translated)
            if reason:
                return False, f"Region {i+1}/{len(texts)} {reason}"
        return True, None

    def keep_for_manual_edit(values):
        if not isinstance(values, (list, tuple)) or len(values) > len(texts):
            return None
        kept = list(values) + [None] * (len(texts) - len(values))
        review_count = 0
        for i, (source, translated) in enumerate(zip(texts, kept)):
            if validation_reason(source, translated):
                kept[i] = translated if isinstance(translated, str) and translated.strip() else source
                region = ctx.text_regions[region_indices[i]]
                region.review_required = True
                region.review_reason = 'translation_validation_failed'
                review_count += 1
        if review_count:
            ctx.manual_review_required = True
        return kept

    def complete(values):
        ok, _ = validate_translations(values)
        if ok:
            return True
        return (
            isinstance(values, (list, tuple))
            and len(values) == len(texts)
            and all(
                not validation_reason(source, translated)
                or getattr(regions[region_indices[i]], 'review_required', False)
                for i, (source, translated) in enumerate(zip(texts, values))
            )
        )

    if complete(translations):
        return restore_preserved_regions(translations)
    if not texts:
        return restore_preserved_regions([])
    attempts = 1 if owner._uses_gemini(config) else 1 + max(1, config.translator.post_check_max_retry_attempts)
    last_error = None
    for attempt in range(attempts):
        try:
            translations = await owner._dispatch_with_context(config, texts, ctx)
            is_valid, reason = validate_translations(translations)
            if is_valid:
                return restore_preserved_regions(translations)
            last_error = ValueError(reason or 'Missing, blank, or untranslated dialogue')
            logger.warning('Page translation validation failed on attempt %s/%s: %s', attempt + 1, attempts, reason)
        except GeminiRetryExhausted:
            raise
        except TranslationProviderUnavailable:
            raise
        except Exception as exc:
            last_error = exc
        if attempt + 1 < attempts:
            logger.warning('Retrying incomplete page translation (%s/%s)', attempt + 1, attempts - 1)
    if config.translator.keep_failed_pages_for_editing:
        kept = keep_for_manual_edit(translations)
        if kept is not None:
            logger.warning('Keeping page for manual editing: %s', last_error)
            return restore_preserved_regions(kept)
    if owner._uses_gemini(config):
        raise GeminiRetryExhausted(f'Gemini page translation failed validation: {last_error}') from last_error
    raise TranslationFailure(f'Page translation failed after {attempts} attempts: {last_error}') from last_error
