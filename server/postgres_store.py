"""PostgreSQL metadata/state storage with file-backed manga assets."""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
import datetime as dt
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from server.batch_store import BatchConflict, BatchNotFound, BatchStore, InvalidBatch
from server.image_variants import asset_version, final_file, generate_image_variants
from server.manga_summary import synopsis_status
from manga_translator.pipeline.stages import (
    PipelineStage,
    StageStatus,
    fingerprint,
    settings_for_stage,
)
from manga_translator.utils.image_storage import find_asset

logger = logging.getLogger("manga-translator.postgres")
_MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_SAFE_FOLDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
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


class SeriesStoreError(Exception):
    pass


class SeriesNotFound(SeriesStoreError):
    pass


class SeriesConflict(SeriesStoreError):
    pass


class InvalidSeries(SeriesStoreError):
    pass


class GroupConflict(SeriesStoreError):
    pass


class GroupNotFound(SeriesStoreError):
    pass


def _json_load(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return default


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _natural_sort_key(value: str) -> str:
    return re.sub(
        r"\d+",
        lambda match: f"{int(match.group()):020d}",
        str(value).casefold(),
    )


def _parse_finished_at(value: Any, fallback: dt.datetime) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return fallback


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


def _manga_id(title: str) -> str:
    value = (title or "Ungrouped").strip() or "Ungrouped"
    hashed = 2166136261
    for byte in value.encode("utf-8"):
        hashed = ((hashed ^ byte) * 16777619) & 0xFFFFFFFF
    return f"manga-{hashed:x}"


def _iso(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return str(value)


def _safe_folder(folder: str) -> str:
    if not isinstance(folder, str) or not _SAFE_FOLDER.fullmatch(folder):
        raise ValueError("Invalid result folder")
    return folder


def _page_order(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _input_file(folder_path: Path) -> Path | None:
    return find_asset(folder_path, "input")


class PostgresStore:
    def __init__(self, database_url: str, result_root: str | Path):
        self.database_url = database_url
        self.result_root = Path(result_root).resolve()
        self.pool: Any = None

    @property
    def ready(self) -> bool:
        return self.pool is not None

    async def start(self, check_schema: bool = True) -> None:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required")
        try:
            import asyncpg
        except ImportError as error:  # pragma: no cover - dependency is installed in production
            raise RuntimeError("asyncpg is required") from error

        self.pool = await asyncpg.create_pool(
            dsn=self.database_url,
            min_size=1,
            max_size=int(os.getenv("DATABASE_POOL_MAX_SIZE", "10")),
            command_timeout=30,
        )
        try:
            if check_schema:
                await self.check_schema()
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def get_page_id(self, page_ref: str) -> str | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        return await self.pool.fetchval(
            "SELECT id FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1",
            page_ref,
        )

    async def check_schema(self) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        latest = sorted(path.stem for path in _MIGRATIONS_DIR.glob("*.sql"))[-1]
        try:
            version = await self.pool.fetchval(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            )
        except Exception as error:
            raise RuntimeError(
                "PostgreSQL schema is not ready; run `python -m server.db_cli migrate`"
            ) from error
        if version != latest:
            raise RuntimeError(
                "PostgreSQL schema is not ready; run `python -m server.db_cli migrate`"
            )

    async def apply_migrations(self) -> str:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        applied = []
        try:
            applied_versions = {
                row["version"]
                for row in await self.pool.fetch("SELECT version FROM schema_migrations")
            }
        except Exception:
            applied_versions = set()
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            version = path.stem
            if version in applied_versions:
                applied.append(version)
                continue
            sql = path.read_text(encoding="utf-8")
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    await connection.execute(sql)
                    has_id = await connection.fetchval(
                        """
                        SELECT EXISTS(
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema='public' AND table_name='schema_migrations'
                              AND column_name='id'
                        )
                        """
                    )
                    if has_id:
                        await connection.execute(
                            """
                            INSERT INTO schema_migrations(id,version)
                            VALUES($1,$2) ON CONFLICT(version) DO NOTHING
                            """,
                            f"migration-{version}",
                            version,
                        )
                    else:
                        await connection.execute(
                            "INSERT INTO schema_migrations(version) VALUES($1) ON CONFLICT DO NOTHING",
                            version,
                        )
            applied.append(version)
        return applied[-1] if applied else ""

    def _page_snapshot(
        self,
        folder_path: Path,
        read_metadata: bool = True,
        read_regions: bool = True,
        metadata_override: dict[str, Any] | None = None,
        regions_override: list[Any] | None = None,
        generate_variants: bool = True,
    ) -> dict[str, Any]:
        folder = _safe_folder(folder_path.name)
        final_path = final_file(folder_path)
        if final_path is None:
            raise ValueError(f"Missing final image: {folder}")
        metadata_path = folder_path / "meta.json"
        metadata: dict[str, Any] = copy.deepcopy(metadata_override or {})
        if read_metadata and not metadata and metadata_path.is_file():
            try:
                value = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    metadata = value
            except (OSError, UnicodeError, ValueError) as error:
                raise ValueError(f"Malformed meta.json: {folder}") from error

        regions_path = folder_path / "text_regions.json"
        text_regions: list[Any] = copy.deepcopy(regions_override or [])
        if read_regions and not text_regions and regions_path.is_file():
            try:
                value = json.loads(regions_path.read_text(encoding="utf-8"))
                if not isinstance(value, list):
                    raise ValueError("text regions are not an array")
                text_regions = value
            except (OSError, UnicodeError, ValueError) as error:
                raise ValueError(f"Malformed text_regions.json: {folder}") from error

        original_name = metadata.get("originalName")
        if not original_name or original_name == "Unknown":
            original_name = f"{folder}.png"
        manga_title = str(metadata.get("mangaTitle") or "Ungrouped").strip() or "Ungrouped"
        manga_group_id = metadata.get("mangaGroupId") or metadata.get("groupId")
        if manga_group_id is not None:
            manga_group_id = str(manga_group_id).strip() or None
        input_path = _input_file(folder_path)
        finished_at = _parse_finished_at(
            metadata.get("finishedAt"),
            dt.datetime.fromtimestamp(final_path.stat().st_mtime, dt.timezone.utc),
        )
        artifact_names = {
            path.name
            for path in folder_path.iterdir()
            if path.is_file()
            and path.name not in {"meta.json", "text_regions.json"}
            and path.suffix.lower() != ".json"
        }
        documents: dict[str, Any] = {}
        for path in folder_path.glob("*.json"):
            if path.name in {"meta.json", "text_regions.json"}:
                continue
            try:
                documents[path.name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as error:
                raise ValueError(f"Malformed {path.name}: {folder}") from error
        variants = generate_image_variants(folder_path) if generate_variants else {}
        artifact_names.update(f"{name}.webp" for name in variants)
        artifacts = [
            {
                "name": name,
                "relative_path": name,
                "size_bytes": (folder_path / name).stat().st_size,
            }
            for name in sorted(artifact_names)
            if (folder_path / name).is_file()
        ]
        return {
            "folder": folder,
            "manga_title": manga_title,
            "manga_group_id": manga_group_id,
            "original_name": str(original_name),
            "original_sort_key": _natural_sort_key(str(original_name)),
            "page_order": _page_order(metadata.get("pageOrder")),
            "source_type": (
                "original"
                if metadata.get("sourceType") == "original"
                or (
                    (metadata.get("settings") or {}).get("translator") == "none"
                    and (metadata.get("settings") or {}).get("inpainter") == "original"
                )
                else "translated"
            ),
            "finished_at": finished_at,
            "request_id": metadata.get("requestId"),
            "input_name": input_path.name if input_path else None,
            "final_name": final_path.name,
            "has_inpainted": find_asset(folder_path, "inpainted") is not None,
            "has_regions": bool(text_regions),
            "has_thumbnail": "thumbnail.webp" in artifact_names,
            "asset_version": asset_version(folder_path),
            "metadata": metadata,
            "text_regions": text_regions,
            "documents": documents,
            "artifacts": artifacts,
        }

    async def resolve_group_id(self, value: str, *, create: bool = False) -> str | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        value = str(value or "").strip()
        if not value:
            return None
        group_id = await self.pool.fetchval("SELECT id FROM manga_groups WHERE id=$1", value)
        if group_id is not None:
            return group_id
        group_id = await self.pool.fetchval("SELECT id FROM manga_groups WHERE title=$1", value)
        if group_id is not None:
            return group_id
        if value.startswith("manga-"):
            rows = await self.pool.fetch("SELECT id, title FROM manga_groups")
            for row in rows:
                if _manga_id(row["title"]) == value:
                    return row["id"]
        if not create:
            return None
        await self.pool.execute(
            "INSERT INTO manga_groups(id,title) VALUES($1,$2) ON CONFLICT(title) DO NOTHING",
            str(uuid.uuid4()),
            value,
        )
        return await self.pool.fetchval("SELECT id FROM manga_groups WHERE title=$1", value)

    async def _document_owner(self, value: str, connection: Any | None = None) -> tuple[str, str] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        value = _safe_folder(value)
        executor = connection if connection is not None else self.pool
        row = await executor.fetchrow(
            """
            SELECT 'page' AS kind, p.id
            FROM pages p
            WHERE p.active AND (p.id=$1 OR p.folder=$1)
            UNION ALL
            SELECT 'pipeline' AS kind, pr.id
            FROM pipeline_runs pr
            WHERE pr.id=$1 OR pr.folder=$1
            LIMIT 1
            """,
            value,
        )
        return (row["kind"], row["id"]) if row else None

    async def sync_result_folder(
        self,
        folder: str | Path,
        *,
        generate_variants: bool = True,
        page_order: int | None = None,
        replace_page_id: str | None = None,
    ) -> dict[str, Any]:
        folder_path = Path(folder)
        if not folder_path.is_absolute():
            folder_path = self.result_root / folder_path
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        folder_path = folder_path.resolve()
        folder_name = _safe_folder(folder_path.name)
        existing = await self.pool.fetchrow(
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
        stored = await self.pool.fetch(
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
            self._page_snapshot,
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
                group_id = await self.resolve_group_id(snapshot["manga_group_id"], create=False)
            if group_id is None:
                group_id = await self.resolve_group_id(snapshot["manga_title"], create=True)
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

        async with self.pool.acquire() as connection:
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
                        page_values["page_order"] = await self._next_page_order(connection, group_id)
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
                    name for name in document_names if name in _PIPELINE_DOCUMENT_TYPES
                ]
                for name in structured_names:
                    stage, document_type = _PIPELINE_DOCUMENT_TYPES[name]
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
                for stage_record in _pipeline_manifest_stage_rows(
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
                for artifact in _pipeline_manifest_artifact_rows(
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
                    name for name in document_names if name not in _PIPELINE_DOCUMENT_TYPES
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
                self.result_root / _safe_folder(existing["folder"]),
                True,
            )
        await asyncio.to_thread((folder_path / "meta.json").unlink, True)
        await asyncio.to_thread((folder_path / "text_regions.json").unlink, True)
        for name in snapshot["documents"]:
            await asyncio.to_thread((folder_path / name).unlink, True)
        return page_values

    async def index_untracked_results(self) -> dict[str, int]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        folder_names = await asyncio.to_thread(
            lambda: [
                entry.name
                for entry in os.scandir(self.result_root)
                if entry.is_dir() and not entry.name.startswith(".")
            ]
        )
        if not folder_names:
            return {"indexed": 0, "skipped": 0}
        known = await self.pool.fetch(
            "SELECT folder FROM pages WHERE active AND folder=ANY($1::text[])",
            folder_names,
        )
        known_folders = {row["folder"] for row in known}
        untracked = [name for name in folder_names if name not in known_folders]
        indexed = 0
        skipped = 0
        for name in untracked:
            folder = self.result_root / name
            if final_file(folder) is None:
                continue
            try:
                await self.sync_result_folder(folder, generate_variants=False)
                indexed += 1
            except Exception as error:
                skipped += 1
                logger.warning("Failed to index new result folder %s: %s", folder, error)
        return {"indexed": indexed, "skipped": skipped}

    async def get_text_regions(self, record_id: str) -> list[Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        value = await self.pool.fetchval(
            "SELECT text_regions FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1",
            record_id,
        )
        return _json_load(value, []) if value is not None else None

    async def update_text_regions(self, record_id: str, regions: list[dict[str, Any]]) -> bool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        result = await self.pool.execute(
            """
            UPDATE pages SET text_regions=$1::jsonb, has_regions=$2, updated_at=now()
            WHERE active AND (id=$3 OR folder=$3)
            """,
            _json_dump(regions),
            bool(regions),
            record_id,
        )
        return result == "UPDATE 1"

    async def update_review_status(self, record_id: str, status: str, reviewed_at: str | None) -> bool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        if reviewed_at is None:
            result = await self.pool.execute(
                """
                UPDATE pages
                SET metadata=jsonb_set(COALESCE(metadata, '{}'::jsonb) - 'reviewedAt', '{reviewStatus}', to_jsonb($1::text), true),
                    updated_at=now()
                WHERE active AND (id=$2 OR folder=$2)
                """,
                status,
                record_id,
            )
        else:
            result = await self.pool.execute(
                """
                UPDATE pages
                SET metadata=jsonb_set(
                        jsonb_set(COALESCE(metadata, '{}'::jsonb), '{reviewStatus}', to_jsonb($1::text), true),
                        '{reviewedAt}', to_jsonb($2::text), true
                    ),
                    updated_at=now()
                WHERE active AND (id=$3 OR folder=$3)
                """,
                status,
                reviewed_at,
                record_id,
            )
        return result == "UPDATE 1"

    async def save_documents(self, folder: str, documents: dict[str, Any]) -> None:
        """Persist virtual JSON sidecars without writing them beside image assets."""
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        folder = _safe_folder(folder)
        rows = []
        for name, payload in documents.items():
            if Path(name).name != name or not name.endswith(".json"):
                raise ValueError(f"Invalid result document name: {name}")
            rows.append((name, _json_dump(payload)))
        if not rows:
            return
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                owner = await self._document_owner(folder, connection)
                if owner is None:
                    owner_id = str(uuid.uuid4())
                    await connection.execute(
                        "INSERT INTO pipeline_runs(id,folder) VALUES($1,$2) ON CONFLICT(folder) DO NOTHING",
                        owner_id,
                        folder,
                    )
                    owner_id = await connection.fetchval(
                        "SELECT id FROM pipeline_runs WHERE folder=$1", folder
                    )
                    owner = ("pipeline", owner_id)
                kind, owner_id = owner
                if kind == "page":
                    await connection.fetchrow(
                        "SELECT id FROM pages WHERE id=$1 FOR UPDATE", owner_id
                    )
                    structured_rows = [row for row in rows if row[0] in _PIPELINE_DOCUMENT_TYPES]
                    legacy_rows = [row for row in rows if row[0] not in _PIPELINE_DOCUMENT_TYPES]
                    await connection.execute(
                        "DELETE FROM result_documents WHERE page_id=$1 AND name=ANY($2::text[])",
                        owner_id,
                        [row[0] for row in rows],
                    )
                    for name, payload in structured_rows:
                        stage, document_type = _PIPELINE_DOCUMENT_TYPES[name]
                        revision = int(await connection.fetchval(
                            """SELECT COALESCE(MAX(revision),0)+1 FROM pipeline_documents
                               WHERE page_id=$1 AND stage=$2 AND document_type=$3""",
                            owner_id,
                            stage,
                            document_type,
                        ))
                        await connection.execute(
                            """UPDATE pipeline_documents SET active=FALSE
                               WHERE page_id=$1 AND stage=$2 AND document_type=$3 AND active""",
                            owner_id,
                            stage,
                            document_type,
                        )
                        await connection.execute(
                            """INSERT INTO pipeline_documents(
                                   page_id,stage,document_type,revision,schema_version,payload,active
                               ) VALUES($1,$2,$3,$4,1,$5::jsonb,TRUE)""",
                            owner_id,
                            stage,
                            document_type,
                            revision,
                            payload,
                        )
                    if legacy_rows:
                        await connection.executemany(
                            "INSERT INTO result_documents(page_id,name,payload,updated_at) VALUES($1,$2,$3::jsonb,now())",
                            [(owner_id, name, payload) for name, payload in legacy_rows],
                        )
                else:
                    await connection.execute(
                        "DELETE FROM result_documents WHERE pipeline_run_id=$1 AND name=ANY($2::text[])",
                        owner_id,
                        [row[0] for row in rows],
                    )
                    await connection.executemany(
                        "INSERT INTO result_documents(pipeline_run_id,name,payload,updated_at) VALUES($1,$2,$3::jsonb,now())",
                        [(owner_id, name, payload) for name, payload in rows],
                    )

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
        committed = await self.commit_pipeline_outputs(
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
        active = await self.get_pipeline_artifact(page_ref, stage_id, artifact_type)
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

    async def get_document(self, record_id: str, name: str) -> Any | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        if Path(name).name != name or not name.endswith(".json"):
            return None
        owner = await self._document_owner(record_id)
        if owner is None:
            return None
        kind, owner_id = owner
        if kind == "page" and name in _PIPELINE_DOCUMENT_TYPES:
            stage_id, document_type = _PIPELINE_DOCUMENT_TYPES[name]
            value = await self.pool.fetchval(
                """SELECT payload FROM pipeline_documents
                   WHERE page_id=$1 AND stage=$2 AND document_type=$3 AND active""",
                owner_id,
                stage_id,
                document_type,
            )
            if value is not None:
                return _json_load(value, None)
        column = "page_id" if kind == "page" else "pipeline_run_id"
        value = await self.pool.fetchval(
            f"SELECT payload FROM result_documents WHERE {column}=$1 AND name=$2",
            owner_id,
            name,
        )
        return _json_load(value, None) if value is not None else None

    async def get_documents(self, folder: str) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        owner = await self._document_owner(folder)
        if owner is None:
            return {}
        kind, owner_id = owner
        column = "page_id" if kind == "page" else "pipeline_run_id"
        rows = await self.pool.fetch(
            f"SELECT name,payload FROM result_documents WHERE {column}=$1",
            owner_id,
        )
        documents = {row["name"]: _json_load(row["payload"], None) for row in rows}
        if kind == "page":
            structured = await self.pool.fetch(
                """SELECT stage,document_type,payload FROM pipeline_documents
                   WHERE page_id=$1 AND active""",
                owner_id,
            )
            names = {
                (stage, document_type): name
                for name, (stage, document_type) in _PIPELINE_DOCUMENT_TYPES.items()
            }
            for row in structured:
                name = names.get((row["stage"], row["document_type"]))
                if name:
                    documents[name] = _json_load(row["payload"], None)
        return documents

    async def delete_documents(self, folder: str) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        owner = await self._document_owner(folder)
        if owner is None:
            return
        kind, owner_id = owner
        column = "page_id" if kind == "page" else "pipeline_run_id"
        await self.pool.execute(f"DELETE FROM result_documents WHERE {column}=$1", owner_id)
        if kind == "pipeline":
            await self.pool.execute("DELETE FROM pipeline_runs WHERE id=$1", owner_id)

    async def find_request(self, request_id: str) -> str | None:
        if self.pool is None:
            return None
        return await self.pool.fetchval(
            "SELECT folder FROM pages WHERE active AND request_id=$1 ORDER BY updated_at DESC LIMIT 1",
            request_id,
        )

    async def resolve_folder(self, record_id: str) -> str | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        row = await self.pool.fetchrow(
            "SELECT folder FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1",
            record_id,
        )
        return row["folder"] if row else None

    def _page_item(self, row: Any, slim: bool = False, include_cover: bool = False) -> dict[str, Any]:
        metadata = _json_load(row["metadata"], {})
        try:
            version = row["asset_version"]
        except (KeyError, IndexError):
            version = 0
        suffix = f"?v={version}" if version else ""
        base = f"/result/{row['id']}"
        final_name = row.get("final_name", "final.png") if hasattr(row, "get") else row["final_name"]
        group_id = (row.get("group_id") or row.get("manga_group_id")) if hasattr(row, "get") else None
        manga_title = (row.get("manga_title") or row.get("group_title", "")) if hasattr(row, "get") else ""
        page_order = row.get("page_order") if hasattr(row, "get") else row["page_order"]
        full_url = f"{base}/{final_name}{suffix}"
        item = {
            "id": row["id"],
            "legacyId": metadata.get("id"),
            "folder": row["folder"],
            "originalName": row["original_name"] if row["original_name"] and row["original_name"] != "Unknown" else f"{row['folder']}.png",
            "pageOrder": page_order,
            "sourcePath": metadata.get("sourcePath"),
            "groupId": group_id,
            "mangaTitle": manga_title,
            "resultUrl": f"{base}/{final_name}",
            "fullUrl": full_url,
            "thumbnailUrl": f"{base}/thumbnail.webp",
            "batchPreviewUrl": f"{base}/batch.webp{suffix}",
            "detailPreviewUrl": f"{base}/preview.webp{suffix}",
            "readerUrl": f"{base}/reader.webp{suffix}",
            "inputUrl": f"{base}/{row['input_name']}" if row["input_name"] else None,
            "sourceType": row["source_type"],
            "finishedAt": _iso(row["finished_at"]),
        }
        review_status = metadata.get("reviewStatus")
        has_review_flags = any(
            isinstance(region, dict) and region.get("review_required")
            for region in _json_load(row.get("text_regions"), [])
        )
        if has_review_flags:
            review_status = "pending"
        elif review_status not in {"approved", "not_required"}:
            review_status = "not_required"
        item.update({
            "reviewStatus": review_status,
            "reviewedAt": metadata.get("reviewedAt"),
            "needsReview": review_status == "pending",
        })
        if hasattr(row, "get") and row.get("series_id"):
            item["seriesId"] = row["series_id"]
            item["seriesTitle"] = row.get("series_title")
        if include_cover:
            item["coverUrl"] = f"{base}/cover.webp{suffix}"
        if not slim:
            has_bubble_mask = (self.result_root / row["folder"] / "bubble_mask.png").is_file()
            item.update(
                {
                    "inpaintedUrl": f"/result/{row['id']}/inpainted.jpg" if row["has_inpainted"] else None,
                    "textRegionsUrl": f"/result/{row['id']}/text_regions.json" if row["has_regions"] else None,
                    "bubbleMaskUrl": f"/result/{row['id']}/bubble_mask.png" if has_bubble_mask else None,
                    "hasTextRegions": bool(row["has_regions"]),
                    "settings": metadata.get("settings", {}),
                }
            )
        return item

    async def list_groups(
        self,
        limit: int = 12,
        offset: int = 0,
        manga_id: str | None = None,
        search: str | None = None,
        sort: str = "alpha-asc",
        review: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        page_size = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        clean_search = search.strip() if search and search.strip() else None
        effective_status = "review" if review == "pending" or status == "review" else (status or None)
        clean_manga_id = manga_id.strip() if manga_id and manga_id.strip() else None
        target_group_id = await self.resolve_group_id(clean_manga_id) if clean_manga_id else None
        filter_manga = target_group_id or clean_manga_id
        stats = await self.pool.fetchrow(
            """
            WITH matched_groups AS (
                SELECT id, title
                FROM manga_groups
                WHERE ($1::text IS NULL OR id=$1 OR title=$1)
                  AND ($2::text IS NULL OR title ILIKE '%' || $2 || '%')
                  AND (
                      $3::text IS NULL OR $3 = 'all'
                      OR ($3 = 'review' AND EXISTS (
                          SELECT 1 FROM pages rp
                          WHERE rp.active AND rp.manga_group_id = manga_groups.id
                            AND (rp.metadata->>'reviewStatus'='pending'
                                 OR rp.text_regions @> '[{"review_required": true}]'::jsonb)
                      ))
                      OR ($3 = 'translated' AND EXISTS (
                          SELECT 1 FROM pages tp
                          WHERE tp.active AND tp.manga_group_id = manga_groups.id
                            AND tp.source_type = 'translated'
                      ))
                      OR ($3 = 'original' AND NOT EXISTS (
                          SELECT 1 FROM pages tp
                          WHERE tp.active AND tp.manga_group_id = manga_groups.id
                            AND tp.source_type = 'translated'
                      ))
                      OR ($3 = 'summarized' AND EXISTS (
                          SELECT 1 FROM manga_summaries ms
                          WHERE ms.group_id = manga_groups.id
                            AND NULLIF(ms.payload->>'summary', '') IS NOT NULL
                      ))
                  )
            ), grouped AS (
                SELECT mg.id, mg.title, count(*) AS page_count
                FROM matched_groups mg
                JOIN pages p ON p.active AND p.manga_group_id = mg.id
                    AND ($3::text IS NULL OR $3::text != 'review' OR p.metadata->>'reviewStatus'='pending'
                         OR p.text_regions @> '[{"review_required": true}]'::jsonb)
                GROUP BY mg.id, mg.title
            )
            SELECT count(*) AS total_groups, COALESCE(sum(page_count), 0) AS total_images
            FROM grouped
            """,
            filter_manga,
            clean_search,
            effective_status,
        )
        order_by = {
            "alpha-asc": "lower(g.title), g.title",
            "alpha-desc": "lower(g.title) DESC, g.title DESC",
            "date-asc": "grouped.latest_finished_at, lower(g.title), g.title",
            "date-desc": "grouped.latest_finished_at DESC, lower(g.title), g.title",
        }.get(sort, "lower(g.title), g.title")
        rows = await self.pool.fetch(
            f"""
            WITH matched_groups AS (
                SELECT id, title
                FROM manga_groups
                WHERE ($3::text IS NULL OR id=$3 OR title=$3)
                  AND ($4::text IS NULL OR title ILIKE '%' || $4 || '%')
                  AND (
                      $5::text IS NULL OR $5 = 'all'
                      OR ($5 = 'review' AND EXISTS (
                          SELECT 1 FROM pages rp
                          WHERE rp.active AND rp.manga_group_id = manga_groups.id
                            AND (rp.metadata->>'reviewStatus'='pending'
                                 OR rp.text_regions @> '[{{"review_required": true}}]'::jsonb)
                      ))
                      OR ($5 = 'translated' AND EXISTS (
                          SELECT 1 FROM pages tp
                          WHERE tp.active AND tp.manga_group_id = manga_groups.id
                            AND tp.source_type = 'translated'
                      ))
                      OR ($5 = 'original' AND NOT EXISTS (
                          SELECT 1 FROM pages tp
                          WHERE tp.active AND tp.manga_group_id = manga_groups.id
                            AND tp.source_type = 'translated'
                      ))
                      OR ($5 = 'summarized' AND EXISTS (
                          SELECT 1 FROM manga_summaries ms
                          WHERE ms.group_id = manga_groups.id
                            AND NULLIF(ms.payload->>'summary', '') IS NOT NULL
                      ))
                  )
            ), grouped AS (
                SELECT mg.id AS group_id, mg.title AS manga_title,
                       count(*) AS page_count,
                       count(*) FILTER (WHERE $5::text='review'
                         OR p.metadata->>'reviewStatus'='pending'
                         OR p.text_regions @> '[{{"review_required": true}}]'::jsonb) AS review_count,
                       max(p.finished_at) AS latest_finished_at
                FROM matched_groups mg
                JOIN pages p ON p.active AND p.manga_group_id = mg.id
                    AND ($5::text IS NULL OR $5::text != 'review' OR p.metadata->>'reviewStatus'='pending'
                         OR p.text_regions @> '[{{"review_required": true}}]'::jsonb)
                GROUP BY mg.id, mg.title
            )
            SELECT cover.*, grouped.page_count, grouped.review_count, grouped.latest_finished_at,
                   g.id AS group_id, g.title AS manga_title, g.series_id, s.title AS series_title,
                   EXISTS (
                       SELECT 1 FROM manga_summaries ms
                       WHERE ms.group_id=g.id AND NULLIF(ms.payload->>'summary', '') IS NOT NULL
                   ) AS has_summary
            FROM grouped
            JOIN manga_groups g ON g.id = grouped.group_id
            LEFT JOIN manga_series s ON s.id = g.series_id
            JOIN LATERAL (
                SELECT p.*
                FROM pages p
                WHERE p.active AND p.manga_group_id = grouped.group_id
                ORDER BY p.page_order, p.folder
                LIMIT 1
            ) cover ON TRUE
            ORDER BY CASE WHEN g.title='Ungrouped' THEN 1 ELSE 0 END,
                     {order_by}
            LIMIT $1 OFFSET $2
            """,
            page_size,
            offset,
            filter_manga,
            clean_search,
            effective_status,
        )
        groups = []
        for row in rows:
            count = int(row["page_count"])
            title = row["manga_title"]
            groups.append(
                {
                    "id": row["group_id"],
                    "title": title,
                    "count": count,
                    "needsReviewCount": int(row.get("review_count") or 0),
                    "cover": self._page_item(row, include_cover=True),
                    "latestFinishedAt": _iso(row["latest_finished_at"]),
                    "seriesId": row.get("series_id") if hasattr(row, "get") else None,
                    "seriesTitle": row.get("series_title") if hasattr(row, "get") else None,
                    "hasSummary": bool(row.get("has_summary")),
                }
            )
        total_groups = int(stats["total_groups"]) if stats else 0
        next_offset = offset + len(groups) if offset + len(groups) < total_groups else None
        return {
            "groups": groups,
            "totalGroups": total_groups,
            "totalImages": int(stats["total_images"]) if stats else 0,
            "nextOffset": next_offset,
        }

    def _series_member_item(self, row: Any) -> dict[str, Any]:
        cover = None
        if row.get("id") is not None:
            cover = self._page_item(row, include_cover=True)
        return {
            "id": row["group_id"],
            "title": row["group_title"],
            "position": int(row["series_position"]),
            "count": int(row["page_count"]),
            "latestFinishedAt": _iso(row["latest_finished_at"]),
            "cover": cover,
        }

    async def _series_members(self, series_id: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            WITH group_stats AS (
                SELECT g.id AS group_id, g.title AS group_title,
                       g.series_position, count(p.id) AS page_count,
                       max(p.finished_at) AS latest_finished_at
                FROM manga_groups g
                LEFT JOIN pages p ON p.active AND p.manga_group_id = g.id
                WHERE g.series_id = $1
                GROUP BY g.id, g.title, g.series_position
            )
            SELECT stats.*, cover.*
            FROM group_stats stats
            LEFT JOIN LATERAL (
                SELECT p.*
                FROM pages p
                WHERE p.active AND p.manga_group_id = stats.group_id
                ORDER BY p.page_order, p.folder
                LIMIT 1
            ) cover ON TRUE
            WHERE stats.page_count > 0
            ORDER BY stats.series_position, stats.group_id
            """,
            series_id,
        )
        return [self._series_member_item(row) for row in rows]

    async def list_series(
        self,
        limit: int = 12,
        offset: int = 0,
        search: str | None = None,
    ) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        page_size = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        clean_search = search.strip() if search and search.strip() else None
        total = await self.pool.fetchval(
            """
            WITH visible_groups AS (
                SELECT g.id AS group_id, g.series_id
                FROM manga_groups g
                WHERE g.series_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM pages p
                      WHERE p.active AND p.manga_group_id = g.id
                  )
            ), visible_series AS (
                SELECT s.id
                FROM manga_series s
                JOIN visible_groups g ON g.series_id = s.id
                WHERE ($1::text IS NULL OR s.title ILIKE '%' || $1 || '%')
                GROUP BY s.id
                HAVING count(g.group_id) >= 2
            )
            SELECT count(*) FROM visible_series
            """,
            clean_search,
        )
        rows = await self.pool.fetch(
            """
            WITH visible_groups AS (
                SELECT g.id AS group_id, g.series_id, g.series_position, g.title AS group_title
                FROM manga_groups g
                WHERE g.series_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM pages p
                      WHERE p.active AND p.manga_group_id = g.id
                  )
            ), visible_series AS (
                SELECT s.id, s.title, s.updated_at, count(g.group_id) AS member_count
                FROM manga_series s
                JOIN visible_groups g ON g.series_id = s.id
                WHERE ($1::text IS NULL OR s.title ILIKE '%' || $1 || '%')
                GROUP BY s.id, s.title, s.updated_at
                HAVING count(g.group_id) >= 2
            )
            SELECT s.id AS series_id, s.title, s.updated_at AS series_updated_at,
                   s.member_count,
                   cover.*
            FROM visible_series s
            LEFT JOIN LATERAL (
                SELECT g.group_id AS cover_group_id, g.group_title AS cover_group_title,
                       g.series_position, p.*
                FROM visible_groups g
                JOIN LATERAL (
                    SELECT p.*
                    FROM pages p
                    WHERE p.active AND p.manga_group_id = g.group_id
                    ORDER BY p.page_order, p.folder
                    LIMIT 1
                ) p ON TRUE
                WHERE g.series_id = s.id
                ORDER BY g.series_position, g.group_id
                LIMIT 1
            ) cover ON TRUE
            ORDER BY lower(s.title), s.title, s.id
            LIMIT $2 OFFSET $3
            """,
            clean_search,
            page_size,
            offset,
        )
        series = []
        for row in rows:
            cover = self._page_item(row, include_cover=True) if row.get("id") else None
            series.append(
                {
                    "id": row["series_id"],
                    "title": row["title"],
                    "memberCount": int(row["member_count"]),
                    "cover": cover,
                    "firstGroupId": row.get("cover_group_id") or (cover.get("groupId") if cover else None),
                    "updatedAt": _iso(row["series_updated_at"]),
                }
            )
        total_series = int(total or 0)
        next_offset = offset + len(series) if offset + len(series) < total_series else None
        return {
            "series": series,
            "totalSeries": total_series,
            "nextOffset": next_offset,
        }

    async def get_series(self, series_id: str) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        row = await self.pool.fetchrow(
            "SELECT id, title, created_at, updated_at FROM manga_series WHERE id=$1",
            series_id,
        )
        if row is None:
            raise SeriesNotFound("Series not found")
        members = await self._series_members(series_id)
        return {
            "id": row["id"],
            "title": row["title"],
            "members": members,
            "memberCount": len(members),
            "cover": members[0]["cover"] if members else None,
            "firstGroupId": members[0]["id"] if members else None,
            "updatedAt": _iso(row["updated_at"]),
        }

    async def get_series_for_group(self, group_id: str) -> dict[str, Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        series_id = await self.pool.fetchval(
            "SELECT series_id FROM manga_groups WHERE id=$1 OR title=$1 LIMIT 1",
            group_id,
        )
        return await self.get_series(series_id) if series_id else None

    @staticmethod
    def _clean_series_title(title: str) -> str:
        clean_title = (title or "").strip()
        if not clean_title:
            raise InvalidSeries("A series title is required")
        return clean_title

    @staticmethod
    def _validate_group_ids(group_ids: list[str]) -> list[str]:
        if len(group_ids) < 2:
            raise InvalidSeries("A series needs at least two manga groups")
        if any(not isinstance(group_id, str) or not group_id.strip() for group_id in group_ids):
            raise InvalidSeries("Invalid manga group ID")
        if len(set(group_ids)) != len(group_ids):
            raise InvalidSeries("Duplicate manga group ID")
        return group_ids

    async def create_series(self, title: str, group_ids: list[str]) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        clean_title = self._clean_series_title(title)
        group_ids = self._validate_group_ids(group_ids)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                duplicate = await connection.fetchval(
                    "SELECT id FROM manga_series WHERE lower(btrim(title))=lower(btrim($1))",
                    clean_title,
                )
                if duplicate:
                    raise SeriesConflict("A series with this title already exists")
                groups = await connection.fetch(
                    """
                    SELECT g.id, g.title, g.series_id
                    FROM manga_groups g
                    WHERE g.id=ANY($1::text[])
                      AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
                    FOR UPDATE
                    """,
                    group_ids,
                )
                if len(groups) != len(group_ids):
                    raise InvalidSeries("One or more manga groups were not found")
                if any(row["series_id"] for row in groups):
                    raise SeriesConflict("A manga group already belongs to a series")
                ordered = sorted(groups, key=lambda row: (_natural_sort_key(row["title"]), row["id"]))
                series_id = str(uuid.uuid4())
                await connection.execute(
                    "INSERT INTO manga_series(id,title) VALUES($1,$2)",
                    series_id,
                    clean_title,
                )
                await connection.executemany(
                    """
                    UPDATE manga_groups
                    SET series_id=$1, series_position=$2, updated_at=now()
                    WHERE id=$3
                    """,
                    [(series_id, index, row["id"]) for index, row in enumerate(ordered, start=1)],
                )
        return await self.get_series(series_id)

    async def update_series_title(self, series_id: str, title: str) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        clean_title = self._clean_series_title(title)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                exists = await connection.fetchval(
                    "SELECT 1 FROM manga_series WHERE id=$1 FOR UPDATE", series_id
                )
                if not exists:
                    raise SeriesNotFound("Series not found")
                duplicate = await connection.fetchval(
                    """
                    SELECT 1 FROM manga_series
                    WHERE id<>$1 AND lower(btrim(title))=lower(btrim($2))
                    """,
                    series_id,
                    clean_title,
                )
                if duplicate:
                    raise SeriesConflict("A series with this title already exists")
                await connection.execute(
                    "UPDATE manga_series SET title=$2, updated_at=now() WHERE id=$1",
                    series_id,
                    clean_title,
                )
        return await self.get_series(series_id)

    async def replace_series_members(self, series_id: str, group_ids: list[str]) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_ids = self._validate_group_ids(group_ids)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if not await connection.fetchval(
                    "SELECT 1 FROM manga_series WHERE id=$1 FOR UPDATE", series_id
                ):
                    raise SeriesNotFound("Series not found")
                await connection.fetch(
                    "SELECT id FROM manga_groups WHERE series_id=$1 FOR UPDATE", series_id
                )
                groups = await connection.fetch(
                    """
                    SELECT g.id, g.series_id
                    FROM manga_groups g
                    WHERE g.id=ANY($1::text[])
                      AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
                    FOR UPDATE
                    """,
                    group_ids,
                )
                if len(groups) != len(group_ids):
                    raise InvalidSeries("One or more manga groups were not found")
                if any(row["series_id"] not in (None, series_id) for row in groups):
                    raise SeriesConflict("A manga group already belongs to another series")
                await connection.execute(
                    """
                    UPDATE manga_groups
                    SET series_id=NULL, series_position=NULL, updated_at=now()
                    WHERE series_id=$1
                    """,
                    series_id,
                )
                await connection.executemany(
                    """
                    UPDATE manga_groups
                    SET series_id=$1, series_position=$2, updated_at=now()
                    WHERE id=$3
                    """,
                    [(series_id, index, group_id) for index, group_id in enumerate(group_ids, start=1)],
                )
                await connection.execute(
                    "UPDATE manga_series SET updated_at=now() WHERE id=$1", series_id
                )
        return await self.get_series(series_id)

    async def delete_series(self, series_id: str) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if not await connection.fetchval(
                    "SELECT 1 FROM manga_series WHERE id=$1 FOR UPDATE", series_id
                ):
                    raise SeriesNotFound("Series not found")
                await connection.execute(
                    "UPDATE manga_groups SET series_id=NULL, series_position=NULL, updated_at=now() WHERE series_id=$1",
                    series_id,
                )
                await connection.execute("DELETE FROM manga_series WHERE id=$1", series_id)

    async def add_manga_to_series(self, series_id: str, group_ids: list[str]) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        if not group_ids:
            raise InvalidSeries("At least one manga group is required")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if not await connection.fetchval(
                    "SELECT 1 FROM manga_series WHERE id=$1 FOR UPDATE", series_id
                ):
                    raise SeriesNotFound("Series not found")

                target_groups = await connection.fetch(
                    """
                    SELECT g.id, g.series_id, g.title
                    FROM manga_groups g
                    WHERE g.id = ANY($1::text[])
                      AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id = g.id)
                    FOR UPDATE
                    """,
                    group_ids,
                )
                if len(target_groups) != len(set(group_ids)):
                    raise InvalidSeries("One or more manga groups were not found")

                affected_prev_series = set()
                for row in target_groups:
                    prev_id = row["series_id"]
                    if prev_id and prev_id != series_id:
                        affected_prev_series.add(prev_id)

                existing_members = await connection.fetch(
                    """
                    SELECT id FROM manga_groups
                    WHERE series_id = $1
                    ORDER BY series_position, id
                    FOR UPDATE
                    """,
                    series_id,
                )
                existing_member_ids = [row["id"] for row in existing_members]

                await connection.execute(
                    """
                    UPDATE manga_groups
                    SET series_id = NULL, series_position = NULL, updated_at = now()
                    WHERE id = ANY($1::text[])
                    """,
                    group_ids,
                )

                for prev_id in affected_prev_series:
                    remaining = await connection.fetch(
                        """
                        SELECT id FROM manga_groups
                        WHERE series_id = $1
                        ORDER BY series_position, id
                        FOR UPDATE
                        """,
                        prev_id,
                    )
                    if len(remaining) < 2:
                        await connection.execute(
                            """
                            UPDATE manga_groups
                            SET series_id = NULL, series_position = NULL, updated_at = now()
                            WHERE series_id = $1
                            """,
                            prev_id,
                        )
                        await connection.execute("DELETE FROM manga_series WHERE id = $1", prev_id)
                    else:
                        await connection.executemany(
                            """
                            UPDATE manga_groups
                            SET series_position = $2, updated_at = now()
                            WHERE id = $1
                            """,
                            [(row["id"], idx) for idx, row in enumerate(remaining, start=1)],
                        )
                        await connection.execute("UPDATE manga_series SET updated_at = now() WHERE id = $1", prev_id)

                final_member_ids = [m_id for m_id in existing_member_ids if m_id not in group_ids]
                for g_id in group_ids:
                    if g_id not in final_member_ids:
                        final_member_ids.append(g_id)

                if len(final_member_ids) < 2:
                    raise InvalidSeries("A series needs at least two manga groups")

                await connection.executemany(
                    """
                    UPDATE manga_groups
                    SET series_id = $1, series_position = $2, updated_at = now()
                    WHERE id = $3
                    """,
                    [(series_id, idx, g_id) for idx, g_id in enumerate(final_member_ids, start=1)],
                )
                await connection.execute("UPDATE manga_series SET updated_at = now() WHERE id = $1", series_id)

        return await self.get_series(series_id)

    async def move_manga_to_series(self, group_id: str, target_series_id: str) -> dict[str, Any]:
        return await self.add_manga_to_series(target_series_id, [group_id])

    async def remove_manga_from_series(self, group_id: str) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT series_id FROM manga_groups WHERE id = $1 FOR UPDATE", group_id
                )
                if not row or not row["series_id"]:
                    return
                series_id = row["series_id"]

                await connection.execute(
                    "UPDATE manga_groups SET series_id = NULL, series_position = NULL, updated_at = now() WHERE id = $1",
                    group_id,
                )

                remaining = await connection.fetch(
                    "SELECT id FROM manga_groups WHERE series_id = $1 ORDER BY series_position, id FOR UPDATE",
                    series_id,
                )
                if len(remaining) < 2:
                    await connection.execute(
                        "UPDATE manga_groups SET series_id = NULL, series_position = NULL, updated_at = now() WHERE series_id = $1",
                        series_id,
                    )
                    await connection.execute("DELETE FROM manga_series WHERE id = $1", series_id)
                else:
                    await connection.executemany(
                        "UPDATE manga_groups SET series_position = $2, updated_at = now() WHERE id = $1",
                        [(r["id"], idx) for idx, r in enumerate(remaining, start=1)],
                    )
                    await connection.execute("UPDATE manga_series SET updated_at = now() WHERE id = $1", series_id)

    async def group_exists(self, title: str) -> bool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(title)
        return bool(
            group_id
            and await self.pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pages WHERE active AND manga_group_id=$1)",
                group_id,
            )
        )

    async def resolve_group_title(self, record_id: str) -> str | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(record_id)
        row = await self.pool.fetchrow("SELECT title FROM manga_groups WHERE id=$1", group_id) if group_id else None
        return row["title"] if row else None

    async def get_summary_payload(self, group_value: str) -> dict[str, Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(group_value)
        if group_id is None:
            return None
        value = await self.pool.fetchval(
            "SELECT payload FROM manga_summaries WHERE group_id=$1", group_id
        )
        payload = _json_load(value, None)
        return payload if isinstance(payload, dict) else None

    async def save_summary_payload(self, group_value: str, payload: dict[str, Any]) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(group_value)
        if group_id is None:
            raise GroupNotFound("Manga group not found")
        await self.pool.execute(
            """
            INSERT INTO manga_summaries(id,group_id,payload,updated_at)
            VALUES($1,$2,$3::jsonb,now())
            ON CONFLICT(group_id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=now()
            """,
            str(uuid.uuid4()),
            group_id,
            _json_dump(payload),
        )

    async def summary_status(self, group_value: str) -> dict[str, Any] | None:
        group_id = await self.resolve_group_id(group_value)
        if group_id is None:
            return None
        title = await self.resolve_group_title(group_id)
        pages = await self.group_pages(group_id)
        saved = await self.get_summary_payload(group_id)
        status = synopsis_status(self.result_root, title or "Ungrouped", pages, saved=saved)
        status["groupId"] = group_id
        return status

    async def update_summary_job(
        self,
        group_value: str,
        status: str,
        error: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        current_page: int | None = None,
        page_count: int | None = None,
        pages_with_text: int | None = None,
        extraction_required: bool | None = None,
        provider: str | None = None,
        model: str | None = None,
        refresh_text: bool | None = None,
        regenerate: bool | None = None,
    ) -> None:
        group_id = await self.resolve_group_id(group_value)
        if group_id is None:
            raise GroupNotFound("Manga group not found")
        title = await self.resolve_group_title(group_id) or "Ungrouped"
        value = await self.get_summary_payload(group_id) or {}
        value.update(
            {
                "mangaTitle": title,
                "jobStatus": status,
                "jobError": error,
                "jobUpdatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        )
        if status in {"queued", "generating", "paused"}:
            value["jobDismissed"] = False
        if stage is not None:
            value["jobStage"] = stage
        if progress is not None:
            value["jobProgress"] = max(0, min(100, progress))
        if message is not None:
            value["jobMessage"] = message
        if current_page is not None:
            value["jobCurrentPage"] = max(0, current_page)
        if page_count is not None:
            value["jobPageCount"] = max(0, page_count)
        if pages_with_text is not None:
            value["jobPagesWithText"] = max(0, pages_with_text)
        if extraction_required is not None:
            value["jobExtractionRequired"] = extraction_required
        if provider is not None:
            value["provider"] = provider
        if model is not None:
            value["model"] = model
        if refresh_text is not None:
            value["jobRefreshText"] = refresh_text
        if regenerate is not None:
            value["jobRegenerate"] = regenerate
        await self.save_summary_payload(group_id, value)

    async def dismiss_summary_job(self, group_value: str) -> None:
        group_id = await self.resolve_group_id(group_value)
        if group_id is None:
            raise GroupNotFound("Manga group not found")
        value = await self.get_summary_payload(group_id) or {}
        value["jobDismissed"] = True
        await self.save_summary_payload(group_id, value)

    async def reconcile_summary_jobs(self) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        rows = await self.pool.fetch(
            """
            SELECT group_id, payload
            FROM manga_summaries
            WHERE payload->>'jobStatus' IN ('generating', 'queued')
            """
        )
        for row in rows:
            payload = _json_load(row["payload"], {})
            if not isinstance(payload, dict):
                continue
            status = payload.get("jobStatus")
            if status == "generating":
                if payload.get("summary"):
                    payload["jobStatus"] = "ready"
                    payload["jobStage"] = "complete"
                    payload["jobProgress"] = 100
                    payload["jobMessage"] = None
                else:
                    payload["jobStatus"] = "queued"
                    payload["jobProgress"] = 0
                    payload["jobMessage"] = "Waiting for an available worker"
                    payload["jobStage"] = (
                        "detecting" if payload.get("jobExtractionRequired", True) else "concatenating"
                    )
                payload["jobUpdatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
                await self.save_summary_payload(str(row["group_id"]), payload)
            elif status == "queued":
                if payload.get("summary") and not payload.get("jobRegenerate") and not payload.get("jobRefreshText"):
                    payload["jobStatus"] = "ready"
                    payload["jobStage"] = "complete"
                    payload["jobProgress"] = 100
                    payload["jobMessage"] = None
                    payload["jobUpdatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
                    await self.save_summary_payload(str(row["group_id"]), payload)

    async def list_runnable_summary_jobs(self) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        rows = await self.pool.fetch(
            """
            SELECT ms.group_id, g.title, ms.payload
            FROM manga_summaries ms
            JOIN manga_groups g ON g.id = ms.group_id
            WHERE COALESCE(ms.payload->>'jobDismissed', 'false') <> 'true'
              AND ms.payload->>'jobStatus' = 'queued'
            ORDER BY ms.updated_at ASC
            """
        )
        runnable: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_load(row["payload"], {})
            if not isinstance(payload, dict):
                continue
            if payload.get("jobStatus") != "queued":
                continue
            if payload.get("summary") and not payload.get("jobRegenerate") and not payload.get("jobRefreshText"):
                continue
            record = {
                "id": f"summary:{row['group_id']}",
                "kind": "summary",
                "groupId": str(row["group_id"]),
                "title": row["title"] or payload.get("mangaTitle") or "Ungrouped",
                "status": "queued",
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "updatedAt": payload.get("jobUpdatedAt") or payload.get("generatedAt"),
                "jobStage": payload.get("jobStage"),
                "jobProgress": payload.get("jobProgress"),
                "jobMessage": payload.get("jobMessage"),
                "jobError": payload.get("jobError"),
                "jobCurrentPage": payload.get("jobCurrentPage"),
                "jobPageCount": payload.get("jobPageCount"),
                "jobPagesWithText": payload.get("jobPagesWithText"),
                "jobExtractionRequired": payload.get("jobExtractionRequired"),
                "jobRefreshText": bool(payload.get("jobRefreshText", False)),
                "jobRegenerate": bool(payload.get("jobRegenerate", False)),
                "summaryAvailable": bool(payload.get("summary")),
            }
            runnable.append(record)
        return runnable

    async def list_summary_jobs(self, completed_limit: int = 20) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        rows = await self.pool.fetch(
            """
            SELECT ms.group_id, g.title, ms.payload
            FROM manga_summaries ms
            JOIN manga_groups g ON g.id = ms.group_id
            WHERE COALESCE(ms.payload->>'jobDismissed', 'false') <> 'true'
              AND (
                ms.payload->>'jobStatus' IN ('queued', 'generating', 'paused', 'error')
                OR NULLIF(ms.payload->>'summary', '') IS NOT NULL
              )
            """
        )
        active: list[dict[str, Any]] = []
        completed: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_load(row["payload"], {})
            if not isinstance(payload, dict):
                continue
            status = payload.get("jobStatus")
            if status is None and payload.get("summary"):
                status = "ready"
            if status not in {"queued", "generating", "paused", "ready", "error"}:
                continue
            record = {
                "id": f"summary:{row['group_id']}",
                "kind": "summary",
                "groupId": str(row["group_id"]),
                "title": row["title"] or payload.get("mangaTitle") or "Ungrouped",
                "status": status,
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "updatedAt": payload.get("jobUpdatedAt") or payload.get("generatedAt"),
                "jobStage": payload.get("jobStage"),
                "jobProgress": payload.get("jobProgress"),
                "jobMessage": payload.get("jobMessage"),
                "jobError": payload.get("jobError"),
                "jobCurrentPage": payload.get("jobCurrentPage"),
                "jobPageCount": payload.get("jobPageCount"),
                "jobPagesWithText": payload.get("jobPagesWithText"),
                "jobExtractionRequired": payload.get("jobExtractionRequired"),
                "jobRefreshText": bool(payload.get("jobRefreshText", False)),
                "jobRegenerate": bool(payload.get("jobRegenerate", False)),
                "summaryAvailable": bool(payload.get("summary")),
            }
            (completed if status == "ready" else active).append(record)

        active.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        completed.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return active + completed[:max(0, completed_limit)]

    async def list_results(
        self,
        sort: str = "alpha",
        manga: str | None = None,
        detail: str | None = None,
        limit: int = 100,
        offset: int = 0,
        review: str | None = None,
    ) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        order_by = "p.page_order ASC, p.folder ASC"
        clean_manga = manga.strip() if manga is not None else None
        group_id = await self.resolve_group_id(clean_manga) if clean_manga else None
        if clean_manga and group_id is None:
            return {"directories": [], "items": [], "total": 0, "nextOffset": None}
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 500))
        where = "p.active AND ($1::text IS NULL OR p.manga_group_id=$1) AND ($2::text IS NULL OR p.metadata->>'reviewStatus'='pending' OR p.text_regions @> '[{\"review_required\": true}]'::jsonb)"
        total = await self.pool.fetchval(f"SELECT count(*) FROM pages p WHERE {where}", group_id, review)
        rows = await self.pool.fetch(
            f"""
            SELECT p.*, g.id AS group_id, g.title AS manga_title, g.series_id, s.title AS series_title
            FROM pages p
            JOIN manga_groups g ON g.id = p.manga_group_id
            LEFT JOIN manga_series s ON s.id = g.series_id
            WHERE {where}
            ORDER BY {order_by}
            LIMIT $3 OFFSET $4
            """,
            group_id,
            review,
            limit,
            offset,
        )
        slim = detail in {"reader", "slim"}
        items = [self._page_item(row, slim=slim) for row in rows]
        next_offset = offset + len(items) if offset + len(items) < int(total) else None
        return {
            "directories": [item["folder"] for item in items],
            "items": items,
            "total": int(total),
            "nextOffset": next_offset,
        }

    async def page_detail(self, record_id: str) -> dict[str, Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        row = await self.pool.fetchrow(
            """
            SELECT p.*, g.id AS group_id, g.title AS manga_title, g.series_id, s.title AS series_title
            FROM pages p
            JOIN manga_groups g ON g.id = p.manga_group_id
            LEFT JOIN manga_series s ON s.id = g.series_id
            WHERE p.active AND (p.id=$1 OR p.folder=$1)
            LIMIT 1
            """,
            record_id,
        )
        return self._page_item(row) if row else None

    async def group_pages(self, title: str) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(title)
        if group_id is None:
            return []
        rows = await self.pool.fetch(
            """SELECT p.*, g.id AS group_id, g.title AS group_title
               FROM pages p
               JOIN manga_groups g ON g.id=p.manga_group_id
               WHERE p.active AND p.manga_group_id=$1
               ORDER BY p.page_order, p.original_sort_key, p.folder""",
            group_id,
        )
        pages = []
        for row in rows:
            metadata = _json_load(row["metadata"], {})
            metadata.update({
                "mangaTitle": row["group_title"],
                "mangaGroupId": row["group_id"],
                "groupId": row["group_id"],
                "pageOrder": row["page_order"],
                "sourceType": row["source_type"],
            })
            pages.append({
                "id": row["id"],
                "folder": row["folder"],
                "path": self.result_root / row["folder"],
                "name": row["original_name"] if row["original_name"] and row["original_name"] != "Unknown" else f"{row['folder']}.png",
                "meta": metadata,
                "groupId": row["group_id"],
                "mangaTitle": row["group_title"],
                "sourceType": row["source_type"],
                "hasRegions": bool(row["has_regions"]),
                "textRegions": _json_load(row["text_regions"], []),
                "pageOrder": row["page_order"],
            })
        return pages

    async def export_pages(
        self, title: str, folders: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(title)
        if group_id is None:
            return []
        if folders:
            record_values = [str(folder) for folder in folders]
            rows = await self.pool.fetch(
                """
                SELECT * FROM pages
                WHERE active AND manga_group_id=$1
                  AND (id=ANY($2::text[]) OR folder=ANY($2::text[]))
                ORDER BY page_order, original_sort_key, folder
                """,
                group_id,
                record_values,
            )
        else:
            rows = await self.pool.fetch(
                "SELECT * FROM pages WHERE active AND manga_group_id=$1 ORDER BY page_order, original_sort_key, folder",
                group_id,
            )
        pages = []
        for row in rows:
            page_root = self.result_root / row["folder"]
            source = page_root / row["input_name"] if row["source_type"] == "original" and row["input_name"] else final_file(page_root)
            if source is not None and source.is_file():
                pages.append({
                    "originalName": row["original_name"] if row["original_name"] and row["original_name"] != "Unknown" else f"{row['folder']}.png",
                    "pageOrder": row["page_order"],
                    "path": str(source),
                })
        return pages

    async def reorder_pages(self, group_id: str, page_ids: list[str]) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        if not page_ids or len(set(page_ids)) != len(page_ids):
            raise ValueError("pageIds must contain every page exactly once")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if await connection.fetchval(
                    "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                ) is None:
                    raise GroupNotFound("Manga group not found")
                rows = await connection.fetch(
                    """
                    SELECT id
                    FROM pages
                    WHERE active AND manga_group_id=$1
                    FOR UPDATE
                    """,
                    group_id,
                )
                expected = {row["id"] for row in rows}
                if expected != set(page_ids):
                    raise ValueError("pageIds must contain every active page in the manga group")
                await connection.execute(
                    """
                    WITH ordered AS (
                        SELECT id, ROW_NUMBER() OVER (ORDER BY page_order, folder)::INTEGER AS position
                        FROM pages
                        WHERE active AND manga_group_id=$1
                    )
                    UPDATE pages AS p
                    SET page_order=-ordered.position
                    FROM ordered
                    WHERE p.id=ordered.id
                    """,
                    group_id,
                )
                await connection.executemany(
                    "UPDATE pages SET page_order=$2, updated_at=now() WHERE id=$1",
                    [(page_id, index) for index, page_id in enumerate(page_ids, start=1)],
                )
                return [
                    {"id": page_id, "pageOrder": index}
                    for index, page_id in enumerate(page_ids, start=1)
                ]

    async def compact_page_order(self, group_id: str) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.fetchrow(
                    "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                )
                await self._compact_page_order(connection, group_id)

    @staticmethod
    async def _compact_page_order(connection: Any, group_id: str) -> None:
        pages = await connection.fetch(
            """
            SELECT id
            FROM pages
            WHERE active AND manga_group_id=$1
            ORDER BY page_order, folder
            FOR UPDATE
            """,
            group_id,
        )
        if not pages:
            return
        reserved_rows = await connection.fetch(
            """
            SELECT DISTINCT bi.page_order
            FROM batch_items bi
            JOIN batches b ON b.id=bi.batch_id
            WHERE bi.manga_group_id=$1
              AND bi.page_order IS NOT NULL
              AND bi.page_order > 0
              AND b.active AND NOT b.dismissed
              AND bi.status IN ('queued', 'processing', 'error')
            """,
            group_id,
        )
        reserved = {int(row["page_order"]) for row in reserved_rows}
        next_order = 1
        assignments: list[tuple[str, int]] = []
        for page in pages:
            while next_order in reserved:
                next_order += 1
            assignments.append((page["id"], next_order))
            next_order += 1

        await connection.executemany(
            "UPDATE pages SET page_order=$2, updated_at=now() WHERE id=$1",
            [(page_id, -index) for index, (page_id, _) in enumerate(assignments, start=1)],
        )
        await connection.executemany(
            "UPDATE pages SET page_order=$2, updated_at=now() WHERE id=$1",
            assignments,
        )

    @staticmethod
    async def _next_page_order(connection: Any, group_id: str) -> int:
        return int(
            await connection.fetchval(
                """
                SELECT GREATEST(
                    COALESCE((
                        SELECT MAX(page_order)
                        FROM pages
                        WHERE active AND manga_group_id=$1
                    ), 0),
                    COALESCE((
                        SELECT MAX(bi.page_order)
                        FROM batch_items bi
                        JOIN batches b ON b.id=bi.batch_id
                        WHERE bi.manga_group_id=$1
                          AND bi.page_order IS NOT NULL
                          AND b.active AND NOT b.dismissed
                          AND bi.status IN ('queued', 'processing', 'error')
                    ), 0)
                ) + 1
                """,
                group_id,
            )
        )

    async def update_meta(
        self,
        folders: list[str] | None,
        old_title: str | None,
        new_title: str,
    ) -> int:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        clean_title = new_title.strip() or "Ungrouped"
        old_group_id = await self.resolve_group_id(old_title) if old_title is not None else None
        record_values = [str(folder) for folder in (folders or [])]
        condition = []
        args: list[Any] = []
        if record_values:
            placeholder = len(args) + 1
            condition.append(f"(id = ANY(${placeholder}::text[]) OR folder = ANY(${placeholder}::text[]))")
            args.append(record_values)
        if old_title is not None:
            if old_group_id is None:
                return 0
            condition.append(f"manga_group_id=${len(args) + 1}")
            args.append(old_group_id)
        if not condition:
            return 0
        rows = await self.pool.fetch(
            f"SELECT id, manga_group_id, page_order, metadata FROM pages WHERE active AND ({' OR '.join(condition)})",
            *args,
        )
        if not rows:
            return 0
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                target_group_id = await connection.fetchval(
                    "SELECT id FROM manga_groups WHERE title=$1", clean_title
                )
                if target_group_id is None:
                    target_group_id = await connection.fetchval(
                        "INSERT INTO manga_groups(id,title) VALUES($1,$2) RETURNING id",
                        str(uuid.uuid4()),
                        clean_title,
                    )
                source_group_ids = {row["manga_group_id"] for row in rows}
                for locked_group_id in sorted(source_group_ids | {target_group_id}):
                    await connection.fetchrow(
                        "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", locked_group_id
                    )
                moving_rows = sorted(
                    (row for row in rows if row["manga_group_id"] != target_group_id),
                    key=lambda row: (row["page_order"], row["id"]),
                )
                moving_ids = {row["id"] for row in moving_rows}
                next_order = await self._next_page_order(connection, target_group_id)
                update_rows = []
                for row in sorted(
                    rows,
                    key=lambda row: (
                        row["manga_group_id"] == target_group_id,
                        row["page_order"],
                        row["id"],
                    ),
                ):
                    metadata = _json_load(row["metadata"], {})
                    metadata["mangaTitle"] = clean_title
                    metadata["mangaGroupId"] = target_group_id
                    metadata["groupId"] = target_group_id
                    assigned_order = next_order if row["id"] in moving_ids else row["page_order"]
                    if row["id"] in moving_ids:
                        next_order += 1
                    update_rows.append(
                        (target_group_id, assigned_order, _json_dump(metadata), row["id"])
                    )
                await connection.executemany(
                    """
                    UPDATE pages SET manga_group_id=$1, page_order=$2, metadata=$3::jsonb,
                        updated_at=now() WHERE id=$4
                    """,
                    update_rows,
                )
                for source_group_id in sorted(source_group_ids - {target_group_id}):
                    await self._compact_page_order(connection, source_group_id)
        return len(rows)

    async def rename_group(self, group_id: str, new_title: str) -> tuple[str, int]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        clean_title = (new_title or "").strip() or "Ungrouped"
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT id, title FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                )
                if row is None:
                    raise GroupNotFound("Manga group not found")
                duplicate = await connection.fetchval(
                    "SELECT 1 FROM manga_groups WHERE id<>$1 AND title=$2",
                    group_id,
                    clean_title,
                )
                if duplicate:
                    raise GroupConflict("A manga group with this title already exists")
                old_title = row["title"]
                if old_title == clean_title:
                    count = await connection.fetchval(
                        "SELECT count(*) FROM pages WHERE active AND manga_group_id=$1", group_id
                    )
                    return old_title, int(count or 0)
                count = await connection.fetchval(
                    "SELECT count(*) FROM pages WHERE active AND manga_group_id=$1", group_id
                )
                await connection.execute(
                    "UPDATE manga_groups SET title=$2, updated_at=now() WHERE id=$1",
                    group_id,
                    clean_title,
                )
                await connection.execute(
                    """
                    UPDATE pages
                    SET metadata=jsonb_set(metadata, '{mangaTitle}', to_jsonb($2::text), TRUE),
                        updated_at=now()
                    WHERE active AND manga_group_id=$1
                    """,
                    group_id,
                    clean_title,
                )
        return old_title, int(count or 0)

    async def delete_result(self, folder: str) -> bool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        record_id = _safe_folder(str(folder))
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                page = await connection.fetchrow(
                    """
                    SELECT p.id, g.id AS group_id, g.series_id
                    FROM pages p
                    JOIN manga_groups g ON g.id=p.manga_group_id
                    WHERE p.active AND (p.id=$1 OR p.folder=$1)
                    FOR UPDATE OF p, g
                    """,
                    record_id,
                )
                if page is None:
                    return False
                result = await connection.execute("DELETE FROM pages WHERE id=$1", page["id"])
                await self._compact_page_order(connection, page["group_id"])
                if not await connection.fetchval(
                    "SELECT 1 FROM pages WHERE active AND manga_group_id=$1 LIMIT 1", page["group_id"]
                ):
                    await connection.execute("DELETE FROM manga_groups WHERE id=$1", page["group_id"])
                if page["series_id"]:
                    remaining = await connection.fetchval(
                        """
                        SELECT count(*) FROM manga_groups g
                        WHERE g.series_id=$1
                          AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
                        """,
                        page["series_id"],
                    )
                    if int(remaining or 0) < 2:
                        await connection.execute(
                            "UPDATE manga_groups SET series_id=NULL, series_position=NULL WHERE series_id=$1",
                            page["series_id"],
                        )
                        await connection.execute("DELETE FROM manga_series WHERE id=$1", page["series_id"])
        return result.endswith("1")

    async def delete_group(self, title: str) -> list[str]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(title)
        if group_id is None:
            return []
        rows = await self.pool.fetch("SELECT folder FROM pages WHERE manga_group_id=$1 AND active", group_id)
        folders = [row["folder"] for row in rows]
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                group = await connection.fetchrow(
                    "SELECT id, series_id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                )
                await connection.execute("DELETE FROM pages WHERE manga_group_id=$1", group_id)
                if group:
                    await connection.execute("DELETE FROM manga_groups WHERE id=$1", group["id"])
                    if group["series_id"]:
                        remaining = await connection.fetchval(
                            """
                            SELECT count(*) FROM manga_groups g
                            WHERE g.series_id=$1
                              AND EXISTS (SELECT 1 FROM pages p WHERE p.active AND p.manga_group_id=g.id)
                            """,
                            group["series_id"],
                        )
                        if int(remaining or 0) < 2:
                            await connection.execute(
                                "UPDATE manga_groups SET series_id=NULL, series_position=NULL WHERE series_id=$1",
                                group["series_id"],
                            )
                            await connection.execute(
                                "DELETE FROM manga_series WHERE id=$1", group["series_id"]
                            )
        return folders

    async def clear_results(self) -> int:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        count = await self.pool.fetchval("SELECT count(*) FROM pages WHERE active")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("DELETE FROM result_documents")
                await connection.execute("DELETE FROM pages")
                await connection.execute("DELETE FROM pipeline_runs")
        return int(count)

    async def get_progress(self, installation_id: str, manga_title: str) -> dict[str, Any] | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        group_id = await self.resolve_group_id(manga_title)
        if group_id is None:
            return None
        row = await self.pool.fetchrow(
            """
            SELECT rp.id, rp.page_id, rp.page_number, rp.scroll_top, rp.complete,
                   rp.updated_at, g.id AS group_id, g.title AS manga_title
            FROM reading_progress rp
            JOIN manga_groups g ON g.id=rp.group_id
            WHERE rp.installation_id=$1 AND rp.group_id=$2
            """,
            installation_id,
            group_id,
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "installationId": installation_id,
            "groupId": row["group_id"],
            "mangaTitle": row["manga_title"],
            "pageId": row["page_id"],
            "page": row["page_number"],
            "scrollTop": row["scroll_top"],
            "complete": row["complete"],
            "updatedAt": _iso(row["updated_at"]),
        }

    async def save_progress(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        updated_at = payload.get("updatedAt")
        parsed_updated_at = _parse_finished_at(updated_at, dt.datetime.now(dt.timezone.utc))
        group_value = payload.get("groupId") or payload.get("mangaTitle")
        group_id = await self.resolve_group_id(group_value) if group_value else None
        if group_id is None:
            raise GroupNotFound("Manga group not found")
        page_id = payload.get("pageId")
        if page_id:
            page = await self.pool.fetchrow(
                "SELECT id, manga_group_id FROM pages WHERE active AND (id=$1 OR folder=$1) LIMIT 1",
                str(page_id),
            )
            if page is None:
                page_id = None
            elif page["manga_group_id"] != group_id:
                raise ValueError("Page does not belong to the manga group")
            else:
                page_id = page["id"]
        values = (
            str(uuid.uuid4()),
            payload["installationId"],
            group_id,
            page_id,
            payload.get("page"),
            max(0, int(payload.get("scrollTop", 0))),
            bool(payload.get("complete", False)),
            parsed_updated_at,
        )
        row = await self.pool.fetchrow(
            """
            INSERT INTO reading_progress(
                id,installation_id,group_id,page_id,page_number,scroll_top,complete,updated_at
            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT(installation_id,group_id) DO UPDATE SET
                page_id=EXCLUDED.page_id,
                page_number=EXCLUDED.page_number,
                scroll_top=EXCLUDED.scroll_top,
                complete=EXCLUDED.complete,
                updated_at=EXCLUDED.updated_at
            WHERE reading_progress.updated_at <= EXCLUDED.updated_at
            RETURNING id,page_id,page_number,scroll_top,complete,updated_at
            """,
            *values,
        )
        if row is None:
            return (await self.get_progress(values[1], values[2])) or payload
        group_title = await self.pool.fetchval("SELECT title FROM manga_groups WHERE id=$1", group_id)
        return {
            "id": row["id"],
            "installationId": values[1],
            "groupId": group_id,
            "mangaTitle": group_title,
            "pageId": row["page_id"],
            "page": row["page_number"],
            "scrollTop": row["scroll_top"],
            "complete": row["complete"],
            "updatedAt": _iso(row["updated_at"]),
        }

    async def record_migration(
        self, source_type: str, source_key: str, status: str, detail: dict[str, Any]
    ) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        await self.pool.execute(
            """
            INSERT INTO migration_records(id,source_type,source_key,status,detail,updated_at)
            VALUES($1,$2,$3,$4,$5::jsonb,now())
            ON CONFLICT(source_type,source_key) DO UPDATE SET
                status=EXCLUDED.status, detail=EXCLUDED.detail, updated_at=now()
            """,
            str(uuid.uuid4()),
            source_type,
            source_key,
            status,
            _json_dump(detail),
        )


class PostgresBatchStore(BatchStore):
    """BatchStore-compatible adapter; PostgreSQL owns state, files own inputs."""

    def __init__(self, database: PostgresStore, root: str | Path, result_root: str | Path):
        super().__init__(root, result_root)
        self.database = database

    @property
    def _pool(self) -> Any:
        if self.database.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        return self.database.pool

    @asynccontextmanager
    async def _batch_connection(self, connection: Any = None):
        if connection is not None:
            yield connection
            return
        async with self._pool.acquire() as acquired:
            yield acquired

    async def _save_db_manifest(self, manifest: dict[str, Any], connection: Any = None) -> None:
        async with self._batch_connection(connection) as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO batches(
                        id,title,status,dismissed,added_at,updated_at,
                        total_items,completed_count,manifest,snapshot_path,active
                    ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,TRUE)
                    ON CONFLICT(id) DO UPDATE SET
                        title=EXCLUDED.title,
                        status=EXCLUDED.status,
                        dismissed=EXCLUDED.dismissed,
                        added_at=EXCLUDED.added_at,
                        updated_at=EXCLUDED.updated_at,
                        total_items=EXCLUDED.total_items,
                        completed_count=EXCLUDED.completed_count,
                        manifest=EXCLUDED.manifest,
                        snapshot_path=EXCLUDED.snapshot_path,
                        active=TRUE
                    """,
                    manifest["id"],
                    manifest["title"],
                    manifest["status"],
                    bool(manifest.get("dismissed", False)),
                    int(manifest.get("addedAt", 0)),
                    int(manifest.get("updatedAt", 0)),
                    int(manifest.get("totalItems", len(manifest.get("items", [])))),
                    int(manifest.get("completedCount", 0)),
                    _json_dump(manifest),
                    str(self._manifest_path(manifest["id"])),
                )
                reservations = {
                    row["id"]: row
                    for row in await connection.fetch(
                        "SELECT id, manga_group_id, page_order FROM batch_items WHERE batch_id=$1",
                        manifest["id"],
                    )
                }
                await connection.execute("DELETE FROM batch_items WHERE batch_id=$1", manifest["id"])
                prepared_items: list[tuple[dict[str, Any], str, str | None, int | None]] = []
                for item in manifest.get("items", []):
                    item_title = item.get("mangaTitle", manifest["mangaTitle"])
                    group_id = await connection.fetchval(
                        "SELECT id FROM manga_groups WHERE id=$1", item.get("mangaGroupId")
                    )
                    if group_id is None:
                        group_id = await connection.fetchval(
                            "SELECT id FROM manga_groups WHERE title=$1", item_title
                        )
                    if group_id is None:
                        group_id = await connection.fetchval(
                            "INSERT INTO manga_groups(id,title) VALUES($1,$2) RETURNING id",
                            str(uuid.uuid4()),
                            item_title,
                        )
                    page_value = item.get("pageId") or item.get("resultFolder")
                    page_id = None
                    page = None
                    if page_value:
                        page = await connection.fetchrow(
                            """
                            SELECT p.id, p.manga_group_id, p.page_order, g.title AS manga_title
                            FROM pages p
                            JOIN manga_groups g ON g.id=p.manga_group_id
                            WHERE p.active AND (p.id=$1 OR p.folder=$1)
                            LIMIT 1
                            """,
                            str(page_value),
                        )
                    if page is not None:
                            if page["manga_group_id"] != group_id:
                                logger.warning(
                                    "Repairing stale batch item group for page %s: %s -> %s",
                                    page["id"],
                                    group_id,
                                    page["manga_group_id"],
                                )
                                group_id = page["manga_group_id"]
                                item_title = page["manga_title"]
                            page_id = page["id"]
                            item["pageOrder"] = (
                                page.get("page_order")
                                if hasattr(page, "get")
                                else page["page_order"]
                            )
                    else:
                        # Progress writes must keep the reservation used by the running job.
                        # Only persisted positions are trusted, never client-supplied ones.
                        reservation = reservations.get(item["id"])
                        item["pageOrder"] = (
                            reservation["page_order"]
                            if reservation and reservation["manga_group_id"] == group_id
                            else None
                        )
                    item["mangaTitle"] = item_title
                    item["mangaGroupId"] = group_id
                    item["pageId"] = page_id
                    prepared_items.append((item, group_id, page_id, _page_order(item.get("pageOrder"))))

                by_group: dict[str, list[dict[str, Any]]] = {}
                for item, group_id, _page_id, page_order in prepared_items:
                    if page_order is None:
                        by_group.setdefault(group_id, []).append(item)
                for group_id, missing_items in sorted(by_group.items()):
                    await connection.fetchrow(
                        "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                    )
                    current_max = int(
                        await connection.fetchval(
                            """
                            SELECT GREATEST(
                                COALESCE((
                                    SELECT MAX(page_order) FROM pages
                                    WHERE active AND manga_group_id=$1
                                ), 0),
                                COALESCE((
                                    SELECT MAX(bi.page_order)
                                    FROM batch_items bi
                                    JOIN batches b ON b.id=bi.batch_id
                                    WHERE bi.manga_group_id=$1
                                      AND bi.page_order IS NOT NULL
                                      AND b.active AND NOT b.dismissed
                                      AND bi.status IN ('queued', 'processing', 'error')
                                ), 0),
                                $2
                            )
                            """,
                            group_id,
                            max(
                                (_page_order(item.get("pageOrder")) or 0)
                                for item in manifest.get("items", [])
                                if item.get("mangaGroupId") == group_id
                            ),
                        )
                    )
                    for item in missing_items:
                        current_max += 1
                        item["pageOrder"] = current_max

                item_rows = []
                for item, group_id, page_id, page_order in prepared_items:
                    page_order = _page_order(item.get("pageOrder")) or page_order
                    item_rows.append(
                        (
                            manifest["id"],
                            item["id"],
                            item["name"],
                            group_id,
                            page_id,
                            item.get("status", "queued"),
                            item.get("stage"),
                            item.get("stageStartedAt"),
                            item.get("error"),
                            item.get("requestId"),
                            page_order,
                            _json_dump(item),
                        )
                    )
                if not manifest.get("mangaGroupId") and prepared_items:
                    manifest["mangaGroupId"] = prepared_items[0][1]
                await connection.execute(
                    "UPDATE batches SET manifest=$2::jsonb WHERE id=$1",
                    manifest["id"],
                    _json_dump(manifest),
                )
                await connection.executemany(
                    """
                    INSERT INTO batch_items(
                        batch_id,id,name,manga_group_id,page_id,status,stage,stage_started_at,
                        error,request_id,page_order,payload
                    ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
                    ON CONFLICT (batch_id, id) DO UPDATE SET
                        name = EXCLUDED.name,
                        manga_group_id = EXCLUDED.manga_group_id,
                        page_id = EXCLUDED.page_id,
                        status = EXCLUDED.status,
                        stage = EXCLUDED.stage,
                        stage_started_at = EXCLUDED.stage_started_at,
                        error = EXCLUDED.error,
                        request_id = EXCLUDED.request_id,
                        page_order = EXCLUDED.page_order,
                        payload = EXCLUDED.payload
                    """,
                    item_rows,
                )

    async def _hydrate_manifest(
        self, manifest: dict[str, Any], connection: Any = None
    ) -> dict[str, Any]:
        fetch = connection.fetch if connection is not None else self._pool.fetch
        rows = await fetch(
            """
            SELECT i.id, i.manga_group_id, g.title AS manga_title,
                   i.page_id, i.page_order, p.folder AS result_folder,
                   i.status, i.stage, i.stage_started_at, i.error, i.request_id, i.payload
            FROM batch_items i
            LEFT JOIN manga_groups g ON g.id=i.manga_group_id
            LEFT JOIN pages p ON p.id=i.page_id
            WHERE i.batch_id=$1
            """,
            manifest["id"],
        )
        by_id = {row["id"]: row for row in rows}
        hydrated = copy.deepcopy(manifest)
        for item in hydrated.get("items", []):
            row = by_id.get(item.get("id"))
            if row is None:
                continue
            payload = _json_load(row.get("payload"), {})
            if isinstance(payload, dict):
                item.update(payload)
            for column, key in (
                ("status", "status"),
                ("stage", "stage"),
                ("stage_started_at", "stageStartedAt"),
                ("error", "error"),
                ("request_id", "requestId"),
            ):
                if column in row.keys():
                    if column == "stage_started_at" and row[column] is None:
                        continue
                    item[key] = row[column]
            item["mangaGroupId"] = row["manga_group_id"]
            item["pageId"] = row["page_id"]
            item["pageOrder"] = row["page_order"]
            item["mangaTitle"] = row["manga_title"] or item.get("mangaTitle", hydrated.get("mangaTitle"))
            if row["result_folder"] is not None:
                item["resultFolder"] = row["result_folder"]
        return hydrated

    async def _db_manifest(
        self, batch_id: str, connection: Any = None, for_update: bool = False
    ) -> dict[str, Any]:
        query = """SELECT manifest,status,title,dismissed,added_at,
                           updated_at,total_items,completed_count
                    FROM batches WHERE id=$1 AND active"""
        if for_update:
            query += " FOR UPDATE"
        fetchrow = connection.fetchrow if connection is not None else self._pool.fetchrow
        row = await fetchrow(query, batch_id)
        if row is None:
            raise BatchNotFound(batch_id)
        value = _json_load(row["manifest"], None)
        if not isinstance(value, dict):
            raise InvalidBatch(f"Malformed database manifest: {batch_id}")
        return await self._hydrate_manifest(self._overlay_batch_columns(value, row), connection)

    @staticmethod
    def _overlay_batch_columns(manifest: dict[str, Any], row: Any) -> dict[str, Any]:
        hydrated = copy.deepcopy(manifest)
        fields = {
            "status": "status",
            "title": "title",
            "dismissed": "dismissed",
            "added_at": "addedAt",
            "updated_at": "updatedAt",
            "total_items": "totalItems",
            "completed_count": "completedCount",
        }
        for column, key in fields.items():
            if column in row.keys():
                hydrated[key] = row[column]
        return hydrated

    def _write_snapshot(self, manifest: dict[str, Any]) -> None:
        path = self._manifest_path(manifest["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_manifest(path, manifest)

    def _stage_batch(
        self,
        batch_id: str,
        normalized: dict[str, Any],
        files: dict[str, tuple[str, bytes]],
    ) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        batch_dir = self._batch_dir(batch_id)
        if batch_dir.exists():
            raise BatchConflict(f"Batch {batch_id} already exists but is not indexed")
        expected = set() if normalized.get("kind") in {"rerender", "pipeline-rerun"} else {
            item["id"] for item in normalized["items"] if item["status"] != "completed"
        }
        if set(files) != expected:
            missing = expected - set(files)
            extra = set(files) - expected
            detail = []
            if missing:
                detail.append(f"missing uploads: {sorted(missing)}")
            if extra:
                detail.append(f"unknown uploads: {sorted(extra)}")
            raise InvalidBatch("; ".join(detail) or "Upload does not match manifest")
        temporary = Path(tempfile.mkdtemp(prefix=f".{batch_id}-", dir=self.root))
        try:
            inputs = temporary / "inputs"
            inputs.mkdir()
            for item in normalized["items"]:
                if item["status"] == "completed":
                    continue
                if normalized.get("kind") in {"rerender", "pipeline-rerun"}:
                    continue
                filename, content = files[item["id"]]
                if not isinstance(filename, str) or Path(filename).name != filename:
                    raise InvalidBatch("Invalid uploaded filename")
                suffix = Path(filename).suffix.lower() or Path(item["name"]).suffix.lower() or ".bin"
                item["input"] = f"inputs/{item['id']}{suffix}"
                dest = inputs / f"{item['id']}{suffix}"
                if isinstance(content, bytes):
                    dest.write_bytes(content)
                elif hasattr(content, "file"):
                    content.file.seek(0)
                    with dest.open("wb") as out:
                        shutil.copyfileobj(content.file, out)
                elif hasattr(content, "read"):
                    content.seek(0)
                    with dest.open("wb") as out:
                        shutil.copyfileobj(content, out)
                else:
                    dest.write_bytes(bytes(content))
            normalized["updatedAt"] = int(dt.datetime.now().timestamp() * 1000)
            self._write_manifest(temporary / "manifest.json", normalized)
            os.replace(temporary, batch_dir)
            return normalized
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    async def put_batch(
        self,
        batch_id: str,
        manifest: dict[str, Any],
        files: dict[str, tuple[str, bytes]],
    ) -> dict[str, Any]:
        async with self._lock:
            normalized = self._normalize_manifest(batch_id, manifest)
            try:
                existing = await self._db_manifest(batch_id)
            except BatchNotFound:
                existing = None
            if existing is not None:
                if self._submission_shape(existing) == self._submission_shape(normalized):
                    return self._to_dto(existing)
                raise BatchConflict(f"Batch {batch_id} already exists with different data")
            staged = await asyncio.to_thread(self._stage_batch, batch_id, normalized, files)
            try:
                await self._save_db_manifest(staged)
            except Exception:
                logger.exception("Batch %s staged but database indexing failed", batch_id)
                raise
            return self._to_dto(staged)

    async def list_runnable_batches(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT id,manifest,status,title,dismissed,added_at,
                   updated_at,total_items,completed_count FROM batches
            WHERE active AND status IN ('waiting', 'processing')
            ORDER BY CASE WHEN manifest->>'priority' = 'true' THEN 0 ELSE 1 END, added_at, id
            """
        )
        result = []
        for row in rows:
            manifest = _json_load(row["manifest"], {})
            if isinstance(manifest, dict):
                manifest = self._overlay_batch_columns(manifest, row)
                result.append(await self._hydrate_manifest(manifest) if manifest.get("items") else manifest)
        return result

    async def list_batches(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """SELECT id,manifest,status,title,dismissed,added_at,
                      updated_at,total_items,completed_count FROM batches WHERE active
               ORDER BY CASE WHEN manifest->>'priority' = 'true' THEN 0 ELSE 1 END, added_at, id"""
        )
        result = []
        for row in rows:
            manifest = _json_load(row["manifest"], {})
            if isinstance(manifest, dict):
                manifest = self._overlay_batch_columns(manifest, row)
                hydrated = await self._hydrate_manifest(manifest) if manifest.get("items") else manifest
                result.append(self._to_dto(hydrated))
        return result

    async def list_batch_summaries(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT
                b.id,
                b.title,
                b.status,
                b.dismissed,
                b.added_at,
                b.updated_at,
                b.total_items,
                b.completed_count,
                b.manifest->'settings' AS settings,
                b.manifest->>'kind' AS kind,
                COALESCE(b.manifest->>'mangaGroupId', MAX(i.manga_group_id)) AS manga_group_id,
                (b.manifest->>'priority')::boolean AS priority,
                COUNT(*) FILTER (WHERE i.status = 'queued' OR i.stage IN ('awaiting_translation', 'reserved')) AS queued_count,
                COUNT(*) FILTER (WHERE i.status = 'processing' AND COALESCE(i.stage, '') NOT IN ('awaiting_translation', 'reserved')) AS processing_count,
                COUNT(*) FILTER (WHERE i.status = 'error') AS failed_count,
                COUNT(*) FILTER (WHERE i.payload->>'needsReview' = 'true') AS needs_review_count
            FROM batches b
            LEFT JOIN batch_items i ON i.batch_id = b.id
            WHERE b.active
            GROUP BY b.id
            ORDER BY CASE WHEN (b.manifest->>'priority')::boolean THEN 0 ELSE 1 END,
                     b.added_at,
                     b.id
            """
        )
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "mangaTitle": row["title"],
                "mangaGroupId": row["manga_group_id"],
                "kind": row["kind"],
                "status": row["status"],
                "dismissed": bool(row["dismissed"]),
                "addedAt": row["added_at"],
                "updatedAt": row["updated_at"],
                "settings": _json_load(row["settings"], {}),
                "priority": bool(row["priority"]),
                "totalItems": int(row["total_items"]),
                "completedCount": int(row["completed_count"]),
                "queuedCount": int(row["queued_count"] or 0),
                "processingCount": int(row["processing_count"] or 0),
                "failedCount": int(row["failed_count"] or 0),
                "needsReviewCount": int(row["needs_review_count"] or 0),
            }
            for row in rows
        ]

    async def update_review_for_result(self, folder: str, needs_review: bool) -> int:
        folder = _safe_folder(folder)
        snapshots = []
        updated = 0
        async with self._lock:
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    rows = await connection.fetch(
                        """
                        SELECT b.id AS batch_id, b.manifest, i.id AS item_id
                        FROM batches b
                        JOIN batch_items i ON i.batch_id=b.id
                        LEFT JOIN pages p ON p.id=i.page_id
                        WHERE b.active
                          AND (p.folder=$1 OR i.payload->>'resultFolder'=$1)
                        FOR UPDATE OF b
                        """,
                        folder,
                    )
                    by_batch: dict[str, tuple[dict[str, Any], set[str]]] = {}
                    for row in rows:
                        batch_id = row["batch_id"]
                        if batch_id not in by_batch:
                            by_batch[batch_id] = (copy.deepcopy(_json_load(row["manifest"], {})), set())
                        by_batch[batch_id][1].add(row["item_id"])

                    now = int(dt.datetime.now().timestamp() * 1000)
                    for batch_id, (manifest, item_ids) in by_batch.items():
                        for item in manifest.get("items", []):
                            if item.get("id") in item_ids:
                                item["needsReview"] = needs_review
                        manifest["updatedAt"] = now
                        await connection.execute(
                            """
                            UPDATE batch_items
                            SET payload=jsonb_set(payload, '{needsReview}', to_jsonb($3::boolean), true)
                            WHERE batch_id=$1 AND id=ANY($2::text[])
                            """,
                            batch_id,
                            list(item_ids),
                            needs_review,
                        )
                        await connection.execute(
                            "UPDATE batches SET manifest=$2::jsonb, updated_at=$3 WHERE id=$1",
                            batch_id,
                            _json_dump(manifest),
                            now,
                        )
                        snapshots.append(manifest)
                        updated += len(item_ids)
            for manifest in snapshots:
                await asyncio.to_thread(self._write_snapshot, manifest)
        return updated

    async def get_batch(self, batch_id: str) -> dict[str, Any]:
        return self._to_dto(await self._db_manifest(batch_id))

    async def mutate(self, batch_id: str, mutator: Any) -> dict[str, Any]:
        async with self._lock:
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    manifest = await self._db_manifest(batch_id, connection, for_update=True)
                    result = mutator(manifest)
                    if result is False:
                        return self._to_dto(manifest)
                    manifest["updatedAt"] = int(dt.datetime.now().timestamp() * 1000)
                    manifest["totalItems"] = max(
                        len(manifest.get("items", [])), int(manifest.get("totalItems", 0))
                    )
                    manifest["completedCount"] = max(
                        sum(item.get("status") == "completed" for item in manifest.get("items", [])),
                        int(manifest.get("completedCount", 0)),
                    )
                    await self._save_db_manifest(manifest, connection)
            await asyncio.to_thread(self._write_snapshot, manifest)
            return self._to_dto(manifest)

    async def delete_batch(self, batch_id: str) -> None:
        async with self._lock:
            manifest = await self._db_manifest(batch_id)
            group_ids = sorted({
                item.get("mangaGroupId")
                for item in manifest.get("items", [])
                if item.get("mangaGroupId")
            })
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    for group_id in group_ids:
                        await connection.fetchrow(
                            "SELECT id FROM manga_groups WHERE id=$1 FOR UPDATE", group_id
                        )
                    for group_id in group_ids:
                        await self.database._compact_page_order(connection, group_id)
                    await connection.execute("DELETE FROM batches WHERE id=$1", batch_id)
            await asyncio.to_thread(shutil.rmtree, self._batch_dir(batch_id), True)

    async def input_path(self, batch_id: str, item_id: str) -> Path:
        manifest = await self._db_manifest(batch_id)
        for item in manifest.get("items", []):
            if item.get("id") == item_id:
                path = self._input_path(batch_id, item)
                if path.is_file():
                    return path
                folder = item.get("resultFolder")
                if folder and isinstance(folder, str):
                    result = self.result_root / folder
                    for stem in ("input", "inpainted", "final"):
                        fallback = find_asset(result, stem)
                        if fallback and fallback.is_file():
                            return fallback
                break
        raise BatchNotFound(f"{batch_id}/{item_id}")

    async def reconcile(self, result_root: str | Path | None = None) -> None:
        root = Path(result_root or self.result_root).resolve()
        rows = await self._pool.fetch("SELECT manifest FROM batches WHERE active")
        request_ids = [
            item.get("requestId")
            for row in rows
            for item in _json_load(row["manifest"], {}).get("items", [])
            if item.get("status") == "processing" and item.get("requestId")
        ]
        folders_by_request = {}
        if request_ids:
            matches = await self._pool.fetch(
                """
                SELECT DISTINCT ON (request_id) request_id, folder
                FROM pages
                WHERE active AND request_id = ANY($1::text[])
                ORDER BY request_id, updated_at DESC
                """,
                request_ids,
            )
            folders_by_request = {row["request_id"]: row["folder"] for row in matches}
        for row in rows:
            manifest = _json_load(row["manifest"], {})
            was_paused = manifest.get("status") == "paused"
            changed = False
            for item in manifest.get("items", []):
                if item.get("status") != "processing":
                    continue
                folder = folders_by_request.get(item.get("requestId")) or item.get("resultFolder")
                result = (
                    root / folder
                    if isinstance(folder, str) and Path(folder).name == folder
                    else None
                )
                if result and final_file(result) is not None:
                    item.update(status="completed", stage="finished", resultFolder=folder)
                    self._input_path(batch_id=manifest["id"], item=item).unlink(missing_ok=True)
                else:
                    item.update(status="queued", stage=None)
                changed = True
            if changed:
                statuses = {item.get("status") for item in manifest.get("items", [])}
                manifest["status"] = (
                    "paused" if was_paused and "queued" in statuses else
                    "error" if "error" in statuses else
                    "completed" if statuses and statuses <= {"completed"} else
                    "waiting"
                )
                await self._save_db_manifest(manifest)
                await asyncio.to_thread(self._write_snapshot, manifest)

    async def register_result(
        self,
        folder: str,
        page_order: int | None = None,
        page_id: str | None = None,
    ) -> None:
        await self.database.sync_result_folder(
            folder,
            page_order=page_order,
            replace_page_id=page_id,
        )

    async def import_existing_batch(self, batch_id: str) -> dict[str, Any]:
        normalized = await asyncio.to_thread(
            self._normalize_manifest,
            batch_id,
            self._read_manifest(self._manifest_path(batch_id)),
        )
        await self._save_db_manifest(normalized)
        return self._to_dto(normalized)
