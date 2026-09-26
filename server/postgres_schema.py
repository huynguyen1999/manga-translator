"""Connection startup and schema migration for the PostgresStore facade."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


async def start(store: Any, check_schema: bool = True) -> None:
    if not store.database_url:
        raise RuntimeError("DATABASE_URL is required")
    try:
        import asyncpg
    except ImportError as error:  # pragma: no cover - dependency is installed in production
        raise RuntimeError("asyncpg is required") from error

    store.pool = await asyncpg.create_pool(
        dsn=store.database_url,
        min_size=1,
        max_size=int(os.getenv("DATABASE_POOL_MAX_SIZE", "10")),
        command_timeout=30,
    )
    try:
        if check_schema:
            await store.check_schema()
    except Exception:
        await store.close()
        raise


async def close(store: Any) -> None:
    if store.pool is not None:
        await store.pool.close()
        store.pool = None


async def check_schema(store: Any) -> None:
    if store.pool is None:
        raise RuntimeError("PostgreSQL store is not started")
    latest = sorted(path.stem for path in MIGRATIONS_DIR.glob("*.sql"))[-1]
    try:
        version = await store.pool.fetchval(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        )
    except Exception as error:
        raise RuntimeError(
            "PostgreSQL schema is not ready; run `python -m server.db_cli migrate`"
        ) from error
    if version != latest:
        raise RuntimeError(
            "PostgreSQL schema is not ready; run `python -m server.db_cli migrate`"
        )


async def apply_migrations(store: Any) -> str:
    if store.pool is None:
        raise RuntimeError("PostgreSQL store is not started")
    applied = []
    try:
        applied_versions = {
            row["version"]
            for row in await store.pool.fetch("SELECT version FROM schema_migrations")
        }
    except Exception:
        applied_versions = set()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in applied_versions:
            applied.append(version)
            continue
        sql = path.read_text(encoding="utf-8")
        async with store.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(sql)
                has_id = await connection.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='schema_migrations'
                          AND column_name='id'
                    )
                    """
                )
                if has_id:
                    await connection.execute(
                        """
                        INSERT INTO schema_migrations(id,version)
                        VALUES($1,$2) ON CONFLICT(version) DO NOTHING
                        """,
                        f"migration-{version}",
                        version,
                    )
                else:
                    await connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES($1) ON CONFLICT DO NOTHING",
                        version,
                    )
        applied.append(version)
    return applied[-1] if applied else ""
