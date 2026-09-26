"""PostgreSQL metadata/state storage with file-backed manga assets."""

from __future__ import annotations

import datetime as dt
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any
from server.postgres_common import _json_dump, _safe_folder

from server.batch_store import BatchConflict, BatchNotFound, BatchStore, InvalidBatch
from server.series_repository import (
    GroupConflict,
    GroupNotFound,
    InvalidSeries,
    SeriesConflict,
    SeriesNotFound,
    SeriesRepository,
    SeriesStoreError,
)
from server.image_variants import asset_version, final_file, generate_image_variants
from server.result_snapshot import (
    natural_sort_key as _natural_sort_key,
    page_snapshot as _page_snapshot_impl,
    parse_finished_at as _parse_finished_at,
)
from manga_translator.pipeline.stages import PipelineStage, StageStatus
from manga_translator.utils.image_storage import find_asset
from server.postgres_schema import (
    apply_migrations as _apply_migrations,
    check_schema as _check_schema,
    close as _close_postgres,
    start as _start_postgres,
)
from server.document_owner import resolve_document_owner as _resolve_document_owner
from server.result_index_queries import index_untracked_results as _index_untracked_results

logger = logging.getLogger("manga-translator.postgres")


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






def _input_file(folder_path: Path) -> Path | None:
    return find_asset(folder_path, "input")


