from __future__ import annotations

import asyncio
from io import BytesIO
import logging
import inspect
import multiprocessing
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from PIL import Image
from starlette.testclient import TestClient

from server.api.routes.manga_import_jobs import create_manga_import_job_router
from server.api.routes.result_import import (
    create_manga_import_job_processor,
    create_result_import_router,
)
from server.manga_import_jobs import MangaImportJobQueue, MangaImportJobStore
from server.original_import_lock import _original_import_lock
from server.original_import import (
    is_archive_upload,
    iter_archive_pages,
    iter_original_upload_pages,
    validate_original_upload,
    write_original_import,
)


def _upload(name: str, content: bytes):
    return SimpleNamespace(filename=name, file=BytesIO(content))


def _job(title: str, name: str = "01.png"):
    return {
        "title": title,
        "mangaGroupId": None,
        "isNewGroup": None,
        "files": [{"sourcePath": name}],
        "fileCount": 1,
        "totalBytes": 3,
    }


def _create_job_with_client_id(root: str, barrier, results, job_id: str) -> None:
    async def create():
        store = MangaImportJobStore(root)
        barrier.wait(timeout=10)
        try:
            await store.create({
                "id": job_id,
                "title": "Same upload",
                "status": "queued",
                "clientUploadId": "shared-client-id",
            })
        except Exception as error:
            results.put(type(error).__name__)
        else:
            results.put("created")

    asyncio.run(create())


async def _wait_for_status(store: MangaImportJobStore, job_id: str, status: str):
    for _ in range(500):
        try:
            job = await store.get(job_id)
        except Exception:
            job = None
        if job and job["status"] == status:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not reach {status}")


class MangaImportJobQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="manga-import-jobs-")
        self.root = Path(self.temp_dir.name)
        self.store = MangaImportJobStore(self.root / "jobs")

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_files_and_state_survive_store_restart_and_processing_recovers(self):
        job = await MangaImportJobQueue(self.store, None, logging.getLogger(__name__)).accept(
            _job("Recovered"), [_upload("01.png", b"page-bytes")]
        )
        self.assertEqual(
            (self.store.stage_path(job["id"]) / "000000.upload").read_bytes(), b"page-bytes"
        )
        await self.store.claim_next()

        restarted_store = MangaImportJobStore(self.root / "jobs")
        resumed = asyncio.Event()

        async def processor(_job, staged_dir, _progress):
            self.assertEqual((staged_dir / "000000.upload").read_bytes(), b"page-bytes")
            resumed.set()
            return {"totalPages": 1, "items": [], "group": {"title": "Recovered"}}

        queue = MangaImportJobQueue(restarted_store, processor, logging.getLogger(__name__))
        await queue.start()
        await asyncio.wait_for(resumed.wait(), 2)
        completed = await _wait_for_status(restarted_store, job["id"], "completed")
        await queue.stop()

        self.assertEqual(completed["attempt"], 2)
        self.assertFalse(restarted_store.stage_path(job["id"]).exists())

    async def test_fifo_and_single_active_worker(self):
        queue_store = self.store
        first = await MangaImportJobQueue(queue_store, None, logging.getLogger(__name__)).accept(
            _job("First"), [_upload("01.png", b"one")]
        )
        second = await MangaImportJobQueue(queue_store, None, logging.getLogger(__name__)).accept(
            _job("Second"), [_upload("02.png", b"two")]
        )
        started = asyncio.Event()
        release_first = asyncio.Event()
        order = []
        active = 0
        max_active = 0

        async def processor(job, _staged_dir, _progress):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            order.append(job["id"])
            if job["id"] == first["id"]:
                started.set()
                await release_first.wait()
            active -= 1
            return {"totalPages": 1, "items": []}

        queue = MangaImportJobQueue(queue_store, processor, logging.getLogger(__name__))
        await queue.start()
        await asyncio.wait_for(started.wait(), 2)
        self.assertEqual((await queue_store.get(second["id"]))["status"], "queued")
        release_first.set()
        await _wait_for_status(queue_store, second["id"], "completed")
        await queue.stop()

        self.assertEqual(order, [first["id"], second["id"]])
        self.assertEqual(max_active, 1)

    async def test_failure_continues_and_retry_reuses_staging_as_new_acceptance(self):
        first = await MangaImportJobQueue(self.store, None, logging.getLogger(__name__)).accept(
            _job("Fails once"), [_upload("01.png", b"retry-me")]
        )
        second = await MangaImportJobQueue(self.store, None, logging.getLogger(__name__)).accept(
            _job("Continues"), [_upload("02.png", b"next")]
        )
        calls = []

        async def processor(job, _staged_dir, _progress):
            calls.append(job["id"])
            if job["id"] == first["id"] and calls.count(first["id"]) == 1:
                raise RuntimeError("first attempt failed")
            return {"totalPages": 1, "items": []}

        queue = MangaImportJobQueue(self.store, processor, logging.getLogger(__name__))
        await queue.start()
        failed = await _wait_for_status(self.store, first["id"], "failed")
        await _wait_for_status(self.store, second["id"], "completed")
        staged_path = self.store.stage_path(first["id"])
        self.assertTrue(staged_path.is_dir())
        self.assertEqual((staged_path / "000000.upload").read_bytes(), b"retry-me")

        old_acceptance = failed["acceptedAt"]
        retried = await queue.retry(first["id"])
        self.assertGreater(retried["acceptedAt"], old_acceptance)
        await _wait_for_status(self.store, first["id"], "completed")
        await queue.stop()

        self.assertEqual(calls, [first["id"], second["id"], first["id"]])
        self.assertFalse(staged_path.exists())

    async def test_import_wait_yields_to_other_event_loop_work(self):
        job = await MangaImportJobQueue(self.store, None, logging.getLogger(__name__)).accept(
            _job("Independent"), [_upload("01.png", b"page")]
        )
        importing = asyncio.Event()
        release = asyncio.Event()
        translation_completed = asyncio.Event()
        summary_completed = asyncio.Event()

        async def processor(_job, _staged_dir, _progress):
            importing.set()
            await release.wait()
            return {"totalPages": 1, "items": []}

        async def independent_task(done):
            done.set()

        queue = MangaImportJobQueue(self.store, processor, logging.getLogger(__name__))
        await queue.start()
        await asyncio.wait_for(importing.wait(), 2)
        await asyncio.gather(independent_task(translation_completed), independent_task(summary_completed))
        self.assertTrue(translation_completed.is_set())
        self.assertTrue(summary_completed.is_set())
        release.set()
        await _wait_for_status(self.store, job["id"], "completed")
        await queue.stop()

    async def test_worker_lease_protects_recovery_and_follower_polls_for_jobs(self):
        first = await MangaImportJobQueue(
            self.store, None, logging.getLogger(__name__)
        ).accept(_job("Recovered first"), [_upload("01.png", b"one")])
        await self.store.claim_next()

        follower_store = MangaImportJobStore(self.root / "jobs")
        owner_started = asyncio.Event()
        release_owner = asyncio.Event()
        owner_jobs = []
        follower_jobs = []

        async def owner_processor(job, _staged_dir, _progress):
            owner_jobs.append(job["id"])
            if job["id"] == first["id"]:
                owner_started.set()
                await release_owner.wait()
            return {"totalPages": 1, "items": []}

        async def follower_processor(job, _staged_dir, _progress):
            follower_jobs.append(job["id"])
            return {"totalPages": 1, "items": []}

        owner = MangaImportJobQueue(self.store, owner_processor, logging.getLogger(__name__))
        follower = MangaImportJobQueue(
            follower_store, follower_processor, logging.getLogger(__name__)
        )
        await owner.start()
        await asyncio.wait_for(owner_started.wait(), 2)
        await follower.start()

        still_processing = await follower_store.get(first["id"])
        self.assertEqual(still_processing["status"], "processing")
        self.assertEqual(still_processing["attempt"], 2)

        second = await follower.accept(
            _job("Accepted by follower"), [_upload("02.png", b"two")]
        )
        release_owner.set()
        await _wait_for_status(self.store, first["id"], "completed")
        await _wait_for_status(self.store, second["id"], "completed")
        self.assertEqual(owner_jobs, [first["id"], second["id"]])
        self.assertEqual(follower_jobs, [])

        await owner.stop()
        third = await follower.accept(
            _job("Follower acquired lease"), [_upload("03.png", b"three")]
        )
        await _wait_for_status(follower_store, third["id"], "completed")
        await follower.stop()
        self.assertEqual(follower_jobs, [third["id"]])

    async def test_completed_job_survives_staging_cleanup_error(self):
        logger = Mock()

        async def processor(_job, _staged_dir, _progress):
            return {"totalPages": 1, "items": []}

        queue = MangaImportJobQueue(self.store, processor, logger)
        job = await queue.accept(_job("Cleanup error"), [_upload("01.png", b"page")])
        with patch("server.manga_import_jobs.shutil.rmtree", side_effect=OSError("disk busy")):
            await queue.start()
            completed = await _wait_for_status(self.store, job["id"], "completed")
            await queue.stop()

        self.assertEqual(completed["status"], "completed")
        self.assertTrue(self.store.stage_path(job["id"]).is_dir())
        logger.exception.assert_called_once()

    async def test_client_upload_id_is_atomic_across_processes(self):
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(3)
        results = context.Queue()
        processes = [
            context.Process(
                target=_create_job_with_client_id,
                args=(str(self.root / "shared-jobs"), barrier, results, job_id),
            )
            for job_id in ("a" * 32, "b" * 32)
        ]
        for process in processes:
            process.start()
        barrier.wait(timeout=10)
        for process in processes:
            process.join(timeout=15)
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        self.assertCountEqual(
            [results.get(timeout=2), results.get(timeout=2)],
            ["created", "MangaImportJobConflict"],
        )
        records = await MangaImportJobStore(self.root / "shared-jobs").list()
        self.assertEqual(len(records), 1)


