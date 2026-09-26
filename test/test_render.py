import os
import sys
import cv2
import pytest
import numpy as np
from types import SimpleNamespace

from manga_translator.rendering import dispatch as dispatch_rendering, dispatch_eng_render
from manga_translator.config import Renderer
from manga_translator.manga_translator import MangaTranslator
from manga_translator.utils import (
    TextBlock,
    visualize_textblocks,
)


RENDER_IMAGE_FOLDER = 'test/testdata/render'
os.makedirs(RENDER_IMAGE_FOLDER, exist_ok=True)

def save_result(path, img, regions):
    path = os.path.join(RENDER_IMAGE_FOLDER, path)
    cv2.imwrite(path, visualize_textblocks(img, regions))


@pytest.mark.asyncio
async def test_default_renderer():
    width, height = 1000, 1000
    img = np.zeros((height, width, 3))
    regions = [
        TextBlock(
            [[[10, 10], [200, 10], [10, 400], [200, 400]]],
            texts=['a', 'b','c', 'd', 'e', 'f'],
            translation='aaaaaa bbbbbbbbbbbb cccc ddddddddddd eeeeeeeeeeeeee fff'
        ),
        TextBlock(
            [[[410, 10], [900, 10], [410, 800], [900, 800]]],
            texts=['eng', 'pne'],
            translation=#'aaaaaa bbbbbbbbbbbb cccc' \
                # 'dddddddddddddddddddddddddddddddddddddddddddddddddddd eeeeeeeeeeeeee fff' \
                # 'dddddddddddddddddddddddddddddddddddddddddddddddddddd fff' \
                # 'dddddddddddddddddddddddddddddddddddddddddddddddddddd ' \
                'normal english sentences can be hyphenated! ' \
                'Pneumonoultramicroscopicsilicovolcanoconiosis'
        ),
    ]
    for region in regions:
        region.target_lang = 'ENG'
        region.set_font_colors([255, 255, 255], [200, 200, 200])
        region.font_size = 100

    img_rendered = await dispatch_rendering(img, regions, hyphenate=False)
    save_result('default1.png', img_rendered, regions)


@pytest.mark.asyncio
@pytest.mark.parametrize('renderer, dispatch_name', [
    (Renderer.none, None),
    (Renderer.default, 'dispatch_rendering'),
    (Renderer.manga2Eng, 'dispatch_eng_render'),
    (Renderer.manga2EngPillow, 'dispatch_eng_render_pillow'),
])
async def test_text_rendering_preserves_clean_inpainted_canvas(monkeypatch, renderer, dispatch_name):
    import manga_translator.manga_translator as manga_translator_module

    translator = object.__new__(MangaTranslator)
    translator._model_usage_timestamps = {}
    translator.font_path = ''

    clean = np.full((2, 2, 3), 123, dtype=np.uint8)
    ctx = SimpleNamespace(
        img_inpainted=clean.copy(),
        img_rgb=clean.copy(),
        text_regions=[SimpleNamespace(target_lang='ENG', translation='test')] if renderer in (Renderer.manga2Eng, Renderer.manga2EngPillow) else [],
        render_mask=None,
    )
    config = SimpleNamespace(render=SimpleNamespace(
        renderer=renderer,
        line_spacing=None,
        font_size=None,
        font_size_offset=0,
        font_size_minimum=0,
        no_hyphenation=False,
    ))

    if dispatch_name:
        async def mutating_renderer(canvas, *_args, **_kwargs):
            canvas.fill(0)
            return canvas

        import manga_translator.rendering as rendering_module
        monkeypatch.setattr(manga_translator_module, dispatch_name, mutating_renderer)
        target_name = 'dispatch' if dispatch_name == 'dispatch_rendering' else dispatch_name
        monkeypatch.setattr(rendering_module, target_name, mutating_renderer)

    output = await translator._run_text_rendering(config, ctx)

    np.testing.assert_array_equal(ctx.img_inpainted, clean)
    if dispatch_name:
        np.testing.assert_array_equal(output, np.zeros_like(clean))
    else:
        np.testing.assert_array_equal(output, clean)


def test_resolve_font_name_or_path():
    from manga_translator.rendering import resolve_font_name_or_path, get_default_eng_font

    default_font = get_default_eng_font()
    assert resolve_font_name_or_path(None) == default_font
    assert resolve_font_name_or_path("") == default_font
    assert resolve_font_name_or_path("wildwords") == default_font
    assert resolve_font_name_or_path("Sans-serif") == default_font

    anime_ace = resolve_font_name_or_path("anime_ace")
    assert "anime_ace.ttf" in anime_ace
    assert os.path.isfile(anime_ace)

    comic = resolve_font_name_or_path("comic_shanns")
    assert "comic shanns 2.ttf" in comic
    assert os.path.isfile(comic)

