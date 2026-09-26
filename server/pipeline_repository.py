"""Canonical PostgreSQL pipeline-state and artifact repository."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from manga_translator.pipeline.stages import PipelineStage, StageStatus
from server.postgres_common import _json_dump, _json_load


_PIPELINE_DOCUMENT_TYPES = {
    "detection.json": ("detection", "regions"),
    "ocr.json": ("ocr", "regions"),
    "bubble_detections.json": ("bubble_detection", "detections"),
    "text_regions_merged.json": ("text_grouping", "regions"),
    "translations.json": ("translation", "translations"),
    "professional_translation.json": ("translation", "professional_result"),
    "translation_remap.json": ("translation", "remap"),
    "layout.json": ("layout", "layout"),
    "text_regions.json": ("rendering", "text_regions"),
    "profiling.json": ("mask_generation", "metrics"),
}


class PipelineRepository:
    def __init__(self, store: Any):
        self._store = store

    @property
    def pool(self) -> Any:
        return self._store.pool

    async def start_pipeline_stage(
        self,
        page_ref: str,
        stage: str | PipelineStage,
        *,
        input_fingerprint: str | None = None,
        settings_fingerprint: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> int | None:
        """Start a durable attempt; return None when the result is not an indexed page."""
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        stage_id = PipelineStage(stage).value
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    INSERT INTO page_stage_state(
                        page_id,stage,status,attempt,input_fingerprint,
                        settings_fingerprint,started_at,completed_at,duration_ms,
                        error_code,error_message,updated_at
                    )
                    SELECT id,$2,'running',1,$3,$4,now(),NULL,NULL,NULL,NULL,now()
                    FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1
                    ON CONFLICT(page_id,stage) DO UPDATE SET
                        status='running',
                        attempt=page_stage_state.attempt+1,
                        input_fingerprint=COALESCE(EXCLUDED.input_fingerprint,page_stage_state.input_fingerprint),
                        settings_fingerprint=COALESCE(EXCLUDED.settings_fingerprint,page_stage_state.settings_fingerprint),
                        started_at=now(),completed_at=NULL,duration_ms=NULL,
                        error_code=NULL,error_message=NULL,updated_at=now()
                    WHERE page_stage_state.status <> 'running'
                    RETURNING page_id,attempt
                    """,
                    page_ref,
                    stage_id,
                    input_fingerprint,
                    settings_fingerprint,
                )
                if row is None:
                    existing = await connection.fetchrow(
                        """SELECT state.status FROM page_stage_state state
                           JOIN pages page ON page.id=state.page_id
                           WHERE page.active AND (page.id=$1 OR page.folder=$1)
                             AND state.stage=$2""",
                        page_ref,
                        stage_id,
                    )
                    if existing and existing["status"] == "running":
                        raise RuntimeError(f"Pipeline stage is already running: {stage_id}")
                    return None
                await connection.execute(
                    """
                    INSERT INTO page_stage_attempts(page_id,stage,attempt,status,settings)
                    VALUES($1,$2,$3,'running',$4::jsonb)
                    """,
                    row["page_id"],
                    stage_id,
                    row["attempt"],
                    _json_dump(settings or {}),
                )
                return int(row["attempt"])

    async def finish_pipeline_stage(
        self,
        page_ref: str,
        stage: str | PipelineStage,
        status: str | StageStatus = StageStatus.COMPLETED,
        *,
        duration_ms: int | None = None,
        metrics: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Finish the current stage attempt and retain its diagnostic history."""
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        stage_id = PipelineStage(stage).value
        status_id = StageStatus(status).value
        if status_id not in {"completed", "failed", "interrupted"}:
            raise ValueError(f"Invalid terminal stage status: {status_id}")
        duration = max(0, int(duration_ms)) if duration_ms is not None else None
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    SELECT state.page_id,state.attempt
                    FROM page_stage_state state
                    JOIN pages page ON page.id=state.page_id
                    WHERE page.active AND (page.id=$1 OR page.folder=$1)
                      AND state.stage=$2 AND state.status='running'
                    FOR UPDATE OF state
                    """,
                    page_ref,
                    stage_id,
                )
                if row is None:
                    return False
                await connection.execute(
                    """
                    UPDATE page_stage_state
                    SET status=$3,completed_at=now(),duration_ms=$4,
                        error_code=$5,error_message=$6,updated_at=now()
                    WHERE page_id=$1 AND stage=$2
                    """,
                    row["page_id"],
                    stage_id,
                    status_id,
                    duration,
                    error_code,
                    error_message,
                )
                await connection.execute(
                    """
                    UPDATE page_stage_attempts
                    SET status=$4,finished_at=now(),duration_ms=$5,
                        metrics=$6::jsonb,error_code=$7,error_message=$8
                    WHERE page_id=$1 AND stage=$2 AND attempt=$3
                    """,
                    row["page_id"],
                    stage_id,
                    row["attempt"],
                    status_id,
                    duration,
                    _json_dump(metrics or {}),
                    error_code,
                    error_message,
                )
        return True

    async def invalidate_pipeline_stages(
        self, page_ref: str, stages: list[str | PipelineStage]
    ) -> int:
        """Mark existing checkpoints stale while keeping their attempt history."""
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        stage_ids = list(dict.fromkeys(PipelineStage(stage).value for stage in stages))
        if not stage_ids:
            return 0
        result = await self.pool.execute(
            """
            UPDATE page_stage_state state
            SET status='invalidated',started_at=NULL,completed_at=NULL,
                duration_ms=NULL,error_code=NULL,error_message=NULL,updated_at=now()
            FROM pages page
            WHERE state.page_id=page.id AND page.active
              AND (page.id=$1 OR page.folder=$1) AND state.stage=ANY($2::text[])
              AND state.status <> 'running'
            """,
            page_ref,
            stage_ids,
        )
        return int(result.rsplit(" ", 1)[-1])

    async def get_pipeline_stage_state(self, page_ref: str) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        rows = await self.pool.fetch(
            """
            SELECT state.stage,state.status,state.attempt,state.input_fingerprint,
                   state.settings_fingerprint,state.started_at,state.completed_at,
                   state.duration_ms,state.error_code,state.error_message
            FROM page_stage_state state
            JOIN pages page ON page.id=state.page_id
            WHERE page.active AND (page.id=$1 OR page.folder=$1)
            ORDER BY array_position(
                ARRAY['input','colorization','upscale','detection','ocr','bubble_detection',
                      'text_grouping','translation','mask_generation','layout',
                      'inpainting','rendering','finalize'],state.stage
            )
            """,
            page_ref,
        )
        return [dict(row) for row in rows]

    async def interrupt_running_pipeline_stages(self) -> int:
        """Close attempts left running when the server process stopped."""
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE page_stage_attempts attempt
                    SET status='interrupted',finished_at=now(),
                        error_code='process_restart',
                        error_message='Server stopped before the stage completed'
                    FROM page_stage_state state
                    WHERE attempt.page_id=state.page_id
                      AND attempt.stage=state.stage
                      AND attempt.attempt=state.attempt
                      AND attempt.status='running'
                      AND state.status='running'
                    """
                )
                result = await connection.execute(
                    """
                    UPDATE page_stage_state
                    SET status='interrupted',completed_at=now(),
                        error_code='process_restart',
                        error_message='Server stopped before the stage completed',
                        updated_at=now()
                    WHERE status='running'
                    """
                )
        return int(result.rsplit(" ", 1)[-1])

    async def save_pipeline_document(
        self,
        page_ref: str,
        stage: str | PipelineStage,
        document_type: str,
        payload: Any,
        *,
        schema_version: int = 1,
    ) -> int:
        """Save a new structured-document revision and activate it atomically."""
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        stage_id = PipelineStage(stage).value
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", document_type):
            raise ValueError("Invalid pipeline document type")
        if schema_version < 1:
            raise ValueError("schema_version must be positive")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                page_id = await connection.fetchval(
                    "SELECT id FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1 FOR UPDATE",
                    page_ref,
                )
                if page_id is None:
                    raise ValueError("Pipeline documents require an indexed page")
                revision = int(await connection.fetchval(
                    """SELECT COALESCE(MAX(revision),0)+1 FROM pipeline_documents
                       WHERE page_id=$1 AND stage=$2 AND document_type=$3""",
                    page_id,
                    stage_id,
                    document_type,
                ))
                await connection.execute(
                    """UPDATE pipeline_documents SET active=FALSE
                       WHERE page_id=$1 AND stage=$2 AND document_type=$3 AND active""",
                    page_id,
                    stage_id,
                    document_type,
                )
                await connection.execute(
                    """INSERT INTO pipeline_documents(
                           page_id,stage,document_type,revision,schema_version,payload,active
                       ) VALUES($1,$2,$3,$4,$5,$6::jsonb,TRUE)""",
                    page_id,
                    stage_id,
                    document_type,
                    revision,
                    schema_version,
                    _json_dump(payload),
                )
        return revision

    async def get_pipeline_document(
        self, page_ref: str, stage: str | PipelineStage, document_type: str
    ) -> dict[str, Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        stage_id = PipelineStage(stage).value
        row = await self.pool.fetchrow(
            """SELECT document.revision,document.schema_version,document.payload
               FROM pipeline_documents document
               JOIN pages page ON page.id=document.page_id
               WHERE page.active AND (page.id=$1 OR page.folder=$1)
                 AND document.stage=$2 AND document.document_type=$3 AND document.active""",
            page_ref,
            stage_id,
            document_type,
        )
        if row is None:
            return None
        return {
            "revision": row["revision"],
            "schema_version": row["schema_version"],
            "payload": _json_load(row["payload"], None),
        }

    async def commit_pipeline_outputs(
        self,
        page_ref: str,
        documents: dict[str, Any],
        artifacts: list[dict[str, Any]],
    ) -> bool:
        """Activate structured-document and artifact revisions in one transaction."""
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")

        prepared_artifacts = []
        for artifact in artifacts:
            stage_id = PipelineStage(artifact["stage"]).value
            artifact_type = artifact["artifact_type"]
            relative_path = str(artifact["relative_path"])
            path = PurePosixPath(relative_path)
            windows_path = PureWindowsPath(relative_path)
            if (
                path.is_absolute()
                or windows_path.is_absolute()
                or windows_path.drive
                or not path.parts
                or ".." in path.parts
                or ".." in windows_path.parts
            ):
                raise ValueError("Artifact path must stay inside the result directory")
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", artifact_type):
                raise ValueError("Invalid pipeline artifact type")
            width, height, size = (
                artifact.get("width"), artifact.get("height"), artifact["size_bytes"]
            )
            if size < 0 or (width is not None and width <= 0) or (height is not None and height <= 0):
                raise ValueError("Invalid artifact dimensions or size")
            prepared_artifacts.append((
                stage_id,
                artifact_type,
                path.as_posix(),
                artifact.get("mime_type"),
                width,
                height,
                size,
                artifact.get("checksum"),
            ))

        structured_documents = [
            (name, *_PIPELINE_DOCUMENT_TYPES[name], _json_dump(payload))
            for name, payload in documents.items()
            if name in _PIPELINE_DOCUMENT_TYPES
        ]
        document_rows = [
            (name, _json_dump(payload))
            for name, payload in documents.items()
            if Path(name).name == name and name.endswith(".json")
        ]
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                page_id = await connection.fetchval(
                    "SELECT id FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1 FOR UPDATE",
                    page_ref,
                )
                if page_id is None:
                    return False

                if document_rows:
                    names = [name for name, _ in document_rows]
                    await connection.execute(
                        "DELETE FROM result_documents WHERE page_id=$1 AND name=ANY($2::text[])",
                        page_id,
                        names,
                    )
                    await connection.executemany(
                        "INSERT INTO result_documents(page_id,name,payload,updated_at) VALUES($1,$2,$3::jsonb,now())",
                        [(page_id, name, payload) for name, payload in document_rows],
                    )

                for name, stage_id, document_type, payload in structured_documents:
                    revision = int(await connection.fetchval(
                        """SELECT COALESCE(MAX(revision),0)+1 FROM pipeline_documents
                           WHERE page_id=$1 AND stage=$2 AND document_type=$3""",
                        page_id,
                        stage_id,
                        document_type,
                    ))
                    await connection.execute(
                        """UPDATE pipeline_documents SET active=FALSE
                           WHERE page_id=$1 AND stage=$2 AND document_type=$3 AND active""",
                        page_id,
                        stage_id,
                        document_type,
                    )
                    await connection.execute(
                        """INSERT INTO pipeline_documents(
                               page_id,stage,document_type,revision,schema_version,payload,active
                           ) VALUES($1,$2,$3,$4,1,$5::jsonb,TRUE)""",
                        page_id,
                        stage_id,
                        document_type,
                        revision,
                        payload,
                    )

                for artifact in prepared_artifacts:
                    stage_id, artifact_type, path, mime_type, width, height, size, checksum = artifact
                    revision = int(await connection.fetchval(
                        """SELECT COALESCE(MAX(revision),0)+1 FROM pipeline_artifacts
                           WHERE page_id=$1 AND stage=$2 AND artifact_type=$3""",
                        page_id,
                        stage_id,
                        artifact_type,
                    ))
                    await connection.execute(
                        """UPDATE pipeline_artifacts SET active=FALSE
                           WHERE page_id=$1 AND stage=$2 AND artifact_type=$3 AND active""",
                        page_id,
                        stage_id,
                        artifact_type,
                    )
                    await connection.execute(
                        """INSERT INTO pipeline_artifacts(
                               page_id,stage,artifact_type,relative_path,revision,active,
                               mime_type,width,height,size_bytes,checksum
                           ) VALUES($1,$2,$3,$4,$5,TRUE,$6,$7,$8,$9,$10)""",
                        page_id,
                        stage_id,
                        artifact_type,
                        path,
                        revision,
                        mime_type,
                        width,
                        height,
                        size,
                        checksum,
                    )
        return True

    async def register_pipeline_artifact(
        self,
        page_ref: str,
        stage: str | PipelineStage,
        artifact_type: str,
        relative_path: str,
        *,
        size_bytes: int,
        checksum: str | None = None,
        mime_type: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> int:
        """Register a completed disk artifact as the active revision."""
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        stage_id = PipelineStage(stage).value
        committed = await self._store.commit_pipeline_outputs(
            page_ref,
            {},
            [{
                "stage": stage_id,
                "artifact_type": artifact_type,
                "relative_path": relative_path,
                "size_bytes": size_bytes,
                "checksum": checksum,
                "mime_type": mime_type,
                "width": width,
                "height": height,
            }],
        )
        if not committed:
            raise ValueError("Pipeline artifacts require an indexed page")
        active = await self._store.get_pipeline_artifact(page_ref, stage_id, artifact_type)
        if active is None:
            raise RuntimeError("Pipeline artifact revision was not activated")
        return int(active["revision"])

    async def get_pipeline_artifact(
        self, page_ref: str, stage: str | PipelineStage, artifact_type: str
    ) -> dict[str, Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        stage_id = PipelineStage(stage).value
        row = await self.pool.fetchrow(
            """SELECT artifact.relative_path,artifact.revision,artifact.mime_type,
                      artifact.width,artifact.height,artifact.size_bytes,artifact.checksum
               FROM pipeline_artifacts artifact
               JOIN pages page ON page.id=artifact.page_id
               WHERE page.active AND (page.id=$1 OR page.folder=$1)
                 AND artifact.stage=$2 AND artifact.artifact_type=$3 AND artifact.active""",
            page_ref,
            stage_id,
            artifact_type,
        )
        return dict(row) if row is not None else None
