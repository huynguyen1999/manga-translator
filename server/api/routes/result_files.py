import asyncio
import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from manga_translator.pipeline.stages import STAGE_DEPENDENCIES, STAGE_ORDER, PipelineStage


def create_result_files_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    result_file: Callable[[Path], Optional[Path]],
    find_asset: Callable[[Path, str], Optional[Path]],
    generate_reader_asset: Callable[[Path, str], Optional[Path]],
    generate_image_variants: Callable[..., Any],
    ensure_bbox_artifact: Callable[..., bool],
    ensure_thumbnail_artifact: Callable[[Path, str], bool],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.get("/pipeline-runs/{folder_name}/manifest", tags=["api", "pipeline"])
    @router.get("/api/pipeline-runs/{folder_name}/manifest", tags=["api", "pipeline"])
    async def get_pipeline_manifest(folder_name: str):
        store = get_store()
        manifest = None
        if store is not None:
            try:
                manifest = await store.get_document(folder_name, "pipeline_manifest.json")
            except Exception:
                manifest = None
        if manifest is None:
            disk_path = get_result_root() / folder_name / "pipeline_manifest.json"
            if disk_path.is_file():
                try:
                    manifest = json.loads(disk_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        if manifest is None:
            meta = None
            if store is not None:
                try:
                    meta = await store.get_document(folder_name, "meta.json")
                except Exception:
                    meta = None
            if meta is None:
                disk_meta = get_result_root() / folder_name / "meta.json"
                if disk_meta.is_file():
                    try:
                        meta = json.loads(disk_meta.read_text(encoding="utf-8"))
                    except Exception:
                        pass
            if meta is not None:
                created_at = meta.get("startedAt") or meta.get("finishedAt")
                finished_at = meta.get("finishedAt")
                duration_ms = meta.get("durationMs")
                manifest = {
                    "version": 1,
                    "kind": "pipeline-run",
                    "folder": folder_name,
                    "status": "completed",
                    "createdAt": created_at,
                    "updatedAt": finished_at or created_at,
                    "source": {
                        "filename": meta.get("originalName") if meta.get("originalName") and meta.get("originalName") != "Unknown" else f"{folder_name}.png",
                    },
                    "config": meta.get("settings", {}),
                    "stages": [
                        {
                            "id": "rendering",
                            "label": "Rendering / final",
                            "status": "completed",
                            "startedAt": created_at,
                            "finishedAt": finished_at,
                            "durationMs": duration_ms,
                        }
                    ],
                }
        if manifest is None:
            raise HTTPException(404, detail="Pipeline run not found")
        manifest_stages = {}
        stage_aliases = {"upscaling": "upscale", "textline_merge": "text_grouping"}

        def canonical_stage_id(stage_id: str) -> str:
            return stage_aliases.get(stage_id, stage_id)

        for stage in manifest.get("stages", []):
            if not isinstance(stage, dict) or not stage.get("id"):
                continue
            stage_id = canonical_stage_id(str(stage["id"]))
            manifest_stages[stage_id] = {**stage, "id": stage_id}
        if store is not None:
            try:
                persisted_stages = await store.get_pipeline_stage_state(folder_name)
            except Exception:
                persisted_stages = []
            if persisted_stages:
                for stage in persisted_stages:
                    stage_id = canonical_stage_id(stage["stage"])
                    manifest_stages[stage_id] = {
                        **manifest_stages.get(stage_id, {}),
                        "id": stage_id,
                        "label": manifest_stages.get(stage_id, {}).get(
                            "label", stage_id.replace("_", " ").title()
                        ),
                        "status": stage["status"],
                        "startedAt": stage["started_at"],
                        "finishedAt": stage["completed_at"],
                        "durationMs": stage["duration_ms"],
                        "reason": stage["error_message"],
                    }
        stage_order = {stage.value: index for index, stage in enumerate(STAGE_ORDER)}
        manifest["stages"] = sorted(
            manifest_stages.values(),
            key=lambda stage: stage_order.get(stage.get("id"), len(stage_order)),
        )
        for stage in manifest["stages"]:
            try:
                dependencies = STAGE_DEPENDENCIES[PipelineStage(stage["id"])]
            except (KeyError, ValueError):
                dependencies = frozenset()
            stage["dependsOn"] = [dependency.value for dependency in STAGE_ORDER if dependency in dependencies]
        return manifest

    @router.api_route("/result/{folder_name}/{file_name}", methods=["GET", "HEAD"], tags=["api", "file"])
    @router.api_route("/api/result/{folder_name}/{file_name}", methods=["GET", "HEAD"], tags=["api", "file"])
    async def get_result_file_by_folder(folder_name: str, file_name: str, request: Request):
        """根据文件夹和文件名获取文件 (final.jpg, thumbnail.webp, inpainted.jpg, input.jpg, text_regions.json等)"""
        store = get_store()
        if store is not None:
            resolved_folder = await store.resolve_folder(folder_name)
            if resolved_folder is None and Path(folder_name).name == folder_name and (get_result_root() / folder_name).is_dir():
                resolved_folder = folder_name
            if resolved_folder is None:
                raise HTTPException(404, detail=f"Result {folder_name} not found")
            folder_name = resolved_folder
        result_dir = get_result_root().resolve()
        if not result_dir.exists():
            raise HTTPException(404, detail="Result directory not found")

        folder_path = (result_dir / folder_name).resolve()
        if folder_path.parent != result_dir or not folder_path.is_dir():
            raise HTTPException(404, detail=f"Folder {folder_name} not found")
        if file_name in {"bboxes.png", "bboxes_unfiltered.png"}:
            raise HTTPException(404, detail="Bounding-box images are rendered in the frontend")

        if store is not None and file_name.endswith(".json"):
            payload = await store.get_document(folder_name, file_name)
            if file_name == "text_regions.json":
                payload = await store.get_text_regions(folder_name) if payload is None else payload
            if payload is None and file_name == "detection.json":
                regions = await store.get_text_regions(folder_name)
                if regions is not None:
                    payload = []
                    for region in regions:
                        lines = region.get("lines") or []
                        confidence = region.get("confidence") if region.get("confidence") is not None else region.get("prob")
                        if lines:
                            payload.extend({"pts": line, **({"confidence": confidence} if confidence is not None else {})} for line in lines)
                        else:
                            x = region.get("x", 0)
                            y = region.get("y", 0)
                            w = region.get("width", 0)
                            h = region.get("height", 0)
                            item: dict[str, Any] = {"pts": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]}
                            if confidence is not None:
                                item["confidence"] = confidence
                            payload.append(item)
            if payload is None:
                raise HTTPException(404, detail=f"{file_name} not found")
            return JSONResponse(
                content=payload,
                headers={"Cache-Control": "no-cache"},
            )

        target_path = (folder_path / file_name).resolve()
        if target_path.parent != folder_path:
            raise HTTPException(404, detail=f"Invalid file path")
        if file_name in {"final.png", "final.jpg", "final.jpeg"} and not target_path.is_file():
            target_path = result_file(folder_path) or target_path
        elif file_name == "input.png" and not target_path.is_file():
            target_path = find_asset(folder_path, "input") or target_path
        elif file_name in {"inpainted.png", "inpainted.jpg", "inpainted.jpeg"} and not target_path.is_file():
            target_path = find_asset(folder_path, "inpainted") or target_path

        if store is not None and hasattr(store, "get_pipeline_artifact"):
            artifact_spec = {
                "final.png": ("rendering", "final_image"),
                "final.jpg": ("rendering", "final_image"),
                "final.jpeg": ("rendering", "final_image"),
                "inpainted.png": ("inpainting", "image"),
                "inpainted.jpg": ("inpainting", "image"),
                "inpainted.jpeg": ("inpainting", "image"),
                "mask_raw.png": ("detection", "mask"),
                "text_mask.png": ("mask_generation", "text_mask"),
                "bubble_mask.png": ("mask_generation", "bubble_mask"),
                "mask_final.png": ("mask_generation", "inpaint_mask"),
                "inpaint_mask.png": ("mask_generation", "inpaint_mask"),
            }
            spec = artifact_spec.get(file_name)
            if spec is not None:
                artifact = await store.get_pipeline_artifact(folder_name, *spec)
                if artifact is not None:
                    artifact_path = (result_dir / PurePosixPath(artifact["relative_path"])).resolve()
                    if not artifact_path.is_relative_to(result_dir) or not artifact_path.is_file():
                        raise HTTPException(404, detail=f"{file_name} not found in active pipeline artifacts")
                    target_path = artifact_path

        if file_name in {"input-reader.webp", "inpainted-reader.webp"}:
            generated_path = await asyncio.to_thread(generate_reader_asset, folder_path, file_name)
            if generated_path is None:
                raise HTTPException(404, detail=f"{file_name} not found in folder")
            target_path = generated_path

        if not target_path.is_file():
            if file_name in {"batch.webp", "cover.webp", "preview.webp", "reader.webp"}:
                await asyncio.to_thread(generate_image_variants, folder_path, only=file_name.removesuffix(".webp"))
            if file_name.startswith("thumbnail."):
                if not await asyncio.to_thread(ensure_thumbnail_artifact, folder_path, file_name):
                    raise HTTPException(404, detail=f"{file_name} not found in folder")
            else:
                regions_data = None
                if store is not None and file_name == "detection.json":
                    regions_data = await store.get_text_regions(folder_name)
                    if regions_data is None:
                        raise HTTPException(404, detail="Text regions not found")
                if not await asyncio.to_thread(ensure_bbox_artifact, folder_path, file_name, regions_data):
                    raise HTTPException(404, detail=f"{file_name} not found in folder")

        media_type = "image/png"
        lower_name = target_path.suffix.lower()
        if lower_name.endswith(".json"):
            media_type = "application/json"
        elif lower_name.endswith(".webp"):
            media_type = "image/webp"
        elif lower_name.endswith((".jpg", ".jpeg")):
            media_type = "image/jpeg"
        elif lower_name.endswith(".bmp"):
            media_type = "image/bmp"

        versioned = bool(request and request.query_params.get("v"))
        return FileResponse(
            str(target_path),
            media_type=media_type,
            headers={
                "Content-Disposition": f"inline; filename={file_name}",
                "Cache-Control": "public, max-age=31536000, immutable" if versioned else "public, max-age=0, must-revalidate",
            }
        )

    @router.api_route("/result/{folder_name}/final.png", methods=["GET", "HEAD"], tags=["api", "file"])
    @router.api_route("/api/result/{folder_name}/final.png", methods=["GET", "HEAD"], tags=["api", "file"])
    async def get_result_by_folder(folder_name: str, request: Request):
        """根据文件夹名称获取翻译结果图片 (兼容旧路由)"""
        return await get_result_file_by_folder(folder_name, "final.png", request)

    return router, (get_pipeline_manifest, get_result_file_by_folder, get_result_by_folder)
