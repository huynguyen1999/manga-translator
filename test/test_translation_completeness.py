"""Offline checks for incomplete pages: no translation provider or model needed."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import unittest
from manga_translator.config import Config, Translator
from manga_translator.manga_translator import MangaTranslator, TranslationFailure
from manga_translator.translators.common import TranslationProviderUnavailable
from manga_translator.utils import Context, TextBlock


def setup_page():
    translator = MangaTranslator.__new__(MangaTranslator)
    translator._dispatch_with_context = AsyncMock()
    translator._report_progress = AsyncMock()
    translator.context_size = 0
    translator.all_page_translations = []
    translator.ignore_errors = True
    translator.post_dict = None
    translator._original_page_texts = []
    config = Config()
    config.translator.translator = Translator.sugoi
    config.translator.target_lang = 'ENG'
    config.translator.post_check_max_retry_attempts = 1
    config.translator.enable_post_translation_check = False
    ctx = Context(text_regions=[TextBlock(
        [[[10, 10], [30, 10], [30, 90], [10, 90]]], texts=['うるさい'], font_size=20
    )])
    return translator, config, ctx


class TranslationCompletenessTests(unittest.TestCase):
    def test_batch_facade_uses_standard_mode_and_saves_page_context(self):
        translator, config, ctx = setup_page()
        translator._saved_image_contexts = {}
        translator.disable_memory_optimization = False
        translator.batch_size = 2
        translator.batch_concurrent = False
        ctx.image_context = {"file_md5": "page-md5"}
        config.translator._translator_gen = SimpleNamespace(chain=[])
        expected = [(ctx, config)]
        translator._batch_translate_contexts = AsyncMock(return_value=expected)
        translator._concurrent_translate_contexts = AsyncMock()

        result = asyncio.run(translator.translate_batch_contexts(expected))

        self.assertEqual(result, expected)
        self.assertEqual(translator._saved_image_contexts, {"page-md5": ctx.image_context})
        translator._batch_translate_contexts.assert_awaited_once_with(expected, 2)
        translator._concurrent_translate_contexts.assert_not_awaited()

    def test_batch_facade_keeps_professional_mode_dispatch(self):
        translator, config, ctx = setup_page()
        translator._saved_image_contexts = {}
        translator.disable_memory_optimization = False
        translator.batch_size = 20
        config.translator.translation_quality = "professional"
        ctx.result_documents = {}
        expected = [(ctx, config)]
        translator._apply_post_translation_processing = AsyncMock(return_value=ctx.text_regions)
        with patch(
            "manga_translator.professional_translation.translate_professionally",
            new=AsyncMock(return_value=expected),
        ) as professional:
            result = asyncio.run(translator.translate_batch_contexts(expected))

        self.assertEqual(result, expected)
        professional.assert_awaited_once_with(expected, progress=translator._report_progress)
        translator._report_progress.assert_has_awaits([
            call("analyzing-story"),
            call("after-translating"),
        ])

    def test_batch_memory_error_falls_back_to_individual_pages(self):
        translator, config, ctx = setup_page()
        translator._saved_image_contexts = {}
        translator.disable_memory_optimization = False
        translator.batch_size = 2
        translator.batch_concurrent = False
        config.translator._translator_gen = SimpleNamespace(chain=[])
        translator._batch_translate_contexts = AsyncMock(side_effect=MemoryError("batch too large"))
        translator._translate_page_with_retries = AsyncMock(return_value=["Shut up!"])
        translator._empty_device_cache = Mock()

        result = asyncio.run(translator.translate_batch_contexts([(ctx, config)]))

        self.assertEqual(result, [(ctx, config)])
        self.assertEqual(ctx.text_regions[0].translation, "Shut up!")
        self.assertEqual(ctx.text_regions[0].target_lang, config.translator.target_lang)
        translator._translate_page_with_retries.assert_awaited_once_with(config, ctx)
        translator._empty_device_cache.assert_called_once_with()

    def test_none_translation_stage_preserves_annotations(self):
        translator, config, ctx = setup_page()
        translator.prep_manual = False
        translator._model_usage_timestamps = {}
        config.translator.translator = Translator.none

        annotation = TextBlock(
            [[[40, 10], [60, 10], [60, 90], [40, 90]]], texts=['48'], font_size=20
        )
        annotation.translation_policy = 'preserve'
        ctx.text_regions.append(annotation)

        result = asyncio.run(translator._run_text_translation(config, ctx))

        self.assertIs(result, ctx.text_regions)
        self.assertEqual(ctx.text_regions[0].translation, '')
        self.assertEqual(annotation.translation, '48')
        self.assertEqual(annotation.target_lang, config.translator.target_lang)
        self.assertEqual(annotation._alignment, config.render.alignment)
        self.assertEqual(annotation._direction, config.render.direction)
        self.assertIn(('translation', Translator.none), translator._model_usage_timestamps)

    def test_postprocessing_stage_keeps_facade_and_preserved_text(self):
        translator, config, ctx = setup_page()
        annotation = TextBlock(
            [[[40, 10], [60, 10], [60, 90], [40, 90]]], texts=['48'], font_size=20
        )
        annotation.translation_policy = 'preserve'
        ctx.text_regions.append(annotation)
        translator._translate_page_with_retries = AsyncMock(return_value=['Shut up!', 'discarded'])

        result = asyncio.run(translator._apply_post_translation_processing(ctx, config))

        self.assertIs(result, ctx.text_regions)
        self.assertEqual(ctx.text_regions[0].translation, 'Shut up!')
        self.assertEqual(annotation.translation, '48')

    def test_cached_inpainted_image_without_mask_rebuilds_mask_and_inpaints(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        import numpy as np
        from PIL import Image
        from unittest.mock import patch

        from manga_translator.mask_builder import build_inpaint_masks

        translator, config, ctx = setup_page()
        image = np.full((120, 120, 3), 255, dtype=np.uint8)
        mask = np.zeros((120, 120), dtype=np.uint8)
        mask[8:95, 8:35] = 255
        ctx.img_rgb = image
        ctx.input = Image.fromarray(image)
        ctx.img_inpainted = np.zeros_like(image)  # stale artifact with no active mask
        ctx.mask = None
        ctx.result_documents = {}
        ctx.bubble_detections = []
        ctx._bubble_detection_done = True
        ctx._bubble_layout_ready = True
        config.bubble_detection.enabled = False

        bundle = SimpleNamespace(
            text_mask=mask,
            bubble_cleanup_mask=mask,
            detector_rescue_mask=mask,
            bubble_residual_mask=np.zeros_like(mask),
            protected_edge_mask=np.zeros_like(mask),
            profile={},
            page_geometry=None,
            final_inpaint_mask=mask,
        )
        inpaint_inputs = []

        async def fake_inpaint(_config, current):
            inpaint_inputs.append(current.img_inpainted)
            return current.img_rgb.copy()

        translator.verbose = False
        translator._pipeline_run = None
        translator._current_image_context = None
        translator._report_progress = AsyncMock()
        translator._result_path = lambda name: str(Path(tempfile.gettempdir()) / name)
        translator._run_inpainting = AsyncMock(side_effect=fake_inpaint)
        translator._run_text_rendering = AsyncMock(return_value=image.copy())
        translator._revert_upscale = AsyncMock(side_effect=lambda _config, current: current)
        translator._empty_device_cache = lambda: None

        with patch(
            "manga_translator.manga_translator.run_cpu_stage",
            new=AsyncMock(return_value=bundle),
        ) as cpu_stage:
            asyncio.run(translator._complete_translation_pipeline(ctx, config))

        self.assertIs(cpu_stage.await_args.args[0], build_inpaint_masks)
        self.assertEqual(len(inpaint_inputs), 1)
        self.assertIsNone(inpaint_inputs[0])

    def test_incomplete_translation_retries(self):
        for bad in ([], [''], ['  '], [None], ['うるさい'], ['one', 'extra']):
            translator, config, ctx = setup_page()
            translator._dispatch_with_context.side_effect = [bad, ['Shut up!']]
            assert asyncio.run(translator._translate_page_with_retries(config, ctx)) == ['Shut up!']
            assert translator._dispatch_with_context.await_count == 2
    
    
    def test_exhausted_retries_fail_even_when_errors_ignored(self):
        translator, config, ctx = setup_page()
        translator._dispatch_with_context.return_value = ['']
        with self.assertRaisesRegex(TranslationFailure, 'after 2 attempts'):
            asyncio.run(translator._translate_page_with_retries(config, ctx))
        assert translator._dispatch_with_context.await_count == 2
    
    
    def test_provider_exception_retried(self):
        translator, config, ctx = setup_page()
        translator._dispatch_with_context.side_effect = [RuntimeError('unavailable'), ['Shut up!']]
        assert asyncio.run(translator._translate_page_with_retries(config, ctx)) == ['Shut up!']

    def test_unavailable_provider_is_not_retried(self):
        translator, config, ctx = setup_page()
        translator._dispatch_with_context.side_effect = TranslationProviderUnavailable('bridge unavailable')
        with self.assertRaisesRegex(TranslationProviderUnavailable, 'bridge unavailable'):
            asyncio.run(translator._translate_page_with_retries(config, ctx))
        translator._dispatch_with_context.assert_awaited_once()
    
    
    def test_complete_values_do_not_call_provider(self):
        translator, config, ctx = setup_page()
        assert asyncio.run(translator._translate_page_with_retries(config, ctx, ['Shut up!'])) == ['Shut up!']
        translator._dispatch_with_context.assert_not_awaited()

    def test_standalone_ocr_symbol_does_not_fail_page_validation(self):
        translator, config, ctx = setup_page()
        ctx.text_regions[0].text = '彡'
        config.translator.enable_post_translation_check = True
        assert asyncio.run(translator._translate_page_with_retries(config, ctx, ['彡'])) == ['彡']

    def test_single_character_foreign_dialogue_still_retries(self):
        translator, config, ctx = setup_page()
        ctx.text_regions[0].text = '死'
        config.translator.enable_post_translation_check = True
        translator._dispatch_with_context.side_effect = [['死'], ['Die']]
        assert asyncio.run(translator._translate_page_with_retries(config, ctx)) == ['Die']

    def test_failed_region_can_be_kept_for_manual_editing(self):
        translator, config, ctx = setup_page()
        ctx.text_regions[0].text = '死'
        config.translator.keep_failed_pages_for_editing = True
        translator._dispatch_with_context.return_value = ['死']

        assert asyncio.run(translator._translate_page_with_retries(config, ctx)) == ['死']
        assert ctx.text_regions[0].review_required is True
        assert ctx.text_regions[0].review_reason == 'translation_validation_failed'
        assert ctx.manual_review_required is True

    def test_partial_translation_keeps_successful_regions_for_manual_editing(self):
        translator, config, ctx = setup_page()
        ctx.text_regions.append(TextBlock(
            [[[40, 10], [60, 10], [60, 90], [40, 90]]], texts=['二'], font_size=20
        ))
        config.translator.keep_failed_pages_for_editing = True
        translator._dispatch_with_context.return_value = ['First translation']

        assert asyncio.run(translator._translate_page_with_retries(config, ctx)) == [
            'First translation', '二'
        ]
        assert ctx.text_regions[1].review_required is True
        assert ctx.manual_review_required is True
    
    
    def test_original_mode_preserved(self):
        translator, config, ctx = setup_page()
        config.translator.translator = Translator.original
        assert asyncio.run(translator._translate_page_with_retries(config, ctx, ['うるさい'])) == ['うるさい']
        translator._dispatch_with_context.assert_not_awaited()
    
    
    def test_concurrent_failed_page_does_not_stop_next_page(self):
        translator, config, failed = setup_page()
        _, _, good = setup_page()
        translator._batch_translate_texts = AsyncMock(side_effect=[[''], ['Shut up!']])
        translator._dispatch_with_context.return_value = ['']
        results = asyncio.run(translator._concurrent_translate_contexts([(failed, config), (good, config)]))
        assert len(results) == 2
        assert failed.translation_error and failed.result is None
        assert not good.get('translation_error')
        assert good.text_regions[0].translation == 'Shut up!'
    
    
    def test_standard_batch_failed_page_does_not_stop_next_page(self):
        translator, config, failed = setup_page()
        _, _, good = setup_page()
        translator._batch_translate_texts = AsyncMock(return_value=['', 'Shut up!'])
        translator._dispatch_with_context.return_value = ['']
        results = asyncio.run(translator._batch_translate_contexts([(failed, config), (good, config)], 2))
        assert len(results) == 2
        assert failed.translation_error and failed.result is None
        assert good.text_regions[0].translation == 'Shut up!'

    def test_batch_transport_failure_retries_pages_independently(self):
        translator, config, failed = setup_page()
        _, _, good = setup_page()
        translator._batch_translate_texts = AsyncMock(side_effect=RuntimeError('offline'))
        translator._dispatch_with_context.side_effect = [[''], [''], ['Shut up!']]
        results = asyncio.run(translator._batch_translate_contexts([(failed, config), (good, config)], 2))
        self.assertEqual(len(results), 2)
        self.assertTrue(failed.translation_error)
        self.assertEqual(good.text_regions[0].translation, 'Shut up!')

    def test_failed_page_json_keeps_error_without_reading_image(self):
        from server.to_json import to_translation
        response = to_translation(Context(translation_error='Missing dialogue', result=None))
        self.assertEqual(response.translations, [])
        self.assertEqual(response.error, 'Missing dialogue')

    def test_missing_erasing_mask_fails_before_rendering(self):
        import numpy as np
        translator, config, ctx = setup_page()
        ctx.img_rgb = np.full((120, 120, 3), 255, dtype=np.uint8)
        ctx.mask = np.zeros((120, 120), dtype=np.uint8)
        with self.assertRaisesRegex(TranslationFailure, 'No erasing mask'):
            asyncio.run(translator._run_inpainting(config, ctx))

    def test_suppressed_or_unchanged_text_does_not_require_erasure(self):
        import numpy as np
        for suppressed, unchanged in ((True, False), (False, True)):
            translator, config, ctx = setup_page()
            ctx.img_rgb = np.full((120, 120, 3), 255, dtype=np.uint8)
            ctx.mask = np.zeros((120, 120), dtype=np.uint8)
            region = ctx.text_regions[0]
            region.translation = region.text if unchanged else 'VISIBLE TRANSLATION'
            region.review_required = True
            region.review_reason = (
                'Translation identical to original' if unchanged else 'no_joint_layout'
            )
            region._render_suppressed = suppressed
            translator.verbose = False
            translator._mps_call = AsyncMock(return_value=[ctx.img_rgb.copy()])

            result = asyncio.run(translator._run_inpainting(config, ctx))

            self.assertEqual(result.shape, ctx.img_rgb.shape)
            translator._mps_call.assert_awaited_once()
