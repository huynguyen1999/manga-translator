"""Small shared helpers for PostgreSQL stores."""

import json
import re
from typing import Any

_SAFE_FOLDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")

def _json_load(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return default
def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
def _safe_folder(folder: str) -> str:
    if not isinstance(folder, str) or not _SAFE_FOLDER.fullmatch(folder):
        raise ValueError("Invalid result folder")
    return folder
def _page_order(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