class PostgresStore:
    def __init__(self, database_url: str, result_root: str | Path):
        self.database_url = database_url
        self.result_root = Path(result_root).resolve()
        self.pool: Any = None
        self._series_repository = SeriesRepository(self, _iso, _natural_sort_key)
        self._summary_repository = SummaryRepository(self)
        self._pipeline_repository = PipelineRepository(self)
        self._manga_repository = MangaRepository(self, _iso, _parse_finished_at)
        self._manga_mutation_repository = MangaMutationRepository(self)
        self._page_state_repository = PageStateRepository(self, _manga_id)
        self._document_repository = DocumentRepository(self)

    @property
    def ready(self) -> bool:
        return self.pool is not None

    async def start(self, check_schema: bool = True) -> None:
        return await _start_postgres(self, check_schema)

    async def close(self) -> None:
        return await _close_postgres(self)

    async def get_page_id(self, page_ref: str) -> str | None:
        return await self._page_state_repository.get_page_id(page_ref)

    async def check_schema(self) -> None:
        return await _check_schema(self)

    async def apply_migrations(self) -> str:
        return await _apply_migrations(self)

    def _page_snapshot(
        self,
        folder_path: Path,
        read_metadata: bool = True,
        read_regions: bool = True,
        metadata_override: dict[str, Any] | None = None,
        regions_override: list[Any] | None = None,
        generate_variants: bool = True,
    ) -> dict[str, Any]:
        return _page_snapshot_impl(
            folder_path,
            read_metadata,
            read_regions,
            metadata_override,
            regions_override,
            generate_variants,
            final_file=final_file,
            input_file=_input_file,
            generate_image_variants=generate_image_variants,
            asset_version=asset_version,
            find_asset=find_asset,
        )

    async def resolve_group_id(self, value: str, *, create: bool = False) -> str | None:
        return await self._page_state_repository.resolve_group_id(value, create=create)

    async def _document_owner(self, value: str, connection: Any | None = None) -> tuple[str, str] | None:
        return await _resolve_document_owner(self, value, connection)

    async def sync_result_folder(
        self,
        folder: str | Path,
        *,
        generate_variants: bool = True,
        page_order: int | None = None,
        replace_page_id: str | None = None,
    ) -> dict[str, Any]:
        return await _sync_result_folder(
            self,
            folder,
            generate_variants=generate_variants,
            page_order=page_order,
            replace_page_id=replace_page_id,
            pipeline_document_types=_PIPELINE_DOCUMENT_TYPES,
            pipeline_manifest_stage_rows=_pipeline_manifest_stage_rows,
            pipeline_manifest_artifact_rows=_pipeline_manifest_artifact_rows,
        )

    async def index_untracked_results(self) -> dict[str, int]:
        return await _index_untracked_results(self, logger)

    async def get_text_regions(self, record_id: str) -> list[Any] | None:
        return await self._page_state_repository.get_text_regions(record_id)

    async def update_text_regions(self, record_id: str, regions: list[dict[str, Any]]) -> bool:
        return await self._page_state_repository.update_text_regions(record_id, regions)

    async def update_review_status(self, record_id: str, status: str, reviewed_at: str | None) -> bool:
        return await self._page_state_repository.update_review_status(
            record_id, status, reviewed_at
        )

    async def save_documents(self, folder: str, documents: dict[str, Any]) -> None:
        """Persist virtual JSON sidecars without writing them beside image assets."""
        return await self._document_repository.save_documents(folder, documents)

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
        return await self._pipeline_repository.start_pipeline_stage(page_ref, stage, input_fingerprint=input_fingerprint, settings_fingerprint=settings_fingerprint, settings=settings)

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
        return await self._pipeline_repository.finish_pipeline_stage(page_ref, stage, status, duration_ms=duration_ms, metrics=metrics, error_code=error_code, error_message=error_message)

    async def invalidate_pipeline_stages(
        self, page_ref: str, stages: list[str | PipelineStage]
    ) -> int:
        """Mark existing checkpoints stale while keeping their attempt history."""
        return await self._pipeline_repository.invalidate_pipeline_stages(page_ref, stages)

    async def get_pipeline_stage_state(self, page_ref: str) -> list[dict[str, Any]]:
        return await self._pipeline_repository.get_pipeline_stage_state(page_ref)

    async def interrupt_running_pipeline_stages(self) -> int:
        """Close attempts left running when the server process stopped."""
        return await self._pipeline_repository.interrupt_running_pipeline_stages()

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
        return await self._pipeline_repository.save_pipeline_document(page_ref, stage, document_type, payload, schema_version=schema_version)

    async def get_pipeline_document(
        self, page_ref: str, stage: str | PipelineStage, document_type: str
    ) -> dict[str, Any] | None:
        return await self._pipeline_repository.get_pipeline_document(page_ref, stage, document_type)

    async def commit_pipeline_outputs(
        self,
        page_ref: str,
        documents: dict[str, Any],
        artifacts: list[dict[str, Any]],
    ) -> bool:
        """Activate structured-document and artifact revisions in one transaction."""
        return await self._pipeline_repository.commit_pipeline_outputs(page_ref, documents, artifacts)

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
        return await self._pipeline_repository.register_pipeline_artifact(page_ref, stage, artifact_type, relative_path, size_bytes=size_bytes, checksum=checksum, mime_type=mime_type, width=width, height=height)

    async def get_pipeline_artifact(
        self, page_ref: str, stage: str | PipelineStage, artifact_type: str
    ) -> dict[str, Any] | None:
        return await self._pipeline_repository.get_pipeline_artifact(page_ref, stage, artifact_type)

    async def get_document(self, record_id: str, name: str) -> Any | None:
        return await self._document_repository.get_document(record_id, name)

    async def get_documents(self, folder: str) -> dict[str, Any]:
        return await self._document_repository.get_documents(folder)

    async def delete_documents(self, folder: str) -> None:
        return await self._document_repository.delete_documents(folder)

    async def find_request(self, request_id: str) -> str | None:
        return await self._page_state_repository.find_request(request_id)

    async def resolve_folder(self, record_id: str) -> str | None:
        return await self._page_state_repository.resolve_folder(record_id)

    def _page_item(self, row: Any, slim: bool = False, include_cover: bool = False) -> dict[str, Any]:
        return self._manga_repository._page_item(row, slim=slim, include_cover=include_cover)

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
        return await self._manga_repository.list_groups(
            limit, offset, manga_id, search, sort, review, status
        )

    def _series_member_item(self, row: Any) -> dict[str, Any]:
        return self._series_repository._series_member_item(row)

    async def _series_members(self, series_id: str) -> list[dict[str, Any]]:
        return await self._series_repository._series_members(series_id)

    async def list_series(
        self,
        limit: int = 12,
        offset: int = 0,
        search: str | None = None,
    ) -> dict[str, Any]:
        return await self._series_repository.list_series(limit, offset, search)

    async def get_series(self, series_id: str) -> dict[str, Any]:
        return await self._series_repository.get_series(series_id)

    async def get_series_for_group(self, group_id: str) -> dict[str, Any] | None:
        return await self._series_repository.get_series_for_group(group_id)

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
        return await self._series_repository.create_series(title, group_ids)

    async def update_series_title(self, series_id: str, title: str) -> dict[str, Any]:
        return await self._series_repository.update_series_title(series_id, title)

    async def replace_series_members(self, series_id: str, group_ids: list[str]) -> dict[str, Any]:
        return await self._series_repository.replace_series_members(series_id, group_ids)

    async def delete_series(self, series_id: str) -> None:
        return await self._series_repository.delete_series(series_id)

    async def add_manga_to_series(self, series_id: str, group_ids: list[str]) -> dict[str, Any]:
        return await self._series_repository.add_manga_to_series(series_id, group_ids)

    async def move_manga_to_series(self, group_id: str, target_series_id: str) -> dict[str, Any]:
        return await self._series_repository.move_manga_to_series(group_id, target_series_id)

    async def remove_manga_from_series(self, group_id: str) -> None:
        return await self._series_repository.remove_manga_from_series(group_id)

    async def group_exists(self, title: str) -> bool:
        return await self._page_state_repository.group_exists(title)

    async def resolve_group_title(self, record_id: str) -> str | None:
        return await self._page_state_repository.resolve_group_title(record_id)

    async def get_summary_payload(self, group_value: str) -> dict[str, Any] | None:
        return await self._summary_repository.get_summary_payload(group_value)

    async def save_summary_payload(self, group_value: str, payload: dict[str, Any]) -> None:
        return await self._summary_repository.save_summary_payload(group_value, payload)

    async def summary_status(self, group_value: str) -> dict[str, Any] | None:
        return await self._summary_repository.summary_status(group_value)

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
        stage_passed_count: int | None = None,
    ) -> None:
        return await self._summary_repository.update_summary_job(group_value, status, error, stage, progress, message, current_page, page_count, pages_with_text, extraction_required, provider, model, refresh_text, regenerate, stage_passed_count)

    async def dismiss_summary_job(self, group_value: str) -> None:
        return await self._summary_repository.dismiss_summary_job(group_value)

    async def reconcile_summary_jobs(self) -> None:
        return await self._summary_repository.reconcile_summary_jobs()

    async def list_runnable_summary_jobs(self) -> list[dict[str, Any]]:
        return await self._summary_repository.list_runnable_summary_jobs()

    async def list_summary_jobs(self, completed_limit: int = 20) -> list[dict[str, Any]]:
        return await self._summary_repository.list_summary_jobs(completed_limit)

    async def list_results(
        self,
        sort: str = "alpha",
        manga: str | None = None,
        detail: str | None = None,
        limit: int = 100,
        offset: int = 0,
        review: str | None = None,
    ) -> dict[str, Any]:
        return await self._manga_repository.list_results(
            sort, manga, detail, limit, offset, review
        )

    async def page_detail(self, record_id: str) -> dict[str, Any] | None:
        return await self._manga_repository.page_detail(record_id)

    async def group_pages(self, title: str) -> list[dict[str, Any]]:
        return await self._manga_repository.group_pages(title)

    async def export_pages(
        self, title: str, folders: list[str] | None = None, *, original: bool = False
    ) -> list[dict[str, Any]]:
        return await self._manga_repository.export_pages(
            title, folders, original=original
        )

    async def reorder_pages(self, group_id: str, page_ids: list[str]) -> list[dict[str, Any]]:
        return await self._manga_mutation_repository.reorder_pages(group_id, page_ids)

    async def compact_page_order(self, group_id: str) -> None:
        return await self._manga_mutation_repository.compact_page_order(group_id)

    @staticmethod
    async def _compact_page_order(connection: Any, group_id: str) -> None:
        return await MangaMutationRepository._compact_page_order(connection, group_id)

    @staticmethod
    async def _next_page_order(connection: Any, group_id: str) -> int:
        return await MangaMutationRepository._next_page_order(connection, group_id)

    async def update_meta(
        self,
        folders: list[str] | None,
        old_title: str | None,
        new_title: str,
    ) -> int:
        return await self._manga_mutation_repository.update_meta(folders, old_title, new_title)

    async def rename_group(self, group_id: str, new_title: str) -> tuple[str, int]:
        return await self._manga_mutation_repository.rename_group(group_id, new_title)

    async def delete_results(self, folders: list[str]) -> list[str]:
        return await self._manga_mutation_repository.delete_results(folders)

    async def delete_result(self, folder: str) -> bool:
        return await self._manga_mutation_repository.delete_result(folder)

    async def delete_group(self, title: str) -> list[str]:
        return await self._manga_mutation_repository.delete_group(title)

    async def clear_results(self) -> int:
        return await self._manga_mutation_repository.clear_results()

    async def get_progress(self, installation_id: str, manga_title: str) -> dict[str, Any] | None:
        return await self._manga_repository.get_progress(installation_id, manga_title)

    async def save_progress(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._manga_repository.save_progress(payload)

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


from server.postgres_batch_store import PostgresBatchStore
from server.pipeline_repository import PipelineRepository, _PIPELINE_DOCUMENT_TYPES
from server.result_indexing import (
    _canonical_pipeline_stage,
    _manifest_datetime,
    _pipeline_manifest_artifact_rows,
    _pipeline_manifest_stage_rows,
    sync_result_folder as _sync_result_folder,
)
from server.manga_repository import MangaRepository
from server.manga_mutation_repository import MangaMutationRepository
from server.document_repository import DocumentRepository
from server.page_state_repository import PageStateRepository
from server.summary_repository import SummaryRepository
