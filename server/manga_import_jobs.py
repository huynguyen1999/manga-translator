"""Durable FIFO queue for original manga uploads."""

from __future__ import annotations

import asyncio
import errno
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
from server.manga_import_job_records import list_records, read_record, write_record

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
    import msvcrt

_SAFE_ID = re.compile(r"^[a-f0-9]{32}$")
_WORKER_POLL_SECONDS = 0.25


def _now_ms() -> int:
    return int(time.time() * 1000)


def _lock_file(path: Path, *, blocking: bool) -> int | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(fd, flags)
            except BlockingIOError:
                os.close(fd)
                return None
        else:  # pragma: no cover - Windows
            os.lseek(fd, 0, os.SEEK_SET)
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EDEADLK} and getattr(error, "winerror", None) != 33:
                        raise
                    if not blocking:
                        os.close(fd)
                        return None
                    time.sleep(0.05)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _unlock_file(fd: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        else:  # pragma: no cover - Windows
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


class MangaImportJobNotFound(Exception):
    pass


class MangaImportJobConflict(Exception):
    pass


class MangaImportJobStore:
    """Store job state in JSON files or PostgreSQL; staged uploads always live on disk."""

    def __init__(self, root: str | Path, database: Any = None):
        self.root = Path(root).resolve()
        self.staging_root = self.root / "staged"
        self.incoming_root = self.root / "incoming"
        self.records_root = self.root / "records"
        self.details_root = self.records_root / "details"
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

    def _details_path(self, job_id: str) -> Path:
        self._record_path(job_id)
        return self.details_root / f"{job_id}.json"

    def _write_record(self, job: dict[str, Any]) -> None:
        write_record(self._record_path(job["id"]), self._details_path(job["id"]), job, self._write_json)

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
                """INSERT INTO manga_import_jobs(id,title,status,client_upload_id,accepted_at,updated_at,payload,result_items)
                   VALUES($1,$2,$3,$4,clock_timestamp(),clock_timestamp(),$5::jsonb,$6::jsonb)
                   ON CONFLICT DO NOTHING RETURNING id""",
                job["id"], job["title"], job["status"], job.get("clientUploadId"),
                _json_dump({key: value for key, value in job.items() if key != "items"}),
                _json_dump(job.get("items", [])),
            )
            if inserted is None:
                raise MangaImportJobConflict(job.get("clientUploadId") or job["id"])
            return
        async with self._lock:
            lock_fd = await asyncio.to_thread(
                _lock_file, self.root / ".client-upload-id.lock", blocking=True
            )
            try:
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
                await asyncio.to_thread(self._write_record, job)
            finally:
                await asyncio.to_thread(_unlock_file, lock_fd)

    async def acquire_worker_lease(self):
        from server.manga_import_job_lifecycle import acquire_worker_lease

        return await acquire_worker_lease(self)

    async def release_worker_lease(self, lease) -> None:
        from server.manga_import_job_lifecycle import release_worker_lease

        await release_worker_lease(self, lease)

    async def _acquire_staging_lock(self) -> int:
        return await asyncio.to_thread(
            _lock_file, self.root / ".staging.lock", blocking=True
        )

    async def _release_staging_lock(self, lock_fd: int) -> None:
        await asyncio.to_thread(_unlock_file, lock_fd)

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

    async def get(self, job_id: str, *, include_items: bool = True) -> dict[str, Any]:
        self._record_path(job_id)
        if self.database is not None:
            items = ",result_items" if include_items else ""
            row = await self._pool.fetchrow(
                f"SELECT payload{items},status,accepted_at,updated_at FROM manga_import_jobs WHERE id=$1",
                job_id,
            )
            if row is None:
                raise MangaImportJobNotFound(job_id)
            job = _json_load(row["payload"], {})
            job.update(status=row["status"], acceptedAt=int(row["accepted_at"].timestamp() * 1000),
                       updatedAt=int(row["updated_at"].timestamp() * 1000))
            if include_items:
                job["items"] = _json_load(row["result_items"], [])
            else:
                job.pop("items", None)
            return job
        try:
            job = await asyncio.to_thread(
                read_record, self._record_path(job_id), self._details_path(job_id)
            )
        except FileNotFoundError as error:
            raise MangaImportJobNotFound(job_id) from error
        if not include_items:
            job.pop("items", None)
        return job

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

        return await asyncio.to_thread(
            list_records, self.records_root, self.details_root, self._write_json
        )

    async def update(self, job: dict[str, Any]) -> dict[str, Any]:
        job = {**job, "updatedAt": _now_ms()}
        if self.database is not None:
            result = await self._pool.execute(
                "UPDATE manga_import_jobs SET title=$2,status=$3,client_upload_id=$4,accepted_at=CASE WHEN status='failed' AND $3='queued' THEN clock_timestamp() ELSE accepted_at END,accepted_order=CASE WHEN status='failed' AND $3='queued' THEN nextval(pg_get_serial_sequence('manga_import_jobs','accepted_order')) ELSE accepted_order END,updated_at=clock_timestamp(),payload=$5::jsonb,result_items=CASE WHEN $6 THEN $7::jsonb ELSE result_items END WHERE id=$1",
                job["id"], job["title"], job["status"], job.get("clientUploadId"),
                _json_dump({key: value for key, value in job.items() if key != "items"}),
                "items" in job, _json_dump(job.get("items", [])),
            )
            if result != "UPDATE 1":
                raise MangaImportJobNotFound(job["id"])
            return job
        async with self._lock:
            path = self._record_path(job["id"])
            if not path.exists():
                raise MangaImportJobNotFound(job["id"])
            previous = await self.get(job["id"], include_items=False)
            if previous.get("status") == "failed" and job.get("status") == "queued":
                jobs = await self.list()
                job["acceptedAt"] = max(
                    _now_ms(), max((item.get("acceptedAt", 0) for item in jobs), default=0) + 1
                )
            await asyncio.to_thread(self._write_record, job)
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
            await asyncio.to_thread(self._write_record, job)
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
                job = await self.get(job_id, include_items=False)
                raise MangaImportJobConflict(f"Cannot dismiss a {job['status']} job")
            return
        async with self._lock:
            job = await self.get(job_id)
            if job.get("status") not in {"failed", "completed"}:
                raise MangaImportJobConflict(f"Cannot dismiss a {job['status']} job")
            await asyncio.to_thread(self._record_path(job_id).unlink)
            await asyncio.to_thread(self._details_path(job_id).unlink, missing_ok=True)

    async def cleanup_staging(self) -> None:
        lock_fd = await self._acquire_staging_lock()
        try:
            jobs = await self.list()
            retained = {job["id"] for job in jobs if job.get("status") == "failed"}
            retained.update(job["id"] for job in jobs if job.get("status") in {"queued", "processing"})

            def cleanup() -> None:
                self.staging_root.mkdir(parents=True, exist_ok=True)
                self.incoming_root.mkdir(parents=True, exist_ok=True)
                for path in self.staging_root.iterdir():
                    if path.name not in retained:
                        shutil.rmtree(path, ignore_errors=True)
                for path in self.incoming_root.iterdir():
                    if not path.is_dir():
                        continue
                    upload_lock = _lock_file(path / ".upload.lock", blocking=False)
                    if upload_lock is None:
                        continue
                    _unlock_file(upload_lock)
                    shutil.rmtree(path, ignore_errors=True)

            await asyncio.to_thread(cleanup)
        finally:
            await self._release_staging_lock(lock_fd)


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

    async def _wait_for_work_or_lease(self) -> None:
        if self._stopping:
            return
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=_WORKER_POLL_SECONDS)
        except TimeoutError:
            pass

    async def accept(self, job: dict[str, Any], uploads: list[Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        self.store.stage_path(job_id).parent.mkdir(parents=True, exist_ok=True)
        staging_lock = await self.store._acquire_staging_lock()
        try:
            self.store.incoming_root.mkdir(parents=True, exist_ok=True)
            temporary = Path(
                tempfile.mkdtemp(prefix=f"{job_id}-", dir=self.store.incoming_root)
            )
            upload_lock = _lock_file(temporary / ".upload.lock", blocking=True)
        finally:
            await self.store._release_staging_lock(staging_lock)

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

        staging_lock = None
        created = False
        create_started = False
        try:
            staged = await asyncio.to_thread(copy_uploads)
            staging_lock = await self.store._acquire_staging_lock()
            final_path = self.store.stage_path(job_id)
            await asyncio.to_thread(_unlock_file, upload_lock)
            upload_lock = None
            (temporary / ".upload.lock").unlink()
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
            create_started = self.store.database is not None
            await self.store.create(accepted)
            created = True
        except Exception as error:
            if staging_lock is None:
                staging_lock = await self.store._acquire_staging_lock()
            if upload_lock is not None:
                await asyncio.to_thread(_unlock_file, upload_lock)
                upload_lock = None
            if not created:
                shutil.rmtree(temporary, ignore_errors=True)
                if not create_started or isinstance(error, MangaImportJobConflict):
                    shutil.rmtree(self.store.stage_path(job_id), ignore_errors=True)
                # Otherwise preserve staging: an insert may commit after its
                # acknowledgement is lost. Startup reconciliation is safe later.
            raise
        finally:
            if staging_lock is not None:
                await self.store._release_staging_lock(staging_lock)
            if upload_lock is not None:
                await asyncio.to_thread(_unlock_file, upload_lock)
        self.wake()
        return accepted

    async def retry(self, job_id: str) -> dict[str, Any]:
        job = await self.store.get(job_id, include_items=False)
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
        from server.manga_import_job_lifecycle import run_manga_import_queue

        await run_manga_import_queue(self)
