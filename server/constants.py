# -*- coding: utf-8 -*-
"""Constants and paths for the HTTP/WebSocket manga translation server."""

from pathlib import Path
import os

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

WORKSPACE_ROOT = PROJECT_ROOT / "workspace"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
MANGA_DATA_ROOT = Path(os.getenv("MANGA_DATA_ROOT", str(WORKSPACE_ROOT))).expanduser().resolve()
BATCH_ROOT = MANGA_DATA_ROOT / "batches"
SERVER_RESULT_ROOT = MANGA_DATA_ROOT / "results"
LEGACY_RESULT_ROOT = PROJECT_ROOT / "result"
MIGRATION_MAP_PATH = SERVER_RESULT_ROOT / ".legacy-migrations.json"

UPLOAD_CACHE_DIR = 'upload-cache'
DEFAULT_RESULT_DIR = 'result'

HEADER_X_NONCE = 'X-Nonce'

MEDIA_TYPE_PNG = 'image/png'
MEDIA_TYPE_ZIP = 'application/zip'
MEDIA_TYPE_JSON = 'application/json'

SUPERVISOR_INTERVAL_SEC = 2.0
EXECUTOR_MODE_INPROCESS = 'inprocess'
