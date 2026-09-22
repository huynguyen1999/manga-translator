import asyncio
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from manga_translator.args import expand_input_paths
from manga_translator.mode.local import MangaTranslatorLocal, archive_output_path


class _FakeTranslator:
    async def translate_path(self, source, translated, params):
        shutil.copytree(source, translated)
        (Path(translated) / 'pages' / 'page.png').write_bytes(b'translated')


class TestLocalArchives(unittest.TestCase):
    def test_wildcard_inputs_and_output_names(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'volume10.cbz').touch()
            (root / 'volume2.cbz').touch()
            self.assertEqual(
                set(expand_input_paths([str(root / '*.cbz')])),
                {str(root / 'volume10.cbz'), str(root / 'volume2.cbz')},
            )
            self.assertEqual(
                archive_output_path(str(root / 'volume2.cbz')),
                str(root / 'volume2-translated.cbz'),
            )

    def test_archive_translation_preserves_metadata_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'volume.cbz'
            output = root / 'volume-translated.cbz'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('ComicInfo.xml', '<ComicInfo/>')
                archive.writestr('pages/page.png', b'original')

            asyncio.run(MangaTranslatorLocal.translate_archive(_FakeTranslator(), str(source), str(output), {}))

            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {'ComicInfo.xml', 'pages/page.png'})
                self.assertEqual(archive.read('ComicInfo.xml'), b'<ComicInfo/>')
                self.assertEqual(archive.read('pages/page.png'), b'translated')

            unsafe = root / 'unsafe.cbz'
            with zipfile.ZipFile(unsafe, 'w') as archive:
                archive.writestr('../outside.txt', b'nope')
            with self.assertRaises(ValueError):
                asyncio.run(MangaTranslatorLocal.translate_archive(_FakeTranslator(), str(unsafe), str(root / 'x.cbz'), {}))


if __name__ == '__main__':
    unittest.main()
