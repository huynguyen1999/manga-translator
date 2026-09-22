import asyncio
from unittest.mock import AsyncMock, patch

from PIL import Image

from manga_translator.config import Config, PipelineLabConfig
from manga_translator.manga_translator import MangaTranslator
from manga_translator.pipeline_lab import PipelineLabRun, STAGES, deserialize_textblocks, serialize_regions, set_document_saver
from manga_translator.utils import Context, TextBlock


async def _save_documents(_folder, _documents):
    pass


set_document_saver(_save_documents)


def test_grouped_region_metadata_survives_batch_serialization():
    region = TextBlock([[[0, 0], [2, 0], [2, 2], [0, 2]]], texts=["source"])
    region.region_id = "stable"
    region.group_id = "bubble_1"
    region.group_members = ["a", "b"]
    region.bubble_bounds = [0, 0, 3, 3]

    restored = deserialize_textblocks(serialize_regions([region]))[0]

    assert restored.region_id == "stable"
    assert restored.group_id == "bubble_1"
    assert restored.group_members == ["a", "b"]
    assert restored.bubble_bounds == [0, 0, 3, 3]


def test_pipeline_lab_manifest_tracks_partial_run(tmp_path):
    class Lab:
        enabled = True
        stage_plan = {"colorization": True, "upscaling": False}

    class Config:
        original_name = "page.png"
        pipeline_lab = Lab()

        def dict(self):
            return {"original_name": self.original_name}

    run = PipelineLabRun(tmp_path, "run-1", Image.new("RGB", (12, 8)), Config())
    (tmp_path / "run-1" / "input.png").write_bytes(b"input")
    run.refresh()
    run.progress("colorizing")
    (tmp_path / "run-1" / "colorized.png").write_bytes(b"colorized")
    run.progress("skip-no-regions", True)
    run.progress("finished", True)

    manifest = run.manifest
    stages = {stage["id"]: stage for stage in manifest["stages"]}
    assert manifest["status"] == "partial"
    assert stages["input"]["artifacts"] == ["input.png"]
    assert stages["colorization"]["artifacts"] == ["colorized.png"]
    assert stages["detection"]["status"] == "unavailable"
    assert stages["upscaling"]["status"] == "skipped"
    assert stages["bubble_detection"]["status"] == "unavailable"


def test_pipeline_lab_checkpoints_json_without_sidecars(tmp_path):
    saved = {}

    async def save(folder, documents):
        saved[folder] = documents

    set_document_saver(save)
    run = PipelineLabRun(tmp_path, "run-1", Image.new("RGB", (4, 4)), Config())
    run.write_json("ocr.json", [{"text": "hello"}])
    asyncio.run(run.checkpoint())

    assert saved["run-1"]["ocr.json"] == [{"text": "hello"}]
    assert saved["run-1"]["pipeline_manifest.json"]["folder"] == "run-1"
    assert list((tmp_path / "run-1").glob("*.json")) == []
    set_document_saver(_save_documents)


def test_pipeline_lab_creates_manifest_without_global_verbose(tmp_path, monkeypatch):
    translator = MangaTranslator.__new__(MangaTranslator)
    translator.verbose = False
    translator._pipeline_lab_run = None
    translator._progress_hooks = []
    translator._current_image_context = None
    translator._saved_image_contexts = {}
    translator.result_sub_folder = ""
    translator.all_page_translations = []
    translator._original_page_texts = []
    translator._is_streaming_mode = True
    translator._translate = lambda _config, _ctx: _empty_context()
    translator._report_progress = lambda *_args, **_kwargs: asyncio.sleep(0)
    translator._async_imwrite = lambda *_args, **_kwargs: asyncio.sleep(0, result=True)
    monkeypatch.setattr("manga_translator.manga_translator.BASE_PATH", str(tmp_path))

    config = Config(pipeline_lab=PipelineLabConfig(enabled=True))
    asyncio.run(translator.translate(Image.new("RGB", (4, 4)), config))

    folders = list((tmp_path / "result").iterdir())
    assert len(folders) == 1
    assert not (folders[0] / "pipeline_manifest.json").exists()


