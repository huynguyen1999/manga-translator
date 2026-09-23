from manga_translator.pipeline.stages import (
    PipelineStage,
    ResourceClass,
    STAGE_DEPENDENCIES,
    STAGE_ORDER,
    STAGE_RESOURCES,
    downstream_stages,
    fingerprint,
    settings_for_stage,
    stages_affected_by_settings,
    stage_from_progress,
)
from server.pipeline_rerun import PipelineRerunMode, resolve_rerun_plan


def test_layout_retry_restores_persisted_mask(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import cv2
    import numpy as np
    from PIL import Image

    from manga_translator import Config, Context
    from manga_translator.pipeline.run import PipelineRun

    config = Config()
    with Image.new("RGB", (2, 2)) as image:
        run = PipelineRun(tmp_path, "page", image, config)
    expected_mask = np.array([[0, 255], [255, 0]], dtype=np.uint8)
    assert cv2.imwrite(str(run.path / "mask_final.png"), expected_mask)
    ctx = Context()
    ctx.img_rgb = np.zeros((2, 2, 3), dtype=np.uint8)
    ctx.text_regions = [SimpleNamespace()]
    run.ctx = ctx
    observed_masks = []

    async def capture_layout(_layout, stage_ctx, *_args, **_kwargs):
        observed_masks.append(stage_ctx.inpaint_mask.copy())

    monkeypatch.setattr("manga_translator.pipeline.run.run_cpu_stage", capture_layout)
    asyncio.run(run.retry_stage("layout", config, SimpleNamespace(font_path=None)))

    assert np.array_equal(observed_masks[0], expected_mask)


def test_translation_retry_preserves_inpainting_checkpoint():
    assert downstream_stages(PipelineStage.TRANSLATION) == (
        PipelineStage.TRANSLATION,
        PipelineStage.LAYOUT,
        PipelineStage.RENDERING,
        PipelineStage.FINALIZE,
    )


def test_ocr_invalidation_reaches_text_dependent_stages():
    affected = downstream_stages("ocr")
    assert PipelineStage.TEXT_GROUPING in affected
    assert PipelineStage.MASK_GENERATION in affected
    assert PipelineStage.LAYOUT in affected
    assert PipelineStage.INPAINTING in affected
    assert PipelineStage.RENDERING in affected


def test_layout_depends_on_final_mask():
    assert PipelineStage.MASK_GENERATION in STAGE_DEPENDENCIES[PipelineStage.LAYOUT]


def test_stage_graph_and_resources_cover_each_stage():
    assert set(STAGE_DEPENDENCIES) == set(STAGE_ORDER)
    assert set(STAGE_RESOURCES) == set(STAGE_ORDER)
    positions = {stage: index for index, stage in enumerate(STAGE_ORDER)}
    assert all(
        positions[dependency] < positions[stage]
        for stage, dependencies in STAGE_DEPENDENCIES.items()
        for dependency in dependencies
    )
    assert STAGE_RESOURCES[PipelineStage.OCR] is ResourceClass.GPU
    assert STAGE_RESOURCES[PipelineStage.TRANSLATION] is ResourceClass.NETWORK
    assert PipelineStage.COLORIZATION in downstream_stages(PipelineStage.INPUT)
    assert PipelineStage.COLORIZATION in stages_affected_by_settings(["colorizer"])
    assert PipelineStage.COLORIZATION in stages_affected_by_settings(["colorThreshold"])


def test_setting_invalidation_uses_shared_dependencies():
    font_changes = stages_affected_by_settings(["renderFont"])
    assert font_changes == (
        PipelineStage.LAYOUT,
        PipelineStage.RENDERING,
        PipelineStage.FINALIZE,
    )
    assert PipelineStage.INPAINTING not in font_changes
    assert PipelineStage.OCR in stages_affected_by_settings(["customOcrProb"])
    bubble_settings = {"bubble_detection": {"enabled": True, "group_regions": False}}
    assert settings_for_stage(bubble_settings, PipelineStage.BUBBLE_DETECTION) == {
        "bubble_detection": {"enabled": True}
    }
    assert settings_for_stage(bubble_settings, PipelineStage.TEXT_GROUPING) == {
        "bubble_detection": {"group_regions": False}
    }


def test_fingerprint_is_stable_for_equivalent_json_objects():
    assert fingerprint({"a": 1, "b": [2, 3]}) == fingerprint({"b": [2, 3], "a": 1})


def test_rerun_presets_invalidate_only_dependency_closure():
    translation = resolve_rerun_plan(PipelineRerunMode.TRANSLATION_TYPESETTING)
    assert translation.stages_to_invalidate == (
        PipelineStage.TRANSLATION,
        PipelineStage.LAYOUT,
        PipelineStage.RENDERING,
        PipelineStage.FINALIZE,
    )
    assert PipelineStage.INPAINTING not in translation.stages_to_invalidate

    typesetting = resolve_rerun_plan(PipelineRerunMode.TYPESETTING)
    assert PipelineStage.TRANSLATION not in typesetting.stages_to_invalidate


def test_translator_progress_maps_to_canonical_ids():
    assert stage_from_progress("colorizing") is PipelineStage.COLORIZATION
    assert stage_from_progress("textline_merge") is PipelineStage.TEXT_GROUPING
    assert stage_from_progress("editing:Story 1/2") is PipelineStage.TRANSLATION
    assert stage_from_progress("debug_folder:page") is None
