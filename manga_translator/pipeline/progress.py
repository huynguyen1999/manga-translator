"""Progress hook dispatch used by the MangaTranslator compatibility facade."""


def add_progress_hook(owner, hook):
    owner._progress_hooks.append(hook)


async def emit_progress(owner, state: str, finished: bool = False):
    for hook in owner._progress_hooks:
        await hook(state, finished)


async def report_progress(owner, state: str, finished: bool = False):
    if owner._pipeline_run is not None:
        run = owner._pipeline_run
        run.progress(state, finished)
        await run.checkpoint()
        if finished and state == "finished":
            run.cleanup_completed_artifacts()
            run.refresh()
            await run.checkpoint()
    await emit_progress(owner, state, finished)


def add_logger_hook(owner, logger):
    # TODO: Pass ctx to logger hook
    log_messages = {
        "upscaling": "Running upscaling",
        "detection": "Running text detection",
        "ocr": "Running ocr",
        "mask-generation": "Running mask refinement",
        "translating": "Running text translation",
        "rendering": "Running rendering",
        "colorizing": "Running colorization",
        "downscaling": "Running downscaling",
    }
    log_messages_skip = {
        "skip-no-regions": "No text regions! - Skipping",
        "skip-no-text": "No text regions with text! - Skipping",
        "error-translating": "Text translator returned empty queries",
        "cancelled": "Image translation cancelled",
    }
    log_messages_error = {
        # 'error-lang':           'Target language not supported by chosen translator',
    }

    async def hook(state, finished):
        if state in log_messages:
            logger.info(log_messages[state])
        elif state.startswith("offline_model:"):
            logger.info(f"Using offline model: {state.removeprefix('offline_model:')}")
        elif state.startswith("gemini_model:"):
            logger.info(f"Using Gemini model: {state.removeprefix('gemini_model:')}")
        elif state in log_messages_skip:
            logger.warn(log_messages_skip[state])
        elif state in log_messages_error:
            logger.error(log_messages_error[state])

    owner.add_progress_hook(hook)
