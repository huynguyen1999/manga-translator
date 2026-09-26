import asyncio
import io
import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse

from server.request_extraction import TranslateRequest
from server.to_json import TranslationResponse, to_translation


def transform_to_image(ctx):
    # 检查是否使用占位符（在web模式下final.png保存后会设置此标记）
    if hasattr(ctx, 'use_placeholder') and ctx.use_placeholder:
        # ctx.result已经是1x1占位符图片，快速传输
        img_byte_arr = io.BytesIO()
        ctx.result.save(img_byte_arr, format="PNG")
        return img_byte_arr.getvalue()

    # 返回完整的翻译结果
    img_byte_arr = io.BytesIO()
    ctx.result.save(img_byte_arr, format="PNG")
    return img_byte_arr.getvalue()


def transform_to_json(ctx):
    return to_translation(ctx).model_dump_json().encode("utf-8")


def transform_to_bytes(ctx):
    return to_translation(ctx).to_bytes()


def create_translation_router(
    get_ctx: Callable[..., Any],
    while_streaming: Callable[..., Any],
    to_translation: Callable[[Any], Any],
    index_context_result: Callable[[Any], Any],
    parse_config: Callable[[str], Any],
    apply_manga_title_alias: Callable[[Any, dict[str, Any]], Any],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.post("/translate/json", response_model=TranslationResponse, tags=["api", "json"], response_description="json strucure inspired by the ichigo translator extension")
    async def translate_json(req: Request, data: TranslateRequest):
        ctx = await get_ctx(req, data.config, data.image)
        await index_context_result(ctx)
        return to_translation(ctx)

    @router.post("/translate/bytes", response_class=StreamingResponse, tags=["api", "json"], response_description="custom byte structure for decoding look at examples in 'examples/response.*'")
    async def translate_bytes(req: Request, data: TranslateRequest):
        ctx = await get_ctx(req, data.config, data.image)
        await index_context_result(ctx)
        return StreamingResponse(content=to_translation(ctx).to_bytes())

    @router.post("/translate/image", response_description="the result image", tags=["api", "json"], response_class=StreamingResponse)
    async def translate_image(req: Request, data: TranslateRequest) -> StreamingResponse:
        ctx = await get_ctx(req, data.config, data.image)
        await index_context_result(ctx)

        def _save():
            img_byte_arr = io.BytesIO()
            ctx.result.save(img_byte_arr, format="PNG")
            img_byte_arr.seek(0)
            return img_byte_arr

        img_byte_arr = await asyncio.to_thread(_save)
        return StreamingResponse(img_byte_arr, media_type="image/png")

    @router.post("/translate/json/stream", response_class=StreamingResponse, tags=["api", "json"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
    async def stream_json(req: Request, data: TranslateRequest) -> StreamingResponse:
        return await while_streaming(req, transform_to_json, data.config, data.image)

    @router.post("/translate/bytes/stream", response_class=StreamingResponse, tags=["api", "json"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
    async def stream_bytes(req: Request, data: TranslateRequest) -> StreamingResponse:
        return await while_streaming(req, transform_to_bytes, data.config, data.image)

    @router.post("/translate/image/stream", response_class=StreamingResponse, tags=["api", "json"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
    async def stream_image(req: Request, data: TranslateRequest) -> StreamingResponse:
        return await while_streaming(req, transform_to_image, data.config, data.image)

    @router.post("/translate/with-form/json", response_model=TranslationResponse, tags=["api", "form"], response_description="json strucure inspired by the ichigo translator extension")
    async def json_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
        img = await image.read()
        conf = parse_config(config)
        try:
            raw_conf = json.loads(config)
            if isinstance(raw_conf, dict):
                apply_manga_title_alias(conf, raw_conf)
        except Exception:
            pass
        if not conf.original_name and image.filename:
            conf.original_name = image.filename
        ctx = await get_ctx(req, conf, img)
        await index_context_result(ctx)
        return to_translation(ctx)

    @router.post("/translate/with-form/bytes", response_class=StreamingResponse, tags=["api", "form"], response_description="custom byte structure for decoding look at examples in 'examples/response.*'")
    async def bytes_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
        img = await image.read()
        conf = parse_config(config)
        try:
            raw_conf = json.loads(config)
            if isinstance(raw_conf, dict):
                apply_manga_title_alias(conf, raw_conf)
        except Exception:
            pass
        if not conf.original_name and image.filename:
            conf.original_name = image.filename
        ctx = await get_ctx(req, conf, img)
        await index_context_result(ctx)
        return StreamingResponse(content=to_translation(ctx).to_bytes())

    @router.post("/translate/with-form/image", response_description="the result image", tags=["api", "form"], response_class=StreamingResponse)
    async def image_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
        img = await image.read()
        conf = parse_config(config)
        try:
            raw_conf = json.loads(config)
            if isinstance(raw_conf, dict):
                apply_manga_title_alias(conf, raw_conf)
        except Exception:
            pass
        if not conf.original_name and image.filename:
            conf.original_name = image.filename
        ctx = await get_ctx(req, conf, img)
        await index_context_result(ctx)

        def _save():
            img_byte_arr = io.BytesIO()
            ctx.result.save(img_byte_arr, format="PNG")
            img_byte_arr.seek(0)
            return img_byte_arr

        img_byte_arr = await asyncio.to_thread(_save)
        return StreamingResponse(img_byte_arr, media_type="image/png")

    @router.post("/translate/with-form/json/stream", response_class=StreamingResponse, tags=["api", "form"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
    async def stream_json_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
        img = await image.read()
        conf = parse_config(config)
        try:
            raw_conf = json.loads(config)
            if isinstance(raw_conf, dict):
                apply_manga_title_alias(conf, raw_conf)
        except Exception:
            pass
        if not conf.original_name and image.filename:
            conf.original_name = image.filename
        # Mark this as a web frontend call for placeholder response optimization.
        conf._is_web_frontend = True
        return await while_streaming(req, transform_to_json, conf, img)

    @router.post("/translate/with-form/bytes/stream", response_class=StreamingResponse, tags=["api", "form"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
    async def stream_bytes_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
        img = await image.read()
        conf = parse_config(config)
        try:
            raw_conf = json.loads(config)
            if isinstance(raw_conf, dict):
                apply_manga_title_alias(conf, raw_conf)
        except Exception:
            pass
        if not conf.original_name and image.filename:
            conf.original_name = image.filename
        return await while_streaming(req, transform_to_bytes, conf, img)

    @router.post("/translate/with-form/image/stream", response_class=StreamingResponse, tags=["api", "form"], response_description="Standard streaming endpoint - returns complete image data. Suitable for API calls and scripts.")
    async def stream_image_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
        """通用流式端点：返回完整图片数据，适用于API调用和comicread脚本"""
        img = await image.read()
        conf = parse_config(config)
        apply_manga_title_alias(conf, json.loads(config))
        if not conf.original_name and image.filename:
            conf.original_name = image.filename
        conf._web_frontend_optimized = False
        return await while_streaming(req, transform_to_image, conf, img)

    @router.post("/translate/with-form/image/stream/web", response_class=StreamingResponse, tags=["api", "form"], response_description="Web frontend optimized streaming endpoint - uses placeholder optimization for faster response.")
    @router.post("/api/translate/with-form/image/stream/web", response_class=StreamingResponse, tags=["api", "form"], response_description="Web frontend optimized streaming endpoint - uses placeholder optimization for faster response.")
    async def stream_image_form_web(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
        """Web前端专用端点：使用占位符优化，提供极速体验"""
        img = await image.read()
        conf = parse_config(config)
        apply_manga_title_alias(conf, json.loads(config))
        if not conf.original_name and image.filename:
            conf.original_name = image.filename
        conf._web_frontend_optimized = True
        return await while_streaming(req, transform_to_image, conf, img)

    return router, (
        translate_json,
        translate_bytes,
        translate_image,
        stream_json,
        stream_bytes,
        stream_image,
        json_form,
        bytes_form,
        image_form,
        stream_json_form,
        stream_bytes_form,
        stream_image_form,
        stream_image_form_web,
    )
