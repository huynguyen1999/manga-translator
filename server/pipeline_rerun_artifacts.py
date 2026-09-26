"""Version and atomically commit artifacts from a completed rerun."""

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from manga_translator.pipeline.translation_remap import TranslationRemapResult
from server.image_variants import final_file
from server.pipeline_rerun_plan import PipelineRerunMode, PipelineRerunPlan


def _copy_file_atomic(source: Path, target: Path) -> None:
    """Replace one result file only after its complete staged copy is ready."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        if source.stat().st_size == 0:
            raise ValueError(f"Staged artifact is empty: {source.name}")
        shutil.copy2(source, temporary)
        if temporary.stat().st_size != source.stat().st_size:
            raise OSError(f"Staged artifact copy was incomplete: {source.name}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


_RERUN_ARTIFACTS = {
    "final.jpg": ("rendering", "final_image"),
    "final.png": ("rendering", "final_image"),
    "inpainted.jpg": ("inpainting", "image"),
    "inpainted.png": ("inpainting", "image"),
    "mask_raw.png": ("detection", "mask"),
    "text_mask.png": ("mask_generation", "text_mask"),
    "bubble_mask.png": ("mask_generation", "bubble_mask"),
    "mask_final.png": ("mask_generation", "inpaint_mask"),
    "inpaint_mask.png": ("mask_generation", "inpaint_mask"),
}


def _prepare_versioned_artifact(
    source: Path, result_root: Path, result_dir: Path, stage: str, artifact_type: str
) -> tuple[dict[str, Any], Path]:
    destination = result_dir / "pipeline_artifacts" / stage / f"{artifact_type}-{uuid.uuid4().hex}{source.suffix.lower()}"
    _copy_file_atomic(source, destination)
    try:
        with Image.open(destination) as image:
            width, height = image.size
            image.verify()
        digest = hashlib.sha256()
        with destination.open("rb") as artifact_file:
            for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                digest.update(chunk)
        checksum = digest.hexdigest()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    relative_path = destination.resolve().relative_to(result_root.resolve())
    mime_type = "image/jpeg" if destination.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return ({
        "stage": stage,
        "artifact_type": artifact_type,
        "relative_path": relative_path.as_posix(),
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "size_bytes": destination.stat().st_size,
        "checksum": checksum,
    }, destination)


async def commit_rerun_artifacts(
    result_dir: Path,
    staging_dir: Path,
    plan: PipelineRerunPlan,
    remap_result: Optional[TranslationRemapResult] = None,
    database: Any = None,
    job_id: str = "",
) -> None:
    """Atomically commit successfully staged rerun artifacts to the live result directory."""
    final_target = final_file(result_dir) or (result_dir / "final.jpg")

    # Load existing manifest to track rerun provenance & revisions
    manifest_path = result_dir / "pipeline_manifest.json"
    manifest: Dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    current_revision = int(manifest.get("revision", 1))
    new_revision = current_revision + 1

    executed_stages: List[str] = []
    reused_stages: List[str] = []

    if plan.mode == PipelineRerunMode.FULL:
        executed_stages = ["input", "detection", "ocr", "textline_merge", "bubble_detection", "mask_generation", "inpainting", "translation", "rendering"]
    elif plan.mode == PipelineRerunMode.TYPESETTING:
        executed_stages = ["rendering"]
        reused_stages = ["input", "detection", "ocr", "textline_merge", "bubble_detection", "mask_generation", "inpainting", "translation"]
    elif plan.mode == PipelineRerunMode.TRANSLATION_TYPESETTING:
        executed_stages = ["translation", "rendering"]
        reused_stages = ["input", "detection", "ocr", "textline_merge", "bubble_detection", "mask_generation", "inpainting"]
    elif plan.mode == PipelineRerunMode.REPROCESS_TEXT:
        executed_stages = ["detection", "ocr", "textline_merge", "mask_generation", "inpainting", "translation_remap", "rendering"]
        reused_stages = ["input", "bubble_detection", "translation"]

    last_rerun_info: Dict[str, Any] = {
        "jobId": job_id,
        "mode": plan.mode.value,
        "completedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "parentRevision": current_revision,
        "revision": new_revision,
        "executedStages": executed_stages,
        "reusedStages": reused_stages,
    }
    if remap_result is not None:
        last_rerun_info["translationRemap"] = remap_result.summary()

    manifest["revision"] = new_revision
    manifest["lastRerun"] = last_rerun_info
    (staging_dir / "pipeline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Collect documents for database save
    db_documents: Dict[str, Any] = {}
    for json_file in staging_dir.glob("*.json"):
        if json_file.name.startswith("."):
            continue
        try:
            db_documents[json_file.name] = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    versioned_artifacts: list[dict[str, Any]] = []
    versioned_files: list[Path] = []
    page_id = (
        await database.get_page_id(result_dir.name)
        if database is not None and hasattr(database, "get_page_id")
        else None
    )
    if page_id:
        selected: dict[str, Path] = {}
        for staged_file in sorted(staging_dir.iterdir()):
            spec = _RERUN_ARTIFACTS.get(staged_file.name)
            if spec is None or staged_file.is_dir():
                continue
            artifact_type = spec[1]
            current = selected.get(artifact_type)
            if current is None or (current.suffix.lower() == ".png" and staged_file.suffix.lower() in {".jpg", ".jpeg"}):
                selected[artifact_type] = staged_file
        for staged_file in selected.values():
            stage_id, artifact_type = _RERUN_ARTIFACTS[staged_file.name]
            artifact, artifact_path = await asyncio.to_thread(
                _prepare_versioned_artifact,
                staged_file,
                result_dir.parent,
                result_dir,
                stage_id,
                artifact_type,
            )
            versioned_artifacts.append(artifact)
            versioned_files.append(artifact_path)

        try:
            committed = await database.commit_pipeline_outputs(
                result_dir.name, db_documents, versioned_artifacts
            )
        except Exception:
            for path in versioned_files:
                path.unlink(missing_ok=True)
            raise
        if not committed:
            for path in versioned_files:
                path.unlink(missing_ok=True)
            versioned_artifacts.clear()
            versioned_files.clear()
            await database.save_documents(result_dir.name, db_documents)
    elif database is not None and db_documents:
        await database.save_documents(result_dir.name, db_documents)

    # Atomically replace files from staging dir to live folder
    for staged_file in staging_dir.iterdir():
        if staged_file.name.startswith(".") or staged_file.is_dir():
            continue
        dest_name = staged_file.name
        if dest_name == "final.jpg" and final_target.suffix.lower() in {".png"}:
            dest_name = "final.png"
        target_path = result_dir / dest_name
        await asyncio.to_thread(_copy_file_atomic, staged_file, target_path)

    # Invalidate web view cached variants
    for variant in ("batch.webp", "cover.webp", "preview.webp", "reader.webp"):
        (result_dir / variant).unlink(missing_ok=True)
