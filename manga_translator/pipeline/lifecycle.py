"""Model and device lifecycle operations used by MangaTranslator."""


def empty_device_cache(owner, get_model_executor, empty_device_cache):
    # In-process MPS cache cleanup runs exclusively after active model calls.
    # Calling empty_cache from a pipeline can race another lane's Metal work.
    device = getattr(owner, "device", "cpu")
    if device == "mps" and get_model_executor() is not None:
        return
    empty_device_cache(device)


async def unload_model(owner, tool, model, *, logger, get_model_executor, unloaders):
    async def unload():
        logger.info(f"Unloading {tool} model: {model}")
        match tool:
            case "colorization":
                await unloaders[tool](model)
            case "detection":
                await unloaders[tool](model)
            case "inpainting":
                await unloaders[tool](model)
            case "ocr":
                await unloaders[tool](model)
            case "upscaling":
                await unloaders[tool](model)
            case "translation":
                await unloaders[tool](model)
            case "bubble_detection":
                await unloaders[tool]()
        owner._empty_device_cache()

    executor = get_model_executor()
    if executor is None:
        await unload()
    else:
        await executor.run_exclusive(unload)


async def model_cleanup_job(owner, *, sleep, time, get_model_executor):
    while True:
        await sleep(20)
        executor = get_model_executor()
        if executor is not None:
            await executor.cleanup_models(owner.models_ttl)
        elif owner.models_ttl > 0:
            now = time()
            for (tool, model), last_used in list(owner._model_usage_timestamps.items()):
                if now - last_used > owner.models_ttl:
                    await owner._unload_model(tool, model)
                    del owner._model_usage_timestamps[(tool, model)]
