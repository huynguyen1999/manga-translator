"""Explicit PostgreSQL schema, asset relocation, import, and verification commands."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

from server.constants import BATCH_ROOT, MANGA_DATA_ROOT, SERVER_RESULT_ROOT, WORKSPACE_ROOT
from server.postgres_store import PostgresBatchStore, PostgresStore, _natural_sort_key
from server.image_variants import asset_version, generate_image_variants
from server.image_variants import final_file
from manga_translator.utils.image_storage import find_asset, save_jpeg


def _files(root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: str(path.relative_to(root)))


def _tree_stats(root: Path) -> dict[str, object]:
    paths = _files(root) if root.is_dir() else []
    total_bytes = sum(path.stat().st_size for path in paths)
    samples: dict[str, str] = {}
    for index, path in enumerate(paths):
        if index % max(1, len(paths) // 32) != 0:
            continue
        samples[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"files": len(paths), "bytes": total_bytes, "sampleSha256": samples}


def _copy_tree_preserving_source(source: Path, target: Path) -> dict[str, object]:
    if not source.is_dir():
        raise SystemExit(f"Source directory does not exist: {source}")
    target.mkdir(parents=True, exist_ok=True)
    for path in _files(source):
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.stat().st_size != path.stat().st_size:
                raise SystemExit(f"Refusing to overwrite a different file: {destination}")
            continue
        shutil.copy2(path, destination)
    source_stats = _tree_stats(source)
    source_paths = {path.relative_to(source) for path in _files(source)}
    target_paths = {path.relative_to(target) for path in _files(target)}
    unexpected = {
        path for path in target_paths - source_paths if not path.name.startswith("thumbnail.")
    }
    mismatched = [
        path for path in source_paths
        if not (target / path).is_file() or (target / path).stat().st_size != (source / path).stat().st_size
    ]
    sample_mismatches = [
        relative
        for relative, digest in (source_stats["sampleSha256"] or {}).items()
        if hashlib.sha256((target / relative).read_bytes()).hexdigest() != digest
    ]
    if unexpected or mismatched or sample_mismatches:
        raise SystemExit(f"Relocation verification failed: {source} -> {target}")
    target_stats = _tree_stats(target)
    return {
        "source": str(source),
        "target": str(target),
        "stats": target_stats,
        "sourceFilesVerified": len(source_paths),
        "derivedFilesAllowed": len(target_paths - source_paths) - len(unexpected),
    }


async def _open_store(check_schema: bool = True) -> PostgresStore:
    database_url = os.getenv("DATABASE_URL", "")
    store = PostgresStore(database_url, SERVER_RESULT_ROOT)
    await store.start(check_schema=check_schema)
    return store


async def _migrate() -> None:
    store = await _open_store(check_schema=False)
    try:
        print(json.dumps({"appliedThrough": await store.apply_migrations()}))
    finally:
        await store.close()


async def _import(args: argparse.Namespace) -> None:
    store = await _open_store()
    batch_store = PostgresBatchStore(store, BATCH_ROOT, SERVER_RESULT_ROOT)
    results_root = Path(args.results_root).resolve()
    batches_root = Path(args.batches_root).resolve()
    imported_pages = 0
    skipped_pages = 0
    imported_batches = 0
    skipped_batches = 0
    imported_summaries = 0
    skipped_summaries = 0
    try:
        if results_root != SERVER_RESULT_ROOT.resolve():
            print(
                f"Warning: assets are imported from {results_root}; API paths point at {SERVER_RESULT_ROOT}. "
                "Run relocate first when these are different.",
                flush=True,
            )
        for folder in sorted(results_root.iterdir()) if results_root.is_dir() else []:
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            try:
                await store.sync_result_folder(folder)
                imported_pages += 1
            except Exception as error:
                skipped_pages += 1
                await store.record_migration(
                    "result", str(folder), "skipped", {"error": str(error)}
                )

        for batch_dir in sorted(batches_root.iterdir()) if batches_root.is_dir() else []:
            if not batch_dir.is_dir() or batch_dir.name.startswith("."):
                continue
            try:
                await batch_store.import_existing_batch(batch_dir.name)
                imported_batches += 1
            except Exception as error:
                skipped_batches += 1
                await store.record_migration(
                    "batch", str(batch_dir), "skipped", {"error": str(error)}
                )
        summaries_root = results_root / ".summaries"
        for summary_path in sorted(summaries_root.glob("*.json")) if summaries_root.is_dir() else []:
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Summary has no mangaTitle")
                title = str(payload.get("mangaTitle") or "").strip()
                if not title:
                    raise ValueError("Summary has no mangaTitle")
                group_id = await store.resolve_group_id(title, create=True)
                if group_id is None:
                    raise ValueError(f"Could not resolve manga group: {title}")
                await store.save_summary_payload(group_id, payload)
                if await store.get_summary_payload(group_id) != payload:
                    raise RuntimeError("Database verification failed for summary")
                imported_summaries += 1
            except Exception as error:
                skipped_summaries += 1
                await store.record_migration(
                    "summary", str(summary_path), "skipped", {"error": str(error)}
                )
        print(json.dumps({
            "importedPages": imported_pages,
            "skippedPages": skipped_pages,
            "importedBatches": imported_batches,
            "skippedBatches": skipped_batches,
            "importedSummaries": imported_summaries,
            "skippedSummaries": skipped_summaries,
        }))
    finally:
        await store.close()


async def _reconcile() -> None:
    store = await _open_store()
    batch_store = PostgresBatchStore(store, BATCH_ROOT, SERVER_RESULT_ROOT)
    indexed = 0
    skipped = 0
    try:
        if SERVER_RESULT_ROOT.is_dir():
            for folder in sorted(SERVER_RESULT_ROOT.iterdir()):
                if not folder.is_dir() or folder.name.startswith("."):
                    continue
                try:
                    await store.sync_result_folder(folder)
                    indexed += 1
                except Exception as error:
                    skipped += 1
                    await store.record_migration(
                        "result", str(folder), "reconcile_failed", {"error": str(error)}
                    )
        await batch_store.reconcile(SERVER_RESULT_ROOT)
        print(json.dumps({"indexedPages": indexed, "skippedPages": skipped}))
    finally:
        await store.close()


async def _migrate_result_json(args: argparse.Namespace) -> None:
    """Move legacy result-folder JSON into PostgreSQL; run with writers stopped."""
    if not args.confirm_workers_stopped:
        raise SystemExit("Stop translation workers, then pass --confirm-workers-stopped")
    store = await _open_store()
    root = Path(args.results_root).resolve()
    report = {"folders": 0, "imported": 0, "deleted": 0, "failed": []}
    try:
        for folder in sorted(root.iterdir()) if root.is_dir() else []:
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            paths = sorted(folder.glob("*.json"))
            controls = [folder / ".continue", folder / ".stop"]
            if not paths and not any(path.exists() for path in controls):
                continue
            report["folders"] += 1
            try:
                raw = {path.name: path.read_bytes() for path in paths}
                documents = {
                    name: json.loads(content.decode("utf-8"))
                    for name, content in raw.items()
                }
                if final_file(folder) is not None:
                    await store.sync_result_folder(folder)
                else:
                    await store.save_documents(folder.name, documents)
                    for name, payload in documents.items():
                        if await store.get_document(folder.name, name) != payload:
                            raise RuntimeError(f"Database verification failed for {name}")
                    for path in paths:
                        if path.read_bytes() != raw[path.name]:
                            raise RuntimeError(f"Source changed during migration: {path.name}")
                        path.unlink()
                report["imported"] += len(documents)
                report["deleted"] += sum(not path.exists() for path in paths)
                for path in controls:
                    path.unlink(missing_ok=True)
            except Exception as error:
                report["failed"].append({"folder": folder.name, "error": str(error)})
                await store.record_migration(
                    "result-json", str(folder), "failed", {"error": str(error)}
                )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["failed"]:
            raise SystemExit(1)
    finally:
        await store.close()


async def _migrate_result_images(args: argparse.Namespace) -> None:
    if args.apply and not args.confirm_workers_stopped:
        raise SystemExit("Stop translation workers, then pass --confirm-workers-stopped")
    from PIL import Image

    root = Path(args.results_root).resolve()
    report = {
        "dryRun": not args.apply,
        "folders": 0,
        "converted": 0,
        "cleaned": 0,
        "wouldConvert": 0,
        "estimatedReclaimBytes": 0,
        "bytesBefore": 0,
        "bytesAfter": 0,
        "failed": [],
    }
    store = await _open_store() if args.apply else None
    db_updates: list[tuple[str, str | None, int, str]] = []
    try:
        for folder in sorted(root.iterdir()) if root.is_dir() else []:
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            final = final_file(folder)
            input_asset = find_asset(folder, "input")
            cleanup_names = {
                "input.png", "input.jpeg", "final.png", "final.jpeg",
                "colorized.png", "upscaled.png", "bboxes.png",
                "bboxes_unfiltered.png", "inpaint_input.png", "mask_raw.png",
                "mask_final.png",
            }
            has_cleanup_assets = any((folder / name).is_file() for name in cleanup_names)
            if final is None and input_asset is None and not has_cleanup_assets:
                continue
            report["folders"] += 1
            before = sum(path.stat().st_size for path in folder.iterdir() if path.is_file())
            if not args.apply:
                report["wouldConvert"] += 1
                report["estimatedReclaimBytes"] += sum(
                    path.stat().st_size for path in folder.iterdir()
                    if path.is_file() and (path.name in cleanup_names or path.suffix.lower() == ".json")
                )
            try:
                if args.apply:
                    has_local_documents = any(folder.glob("*.json"))
                    for source, target in ((final, folder / "final.jpg"), (input_asset, folder / "input.jpg")):
                        if source is None:
                            continue
                        if source != target:
                            with Image.open(source) as image:
                                size = image.size
                                save_jpeg(image, target)
                            with Image.open(target) as converted:
                                if converted.size != size:
                                    raise RuntimeError(f"Dimension verification failed for {target.name}")
                    # Preserve local documents before deleting them. JSON-free folders
                    # only need the post-cleanup sync, avoiding a redundant full scan.
                    if store is not None and has_local_documents and final is not None:
                        await store.sync_result_folder(folder)
                    for name in cleanup_names:
                        (folder / name).unlink(missing_ok=True)
                    for path in folder.glob("*.json"):
                        path.unlink(missing_ok=True)
                    if any(not (folder / f"{name}.webp").is_file() for name in ("batch", "preview", "reader")):
                        generate_image_variants(folder)
                    if store is not None and has_local_documents and final is not None:
                        await store.sync_result_folder(folder)
                    if store is not None:
                        final_after = final_file(folder)
                        input_after = find_asset(folder, "input")
                        if final_after is not None:
                            db_updates.append((
                                final_after.name,
                                input_after.name if input_after is not None else None,
                                final_after.stat().st_mtime_ns,
                                folder.name,
                            ))
                    report["converted"] += 1
                    report["cleaned"] += 1
                after = sum(path.stat().st_size for path in folder.iterdir() if path.is_file())
                report["bytesBefore"] += before
                report["bytesAfter"] += after
            except Exception as error:
                report["failed"].append({"folder": folder.name, "error": str(error)})
        if store is not None and db_updates:
            async with store.pool.acquire() as connection:
                await connection.executemany(
                    """
                    UPDATE pages
                    SET final_name=$1, input_name=$2, asset_version=$3, updated_at=now()
                    WHERE folder=$4
                    """,
                    db_updates,
                )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["failed"]:
            raise SystemExit(1)
    finally:
        if store is not None:
            await store.close()


def _verify(args: argparse.Namespace) -> None:
    results_root = Path(args.results_root).resolve()
    batches_root = Path(args.batches_root).resolve()
    report = {
        "results": _tree_stats(results_root),
        "batches": _tree_stats(batches_root),
        "malformedMetadata": [],
        "unknownResultFolders": [],
    }
    for folder in sorted(results_root.iterdir()) if results_root.is_dir() else []:
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        final = final_file(folder)
        metadata = folder / "meta.json"
        if final is None:
            report["unknownResultFolders"].append(folder.name)
            continue
        if metadata.is_file():
            try:
                value = json.loads(metadata.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("metadata is not an object")
            except Exception as error:
                report["malformedMetadata"].append({"folder": folder.name, "error": str(error)})
    report["ok"] = not report["malformedMetadata"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


def _number(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_pipeline_regions(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    regions = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        xywh = item.get("xywh")
        if not isinstance(xywh, list):
            points = item.get("pts")
            if isinstance(points, list) and points and all(isinstance(point, list) for point in points):
                xs = [_number(point[0]) for point in points if len(point) >= 2]
                ys = [_number(point[1]) for point in points if len(point) >= 2]
                xywh = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)] if xs and ys else []
            else:
                xywh = [item.get("x", 0), item.get("y", 0), item.get("width", 0), item.get("height", 0)]
        xywh = (xywh + [0, 0, 0, 0])[:4]
        region = dict(item)
        if "lines" not in region and isinstance(item.get("pts"), list):
            region["lines"] = [item["pts"]]
        region.update(
            {
                "id": item.get("id") or f"bubble_{index}",
                "x": _number(xywh[0]),
                "y": _number(xywh[1]),
                "width": _number(xywh[2]),
                "height": _number(xywh[3]),
                "original_text": item.get("original_text") or item.get("text_raw") or item.get("text") or "",
                "translation": item.get("translation") or "",
                "font_size": _number(item.get("font_size"), 24),
            }
        )
        regions.append(region)
    return regions


async def _recover_text_regions(args: argparse.Namespace) -> None:
    store = await _open_store()
    results_root = Path(args.results_root).resolve()
    recovered = 0
    try:
        rows = await store.pool.fetch(
            "SELECT folder FROM pages WHERE active AND jsonb_array_length(text_regions)=0"
        )
        updates = []
        for row in rows:
            folder = results_root / row["folder"]
            regions = []
            for source in (
                folder / "translations.json",
                folder / "text_regions_merged.json",
                folder / "ocr.json",
                folder / "detection.json",
            ):
                if not source.is_file():
                    continue
                try:
                    regions = _normalize_pipeline_regions(json.loads(source.read_text(encoding="utf-8")))
                except (OSError, UnicodeError, ValueError):
                    regions = []
                if regions:
                    break
            if regions:
                updates.append((_json_dump(regions), row["folder"]))
        async with store.pool.acquire() as connection:
            await connection.executemany(
                """
                UPDATE pages SET text_regions=$1::jsonb, has_regions=TRUE, updated_at=now()
                WHERE active AND folder=$2
                """,
                updates,
            )
        recovered = len(updates)
    finally:
        await store.close()
    print(json.dumps({"recoveredPages": recovered}))


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _relocate(args: argparse.Namespace) -> None:
    data_root = Path(args.data_root).resolve()
    result = _copy_tree_preserving_source(Path(args.results_root).resolve(), data_root / "results")
    batches = _copy_tree_preserving_source(Path(args.batches_root).resolve(), data_root / "batches")
    print(json.dumps({"results": result, "batches": batches}, ensure_ascii=False, indent=2))


def _backfill_variants(args: argparse.Namespace) -> None:
    results_root = Path(args.results_root).resolve()
    folders = [
        folder for folder in sorted(results_root.iterdir()) if folder.is_dir()
        and not folder.name.startswith(".") and final_file(folder) is not None
    ] if results_root.is_dir() else []
    def generate(folder: Path) -> bool:
        return bool(generate_image_variants(folder, force=args.force))
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
        outcomes = list(pool.map(generate, folders))
    generated = sum(outcomes)
    skipped = len(outcomes) - generated
    cover_folders: set[str] = set()
    metadata_by_folder: dict[str, tuple[str, str]] = {}
    try:
        metadata_by_folder = asyncio.run(_load_variant_metadata([folder.name for folder in folders]))
    except Exception as error:
        raise SystemExit(f"Could not load page metadata from PostgreSQL: {error}") from error
    groups: dict[str, tuple[str, str]] = {}
    for folder in folders:
        title, original = metadata_by_folder.get(folder.name, ("Ungrouped", folder.name))
        candidate = (_natural_sort_key(original), folder.name)
        if title not in groups or candidate < groups[title]:
            groups[title] = candidate
    cover_folders = {folder_name for _, folder_name in groups.values()}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
        cover_outcomes = list(pool.map(
            lambda folder: bool(generate_image_variants(folder, include_cover=True)),
            [folder for folder in folders if folder.name in cover_folders],
        ))
    pruned = 0
    if not args.keep_extra_covers:
        for folder in folders:
            cover = folder / "cover.webp"
            if folder.name not in cover_folders and cover.is_file():
                cover.unlink()
                pruned += 1
    if args.sync_database and generated:
        rows = [(asset_version(folder), folder.name) for folder, ok in zip(folders, outcomes) if ok]
        try:
            asyncio.run(_refresh_asset_versions(rows))
        except Exception as error:
            print(f"Warning: could not refresh database asset versions: {error}", flush=True)
    print(json.dumps({
        "resultsRoot": str(results_root),
        "generatedPages": generated,
        "skippedPages": skipped,
        "coverPages": sum(cover_outcomes),
        "prunedCoverPages": pruned,
    }))


async def _refresh_asset_versions(rows: list[tuple[int, str]]) -> None:
    store = await _open_store()
    try:
        async with store.pool.acquire() as connection:
            await connection.executemany(
                "UPDATE pages SET asset_version=$1, updated_at=now() WHERE folder=$2",
                rows,
            )
    finally:
        await store.close()


async def _load_variant_metadata(folder_names: list[str]) -> dict[str, tuple[str, str]]:
    store = await _open_store()
    try:
        rows = await store.pool.fetch(
            """
            SELECT p.folder, g.title AS manga_title, p.original_name
            FROM pages p
            JOIN manga_groups g ON g.id=p.manga_group_id
            WHERE active AND folder=ANY($1::text[])
            """,
            folder_names,
        )
        return {
            row["folder"]: (row["manga_title"], row["original_name"])
            for row in rows
        }
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate", help="Apply SQL migrations")

    relocate = subparsers.add_parser("relocate", help="Copy assets to the configured data root and verify them")
    relocate.add_argument("--data-root", default=str(MANGA_DATA_ROOT))
    relocate.add_argument("--results-root", default=str(WORKSPACE_ROOT / "results"))
    relocate.add_argument("--batches-root", default=str(WORKSPACE_ROOT / "batches"))

    verify = subparsers.add_parser("verify", help="Validate source metadata and report deterministic tree samples")
    verify.add_argument("--results-root", default=str(SERVER_RESULT_ROOT))
    verify.add_argument("--batches-root", default=str(BATCH_ROOT))

    importer = subparsers.add_parser("import", help="Import relocated files and manifests into PostgreSQL")
    importer.add_argument("--results-root", default=str(SERVER_RESULT_ROOT))
    importer.add_argument("--batches-root", default=str(BATCH_ROOT))

    subparsers.add_parser("reconcile", help="Reindex files and repair interrupted batch states")
    result_json = subparsers.add_parser(
        "migrate-result-json",
        help="Move result-folder JSON into PostgreSQL (stop translation workers first)",
    )
    result_json.add_argument("--results-root", default=str(SERVER_RESULT_ROOT))
    result_json.add_argument("--confirm-workers-stopped", action="store_true")
    result_images = subparsers.add_parser(
        "migrate-result-images",
        help="Convert result inputs/outputs to JPEG and remove successful-run checkpoints",
    )
    result_images.add_argument("--results-root", default=str(SERVER_RESULT_ROOT))
    result_images.add_argument("--apply", action="store_true")
    result_images.add_argument("--confirm-workers-stopped", action="store_true")
    recover = subparsers.add_parser("recover-text-regions", help="Recover retained pipeline regions into PostgreSQL")
    recover.add_argument("--results-root", default=str(SERVER_RESULT_ROOT))
    backfill = subparsers.add_parser("backfill-image-variants", help="Generate page derivatives and one cover WebP per manga")
    backfill.add_argument("--results-root", default=str(SERVER_RESULT_ROOT))
    backfill.add_argument("--force", action="store_true")
    backfill.add_argument("--workers", type=int, default=4)
    backfill.add_argument("--no-database", dest="sync_database", action="store_false", help="Do not refresh PostgreSQL asset revisions")
    backfill.add_argument("--keep-extra-covers", action="store_true", help="Keep legacy cover.webp files on non-cover pages")
    backfill.set_defaults(sync_database=True)
    args = parser.parse_args()
    if args.command == "migrate":
        asyncio.run(_migrate())
    elif args.command == "relocate":
        _relocate(args)
    elif args.command == "verify":
        _verify(args)
    elif args.command == "import":
        asyncio.run(_import(args))
    elif args.command == "reconcile":
        asyncio.run(_reconcile())
    elif args.command == "migrate-result-json":
        asyncio.run(_migrate_result_json(args))
    elif args.command == "migrate-result-images":
        asyncio.run(_migrate_result_images(args))
    elif args.command == "recover-text-regions":
        asyncio.run(_recover_text_regions(args))
    elif args.command == "backfill-image-variants":
        _backfill_variants(args)


if __name__ == "__main__":
    main()
