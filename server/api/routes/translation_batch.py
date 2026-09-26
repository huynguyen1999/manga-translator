import io
import json
import os
import tempfile
import zipfile

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from server.request_extraction import BatchTranslateRequest, get_batch_ctx
from server.to_json import TranslationResponse, to_translation

router = APIRouter()


@router.post(
    "/translate/batch/json",
    response_model=list[TranslationResponse],
    tags=["api", "json", "batch"],
)
async def batch_json(req: Request, data: BatchTranslateRequest):
    """Batch translate images and return JSON format results"""
    results = await get_batch_ctx(req, data.config, data.images, data.batch_size)
    return [to_translation(ctx) for ctx in results]


@router.post(
    "/translate/batch/images",
    response_description="Zip file containing translated images",
    tags=["api", "batch"],
)
async def batch_images(req: Request, data: BatchTranslateRequest):
    """Batch translate images and return zip archive containing translated images"""
    results = await get_batch_ctx(req, data.config, data.images, data.batch_size)

    # Create temporary ZIP file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
        with zipfile.ZipFile(tmp_file, "w") as zip_file:
            for i, ctx in enumerate(results):
                if ctx.result:
                    img_byte_arr = io.BytesIO()
                    ctx.result.save(img_byte_arr, format="PNG")
                    zip_file.writestr(f"translated_{i+1}.png", img_byte_arr.getvalue())
            failures = {
                str(i + 1): ctx.translation_error
                for i, ctx in enumerate(results)
                if ctx.get("translation_error")
            }
            if failures:
                zip_file.writestr(
                    "failed_pages.json",
                    json.dumps(failures, ensure_ascii=False, indent=2),
                )

        # Return ZIP file
        with open(tmp_file.name, "rb") as f:
            zip_data = f.read()

        # Clean up temporary file
        os.unlink(tmp_file.name)

        return StreamingResponse(
            io.BytesIO(zip_data),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=translated_images.zip"},
        )
