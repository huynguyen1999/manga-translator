import unittest

from manga_translator.config import Config, Detector, Ocr, OcrConfig, Translator, TranslatorChain


class TestConfig(unittest.TestCase):
    def test_translator_chain_parses_translator_and_language(self):
        chain = TranslatorChain("groq:ENG;sugoi:JPN")
        self.assertEqual(chain.chain, [(Translator.groq, "ENG"), (Translator.sugoi, "JPN")])

    def test_detector_aliases(self):
        self.assertEqual(Detector("manga_text_detector"), Detector.default)
        self.assertEqual(Detector("manga-text-detector"), Detector.default)
        self.assertEqual(Detector("manga_text_detector_default"), Detector.default)
        self.assertEqual(Detector("dbnet_resnet34"), Detector.default)
        self.assertEqual(Detector("default"), Detector.default)
        self.assertEqual(Detector("DEFAULT"), Detector.default)
        self.assertEqual(Detector("ctd"), Detector.ctd)
        self.assertEqual(Detector("comic_text_detector"), Detector.ctd)
        self.assertEqual(Detector("comic-text-detector"), Detector.ctd)
        self.assertEqual(Detector("dbconvnext"), Detector.dbconvnext)
        self.assertEqual(Detector("dbnet_convnext"), Detector.dbconvnext)
        self.assertEqual(Detector("craft"), Detector.craft)
        self.assertEqual(Detector("paddle"), Detector.paddle)
        self.assertEqual(Detector("paddle_rust"), Detector.paddle)
        self.assertEqual(Detector("none"), Detector.none)

    def test_config_model_validate_detector_alias(self):
        config = Config.model_validate({"detector": {"detector": "manga_text_detector"}})
        self.assertEqual(config.detector.detector, Detector.default)

        config_ctd = Config.model_validate({"detector": {"detector": "comic_text_detector"}})
        self.assertEqual(config_ctd.detector.detector, Detector.ctd)

    def test_professional_translation_quality_validation(self):
        config = Config.model_validate({"translator": {"translation_quality": "professional"}})
        self.assertEqual(config.translator.translation_quality, "professional")
        with self.assertRaises(ValueError):
            Config.model_validate({"translator": {"translation_quality": "best"}})

    def test_story_plan_is_preserved(self):
        plan = {"enabled": True, "autoDetect": True, "mergeAllPages": False, "archives": [], "segments": []}
        config = Config.model_validate({"translator": {"story_plan": plan}})
        self.assertEqual(config.translator.story_plan, plan)

    def test_ocr_prob_config(self):
        config = Config.model_validate({"ocr": {"prob": 0.8}})
        self.assertEqual(config.ocr.prob, 0.8)

        config_none = Config.model_validate({"ocr": {}})
        self.assertIsNone(config_none.ocr.prob)

    def test_ocr_default_is_48px_ctc(self):
        self.assertEqual(OcrConfig().ocr, Ocr.ocr48px_ctc)

    def test_render_case_config_and_transformation(self):
        from manga_translator.config import RenderConfig

        render_default = RenderConfig()
        self.assertFalse(render_default.uppercase)
        self.assertFalse(render_default.lowercase)
        self.assertEqual(render_default.transform_text_case("Hello World!"), "Hello World!")

        render_upper = RenderConfig(uppercase=True)
        self.assertTrue(render_upper.uppercase)
        self.assertEqual(render_upper.transform_text_case("Hello World!"), "HELLO WORLD!")

        render_lower = RenderConfig(lowercase=True)
        self.assertTrue(render_lower.lowercase)
        self.assertEqual(render_lower.transform_text_case("Hello World!"), "hello world!")

        # String aliases via validator / init
        render_alias_upper = RenderConfig(letter_case="upper")
        self.assertTrue(render_alias_upper.uppercase)
        self.assertFalse(render_alias_upper.lowercase)
        self.assertEqual(render_alias_upper.transform_text_case("Hello World!"), "HELLO WORLD!")

        render_alias_lower = RenderConfig(letter_case="lower")
        self.assertTrue(render_alias_lower.lowercase)
        self.assertFalse(render_alias_lower.uppercase)
        self.assertEqual(render_alias_lower.transform_text_case("Hello World!"), "hello world!")

        render_alias_none = RenderConfig(letter_case="none")
        self.assertFalse(render_alias_none.uppercase)
        self.assertFalse(render_alias_none.lowercase)
        self.assertEqual(render_alias_none.transform_text_case("Hello World!"), "Hello World!")

        # Via Config.model_validate
        config_validate_upper = Config.model_validate({"render": {"letter_case": "upper"}})
        self.assertTrue(config_validate_upper.render.uppercase)

        config_validate_lower = Config.model_validate({"render": {"letter_case": "lower"}})
        self.assertTrue(config_validate_lower.render.lowercase)


if __name__ == "__main__":
    unittest.main()
