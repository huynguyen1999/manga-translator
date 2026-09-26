from io import BytesIO
from types import SimpleNamespace
import asyncio
import unittest
import zipfile

from PIL import Image

from server.main import _iter_original_upload_pages


class OriginalImportTests(unittest.TestCase):
    @staticmethod
    def png_bytes(color):
        image = Image.new("RGBA", (2, 2), color=color)
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def test_archive_upload_filters_hidden_entries_and_natural_sorts_pages(self):
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("chapter/10.png", self.png_bytes((10, 20, 30, 128)))
            zipped.writestr("chapter/2.png", self.png_bytes((20, 30, 40, 64)))
            zipped.writestr(".hidden/1.png", self.png_bytes((30, 40, 50, 255)))
            zipped.writestr("__MACOSX/._3.png", self.png_bytes((40, 50, 60, 255)))

        upload = SimpleNamespace(filename="chapter.cbz", file=BytesIO(archive.getvalue()))
        pages = list(_iter_original_upload_pages([upload]))

        self.assertEqual([page[0] for page in pages], ["2.png", "10.png"])
        self.assertEqual([page[1] for page in pages], ["chapter.cbz/chapter/2.png", "chapter.cbz/chapter/10.png"])
        for page in pages:
            with Image.open(BytesIO(page[3])) as normalized:
                self.assertEqual(normalized.mode, "RGBA")
                self.assertEqual(normalized.format, "PNG")


class OriginalImportMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_requests_wait_for_the_shared_import_lock(self):
        from server.main import _original_import_lock, serialize_original_manga_imports

        request = SimpleNamespace(url=SimpleNamespace(path="/api/results/import"))
        entered = asyncio.Event()

        async def call_next(_request):
            entered.set()
            return "response"

        await _original_import_lock.acquire()
        try:
            task = asyncio.create_task(serialize_original_manga_imports(request, call_next))
            await asyncio.sleep(0)
            self.assertFalse(entered.is_set())
        finally:
            _original_import_lock.release()

        self.assertEqual(await task, "response")
        self.assertTrue(entered.is_set())


if __name__ == "__main__":
    unittest.main()
