"""File-backed result ingestion into PostgreSQL."""

from __future__ import annotations

import asyncio
import datetime as dt
import mimetypes
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from manga_translator.pipeline.stages import PipelineStage, StageStatus, fingerprint, settings_for_stage
from server.postgres_common import _json_dump, _json_load, _page_order, _safe_folder


def _pipeline_manifest_stage_rows(manifest: Any) -> list[dict[str, Any]]:
    """Convert finished legacy checkpoints into canonical durable stage records."""
    if not isinstance(manifest, dict):
        return []
    config = manifest.get("config")
    config = config if isinstance(config, dict) else {}
    status_map = {
        "completed": StageStatus.COMPLETED.value,
        "failed": StageStatus.FAILED.value,
        "cancelled": StageStatus.INTERRUPTED.value,
        "interrupted": StageStatus.INTERRUPTED.value,
    }
    rows = []
    for item in manifest.get("stages", []):
        if not isinstance(item, dict):
            continue
        status = status_map.get(str(item.get("status", "")).lower())
        if status is None:
            continue
        stage = _canonical_pipeline_stage(item.get("id"))
        if stage is None:
            continue
        started_at = _manifest_datetime(item.get("startedAt"))
        if stage is PipelineStage.INPUT and started_at is None:
            started_at = _manifest_datetime(manifest.get("createdAt"))
        if started_at is None:
            continue
        settings = settings_for_stage(config, stage)
        duration = item.get("durationMs")
        rows.append({
            "stage": stage.value,
            "status": status,
            "started_at": started_at,
            "completed_at": _manifest_datetime(item.get("finishedAt")) or started_at,
            "duration_ms": max(0, int(duration)) if isinstance(duration, (int, float)) else None,
            "settings": settings,
            "settings_fingerprint": fingerprint(settings),
            "error_code": "stage_failed" if status == StageStatus.FAILED.value else None,
            "error_message": item.get("reason") if status != StageStatus.COMPLETED.value else None,
            "metrics": item.get("metrics") if isinstance(item.get("metrics"), dict) else {},
        })
    return rows


def _canonical_pipeline_stage(value: Any) -> PipelineStage | None:
    aliases = {"upscaling": "upscale", "textline_merge": "text_grouping"}
    try:
        return PipelineStage(aliases.get(str(value), str(value)))
    except ValueError:
        return None


def _pipeline_manifest_artifact_rows(
    manifest: Any, folder_path: Path, folder_name: str
) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        return []
    rows = []
    for item in manifest.get("stages", []):
        if not isinstance(item, dict) or item.get("status") != "completed":
            continue
        stage = _canonical_pipeline_stage(item.get("id"))
        if stage is None:
            continue
        for name in item.get("artifacts", []):
            if not isinstance(name, str) or Path(name).name != name or Path(name).suffix.lower() == ".json":
                continue
            path = folder_path / name
            if not path.is_file():
                continue
            suffix = path.suffix.lower().lstrip(".")
            artifact_type = re.sub(r"[^a-z0-9]+", "_", f"{path.stem}_{suffix}".lower()).strip("_")
            if not artifact_type or len(artifact_type) > 64:
                continue
            rows.append({
                "stage": stage.value,
                "artifact_type": artifact_type,
                "relative_path": f"{folder_name}/{name}",
                "mime_type": mimetypes.guess_type(name)[0],
                "size_bytes": path.stat().st_size,
            })
    return rows


def _manifest_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


