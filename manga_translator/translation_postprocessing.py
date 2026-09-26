"""Post-translation normalization and validation stage."""

import regex as re

from .config import Config
from .translators.gemini_keys import GeminiRetryExhausted
from .utils import Context, is_preserved_region


async def postprocess_translation(
    owner,
    ctx: Context,
    config: Config,
    load_dictionary,
    apply_dictionary,
    *,
    logger,
):
    # 检查text_regions是否为None或空
    if not ctx.text_regions:
        return []

    translated = await owner._translate_page_with_retries(
        config, ctx, [region.translation for region in ctx.text_regions]
    )
    for region, translation in zip(ctx.text_regions, translated):
        region.translation = translation
        region.target_lang = config.translator.target_lang
        region._alignment = config.render.alignment
        region._direction = config.render.direction

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
            # 引号处理逻辑
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

            # 括号修正逻辑
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
            for v in replace_items:
                region.translation = region.translation.replace(v[1], v[0])

    # 注意：翻译结果的保存移动到了translate方法的最后，确保保存的是最终结果

    # 应用后字典
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

    # 单个region幻觉检测
    failed_regions = []
    if config.translator.enable_post_translation_check:
        logger.info("Starting post-translation check...")

        # 单个region级别的幻觉检测
        for region in ctx.text_regions:
            if not is_preserved_region(region) and region.translation and region.translation.strip():
                # 只检查重复内容幻觉
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
                try:
                    logger.info(f"Retrying translation for region with text: '{region.text}'")
                    new_translation = await owner._retry_translation_with_validation(region, config, ctx)
                    if new_translation:
                        old_translation = region.translation
                        region.translation = new_translation
                        logger.info(f"Region retry successful: '{old_translation}' -> '{new_translation}'")
                    else:
                        logger.warning(f"Region retry failed, keeping original: '{region.translation}'")
                except Exception as e:
                    logger.error(f"Error during region retry: {e}")

    return ctx.text_regions
