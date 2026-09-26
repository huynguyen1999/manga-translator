import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

_JSON_DOCUMENTS = (
    "detection.json",
    "ocr.json",
    "bubble_detections.json",
    "text_regions_merged.json",
    "translations.json",
    "translation_remap.json",
    "layout.json",
    "text_regions.json",
    "meta.json",
)
PIPELINE_CASE_DOCUMENTS = set(_JSON_DOCUMENTS[:-1])


def register_pipeline_case_data_routes(
    router: APIRouter,
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    get_legacy_result_root: Callable[[], Path],
    scan_results: Callable[..., dict[str, Any]],
) -> None:
    @router.get("/pipeline-cases/{page_ref}/data", tags=["api", "pipeline"])
    @router.get("/api/pipeline-cases/{page_ref}/data", tags=["api", "pipeline"])
    async def get_pipeline_case_data(page_ref: str):
        """Return saved page metadata and stage documents without running layout or rendering."""
        store = get_store()
        source = await store.page_detail(page_ref) if store is not None else None
        if source is None and store is None:
            for root in (get_result_root(), get_legacy_result_root()):
                scanned = await asyncio.to_thread(
                    scan_results, root, "order", None, None, None, 0, None
                )
                source = next(
                    (item for item in scanned["items"] if page_ref in {item.get("id"), item.get("folder")}),
                    None,
                )
                if source is not None:
                    break
        if source is None:
            raise HTTPException(404, detail=f"Page {page_ref} not found")

        folder = source.get("folder")
        if not isinstance(folder, str) or Path(folder).name != folder:
            raise HTTPException(400, detail="Page has an invalid result folder")
        result_root = get_result_root().resolve()
        source_dir = (result_root / folder).resolve()
        if source_dir.parent != result_root or not source_dir.is_dir():
            legacy_root = get_legacy_result_root().resolve()
            source_dir = (legacy_root / folder).resolve()
            if source_dir.parent != legacy_root or not source_dir.is_dir():
                raise HTTPException(404, detail="Source page files are unavailable")

        saved = await store.get_documents(folder) if store is not None else {}
        artifacts, missing = {}, []
        for filename in _JSON_DOCUMENTS:
            payload = saved.get(filename)
            path = source_dir / filename
            if payload is None and path.is_file():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    payload = None
            if payload is None:
                missing.append(filename)
            else:
                artifacts[filename.removesuffix(".json")] = payload

        pipeline = saved.get("pipeline_manifest.json")
        manifest_path = source_dir / "pipeline_manifest.json"
        if pipeline is None and manifest_path.is_file():
            try:
                pipeline = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pipeline = {}
        meta = artifacts.get("meta") or {}
        return {
            "page": {
                "id": source.get("id") or page_ref,
                "folder": folder,
                "url": source.get("resultUrl") or f"/result/{folder}/final.jpg",
            },
            "settings": source.get("settings") or meta.get("settings", {}),
            "pipeline": pipeline or {},
            "artifacts": artifacts,
            "missingArtifacts": missing,
        }
