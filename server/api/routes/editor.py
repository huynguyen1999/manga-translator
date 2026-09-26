import asyncio
import io
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from manga_translator.pipeline.cpu import CPU_PRIORITY_INTERACTIVE
from server.api.schemas.editor import LayoutPreviewRequest, ReviewStatusRequest, SaveEditsRequest


def create_editor_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    run_cpu_stage: Callable[..., Any],
    find_asset: Callable[[Path, str], Optional[Path]],
    result_file: Callable[[Path], Optional[Path]],
    save_jpeg: Callable[..., Any],
    get_cached_meta: Callable[[Path], dict],
    invalidate_meta_cache: Callable[[Optional[str]], None],
    review_status_for_regions: Callable[[list[dict]], str],
    sync_batch_review: Callable[[str, bool], Any],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.post("/result/{folder_name}/layout-preview", tags=["api", "editor"])
    @router.post("/api/result/{folder_name}/layout-preview", tags=["api", "editor"])
    async def layout_preview(folder_name: str, data: LayoutPreviewRequest):
        """Use the production FreeType metrics to reflow one linked bubble group."""
        store = get_store()
        if store is not None:
            resolved_folder = await store.resolve_folder(folder_name)
            if resolved_folder is None:
                raise HTTPException(404, detail=f"Folder {folder_name} not found")
            folder_name = resolved_folder
        result_dir = get_result_root().resolve()
        folder_path = (result_dir / folder_name).resolve()
        if folder_path.parent != result_dir or not folder_path.is_dir():
            raise HTTPException(404, detail=f"Folder {folder_name} not found")

        def _layout():
            from manga_translator.rendering import _RENDER_LOCK, text_render, get_default_eng_font
            from manga_translator.rendering.bubble_layout import decode_safe_shape, encode_rendered_box
            from manga_translator.rendering.layout import layout_page
            from manga_translator.utils import TextBlock, Context
            from manga_translator.config import Config, RenderConfig
            import cv2
            import numpy as np

            lines = [[[s.x, s.y], [s.x + s.width, s.y], [s.x + s.width, s.y + s.height], [s.x, s.y + s.height]] for s in data.segments]
            region = TextBlock(lines, texts=[""], translation=data.translation,
                               font_size=data.font_size, target_lang=data.target_lang,
                               fg_color=(0, 0, 0), bg_color=(255, 255, 255),
                               alignment=data.alignment)
            saved_regions_path = folder_path / "text_regions.json"
            saved = json.loads(saved_regions_path.read_text(encoding="utf-8")) if saved_regions_path.is_file() else []
            if not isinstance(saved, list):
                saved = []
            stored = next((item for item in saved if isinstance(item, dict) and item.get("id") == data.group_id), None)
            shape = stored.get("bubble_safe_shape") if stored else None
            original_path = find_asset(folder_path, "original_canvas") or result_file(folder_path)
            original = cv2.imread(str(original_path)) if original_path is not None else None
            if original is None:
                max_x = max((s.x + s.width for s in data.segments), default=500) + 50
                max_y = max((s.y + s.height for s in data.segments), default=500) + 50
                original = np.zeros((max_y, max_x, 3), dtype=np.uint8)

            if shape:
                region._bubble_interior = decode_safe_shape(shape, *original.shape[:2])
            shape_available = getattr(region, "_bubble_interior", None) is not None

            font_path = get_default_eng_font()
            cfg = Config(
                render=RenderConfig(
                    font_size=data.font_size if data.font_size and data.font_size > 0 else None,
                    font_size_minimum=data.minimum_font_size,
                    line_spacing=data.line_spacing,
                    alignment=data.alignment,
                    direction="auto",
                )
            )
            ctx = Context(img_rgb=original, text_regions=[region])
            with _RENDER_LOCK:
                text_render.set_font(font_path)
                layout_page(ctx, cfg, font_path)

            segments = getattr(region, "layout_segments", None) or []
            if not segments:
                return {"fits": False, "font_size": data.font_size, "layout_segments": [], "needs_review": True}

            font_size = getattr(region, "font_size", data.font_size)
            box = getattr(region, "_bubble_box", None)
            rendered_png = encode_rendered_box(box) if box is not None and np.any(box[:, :, 3]) else None

            return {
                "fits": True,
                "needs_review": not shape_available,
                "font_size": font_size,
                "layout_segments": [
                    {
                        "x": segment.get("x", 0),
                        "y": segment.get("y", 0),
                        "width": segment.get("width", 0),
                        "height": segment.get("height", 0),
                        "text": segment.get("text", data.translation),
                        "font_size": segment.get("font_size", font_size),
                        "rendered_png": rendered_png if idx == 0 else None,
                        "positioned_lines": [
                            {"text": line.get("text", ""), "x": line.get("x", 0), "y": line.get("y", 0)}
                            for line in segment.get("lines", [])
                        ],
                    }
                    for idx, segment in enumerate(segments)
                ],
            }

        return await run_cpu_stage(_layout, priority=CPU_PRIORITY_INTERACTIVE)

    @router.post("/result/{folder_name}/save_edits", tags=["api", "editor"])
    @router.post("/api/result/{folder_name}/save_edits", tags=["api", "editor"])
    @router.put("/api/pages/{folder_name}/edits", tags=["api", "editor"])
    async def save_edits(folder_name: str, data: SaveEditsRequest):
        """保存交互式编辑器中修改的文本区域和渲染图像"""
        store = get_store()
        if store is not None:
            resolved_folder = await store.resolve_folder(folder_name)
            if resolved_folder is None:
                raise HTTPException(404, detail=f"Folder {folder_name} not found")
            folder_name = resolved_folder
        result_dir = get_result_root().resolve()
        folder_path = (result_dir / folder_name).resolve()
        if folder_path.parent != result_dir or not folder_path.is_dir():
            raise HTTPException(404, detail=f"Folder {folder_name} not found")

        def _do_save():
            import base64
            import binascii
            from PIL import Image
            image_bytes = None
            if data.final_image_base64:
                try:
                    b64_content = data.final_image_base64
                    if "base64," in b64_content:
                        b64_content = b64_content.split("base64,", 1)[1]
                    image_bytes = base64.b64decode(b64_content, validate=True)
                    with Image.open(io.BytesIO(image_bytes)) as image:
                        image.verify()
                except (binascii.Error, OSError, ValueError) as e:
                    raise ValueError("Invalid final image") from e

            # 若前端传回重新合成渲染的图像 base64，保存更新 final.jpg
            if image_bytes is not None:
                final_path = result_file(folder_path) or (folder_path / "final.jpg")
                with Image.open(io.BytesIO(image_bytes)) as image:
                    image.load()
                    if final_path.suffix.lower() in {".jpg", ".jpeg"}:
                        save_jpeg(image, final_path)
                    else:
                        image.save(final_path, format="PNG", compress_level=6)
                for thumb_name in ("thumbnail.webp", "thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg"):
                    (folder_path / thumb_name).unlink(missing_ok=True)

        try:
            await asyncio.to_thread(_do_save)
            status = review_status_for_regions(data.text_regions)
            reviewed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if status == "approved" else None
            if store is not None:
                if not await store.update_text_regions(folder_name, data.text_regions):
                    raise HTTPException(404, detail=f"Result {folder_name} not found")
                await store.sync_result_folder(folder_name)
                if not await store.update_review_status(folder_name, status, reviewed_at):
                    raise HTTPException(404, detail=f"Result {folder_name} not found")
            else:
                regions_path = folder_path / "text_regions.json"
                temporary_path: Optional[Path] = None
                try:
                    with tempfile.NamedTemporaryFile(
                        "w", encoding="utf-8", dir=folder_path, prefix=".regions.", suffix=".tmp", delete=False
                    ) as temporary:
                        json.dump(data.text_regions, temporary, ensure_ascii=False, indent=2)
                        temporary_path = Path(temporary.name)
                    os.replace(temporary_path, regions_path)
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
                metadata = dict(get_cached_meta(folder_path))
                metadata.update({"reviewStatus": status, "reviewedAt": reviewed_at})
                meta_path = folder_path / "meta.json"
                temporary_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        "w", encoding="utf-8", dir=folder_path, prefix=".meta.", suffix=".tmp", delete=False
                    ) as temporary:
                        json.dump(metadata, temporary, ensure_ascii=False, indent=2)
                        temporary_path = Path(temporary.name)
                    os.replace(temporary_path, meta_path)
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
                invalidate_meta_cache(folder_name)
            await sync_batch_review(folder_name, status == "pending")
            return {
                "status": "success",
                "message": "Edits saved successfully",
                "reviewStatus": status,
                "reviewedAt": reviewed_at,
            }
        except HTTPException:
            raise
        except ValueError as ve:
            raise HTTPException(400, detail=str(ve))
        except Exception as e:
            raise HTTPException(500, detail=f"Failed to save edits: {str(e)}")

    @router.patch("/pages/{folder_name}/review", tags=["api", "editor"])
    @router.patch("/api/pages/{folder_name}/review", tags=["api", "editor"])
    async def update_page_review(folder_name: str, data: ReviewStatusRequest):
        store = get_store()
        if store is not None:
            resolved = await store.resolve_folder(folder_name)
            if resolved is None:
                raise HTTPException(404, detail=f"Result {folder_name} not found")
            folder_name = resolved
            if data.status == "approved":
                regions = await store.get_text_regions(folder_name) or []
                if review_status_for_regions(regions) == "pending":
                    raise HTTPException(409, detail="Resolve flagged text regions before approving")
            if not await store.update_review_status(
                folder_name,
                data.status,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if data.status == "approved" else None,
            ):
                raise HTTPException(404, detail=f"Result {folder_name} not found")
        else:
            result_root = get_result_root()
            folder_path = (result_root / folder_name).resolve()
            if folder_path.parent != result_root.resolve() or not folder_path.is_dir():
                raise HTTPException(404, detail=f"Result {folder_name} not found")
            if data.status == "approved":
                try:
                    regions = json.loads((folder_path / "text_regions.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    regions = []
                if review_status_for_regions(regions) == "pending":
                    raise HTTPException(409, detail="Resolve flagged text regions before approving")
            metadata = dict(get_cached_meta(folder_path))
            metadata.update({
                "reviewStatus": data.status,
                "reviewedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if data.status == "approved" else None,
            })
            meta_path = folder_path / "meta.json"
            meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            invalidate_meta_cache(folder_name)
        await sync_batch_review(folder_name, data.status == "pending")
        return {"status": data.status, "folder": folder_name}

    return router, (layout_preview, save_edits, update_page_review)
