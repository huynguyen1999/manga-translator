import asyncio
from unittest.mock import AsyncMock, Mock, patch

from manga_translator.manga_translator import MangaTranslator
from manga_translator.pipeline.lifecycle import model_cleanup_job


def test_mps_device_cache_waits_for_shared_executor():
    translator = MangaTranslator.__new__(MangaTranslator)
    translator.device = "mps"

    with_executor = Mock()
    no_executor = Mock()
    with patch("manga_translator.manga_translator.get_model_executor", return_value=object()), \
        patch("manga_translator.manga_translator.empty_device_cache", with_executor):
        translator._empty_device_cache()
    with patch("manga_translator.manga_translator.get_model_executor", return_value=None), \
        patch("manga_translator.manga_translator.empty_device_cache", no_executor):
        translator._empty_device_cache()

    with_executor.assert_not_called()
    no_executor.assert_called_once_with("mps")


def test_model_unload_runs_exclusively_and_clears_device_cache():
    translator = MangaTranslator.__new__(MangaTranslator)
    events = []

    class Executor:
        async def run_exclusive(self, callback):
            events.append("exclusive")
            await callback()

    async def unload_ocr(model):
        events.append(("unload", model))

    translator._empty_device_cache = lambda: events.append("empty-cache")
    with patch("manga_translator.manga_translator.get_model_executor", return_value=Executor()), \
        patch("manga_translator.manga_translator.unload_ocr", unload_ocr):
        asyncio.run(translator._unload_model("ocr", "test-model"))

    assert events == ["exclusive", ("unload", "test-model"), "empty-cache"]


def test_model_cleanup_preserves_ttl_unload_and_timestamp_removal():
    translator = MangaTranslator.__new__(MangaTranslator)
    translator.models_ttl = 10
    translator._model_usage_timestamps = {("ocr", "test-model"): 80}
    unloaded = []
    translator._unload_model = AsyncMock(side_effect=lambda tool, model: unloaded.append((tool, model)))
    sleep_calls = 0

    async def tick(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError

    try:
        asyncio.run(
            model_cleanup_job(
                translator,
                sleep=tick,
                time=lambda: 100,
                get_model_executor=lambda: None,
            )
        )
    except asyncio.CancelledError:
        pass

    assert unloaded == [("ocr", "test-model")]
    assert translator._model_usage_timestamps == {}