class IdempotentOriginalImportTests(unittest.TestCase):
    def test_legacy_import_router_factory_contract(self):
        self.assertEqual(
            tuple(inspect.signature(create_result_import_router).parameters),
            (
                "get_store", "get_result_root", "get_max_batch_items",
                "get_max_batch_item_bytes", "get_max_title_length",
                "has_file_backed_group", "manga_id", "write_original_import",
                "iter_original_upload_pages", "compact_file_backed_group",
                "write_file_backed_meta", "scan_manga_groups", "scan_results", "invalidate_meta_cache",
                "warm_preview_variants", "logger",
            ),
        )

    def test_reprocessing_same_job_reuses_page_folders(self):
        image_bytes = BytesIO()
        Image.new("RGB", (2, 2), color="red").save(image_bytes, format="PNG")
        content = image_bytes.getvalue()
        with tempfile.TemporaryDirectory(prefix="original-import-idempotency-") as directory:
            result_root = Path(directory)
            encoded = []

            def save_jpeg(image, path):
                encoded.append(Path(path).name)
                image.save(path, format="JPEG")

            def run():
                return write_original_import(
                    "Resumable",
                    [("01.png", "01.png", content, content, ".png")],
                    "manga-resumable",
                    result_root=result_root,
                    logger=logging.getLogger(__name__),
                    save_jpeg=save_jpeg,
                    import_job_id="a" * 32,
                )

            first = run()["records"]
            second = run()["records"]

            self.assertEqual([record["folder"] for record in first], [record["folder"] for record in second])
            self.assertEqual(len([path for path in result_root.iterdir() if path.is_dir()]), 1)
            self.assertEqual(second[0]["metadata"]["importJobId"], "a" * 32)
            folder = result_root / first[0]["folder"]
            self.assertEqual(encoded, ["input.jpg"])
            self.assertEqual((folder / "input.jpg").read_bytes(), (folder / "final.jpg").read_bytes())

    def test_job_resume_is_idempotent_and_other_new_title_is_rejected(self):
        image_bytes = BytesIO()
        Image.new("RGB", (2, 2), color="red").save(image_bytes, format="PNG")
        content = image_bytes.getvalue()

        with tempfile.TemporaryDirectory(prefix="original-import-job-processor-") as directory:
            root = Path(directory)
            result_root = root / "results"
            result_root.mkdir()
            logger = logging.getLogger(__name__)

            def pages(_root_or_title, title=None):
                title = title or _root_or_title
                found = []
                for folder in result_root.iterdir():
                    metadata_path = folder / "meta.json"
                    final_path = folder / "final.jpg"
                    if not folder.is_dir() or not final_path.is_file() or not metadata_path.is_file():
                        continue
                    import json
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if metadata.get("mangaTitle") == title:
                        found.append({"folder": folder.name, "path": folder, "meta": metadata})
                return found

            def upload_pages(uploads, source_paths=None):
                return iter_original_upload_pages(
                    uploads,
                    source_paths,
                    max_batch_items=10,
                    max_item_bytes=1024,
                    max_import_bytes=4096,
                    is_archive_upload=is_archive_upload,
                    iter_archive_pages=lambda source, name: iter_archive_pages(
                        source,
                        name,
                        max_item_bytes=1024,
                        natural_keys=lambda value: value,
                        validate_upload=lambda data, filename: validate_original_upload(
                            data, filename, supported_formats={"PNG": ".png"}, logger=logger
                        ),
                        logger=logger,
                    ),
                    validate_upload=lambda data, filename: validate_original_upload(
                        data, filename, supported_formats={"PNG": ".png"}, logger=logger
                    ),
                )

            def writer(title, source_pages, group_id, *, import_job_id):
                return write_original_import(
                    title,
                    source_pages,
                    group_id,
                    result_root=result_root,
                    logger=logger,
                    save_jpeg=lambda image, path: image.save(path, format="JPEG"),
                    import_job_id=import_job_id,
                )

            def write_meta(_root, folder, metadata):
                import json
                (result_root / folder / "meta.json").write_text(
                    json.dumps(metadata), encoding="utf-8"
                )

            def scan_groups(_root, _limit, _offset, _manga_id, search, _sort):
                matching = pages(search)
                groups = ([{"id": f"manga-{search}", "title": search, "count": len(matching)}]
                          if matching else [])
                return {"groups": groups, "totalImages": len(matching)}

            def scan_results(_root, _sort, manga, _detail, limit, _offset, _review):
                matching = pages(manga)[:limit]
                return {"items": [{"id": page["folder"], "originalName": page["meta"]["originalName"]}
                                   for page in matching]}

            import_dependencies = (
                lambda: None,
                lambda: result_root,
                lambda: 10,
                lambda: 1024,
                lambda: 120,
                lambda title: bool(pages(title)),
                lambda title: f"manga-{title}",
                writer,
                upload_pages,
                lambda _root, title, excluded: [page for page in pages(title) if page["folder"] not in excluded],
                write_meta,
                scan_groups,
                scan_results,
                lambda: None,
                lambda _folders: None,
                logger,
            )
            router, exports = create_result_import_router(*import_dependencies)
            self.assertEqual(len(exports), 1)
            self.assertEqual(exports[0].__name__, "import_original_manga")
            self.assertIn("/api/results/import", {route.path for route in router.routes})
            processor_dependencies = (*import_dependencies[:10], pages, *import_dependencies[10:])
            process = create_manga_import_job_processor(
                *processor_dependencies[:2], *processor_dependencies[5:]
            )

            async def run():
                async def no_progress(*_args, **_kwargs):
                    pass

                first_id = "a" * 32
                staged = root / "staged" / first_id
                staged.mkdir(parents=True)
                (staged / "000000.upload").write_bytes(content)
                job = {
                    "id": first_id,
                    "title": "Duplicate Guard",
                    "mangaGroupId": None,
                    "isNewGroup": None,
                    "files": [{"filename": "01.png", "sourcePath": "01.png"}],
                }
                await _original_import_lock.acquire()
                locked_import = asyncio.create_task(process(job, staged, no_progress))
                try:
                    await asyncio.sleep(0)
                    self.assertFalse(locked_import.done())
                finally:
                    _original_import_lock.release()
                result = await locked_import
                await process(job, staged, no_progress)
                self.assertEqual(result["totalPages"], 1)
                self.assertEqual(len(pages("Duplicate Guard")), 1)

                other = dict(job, id="b" * 32)
                other_staged = root / "staged" / other["id"]
                other_staged.mkdir(parents=True)
                (other_staged / "000000.upload").write_bytes(content)
                with self.assertRaises(HTTPException) as raised:
                    await process(other, other_staged, no_progress)
                self.assertEqual(raised.exception.status_code, 409)

            # This synchronous test opens a new loop after the middleware test bound
            # the module lock to its isolated loop.
            with patch(
                "server.api.routes.original_import_routes._original_import_lock",
                asyncio.Lock(),
            ):
                asyncio.run(run())


