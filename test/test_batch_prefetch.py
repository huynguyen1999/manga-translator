import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

from manga_translator.config import Config
from manga_translator.pipeline.batch.prefetch import prefetch_input_stream
from manga_translator.pipeline.batch.workflow import translate_batch
from manga_translator.utils import Context


class BatchPrefetchTest(unittest.IsolatedAsyncioTestCase):
    async def test_prefetch_input_stream_with_mixed_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "test1.png"
            img1 = Image.new("RGB", (30, 30), color=(255, 0, 0))
            img1.save(file_path)

            img2 = Image.new("RGB", (40, 40), color=(0, 255, 0))
            img3 = Image.new("RGB", (50, 50), color=(0, 0, 255))
            loader = lambda: img3

            config = Config()
            items = [
                (str(file_path), config),
                (img2, config),
                (loader, config),
            ]

            results = []
            async for idx, image, cfg, err in prefetch_input_stream(items, start_index=10, max_prefetch=2):
                self.assertIsNone(err)
                self.assertIsNotNone(image)
                self.assertIsInstance(image, Image.Image)
                self.assertIs(cfg, config)
                results.append((idx, image.size))

            self.assertEqual(
                results,
                [
                    (10, (30, 30)),
                    (11, (40, 40)),
                    (12, (50, 50)),
                ],
            )

    async def test_prefetch_input_stream_handles_invalid_source_gracefully(self):
        config = Config()
        items = [
            ("non_existent_file_12345.png", config),
            (12345, config),  # Invalid type
        ]

        results = []
        async for idx, image, cfg, err in prefetch_input_stream(items, start_index=0):
            self.assertIsNone(image)
            self.assertIsNotNone(err)
            results.append((idx, type(err)))

        self.assertEqual(results[0][0], 0)
        self.assertEqual(results[1][0], 1)
        self.assertIs(results[1][1], TypeError)

    async def test_translate_batch_pipelines_input_to_detection(self):
        owner = MagicMock()
        owner.batch_size = 10
        owner._saved_image_contexts = {}
        owner.all_page_translations = []
        owner._original_page_texts = []

        # Mock owner.prepare (which handles detection / OCR)
        prepared_order = []
        async def mock_prepare(image, config):
            prepared_order.append(image.size)
            ctx = Context(input=image, text_regions=[])
            return ctx
        owner.prepare = AsyncMock(side_effect=mock_prepare)

        # Mock translate_and_render_batch
        async def mock_translate_and_render(pre_contexts, batch_size):
            return [ctx for ctx, _ in pre_contexts]
        owner.translate_and_render_batch = AsyncMock(side_effect=mock_translate_and_render)

        img_a = Image.new("RGB", (100, 100), color=(1, 1, 1))
        img_b = Image.new("RGB", (200, 200), color=(2, 2, 2))
        config = Config()

        results = await translate_batch(
            owner,
            [(img_a, config), (img_b, config)],
            batch_size=5,
            logger=MagicMock(),
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(prepared_order, [(100, 100), (200, 200)])
        self.assertEqual(owner.prepare.await_count, 2)
