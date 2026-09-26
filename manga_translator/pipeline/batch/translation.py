"""Batch translation execution for grouped pipeline contexts."""

import asyncio
from typing import List

from manga_translator import Config, Context
from manga_translator.config import Translator
from manga_translator.translators import (
    GPT_TRANSLATORS,
    dispatch as dispatch_translation,
    dispatch_structured as dispatch_structured_translation,
)
from manga_translator.utils import is_preserved_region


async def concurrent_translate_contexts(owner, contexts_with_configs, *, logger):
    '\n        并发处理翻译步骤，为每个图片单独发送翻译请求，避免合并大批次\n        '
    # 在并发模式下，先保存所有页面的原文用于上下文
    batch_original_texts = []  # 存储当前批次的原文
    if owner.context_size > 0:
        for i, (ctx, config) in enumerate(contexts_with_configs):
            if ctx.text_regions:
                # 保存当前页面的原文
                page_texts = {}
                for j, region in enumerate(ctx.text_regions):
                    if not is_preserved_region(region):
                        page_texts[j] = region.text
                batch_original_texts.append(page_texts)

                # 确保 _original_page_texts 有足够的长度
                while len(owner._original_page_texts) <= len(owner.all_page_translations) + i:
                    owner._original_page_texts.append({})

                owner._original_page_texts[len(owner.all_page_translations) + i] = page_texts
            else:
                batch_original_texts.append({})

    async def translate_single_context(ctx_config_pair_with_index):
        """翻译单个context的异步函数"""
        ctx, config, page_index, batch_index = ctx_config_pair_with_index
        try:
            if not ctx.text_regions:
                return ctx, config

            # Preserved source annotations do not enter translation requests.
            translatable_indices = [
                i for i, region in enumerate(ctx.text_regions)
                if not is_preserved_region(region)
            ]
            texts = [ctx.text_regions[i].text for i in translatable_indices]

            translated_texts = [None] * len(ctx.text_regions)
            for i, region in enumerate(ctx.text_regions):
                if is_preserved_region(region):
                    translated_texts[i] = region.text

            if texts:
                logger.debug(f'Translating {len(texts)} regions for single image in concurrent mode (page {page_index}, batch {batch_index})')

                # 单独翻译这一张图片的文本，传递页面索引和批次索引用于正确的上下文
                try:
                    translated = await owner._batch_translate_texts(
                        texts, config, ctx,
                        page_index=page_index,
                        batch_index=batch_index,
                        batch_original_texts=batch_original_texts
                    )
                except Exception:
                    if owner._uses_gemini(config):
                        raise
                    translated = []
                for i, value in zip(translatable_indices, translated):
                    translated_texts[i] = value
            translated_texts = await owner._translate_page_with_retries(config, ctx, translated_texts)

            # 将翻译结果分配回各个region
            for i, region in enumerate(ctx.text_regions):
                if i < len(translated_texts):
                    region.translation = translated_texts[i]
                    region.target_lang = config.translator.target_lang
                    region._alignment = config.render.alignment
                    region._direction = config.render.direction

            # 应用后处理逻辑（括号修正、过滤等）
            if ctx.text_regions:
                ctx.text_regions = await owner._apply_post_translation_processing(ctx, config)

            # 单页目标语言检查（如果启用）
            if config.translator.enable_post_translation_check and ctx.text_regions:
                page_lang_check_result = await owner._check_target_language_ratio(
                    ctx.text_regions,
                    config.translator.target_lang,
                    min_ratio=0.3  # 对单页使用更宽松的阈值
                )

                if not page_lang_check_result:
                    logger.warning(f"Page-level target language check failed for single image")

                    if owner._uses_gemini(config):
                        raise GeminiRetryExhausted("Gemini translation failed the target-language check.")

                    # 单页重试逻辑
                    max_retry = config.translator.post_check_max_retry_attempts
                    retry_count = 0

                    while retry_count < max_retry and not page_lang_check_result:
                        retry_count += 1
                        logger.info(f"Retrying single image translation {retry_count}/{max_retry}")

                        # 重新翻译
                        translatable_regions = [
                            region for region in ctx.text_regions
                            if not is_preserved_region(region) and getattr(region, 'text', None)
                        ]
                        original_texts = [region.text for region in translatable_regions]
                        if original_texts:
                            try:
                                new_translations = await owner._batch_translate_texts(original_texts, config, ctx)

                                # 更新翻译结果
                                for region, translation in zip(translatable_regions, new_translations):
                                    old_translation = region.translation
                                    region.translation = translation
                                    logger.debug(f"Region translation updated: '{old_translation}' -> '{translation}'")

                                # 重新检查
                                page_lang_check_result = await owner._check_target_language_ratio(
                                    ctx.text_regions,
                                    config.translator.target_lang,
                                    min_ratio=0.3
                                )

                                if page_lang_check_result:
                                    logger.info(f"Single image target language check passed after retry {retry_count}")
                                    break

                            except Exception as e:
                                logger.error(f"Error during single image retry {retry_count}: {e}")
                                break
                        else:
                            break

                    if not page_lang_check_result:
                        logger.warning(f"Single image target language check failed after all {max_retry} retries")

            # 过滤逻辑
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

            return ctx, config

        except TranslationFailure as exc:
            ctx.translation_error = str(exc)
            ctx.result = None
            return ctx, config
        except Exception as e:
            logger.error(f"Error in concurrent translation for single image: {e}")
            ctx.translation_error = str(e)
            ctx.result = None
            return ctx, config

    # 创建并发任务，为每个任务添加页面索引和批次索引
    tasks = []
    for i, ctx_config_pair in enumerate(contexts_with_configs):
        # 计算当前页面在整个翻译序列中的索引
        page_index = len(owner.all_page_translations) + i
        batch_index = i  # 在当前批次中的索引
        ctx_config_pair_with_index = (*ctx_config_pair, page_index, batch_index)
        task = asyncio.create_task(translate_single_context(ctx_config_pair_with_index))
        tasks.append(task)

    logger.info(f'Starting concurrent translation of {len(tasks)} images...')

    # 等待所有任务完成
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"Error in concurrent translation gather: {e}")
        raise

    # 处理结果，检查是否有异常
    final_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Image {i+1} concurrent translation failed: {result}")
            ctx, config = contexts_with_configs[i]
            ctx.translation_error = str(result)
            ctx.result = None
            final_results.append((ctx, config))
        else:
            final_results.append(result)

    logger.info(f'Concurrent translation completed: {len(final_results)} images processed')
    return final_results


