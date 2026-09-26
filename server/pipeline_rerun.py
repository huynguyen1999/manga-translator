"""Compatibility facade for pipeline rerun execution and artifact helpers."""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict

from manga_translator.config import Config
from manga_translator.utils.image_storage import find_asset
from server.pipeline_rerun_artifacts import (
    _RERUN_ARTIFACTS,
    _copy_file_atomic,
    _prepare_versioned_artifact,
    commit_rerun_artifacts,
)
from server.pipeline_rerun_context import load_rerun_context
from server.pipeline_rerun_execution import execute_rerun_plan
from server.pipeline_rerun_plan import (
    PipelineRerunMode,
    PipelineRerunPlan,
    _frozen_layout_document,
    resolve_rerun_plan,
    validate_rerun_prerequisites,
)


async def run_temporary_pipeline_case(
    *,
    source_dir: Path,
    documents: Dict[str, Any],
    replacements: Dict[str, Any],
    mode: str,
    settings: Dict[str, Any],
    page: Dict[str, Any],
    instance: Any,
) -> Dict[str, Any]:
    """Run a review-only rerun from temporary files without saving page or batch state."""
    if not hasattr(instance, "_run_translation") or not hasattr(instance, "translator"):
        raise RuntimeError("Temporary pipeline cases require an in-process executor")

    plan = resolve_rerun_plan(mode)
    with tempfile.TemporaryDirectory(prefix="pipeline-case-") as temp_name:
        temp_root = Path(temp_name)
        case_dir = temp_root / "source"
        case_dir.mkdir()
        copied_files = set()
        for path in source_dir.glob("*.json"):
            await asyncio.to_thread(shutil.copy2, path, case_dir / path.name)
            copied_files.add(path.name)
        for stem in (
            "input", "original_canvas", "upscaled", "inpainted", "final",
            "bubble_mask", "inpaint_mask", "mask_final", "mask_raw",
        ):
            path = find_asset(source_dir, stem)
            if path is not None and path.name not in copied_files:
                await asyncio.to_thread(shutil.copy2, path, case_dir / path.name)
                copied_files.add(path.name)
        for name, payload in documents.items():
            if Path(name).name == name and name.endswith(".json"):
                (case_dir / name).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        for name, payload in replacements.items():
            (case_dir / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        valid, reason = validate_rerun_prerequisites(
            case_dir,
            plan.mode,
            record={"hasTextRegions": any(
                (case_dir / name).is_file()
                for name in ("text_regions.json", "translations.json")
            )},
        )
        if not valid:
            raise RuntimeError(reason or "Temporary case is not eligible for rerun")

        from server.batch_config import config_for

        title = page.get("mangaTitle") or "Ungrouped"
        config = config_for(
            {"settings": settings, "title": title, "mangaGroupId": page.get("groupId")},
            {
                "settings": settings,
                "name": page.get("originalName") or page.get("name"),
                "mangaTitle": title,
                "mangaGroupId": page.get("groupId"),
                "pageOrder": page.get("pageOrder"),
            },
        )
        if plan.mode == PipelineRerunMode.TYPESETTING:
            config.bubble_detection.enabled = False
            config.translator.translator = "none"
            config.translator.translation_quality = "fast"
            config.upscale.upscale_ratio = None
            config.upscale.revert_upscaling = False

        ctx, state = await load_rerun_context(case_dir, plan, config)
        staging_dir = temp_root / "output"
        staging_dir.mkdir()

        async def execute():
            return await execute_rerun_plan(
                translator=instance.translator,
                ctx=ctx,
                config=config,
                plan=plan,
                state=state,
                staging_dir=staging_dir,
            )

        await instance._run_translation(execute)
        image_path = staging_dir / "final.jpg"
        if not image_path.is_file():
            raise RuntimeError("Temporary pipeline case produced no final image")
        output_artifacts = {}
        for path in staging_dir.glob("*.json"):
            try:
                output_artifacts[path.name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
        return {
            "mode": plan.mode.value,
            "sourcePageId": page.get("id"),
            "image": image_path.read_bytes(),
            "artifacts": output_artifacts,
        }
