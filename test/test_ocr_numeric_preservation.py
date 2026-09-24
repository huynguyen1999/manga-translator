import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import cv2

from manga_translator.config import Config, Translator, TranslatorConfig
from manga_translator.manga_translator import MangaTranslator
from manga_translator.mask_builder import build_inpaint_masks
from manga_translator.pipeline.run import deserialize_textblocks, serialize_regions
from manga_translator.professional_translation import ProfessionalTranslator, translate_professionally
from manga_translator.rendering import get_default_eng_font, render_page
from manga_translator.rendering.layout import layout_page
from manga_translator.utils import Context, TextBlock, contains_linguistic_ocr_text, is_meaningful_ocr_text


def region(text, y=10):
    block = TextBlock(
        lines=[[[10, y], [70, y], [70, y + 20], [10, y + 20]]],
        texts=[text],
    )
    block.region_id = f"region-{y}"
    return block


class NumericOcrPreservationTests(unittest.IsolatedAsyncioTestCase):
    def test_unicode_ocr_meaning_is_separate_from_linguistic_content(self):
        for text in ("(48)", "48", "1998", "100%", "#12", "12:30", "１２", "Ⅷ"):
            with self.subTest(text=text):
                self.assertTrue(is_meaningful_ocr_text(text))
                self.assertFalse(contains_linguistic_ocr_text(text))

        for text in ("100円", "第3話"):
            with self.subTest(text=text):
                self.assertTrue(is_meaningful_ocr_text(text))
                self.assertTrue(contains_linguistic_ocr_text(text))

        for text in ("...", "!!!", "——"):
            with self.subTest(text=text):
                self.assertFalse(is_meaningful_ocr_text(text))

    async def test_merge_keeps_numeric_regions_and_marks_preservation_policy(self):
        translator = MangaTranslator({"no_gpu": True})
        config = Config()
        config.translator.no_text_lang_skip = True
        ctx = Context(img_rgb=np.zeros((160, 100, 3), dtype=np.uint8), textlines=[])
        samples = ["(48)", "48", "1998", "100%", "#12", "12:30", "１２", "Ⅷ", "100円", "第3話", "...", "!!!", "——"]
        blocks = [region(text, 5 + index * 12) for index, text in enumerate(samples)]

        with patch("manga_translator.manga_translator.dispatch_textline_merge", new=AsyncMock(return_value=blocks)):
            merged = await translator._run_textline_merge(config, ctx)

        kept = {item.text: item for item in merged}
        self.assertEqual(set(kept), set(samples[:10]))
        for text in samples[:8]:
            self.assertEqual(kept[text].translation_policy, "preserve")
            self.assertEqual(kept[text].translation, text)
            self.assertEqual(kept[text].retention_reason, "numeric_content")
        for text in samples[8:10]:
            self.assertEqual(kept[text].translation_policy, "translate")

    async def test_page_translation_excludes_preserved_regions_from_provider(self):
        translator = MangaTranslator({"no_gpu": True})
        config = Config()
        numeric = region("(48)")
        numeric.translation_policy = "preserve"
        japanese = region("中本", 40)
        ctx = Context(text_regions=[numeric, japanese])
        translator._dispatch_with_context = AsyncMock(return_value=["NAKAMOTO"])

        result = await translator._translate_page_with_retries(config, ctx)

        translator._dispatch_with_context.assert_awaited_once()
        self.assertEqual(translator._dispatch_with_context.await_args.args[1], ["中本"])
        self.assertEqual(result, ["(48)", "NAKAMOTO"])

    async def test_grouped_batch_translation_excludes_preserved_regions(self):
        translator = MangaTranslator({"no_gpu": True})
        config = Config()
        numeric = region("(48)")
        numeric.translation_policy = "preserve"
        japanese = region("中本", 40)
        ctx = Context(text_regions=[numeric, japanese], result_documents={})
        translator._batch_translate_texts = AsyncMock(return_value=["NAKAMOTO"])
        translator._apply_post_translation_processing = AsyncMock(side_effect=lambda page, _config: page.text_regions)

        await translator._batch_translate_contexts([(ctx, config)], batch_size=1)

        translator._batch_translate_texts.assert_awaited_once()
        self.assertEqual(translator._batch_translate_texts.await_args.args[0], ["中本"])
        self.assertEqual(numeric.translation, "(48)")
        self.assertEqual(japanese.translation, "NAKAMOTO")

    async def test_concurrent_translation_excludes_preserved_regions(self):
        translator = MangaTranslator({"no_gpu": True})
        config = Config()
        numeric = region("(48)")
        numeric.translation_policy = "preserve"
        japanese = region("中本", 40)
        ctx = Context(text_regions=[numeric, japanese], result_documents={})
        translator._batch_translate_texts = AsyncMock(return_value=["NAKAMOTO"])
        translator._apply_post_translation_processing = AsyncMock(side_effect=lambda page, _config: page.text_regions)

        await translator._concurrent_translate_contexts([(ctx, config)])

        translator._batch_translate_texts.assert_awaited_once()
        self.assertEqual(translator._batch_translate_texts.await_args.args[0], ["中本"])
        self.assertEqual(numeric.translation, "(48)")
        self.assertEqual(japanese.translation, "NAKAMOTO")

    async def test_professional_translation_excludes_and_preserves_numeric_regions(self):
        numeric = region("(48)")
        numeric.translation_policy = "preserve"
        japanese = region("中本", 40)
        ctx = SimpleNamespace(
            text_regions=[numeric, japanese], result_documents={}, manual_review_required=False,
        )
        config = SimpleNamespace(
            page_order=1,
            translator=TranslatorConfig(
                translator=Translator.deepseek,
                target_lang="ENG",
                translation_quality="professional",
            ),
            render=SimpleNamespace(transform_text_case=lambda text: text),
        )
        analyzed_pages = []
        localized_pages = []

        async def analyze(_self, pages, _override):
            analyzed_pages.extend(pages)
            return {"stories": [{"start_page": 1, "end_page": 1, "confidence": 1.0}]}

        async def localize(_self, _story, pages, *_metadata):
            localized_pages.extend(pages)
            for page in pages:
                for item in page["regions"]:
                    item.update(draft="NAKAMOTO", confidence=1.0, review_reasons=[])

        async def edit(_self, _story, pages, *_metadata):
            for page in pages:
                for item in page["regions"]:
                    item.update(final="NAKAMOTO")

        with (
            patch.object(ProfessionalTranslator, "analyze", analyze),
            patch.object(ProfessionalTranslator, "localize_story", localize),
            patch.object(ProfessionalTranslator, "edit_story", edit),
        ):
            await translate_professionally([(ctx, config)])

        self.assertEqual([item["source"] for item in analyzed_pages[0]["regions"]], ["中本"])
        self.assertEqual([item["source"] for item in localized_pages[0]["regions"]], ["中本"])
        self.assertEqual(numeric.translation, "(48)")
        self.assertEqual(japanese.translation, "NAKAMOTO")
        audit = ctx.result_documents["professional_translation.json"]["regions"]
        self.assertEqual({item["source"] for item in audit}, {"(48)", "中本"})
        self.assertEqual(next(item for item in audit if item["source"] == "(48)")["final"], "(48)")

    async def test_preserved_numeric_region_survives_checkpoint_and_enters_text_mask(self):
        numeric = region("(48)")
        numeric.translation_policy = "preserve"
        numeric.retention = "kept"
        numeric.retention_reason = "numeric_content"
        numeric.translation = numeric.text

        restored = deserialize_textblocks(serialize_regions([numeric]))[0]
        self.assertEqual(restored.translation_policy, "preserve")
        self.assertEqual(restored.retention_reason, "numeric_content")

        image = np.full((100, 100, 3), 255, dtype=np.uint8)
        cv2.putText(image, "48", (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        masks = await build_inpaint_masks(
            image=image,
            detector_textlines=[],
            detector_mask=None,
            text_regions=[restored],
            bubble_detections=[],
            config=Config(),
        )
        self.assertTrue(np.any(masks.text_mask[10:30, 10:70]))

        config = Config()
        restored.target_lang = "ENG"
        restored.source_font_size = 14
        restored.calibrated_font_size = 14
        ctx = Context(
            img_rgb=image,
            img_inpainted=np.full_like(image, 255),
            text_regions=[restored],
        )
        font_path = get_default_eng_font()
        layout_page(ctx, config, font_path)
        self.assertEqual(restored.calibrated_font_size, 14)
        rendered = await render_page(ctx, config, font_path)
        self.assertTrue(np.any(rendered[10:30, 10:70] < 255))
        self.assertEqual(restored.translation, "(48)")


if __name__ == "__main__":
    unittest.main()
