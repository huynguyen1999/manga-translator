"""Translation preset persistence operations with dual PostgreSQL / file-backed fallback."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.postgres_common import _json_dump, _json_load

logger = logging.getLogger("manga-translator.presets")


class PresetStoreError(Exception):
    pass


class PresetNotFound(PresetStoreError):
    pass


class PresetConflict(PresetStoreError):
    pass


class InvalidPreset(PresetStoreError):
    pass


def _clean_preset_name(name: Any) -> str:
    if not isinstance(name, str):
        raise InvalidPreset("Preset name must be a string")
    clean = name.strip()
    if not clean:
        raise InvalidPreset("Preset name cannot be empty")
    if len(clean) > 100:
        raise InvalidPreset("Preset name must be 100 characters or fewer")
    return clean


def _clean_preset_description(description: Any) -> str:
    if description is None:
        return ""
    if not isinstance(description, str):
        raise InvalidPreset("Preset description must be a string")
    clean = description.strip()
    if len(clean) > 500:
        raise InvalidPreset("Preset description must be 500 characters or fewer")
    return clean


def _iso(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return str(value) if value is not None else dt.datetime.now(dt.timezone.utc).isoformat()


class PresetRepository:
    def __init__(
        self,
        store: Any = None,
        result_root: Path | str | None = None,
        iso_fn: Callable[[Any], str] | None = None,
    ):
        self._store = store
        self._result_root = Path(result_root).resolve() if result_root else None
        self._iso_fn = iso_fn or _iso
        self._file_lock = asyncio.Lock()

    @property
    def pool(self) -> Any:
        return getattr(self._store, "pool", None) if self._store is not None else None

    @property
    def _file_path(self) -> Path | None:
        root = self._result_root
        if root is None and self._store is not None and getattr(self._store, "result_root", None) is not None:
            root = Path(self._store.result_root).resolve()
        if root is not None:
            return root / "presets.json"
        return None

    def _format_row(self, row: Any) -> dict[str, Any]:
        settings = row["settings"]
        if isinstance(settings, str):
            settings = _json_load(settings, {})
        description = (
            row.get("description", "")
            if isinstance(row, dict)
            else (row["description"] if row["description"] is not None else "")
        )
        return {
            "id": row["id"],
            "name": row["name"],
            "description": description or "",
            "isDefault": bool(row["is_default"]),
            "settings": settings if isinstance(settings, dict) else {},
            "createdAt": self._iso_fn(row["created_at"]),
            "updatedAt": self._iso_fn(row["updated_at"]),
        }

    # ==================== PostgreSQL Operations ====================

    async def _pg_list_presets(self) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            SELECT id, name, description, is_default, settings, created_at, updated_at
            FROM translation_presets
            ORDER BY is_default DESC, lower(btrim(name)), id
            """
        )
        return [self._format_row(row) for row in rows]

    async def _pg_get_preset(self, preset_id: str) -> dict[str, Any]:
        row = await self.pool.fetchrow(
            """
            SELECT id, name, description, is_default, settings, created_at, updated_at
            FROM translation_presets
            WHERE id = $1
            """,
            preset_id,
        )
        if row is None:
            raise PresetNotFound(f"Preset '{preset_id}' not found")
        return self._format_row(row)

    async def _pg_create_preset(
        self,
        name: str,
        description: str = "",
        settings: dict[str, Any] | None = None,
        is_default: bool = False,
    ) -> dict[str, Any]:
        clean_name = _clean_preset_name(name)
        clean_desc = _clean_preset_description(description)
        settings_payload = settings if isinstance(settings, dict) else {}
        preset_id = f"preset-{uuid.uuid4().hex[:12]}"

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                duplicate = await conn.fetchval(
                    "SELECT id FROM translation_presets WHERE lower(btrim(name)) = lower(btrim($1))",
                    clean_name,
                )
                if duplicate:
                    raise PresetConflict(f"A preset with name '{clean_name}' already exists")

                if is_default:
                    await conn.execute(
                        "UPDATE translation_presets SET is_default = FALSE, updated_at = now() WHERE is_default = TRUE"
                    )

                row = await conn.fetchrow(
                    """
                    INSERT INTO translation_presets (id, name, description, is_default, settings)
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    RETURNING id, name, description, is_default, settings, created_at, updated_at
                    """,
                    preset_id,
                    clean_name,
                    clean_desc,
                    is_default,
                    _json_dump(settings_payload),
                )
                return self._format_row(row)

    async def _pg_update_preset(
        self,
        preset_id: str,
        name: str | None = None,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
        is_default: bool | None = None,
    ) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    """
                    SELECT id, name, description, is_default, settings, created_at, updated_at
                    FROM translation_presets
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    preset_id,
                )
                if existing is None:
                    raise PresetNotFound(f"Preset '{preset_id}' not found")

                target_name = _clean_preset_name(name) if name is not None else existing["name"]
                target_desc = (
                    _clean_preset_description(description)
                    if description is not None
                    else (existing["description"] or "")
                )
                target_settings = settings if settings is not None else _json_load(existing["settings"], {})
                target_default = is_default if is_default is not None else existing["is_default"]

                if name is not None:
                    duplicate = await conn.fetchval(
                        """
                        SELECT id FROM translation_presets
                        WHERE id <> $1 AND lower(btrim(name)) = lower(btrim($2))
                        """,
                        preset_id,
                        target_name,
                    )
                    if duplicate:
                        raise PresetConflict(f"A preset with name '{target_name}' already exists")

                if target_default and not existing["is_default"]:
                    await conn.execute(
                        "UPDATE translation_presets SET is_default = FALSE, updated_at = now() WHERE is_default = TRUE AND id <> $1",
                        preset_id,
                    )

                row = await conn.fetchrow(
                    """
                    UPDATE translation_presets
                    SET name = $2, description = $3, is_default = $4, settings = $5::jsonb, updated_at = now()
                    WHERE id = $1
                    RETURNING id, name, description, is_default, settings, created_at, updated_at
                    """,
                    preset_id,
                    target_name,
                    target_desc,
                    target_default,
                    _json_dump(target_settings),
                )
                return self._format_row(row)

    async def _pg_delete_preset(self, preset_id: str) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                deleted = await conn.fetchval(
                    "DELETE FROM translation_presets WHERE id = $1 RETURNING id",
                    preset_id,
                )
                if deleted is None:
                    raise PresetNotFound(f"Preset '{preset_id}' not found")

    # ==================== File-Backed Operations ====================

    async def _read_file_presets(self) -> list[dict[str, Any]]:
        path = self._file_path
        if path is None or not path.exists():
            return []
        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, list):
                return data
            return []
        except Exception as e:
            logger.warning(f"Failed to read file-backed presets from {path}: {e}")
            return []

    async def _write_file_presets(self, presets: list[dict[str, Any]]) -> None:
        path = self._file_path
        if path is None:
            raise RuntimeError("Result root is not configured for file-backed preset storage")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(path)

    async def _file_list_presets(self) -> list[dict[str, Any]]:
        async with self._file_lock:
            presets = await self._read_file_presets()
            return sorted(
                presets,
                key=lambda p: (not p.get("isDefault", False), p.get("name", "").lower(), p.get("id", "")),
            )

    async def _file_get_preset(self, preset_id: str) -> dict[str, Any]:
        async with self._file_lock:
            presets = await self._read_file_presets()
            for p in presets:
                if p["id"] == preset_id:
                    return p
            raise PresetNotFound(f"Preset '{preset_id}' not found")

    async def _file_create_preset(
        self,
        name: str,
        description: str = "",
        settings: dict[str, Any] | None = None,
        is_default: bool = False,
    ) -> dict[str, Any]:
        clean_name = _clean_preset_name(name)
        clean_desc = _clean_preset_description(description)
        settings_payload = settings if isinstance(settings, dict) else {}
        preset_id = f"preset-{uuid.uuid4().hex[:12]}"
        now_str = dt.datetime.now(dt.timezone.utc).isoformat()

        async with self._file_lock:
            presets = await self._read_file_presets()
            for p in presets:
                if p["name"].strip().lower() == clean_name.lower():
                    raise PresetConflict(f"A preset with name '{clean_name}' already exists")

            if is_default:
                for p in presets:
                    if p.get("isDefault"):
                        p["isDefault"] = False
                        p["updatedAt"] = now_str

            new_preset = {
                "id": preset_id,
                "name": clean_name,
                "description": clean_desc,
                "isDefault": is_default,
                "settings": settings_payload,
                "createdAt": now_str,
                "updatedAt": now_str,
            }
            presets.append(new_preset)
            await self._write_file_presets(presets)
            return new_preset

    async def _file_update_preset(
        self,
        preset_id: str,
        name: str | None = None,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
        is_default: bool | None = None,
    ) -> dict[str, Any]:
        async with self._file_lock:
            presets = await self._read_file_presets()
            target_idx = -1
            for idx, p in enumerate(presets):
                if p["id"] == preset_id:
                    target_idx = idx
                    break
            if target_idx == -1:
                raise PresetNotFound(f"Preset '{preset_id}' not found")

            now_str = dt.datetime.now(dt.timezone.utc).isoformat()
            target = presets[target_idx]

            if name is not None:
                clean_name = _clean_preset_name(name)
                for p in presets:
                    if p["id"] != preset_id and p["name"].strip().lower() == clean_name.lower():
                        raise PresetConflict(f"A preset with name '{clean_name}' already exists")
                target["name"] = clean_name

            if description is not None:
                target["description"] = _clean_preset_description(description)

            if settings is not None:
                target["settings"] = settings if isinstance(settings, dict) else {}

            if is_default is not None:
                if is_default and not target.get("isDefault"):
                    for p in presets:
                        if p["id"] != preset_id and p.get("isDefault"):
                            p["isDefault"] = False
                            p["updatedAt"] = now_str
                target["isDefault"] = is_default

            target["updatedAt"] = now_str
            await self._write_file_presets(presets)
            return target

    async def _file_delete_preset(self, preset_id: str) -> None:
        async with self._file_lock:
            presets = await self._read_file_presets()
            filtered = [p for p in presets if p["id"] != preset_id]
            if len(filtered) == len(presets):
                raise PresetNotFound(f"Preset '{preset_id}' not found")
            await self._write_file_presets(filtered)

    # ==================== Unified Public Facade ====================

    async def list_presets(self) -> list[dict[str, Any]]:
        if self.pool is not None:
            return await self._pg_list_presets()
        return await self._file_list_presets()

    async def get_preset(self, preset_id: str) -> dict[str, Any]:
        if self.pool is not None:
            return await self._pg_get_preset(preset_id)
        return await self._file_get_preset(preset_id)

    async def create_preset(
        self,
        name: str,
        description: str = "",
        settings: dict[str, Any] | None = None,
        is_default: bool = False,
    ) -> dict[str, Any]:
        if self.pool is not None:
            return await self._pg_create_preset(name, description, settings, is_default)
        return await self._file_create_preset(name, description, settings, is_default)

    async def update_preset(
        self,
        preset_id: str,
        name: str | None = None,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
        is_default: bool | None = None,
    ) -> dict[str, Any]:
        if self.pool is not None:
            return await self._pg_update_preset(preset_id, name, description, settings, is_default)
        return await self._file_update_preset(preset_id, name, description, settings, is_default)

    async def delete_preset(self, preset_id: str) -> None:
        if self.pool is not None:
            return await self._pg_delete_preset(preset_id)
        return await self._file_delete_preset(preset_id)

    async def set_default_preset(self, preset_id: str) -> dict[str, Any]:
        return await self.update_preset(preset_id, is_default=True)
