import asyncio
import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest

import manga_translator.translators as translators
from manga_translator.config import Config, Translator, TranslatorChain
from manga_translator.pipeline.batch.translation import batch_translate_texts
from manga_translator.pipeline.stages import ResourceClass
from manga_translator.translation_dispatch import dispatch_with_context
from manga_translator.translators.common import OfflineTranslator
from manga_translator.utils import Context
from server.batch_translation_resources import resource_callbacks


class ResourceScheduler:
    def __init__(self, events):
        self.events = events

    async def _acquire_resource(self, stage_id, resource):
        self.events.append(("acquire", resource, translators._OFFLINE_TRANSLATOR_LOCK.locked()))
        return object()

    def _release_stage_resource(self, stage_id, slot, resource=None):
        self.events.append(("release", resource, translators._OFFLINE_TRANSLATOR_LOCK.locked()))


class ApiTranslator:
    def __init__(self, events, name="api", translate=None):
        self.events = events
        self.name = name
        self._translate_fn = translate

    async def translate(self, _from_lang, _to_lang, queries, _use_mtpe=False):
        self.events.append(("translate", self.name))
        if self._translate_fn:
            return await self._translate_fn(queries)
        return [f"{query}-{self.name}" for query in queries]


class FakeOfflineTranslator(OfflineTranslator):
    def __init__(self, events):
        self.events = events

    async def load(self, _from_lang, _to_lang, device):
        self.events.append(("load", device))

    async def translate(self, _from_lang, _to_lang, queries, _use_mtpe=False):
        self.events.append(("translate", "offline"))
        return [f"{query}-offline" for query in queries]

    async def _infer(self, _from_lang, _to_lang, queries):
        return queries

    async def _load(self, _from_lang, _to_lang, _device):
        pass

    async def _unload(self):
        pass


@pytest.mark.parametrize(
    ("device", "offline_resource"),
    [
        ("cuda:0", ResourceClass.GPU),
        ("mps", ResourceClass.CPU_HEAVY),
        ("xpu", ResourceClass.CPU_HEAVY),
        ("cpu", ResourceClass.CPU_HEAVY),
    ],
)
def test_dispatch_leases_each_engine_in_order(device, offline_resource):
    async def run():
        events = []
        scheduler = ResourceScheduler(events)
        acquire, release = resource_callbacks(
            scheduler, asyncio.get_running_loop()
        )
        args = Context(_batch_resource_acquire=acquire, _batch_resource_release=release)
        api = ApiTranslator(events)
        offline = FakeOfflineTranslator(events)
        instances = {Translator.caiyun: api, Translator.sugoi: offline}

        with patch.object(translators, "get_translator", side_effect=instances.__getitem__):
            result = await translators.dispatch(
                TranslatorChain("caiyun:ENG;sugoi:ENG;caiyun:ENG"),
                ["source"], args=args, device=device,
            )

        assert result == ["source-api-offline-api"]
        assert [event[:2] for event in events if event[0] in {"acquire", "release"}] == [
            ("acquire", ResourceClass.NETWORK), ("release", ResourceClass.NETWORK),
            ("acquire", offline_resource), ("release", offline_resource),
            ("acquire", ResourceClass.NETWORK), ("release", ResourceClass.NETWORK),
        ]
        assert events[3] == ("acquire", offline_resource, True)
        assert events[4] == ("load", device)
        assert events[6] == ("release", offline_resource, True)

    asyncio.run(run())


def test_structured_dispatch_leases_api_offline_api_and_loads_offline_under_lock():
    async def run():
        events = []
        scheduler = ResourceScheduler(events)
        acquire, release = resource_callbacks(
            scheduler, asyncio.get_running_loop()
        )
        args = Context(_batch_resource_acquire=acquire, _batch_resource_release=release)
        api = ApiTranslator(events)
        offline = FakeOfflineTranslator(events)
        instances = {Translator.chatgpt: api, Translator.sugoi: offline}

        async def structured(_translator, _lang, items):
            events.append(("translate", "structured"))
            return {key: f"{value}-api" for key, value in items}

        with (
            patch.object(translators, "get_translator", side_effect=instances.__getitem__),
            patch.object(translators, "translate_structured", side_effect=structured),
        ):
            result = await translators.dispatch_structured(
                TranslatorChain("chatgpt:ENG;sugoi:ENG;chatgpt:ENG"),
                [("id", "source")], args=args, device="cuda:0",
            )

        assert result == {"id": "source-api-offline-api"}
        assert [event[:2] for event in events if event[0] in {"acquire", "release"}] == [
            ("acquire", ResourceClass.NETWORK), ("release", ResourceClass.NETWORK),
            ("acquire", ResourceClass.GPU), ("release", ResourceClass.GPU),
            ("acquire", ResourceClass.NETWORK), ("release", ResourceClass.NETWORK),
        ]
        assert ("load", "cuda:0") in events
        assert ("acquire", ResourceClass.GPU, True) in events

    asyncio.run(run())