class MangaImportJobApiTests(unittest.TestCase):
    def test_multipart_acceptance_status_retry_and_dismissal(self):
        with tempfile.TemporaryDirectory(prefix="manga-import-api-") as directory:
            store = MangaImportJobStore(Path(directory) / "jobs")
            started = threading.Event()
            release = threading.Event()

            async def processor(job, staged_dir, _progress):
                self.assertEqual((staged_dir / "000000.upload").read_bytes(), b"image bytes")
                started.set()
                if job["attempt"] == 1:
                    await asyncio.to_thread(release.wait)
                    raise RuntimeError("retry available")
                return {
                    "totalPages": 1,
                    "group": {"id": "manga-api", "title": job["title"]},
                    "items": [{"id": "page-1", "originalName": "01.png"}],
                    "totalImages": 1,
                }

            queue = MangaImportJobQueue(store, processor, logging.getLogger(__name__))

            @asynccontextmanager
            async def lifespan(_app):
                await queue.start()
                try:
                    yield
                finally:
                    release.set()
                    await queue.stop()

            app = FastAPI(lifespan=lifespan)
            router, _ = create_manga_import_job_router(
                lambda: queue,
                lambda: 10,
                lambda: 1024,
                lambda: 4096,
                lambda: 120,
            )
            app.include_router(router)

            with TestClient(app) as client:
                response = client.post(
                    "/api/manga-import-jobs",
                    data={
                        "mangaTitle": "Queued Series",
                        "mangaGroupId": "group-existing",
                        "isNewGroup": "false",
                        "clientUploadId": "studio-draft-123",
                        "pageMetadata": '[{"sourcePath":"volume/01.png"}]',
                    },
                    files=[("files", ("01.png", b"image bytes", "image/png"))],
                )
                self.assertEqual(response.status_code, 202, response.text)
                accepted = response.json()["job"]
                self.assertEqual(accepted["status"], "queued")
                self.assertEqual(accepted["title"], "Queued Series")
                self.assertEqual(accepted["fileCount"], 1)

                self.assertTrue(started.wait(timeout=2))
                processing = client.get(f"/api/manga-import-jobs/{accepted['id']}").json()["job"]
                self.assertEqual(processing["status"], "processing")

                duplicate = client.post(
                    "/api/manga-import-jobs",
                    data={"mangaTitle": "Queued Series", "clientUploadId": "studio-draft-123"},
                    files=[("files", ("01.png", b"image bytes", "image/png"))],
                )
                self.assertEqual(duplicate.status_code, 202)
                self.assertEqual(duplicate.json()["job"]["id"], accepted["id"])
                self.assertEqual(len(client.get("/api/manga-import-jobs").json()["jobs"]), 1)

                release.set()
                failed = _poll_job(client, accepted["id"], "failed")
                self.assertEqual(failed["error"], "retry available")
                staged_path = store.stage_path(accepted["id"])
                self.assertTrue(staged_path.is_dir())
                self.assertEqual((staged_path / "000000.upload").read_bytes(), b"image bytes")

                retried = client.post(f"/api/manga-import-jobs/{accepted['id']}/retry")
                self.assertEqual(retried.status_code, 200, retried.text)
                self.assertEqual(retried.json()["job"]["status"], "queued")
                completed = _poll_job(client, accepted["id"], "completed")
                self.assertEqual(completed["groupId"], "manga-api")
                self.assertEqual(completed["group"]["title"], "Queued Series")
                self.assertEqual(completed["items"][0]["id"], "page-1")
                self.assertEqual(completed["processedPages"], 1)
                listed = client.get("/api/manga-import-jobs").json()["jobs"]
                self.assertEqual(listed[0]["id"], accepted["id"])

                dismissed = client.delete(f"/api/manga-import-jobs/{accepted['id']}")
                self.assertEqual(dismissed.status_code, 200)
                self.assertFalse(staged_path.exists())
                self.assertEqual(client.get("/api/manga-import-jobs").json()["jobs"], [])


def _poll_job(client: TestClient, job_id: str, status: str):
    for _ in range(200):
        response = client.get(f"/api/manga-import-jobs/{job_id}")
        if response.status_code == 200 and response.json()["job"]["status"] == status:
            return response.json()["job"]
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not reach {status}")


if __name__ == "__main__":
    unittest.main()