def test_pipeline_lab_publishes_folder_after_input_artifact(tmp_path, monkeypatch):
    translator = MangaTranslator.__new__(MangaTranslator)
    translator.verbose = False
    translator._pipeline_lab_run = None
    translator._progress_hooks = []
    translator._current_image_context = None
    translator._saved_image_contexts = {}
    translator.result_sub_folder = ""
    translator.all_page_translations = []
    translator._original_page_texts = []
    translator._is_streaming_mode = True
    events = []

    async def report(state, *_args, **_kwargs):
        events.append(state)

    async def write(*_args, **_kwargs):
        events.append("input_written")
        return True

    translator._translate = lambda _config, _ctx: _empty_context()
    translator._report_progress = report
    translator._async_imwrite = write
    monkeypatch.setattr("manga_translator.manga_translator.BASE_PATH", str(tmp_path))

    asyncio.run(translator.translate(Image.new("RGB", (4, 4)), Config(pipeline_lab=PipelineLabConfig(enabled=True))))

    assert events.index("input_written") < events.index(next(state for state in events if state.startswith("debug_folder:")))


def test_pipeline_lab_releases_runtime_references():
    run = PipelineLabRun.__new__(PipelineLabRun)
    translator = type("Translator", (), {})()
    run.ctx = object()
    run.translator = translator
    translator._pipeline_lab_run = run

    run.release_runtime()

    assert run.ctx is None
    assert run.translator is None
    assert translator._pipeline_lab_run is None


async def _empty_context():
    return Context(text_regions=[])


def test_manual_pipeline_waits_for_continue(tmp_path):
    class Lab:
        enabled = True
        manual = True
        stage_plan = {"colorization": True}

    class Config:
        original_name = "page.png"
        pipeline_lab = Lab()

        def dict(self):
            return {"pipeline_lab": {"manual": True}}

    run = PipelineLabRun(tmp_path, "run-1", Image.new("RGB", (4, 4)), Config())

    async def scenario():
        notifications = []

        async def notify(state, _finished):
            notifications.append(state)

        waiter = asyncio.create_task(run.wait_for_continue("colorization", notify))
        await asyncio.sleep(0)
        manifest = run.manifest
        assert manifest["status"] == "paused"
        assert manifest["waitingFor"] == "colorization"
        assert notifications == ["manual_wait:colorization"]
        run.request_continue()
        await waiter
        assert run.manifest["status"] == "running"

    asyncio.run(scenario())


def test_retry_from_stage_runs_only_failed_stage_and_following_stages(tmp_path):
    run = PipelineLabRun.__new__(PipelineLabRun)
    run.manifest = {
        "status": "failed",
        "createdAt": "2026-09-16T01:02:03+00:00",
        "stages": [
            {"id": stage_id, "status": "failed" if stage_id == "translation" else "unavailable"}
            for stage_id, _ in STAGES
        ],
    }
    run.started = {}
    run.active_stage = None
    run._write = lambda: None
    calls = []

    async def retry_stage(stage_id, _config, _translator):
        calls.append(stage_id)
        run._stage(stage_id)["status"] = "completed"
        return {"status": "ok", "stage": stage_id, "manifest": run.manifest}

    run.retry_stage = AsyncMock(side_effect=retry_stage)
    with patch("manga_translator.pipeline_lab._now", return_value="2026-09-16T02:03:04+00:00"):
        result = asyncio.run(run.retry_from_stage("translation", Config(), object()))

    assert calls == ["translation", "rendering"]
    assert result["stage"] == "rendering"
    assert run.manifest["status"] == "completed"
    assert run.manifest["createdAt"] == "2026-09-16T02:03:04+00:00"
