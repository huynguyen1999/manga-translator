"""Durable FIFO queue for original manga uploads."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from server.postgres_common import _json_dump, _json_load

_SAFE_ID = re.compile(r"^[a-f0-9]{32}$")


def _now_ms() -> int:
    return int(time.time() * 1000)


class MangaImportJobNotFound(Exception):
    pass


class MangaImportJobConflict(Exception):
    pass


class MangaImportJobStore:
    """Store job state in JSON files or PostgreSQL; staged uploads always live on disk."""

    def __init__(self, root: str | Path, database: Any = None):
        self.root = Path(root).resolve()
        self.staging_root = self.root / "staged"
        self.records_root = self.root / "records"
        self.database = database
        self._lock = asyncio.Lock()

    @property
    def _pool(self):
        if self.database is None or self.database.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        return self.database.pool

    def stage_path(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not _SAFE_ID.fullmatch(job_id):
            raise ValueError("Invalid manga import job ID")
        return self.staging_root / job_id

    def _record_path(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not _SAFE_ID.fullmatch(job_id):
            raise ValueError("Invalid manga import job ID")
        return self.records_root / f"{job_id}.json"

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, path)

    async def create(self, job: dict[str, Any]) -> None:
        if self.database is not None:
            inserted = await self._pool.fetchrow(
                """INSERT INTO manga_import_jobs(id,title,status,client_upload_id,accepted_at,updated_at,payload)
                   VALUES($1,$2,$3,$4,clock_timestamp(),clock_timestamp(),$5::jsonb)
                   ON CONFLICT DO NOTHING RETURNING id""",
                job["id"], job["title"], job["status"], job.get("clientUploadId"), _json_dump(job),
            )
            if inserted is None:
                raise MangaImportJobConflict(job.get("clientUploadId") or job["id"])
            return
        async with self._lock:
            path = self._record_path(job["id"])
            if path.exists():
                raise MangaImportJobConflict(job["id"])
            existing = await self.list()
            if job.get("clientUploadId") and any(
                item.get("clientUploadId") == job["clientUploadId"] for item in existing
            ):
                raise MangaImportJobConflict(job["clientUploadId"])
            job["acceptedAt"] = max(
                _now_ms(), max((item.get("acceptedAt", 0) for item in existing), default=0) + 1
            )
            job["createdAt"] = job["acceptedAt"]
            job["updatedAt"] = job["acceptedAt"]
            await asyncio.to_thread(self._write_json, path, job)

    async def get_by_client_upload_id(self, client_upload_id: str) -> dict[str, Any] | None:
        if self.database is not None:
            row = await self._pool.fetchrow(
                "SELECT payload,status,accepted_at,updated_at FROM manga_import_jobs WHERE client_upload_id=$1",
                client_upload_id,
            )
            if row is None:
                return None
            job = _json_load(row["payload"], {})
            job.update(status=row["status"], acceptedAt=int(row["accepted_at"].timestamp() * 1000),
                       updatedAt=int(row["updated_at"].timestamp() * 1000))
            return job
        return next(
            (job for job in await self.list() if job.get("clientUploadId") == client_upload_id),
            None,
        )

    async def get(self, job_id: str) -> dict[str, Any]:
        self._record_path(job_id)
        if self.database is not None:
            row = await self._pool.fetchrow(
                "SELECT payload,status,accepted_at,updated_at FROM manga_import_jobs WHERE id=$1",
                job_id,
            )
            if row is None:
                raise MangaImportJobNotFound(job_id)
            job = _json_load(row["payload"], {})
            job.update(status=row["status"], acceptedAt=int(row["accepted_at"].timestamp() * 1000),
                       updatedAt=int(row["updated_at"].timestamp() * 1000))
            return job
        try:
            return await asyncio.to_thread(
                lambda: json.loads(self._record_path(job_id).read_text(encoding="utf-8"))
            )
        except FileNotFoundError as error:
            raise MangaImportJobNotFound(job_id) from error

    async def list(self) -> list[dict[str, Any]]:
        if self.database is not None:
            rows = await self._pool.fetch(
                "SELECT payload,status,accepted_at,updated_at FROM manga_import_jobs "
                "ORDER BY accepted_order DESC"
            )
            jobs = []
            for row in rows:
                job = _json_load(row["payload"], {})
                job.update(status=row["status"], acceptedAt=int(row["accepted_at"].timestamp() * 1000),
                           updatedAt=int(row["updated_at"].timestamp() * 1000))
                jobs.append(job)
            return jobs

        def read_records() -> list[dict[str, Any]]:
            if not self.records_root.exists():
                return []
            jobs = []
            for path in self.records_root.glob("*.json"):
                try:
                    job = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(job, dict):
                    jobs.append(job)
            return sorted(jobs, key=lambda job: (job.get("acceptedAt", 0), job.get("id", "")), reverse=True)

        return await asyncio.to_thread(read_records)

    async def update(self, job: dict[str, Any]) -> dict[str, Any]:
        job = {**job, "updatedAt": _now_ms()}
        if self.database is not None:
            result = await self._pool.execute(
                "UPDATE manga_import_jobs SET title=$2,status=$3,client_upload_id=$4,accepted_at=CASE WHEN status='failed' AND $3='queued' THEN clock_timestamp() ELSE accepted_at END,accepted_order=CASE WHEN status='failed' AND $3='queued' THEN nextval(pg_get_serial_sequence('manga_import_jobs','accepted_order')) ELSE accepted_order END,updated_at=clock_timestamp(),payload=$5::jsonb WHERE id=$1",
                job["id"], job["title"], job["status"], job.get("clientUploadId"), _json_dump(job),
            )
            if result != "UPDATE 1":
                raise MangaImportJobNotFound(job["id"])
            return job
        async with self._lock:
            path = self._record_path(job["id"])
            if not path.exists():
                raise MangaImportJobNotFound(job["id"])
            previous = await self.get(job["id"])
            if previous.get("status") == "failed" and job.get("status") == "queued":
                jobs = await self.list()
                job["acceptedAt"] = max(
                    _now_ms(), max((item.get("acceptedAt", 0) for item in jobs), default=0) + 1
                )
            await asyncio.to_thread(self._write_json, path, job)
        return job

    async def claim_next(self) -> dict[str, Any] | None:
        if self.database is not None:
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    await connection.execute("SELECT pg_advisory_xact_lock(718239114, 1)")
                    if await connection.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM manga_import_jobs WHERE status='processing')"
                    ):
                        return None
                    row = await connection.fetchrow(
                        "SELECT id,payload FROM manga_import_jobs WHERE status='queued' "
                        "ORDER BY accepted_order LIMIT 1 FOR UPDATE SKIP LOCKED"
                    )
                    if row is None:
                        return None
                    job = _json_load(row["payload"], {})
                    job.update(status="processing", startedAt=_now_ms(), error=None)
                    job["attempt"] = int(job.get("attempt", 0)) + 1
                    job = {**job, "updatedAt": _now_ms()}
                    await connection.execute(
                        "UPDATE manga_import_jobs SET status='processing',updated_at=to_timestamp($2 / 1000.0),payload=$3::jsonb WHERE id=$1",
                        job["id"], job["updatedAt"], _json_dump(job),
                    )
                    return job

        async with self._lock:
            queued = [job for job in await self.list() if job.get("status") == "queued"]
            if not queued:
                return None
            job = min(queued, key=lambda value: (value.get("acceptedAt", 0), value["id"]))
            job.update(status="processing", startedAt=_now_ms(), error=None)
            job["attempt"] = int(job.get("attempt", 0)) + 1
            job["updatedAt"] = _now_ms()
            await asyncio.to_thread(self._write_json, self._record_path(job["id"]), job)
            return job

    async def recover_processing(self) -> None:
        for job in await self.list():
            if job.get("status") == "processing":
                job["status"] = "queued"
                job["startedAt"] = None
                await self.update(job)

    async def delete(self, job_id: str) -> None:
        self._record_path(job_id)
        if self.database is not None:
            result = await self._pool.execute(
                "DELETE FROM manga_import_jobs WHERE id=$1 AND status IN ('failed','completed')",
                job_id,
            )
            if result != "DELETE 1":
                job = await self.get(job_id)
                raise MangaImportJobConflict(f"Cannot dismiss a {job['status']} job")
            return
        async with self._lock:
            job = await self.get(job_id)
            if job.get("status") not in {"failed", "completed"}:
                raise MangaImportJobConflict(f"Cannot dismiss a {job['status']} job")
            await asyncio.to_thread(self._record_path(job_id).unlink)

    async def cleanup_staging(self) -> None:
        jobs = await self.list()
        retained = {job["id"] for job in jobs if job.get("status") == "failed"}
        retained.update(job["id"] for job in jobs if job.get("status") in {"queued", "processing"})

        def cleanup() -> None:
            self.staging_root.mkdir(parents=True, exist_ok=True)
            for path in self.staging_root.iterdir():
                if path.name not in retained:
                    shutil.rmtree(path, ignore_errors=True)

        await asyncio.to_thread(cleanup)


class MangaImportJobQueue:
    """One sequential importer; translation and summary scheduling stay separate."""

    def __init__(
        self,
        store: MangaImportJobStore,
        processor: Callable[..., Any],
        logger: Any,
    ):
        self.store = store
        self.processor = processor
        self.logger = logger
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        await self.store.recover_processing()
        await self.store.cleanup_staging()
        self._task = asyncio.create_task(self._run(), name="manga-import-jobs")
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            await self._task
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def accept(self, job: dict[str, Any], uploads: list[Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        self.store.stage_path(job_id).parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{job_id}-", dir=self.store.staging_root))

        def copy_uploads() -> list[dict[str, str]]:
            staged = []
            for index, upload in enumerate(uploads):
                name = str(upload.filename or "")
                path = temporary / f"{index:06d}.upload"
                upload.file.seek(0)
                with path.open("wb") as output:
                    shutil.copyfileobj(upload.file, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                metadata = job["files"][index]
                staged.append({"filename": name, "sourcePath": metadata["sourcePath"]})
            return staged

        try:
            staged = await asyncio.to_thread(copy_uploads)
            final_path = self.store.stage_path(job_id)
            await asyncio.to_thread(os.replace, temporary, final_path)
            now = _now_ms()
            accepted = {
                **job,
                "id": job_id,
                "files": staged,
                "status": "queued",
                "acceptedAt": now,
                "createdAt": now,
                "updatedAt": now,
                "startedAt": None,
                "completedAt": None,
                "processedPages": 0,
                "totalPages": None,
                "progress": 0,
                "error": None,
                "attempt": 0,
            }
            await self.store.create(accepted)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            if "job_id" in locals():
                shutil.rmtree(self.store.stage_path(job_id), ignore_errors=True)
            raise
        self.wake()
        return accepted

    async def retry(self, job_id: str) -> dict[str, Any]:
        job = await self.store.get(job_id)
        if job.get("status") != "failed":
            raise MangaImportJobConflict(f"Cannot retry a {job.get('status')} job")
        if not self.store.stage_path(job_id).is_dir():
            raise MangaImportJobConflict("Staged upload is unavailable")
        now = _now_ms()
        job.update(status="queued", acceptedAt=now, updatedAt=now, startedAt=None,
                   completedAt=None, processedPages=0, progress=0, error=None,
                   attempt=int(job.get("attempt", 0)))
        job = await self.store.update(job)
        self.wake()
        return job

    async def dismiss(self, job_id: str) -> None:
        await self.store.delete(job_id)
        await asyncio.to_thread(shutil.rmtree, self.store.stage_path(job_id), True)

    async def _run(self) -> None:
        while not self._stopping:
            self._wake.clear()
            job = await self.store.claim_next()
            if job is None:
                if self._stopping:
                    return
                await self._wake.wait()
                continue
            try:
                async def update_progress(processed: int, total: int | None = None, phase: str | None = None):
                    current = await self.store.get(job["id"])
                    current["processedPages"] = processed
                    if total is not None:
                        current["totalPages"] = total
                        current["progress"] = round(100 * processed / total) if total else 0
                    if phase:
                        current["phase"] = phase
                    await self.store.update(current)

                result = await self.processor(
                    job, self.store.stage_path(job["id"]), update_progress
                )
                current = await self.store.get(job["id"])
                total = int(result.get("totalPages", current.get("totalPages") or 0))
                current.update(
                    status="completed", completedAt=_now_ms(), processedPages=total,
                    totalPages=total, progress=100, error=None,
                    group=result.get("group"), items=result.get("items", []),
                    groupId=(result.get("group") or {}).get("id"),
                    totalImages=result.get("totalImages"),
                )
                await self.store.update(current)
                await asyncio.to_thread(shutil.rmtree, self.store.stage_path(job["id"]), True)
            except Exception as error:
                self.logger.exception("Manga import job failed: id=%s", job["id"])
                current = await self.store.get(job["id"])
                detail = getattr(error, "detail", None)
                current.update(status="failed", error=str(detail or error), completedAt=None)
                await self.store.update(current)


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    total = job.get("totalPages")
    processed = int(job.get("processedPages", 0))
    return {
        key: job.get(key)
        for key in (
            "id", "title", "status", "createdAt", "acceptedAt", "updatedAt",
            "startedAt", "completedAt", "processedPages", "totalPages", "progress",
            "error", "group", "items", "totalImages", "attempt", "phase",
            "groupId",
        )
        if key in job
    } | {
        "progress": job.get("progress", round(100 * processed / total) if total else 0),
        "fileCount": len(job.get("files", [])),
    }
