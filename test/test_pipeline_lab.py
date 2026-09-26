import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
from PIL import Image

from manga_translator.config import Config
from manga_translator.manga_translator import MangaTranslator
from manga_translator.pipeline.cpu import CPU_PRIORITY_BACKGROUND
from manga_translator.pipeline.run import PipelineRun, STAGES, deserialize_textblocks, serialize_regions, set_document_saver
from manga_translator.utils import Context, Quadrilateral, TextBlock


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


def test_pipeline_run_manifest_tracks_partial_run(tmp_path):
    class Config:
        original_name = "page.png"

        def dict(self):
            return {"original_name": self.original_name}

    run = PipelineRun(tmp_path, "run-1", Image.new("RGB", (12, 8)), Config())
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


def test_pipeline_run_keeps_independent_stages_pending_until_finished(tmp_path):
    run = PipelineRun(tmp_path, "run-1", Image.new("RGB", (4, 4)), Config())

    run.progress("inpainting")
    stages = {stage["id"]: stage for stage in run.manifest["stages"]}
    assert stages["inpainting"]["status"] == "running"
    assert stages["translation"]["status"] == "pending"
    assert stages["layout"]["status"] == "pending"

    run.progress("translating")
    stages = {stage["id"]: stage for stage in run.manifest["stages"]}
    assert stages["inpainting"]["status"] == "completed"
    assert stages["translation"]["status"] == "running"
    assert stages["layout"]["status"] == "pending"


def test_pipeline_run_checkpoints_json_without_sidecars(tmp_path):
    saved = {}

    async def save(folder, documents):
        saved[folder] = documents

    set_document_saver(save)
    run = PipelineRun(tmp_path, "run-1", Image.new("RGB", (4, 4)), Config())
    run.write_json("ocr.json", [{"text": "hello"}])
    asyncio.run(run.checkpoint())

    assert saved["run-1"]["ocr.json"] == [{"text": "hello"}]
    assert saved["run-1"]["pipeline_manifest.json"]["folder"] == "run-1"
    assert list((tmp_path / "run-1").glob("*.json")) == []
    set_document_saver(_save_documents)


def test_pipeline_render_checkpoints_manga_metadata(tmp_path):
    config = Config(
        manga_title="New manga",
        manga_group_id="group-id",
        original_name="page.png",
        page_order=1,
    )
    image = Image.new("RGB", (8, 8))
    run = PipelineRun(tmp_path, "page", image, config)
    run.ctx = Context(
        input=image,
        img_inpainted=np.zeros((8, 8, 3), dtype=np.uint8),
        text_regions=[],
    )
    translator = MangaTranslator.__new__(MangaTranslator)
    translator._current_image_context = None
    translator._pipeline_run = run
    translator.font_path = None
    translator._run_text_rendering = AsyncMock(return_value=np.zeros((8, 8, 3), dtype=np.uint8))

    asyncio.run(run.retry_stage("rendering", config, translator))

    assert run.documents["meta.json"]["mangaTitle"] == "New manga"
    assert run.documents["meta.json"]["mangaGroupId"] == "group-id"


def test_pipeline_ocr_retry_persists_precomputed_batch_result(tmp_path):
    config = Config()
    image = Image.new("RGB", (8, 8))
    region = Quadrilateral(
        np.array([[1, 1], [5, 1], [5, 5], [1, 5]]), "source", 1.0
    )
    run = PipelineRun(tmp_path, "run-ocr", image, config)
    run.ctx = Context(
        input=image,
        upscaled=image,
        img_rgb=np.zeros((8, 8, 3), dtype=np.uint8),
        textlines=[region],
    )
    translator = SimpleNamespace(_run_ocr=AsyncMock(side_effect=AssertionError("reran OCR")))

    asyncio.run(run.retry_stage(
        "ocr", config, translator, precomputed_ocr=[region]
    ))

    assert translator._run_ocr.await_count == 0
    assert run.documents["ocr.json"][0]["text"] == "source"


def test_pipeline_ocr_retry_skips_when_detection_has_no_textlines(tmp_path):
    config = Config()
    image = Image.new("RGB", (8, 8))
    run = PipelineRun(tmp_path, "run-empty-ocr", image, config)
    run.ctx = Context(
        input=image,
        upscaled=image,
        img_rgb=np.zeros((8, 8, 3), dtype=np.uint8),
        textlines=[],
    )
    run.documents["detection.json"] = []
    translator = SimpleNamespace(_run_ocr=AsyncMock(side_effect=AssertionError("ran OCR without textlines")))

    asyncio.run(run.retry_stage("ocr", config, translator))

    assert run.documents["ocr.json"] == []
    assert run._stage("ocr")["status"] == "completed"
    translator._run_ocr.assert_not_awaited()


def test_pipeline_upscale_retry_persists_precomputed_batch_result(tmp_path):
    config = Config()
    image = Image.new("RGB", (8, 8))
    run = PipelineRun(tmp_path, "run-upscale", image, config)
    run.ctx = Context(input=image, img_colorized=image)
    output = Image.new("RGB", (16, 16), color="red")
    translator = SimpleNamespace(_run_upscaling=AsyncMock())

    async def execute():
        await run.begin_stage("upscaling", config)
        await run.retry_stage(
            "upscaling",
            config,
            translator,
            precomputed_upscale=output,
            stage_already_running=True,
        )

    asyncio.run(execute())

    assert run._stage("upscaling")["status"] == "completed"
    assert run._stage("upscaling")["durationMs"] >= 0
    assert (run.path / "upscaled.png").is_file()
    translator._run_upscaling.assert_not_awaited()