async def batch_translate_texts(
    owner,
    texts: List[str],
    config: Config,
    ctx: Context,
    batch_contexts: List[Context] = None,
    page_index: int = None,
    batch_index: int = None,
    batch_original_texts: List[dict] = None,
    text_ids: List[str] = None,
    *,
    logger,
) -> List[str]:
    '\n        批量翻译文本列表，使用现有的翻译器接口\n\n        Args:\n            texts: 要翻译的文本列表\n            config: 配置对象\n            ctx: 上下文对象\n            batch_contexts: 批处理上下文列表\n            page_index: 当前页面索引，用于并发模式下的上下文计算\n            batch_index: 当前页面在批次中的索引\n            batch_original_texts: 当前批次的原文数据\n        '
    if config.translator.translator == Translator.none:
        return ["" for _ in texts]

    if text_ids and any(key in GPT_TRANSLATORS for key, _ in config.translator.translator_gen.chain):
        translated = await dispatch_structured_translation(
            config.translator.translator_gen,
            list(zip(text_ids, texts)),
            config.translator,
            ctx,
            'cpu' if owner._gpu_limited_memory else owner.device,
        )
        return [translated[text_id] for text_id in text_ids]



    # 如果是ChatGPT翻译器，需要处理上下文
    if config.translator.translator == Translator.chatgpt:
        from .translators.chatgpt import OpenAITranslator
        translator = OpenAITranslator()

        # 确定是否使用并发模式和原文上下文
        use_original_text = owner.batch_concurrent and owner.batch_size > 1

        done_pages = owner.all_page_translations
        if owner.context_size > 0 and done_pages:
            pages_expected = min(owner.context_size, len(done_pages))
            non_empty_pages = [
                page for page in done_pages
                if any(sent.strip() for sent in page.values())
            ]
            pages_used = min(owner.context_size, len(non_empty_pages))
            skipped = pages_expected - pages_used
        else:
            pages_used = skipped = 0

        if owner.context_size > 0:
            context_type = "original text" if use_original_text else "translation results"
            logger.info(f"Context-aware translation enabled with {owner.context_size} pages of history using {context_type}")

        translator.parse_args(config.translator)

        # 构建上下文 - 在并发模式下使用原文和页面索引
        prev_ctx = owner._build_prev_context(
            use_original_text=use_original_text,
            current_page_index=page_index,
            batch_index=batch_index,
            batch_original_texts=batch_original_texts
        )
        translator.set_prev_context(prev_ctx)

        if pages_used > 0:
            context_count = prev_ctx.count("<|")
            logger.info(f"Carrying {pages_used} pages of context, {context_count} sentences as translation reference")
        if skipped > 0:
            logger.warning(f"Skipped {skipped} pages with no sentences")

        return await translator._translate(
            ctx.from_lang,
            config.translator.target_lang,
            texts
        )

    else:
        # 使用通用翻译调度器
        return await dispatch_translation(
            config.translator.translator_gen,
            texts,
            config.translator,
            owner.use_mtpe,
            ctx,
            'cpu' if owner._gpu_limited_memory else owner.device
        )
