"""Filesystem scans that find completed results missing from the database index."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from server.image_variants import final_file


async def index_untracked_results(store: Any, logger: logging.Logger) -> dict[str, int]:
    if store.pool is None:
        raise RuntimeError("PostgreSQL store is not started")
    folder_names = await asyncio.to_thread(
        lambda: [
            entry.name
            for entry in os.scandir(store.result_root)
            if entry.is_dir() and not entry.name.startswith(".")
        ]
    )
    if not folder_names:
        return {"indexed": 0, "skipped": 0}
    known = await store.pool.fetch(
        "SELECT folder FROM pages WHERE active AND folder=ANY($1::text[])",
        folder_names,
    )
    known_folders = {row["folder"] for row in known}
    untracked = [name for name in folder_names if name not in known_folders]
    indexed = 0
    skipped = 0
    for name in untracked:
        folder = store.result_root / name
        if final_file(folder) is None:
            continue
        try:
            await store.sync_result_folder(folder, generate_variants=False)
            indexed += 1
        except Exception as error:
            skipped += 1
            logger.warning("Failed to index new result folder %s: %s", folder, error)
    return {"indexed": indexed, "skipped": skipped}
