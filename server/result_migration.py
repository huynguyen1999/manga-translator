"""Idempotent migration of legacy result folders into workspace/results."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger("manga-translator.server")


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file():
            digest.update(str(child.relative_to(path)).encode())
            digest.update(child.read_bytes())
    return digest.hexdigest()


def migrate_legacy_results(legacy_root: str | Path, result_root: str | Path, mapping_path: str | Path) -> dict[str, str]:
    legacy_root = Path(legacy_root).resolve()
    result_root = Path(result_root).resolve()
    mapping_path = Path(mapping_path).resolve()
    result_root.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    if mapping_path.is_file():
        try:
            value = json.loads(mapping_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                mapping = {str(k): str(v) for k, v in value.items()}
        except (OSError, ValueError):
            logger.warning("Ignoring malformed legacy result migration map: %s", mapping_path)

    if not legacy_root.is_dir():
        return mapping

    changed = False
    for source in sorted(legacy_root.iterdir(), key=lambda path: path.name):
        if not source.is_dir() or source.name.startswith("."):
            continue
        key = str(source)
        destination_name = mapping.get(key, source.name)
        destination = result_root / destination_name
        if destination.is_dir() and _tree_digest(source) == _tree_digest(destination):
            mapping[key] = destination_name
            continue
        if destination.exists():
            suffix = hashlib.sha256(key.encode()).hexdigest()[:10]
            conflict_name = f"{source.name}-legacy-{suffix}"
            destination_name = conflict_name
            destination = result_root / destination_name
            conflict_index = 0
            while destination.exists():
                if destination.is_dir() and _tree_digest(source) == _tree_digest(destination):
                    mapping[key] = destination_name
                    break
                conflict_index += 1
                destination_name = f"{conflict_name}-{conflict_index}"
                destination = result_root / destination_name
            if destination.exists():
                continue
            logger.warning("Legacy result folder conflicts; installing %s as %s", source.name, destination_name)

        temporary = Path(tempfile.mkdtemp(prefix=f".{destination_name}-", dir=result_root))
        try:
            shutil.copytree(source, temporary, dirs_exist_ok=True)
            if _tree_digest(source) != _tree_digest(temporary):
                raise OSError(f"Verification failed for legacy result {source}")
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        mapping[key] = destination_name
        changed = True

    if changed or not mapping_path.exists():
        temporary = mapping_path.with_name(f".{mapping_path.name}.tmp")
        temporary.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, mapping_path)
    return mapping
