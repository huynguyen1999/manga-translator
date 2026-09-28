import asyncio
import tempfile
from pathlib import Path
import pytest

from server.preset_repository import (
    InvalidPreset,
    PresetConflict,
    PresetNotFound,
    PresetRepository,
)


@pytest.mark.asyncio
async def test_file_backed_preset_repository_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        repo = PresetRepository(store=None, result_root=root)

        # 1. Initially empty
        presets = await repo.list_presets()
        assert presets == []

        # 2. Create preset
        p1 = await repo.create_preset(
            name="Standard Japanese Manga",
            description="Default settings for B&W manga",
            settings={"targetLanguage": "ENG", "translator": "deepseek", "ocr": "48px"},
            is_default=True,
        )
        assert p1["id"].startswith("preset-")
        assert p1["name"] == "Standard Japanese Manga"
        assert p1["description"] == "Default settings for B&W manga"
        assert p1["isDefault"] is True
        assert p1["settings"]["targetLanguage"] == "ENG"
        assert "createdAt" in p1
        assert "updatedAt" in p1

        # 3. Create second preset
        p2 = await repo.create_preset(
            name="Color Webtoon",
            description="Settings for full color manhwa",
            settings={"targetLanguage": "ENG", "colorizer": "mc2", "inpainter": "lama_mpe"},
            is_default=False,
        )
        assert p2["name"] == "Color Webtoon"
        assert p2["isDefault"] is False

        # 4. List presets (default should be sorted first)
        presets = await repo.list_presets()
        assert len(presets) == 2
        assert presets[0]["id"] == p1["id"]
        assert presets[1]["id"] == p2["id"]

        # 5. Duplicate name conflict
        with pytest.raises(PresetConflict):
            await repo.create_preset(name="  standard japanese manga  ")

        # 6. Invalid name validations
        with pytest.raises(InvalidPreset):
            await repo.create_preset(name="   ")
        with pytest.raises(InvalidPreset):
            await repo.create_preset(name="a" * 101)
        with pytest.raises(InvalidPreset):
            await repo.create_preset(name="Valid", description="d" * 501)

        # 7. Get preset
        fetched = await repo.get_preset(p1["id"])
        assert fetched["name"] == "Standard Japanese Manga"

        with pytest.raises(PresetNotFound):
            await repo.get_preset("nonexistent-id")

        # 8. Update preset settings and make p2 default
        updated_p2 = await repo.update_preset(
            p2["id"],
            name="Color Manhwa / Webtoon Pro",
            settings={"targetLanguage": "ENG", "colorizer": "mc2", "upscaler": "esrgan"},
            is_default=True,
        )
        assert updated_p2["name"] == "Color Manhwa / Webtoon Pro"
        assert updated_p2["isDefault"] is True
        assert updated_p2["settings"]["upscaler"] == "esrgan"

        # Check that p1 is no longer default
        updated_p1 = await repo.get_preset(p1["id"])
        assert updated_p1["isDefault"] is False

        # 9. Set default helper
        await repo.set_default_preset(p1["id"])
        assert (await repo.get_preset(p1["id"]))["isDefault"] is True
        assert (await repo.get_preset(p2["id"]))["isDefault"] is False

        # 10. Delete preset
        await repo.delete_preset(p2["id"])
        presets = await repo.list_presets()
        assert len(presets) == 1
        assert presets[0]["id"] == p1["id"]

        with pytest.raises(PresetNotFound):
            await repo.delete_preset(p2["id"])


class MockAsyncpgRecord:
    """Simulates asyncpg.Record which supports subscripting but has no .get() method."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key: str):
        return self._data[key]

    # Deliberately do not implement .get()


def test_preset_format_row_with_asyncpg_record():
    repo = PresetRepository(store=None)
    record = MockAsyncpgRecord({
        "id": "preset-123",
        "name": "AsyncPG Preset",
        "description": "DB stored preset",
        "is_default": True,
        "settings": '{"targetLanguage":"ENG"}',
        "created_at": "2026-09-29T00:00:00Z",
        "updated_at": "2026-09-29T00:00:00Z",
    })

    formatted = repo._format_row(record)
    assert formatted["id"] == "preset-123"
    assert formatted["name"] == "AsyncPG Preset"
    assert formatted["description"] == "DB stored preset"
    assert formatted["isDefault"] is True
    assert formatted["settings"] == {"targetLanguage": "ENG"}