def test_pipeline_layout_retry_uses_background_cpu_lane(tmp_path):
    run = PipelineRun(tmp_path, "run-layout", Image.new("RGB", (8, 8)), Config())
    run.ctx = Context(text_regions=[SimpleNamespace(translation="")])
    run.checkpoint = AsyncMock()
    translator = SimpleNamespace(font_path=None)

    async def execute():
        with patch("manga_translator.pipeline.run.run_cpu_stage", new_callable=AsyncMock) as run_cpu:
            await run.retry_stage("layout", Config(), translator)
        return run_cpu

    run_cpu = asyncio.run(execute())

    assert run_cpu.await_args.kwargs["priority"] == CPU_PRIORITY_BACKGROUND


def test_pipeline_run_creates_manifest_without_global_verbose(tmp_path, monkeypatch):
    translator = MangaTranslator.__new__(MangaTranslator)
    translator.verbose = False
    translator._pipeline_run = None
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

    config = Config()
    asyncio.run(translator.translate(Image.new("RGB", (4, 4)), config))

    folders = list((tmp_path / "result").iterdir())
    assert len(folders) == 1
    assert not (folders[0] / "pipeline_manifest.json").exists()


def test_pipeline_run_publishes_folder_after_input_artifact(tmp_path, monkeypatch):
    translator = MangaTranslator.__new__(MangaTranslator)
    translator.verbose = False
    translator._pipeline_run = None
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

    def write(*_args, **_kwargs):
        events.append("input_written")
        return True

    translator._translate = lambda _config, _ctx: _empty_context()
    translator._report_progress = report
    translator._async_imwrite = write
    monkeypatch.setattr("manga_translator.manga_translator.save_jpeg", write)
    monkeypatch.setattr("manga_translator.manga_translator.BASE_PATH", str(tmp_path))

    asyncio.run(translator.translate(Image.new("RGB", (4, 4)), Config()))

    assert events.index("input_written") < events.index(next(state for state in events if state.startswith("debug_folder:")))


def test_pipeline_run_releases_runtime_references():
    run = PipelineRun.__new__(PipelineRun)
    translator = type("Translator", (), {})()
    run.ctx = object()
    run.translator = translator
    translator._pipeline_run = run

    run.release_runtime()

    assert run.ctx is None
    assert run.translator is None
    assert translator._pipeline_run is None


async def _empty_context():
    return Context(text_regions=[])


def test_retry_from_stage_uses_dependency_closure(tmp_path):
    run = PipelineRun.__new__(PipelineRun)
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
    with patch("manga_translator.pipeline.run._now", return_value="2026-09-16T02:03:04+00:00"):
        result = asyncio.run(run.retry_from_stage("translation", Config(), object()))

    assert calls == ["translation", "layout", "rendering"]
    assert result["stage"] == "rendering"
    assert run.manifest["status"] == "completed"
    assert run.manifest["createdAt"] == "2026-09-16T02:03:04+00:00"


def test_ocr_retry_reuses_bubble_detection_but_rebuilds_dependents():
    run = PipelineRun.__new__(PipelineRun)
    run.manifest = {
        "status": "failed",
        "stages": [
            {"id": stage_id, "status": "failed" if stage_id == "ocr" else "completed"}
            for stage_id, _ in STAGES
        ],
    }
    run.started = {}
    run.active_stage = None
    run._write = lambda: None
    calls = []

    async def retry_stage(stage_id, _config, _translator):
        calls.append(stage_id)
        return {"status": "ok", "stage": stage_id}

    run.retry_stage = AsyncMock(side_effect=retry_stage)
    asyncio.run(run.retry_from_stage("ocr", Config(), object()))

    assert calls == [
        "ocr", "textline_merge", "translation", "mask_generation",
        "layout", "inpainting", "rendering",
    ]


def test_textline_retry_reuses_saved_bubble_geometry(tmp_path):
    run = PipelineRun(tmp_path, "page", Image.new("RGB", (20, 20)), Config())
    run.ctx = Context(
        img_rgb=np.zeros((20, 20, 3), dtype=np.uint8),
        textlines=[TextBlock([[[5, 5], [10, 5], [10, 10], [5, 10]]], texts=["hello"])],
    )
    run.documents["bubble_detections.json"] = [{
        "polygons": [[[0, 0], [19, 0], [19, 19], [0, 19]]],
    }]
    run.checkpoint = AsyncMock()

    region = TextBlock([[[5, 5], [10, 5], [10, 10], [5, 10]]], texts=["hello"])
    translator = type("Translator", (), {})()
    translator._run_textline_merge = AsyncMock(return_value=[region])
    translator._detect_speech_bubbles = AsyncMock()

    asyncio.run(run.retry_stage("textline_merge", Config(), translator))

    assert translator._detect_speech_bubbles.await_count == 0
    assert run.ctx._bubble_detection_done
    assert len(run.ctx.text_regions) == 1
    assert run.ctx.text_regions[0]._bubble_mask.any()


def test_old_manifests_gain_canonical_layout_checkpoint(tmp_path):
    manifest = {
        "stages": [
            {"id": stage_id, "label": label, "status": "completed"}
            for stage_id, label in STAGES if stage_id != "layout"
        ]
    }

    run = PipelineRun.from_documents(
        tmp_path, "page", {"pipeline_manifest.json": manifest}
    )

    stage_ids = [stage["id"] for stage in run.manifest["stages"]]
    assert stage_ids.index("translation") < stage_ids.index("mask_generation")
    assert stage_ids.index("mask_generation") < stage_ids.index("layout")
    assert run._stage("layout")["status"] == "pending"