def test_dispatch_releases_lease_on_engine_error_and_cancellation():
    async def run():
        events = []
        scheduler = ResourceScheduler(events)
        acquire, release = resource_callbacks(
            scheduler, asyncio.get_running_loop()
        )
        args = Context(_batch_resource_acquire=acquire, _batch_resource_release=release)

        async def fail(_queries):
            raise RuntimeError("provider failed")

        with patch.object(
            translators, "get_translator", return_value=ApiTranslator(events, translate=fail)
        ):
            with pytest.raises(RuntimeError, match="provider failed"):
                await translators.dispatch(TranslatorChain("caiyun:ENG"), ["source"], args=args)
        assert events[-1][:2] == ("release", ResourceClass.NETWORK)

        started = asyncio.Event()

        async def wait(_queries):
            started.set()
            await asyncio.Event().wait()

        events.clear()
        with patch.object(
            translators, "get_translator", return_value=ApiTranslator(events, translate=wait)
        ):
            task = asyncio.create_task(
                translators.dispatch(TranslatorChain("caiyun:ENG"), ["source"], args=args)
            )
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert events[-1][:2] == ("release", ResourceClass.NETWORK)

    asyncio.run(run())


def test_direct_chatgpt_paths_use_batch_lease_and_effective_cpu_device():
    async def run():
        events = []
        scheduler = ResourceScheduler(events)
        acquire, release = resource_callbacks(
            scheduler, asyncio.get_running_loop()
        )
        callback_devices = []

        async def acquire_recording_device(is_offline, device):
            callback_devices.append((is_offline, device))
            return await acquire(is_offline, device)

        provider = Mock()
        provider.model = "gpt-test"
        provider._translate = AsyncMock(return_value=["done"])
        owner = Mock()
        owner.all_page_translations = []
        owner.context_size = 0
        owner._build_prev_context = Mock(return_value="history")
        owner._gpu_limited_memory = True
        owner.device = "cuda:0"
        owner.batch_concurrent = False
        owner.batch_size = 1
        owner._original_page_texts = []
        owner.use_mtpe = False
        config = Config()
        config.translator.translator = Translator.chatgpt
        ctx = Context(
            from_lang="JPN",
            _batch_resource_acquire=acquire_recording_device,
            _batch_resource_release=release,
        )

        with patch("manga_translator.translators.chatgpt.OpenAITranslator", return_value=provider):
            result = await dispatch_with_context(owner, config, ["source"], ctx, logger=logging.getLogger("test"))
            assert result == ["done"]

            result = await batch_translate_texts(
                owner, ["source"], config, ctx, logger=logging.getLogger("test")
            )

        assert result == ["done"]
        assert callback_devices == [(False, "cpu"), (False, "cpu")]
        assert [event[:2] for event in events if event[0] in {"acquire", "release"}] == [
            ("acquire", ResourceClass.NETWORK), ("release", ResourceClass.NETWORK),
            ("acquire", ResourceClass.NETWORK), ("release", ResourceClass.NETWORK),
        ]

    asyncio.run(run())


def test_noop_translation_engines_do_not_take_network_leases():
    async def run():
        events = []
        acquire, release = resource_callbacks(
            ResourceScheduler(events), asyncio.get_running_loop()
        )
        args = Context(_batch_resource_acquire=acquire, _batch_resource_release=release)
        for key, expected in ((Translator.original, ["source"]), (Translator.none, [""])):
            result = await translators.dispatch(
                TranslatorChain(f"{key.value}:ENG"), ["source"], args=args
            )
            assert result == expected
        assert not events

    asyncio.run(run())
