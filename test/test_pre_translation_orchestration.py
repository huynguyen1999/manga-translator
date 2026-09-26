import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from manga_translator.config import Config
from manga_translator.manga_translator import MangaTranslator


def test_pre_translation_stage_order_is_stable(tmp_path):
    translator = MangaTranslator.__new__(MangaTranslator)
    translator.models_ttl = 1
    translator.device = "cpu"
    translator._model_cleanup_task = object()
    translator._pipeline_run = None
    translator._current_image_context = None
    translator.result_root = str(tmp_path)
    translator.verbose = False
    translator.ignore_errors = False
    translator.pre_dict = None
    events = []

    async def report(stage, *_args):
        events.append(f"progress:{stage}")

    async def detect(_config, _ctx):
        events.append("stage:detection")
        return [region], None, None

    async def ocr(_config, text_ctx):
        events.append("stage:ocr")
        return text_ctx.textlines

    async def merge(_config, text_ctx):
        events.append("stage:textline_merge")
        return text_ctx.textlines

    async def bubbles(_config, _ctx):
        events.append("stage:bubble_detection")

    region = SimpleNamespace(
        text="source",
        text_raw="source",
        translation="",
        xywh=[0, 0, 1, 1],
        lines=[],
        review_required=False,
    )
    translator._report_progress = report
    translator._log_memory_boundary = lambda *_args: None
    translator._result_path = lambda _name: str(tmp_path / "input.jpg")
    translator._run_detection = detect
    translator._run_ocr = ocr
    translator._run_textline_merge = merge
    translator._detect_speech_bubbles = bubbles

    with patch("manga_translator.manga_translator.save_jpeg"), patch(
        "manga_translator.pipeline.orchestrator.prepare_page_geometry",
        return_value=(None, None),
    ):
        ctx = asyncio.run(
            translator._translate_until_translation(Image.new("RGB", (2, 2)), Config())
        )

    assert ctx.text_regions == [region]
    assert events == [
        "progress:detection",
        "stage:detection",
        "progress:ocr",
        "stage:ocr",
        "progress:textline_merge",
        "stage:textline_merge",
        "stage:bubble_detection",
        "progress:awaiting_translation",
    ]
