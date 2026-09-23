import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import torch
from PIL import Image

from manga_translator.upscaling.esrgan_pytorch import ESRGANUpscalerPytorch


class FakeUpscaler(torch.nn.Module):
    def forward(self, batch):
        return torch.nn.functional.interpolate(batch, scale_factor=4, mode="nearest")


def test_ultrasharp_tiles_large_images():
    upscaler = ESRGANUpscalerPytorch.__new__(ESRGANUpscalerPytorch)
    upscaler.model = FakeUpscaler()
    upscaler.device = "cpu"
    upscaler.scale = 4

    result = asyncio.run(upscaler._infer([Image.new("RGB", (600, 700))], 2))

    assert result[0].size == (1200, 1400)


def test_revert_upscale_skipped_when_upscaling_deactivated():
    from manga_translator.manga_translator import MangaTranslator
    from manga_translator.config import Config
    from manga_translator.utils import Context

    translator = MangaTranslator.__new__(MangaTranslator)
    translator.verbose = False
    translator.result_sub_folder = None
    translator._pipeline_run = None

    reported_progress = []
    async def report_progress(state, finished=False):
        reported_progress.append(state)
    translator._report_progress = report_progress

    img_input = Image.new("RGB", (100, 100))
    img_result = Image.new("RGB", (200, 200))
    ctx = Context(input=img_input, result=img_result, upscaled_ran=False)

    # When upscale_ratio is None (deactivated)
    config = Config()
    config.upscale.upscale_ratio = None
    config.upscale.revert_upscaling = True

    res = asyncio.run(translator._revert_upscale(config, ctx))
    assert res.result.size == (200, 200)
    assert "downscaling" not in reported_progress

    # When upscale_ratio is active, revert_upscaling is True, and upscaled_ran is True
    ctx_active = Context(input=img_input, result=img_result, upscaled_ran=True)
    config_active = Config()
    config_active.upscale.upscale_ratio = 2
    config_active.upscale.revert_upscaling = True

    res_active = asyncio.run(translator._revert_upscale(config_active, ctx_active))
    assert res_active.result.size == (100, 100)
    assert "downscaling" in reported_progress


def test_revert_upscale_accepts_checkpoint_context_without_result_documents(tmp_path):
    from manga_translator.manga_translator import MangaTranslator
    from manga_translator.config import Config
    from manga_translator.utils import Context

    translator = MangaTranslator.__new__(MangaTranslator)
    translator.verbose = False
    translator.result_sub_folder = ""
    translator.result_root = str(tmp_path)
    translator._pipeline_run = SimpleNamespace(
        documents={}, manifest={"createdAt": "2026-09-23T00:00:00Z"}, checkpoint=AsyncMock()
    )
    translator._current_image_context = {
        "subfolder": "page-1",
        "original_name": "1.webp",
        "manga_title": "test",
    }
    translator._progress_hooks = []
    translator._report_progress = AsyncMock()
    translator._get_image_subfolder = lambda: "page-1"

    def result_path(name):
        path = Path(tmp_path) / "page-1" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)

    translator._result_path = result_path
    ctx = Context(input=Image.new("RGB", (2, 2)), result=Image.new("RGB", (2, 2)))
    config = Config()

    result = asyncio.run(translator._revert_upscale(config, ctx))

    assert result.result.size == (1, 1)
    assert translator._pipeline_run.documents["text_regions.json"] == []
    translator._pipeline_run.checkpoint.assert_awaited_once()


if __name__ == "__main__":
    test_ultrasharp_tiles_large_images()
    test_revert_upscale_skipped_when_upscaling_deactivated()
    print("upscaling tests: ok")
