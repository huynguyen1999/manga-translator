"""Batch translation coordination for MangaTranslator."""

from datetime import datetime, timezone
import regex as re
import time
import uuid
from typing import List

from manga_translator.config import Translator
from manga_translator.pipeline.run import serialize_regions
from manga_translator.translators.gemini_keys import GeminiRetryExhausted
from manga_translator.translators.structured import StructuredTranslationError
from manga_translator.translation_errors import TranslationFailure
from manga_translator.utils import is_preserved_region


async def batch_translate_contexts(
    owner, contexts_with_configs: List[tuple], batch_size: int, *, logger
) -> List[tuple]:
    """Translate a bounded group of contexts while retaining page order."""
    results = []
    batches = []
    batch = []
    translatable_pages = 0
    for pair in contexts_with_configs:
        batch.append(pair)
        if pair[0].text_regions:
            translatable_pages += 1
        if translatable_pages == batch_size:
            batches.append(batch)
            batch = []
            translatable_pages = 0
    if batch:
        batches.append(batch)

    # Empty pages do not consume the configurable translation batch size.
    for batch_index, batch in enumerate(batches):
        logger.info(f'Processing translation batch {batch_index + 1}/{len(batches)}')

        # 收集当前批次的所有文本
        all_texts = []
        all_text_ids = []

        for ctx_idx, (ctx, config) in enumerate(batch):
            if ctx.result_documents is None:
                ctx.result_documents = {}
            if not ctx.text_regions:
                continue

            for region_idx, region in enumerate(ctx.text_regions):
                if not getattr(region, 'region_id', None):
                    region.region_id = uuid.uuid4().hex
                if is_preserved_region(region):
                    region.translation = region.text
                    region.target_lang = config.translator.target_lang
                    region._alignment = config.render.alignment
                    region._direction = config.render.direction
                    continue
                all_texts.append(region.text)
                all_text_ids.append(region.region_id)

        if not all_texts:
            # 当前批次没有需要翻译的文本
            results.extend(batch)
            continue

        # 批量翻译
        trans_start_time = time.monotonic()
        trans_start_iso = datetime.now(timezone.utc).isoformat()
        try:
            await owner._report_progress('translating')
            # 使用第一个配置进行翻译（假设批次内配置相同）
            sample_config = batch[0][1] if batch else None
            if sample_config:
                # 支持批量翻译 - 传递所有批次上下文
                batch_contexts = [ctx for ctx, config in batch]
                translated_texts = await owner._batch_translate_texts(
                    all_texts, sample_config, batch[0][0], batch_contexts,
                    text_ids=all_text_ids,
                )
            else:
                translated_texts = all_texts  # 无法翻译时保持原文

            if len(translated_texts) != len(all_texts):
                raise TranslationFailure('Batch returned an incorrect number of translations')

            trans_duration_ms = round((time.monotonic() - trans_start_time) * 1000)
            trans_finished_iso = datetime.now(timezone.utc).isoformat()

            translations_by_id = dict(zip(all_text_ids, translated_texts))
            for ctx_idx, (ctx, config) in enumerate(batch):
                if not ctx.text_regions:  # 检查text_regions是否为None或空
                    continue
                ctx.translation_duration_ms = trans_duration_ms
                ctx.translation_started_at = trans_start_iso
                ctx.translation_finished_at = trans_finished_iso
                for region_idx, region in enumerate(ctx.text_regions):
                    if region.region_id in translations_by_id:
                        region.translation = translations_by_id[region.region_id]
                        region.target_lang = config.translator.target_lang
                        region._alignment = config.render.alignment
                        region._direction = config.render.direction

            # 应用后处理逻辑（括号修正、过滤等）
            for ctx, config in batch:
                if ctx.text_regions:
                    try:
                        ctx.text_regions = await owner._apply_post_translation_processing(ctx, config)
                        ctx.result_documents['translations.json'] = serialize_regions(ctx.text_regions)
                        translator_config = getattr(config, "translator", None)
                        translator_val = getattr(getattr(translator_config, "translator", None), "value", None)
                        translator_val = translator_val or str(getattr(translator_config, "translator", "unknown"))
                        model_val = getattr(ctx, "translator_model", None) or getattr(ctx, "offline_model", None) or getattr(ctx, "gemini_model", None)
                        ctx.result_documents['translation_detail.json'] = {
                            "processedAt": trans_finished_iso,
                            "startedAt": trans_start_iso,
                            "finishedAt": trans_finished_iso,
                            "durationMs": trans_duration_ms,
                            "translator": {
                                "name": translator_val,
                                "model": model_val,
                                "sourceLanguage": getattr(translator_config, "source_lang", "auto"),
                                "targetLanguage": getattr(translator_config, "target_lang", None),
                            },
                            "request": [
                                {"id": getattr(r, "region_id", str(i)), "text": getattr(r, "text_raw", r.text)}
                                for i, r in enumerate(ctx.text_regions)
                                if not is_preserved_region(r)
                            ],
                            "response": [
                                {"id": getattr(r, "region_id", str(i)), "text": r.translation}
                                for i, r in enumerate(ctx.text_regions)
                            ],
                        }
                    except Exception as exc:
                        ctx.translation_error = str(exc)
                        ctx.text_regions = []
                        ctx.result = None

            # 批次级别的目标语言检查
            if batch and batch[0][1].translator.enable_post_translation_check:
                # 收集批次内所有页面的filtered regions
                all_batch_regions = []
                for ctx, config in batch:
                    if ctx.text_regions:
                        all_batch_regions.extend(ctx.text_regions)

                # 进行批次级别的目标语言检查
                batch_lang_check_result = True
                if sum(not is_preserved_region(region) for region in all_batch_regions) > 10:
                    sample_config = batch[0][1]
                    logger.info(f"Starting batch-level target language check with {len(all_batch_regions)} regions...")
                    batch_lang_check_result = await owner._check_target_language_ratio(
                        all_batch_regions,
                        sample_config.translator.target_lang,
                        min_ratio=0.5
                    )

                    if not batch_lang_check_result:
                        logger.warning("Batch-level target language ratio check failed")

                        if owner._uses_gemini(sample_config):
                            raise GeminiRetryExhausted("Gemini batch failed the target-language check.")

                        # 批次重新翻译逻辑
                        max_batch_retry = sample_config.translator.post_check_max_retry_attempts
                        batch_retry_count = 0

                        while batch_retry_count < max_batch_retry and not batch_lang_check_result:
                            batch_retry_count += 1
                            logger.warning(f"Starting batch retry {batch_retry_count}/{max_batch_retry}")

                            # 重新翻译批次内所有区域
                            all_original_texts = []
                            region_mapping = []  # 记录每个text属于哪个ctx

                            for ctx_idx, (ctx, config) in enumerate(batch):
                                if ctx.text_regions:
                                    for region in ctx.text_regions:
                                        if (not is_preserved_region(region)
                                                and hasattr(region, 'text') and region.text):
                                            all_original_texts.append(region.text)
                                            region_mapping.append((ctx_idx, region))

                            if all_original_texts:
                                try:
                                    # 重新批量翻译
                                    logger.info(f"Retrying translation for {len(all_original_texts)} regions...")
                                    new_translations = await owner._batch_translate_texts(all_original_texts, sample_config, batch[0][0])

                                    # 更新翻译结果到各个region
                                    for i, (ctx_idx, region) in enumerate(region_mapping):
                                        if i < len(new_translations) and new_translations[i]:
                                            old_translation = region.translation
                                            region.translation = new_translations[i]
                                            logger.debug(f"Region {i+1} translation updated: '{old_translation}' -> '{new_translations[i]}'")

                                    # 重新收集所有regions并检查目标语言比例
                                    all_batch_regions = []
                                    for ctx, config in batch:
                                        if ctx.text_regions:
                                            all_batch_regions.extend(ctx.text_regions)

                                    logger.info(f"Re-checking batch-level target language ratio after batch retry {batch_retry_count}...")
                                    batch_lang_check_result = await owner._check_target_language_ratio(
                                        all_batch_regions,
                                        sample_config.translator.target_lang,
                                        min_ratio=0.5
                                    )

                                    if batch_lang_check_result:
                                        logger.info(f"Batch-level target language check passed")
                                        break
                                    else:
                                        logger.warning(f"Batch-level target language check still failed")

                                except Exception as e:
                                    logger.error(f"Error during batch retry {batch_retry_count}: {e}")
                                    break
                            else:
                                logger.warning("No text found for batch retry")
                                break

                        if not batch_lang_check_result:
                            logger.error(f"Batch-level target language check failed after all {max_batch_retry} batch retries")
                else:
                    logger.info(f"Skipping batch-level target language check: only {len(all_batch_regions)} regions (threshold: 10)")

                # 统一的成功信息
                if batch_lang_check_result:
                    logger.info("All translation regions passed post-translation check.")
                else:
                    logger.warning("Some translation regions failed post-translation check.")

            # 过滤逻辑（简化版本，保留主要过滤条件）
            for ctx, config in batch:
                if ctx.text_regions:
                    new_text_regions = []
                    for region in ctx.text_regions:
                        if is_preserved_region(region):
                            region.translation = region.text
                            new_text_regions.append(region)
                            continue
                        should_filter = False
                        filter_reason = ""

                        if not region.translation.strip():
                            should_filter = True
                            filter_reason = "Translation contain blank areas"
                        elif config.translator.translator != Translator.none:
                            if region.translation.isnumeric():
                                should_filter = True
                                filter_reason = "Numeric translation"
                            elif config.filter_text and re.search(config.re_filter_text, region.translation):
                                should_filter = True
                                filter_reason = f"Matched filter text: {config.filter_text}"
                            elif (not getattr(region, 'review_required', False)
                                  and not config.translator.translator == Translator.original):
                                text_equal = region.text.lower().strip() == region.translation.lower().strip()
                                if text_equal:
                                    should_filter = True
                                    filter_reason = "Translation identical to original"

                        if should_filter:
                            region.review_required = True
                            region.review_reason = filter_reason
                            ctx.manual_review_required = True
                            if region.translation.strip():
                                logger.info(f'Filtered out: {region.translation}')
                                logger.info(f'Reason: {filter_reason}')
                        new_text_regions.append(region)
                    ctx.text_regions = new_text_regions

            results.extend(batch)

        except StructuredTranslationError as e:
            logger.error(f"Incomplete structured batch translation: {e}")
            for ctx, config in batch:
                missing = [
                    region for region in (ctx.text_regions or [])
                    if not is_preserved_region(region) and region.region_id not in e.translated
                ]
                if missing:
                    try:
                        values = await owner._translate_page_with_retries(config, ctx)
                        for region, value in zip(ctx.text_regions, values):
                            region.translation = value
                            region.target_lang = config.translator.target_lang
                            region._alignment = config.render.alignment
                            region._direction = config.render.direction
                        ctx.text_regions = await owner._apply_post_translation_processing(ctx, config)
                        ctx.result_documents['translations.json'] = serialize_regions(ctx.text_regions)
                    except Exception as exc:
                        ctx.translation_error = str(exc)
                        ctx.result = None
                else:
                    for region in ctx.text_regions:
                        region.translation = (
                            region.text if is_preserved_region(region)
                            else e.translated[region.region_id]
                        )
                        region.target_lang = config.translator.target_lang
                        region._alignment = config.render.alignment
                        region._direction = config.render.direction
                    ctx.text_regions = await owner._apply_post_translation_processing(ctx, config)
                    ctx.result_documents['translations.json'] = serialize_regions(ctx.text_regions)
                results.append((ctx, config))
        except Exception as e:
            logger.error(f"Error in batch translation: {e}")
            # Retry each page independently; one unavailable translation must
            # not turn every page into untranslated output or stop the batch.
            for ctx, config in batch:
                if ctx.text_regions:
                    try:
                        values = await owner._translate_page_with_retries(config, ctx)
                        for region, value in zip(ctx.text_regions, values):
                            region.translation = value
                            region.target_lang = config.translator.target_lang
                            region._alignment = config.render.alignment
                            region._direction = config.render.direction
                        ctx.text_regions = await owner._apply_post_translation_processing(ctx, config)
                        ctx.result_documents['translations.json'] = serialize_regions(ctx.text_regions)
                    except Exception as exc:
                        ctx.translation_error = str(exc)
                        ctx.result = None
                results.append((ctx, config))

        # 强制垃圾回收以释放内存
        owner._empty_device_cache()

    return results
