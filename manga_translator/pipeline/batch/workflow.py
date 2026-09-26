"""Public batch translation workflow orchestration."""

from typing import List

from manga_translator.utils import Context, is_preserved_region


async def translate_and_render_batch(
    owner, contexts_with_configs: List[tuple], batch_size: int = None, *, logger
) -> List[Context]:
    """
    Translate an aggregate batch of prepared contexts and render the final images.
    """
    translated_contexts = await owner.translate_batch_contexts(contexts_with_configs, batch_size)
    results = []
    logger.debug('Starting post-processing phase...')
    for i, (ctx, config) in enumerate(translated_contexts):
        res_ctx = await owner.render(ctx, config)
        results.append(res_ctx)
        logger.debug(f'Image {i+1} post-processing completed')

    logger.info(f'Batch translation completed: processed {len(results)} images')

    for ctx in results:
        if ctx.text_regions and not ctx.get('translation_error'):
            page_translations = {
                r.text_raw if hasattr(r, "text_raw") else r.text: r.translation
                for r in ctx.text_regions if not is_preserved_region(r)
            }
            owner.all_page_translations.append(page_translations)
            page_original_texts = {
                i: (r.text_raw if hasattr(r, "text_raw") else r.text)
                for i, r in enumerate(ctx.text_regions) if not is_preserved_region(r)
            }
            owner._original_page_texts.append(page_original_texts)

    owner._saved_image_contexts.clear()
    return results


async def translate_batch(owner, images_with_configs: List[tuple], batch_size: int = None, image_names: List[str] = None, *, logger) -> List[Context]:
    """
    批量翻译多张图片，在翻译阶段进行批量处理以提高效率
    Args:
        images_with_configs: List of (image, config) tuples
        batch_size: 批量大小，如果为None则使用实例的batch_size
        image_names: 已弃用的参数，保留用于兼容性
    Returns:
        List of Context objects with translation results
    """
    batch_size = batch_size or owner.batch_size
    if batch_size < 1:
        raise ValueError('batch_size must be at least 1')
    
    logger.debug(f'Starting batch translation: {len(images_with_configs)} images, batch size: {batch_size}')
    results = []
    for offset in range(0, len(images_with_configs), batch_size):
        pre_translation_contexts = []
        chunk = images_with_configs[offset:offset + batch_size]
        for index, (image, config) in enumerate(chunk, start=offset):
            logger.debug(f'Pre-processing image {index+1}/{len(images_with_configs)}')
            try:
                ctx = await owner.prepare(image, config)
                logger.debug(f'Image {index+1} pre-processing successful')
            except Exception as e:
                logger.error(f'Image {index+1} pre-processing error: {e}')
                ctx = Context(input=image, text_regions=[])
            pre_translation_contexts.append((ctx, config))

        chunk_results = await owner.translate_and_render_batch(
            pre_translation_contexts, batch_size=batch_size
        )
        # ponytail: returned outputs remain page-sized; callers release them after save or serialization.
        for ctx in chunk_results:
            ctx.cleanup_runtime(preserve_output=True)
        results.extend(chunk_results)

    if not results:
        logger.warning('No images pre-processed successfully')
    return results

