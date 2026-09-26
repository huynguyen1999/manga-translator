"""Atomic filesystem storage for server-owned translation batches."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from server.image_variants import final_file
from manga_translator.utils.image_storage import find_asset
from manga_translator.config import MAX_MANGA_TITLE_LENGTH
from manga_translator.pipeline.stages import PipelineStage, STAGE_ORDER, stage_from_progress
from typing import Any, Callable


class BatchStoreError(Exception):
    pass


class BatchNotFound(BatchStoreError):
    pass


class BatchConflict(BatchStoreError):
    pass


class InvalidBatch(BatchStoreError):
    pass


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ITEM_STATUSES = {"queued", "processing", "completed", "error"}
_BATCH_STATUSES = {"waiting", "processing", "paused", "completed", "error", "stopping"}
_BATCH_KINDS = {"translation", "manga-upload", "rerender", "pipeline-rerun"}


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise InvalidBatch(f"Invalid {label}")
    return value


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _valid_page_order(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        order = int(value)
    except (TypeError, ValueError):
        return None
    return order if order > 0 else None


def _group_keys(item: dict[str, Any]) -> tuple[str, ...]:
    keys = []
    if item.get("mangaGroupId"):
        keys.append(f"id:{item['mangaGroupId']}")
    if item.get("mangaTitle"):
        keys.append(f"title:{item['mangaTitle']}")
    return tuple(keys or ("title:Ungrouped",))


_BATCH_STAGE_ALIASES = {
    "initialize": PipelineStage.INPUT,
    "starting": PipelineStage.INPUT,
    "queued": PipelineStage.INPUT,
    "reserved": PipelineStage.TRANSLATION,
    "awaiting_translation": PipelineStage.TRANSLATION,
}


def _batch_progress(items: list[dict[str, Any]]) -> dict[str, Any]:
    def stage_for(item: dict[str, Any]) -> PipelineStage:
        value = str(
            item.get("retryFromStage")
            or item.get("pipelineStage")
            or item.get("stage")
            or "initialize"
        )
        stage = stage_from_progress(value) or _BATCH_STAGE_ALIASES.get(value)
        if stage is not None:
            return stage
        try:
            return PipelineStage(value)
        except ValueError:
            return PipelineStage.INPUT

    unfinished = [item for item in items if item.get("status") not in {"completed", "error"}]
    if not unfinished:
        return {}

    current_stage = min(
        (stage_for(item) for item in unfinished), key=STAGE_ORDER.index
    )
    current_index = STAGE_ORDER.index(current_stage)
    return {
        "currentStage": current_stage.value,
        "currentStagePassedCount": sum(
            item.get("status") == "completed" or STAGE_ORDER.index(stage_for(item)) > current_index
            for item in items
        ),
    }


class BatchStore:
    """Single-process async-locked store for batch manifests and uploaded inputs."""

    def __init__(self, root: str | Path, result_root: str | Path):
        self.root = Path(root).resolve()
        self.result_root = Path(result_root).resolve()
        self._lock = asyncio.Lock()

    def _batch_dir(self, batch_id: str) -> Path:
        return self.root / _safe_id(batch_id, "batch ID")

    def _manifest_path(self, batch_id: str) -> Path:
        return self._batch_dir(batch_id) / "manifest.json"

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidBatch(f"Malformed manifest: {path}") from exc
        if not isinstance(value, dict):
            raise InvalidBatch("Manifest must be an object")
        return value

    @staticmethod
    def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _normalize_manifest(self, batch_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("id", batch_id) != batch_id:
            raise InvalidBatch("Manifest ID does not match URL")
        items = raw.get("items")
        if not isinstance(items, list) or not items:
            raise InvalidBatch("Manifest must contain at least one item")
        title = raw.get("title", raw.get("mangaTitle", "Ungrouped"))
        if not isinstance(title, str):
            raise InvalidBatch("Batch title must be a string")
        title = title.strip() or "Ungrouped"
        if len(title) > MAX_MANGA_TITLE_LENGTH:
            raise InvalidBatch(
                f"Batch title must be at most {MAX_MANGA_TITLE_LENGTH} characters"
            )
        kind = raw.get("kind")
        if kind is not None and (not isinstance(kind, str) or kind not in _BATCH_KINDS):
            raise InvalidBatch(f"Invalid batch kind: {kind}")

        manga_group_id = raw.get("mangaGroupId") or raw.get("groupId")
        if manga_group_id is not None and not isinstance(manga_group_id, str):
            raise InvalidBatch("mangaGroupId must be a string")
        manga_group_id = manga_group_id.strip() if manga_group_id else None
        is_new_group = bool(raw.get("isNewGroup", False))

        normalized_items: list[dict[str, Any]] = []
        item_ids: set[str] = set()
        for raw_item in items:
            if not isinstance(raw_item, dict):
                raise InvalidBatch("Invalid batch item")
            item_id = _safe_id(raw_item.get("id"), "item ID")
            if item_id in item_ids:
                raise InvalidBatch("Duplicate item ID")
            item_ids.add(item_id)
            name = raw_item.get("name") or raw_item.get("originalName")
            if not isinstance(name, str) or not name.strip():
                raise InvalidBatch("Each item needs a name")
            status = raw_item.get("status", "queued")
            if status == "finished":
                status = "completed"
            if status == "processing":
                status = "queued"
            if status not in _ITEM_STATUSES:
                raise InvalidBatch(f"Invalid item status: {status}")
            item = copy.deepcopy(raw_item)
            item_title = item.get("mangaTitle", title)
            if not isinstance(item_title, str):
                raise InvalidBatch("Item manga title must be a string")
            if len(item_title.strip()) > MAX_MANGA_TITLE_LENGTH:
                raise InvalidBatch(
                    f"Item manga title must be at most {MAX_MANGA_TITLE_LENGTH} characters"
                )
            item_group_id = raw_item.get("mangaGroupId") or raw_item.get("groupId") or manga_group_id
            if item_group_id is not None and not isinstance(item_group_id, str):
                raise InvalidBatch("Item mangaGroupId must be a string")
            item.update({
                "id": item_id,
                "name": name,
                "status": status,
                "mangaGroupId": item_group_id.strip() if item_group_id else None,
                "pageId": raw_item.get("pageId"),
            })
            page_order = raw_item.get("pageOrder")
            if page_order is not None:
                try:
                    page_order = int(page_order)
                except (TypeError, ValueError) as exc:
                    raise InvalidBatch("pageOrder must be a positive integer") from exc
                if page_order < 1:
                    raise InvalidBatch("pageOrder must be a positive integer")
            item["pageOrder"] = page_order
            source_path = raw_item.get("sourcePath")
            if source_path is not None and not isinstance(source_path, str):
                raise InvalidBatch("sourcePath must be a string")
            item["sourcePath"] = source_path
            item.setdefault("stage", None)
            item.setdefault("error", None)
            item.setdefault("resultFolder", item.get("folder"))
            item["mangaTitle"] = item_title.strip() or title
            if item["resultFolder"] is not None and (
                not isinstance(item["resultFolder"], str)
                or Path(item["resultFolder"]).name != item["resultFolder"]
            ):
                raise InvalidBatch("Invalid result folder")
            item.setdefault("requestId", f"{batch_id}:{item_id}")
            item.setdefault("model", {})
            item.setdefault("addedAt", raw.get("addedAt", _now_ms()))
            normalized_items.append(item)

        settings = raw.get("settings", {})
        if not isinstance(settings, dict):
            raise InvalidBatch("Batch settings must be an object")
        settings = copy.deepcopy(settings)
        translation_quality = settings.get("translationQuality", "fast")
        if translation_quality not in {"fast", "professional"}:
            raise InvalidBatch("translationQuality must be fast or professional")
        settings["translationQuality"] = translation_quality
        story_page_ranges = settings.get("storyPageRanges", "")
        if not isinstance(story_page_ranges, str):
            raise InvalidBatch("storyPageRanges must be a string")
        settings["storyPageRanges"] = story_page_ranges.strip()
        if settings["storyPageRanges"] and not re.fullmatch(r"\s*\d+\s*-\s*\d+(?:\s*,\s*\d+\s*-\s*\d+)*\s*", settings["storyPageRanges"]):
            raise InvalidBatch("storyPageRanges must look like 1-12,13-24")
        story_plan = settings.get("storyPlan")
        if story_plan is not None:
            if not isinstance(story_plan, dict):
                raise InvalidBatch("storyPlan must be an object")
            if not all(isinstance(story_plan.get(key, default), bool) for key, default in (("enabled", True), ("autoDetect", True), ("mergeAllPages", False))):
                raise InvalidBatch("storyPlan flags must be booleans")
            segments = story_plan.get("segments", [])
            archives = story_plan.get("archives", [])
            if not isinstance(segments, list) or not isinstance(archives, list):
                raise InvalidBatch("storyPlan archives and segments must be arrays")
            for segment in segments:
                if not isinstance(segment, dict) or not isinstance(segment.get("startPage"), int) or not isinstance(segment.get("endPage"), int):
                    raise InvalidBatch("storyPlan segments must contain integer page bounds")
                if segment["startPage"] < 1 or segment["endPage"] < segment["startPage"]:
                    raise InvalidBatch("storyPlan contains an invalid segment")
            ordered_segments = sorted(segments, key=lambda segment: segment["startPage"])
            if ordered_segments and ordered_segments[0]["startPage"] != 1:
                raise InvalidBatch("storyPlan segments must start at page 1")
            if any(left["endPage"] + 1 != right["startPage"] for left, right in zip(ordered_segments, ordered_segments[1:])):
                raise InvalidBatch("storyPlan segments must be contiguous")
            settings["storyPlan"] = copy.deepcopy(story_plan)
        try:
            translation_batch_size = int(settings.get("translationBatchSize", 20))
        except (TypeError, ValueError) as exc:
            raise InvalidBatch("translationBatchSize must be an integer") from exc
        if not 1 <= translation_batch_size <= 100:
            raise InvalidBatch("translationBatchSize must be between 1 and 100")
        settings["translationBatchSize"] = translation_batch_size
        status = raw.get("status", "waiting")
        if status == "queued":
            status = "waiting"
        if status not in _BATCH_STATUSES:
            raise InvalidBatch(f"Invalid batch status: {status}")
        if status == "processing":
            status = "waiting"
        item_statuses = {item["status"] for item in normalized_items}
        if item_statuses and not item_statuses & {"queued", "processing"}:
            status = "error" if "error" in item_statuses else "completed"

        normalized = copy.deepcopy(raw)
        normalized.update(
            {
                "id": batch_id,
                "title": title,
                "mangaTitle": title,
                "mangaGroupId": manga_group_id,
                "isNewGroup": is_new_group,
                **({"kind": kind} if kind is not None else {}),
                "settings": settings,
                "items": normalized_items,
                "status": status,
                "priority": bool(raw.get("priority", False)),
                "dismissed": bool(raw.get("dismissed", False)),
                "addedAt": raw.get("addedAt", _now_ms()),
                "updatedAt": raw.get("updatedAt", _now_ms()),
            }
        )
        try:
            total_items = int(raw.get("totalItems", len(normalized_items)))
            completed_count = int(raw.get("completedCount", 0))
        except (TypeError, ValueError) as exc:
            raise InvalidBatch("Batch counts must be integers") from exc
        normalized["totalItems"] = max(len(normalized_items), total_items)
        normalized["completedCount"] = max(
            sum(item["status"] == "completed" for item in normalized_items), completed_count
        )
        return normalized

    @staticmethod
    def _submission_shape(manifest: dict[str, Any]) -> tuple[Any, ...]:
        return (
            manifest.get("id"),
            tuple(
                (
                    item.get("id"),
                    item.get("name"),
                    item.get("sourcePath"),
                )
                for item in manifest.get("items", [])
            ),
        )

    def _reserve_page_orders(self, manifest: dict[str, Any]) -> None:
        max_orders: dict[str, int] = {}
        unknown_counts: dict[str, int] = {}

        def observe(item: dict[str, Any]) -> None:
            order = _valid_page_order(item.get("pageOrder"))
            for key in _group_keys(item):
                if order is None:
                    unknown_counts[key] = unknown_counts.get(key, 0) + 1
                else:
                    max_orders[key] = max(max_orders.get(key, 0), order)

        if self.result_root.is_dir():
            for folder in self.result_root.iterdir():
                if not folder.is_dir() or (folder / ".ai-case").is_file() or final_file(folder) is None:
                    continue
                try:
                    metadata = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(metadata, dict):
                    observe(metadata)

        if self.root.is_dir():
            for batch_dir in self.root.iterdir():
                if not batch_dir.is_dir() or batch_dir.name.startswith("."):
                    continue
                manifest_path = batch_dir / "manifest.json"
                if not manifest_path.is_file() or batch_dir.name == manifest["id"]:
                    continue
                try:
                    existing = self._read_manifest(manifest_path)
                except InvalidBatch:
                    continue
                if existing.get("dismissed"):
                    continue
                for item in existing.get("items", []):
                    if item.get("status") in {"queued", "processing", "error"} or _valid_page_order(item.get("pageOrder")):
                        observe(item)

        for item in manifest.get("items", []):
            keys = _group_keys(item)
            order = _valid_page_order(item.get("pageOrder"))
            if order is not None and (item.get("pageId") or item.get("resultFolder")):
                for key in keys:
                    max_orders[key] = max(max_orders.get(key, 0), order)
                continue

            next_order = max(
                max((max_orders.get(key, 0) for key in keys), default=0),
                max((unknown_counts.get(key, 0) for key in keys), default=0),
            ) + 1
            item["pageOrder"] = next_order
            for key in keys:
                max_orders[key] = next_order

    def _to_dto(self, manifest: dict[str, Any]) -> dict[str, Any]:
        batch_id = manifest["id"]
        items = []
        for item in manifest.get("items", []):
            item_id = item["id"]
            input_path = self._input_path(batch_id, item)
            folder = item.get("resultFolder")
            if folder is not None and (
                not isinstance(folder, str) or Path(folder).name != folder
            ):
                raise InvalidBatch("Invalid result folder")
            result = self.result_root / folder if isinstance(folder, str) else None
            final = final_file(result) if result is not None else None
            result_url = f"/result/{folder}/{final.name}" if folder and final else None
            batch_preview_url = f"/result/{folder}/batch.webp" if folder else None
            result_input = (
                find_asset(result, "input")
                or find_asset(result, "inpainted")
                or (final if final is not None else None)
            ) if result is not None else None
            input_url = (
                f"/api/batches/{batch_id}/items/{item_id}/input"
                if input_path.is_file()
                else f"/result/{folder}/{result_input.name}"
                if result_input and result_input.is_file()
                else None
            )
            items.append(
                {
                    "id": item_id,
                    "name": item["name"],
                    "mangaGroupId": item.get("mangaGroupId"),
                    "pageId": item.get("pageId"),
                    "isolatedRerun": bool(item.get("isolatedRerun", False)),
                    "pageOrder": item.get("pageOrder"),
                    "sourcePath": item.get("sourcePath"),
                    "mangaTitle": item.get("mangaTitle", manifest.get("mangaTitle", "Ungrouped")),
                    "status": item.get("status", "queued"),
                    "stage": item.get("stage"),
                    "stageStartedAt": item.get("stageStartedAt"),
                    "error": item.get("error"),
                    "addedAt": item.get("addedAt", manifest.get("addedAt")),
                    "inputUrl": input_url,
                    "resultFolder": folder,
                    "resultUrl": result_url,
                    "fullUrl": result_url,
                    "batchPreviewUrl": batch_preview_url,
                    "requestId": item.get("requestId"),
                    "model": item.get("model") or {},
                    "excludeColor": bool(item.get("excludeColor", False)),
                    "needsReview": bool(item.get("needsReview", False)),
                    "pipelineStage": item.get("pipelineStage"),
                    "retryFromStage": item.get("retryFromStage"),
                }
            )
        return {
            "id": batch_id,
            "title": manifest.get("title", manifest.get("mangaTitle", "Ungrouped")),
            "mangaTitle": manifest.get("mangaTitle", manifest.get("title", "Ungrouped")),
            "mangaGroupId": manifest.get("mangaGroupId"),
            "isNewGroup": bool(manifest.get("isNewGroup", False)),
            "kind": manifest.get("kind"),
            "addedAt": manifest.get("addedAt"),
            "updatedAt": manifest.get("updatedAt"),
            "settings": manifest.get("settings", {}),
            "status": manifest.get("status", "waiting"),
            "priority": bool(manifest.get("priority", False)),
            "dismissed": bool(manifest.get("dismissed", False)),
            "totalItems": manifest.get("totalItems", len(items)),
            "completedCount": manifest.get(
                "completedCount", sum(item["status"] == "completed" for item in items)
            ),
            "queuedCount": sum(item.get("status") == "queued" or item.get("stage") in {"awaiting_translation", "reserved"} for item in items),
            "processingCount": sum(item.get("status") == "processing" and item.get("stage") not in {"awaiting_translation", "reserved"} for item in items),
            "failedCount": sum(item.get("status") == "error" for item in items),
            "needsReviewCount": sum(bool(item.get("needsReview")) for item in items),
            **_batch_progress(manifest.get("items", [])),
            "items": items,
        }

    @staticmethod
    def _to_summary(manifest: dict[str, Any]) -> dict[str, Any]:
        items = manifest.get("items", [])
        manga_group_id = manifest.get("mangaGroupId") or (items[0].get("mangaGroupId") if items else None)
        return {
            "id": manifest["id"],
            "title": manifest.get("title", manifest.get("mangaTitle", "Ungrouped")),
            "mangaTitle": manifest.get("mangaTitle", manifest.get("title", "Ungrouped")),
            "mangaGroupId": manga_group_id,
            "isNewGroup": bool(manifest.get("isNewGroup", False)),
            "kind": manifest.get("kind"),
            "addedAt": manifest.get("addedAt"),
            "updatedAt": manifest.get("updatedAt"),
            "settings": manifest.get("settings", {}),
            "status": manifest.get("status", "waiting"),
            "priority": bool(manifest.get("priority", False)),
            "dismissed": bool(manifest.get("dismissed", False)),
            "totalItems": manifest.get("totalItems", len(items)),
            "completedCount": manifest.get(
                "completedCount", sum(item.get("status") == "completed" for item in items)
            ),
            "queuedCount": sum(item.get("status") == "queued" or item.get("stage") in {"awaiting_translation", "reserved"} for item in items),
            "processingCount": sum(item.get("status") == "processing" and item.get("stage") not in {"awaiting_translation", "reserved"} for item in items),
            "failedCount": sum(item.get("status") == "error" for item in items),
            "needsReviewCount": sum(bool(item.get("needsReview")) for item in items),
            **_batch_progress(items),
        }

    def _input_path(self, batch_id: str, item: dict[str, Any]) -> Path:
        relative = item.get("input")
        if relative:
            path = (self._batch_dir(batch_id) / relative).resolve()
            inputs = (self._batch_dir(batch_id) / "inputs").resolve()
            if path.parent != inputs:
                raise InvalidBatch("Invalid input path")
            return path
        suffix = Path(item.get("name", "")).suffix.lower() or ".bin"
        return self._batch_dir(batch_id) / "inputs" / f"{item['id']}{suffix}"

    async def put_batch(
        self,
        batch_id: str,
        manifest: dict[str, Any],
        files: dict[str, tuple[str, bytes]],
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._put_batch, batch_id, manifest, files)

    def _put_batch(
        self,
        batch_id: str,
        manifest: dict[str, Any],
        files: dict[str, tuple[str, bytes]],
    ) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        batch_id = _safe_id(batch_id, "batch ID")
        normalized = self._normalize_manifest(batch_id, manifest)
        batch_dir = self._batch_dir(batch_id)
        if batch_dir.exists():
            existing = self._read_manifest(batch_dir / "manifest.json")
            if self._submission_shape(existing) == self._submission_shape(normalized):
                return self._to_dto(existing)
            raise BatchConflict(f"Batch {batch_id} already exists with different data")

        expected = set() if normalized.get("kind") in {"rerender", "pipeline-rerun"} else {
            item["id"] for item in normalized["items"] if item["status"] != "completed"
        }
        if set(files) != expected:
            missing = expected - set(files)
            extra = set(files) - expected
            detail = []
            if missing:
                detail.append(f"missing uploads: {sorted(missing)}")
            if extra:
                detail.append(f"unknown uploads: {sorted(extra)}")
            raise InvalidBatch("; ".join(detail) or "Upload does not match manifest")

        # ponytail: batch-time O(results + batches) scan; add an order index if creation becomes hot.
        self._reserve_page_orders(normalized)

        temporary = Path(tempfile.mkdtemp(prefix=f".{batch_id}-", dir=self.root))
        try:
            inputs = temporary / "inputs"
            inputs.mkdir()
            for item in normalized["items"]:
                if item["status"] == "completed":
                    continue
                if normalized.get("kind") in {"rerender", "pipeline-rerun"}:
                    continue
                filename, content = files[item["id"]]
                if not isinstance(filename, str) or Path(filename).name != filename:
                    raise InvalidBatch("Invalid uploaded filename")
                suffix = Path(filename).suffix.lower() or Path(item["name"]).suffix.lower() or ".bin"
                item["input"] = f"inputs/{item['id']}{suffix}"
                dest = inputs / f"{item['id']}{suffix}"
                if isinstance(content, bytes):
                    dest.write_bytes(content)
                elif hasattr(content, "file"):
                    content.file.seek(0)
                    with dest.open("wb") as out:
                        shutil.copyfileobj(content.file, out)
                elif hasattr(content, "read"):
                    content.seek(0)
                    with dest.open("wb") as out:
                        shutil.copyfileobj(content, out)
                else:
                    dest.write_bytes(bytes(content))
            normalized["updatedAt"] = _now_ms()
            self._write_manifest(temporary / "manifest.json", normalized)
            os.replace(temporary, batch_dir)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return self._to_dto(normalized)

    async def list_runnable_batches(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_runnable_batches)

    async def list_batches(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_batches)

    async def list_batch_summaries(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_batch_summaries)

    async def update_review_for_result(self, folder: str, needs_review: bool) -> int:
        if not isinstance(folder, str) or Path(folder).name != folder:
            raise InvalidBatch("Invalid result folder")
        async with self._lock:
            return await asyncio.to_thread(self._update_review_for_result, folder, needs_review)

    def _update_review_for_result(self, folder: str, needs_review: bool) -> int:
        updated = 0
        for manifest in self._list_manifests():
            changed = False
            for item in manifest.get("items", []):
                if item.get("resultFolder") == folder and item.get("needsReview") != needs_review:
                    item["needsReview"] = needs_review
                    updated += 1
                    changed = True
            if changed:
                manifest["updatedAt"] = _now_ms()
                self._write_manifest(self._manifest_path(manifest["id"]), manifest)
        return updated

    def _list_manifests(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        batches = []
        for path in self.root.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            manifest_path = path / "manifest.json"
            if manifest_path.is_file():
                batches.append(self._read_manifest(manifest_path))
        batches.sort(
            key=lambda value: (
                not bool(value.get("priority", False)),
                value.get("addedAt", 0),
                value["id"],
            )
        )
        return batches

    def _list_runnable_batches(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        batches = []
        for path in self.root.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            manifest_path = path / "manifest.json"
            if manifest_path.is_file():
                manifest = self._read_manifest(manifest_path)
                if manifest.get("status") in {"waiting", "processing"}:
                    batches.append(manifest)
        batches.sort(
            key=lambda value: (
                not bool(value.get("priority", False)),
                value.get("addedAt", 0),
                value["id"],
            )
        )
        return batches

    def _list_batches(self) -> list[dict[str, Any]]:
        return [self._to_dto(value) for value in self._list_manifests()]

    def _list_batch_summaries(self) -> list[dict[str, Any]]:
        return [self._to_summary(value) for value in self._list_manifests()]

    async def get_batch(self, batch_id: str) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._get_batch, batch_id)

    def _get_batch(self, batch_id: str) -> dict[str, Any]:
        path = self._manifest_path(batch_id)
        if not path.is_file():
            raise BatchNotFound(batch_id)
        return self._to_dto(self._read_manifest(path))

    async def mutate(self, batch_id: str, mutator: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._mutate, batch_id, mutator)

    def _mutate(self, batch_id: str, mutator: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        path = self._manifest_path(batch_id)
        if not path.is_file():
            raise BatchNotFound(batch_id)
        manifest = self._read_manifest(path)
        result = mutator(manifest)
        if result is False:
            return self._to_dto(manifest)
        manifest["updatedAt"] = _now_ms()
        manifest["totalItems"] = max(
            len(manifest.get("items", [])), int(manifest.get("totalItems", 0))
        )
        manifest["completedCount"] = max(
            sum(item.get("status") == "completed" for item in manifest.get("items", [])),
            int(manifest.get("completedCount", 0)),
        )
        self._write_manifest(path, manifest)
        return self._to_dto(manifest)

    async def delete_batch(self, batch_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_batch, batch_id)

    def _delete_batch(self, batch_id: str) -> None:
        batch_dir = self._batch_dir(batch_id)
        if batch_dir.exists():
            shutil.rmtree(batch_dir)

    async def input_path(self, batch_id: str, item_id: str) -> Path:
        async with self._lock:
            return await asyncio.to_thread(self._input_path_for_item, batch_id, item_id)

    def _input_path_for_item(self, batch_id: str, item_id: str) -> Path:
        manifest = self._read_manifest(self._manifest_path(batch_id))
        for item in manifest.get("items", []):
            if item.get("id") == _safe_id(item_id, "item ID"):
                path = self._input_path(batch_id, item)
                if path.is_file():
                    return path
                folder = item.get("resultFolder")
                if folder and isinstance(folder, str):
                    result = self.result_root / folder
                    for stem in ("input", "inpainted", "final"):
                        fallback = find_asset(result, stem)
                        if fallback and fallback.is_file():
                            return fallback
                break
        raise BatchNotFound(f"{batch_id}/{item_id}")

    async def reconcile(self, result_root: str | Path | None = None) -> None:
        root = Path(result_root or self.result_root).resolve()
        async with self._lock:
            await asyncio.to_thread(self._reconcile, root)

    def _reconcile(self, result_root: Path) -> None:
        if not self.root.is_dir():
            return
        # ponytail: restart-only O(n) result scan; add a request-id index if recovery gets slow.
        def find_result(request_id: Any) -> Path | None:
            if not isinstance(request_id, str) or not result_root.is_dir():
                return None
            for candidate in result_root.iterdir():
                if not candidate.is_dir() or final_file(candidate) is None:
                    continue
                try:
                    meta = json.loads((candidate / "meta.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if meta.get("requestId") == request_id:
                    return candidate
            return None

        for path in self.root.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            manifest_path = path / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = self._read_manifest(manifest_path)
            if manifest.get("status") == "stopping":
                shutil.rmtree(path)
                continue
            changed = False
            for item in manifest.get("items", []):
                if item.get("status") != "processing":
                    continue
                folder = item.get("resultFolder")
                result = (
                    result_root / folder
                    if isinstance(folder, str) and Path(folder).name == folder
                    else None
                )
                meta_path = result / "meta.json" if result else None
                request_id = item.get("requestId")
                if result is None or not result.is_dir():
                    result = find_result(request_id)
                    meta_path = result / "meta.json" if result else None
                completed = False
                if result and result.is_dir() and final_file(result) is not None and meta_path and meta_path.is_file():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        completed = meta.get("requestId") == request_id
                    except (OSError, ValueError):
                        completed = False
                if completed:
                    item["status"] = "completed"
                    item["stage"] = "finished"
                    item["resultFolder"] = result.name
                    changed = True
                    input_path = self._input_path(path.name, item)
                    input_path.unlink(missing_ok=True)
                else:
                    item["status"] = "queued"
                    item["stage"] = None
                    changed = True
            if manifest.get("status") in {"processing", "waiting"}:
                statuses = {item.get("status") for item in manifest.get("items", [])}
                next_status = (
                    "error" if "error" in statuses else
                    "completed" if statuses and statuses <= {"completed"} else
                    "waiting"
                )
                if manifest.get("status") != next_status:
                    manifest["status"] = next_status
                    changed = True
            if changed:
                manifest["updatedAt"] = _now_ms()
                manifest["completedCount"] = max(
                    sum(item.get("status") == "completed" for item in manifest.get("items", [])),
                    int(manifest.get("completedCount", 0)),
                )
                self._write_manifest(manifest_path, manifest)
