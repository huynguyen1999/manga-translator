"""Compatibility document persistence used by the PostgresStore facade."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from server.pipeline_repository import _PIPELINE_DOCUMENT_TYPES
from server.postgres_common import _json_dump, _json_load, _safe_folder


class DocumentRepository:
    def __init__(self, store: Any):
        self._store = store

    @property
    def pool(self) -> Any:
        return self._store.pool

    async def _document_owner(self, value: str, connection: Any | None = None):
        return await self._store._document_owner(value, connection)

    async def save_documents(self, folder: str, documents: dict[str, Any]) -> None:
        """Persist virtual JSON sidecars without writing them beside image assets."""
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        folder = _safe_folder(folder)
        rows = []
        for name, payload in documents.items():
            if Path(name).name != name or not name.endswith(".json"):
                raise ValueError(f"Invalid result document name: {name}")
            rows.append((name, _json_dump(payload)))
        if not rows:
            return
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                owner = await self._document_owner(folder, connection)
                if owner is None:
                    owner_id = str(uuid.uuid4())
                    await connection.execute(
                        "INSERT INTO pipeline_runs(id,folder) VALUES($1,$2) ON CONFLICT(folder) DO NOTHING",
                        owner_id,
                        folder,
                    )
                    owner_id = await connection.fetchval(
                        "SELECT id FROM pipeline_runs WHERE folder=$1", folder
                    )
                    owner = ("pipeline", owner_id)
                kind, owner_id = owner
                if kind == "page":
                    await connection.fetchrow(
                        "SELECT id FROM pages WHERE id=$1 FOR UPDATE", owner_id
                    )
                    structured_rows = [row for row in rows if row[0] in _PIPELINE_DOCUMENT_TYPES]
                    legacy_rows = [row for row in rows if row[0] not in _PIPELINE_DOCUMENT_TYPES]
                    await connection.execute(
                        "DELETE FROM result_documents WHERE page_id=$1 AND name=ANY($2::text[])",
                        owner_id,
                        [row[0] for row in rows],
                    )
                    for name, payload in structured_rows:
                        stage, document_type = _PIPELINE_DOCUMENT_TYPES[name]
                        revision = int(await connection.fetchval(
                            """SELECT COALESCE(MAX(revision),0)+1 FROM pipeline_documents
                               WHERE page_id=$1 AND stage=$2 AND document_type=$3""",
                            owner_id,
                            stage,
                            document_type,
                        ))
                        await connection.execute(
                            """UPDATE pipeline_documents SET active=FALSE
                               WHERE page_id=$1 AND stage=$2 AND document_type=$3 AND active""",
                            owner_id,
                            stage,
                            document_type,
                        )
                        await connection.execute(
                            """INSERT INTO pipeline_documents(
                                   page_id,stage,document_type,revision,schema_version,payload,active
                               ) VALUES($1,$2,$3,$4,1,$5::jsonb,TRUE)""",
                            owner_id,
                            stage,
                            document_type,
                            revision,
                            payload,
                        )
                    if legacy_rows:
                        await connection.executemany(
                            "INSERT INTO result_documents(page_id,name,payload,updated_at) VALUES($1,$2,$3::jsonb,now())",
                            [(owner_id, name, payload) for name, payload in legacy_rows],
                        )
                else:
                    await connection.execute(
                        "DELETE FROM result_documents WHERE pipeline_run_id=$1 AND name=ANY($2::text[])",
                        owner_id,
                        [row[0] for row in rows],
                    )
                    await connection.executemany(
                        "INSERT INTO result_documents(pipeline_run_id,name,payload,updated_at) VALUES($1,$2,$3::jsonb,now())",
                        [(owner_id, name, payload) for name, payload in rows],
                    )
    async def get_document(self, record_id: str, name: str) -> Any | None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        if Path(name).name != name or not name.endswith(".json"):
            return None
        owner = await self._document_owner(record_id)
        if owner is None:
            return None
        kind, owner_id = owner
        if kind == "page" and name in _PIPELINE_DOCUMENT_TYPES:
            stage_id, document_type = _PIPELINE_DOCUMENT_TYPES[name]
            value = await self.pool.fetchval(
                """SELECT payload FROM pipeline_documents
                   WHERE page_id=$1 AND stage=$2 AND document_type=$3 AND active""",
                owner_id,
                stage_id,
                document_type,
            )
            if value is not None:
                return _json_load(value, None)
        column = "page_id" if kind == "page" else "pipeline_run_id"
        value = await self.pool.fetchval(
            f"SELECT payload FROM result_documents WHERE {column}=$1 AND name=$2",
            owner_id,
            name,
        )
        return _json_load(value, None) if value is not None else None
    async def get_documents(self, folder: str) -> dict[str, Any]:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        owner = await self._document_owner(folder)
        if owner is None:
            return {}
        kind, owner_id = owner
        column = "page_id" if kind == "page" else "pipeline_run_id"
        rows = await self.pool.fetch(
            f"SELECT name,payload FROM result_documents WHERE {column}=$1",
            owner_id,
        )
        documents = {row["name"]: _json_load(row["payload"], None) for row in rows}
        if kind == "page":
            structured = await self.pool.fetch(
                """SELECT stage,document_type,payload FROM pipeline_documents
                   WHERE page_id=$1 AND active""",
                owner_id,
            )
            names = {
                (stage, document_type): name
                for name, (stage, document_type) in _PIPELINE_DOCUMENT_TYPES.items()
            }
            for row in structured:
                name = names.get((row["stage"], row["document_type"]))
                if name:
                    documents[name] = _json_load(row["payload"], None)
        return documents
    async def delete_documents(self, folder: str) -> None:
        if self.pool is None:
            raise RuntimeError("PostgreSQL store is not started")
        owner = await self._document_owner(folder)
        if owner is None:
            return
        kind, owner_id = owner
        column = "page_id" if kind == "page" else "pipeline_run_id"
        await self.pool.execute(f"DELETE FROM result_documents WHERE {column}=$1", owner_id)
        if kind == "pipeline":
            await self.pool.execute("DELETE FROM pipeline_runs WHERE id=$1", owner_id)
