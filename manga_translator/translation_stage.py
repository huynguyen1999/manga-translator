"""Text translation pipeline stage, kept behavior-identical to its facade method."""

import json
import os
import time

import regex as re

from .config import Config, Translator
from .translators.gemini_keys import GeminiRetryExhausted
from .utils import Context, is_preserved_region


async def run_text_translation(
    owner,
    config: Config,
    ctx: Context,
    load_dictionary,
    apply_dictionary,
    *,
    logger,
):
    # 检查text_regions是否为None或空
    if not ctx.text_regions:
        return []

    # 如果设置了prep_manual则将translator设置为none，防止token浪费
    # Set translator to none to provent token waste if prep_manual is True  
    if owner.prep_manual:  
        config.translator.translator = Translator.none

    current_time = time.time()
    owner._model_usage_timestamps[("translation", config.translator.translator)] = current_time

    # 为none翻译器添加特殊处理  
    # Add special handling for none translator  
    if config.translator.translator == Translator.none:  
        # 使用none翻译器时，为所有文本区域设置必要的属性  
        # When using none translator, set necessary properties for all text regions  
        for region in ctx.text_regions:  
            region.translation = region.text if is_preserved_region(region) else ""  # Empty translation leaves preserved annotations visible.
            region.target_lang = config.translator.target_lang  
            region._alignment = config.render.alignment  
            region._direction = config.render.direction    
        return ctx.text_regions  

    # 以下翻译处理仅在非none翻译器或有none翻译器但没有prep_manual时执行  
    # Translation processing below only happens for non-none translator or none translator without prep_manual  
    texts = [region.text for region in ctx.text_regions]
    if owner.load_text:  
        input_filename = os.path.splitext(os.path.basename(owner.input_files[0]))[0]  
        with open(owner._result_path(f"{input_filename}_translations.txt"), "r") as f:  
                translated_sentences = json.load(f)  
    else:  
        # 如果是none翻译器，不需要调用翻译服务，文本已经设置为空  
        # If using none translator, no need to call translation service, text is already set to empty  
        if config.translator.translator != Translator.none:  
            # 自动给 ChatGPT 加上下文，其他翻译器不改变
            # Automatically add context to ChatGPT, no change for other translators
            translated_sentences =\
                await owner._translate_page_with_retries(config, ctx)
        else:  
            # 对于none翻译器，创建一个空翻译列表  
            # For none translator, create an empty translation list  
            translated_sentences = ["" for _ in ctx.text_regions]

        # Save translation if args.save_text is set and quit  
        if owner.save_text:  
            input_filename = os.path.splitext(os.path.basename(owner.input_files[0]))[0]  
            with open(owner._result_path(f"{input_filename}_translations.txt"), "w") as f:  
                json.dump(translated_sentences, f, indent=4, ensure_ascii=False)  
            print("Don't continue if --save-text is used")  
            exit(-1)  

    if owner._pipeline_run is not None:
        owner._pipeline_run.record_translation(
            config,
            [
                {"index": index, "text": text}
                for index, text in enumerate(texts)
                if not is_preserved_region(ctx.text_regions[index])
            ],
            [{"index": index, "translation": translation} for index, translation in enumerate(translated_sentences)],
            ctx,
        )

    # 如果不是none翻译器或者是none翻译器但没有prep_manual  
    # If not none translator or none translator without prep_manual  
    if config.translator.translator != Translator.none or not owner.prep_manual:  
        for region, translation in zip(ctx.text_regions, translated_sentences):  
            region.translation = (
                region.text if is_preserved_region(region)
                else config.render.transform_text_case(translation)
            )
            region.target_lang = config.translator.target_lang  
            region._alignment = config.render.alignment  
            region._direction = config.render.direction  

    # Punctuation correction logic. for translators often incorrectly change quotation marks from the source language to those commonly used in the target language.
    check_items = [
        # 圆括号处理
        ["(", "（", "「", "【"],
        ["（", "(", "「", "【"],
        [")", "）", "」", "】"],
        ["）", ")", "」", "】"],

        # 方括号处理
        ["[", "［", "【", "「"],
        ["［", "[", "【", "「"],
        ["]", "］", "】", "」"],
        ["］", "]", "】", "」"],

        # 引号处理
        ["「", "“", "‘", "『", "【"],
        ["」", "”", "’", "』", "】"],
        ["『", "“", "‘", "「", "【"],
        ["』", "”", "’", "」", "】"],

        # 新增【】处理
        ["【", "(", "（", "「", "『", "["],
        ["】", ")", "）", "」", "』", "]"],
    ]

    replace_items = [
        ["「", "“"],
        ["「", "‘"],
        ["」", "”"],
        ["」", "’"],
        ["【", "["],  
        ["】", "]"],  
    ]

    for region in ctx.text_regions:
        if is_preserved_region(region):
            region.translation = region.text
            continue
        if region.text and region.translation:
            if '『' in region.text and '』' in region.text:
                quote_type = '『』'
            elif '「' in region.text and '」' in region.text:
                quote_type = '「」'
            elif '【' in region.text and '】' in region.text: 
                quote_type = '【】'
            else:
                quote_type = None

            if quote_type:
                src_quote_count = region.text.count(quote_type[0])
                dst_dquote_count = region.translation.count('"')
                dst_fwquote_count = region.translation.count('＂')

                if (src_quote_count > 0 and
                    (src_quote_count == dst_dquote_count or src_quote_count == dst_fwquote_count) and
                    not region.translation.isascii()):

                    if quote_type == '「」':
                        region.translation = re.sub(r'"([^"]*)"', r'「\1」', region.translation)
                    elif quote_type == '『』':
                        region.translation = re.sub(r'"([^"]*)"', r'『\1』', region.translation)
                    elif quote_type == '【】':  
                        region.translation = re.sub(r'"([^"]*)"', r'【\1】', region.translation)

            # === 优化后的数量判断逻辑 ===
            # === Optimized quantity judgment logic ===
            for v in check_items:
                num_src_std = region.text.count(v[0])
                num_src_var = sum(region.text.count(t) for t in v[1:])
                num_dst_std = region.translation.count(v[0])
                num_dst_var = sum(region.translation.count(t) for t in v[1:])

                if (num_src_std > 0 and
                    num_src_std != num_src_var and
                    num_src_std == num_dst_std + num_dst_var):
                    for t in v[1:]:
                        region.translation = region.translation.replace(t, v[0])

            # 强制替换规则
            # Forced replacement rules
            for v in replace_items:
                region.translation = region.translation.replace(v[1], v[0])

    # 注意：翻译结果的保存移动到了翻译流程的最后，确保保存的是最终结果而不是重试前的结果

    # Apply post dictionary after translating
    post_dict = load_dictionary(owner.post_dict)
    post_replacements = []  
    for region in ctx.text_regions:  
        if is_preserved_region(region):
            region.translation = region.text
            continue
        original = region.translation  
        region.translation = apply_dictionary(region.translation, post_dict)
        if original != region.translation:  
            post_replacements.append(f"{original} => {region.translation}")  

    if post_replacements:  
        logger.info("Post-translation replacements:")  
        for replacement in post_replacements:  
            logger.info(replacement)  
    else:  
        logger.info("No post-translation replacements made.")

    # 译后检查和重试逻辑 - 第一阶段：单个region幻觉检测
    failed_regions = []
    if config.translator.enable_post_translation_check:
        logger.info("Starting post-translation check...")

        # 单个region级别的幻觉检测（在过滤前进行）
        for region in ctx.text_regions:
            if not is_preserved_region(region) and region.translation and region.translation.strip():
                # 只检查重复内容幻觉，不进行页面级目标语言检查
                if await owner._check_repetition_hallucination(
                    region.translation, 
                    config.translator.post_check_repetition_threshold,
                    silent=False
                ):
                    failed_regions.append(region)

        # 对失败的区域进行重试
        if failed_regions:
            logger.warning(f"Found {len(failed_regions)} regions that failed repetition check, starting retry...")
            if owner._uses_gemini(config):
                raise GeminiRetryExhausted(
                    f"Gemini translation failed repetition check for {len(failed_regions)} region(s)."
                )
            for region in failed_regions:
                await owner._retry_translation_with_validation(region, config, ctx)
            logger.info("Repetition check retry finished.")

    # 译后检查和重试逻辑 - 第二阶段：页面级目标语言检查（使用过滤后的区域）
    if config.translator.enable_post_translation_check:

        # 页面级目标语言检查（使用过滤后的区域数量）
        page_lang_check_result = True
        if ctx.text_regions and len(ctx.text_regions) > 5:
            logger.info(f"Starting page-level target language check with {len(ctx.text_regions)} regions...")
            page_lang_check_result = await owner._check_target_language_ratio(
                ctx.text_regions,
                config.translator.target_lang,
                min_ratio=0.5
            )

            if not page_lang_check_result:
                logger.warning("Page-level target language ratio check failed")

                if owner._uses_gemini(config):
                    raise GeminiRetryExhausted("Gemini translation failed the target-language check.")

                # 第二阶段：整个批次重新翻译逻辑
                max_batch_retry = config.translator.post_check_max_retry_attempts
                batch_retry_count = 0

                while batch_retry_count < max_batch_retry and not page_lang_check_result:
                    batch_retry_count += 1
                    logger.warning(f"Starting batch retry {batch_retry_count}/{max_batch_retry} for page-level target language check...")

                    # 重新翻译所有区域
                    translatable_regions = [
                        region for region in ctx.text_regions
                        if not is_preserved_region(region)
                    ]
                    original_texts = [getattr(region, 'text', '') or '' for region in translatable_regions]

                    if original_texts:
                        try:
                            # 重新批量翻译
                            logger.info(f"Retrying translation for {len(original_texts)} regions...")
                            new_translations = await owner._batch_translate_texts(original_texts, config, ctx)

                            # 更新翻译结果到regions
                            for region, translation in zip(translatable_regions, new_translations):
                                if translation:
                                    old_translation = region.translation
                                    region.translation = translation
                                    logger.debug(f"Region translation updated: '{old_translation}' -> '{translation}'")

                            # 重新检查目标语言比例
                            logger.info(f"Re-checking page-level target language ratio after batch retry {batch_retry_count}...")
                            page_lang_check_result = await owner._check_target_language_ratio(
                                ctx.text_regions,
                                config.translator.target_lang,
                                min_ratio=0.5
                            )

                            if page_lang_check_result:
                                logger.info(f"Page-level target language check passed")
                                break
                            else:
                                logger.warning(f"Page-level target language check still failed")

                        except Exception as e:
                            logger.error(f"Error during batch retry {batch_retry_count}: {e}")
                            break
                    else:
                        logger.warning("No text found for batch retry")
                        break

                if not page_lang_check_result:
                    logger.error(f"Page-level target language check failed after all {max_batch_retry} batch retries")
            else:
                logger.info("Page-level target language ratio check passed")
        else:
            logger.info(f"Skipping page-level target language check: only {len(ctx.text_regions)} regions (threshold: 5)")

        # 统一的成功信息
        if page_lang_check_result:
            logger.info("All translation regions passed post-translation check.")
        else:
            logger.warning("Some translation regions failed post-translation check.")

    # 过滤逻辑（简化版本，保留主要过滤条件）
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

    return new_text_regions
