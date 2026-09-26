import asyncio
import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from server.image_variants import final_file
from server.api.routes.pipeline_case_data import PIPELINE_CASE_DOCUMENTS, register_pipeline_case_data_routes

from server.api.schemas.pipeline import PipelineCaseRerunRequest, PipelineRerunRequest, RerenderRequest


def create_pipeline_rerun_router(
    get_store: Callable[[], Any],
    get_result_root: Callable[[], Path],
    get_legacy_result_root: Callable[[], Path],
    scan_results: Callable[..., dict[str, Any]],
    get_batch_store: Callable[[], Any],
    get_batch_scheduler: Callable[[], Any],
    batch_http_error: Callable[[Exception], Exception],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()
    register_pipeline_case_data_routes(router, get_store, get_result_root, get_legacy_result_root, scan_results)

    @router.post("/results/rerun", tags=["api", "batches"])
    @router.post("/api/results/rerun", tags=["api", "batches"])
    async def rerun_pipeline(data: PipelineRerunRequest):
        from server.pipeline_rerun import PipelineRerunMode, resolve_rerun_plan, validate_rerun_prerequisites

        if not data.pageIds and not data.groupId:
            raise HTTPException(400, detail="pageIds or groupId is required")
        if data.pageIds and data.groupId:
            raise HTTPException(400, detail="Choose pageIds or groupId, not both")
        if len(set(data.pageIds)) != len(data.pageIds):
            raise HTTPException(400, detail="pageIds must be unique")

        try:
            plan = resolve_rerun_plan(data.mode)
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc))

        store = get_store()
        records: list[dict[str, Any]]
        if store is not None:
            if data.groupId:
                pages = await store.group_pages(data.groupId)
            else:
                pages = []
                for page_id in data.pageIds:
                    page = await store.page_detail(page_id)
                    if page is None:
                        raise HTTPException(404, detail=f"Page {page_id} not found")
                    pages.append({
                        "id": page["id"],
                        "folder": page["folder"],
                        "name": page["originalName"],
                        "sourceType": page.get("sourceType"),
                        "hasRegions": page.get("hasTextRegions", False),
                        "pageOrder": page.get("pageOrder"),
                        "meta": {"settings": page.get("settings", {}), "mangaTitle": page.get("mangaTitle")},
                        "groupId": page.get("groupId"),
                    })
            records = [
                {
                    "id": page["id"],
                    "folder": page["folder"],
                    "name": page.get("name") or f"{page['folder']}.png",
                    "sourceType": page.get("sourceType"),
                    "hasTextRegions": bool(page.get("hasRegions")),
                    "pageOrder": page.get("pageOrder"),
                    "settings": (page.get("meta") or {}).get("settings", {}),
                    "mangaTitle": page.get("mangaTitle") or (page.get("meta") or {}).get("mangaTitle", "Ungrouped"),
                    "groupId": page.get("groupId") or (page.get("meta") or {}).get("mangaGroupId"),
                }
                for page in pages
            ]
        else:
            scanned = await asyncio.to_thread(
                scan_results,
                get_result_root(),
                "order",
                data.groupId,
                None,
                None,
                0,
                None,
            )
            by_id = {item["id"]: item for item in scanned["items"]}
            by_folder = {item["folder"]: item for item in scanned["items"]}
            if data.groupId:
                records = [
                    {
                        "id": item["id"],
                        "folder": item["folder"],
                        "name": item["originalName"],
                        "sourceType": item.get("sourceType"),
                        "hasTextRegions": bool(item.get("hasTextRegions")),
                        "pageOrder": item.get("pageOrder"),
                        "settings": item.get("settings", {}),
                        "mangaTitle": item.get("mangaTitle", "Ungrouped"),
                        "groupId": item.get("groupId"),
                    }
                    for item in scanned["items"]
                ]
            else:
                records = []
                for page_id in data.pageIds:
                    item = by_id.get(page_id) or by_folder.get(page_id)
                    if item is None:
                        raise HTTPException(404, detail=f"Page {page_id} not found")
                    records.append({
                        "id": item["id"],
                        "folder": item["folder"],
                        "name": item["originalName"],
                        "sourceType": item.get("sourceType"),
                        "hasTextRegions": bool(item.get("hasTextRegions")),
                        "pageOrder": item.get("pageOrder"),
                        "settings": item.get("settings", {}),
                        "mangaTitle": item.get("mangaTitle", "Ungrouped"),
                        "groupId": item.get("groupId"),
                    })

        eligible_records = []
        ineligible = []
        for record in records:
            folder = record.get("folder")
            if not folder:
                ineligible.append({"pageId": record["id"], "reason": "No folder"})
                continue
            result_dir = (get_result_root() / folder).resolve()
            if not result_dir.is_dir() and (get_legacy_result_root() / folder).is_dir():
                result_dir = (get_legacy_result_root() / folder).resolve()
            valid, reason = validate_rerun_prerequisites(result_dir, plan.mode, database=store, record=record)
            if valid:
                eligible_records.append(record)
            else:
                ineligible.append({"pageId": record["id"], "reason": reason})

        if not eligible_records:
            raise HTTPException(
                400,
                detail=f"No eligible pages found for {plan.mode.value} rerun. {ineligible[0]['reason'] if ineligible else ''}".strip(),
            )

        batch_id = f"rerun-{secrets.token_hex(8)}"
        title = str(eligible_records[0].get("mangaTitle") or "Ungrouped")
        initial_stage = "detection" if plan.mode in {PipelineRerunMode.FULL, PipelineRerunMode.REPROCESS_TEXT} else "translating" if plan.mode == PipelineRerunMode.TRANSLATION_TYPESETTING else "rendering"

        items = [
            {
                "id": f"page-{index}-{secrets.token_hex(4)}",
                "name": record["name"],
                "mangaTitle": record.get("mangaTitle") or title,
                "mangaGroupId": record.get("groupId"),
                "pageId": record["id"],
                "pageOrder": record.get("pageOrder"),
                "resultFolder": record["folder"],
                "rerunMode": plan.mode.value,
                "settings": {**(record.get("settings") or {}), **data.settingsOverrides},
                "status": "queued",
                "stage": initial_stage,
            }
            for index, record in enumerate(eligible_records)
        ]
        manifest = {
            "id": batch_id,
            "kind": "pipeline-rerun",
            "rerunMode": plan.mode.value,
            "title": title,
            "mangaTitle": title,
            "mangaGroupId": eligible_records[0].get("groupId"),
            "settings": data.settingsOverrides or {},
            "status": "waiting",
            "items": items,
            "totalItems": len(items),
        }
        try:
            result = await get_batch_store().put_batch(batch_id, manifest, {})
            get_batch_scheduler().wake()
            return result
        except Exception as error:
            raise batch_http_error(error) from error

    @router.post("/pipeline-cases/rerun", tags=["api", "pipeline"])
    @router.post("/api/pipeline-cases/rerun", tags=["api", "pipeline"])
    @router.post("/pipeline-cases/preview", tags=["api", "pipeline"])
    @router.post("/api/pipeline-cases/preview", tags=["api", "pipeline"])
    async def rerun_pipeline_case(data: PipelineCaseRerunRequest):
        from server.pipeline_rerun import resolve_rerun_plan, run_temporary_pipeline_case

        unsupported = set(data.artifacts) - PIPELINE_CASE_DOCUMENTS
        if unsupported:
            raise HTTPException(400, detail=f"Unsupported case artifacts: {', '.join(sorted(unsupported))}")

        try:
            plan = resolve_rerun_plan(data.mode)
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc

        store = get_store()
        if store is not None:
            source = await store.page_detail(data.pageId)
            if source is None:
                raise HTTPException(404, detail=f"Page {data.pageId} not found")
        else:
            scanned = await asyncio.to_thread(
                scan_results, get_result_root(), "order", None, None, None, 0, None
            )
            source = next(
                (item for item in scanned["items"] if data.pageId in {item.get("id"), item.get("folder")}),
                None,
            )
            if source is None:
                raise HTTPException(404, detail=f"Page {data.pageId} not found")

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

        settings = {**(source.get("settings") or {}), **data.settingsOverrides}
        documents = await store.get_documents(folder) if store is not None else {}
        scheduler = get_batch_scheduler()
        instance = await scheduler.executors.find_executor()
        try:
            result = await run_temporary_pipeline_case(
                source_dir=source_dir,
                documents=documents,
                replacements=data.artifacts,
                mode=plan.mode.value,
                settings=settings,
                page=source,
                instance=instance,
            )
        except Exception as error:
            raise batch_http_error(error) from error
        finally:
            await scheduler.executors.free_executor(instance)

        return {
            "mode": result["mode"],
            "sourcePageId": result["sourcePageId"],
            "artifacts": result["artifacts"],
            "imageBase64": base64.b64encode(result["image"]).decode("ascii"),
        }

    @router.get("/pipeline-cases/{case_id}", tags=["api", "pipeline"])
    @router.get("/api/pipeline-cases/{case_id}", tags=["api", "pipeline"])
    async def get_pipeline_case(case_id: str):
        if not case_id.startswith("ai-case-") or Path(case_id).name != case_id:
            raise HTTPException(404, detail="Pipeline case not found")
        case_dir = (get_result_root().resolve() / case_id).resolve()
        if case_dir.parent != get_result_root().resolve() or not (case_dir / ".ai-case").is_file():
            raise HTTPException(404, detail="Pipeline case not found")
        meta_path = case_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        documents = {}
        for path in case_dir.glob("*.json"):
            try:
                documents[path.stem] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
        return {
            "page": {"id": case_id, "sourcePageId": meta.get("sourcePageId"), "folder": case_id},
            "settings": meta.get("settings", {}),
            "pipeline": documents.get("pipeline_manifest", {}),
            "artifacts": documents,
            "resultUrl": f"/result/{case_id}/{(final_file(case_dir) or Path('final.jpg')).name}",
        }

    @router.post("/results/rerender", tags=["api", "batches"])
    @router.post("/api/results/rerender", tags=["api", "batches"])
    async def rerender_results(data: RerenderRequest):
        # Backward compatibility alias for typesetting rerun
        return await rerun_pipeline(
            PipelineRerunRequest(
                pageIds=data.pageIds,
                groupId=data.groupId,
                mode="typesetting",
                settingsOverrides=data.settingsOverrides,
            )
        )

    return router, (rerun_pipeline, rerender_results)
