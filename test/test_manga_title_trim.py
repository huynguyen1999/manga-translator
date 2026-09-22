import unittest
from manga_translator.config import Config
from server.batch_store import BatchStore
from server.main import (
    _apply_manga_title_alias,
    MangaSummaryRequest,
    CreateSeriesRequest,
    UpdateSeriesRequest,
    UpdateMetaRequest,
    ExportCbzRequest,
    ReadingProgressRequest,
    UpdateBatchRequest,
)


class TestMangaTitleTrim(unittest.TestCase):
    def test_config_manga_title_trimming(self):
        conf = Config(manga_title="  One Piece  ")
        self.assertEqual(conf.manga_title, "One Piece")

        conf_spaces = Config(manga_title="   ")
        self.assertIsNone(conf_spaces.manga_title)

        conf_none = Config(manga_title=None)
        self.assertIsNone(conf_none.manga_title)

    def test_apply_manga_title_alias_trimming(self):
        conf = Config()
        _apply_manga_title_alias(conf, {"mangaTitle": "  Chainsaw Man  "})
        self.assertEqual(conf.manga_title, "Chainsaw Man")

        conf2 = Config()
        _apply_manga_title_alias(conf2, {"mangaTitle": "   "})
        self.assertIsNone(conf2.manga_title)

    def test_request_models_trimming(self):
        summary_req = MangaSummaryRequest(mangaTitle="  Berserk  ")
        self.assertEqual(summary_req.mangaTitle, "Berserk")

        create_series_req = CreateSeriesRequest(title="  Series One  ", groupIds=["g1", "g2"])
        self.assertEqual(create_series_req.title, "Series One")

        update_series_req = UpdateSeriesRequest(title="  Series Two  ")
        self.assertEqual(update_series_req.title, "Series Two")

        meta_req = UpdateMetaRequest(mangaTitle="  New Manga Title  ", oldMangaTitle="  Old Title  ")
        self.assertEqual(meta_req.mangaTitle, "New Manga Title")
        self.assertEqual(meta_req.oldMangaTitle, "Old Title")

        cbz_req = ExportCbzRequest(mangaTitle="  CBZ Title  ")
        self.assertEqual(cbz_req.mangaTitle, "CBZ Title")

        progress_req = ReadingProgressRequest(installationId="install-12345678", mangaTitle="  Progress Manga  ")
        self.assertEqual(progress_req.mangaTitle, "Progress Manga")

        batch_req = UpdateBatchRequest(mangaTitle="  Updated Batch Manga  ")
        self.assertEqual(batch_req.mangaTitle, "Updated Batch Manga")

    def test_batch_store_manifest_trimming(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            store = BatchStore(workspace / "batches", workspace / "results")
            raw_manifest = {
                "id": "batch-test-1",
                "title": "  Batch Spaced Title  ",
                "mangaTitle": "  Batch Spaced Title  ",
                "items": [
                    {
                        "id": "item-1",
                        "name": "page1.png",
                        "mangaTitle": "  Item Spaced Title  ",
                    }
                ],
            }
            normalized = store._normalize_manifest("batch-test-1", raw_manifest)
            self.assertEqual(normalized["title"], "Batch Spaced Title")
            self.assertEqual(normalized["mangaTitle"], "Batch Spaced Title")
            self.assertEqual(normalized["items"][0]["mangaTitle"], "Item Spaced Title")


if __name__ == "__main__":
    unittest.main()
