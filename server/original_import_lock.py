"""Shared process-local serialization for original manga page writes."""

import asyncio

# ponytail: process-local gate keeps the legacy route and importer serial; use a shared lock if server workers split.
_original_import_lock = asyncio.Lock()
