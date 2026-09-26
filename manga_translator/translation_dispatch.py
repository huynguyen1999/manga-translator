"""Translate page text with the existing history and provider behavior."""

from .config import Config, Translator
from .translators import dispatch as dispatch_translation


async def dispatch_with_context(owner, config: Config, texts: list[str], ctx, *, logger):
    # 计算实际要使用的上下文页数和跳过的空页数
    # Calculate the actual number of context pages to use and empty pages to skip
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
        logger.info(f"Context-aware translation enabled with {owner.context_size} pages of history")

    # 构建上下文字符串
    # Build the context string
    prev_ctx = owner._build_prev_context()

    # 如果是 ChatGPT 翻译器，则专门处理上下文注入
    # Special handling for ChatGPT translator: inject context
    if config.translator.translator == Translator.chatgpt:
        from .translators.chatgpt import OpenAITranslator
        translator = OpenAITranslator()

        translator.parse_args(config.translator)
        translator.set_prev_context(prev_ctx)

        if pages_used > 0:
            context_count = prev_ctx.count("<|")
            logger.info(f"Carrying {pages_used} pages of context, {context_count} sentences as translation reference")
        if skipped > 0:
            logger.warning(f"Skipped {skipped} pages with no sentences")

        translated = await translator._translate(ctx.from_lang, config.translator.target_lang, texts)
        model = getattr(translator, 'model', None) or getattr(translator, 'MODEL', None)
        if isinstance(model, str):
            ctx['translator_model'] = model
        return translated

    translated = await owner._mps_call(
        dispatch_translation,
        config.translator.translator_gen,
        texts,
        config.translator,
        owner.use_mtpe,
        ctx,
        'cpu' if owner._gpu_limited_memory else owner.device
    )
    if ctx.get('offline_model'):
        await owner._report_progress(f'offline_model:{ctx.offline_model}')
    if ctx.get('gemini_model'):
        await owner._report_progress(f'gemini_model:{ctx.gemini_model}')
    if ctx.get('translator_model'):
        await owner._report_progress(f'translator_model:{ctx.translator_model}')
    return translated