async def sync_result_folder(
    store,
    folder: str | Path,
    *,
    generate_variants: bool = True,
    page_order: int | None = None,
    replace_page_id: str | None = None,
    pipeline_document_types: dict[str, tuple[str, str]],
    pipeline_manifest_stage_rows: Callable[[Any], list[dict[str, Any]]],
    pipeline_manifest_artifact_rows: Callable[[Any, Path, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    folder_path = Path(folder)
    if not folder_path.is_absolute():
        folder_path = store.result_root / folder_path
    if store.pool is None:
        raise RuntimeError("PostgreSQL store is not started")
    folder_path = folder_path.resolve()
    folder_name = _safe_folder(folder_path.name)
    existing = await store.pool.fetchrow(
        f"""
        SELECT p.id, p.manga_group_id, g.title AS manga_title,
               p.folder, p.original_name, p.original_sort_key, p.source_type,
               p.finished_at, p.request_id, p.metadata, p.text_regions, p.page_order
        FROM pages p
        JOIN manga_groups g ON g.id = p.manga_group_id
        WHERE p.{"id" if replace_page_id else "folder"}=$1
        """,
        replace_page_id or folder_name,
    )
    replacing_folder = existing is not None and existing["folder"] != folder_name
    stored = await store.pool.fetch(
        """
        SELECT rd.name,rd.payload
        FROM result_documents rd
        LEFT JOIN pages p ON p.id = rd.page_id
        LEFT JOIN pipeline_runs pr ON pr.id = rd.pipeline_run_id
        WHERE (p.folder=$1 OR pr.folder=$1) AND rd.name=ANY($2::text[])
        ORDER BY rd.page_id IS NOT NULL DESC
        """,
        folder_name,
        ["meta.json", "text_regions.json", "pipeline_manifest.json"],
    )
    stored_documents = {
        row["name"]: _json_load(row["payload"], {} if row["name"] == "meta.json" else [])
        for row in stored
    }
    snapshot = await asyncio.to_thread(
        store._page_snapshot,
        folder_path,
        existing is None or replacing_folder,
        existing is None or replacing_folder,
        stored_documents.get("meta.json"),
        stored_documents.get("text_regions.json"),
        generate_variants,
    )
    if existing is None:
        page_values = snapshot
        page_id = str(uuid.uuid4())
        group_id = None
        if snapshot.get("manga_group_id"):
            group_id = await store.resolve_group_id(snapshot["manga_group_id"], create=False)
        if group_id is None:
            group_id = await store.resolve_group_id(snapshot["manga_title"], create=True)
        existing_regions: list[Any] = []
    else:
        existing_regions = _json_load(existing["text_regions"], [])
        page_values = {
            **snapshot,
            "manga_title": existing["manga_title"],
            "manga_group_id": existing["manga_group_id"],
            "original_name": existing["original_name"],
            "original_sort_key": existing["original_sort_key"],
            "page_order": existing["page_order"],
            "source_type": snapshot["source_type"] if replacing_folder else existing["source_type"],
            "finished_at": snapshot["finished_at"] if replacing_folder else existing["finished_at"],
            "request_id": snapshot["request_id"] or existing["request_id"],
            "metadata": snapshot["metadata"] if replacing_folder else _json_load(existing["metadata"], {}),
            "text_regions": snapshot["text_regions"] if replacing_folder else existing_regions,
        }
        page_id = existing["id"]
        group_id = existing["manga_group_id"]

    async with store.pool.acquire() as connection:
        async with connection.transaction():
            if group_id is None:
                group_id = await connection.fetchval(
                    """
                    INSERT INTO manga_groups(id,title) VALUES($1,$2)
                    ON CONFLICT(title) DO UPDATE SET updated_at=now()
                    RETURNING id
                    """,
                    str(uuid.uuid4()),
                    page_values["manga_title"],
                )
            await connection.fetchrow(
                "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
            )
            if existing is None:
                page_values["page_order"] = _page_order(page_order) or page_values.get("page_order")
                # Older jobs and imported metadata may carry a position already in use.
                if page_values["page_order"] is None or await connection.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM pages
                        WHERE active AND manga_group_id=$1 AND page_order=$2
                          AND folder<>$3
                    )
                    """,
                    group_id, page_values["page_order"], folder_name,
                ):
                    page_values["page_order"] = await store._next_page_order(connection, group_id)
            page_values["metadata"]["pageOrder"] = page_values["page_order"]
            conflict_target = "id" if existing is not None else "folder"
            await connection.execute(
                f"""
                INSERT INTO pages(
                    id, folder, manga_group_id, original_name, original_sort_key, source_type,
                    page_order, finished_at, request_id, input_name, final_name, has_inpainted, has_regions,
                    has_thumbnail, asset_version, metadata, text_regions, active, updated_at
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,$17::jsonb,TRUE,now())
                ON CONFLICT({conflict_target}) DO UPDATE SET
                    folder=EXCLUDED.folder,
                    manga_group_id=EXCLUDED.manga_group_id,
                    original_name=EXCLUDED.original_name,
                    original_sort_key=EXCLUDED.original_sort_key,
                    page_order=pages.page_order,
                    source_type=EXCLUDED.source_type,
                    finished_at=EXCLUDED.finished_at,
                    request_id=EXCLUDED.request_id,
                    input_name=EXCLUDED.input_name,
                    final_name=EXCLUDED.final_name,
                    has_inpainted=EXCLUDED.has_inpainted,
                    has_regions=EXCLUDED.has_regions,
                    has_thumbnail=EXCLUDED.has_thumbnail,
                    asset_version=EXCLUDED.asset_version,
                    metadata=EXCLUDED.metadata,
                    text_regions=EXCLUDED.text_regions,
                    active=TRUE,
                    updated_at=now()
                """,
                page_id,
                page_values["folder"],
                group_id,
                page_values["original_name"],
                page_values["original_sort_key"],
                page_values["source_type"],
                page_values["page_order"],
                page_values["finished_at"],
                page_values["request_id"],
                page_values["input_name"],
                page_values["final_name"],
                page_values["has_inpainted"],
                bool(page_values["text_regions"]),
                page_values["has_thumbnail"],
                page_values["asset_version"],
                _json_dump(page_values["metadata"]),
                _json_dump(page_values["text_regions"]),
            )
            await connection.execute("DELETE FROM page_artifacts WHERE page_id=$1", page_id)
            artifact_rows = [
                (
                    str(uuid.uuid4()),
                    page_id,
                    artifact["name"],
                    artifact["relative_path"],
                    artifact["size_bytes"],
                )
                for artifact in page_values["artifacts"]
            ]
            if artifact_rows:
                await connection.executemany(
                    """
                    INSERT INTO page_artifacts(id,page_id,name,relative_path,size_bytes,present)
                    VALUES($1,$2,$3,$4,$5,TRUE)
                    """,
                    artifact_rows,
                )
            pipeline_run_id = await connection.fetchval(
                "SELECT id FROM pipeline_runs WHERE folder=$1", folder_name
            )
            indexed_documents = {
                **({"pipeline_manifest.json": stored_documents["pipeline_manifest.json"]}
                   if "pipeline_manifest.json" in stored_documents else {}),
                **snapshot["documents"],
            }
            if pipeline_run_id:
                run_documents = await connection.fetch(
                    "SELECT name,payload FROM result_documents WHERE pipeline_run_id=$1",
                    pipeline_run_id,
                )
                indexed_documents = {
                    **{row["name"]: _json_load(row["payload"], None) for row in run_documents},
                    **indexed_documents,
                }
                await connection.execute(
                    """
                    DELETE FROM result_documents old
                    WHERE old.pipeline_run_id=$1
                      AND EXISTS (
                          SELECT 1 FROM result_documents current
                          WHERE current.page_id=$2 AND current.name=old.name
                      )
                    """,
                    pipeline_run_id,
                    page_id,
                )
                await connection.execute(
                    """
                    UPDATE result_documents
                    SET page_id=$2, pipeline_run_id=NULL, updated_at=now()
                    WHERE pipeline_run_id=$1
                    """,
                    pipeline_run_id,
                    page_id,
                )
                await connection.execute("DELETE FROM pipeline_runs WHERE id=$1", pipeline_run_id)
            document_names = list(indexed_documents)
            structured_names = [
                name for name in document_names if name in pipeline_document_types
            ]
            for name in structured_names:
                stage, document_type = pipeline_document_types[name]
                payload = _json_dump(indexed_documents[name])
                revision = int(await connection.fetchval(
                    """SELECT COALESCE(MAX(revision),0)+1 FROM pipeline_documents
                       WHERE page_id=$1 AND stage=$2 AND document_type=$3""",
                    page_id,
                    stage,
                    document_type,
                ))
                await connection.execute(
                    """UPDATE pipeline_documents SET active=FALSE
                       WHERE page_id=$1 AND stage=$2 AND document_type=$3 AND active""",
                    page_id,
                    stage,
                    document_type,
                )
                await connection.execute(
                    """INSERT INTO pipeline_documents(
                           page_id,stage,document_type,revision,schema_version,payload,active
                       ) VALUES($1,$2,$3,$4,1,$5::jsonb,TRUE)""",
                    page_id,
                    stage,
                    document_type,
                    revision,
                    payload,
                )
            if structured_names:
                await connection.execute(
                    "DELETE FROM result_documents WHERE page_id=$1 AND name=ANY($2::text[])",
                    page_id,
                    structured_names,
                )
            for stage_record in pipeline_manifest_stage_rows(
                indexed_documents.get("pipeline_manifest.json")
            ):
                state = await connection.fetchrow(
                    """INSERT INTO page_stage_state(
                           page_id,stage,status,attempt,input_fingerprint,
                           settings_fingerprint,started_at,completed_at,duration_ms,
                           error_code,error_message,updated_at
                       ) VALUES($1,$2,$3,1,NULL,$4,$5,$6,$7,$8,$9,now())
                       ON CONFLICT(page_id,stage) DO UPDATE SET
                           status=EXCLUDED.status,
                           attempt=page_stage_state.attempt+1,
                           input_fingerprint=NULL,
                           settings_fingerprint=EXCLUDED.settings_fingerprint,
                           started_at=EXCLUDED.started_at,
                           completed_at=EXCLUDED.completed_at,
                           duration_ms=EXCLUDED.duration_ms,
                           error_code=EXCLUDED.error_code,
                           error_message=EXCLUDED.error_message,
                           updated_at=now()
                       WHERE page_stage_state.status <> 'running'
                         AND (page_stage_state.started_at IS NULL
                              OR page_stage_state.started_at < EXCLUDED.started_at)
                       RETURNING attempt""",
                    page_id,
                    stage_record["stage"],
                    stage_record["status"],
                    stage_record["settings_fingerprint"],
                    stage_record["started_at"],
                    stage_record["completed_at"],
                    stage_record["duration_ms"],
                    stage_record["error_code"],
                    stage_record["error_message"],
                )
                if state is not None:
                    await connection.execute(
                        """INSERT INTO page_stage_attempts(
                               page_id,stage,attempt,status,started_at,finished_at,
                               duration_ms,settings,metrics,error_code,error_message
                           ) VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11)""",
                        page_id,
                        stage_record["stage"],
                        state["attempt"],
                        stage_record["status"],
                        stage_record["started_at"],
                        stage_record["completed_at"],
                        stage_record["duration_ms"],
                        _json_dump(stage_record["settings"]),
                        _json_dump(stage_record["metrics"]),
                        stage_record["error_code"],
                        stage_record["error_message"],
                    )
            for artifact in pipeline_manifest_artifact_rows(
                indexed_documents.get("pipeline_manifest.json"),
                folder_path,
                folder_name,
            ):
                revision = int(await connection.fetchval(
                    """SELECT COALESCE(MAX(revision),0)+1 FROM pipeline_artifacts
                       WHERE page_id=$1 AND stage=$2 AND artifact_type=$3""",
                    page_id,
                    artifact["stage"],
                    artifact["artifact_type"],
                ))
                await connection.execute(
                    """UPDATE pipeline_artifacts SET active=FALSE
                       WHERE page_id=$1 AND stage=$2 AND artifact_type=$3 AND active""",
                    page_id,
                    artifact["stage"],
                    artifact["artifact_type"],
                )
                await connection.execute(
                    """INSERT INTO pipeline_artifacts(
                           page_id,stage,artifact_type,relative_path,revision,active,
                           mime_type,width,height,size_bytes,checksum
                       ) VALUES($1,$2,$3,$4,$5,TRUE,$6,NULL,NULL,$7,NULL)""",
                    page_id,
                    artifact["stage"],
                    artifact["artifact_type"],
                    artifact["relative_path"],
                    revision,
                    artifact["mime_type"],
                    artifact["size_bytes"],
                )
            legacy_document_names = [
                name for name in document_names if name not in pipeline_document_types
            ]
            if legacy_document_names:
                await connection.execute(
                    "DELETE FROM result_documents WHERE page_id=$1 AND name=ANY($2::text[])",
                    page_id,
                    legacy_document_names,
                )
                await connection.executemany(
                    """
                    INSERT INTO result_documents(page_id,name,payload,updated_at)
                    VALUES($1,$2,$3::jsonb,now())
                    """,
                    [
                        (page_id, name, _json_dump(payload))
                        for name, payload in indexed_documents.items()
                        if name in legacy_document_names
                    ],
                )
            await connection.execute(
                "DELETE FROM result_documents WHERE page_id=$1 AND name=ANY($2::text[])",
                page_id,
                ["meta.json", "text_regions.json"],
            )
    page_values["manga_group_id"] = group_id
    if replacing_folder:
        await asyncio.to_thread(
            shutil.rmtree,
            store.result_root / _safe_folder(existing["folder"]),
            True,
        )
    await asyncio.to_thread((folder_path / "meta.json").unlink, True)
    await asyncio.to_thread((folder_path / "text_regions.json").unlink, True)
    for name in snapshot["documents"]:
        await asyncio.to_thread((folder_path / name).unlink, True)
    return page_values
