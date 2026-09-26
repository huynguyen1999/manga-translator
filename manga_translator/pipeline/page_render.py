"""Rendering lifecycle for prepared and translated pages."""

from PIL import Image

from manga_translator.config import Config
from manga_translator.pipeline.run import PipelineRun
from manga_translator.utils import Context


async def render_prepared_page(owner, ctx: Context, config: Config, *, logger) -> Context:
    """
    Render text and save final output artifacts for a single prepared & translated image context.
    """
    if getattr(ctx, 'image_context', None):
        owner._current_image_context = ctx.image_context.copy()
    folder = getattr(ctx, 'debug_folder', None) or (owner._current_image_context.get('subfolder') if owner._current_image_context else None)
    if owner._pipeline_run is None and folder:
        documents = getattr(ctx, 'result_documents', {}) or {}
        if 'pipeline_manifest.json' in documents:
            ctx.result_documents = {
                name: document for name, document in documents.items()
                if name != 'pipeline_manifest.json'
            }
        owner._pipeline_run = PipelineRun.get_or_load(owner.result_root, folder)
        if owner._pipeline_run is None:
            owner._pipeline_run = PipelineRun.from_documents(
                owner.result_root, folder, documents
            )
            if owner._pipeline_run is None:
                img_in = getattr(ctx, 'input', None) or Image.new('RGB', (1, 1))
                owner._pipeline_run = PipelineRun(owner.result_root, folder, img_in, config)
    if owner._pipeline_run is not None:
        owner._pipeline_run.ctx = ctx
        owner._pipeline_run.translator = owner
        if getattr(ctx, 'translation_duration_ms', None) is not None:
            try:
                trans_stage = owner._pipeline_run._stage('translation')
                if trans_stage:
                    trans_stage['status'] = 'completed'
                    trans_stage['startedAt'] = getattr(ctx, 'translation_started_at', None) or trans_stage.get('startedAt')
                    trans_stage['finishedAt'] = getattr(ctx, 'translation_finished_at', None) or trans_stage.get('finishedAt')
                    trans_stage['durationMs'] = ctx.translation_duration_ms
            except Exception:
                pass
    try:
        if ctx.text_regions:
            ctx = await owner._complete_translation_pipeline(ctx, config)
        else:
            if getattr(ctx, 'result', None) is None:
                ctx.result = getattr(ctx, 'upscaled', None) or getattr(ctx, 'input', None)
                if ctx.result is not None:
                    owner._current_image_context = getattr(ctx, 'image_context', None) or owner._current_image_context
                    ctx = await owner._revert_upscale(config, ctx)
        return ctx
    except Exception as e:
        logger.error(f'Render error: {e}')
        ctx.translation_error = str(e)
        ctx.result = None
        run = owner._pipeline_run
        if run is not None:
            try:
                run.fail(str(e))
                await run.checkpoint()
            except Exception as checkpoint_error:
                logger.error(f'Could not checkpoint failed render: {checkpoint_error}')
            finally:
                run.release_runtime()
                if owner._pipeline_run is run:
                    owner._pipeline_run = None
        return ctx

