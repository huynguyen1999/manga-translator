import asyncio

from manga_translator.manga_translator import MangaTranslator
from manga_translator.pipeline.progress import add_logger_hook


class PipelineRun:
    def __init__(self):
        self.events = []

    def progress(self, state, finished):
        self.events.append(("progress", state, finished))

    async def checkpoint(self):
        self.events.append(("checkpoint",))

    def cleanup_completed_artifacts(self):
        self.events.append(("cleanup",))

    def refresh(self):
        self.events.append(("refresh",))


class Logger:
    def __init__(self):
        self.events = []

    def info(self, message):
        self.events.append(("info", message))

    def warn(self, message):
        self.events.append(("warn", message))

    def error(self, message):
        self.events.append(("error", message))


def test_completed_progress_checkpoints_before_notifying_hooks():
    async def run():
        translator = MangaTranslator.__new__(MangaTranslator)
        pipeline_run = PipelineRun()
        translator._pipeline_run = pipeline_run
        events = []
        translator._progress_hooks = [
            lambda state, finished: record_hook(events, state, finished),
        ]

        await translator._report_progress("finished", True)

        assert pipeline_run.events == [
            ("progress", "finished", True),
            ("checkpoint",),
            ("cleanup",),
            ("refresh",),
            ("checkpoint",),
        ]
        assert events == [("finished", True)]

    asyncio.run(run())


async def record_hook(events, state, finished):
    events.append((state, finished))


def test_logger_progress_hook_preserves_status_mapping():
    class Owner:
        def __init__(self):
            self.hooks = []

        def add_progress_hook(self, hook):
            self.hooks.append(hook)

    async def run():
        owner = Owner()
        logger = Logger()
        add_logger_hook(owner, logger)
        hook = owner.hooks[0]
        await hook("detection", False)
        await hook("skip-no-regions", True)
        await hook("offline_model:offline-ocr", False)
        assert logger.events == [
            ("info", "Running text detection"),
            ("warn", "No text regions! - Skipping"),
            ("info", "Using offline model: offline-ocr"),
        ]

    asyncio.run(run())
