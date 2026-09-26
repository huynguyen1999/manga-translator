"""Translation-result validation used by single-page and batch paths."""

from typing import List

import regex as re

try:
    import py3langid as langid
except ImportError:
    langid = None

from .translators.common import ISO_639_1_TO_VALID_LANGUAGES
from .utils import is_preserved_region
from .config import Translator


def check_repetition_hallucination(text: str, threshold: int = 5, silent: bool = False, *, logger) -> bool:
    """
    检查文本是否包含重复内容（模型幻觉）
    Check if the text contains repetitive content (model hallucination)
    """
    if not text or len(text.strip()) < threshold:
        return False

    # 检查字符级重复
    consecutive_count = 1
    prev_char = None

    for char in text:
        if char == prev_char:
            consecutive_count += 1
            if consecutive_count >= threshold:
                if not silent:
                    logger.warning(f'Detected character repetition hallucination: "{text}" - repeated character: "{char}", consecutive count: {consecutive_count}')
                return True
        else:
            consecutive_count = 1
        prev_char = char

    # 检查词语级重复（按字符分割中文，按空格分割其他语言）
    segments = re.findall(r'[\u4e00-\u9fff]|\S+', text)

    if len(segments) >= threshold:
        consecutive_segments = 1
        prev_segment = None

        for segment in segments:
            if segment == prev_segment:
                consecutive_segments += 1
                if consecutive_segments >= threshold:
                    if not silent:
                        logger.warning(f'Detected word repetition hallucination: "{text}" - repeated segment: "{segment}", consecutive count: {consecutive_segments}')
                    return True
            else:
                consecutive_segments = 1
            prev_segment = segment

    # 检查短语级重复
    words = text.split()
    if len(words) >= threshold * 2:
        for i in range(len(words) - threshold + 1):
            phrase = ' '.join(words[i:i + threshold//2])
            remaining_text = ' '.join(words[i + threshold//2:])
            if phrase in remaining_text:
                phrase_count = text.count(phrase)
                if phrase_count >= 3:  # 降低短语重复检测阈值
                    if not silent:
                        logger.warning(f'Detected phrase repetition hallucination: "{text}" - repeated phrase: "{phrase}", occurrence count: {phrase_count}')
                    return True

    return False


def check_target_language_ratio(text_regions: List, target_lang: str, min_ratio: float = 0.5, *, logger) -> bool:
    """
    检查翻译结果中目标语言的占比是否达到要求
    使用py3langid进行语言检测
    Check if the target language ratio meets the requirement by detecting the merged translation text

    Args:
        text_regions: 文本区域列表
        target_lang: 目标语言代码
        min_ratio: 最小目标语言占比（此参数在新逻辑中不使用，保留为兼容性）

    Returns:
        bool: True表示通过检查，False表示未通过
    """
    translatable_regions = [region for region in (text_regions or []) if not is_preserved_region(region)]
    if len(translatable_regions) <= 10:
        # 如果区域数量不超过10个，跳过此检查
        return True

    # 合并所有翻译文本
    all_translations = []
    for region in translatable_regions:
        translation = getattr(region, 'translation', '')
        if translation and translation.strip():
            all_translations.append(translation.strip())

    if not all_translations:
        logger.debug('No valid translation texts for language ratio check')
        return True

    # 将所有翻译合并为一个文本进行检测
    merged_text = ''.join(all_translations)

    # logger.info(f'Target language check - Merged text preview (first 200 chars): "{merged_text[:200]}"')
    # logger.info(f'Target language check - Total merged text length: {len(merged_text)} characters')
    # logger.info(f'Target language check - Number of regions: {len(all_translations)}')

    # 使用py3langid进行语言检测
    try:
        detected_lang, confidence = langid.classify(merged_text)
        detected_language = ISO_639_1_TO_VALID_LANGUAGES.get(detected_lang, 'UNKNOWN')
        if detected_language != 'UNKNOWN':
            detected_language = detected_language.upper()

        # logger.info(f'Target language check - py3langid result: "{detected_lang}" -> "{detected_language}" (confidence: {confidence:.3f})')
    except Exception as e:
        logger.debug(f'py3langid failed for merged text: {e}')
        detected_language = 'UNKNOWN'
        confidence = -9999

    # 检查检测出的语言是否为目标语言
    is_target_lang = (detected_language == target_lang.upper())

    # logger.info(f'Target language check: Detected language "{detected_language}" using py3langid (confidence: {confidence:.3f})')
    # logger.info(f'Target language check: Target is "{target_lang.upper()}"')
    # logger.info(f'Target language check result: {"PASSED" if is_target_lang else "FAILED"}')

    return is_target_lang


async def validate_translation(
    owner,
    original_text: str,
    translation: str,
    target_lang: str,
    config,
    ctx=None,
    silent: bool = False,
    page_lang_check_result: bool = None,
    *,
    logger,
) -> bool:
    """Validate a translation using the existing page and region checks."""
    if not config.translator.enable_post_translation_check:
        return True

    if not translation or not translation.strip():
        return True

    if page_lang_check_result is None and ctx and ctx.text_regions and len(ctx.text_regions) > 10:
        page_lang_check_result = await owner._check_target_language_ratio(
            ctx.text_regions,
            target_lang,
            min_ratio=0.5,
        )

    if page_lang_check_result is False:
        if not silent:
            logger.debug("Target language ratio check failed for this region")
        return False

    if await owner._check_repetition_hallucination(
        translation,
        config.translator.post_check_repetition_threshold,
        silent,
    ):
        return False

    return True


async def retry_translation_with_validation(owner, region, config, ctx, *, logger) -> str:
    """Retry one region using the translator and validation policy already in use."""
    original_translation = region.translation
    max_attempts = config.translator.post_check_max_retry_attempts

    for attempt in range(max_attempts):
        is_valid = await owner._validate_translation(
            region.text,
            region.translation,
            config.translator.target_lang,
            config,
            ctx=None,
            silent=True,
            page_lang_check_result=True,
        )

        if is_valid:
            if attempt > 0:
                logger.info(
                    f'Post-translation check passed (Attempt {attempt + 1}/{max_attempts}): "{region.translation}"'
                )
            return region.translation

        if attempt < max_attempts - 1:
            logger.warning(
                f'Post-translation check failed (Attempt {attempt + 1}/{max_attempts}), re-translating: "{region.text}"'
            )

            try:
                if config.translator.translator != Translator.none:
                    from .translators import dispatch

                    retranslated = await dispatch(
                        config.translator.translator_gen,
                        [region.text],
                        config.translator,
                        owner.use_mtpe,
                        ctx,
                        'cpu' if owner._gpu_limited_memory else owner.device,
                    )
                    if retranslated:
                        region.translation = config.render.transform_text_case(retranslated[0])
                        logger.info(f'Re-translation finished: "{region.text}" -> "{region.translation}"')
                    else:
                        logger.warning(
                            f'Re-translation failed, keeping original translation: "{original_translation}"'
                        )
                        region.translation = original_translation
                        break
                else:
                    logger.warning('Translator is none, cannot re-translate.')
                    break

            except Exception as e:
                logger.error(f'Error during re-translation: {e}')
                region.translation = original_translation
                break
        else:
            logger.warning(
                f'Post-translation check failed, maximum retry attempts ({max_attempts}) reached, keeping original translation: "{original_translation}"'
            )
            region.translation = original_translation

    return region.translation
