import os
import sys
import unittest

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from manga_translator.config import Config, TranslatorConfig, Translator

class DefaultTranslatorTest(unittest.TestCase):
    def test_translator_config_default(self):
        config = TranslatorConfig()
        self.assertEqual(config.translator, Translator.deepseek)

    def test_main_config_default(self):
        config = Config()
        self.assertEqual(config.translator.translator, Translator.deepseek)

if __name__ == '__main__':
    unittest.main()
