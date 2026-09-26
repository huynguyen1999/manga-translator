from fastapi import APIRouter, Request

from server.request_extraction import BatchTranslateRequest

router = APIRouter()


@router.post("/simple_execute/translate_batch", tags=["internal-api"])
async def simple_execute_batch(req: Request, data: BatchTranslateRequest):
    """Internal batch translation execution endpoint"""
    from manga_translator import MangaTranslator

    translator = MangaTranslator({"batch_size": data.batch_size})
    images_with_configs = [(img, data.config) for img in data.images]
    return await translator.translate_batch(images_with_configs, data.batch_size)


@router.post("/execute/translate_batch", tags=["internal-api"])
async def execute_batch_stream(req: Request, data: BatchTranslateRequest):
    """Internal batch translation streaming execution endpoint"""
    from manga_translator import MangaTranslator

    translator = MangaTranslator({"batch_size": data.batch_size})
    images_with_configs = [(img, data.config) for img in data.images]
    return await translator.translate_batch(images_with_configs, data.batch_size)
