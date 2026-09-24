import asyncio
import unittest
from types import SimpleNamespace
import numpy as np
from PIL import Image
from manga_translator.utils import Context
from manga_translator import Config
from manga_translator.manga_translator import MangaTranslator

class TestBatchMemoryDecoupling(unittest.TestCase):
    def test_context_cleanup_intermediate(self):
        ctx = Context()
        ctx.input = Image.new("RGB", (100, 100), color="white")
        ctx.img_rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        ctx.img_alpha = np.zeros((100, 100), dtype=np.uint8)
        ctx.upscaled = Image.new("RGB", (200, 200), color="white")
        ctx.mask_raw = np.zeros((100, 100), dtype=np.uint8)
        ctx.mask = np.zeros((100, 100), dtype=np.uint8)
        ctx.img_inpainted = np.zeros((100, 100, 3), dtype=np.uint8)
        ctx.text_regions = [{"text": "Hello"}]
        ctx.result = Image.new("RGB", (100, 100), color="white")

        # Cleanup intermediate keeping input
        ctx.cleanup_intermediate(keep_input=True)
        self.assertIsNotNone(ctx.input)
        self.assertIsNotNone(ctx.result)
        self.assertEqual(ctx.text_regions, [{"text": "Hello"}])
        self.assertIsNone(ctx.img_rgb)
        self.assertIsNone(ctx.img_alpha)
        self.assertIsNone(ctx.upscaled)
        self.assertIsNone(ctx.mask_raw)
        self.assertIsNone(ctx.mask)
        self.assertIsNone(ctx.img_inpainted)

        # Cleanup all images
        ctx.cleanup_all_images()
        self.assertIsNone(ctx.input)
        self.assertIsNone(ctx.result)
        self.assertEqual(ctx.text_regions, [{"text": "Hello"}])

    def test_runtime_cleanup_releases_workspace_and_keeps_requested_outputs(self):
        region = SimpleNamespace(
            _bubble_mask=np.ones((8, 8), dtype=np.uint8),
            _free_text_source_mask=np.ones((8, 8), dtype=np.uint8),
            _free_text_inpaint_mask=np.ones((8, 8), dtype=np.uint8),
            _free_text_ownership_mask=np.ones((8, 8), dtype=np.uint8),
            _free_text_zone=object(),
            layout_segments=[{"text": "Hello"}],
        )
        ctx = Context(
            input=Image.new("RGB", (8, 8)),
            img_rgb=np.zeros((8, 8, 3), dtype=np.uint8),
            img_inpainted=np.ones((8, 8, 3), dtype=np.uint8),
            img_rendered=np.ones((8, 8, 3), dtype=np.uint8),
            mask=np.ones((8, 8), dtype=np.uint8),
            inpaint_mask=np.ones((8, 8), dtype=np.uint8),
            text_mask=np.ones((8, 8), dtype=np.uint8),
            mask_bundle=object(),
            page_geometry=object(),
            _layout_obstacles=object(),
            _free_text_zones=object(),
            textlines=[object()],
            bubble_detections=[object()],
            text_regions=[region],
            translations={"Hello": "Hi"},
            result=Image.new("RGB", (8, 8)),
        )

        ctx.cleanup_mask_diagnostics()
        self.assertIsNotNone(ctx.mask)
        self.assertIsNone(ctx.text_mask)
        self.assertIsNone(ctx.mask_bundle)

        ctx.cleanup_runtime(preserve_output=True)

        self.assertIsNone(ctx.input)
        self.assertIsNone(ctx.img_rgb)
        self.assertIsNone(ctx.img_rendered)
        self.assertIsNone(ctx.mask)
        self.assertIsNone(ctx.inpaint_mask)
        self.assertIsNone(ctx.mask_bundle)
        self.assertIsNone(ctx.page_geometry)
        self.assertIsNone(ctx.textlines)
        self.assertIsNone(ctx.bubble_detections)
        self.assertIsNone(region._bubble_mask)
        self.assertIsNone(region._free_text_source_mask)
        self.assertIsNone(region._free_text_zone)
        self.assertEqual(region.layout_segments, [{"text": "Hello"}])
        self.assertEqual(ctx.translations, {"Hello": "Hi"})
        self.assertIsNotNone(ctx.img_inpainted)
        self.assertIsNotNone(ctx.result)

        ctx.cleanup_runtime()
        self.assertIsNone(ctx.img_inpainted)
        self.assertIsNone(ctx.result)

    def test_legacy_translate_batch_prepares_bounded_chunks(self):
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.batch_size = 2
        prepared_sizes = []

        async def prepare(image, _config):
            return Context(input=image, img_rgb=np.zeros((2, 2, 3), dtype=np.uint8), text_regions=[])

        async def render_chunk(contexts, batch_size=None):
            prepared_sizes.append(len(contexts))
            for ctx, _ in contexts:
                ctx.result = Image.new("RGB", (2, 2))
            return [ctx for ctx, _ in contexts]

        translator.prepare = prepare
        translator.translate_and_render_batch = render_chunk
        images = [(Image.new("RGB", (2, 2)), object()) for _ in range(7)]

        results = asyncio.run(translator.translate_batch(images, batch_size=2))

        self.assertEqual(prepared_sizes, [2, 2, 2, 1])
        self.assertEqual(len(results), 7)
        self.assertTrue(all(ctx.img_rgb is None and ctx.result is not None for ctx in results))

    def test_clear_batch_state_drops_page_context(self):
        translator = MangaTranslator.__new__(MangaTranslator)
        translator.all_page_translations = [{"a": "b"}]
        translator._original_page_texts = [{0: "a"}]
        translator._saved_image_contexts = {"page": {"subfolder": "page"}}
        translator._current_image_context = {"subfolder": "page"}

        translator.clear_batch_state("manga")

        self.assertEqual(translator.all_page_translations, [])
        self.assertEqual(translator._original_page_texts, [])
        self.assertEqual(translator._saved_image_contexts, {})
        self.assertIsNone(translator._current_image_context)
        self.assertEqual(translator._memory_batch_id, "manga")

if __name__ == "__main__":
    unittest.main()
