from manga_translator.pipeline.stages import PipelineStage, STAGE_ORDER


def test_pipeline_stage_order_remains_stable():
    assert [stage.value for stage in STAGE_ORDER] == [
        "input",
        "colorization",
        "upscale",
        "detection",
        "ocr",
        "bubble_detection",
        "text_grouping",
        "translation",
        "mask_generation",
        "layout",
        "inpainting",
        "rendering",
        "finalize",
    ]
    assert STAGE_ORDER == tuple(PipelineStage)
