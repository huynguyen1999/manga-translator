"""Select and run the translation mode for prepared page batches."""

from typing import List


async def translate_prepared_contexts(
    owner,
    contexts_with_configs: List[tuple],
    batch_size: int = None,
    *,
    logger,
    gpt_translators,
    serialize_regions_fn,
) -> List[tuple]:
    """Translate prepared contexts using the configured batch mode."""
    batch_size = batch_size or owner.batch_size
    if batch_size < 1:
        raise ValueError('batch_size must be at least 1')

    if not contexts_with_configs:
        return []

    memory_optimization_enabled = not owner.disable_memory_optimization

    for ctx, config in contexts_with_configs:
        if getattr(ctx, 'image_context', None):
            owner._saved_image_contexts[ctx.image_context['file_md5']] = ctx.image_context.copy()

    logger.debug('Starting batch translation phase...')
    try:
        professional = any(
            config.translator.translation_quality == 'professional'
            for _, config in contexts_with_configs
        )
        if professional:
            if not all(config.translator.translation_quality == 'professional' for _, config in contexts_with_configs):
                raise ValueError('Cannot mix fast and professional translation in one batch')
            from manga_translator.professional_translation import translate_professionally
            await owner._report_progress('analyzing-story')
            translated_contexts = await translate_professionally(
                contexts_with_configs,
                progress=owner._report_progress,
            )
            for ctx, config in translated_contexts:
                for region in ctx.text_regions or []:
                    region._alignment = config.render.alignment
                    region._direction = config.render.direction
                ctx.text_regions = await owner._apply_post_translation_processing(ctx, config)
                ctx.result_documents['translations.json'] = serialize_regions_fn(ctx.text_regions)
            await owner._report_progress('after-translating')
            return translated_contexts
        uses_gpt = any(
            key in gpt_translators
            for key, _ in contexts_with_configs[0][1].translator.translator_gen.chain
        )
        if owner.batch_concurrent and not uses_gpt:
            logger.info('Using concurrent mode for batch translation')
            translated_contexts = await owner._concurrent_translate_contexts(contexts_with_configs)
        else:
            logger.debug('Using standard batch mode for translation')
            translated_contexts = await owner._batch_translate_contexts(contexts_with_configs, batch_size)
    except MemoryError as e:
        logger.error(f'Memory error in batch translation: {e}')
        if not memory_optimization_enabled:
            logger.error('Consider enabling memory optimization')
            raise
        logger.warning('Batch translation failed, switching to individual page translation mode...')
        translated_contexts = []
        for ctx, config in contexts_with_configs:
            try:
                if ctx.text_regions:
                    translated_texts = await owner._translate_page_with_retries(config, ctx)
                    for region, translation in zip(ctx.text_regions, translated_texts):
                        region.translation = translation
                        region.target_lang = config.translator.target_lang
                        region._alignment = config.render.alignment
                        region._direction = config.render.direction
                translated_contexts.append((ctx, config))
                owner._empty_device_cache()
            except Exception as individual_error:
                logger.error(f'Individual page translation failed: {individual_error}')
                ctx.translation_error = str(individual_error)
                ctx.result = None
                translated_contexts.append((ctx, config))

    return translated_contexts
