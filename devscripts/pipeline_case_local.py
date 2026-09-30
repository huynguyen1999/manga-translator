"""Local image and saved-case execution for devscripts/pipeline_case.py."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable


DEFAULT_DATA_DIR = Path("devscripts/data")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".avif"}
GENERATED_IMAGE_NAMES = {
    "input.png",
    "input.jpg",
    "img_rgb.png",
    "original_canvas.png",
    "colorized.png",
    "upscaled.png",
    "inpainted.png",
    "inpainted.jpg",
    "rendered.png",
    "final.jpg",
    "final.png",
    "mask_raw.png",
    "text_mask.png",
    "bubble_mask.png",
    "detector_rescue_mask.png",
    "bubble_residual_mask.png",
    "protected_bubble_edge.png",
    "mask_final.png",
    "inpaint_mask.png",
    "mask_sources_overlay.png",
    "free_text_layout_debug.png",
}
STAGE_ORDER = (
    "input",
    "colorization",
    "upscaling",
    "detection",
    "ocr",
    "bubble_detection",
    "textline_merge",
    "translation",
    "mask_generation",
    "layout",
    "inpainting",
    "rendering",
)
STAGE_ALIASES = {
    "full": "input",
    "reprocess_text": "detection",
    "bubbles": "bubble_detection",
    "merge": "textline_merge",
    "translation_typesetting": "translation",
    "mask": "mask_generation",
    "typesetting": "layout",
    "inpaint": "inpainting",
    "render": "rendering",
}
STAGE_INDEX = {name: idx for idx, name in enumerate(STAGE_ORDER)}
JSON_ARTIFACT_NAMES = (
    "detection.json",
    "ocr.json",
    "bubble_detections.json",
    "panel_detections.json",
    "text_regions_merged.json",
    "translations.json",
    "layout.json",
    "text_regions.json",
    "profiling.json",
)
GENERIC_PARENT_NAMES = {
    "",
    ".",
    "data",
    "images",
    "input",
    "inputs",
    "samples",
    "downloads",
    "desktop",
    "documents",
}


def normalize_stage(stage: str | None) -> str:
    if not stage:
        return "layout"
    cleaned = str(stage).strip().lower().replace("-", "_")
    cleaned = STAGE_ALIASES.get(cleaned, cleaned)
    if cleaned not in STAGE_INDEX:
        valid = ", ".join((*STAGE_ORDER, *sorted(STAGE_ALIASES)))
        raise ValueError(f"Unknown stage '{stage}'. Choose from: {valid}")
    return cleaned


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_saved_case_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    has_json = any(
        (path / name).is_file()
        for name in ("case.json", "meta.json", "text_regions.json", "regions.json", "translations.json", "ocr.json", "detection.json")
    )
    has_image_or_case = (path / "case.json").is_file() or any(
        (path / name).is_file()
        for name in ("input.png", "img_rgb.png", "original_canvas.png", "input.jpg", "rendered.png", "final.jpg")
    )
    return has_json and has_image_or_case


def _case_input_image(case_dir: Path) -> Path | None:
    for name in ("input.png", "img_rgb.png", "original_canvas.png", "input.jpg"):
        candidate = case_dir / name
        if candidate.is_file():
            return candidate
    for filename in ("case.json", "meta.json"):
        meta = _read_json(case_dir / filename)
        if isinstance(meta, dict):
            source = meta.get("sourcePath") or meta.get("source_path")
            if isinstance(source, str) and Path(source).is_file():
                return Path(source)
    return None


def discover_target_images(targets: list[str | Path], data_dir: str | Path = DEFAULT_DATA_DIR) -> list[Path]:
    resolved_images: list[Path] = []
    seen: set[Path] = set()
    for raw in targets:
        target = Path(raw).expanduser()
        if not target.exists():
            raise FileNotFoundError(f"Target path does not exist: {raw}")
        if target.is_file():
            if target.name in {"case.json", "meta.json"} and is_saved_case_dir(target.parent):
                img = _case_input_image(target.parent)
                if img is not None and img.resolve() not in seen:
                    seen.add(img.resolve())
                    resolved_images.append(img.resolve())
                continue
            if target.suffix.lower() not in IMAGE_EXTENSIONS:
                raise ValueError(f"Unsupported image file: {target}")
            if target.resolve() not in seen:
                seen.add(target.resolve())
                resolved_images.append(target.resolve())
            continue
        if is_saved_case_dir(target):
            img = _case_input_image(target)
            if img is not None and img.resolve() not in seen:
                seen.add(img.resolve())
                resolved_images.append(img.resolve())
            continue
        direct_images = sorted(
            child.resolve()
            for child in target.iterdir()
            if child.is_file()
            and not child.name.startswith(".")
            and child.suffix.lower() in IMAGE_EXTENSIONS
            and child.name not in GENERATED_IMAGE_NAMES
        )
        if direct_images:
            for img in direct_images:
                if img not in seen:
                    seen.add(img)
                    resolved_images.append(img)
            continue
        sub_cases = sorted(child for child in target.iterdir() if is_saved_case_dir(child))
        if sub_cases:
            for case_dir in sub_cases:
                img = _case_input_image(case_dir)
                if img is not None and img.resolve() not in seen:
                    seen.add(img.resolve())
                    resolved_images.append(img.resolve())
            continue
        raise ValueError(f"No supported images or saved cases found in directory: {target}")
    return resolved_images


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _iter_existing_baseline_dirs(data_root: Path) -> list[Path]:
    if not data_root.is_dir():
        return []
    found: list[Path] = []
    runs_dir = data_root / "runs"
    for child in sorted(data_root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child == runs_dir:
            continue
        if is_saved_case_dir(child):
            found.append(child)
        else:
            for sub in sorted(child.iterdir()):
                if sub.is_dir() and not sub.name.startswith(".") and is_saved_case_dir(sub):
                    found.append(sub)
    return found


def _matches_source(case_dir: Path, image_path: Path, source_sha256: str | None = None) -> bool:
    resolved_str = str(image_path.resolve())
    for filename in ("case.json", "meta.json"):
        payload = _read_json(case_dir / filename)
        if not isinstance(payload, dict):
            continue
        saved_source = payload.get("sourcePath") or payload.get("source_path")
        if isinstance(saved_source, str) and saved_source:
            try:
                if str(Path(saved_source).expanduser().resolve()) == resolved_str:
                    return True
            except OSError:
                if saved_source == resolved_str:
                    return True
        saved_sha = payload.get("sourceSha256") or payload.get("source_sha256")
        if source_sha256 and isinstance(saved_sha, str) and saved_sha == source_sha256:
            return True
    return False


def resolve_baseline_dir(image_path: str | Path, data_dir: str | Path = DEFAULT_DATA_DIR) -> tuple[Path, str]:
    """Return (baseline_dir, case_name) in data_dir for image_path."""
    img = Path(image_path).expanduser().resolve()
    data_root = Path(data_dir).expanduser().resolve()
    runs_root = data_root / "runs"

    if is_saved_case_dir(img.parent) and _is_inside(img.parent, data_root) and not _is_inside(img.parent, runs_root):
        rel = img.parent.relative_to(data_root).as_posix()
        return img.parent, rel

    parent_name = img.parent.name
    stem = img.stem
    candidates: list[tuple[Path, str]] = []
    if parent_name and parent_name.lower() not in GENERIC_PARENT_NAMES and not parent_name.startswith("tmp"):
        candidates.append((data_root / parent_name / stem, f"{parent_name}/{stem}"))
    candidates.append((data_root / stem, stem))
    if parent_name:
        candidates.append((data_root / f"{parent_name}_{stem}", f"{parent_name}_{stem}"))

    for cand_dir, cand_name in candidates:
        if is_saved_case_dir(cand_dir):
            return cand_dir, cand_name

    img_sha = _file_sha256(img)
    for existing_dir in _iter_existing_baseline_dirs(data_root):
        if _matches_source(existing_dir, img, img_sha):
            rel = existing_dir.relative_to(data_root).as_posix()
            return existing_dir, rel

    if (
        parent_name
        and parent_name.lower() not in GENERIC_PARENT_NAMES
        and not parent_name.startswith("tmp")
        and img.parent != data_root.parent
    ):
        return data_root / parent_name / stem, f"{parent_name}/{stem}"
    return data_root / stem, stem


def _latest_registry_path(data_root: Path) -> Path:
    return data_root / "runs" / "latest.json"


def get_latest_run_dir(case_name: str, baseline_dir: Path, data_dir: str | Path = DEFAULT_DATA_DIR) -> Path:
    data_root = Path(data_dir).expanduser().resolve()
    registry = _read_json(_latest_registry_path(data_root))
    if isinstance(registry, dict):
        entry = registry.get(case_name)
        if isinstance(entry, dict):
            run_dir = entry.get("outputDir")
            if isinstance(run_dir, str) and Path(run_dir).is_dir():
                return Path(run_dir).resolve()
        elif isinstance(entry, str) and Path(entry).is_dir():
            return Path(entry).resolve()
    return baseline_dir.resolve()


def _record_latest_run(case_name: str, output_dir: Path, stage: str, data_dir: str | Path = DEFAULT_DATA_DIR) -> None:
    data_root = Path(data_dir).expanduser().resolve()
    reg_path = _latest_registry_path(data_root)
    registry = _read_json(reg_path)
    if not isinstance(registry, dict):
        registry = {}
    registry[case_name] = {
        "outputDir": str(output_dir.resolve()),
        "caseJson": str((output_dir / "case.json").resolve()),
        "stage": stage,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(reg_path, registry)


def load_case_documents(case_dir: Path, fallback_dir: Path | None = None) -> dict[str, Any]:
    """Load stage JSON documents from case_dir (and fallback_dir if needed)."""
    docs: dict[str, Any] = {}
    dirs = [case_dir]
    if fallback_dir is not None and fallback_dir.resolve() != case_dir.resolve():
        dirs.append(fallback_dir)

    for folder in reversed(dirs):
        if not folder.is_dir():
            continue
        case_payload = _read_json(folder / "case.json")
        if isinstance(case_payload, dict) and isinstance(case_payload.get("artifacts"), dict):
            for key, value in case_payload["artifacts"].items():
                norm_key = key if key.endswith(".json") else f"{key}.json"
                docs[norm_key] = copy.deepcopy(value)
        for name in (*JSON_ARTIFACT_NAMES, "regions.json"):
            file_path = folder / name
            if file_path.is_file():
                loaded = _read_json(file_path)
                if loaded is not None:
                    docs[name] = loaded

    if "text_regions.json" not in docs and "regions.json" in docs:
        docs["text_regions.json"] = copy.deepcopy(docs["regions.json"])
    if "translations.json" not in docs and isinstance(docs.get("text_regions.json"), list):
        docs["translations.json"] = copy.deepcopy(docs["text_regions.json"])
    return docs


def resolve_stage_image(name: str, case_dir: Path, fallback_dir: Path | None = None) -> Path | None:
    alternatives = {
        "input.png": ("input.png", "img_rgb.png", "original_canvas.png", "input.jpg"),
        "img_rgb.png": ("img_rgb.png", "original_canvas.png", "input.png", "input.jpg"),
        "inpainted.png": ("inpainted.png", "inpainted.jpg"),
        "mask_final.png": ("mask_final.png", "inpaint_mask.png"),
        "inpaint_mask.png": ("inpaint_mask.png", "mask_final.png"),
        "final.jpg": ("final.jpg", "rendered.png", "final.png"),
    }.get(name, (name,))
    dirs = [case_dir]
    if fallback_dir is not None and fallback_dir.resolve() != case_dir.resolve():
        dirs.append(fallback_dir)
    for folder in dirs:
        for alt in alternatives:
            cand = folder / alt
            if cand.is_file():
                return cand
        for meta_name in ("case.json", "meta.json"):
            meta = _read_json(folder / meta_name)
            if isinstance(meta, dict) and isinstance(meta.get("files"), dict):
                for alt in alternatives:
                    mapped = meta["files"].get(alt)
                    if isinstance(mapped, str) and Path(mapped).is_file():
                        return Path(mapped)
    return None


def missing_artifacts_for_stage(stage: str, case_dir: Path, fallback_dir: Path | None = None) -> list[str]:
    norm_stage = normalize_stage(stage)
    if not is_saved_case_dir(case_dir) and (fallback_dir is None or not is_saved_case_dir(fallback_dir)):
        return ["baseline_case"]

    missing: list[str] = []
    if resolve_stage_image("input.png", case_dir, fallback_dir) is None:
        missing.append("input.png")

    docs = load_case_documents(case_dir, fallback_dir)
    if norm_stage in {"input", "colorization", "upscaling", "detection"}:
        return missing
    if norm_stage == "ocr":
        if not any(isinstance(docs.get(k), list) for k in ("detection.json", "ocr.json", "text_regions.json")):
            missing.append("detection.json")
        return missing
    if norm_stage in {"bubble_detection", "textline_merge"}:
        if not any(isinstance(docs.get(k), list) for k in ("ocr.json", "detection.json", "text_regions_merged.json", "text_regions.json")):
            missing.append("ocr.json")
        return missing
    if norm_stage == "translation":
        if not any(isinstance(docs.get(k), list) for k in ("text_regions_merged.json", "translations.json", "text_regions.json", "ocr.json")):
            missing.append("text_regions_merged.json")
        return missing
    if norm_stage in {"mask_generation", "layout"}:
        if not any(isinstance(docs.get(k), list) for k in ("translations.json", "text_regions.json", "text_regions_merged.json")):
            missing.append("translations.json")
        return missing
    if norm_stage == "inpainting":
        if resolve_stage_image("mask_final.png", case_dir, fallback_dir) is None and resolve_stage_image("mask_raw.png", case_dir, fallback_dir) is None:
            missing.append("mask_final.png")
        if not any(isinstance(docs.get(k), (list, dict)) for k in ("layout.json", "translations.json", "text_regions.json")):
            missing.append("text_regions.json")
        return missing
    if norm_stage == "rendering":
        if not any(isinstance(docs.get(k), (list, dict)) for k in ("layout.json", "text_regions.json", "translations.json")):
            missing.append("layout.json")
        return missing
    return missing


def check_local_cases(
    targets: list[str | Path],
    *,
    stage: str = "layout",
    data_dir: str | Path = DEFAULT_DATA_DIR,
    from_run: str | Path | None = None,
    from_baseline: bool = False,
) -> dict[str, Any]:
    norm_stage = normalize_stage(stage)
    data_root = Path(data_dir).expanduser().resolve()
    images = discover_target_images(targets, data_root)
    cases: list[dict[str, Any]] = []

    for img in images:
        baseline_dir, case_name = resolve_baseline_dir(img, data_root)
        baseline_exists = is_saved_case_dir(baseline_dir)
        if from_run is not None:
            from_path = Path(from_run).expanduser().resolve()
            if from_path.is_file():
                from_path = from_path.parent
            candidate_sub = from_path / case_name
            last_run_dir = candidate_sub if candidate_sub.is_dir() else from_path
        elif from_baseline or not baseline_exists:
            last_run_dir = baseline_dir
        else:
            last_run_dir = get_latest_run_dir(case_name, baseline_dir, data_root)

        missing = missing_artifacts_for_stage(norm_stage, last_run_dir, baseline_dir) if baseline_exists else ["baseline_case"]
        has_run_before = baseline_exists and not missing
        last_json = None
        if baseline_exists:
            for json_candidate in (
                last_run_dir / "case.json",
                last_run_dir / "text_regions.json",
                last_run_dir / "regions.json",
                baseline_dir / "case.json",
                baseline_dir / "text_regions.json",
                baseline_dir / "regions.json",
            ):
                if json_candidate.is_file():
                    last_json = str(json_candidate)
                    break

        docs = load_case_documents(last_run_dir, baseline_dir) if baseline_exists else {}
        cases.append({
            "inputPath": str(img),
            "caseName": case_name,
            "hasRunBefore": has_run_before,
            "baselineExists": baseline_exists,
            "baselineDir": str(baseline_dir),
            "lastRunDir": str(last_run_dir) if baseline_exists else None,
            "lastRunJson": last_json,
            "availableArtifacts": sorted(docs.keys()),
            "missingForStage": missing,
            "action": "rerun_stage" if (has_run_before and norm_stage != "input") else "run_full_pipeline",
        })

    all_run_before = bool(cases) and all(item["hasRunBefore"] for item in cases)
    return {
        "dataDir": str(data_root),
        "targetStage": norm_stage,
        "allRunBefore": all_run_before,
        "recommendedAction": "rerun_stage" if (all_run_before and norm_stage != "input") else "run_full_pipeline",
        "cases": cases,
    }


def _apply_prompt_ids_to_artifacts(artifacts: dict[str, Any], add_prompt_ids_fn: Callable[[str, Any], Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for name, payload in artifacts.items():
        stem = Path(name).stem
        cloned = copy.deepcopy(payload)
        add_prompt_ids_fn(stem, cloned)
        normalized[stem] = cloned
    return normalized


def build_local_config(
    *,
    translator_name: str = "sugoi",
    case_dir: Path | None = None,
    fallback_dir: Path | None = None,
    settings_overrides: dict[str, Any] | None = None,
):
    from manga_translator.config import Alignment, Config, Detector, Direction, Inpainter, Ocr, Renderer, Translator
    from server.batch_config import config_for

    config = None
    for folder in [d for d in (case_dir, fallback_dir) if d is not None and d.is_dir()]:
        cfg_data = _read_json(folder / "config.json")
        if isinstance(cfg_data, dict):
            try:
                config = Config.model_validate(cfg_data)
                break
            except Exception:
                pass
        for meta_file in ("case.json", "meta.json"):
            meta = _read_json(folder / meta_file)
            if isinstance(meta, dict) and isinstance(meta.get("settings"), dict):
                try:
                    merged_settings = dict(meta["settings"])
                    if settings_overrides:
                        merged_settings.update(settings_overrides)
                    config = config_for({"settings": merged_settings}, {"settings": merged_settings})
                    break
                except Exception:
                    pass
        if config is not None:
            break

    if config is None:
        config = Config()
        config.translator.translator = (
            Translator[translator_name] if translator_name in Translator.__members__ else Translator.sugoi
        )
        config.translator.target_lang = "ENG"
        config.translator.no_text_lang_skip = True
        config.detector.detector = Detector.default
        config.detector.detection_size = 2048
        config.detector.box_threshold = 0.5
        config.detector.unclip_ratio = 2.3
        config.ocr.ocr = Ocr.ocr48px_ctc
        config.inpainter.inpainter = Inpainter.default
        config.inpainter.inpainting_size = 2048
        config.render.renderer = Renderer.default
        config.render.direction = Direction.auto
        config.render.alignment = Alignment.auto
        config.render.rtl = True
        config.bubble_detection.enabled = True
        config.bubble_detection.model = "shadowb_manga109"
        config.bubble_detection.confidence = 0.25
        config.bubble_detection.mask_threshold = 0.5
        config.bubble_detection.padding = 9
        config.mask_dilation_offset = 20

    if translator_name and translator_name in Translator.__members__:
        config.translator.translator = Translator[translator_name]

    if settings_overrides:
        if "fontSize" in settings_overrides or "customFontSize" in settings_overrides:
            val = settings_overrides.get("fontSize", settings_overrides.get("customFontSize"))
            config.render.font_size = int(val) if val not in (None, "") else None
        if "fontSizeOffset" in settings_overrides:
            config.render.font_size_offset = int(settings_overrides["fontSizeOffset"] or 0)
        if "fontSizeMinimum" in settings_overrides:
            config.render.font_size_minimum = int(settings_overrides["fontSizeMinimum"] or 0)
        if "lineSpacing" in settings_overrides:
            val = settings_overrides["lineSpacing"]
            config.render.line_spacing = float(val) if val not in (None, "") else None
        if "noHyphenation" in settings_overrides:
            config.render.no_hyphenation = bool(settings_overrides["noHyphenation"])
        if "targetLanguage" in settings_overrides:
            config.translator.target_lang = str(settings_overrides["targetLanguage"])
        if "translator" in settings_overrides and settings_overrides["translator"] in Translator.__members__:
            config.translator.translator = Translator[settings_overrides["translator"]]
        if "detectionResolution" in settings_overrides:
            config.detector.detection_size = int(settings_overrides["detectionResolution"])
        if "customBoxThreshold" in settings_overrides:
            config.detector.box_threshold = float(settings_overrides["customBoxThreshold"])
        if "customUnclipRatio" in settings_overrides:
            config.detector.unclip_ratio = float(settings_overrides["customUnclipRatio"])
        if "maskDilationOffset" in settings_overrides:
            config.mask_dilation_offset = int(settings_overrides["maskDilationOffset"])
        if "bubbleDetection" in settings_overrides:
            config.bubble_detection.enabled = bool(settings_overrides["bubbleDetection"])

    return config


def inspect_local_case(
    target: str | Path,
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    add_prompt_ids_fn: Callable[[str, Any], Any],
) -> dict[str, Any]:
    path = Path(target).expanduser().resolve()
    data_root = Path(data_dir).expanduser().resolve()
    if path.is_file() and path.name == "case.json":
        case_dir = path.parent
        baseline_dir = case_dir
        case_name = case_dir.name
    elif is_saved_case_dir(path):
        case_dir = path
        baseline_dir = case_dir
        case_name = case_dir.relative_to(data_root).as_posix() if _is_inside(case_dir, data_root) else case_dir.name
    else:
        images = discover_target_images([path], data_root)
        if len(images) != 1:
            return {
                "cases": [
                    inspect_local_case(img, data_dir=data_root, add_prompt_ids_fn=add_prompt_ids_fn)
                    for img in images
                ]
            }
        baseline_dir, case_name = resolve_baseline_dir(images[0], data_root)
        if not is_saved_case_dir(baseline_dir):
            raise FileNotFoundError(
                f"Case '{case_name}' has not been run yet in {data_root}. Run `pipeline_case.py run {target}` first."
            )
        case_dir = get_latest_run_dir(case_name, baseline_dir, data_root)

    saved_case = _read_json(case_dir / "case.json")
    meta = _read_json(case_dir / "meta.json") or {}
    saved_baseline = (
        (saved_case.get("baselineDir") if isinstance(saved_case, dict) else None)
        or (meta.get("baselineDir") if isinstance(meta, dict) else None)
    )
    if isinstance(saved_baseline, str) and Path(saved_baseline).is_dir():
        baseline_dir = Path(saved_baseline).resolve()
    if not meta and (baseline_dir / "meta.json").is_file():
        meta = _read_json(baseline_dir / "meta.json") or {}
    docs = load_case_documents(case_dir, baseline_dir)
    artifacts = _apply_prompt_ids_to_artifacts(docs, add_prompt_ids_fn)
    render_file = resolve_stage_image("final.jpg", case_dir, baseline_dir)
    input_file = resolve_stage_image("input.png", case_dir, baseline_dir)

    result = dict(saved_case) if isinstance(saved_case, dict) else {}
    result.setdefault("caseName", case_name)
    result.setdefault("caseDir", str(case_dir))
    result.setdefault("baselineDir", str(baseline_dir))
    result.setdefault(
        "page",
        {
            "id": case_name,
            "folder": str(case_dir),
            "sourcePath": meta.get("sourcePath") or meta.get("source_path") or (str(input_file) if input_file else None),
            "renderFile": str(render_file) if render_file else None,
        },
    )
    if isinstance(meta.get("settings"), dict):
        result.setdefault("settings", meta["settings"])
    result["artifacts"] = artifacts
    if render_file:
        result["renderFile"] = str(render_file)
    return result


def _extract_region_map(case_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts = case_payload.get("artifacts") or {}
    regions = artifacts.get("text_regions") or artifacts.get("translations") or artifacts.get("regions") or []
    layout_doc = artifacts.get("layout") if isinstance(artifacts.get("layout"), dict) else {}
    layout_regions = layout_doc.get("regions") if isinstance(layout_doc.get("regions"), dict) else {}

    mapped: dict[str, dict[str, Any]] = {}
    if isinstance(regions, list):
        for idx, item in enumerate(regions):
            if not isinstance(item, dict):
                continue
            rid = str(item.get("id") or item.get("region_id") or f"text_region_{idx + 1}")
            raw_id = str(item.get("region_id") or item.get("id") or rid)
            frozen = layout_regions.get(raw_id) or layout_regions.get(rid) or {}
            trace_layout = next(
                (
                    entry
                    for entry in reversed(item.get("content_trace") or [])
                    if isinstance(entry, dict) and entry.get("stage") == "layout"
                ),
                {},
            )
            raw_lines = frozen.get("lines")
            if not raw_lines and isinstance(item.get("layout_segments"), list):
                raw_lines = [
                    line
                    for seg in item["layout_segments"]
                    if isinstance(seg, dict)
                    for line in (seg.get("lines") or [])
                ]
            if not raw_lines and isinstance(trace_layout.get("layout_lines"), list):
                raw_lines = trace_layout["layout_lines"]
            lines = [
                line.get("text", "") if isinstance(line, dict) else str(line)
                for line in (raw_lines or [])
            ]
            mapped[rid] = {
                "id": rid,
                "text": item.get("text"),
                "translation": item.get("translation"),
                "fontSize": frozen.get("font_size", item.get("font_size")),
                "lines": lines,
                "layoutBounds": item.get("layout_bounds"),
                "xyxy": item.get("xyxy"),
                "placementMode": frozen.get("placement_mode", item.get("placement_mode")),
                "solverStatus": frozen.get("solver_status") or trace_layout.get("solver_status"),
                "reviewRequired": bool(item.get("review_required", False)),
                "reviewReason": item.get("review_reason"),
                "warningCodes": (frozen.get("qa_metrics") or {}).get("warning_codes", []),
            }
    return mapped


def _resolve_case_dir_for_compare(target: str | Path, data_dir: str | Path = DEFAULT_DATA_DIR) -> Path:
    path = Path(target).expanduser().resolve()
    if path.is_file():
        return path.parent
    if is_saved_case_dir(path):
        return path
    sub_cases = [child for child in sorted(path.rglob("case.json")) if is_saved_case_dir(child.parent)]
    if len(sub_cases) == 1:
        return sub_cases[0].parent
    return path


def compare_cases(
    before: str | Path | dict[str, Any],
    after: str | Path | dict[str, Any],
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    add_prompt_ids_fn: Callable[[str, Any], Any],
) -> dict[str, Any]:
    if not isinstance(before, dict) and not isinstance(after, dict):
        before_path = _resolve_case_dir_for_compare(before, data_dir)
        after_path = _resolve_case_dir_for_compare(after, data_dir)
        if before_path.is_dir() and after_path.is_dir() and not is_saved_case_dir(before_path) and not is_saved_case_dir(after_path):
            before_sub = {p.name: p for p in before_path.rglob("*") if is_saved_case_dir(p)}
            after_sub = {p.name: p for p in after_path.rglob("*") if is_saved_case_dir(p)}
            common = sorted(before_sub.keys() & after_sub.keys())
            if common:
                per_case = [
                    compare_cases(before_sub[name], after_sub[name], data_dir=data_dir, add_prompt_ids_fn=add_prompt_ids_fn)
                    for name in common
                ]
                return {
                    "caseCount": len(per_case),
                    "changedCaseCount": sum(1 for item in per_case if item["changedRegionCount"] > 0 or item["renderedImageChanged"]),
                    "cases": per_case,
                }

    before_case = (
        before
        if isinstance(before, dict)
        else inspect_local_case(_resolve_case_dir_for_compare(before, data_dir), data_dir=data_dir, add_prompt_ids_fn=add_prompt_ids_fn)
    )
    after_case = (
        after
        if isinstance(after, dict)
        else inspect_local_case(_resolve_case_dir_for_compare(after, data_dir), data_dir=data_dir, add_prompt_ids_fn=add_prompt_ids_fn)
    )

    before_dir_path = Path(before_case["caseDir"]) if isinstance(before_case.get("caseDir"), str) else None
    after_dir_path = Path(after_case["caseDir"]) if isinstance(after_case.get("caseDir"), str) else None
    before_render = Path(before_case["renderFile"]) if isinstance(before_case.get("renderFile"), str) else None
    after_render = Path(after_case["renderFile"]) if isinstance(after_case.get("renderFile"), str) else None
    if before_dir_path and after_dir_path:
        for shared_name in ("rendered.png", "final.jpg", "final.png"):
            if (before_dir_path / shared_name).is_file() and (after_dir_path / shared_name).is_file():
                before_render = before_dir_path / shared_name
                after_render = after_dir_path / shared_name
                break
    before_sha = _file_sha256(before_render) if before_render else None
    after_sha = _file_sha256(after_render) if after_render else None

    before_regions = _extract_region_map(before_case)
    after_regions = _extract_region_map(after_case)
    all_ids = list(dict.fromkeys([*before_regions.keys(), *after_regions.keys()]))

    changed_regions: list[dict[str, Any]] = []
    compare_fields = (
        "translation",
        "fontSize",
        "lines",
        "layoutBounds",
        "placementMode",
        "solverStatus",
        "reviewRequired",
        "warningCodes",
    )
    for rid in all_ids:
        b_item = before_regions.get(rid)
        a_item = after_regions.get(rid)
        if b_item is None or a_item is None:
            changed_regions.append({
                "id": rid,
                "status": "added" if b_item is None else "removed",
                "before": b_item,
                "after": a_item,
            })
            continue
        diffs = {}
        for field in compare_fields:
            if b_item.get(field) != a_item.get(field):
                diffs[field] = {"before": b_item.get(field), "after": a_item.get(field)}
        if diffs:
            changed_regions.append({
                "id": rid,
                "status": "modified",
                "text": a_item.get("text") or b_item.get("text"),
                "changes": diffs,
            })

    b_profile = (before_case.get("layoutProfile") or {}).get("workload") or {}
    a_profile = (after_case.get("layoutProfile") or {}).get("workload") or {}
    workload_diff = {}
    for key in sorted(set(b_profile) | set(a_profile)):
        if b_profile.get(key) != a_profile.get(key):
            workload_diff[key] = {"before": b_profile.get(key), "after": a_profile.get(key)}

    return {
        "caseName": after_case.get("caseName") or before_case.get("caseName"),
        "beforeDir": before_case.get("caseDir"),
        "afterDir": after_case.get("caseDir"),
        "beforeRenderFile": str(before_render) if before_render else None,
        "afterRenderFile": str(after_render) if after_render else None,
        "beforeRenderSha256": before_sha,
        "afterRenderSha256": after_sha,
        "renderedImageChanged": (before_sha != after_sha) if (before_sha and after_sha) else bool(changed_regions),
        "regionCount": {"before": len(before_regions), "after": len(after_regions)},
        "changedRegionCount": len(changed_regions),
        "changedRegions": changed_regions,
        "workloadDiff": workload_diff,
    }


async def _async_execute_local_pipeline(
    image_path: Path,
    *,
    case_name: str,
    start_stage: str,
    output_dir: Path,
    baseline_dir: Path,
    reused_from_dir: Path | None,
    reused_from_json: str | None,
    translator_name: str = "sugoi",
    patch: dict[str, Any] | None = None,
    include_layout_profile: bool = True,
    render_output: Path | None = None,
    add_prompt_ids_fn: Callable[[str, Any], Any],
) -> dict[str, Any]:
    import cv2
    import numpy as np
    from PIL import Image

    from manga_translator.config import Colorizer, Translator
    from manga_translator.detection import prepare as prepare_detection
    from manga_translator.detection.bubble import prepare as prepare_bubble_detection, serialize_bubble_detections
    from manga_translator.detection.bubble_state import restore_bubble_detections
    from manga_translator.detection.panel import deserialize_panel_detections, serialize_panel_detections
    from manga_translator.geometry.bubbles import prepare_page_geometry
    from manga_translator.inpainting import prepare as prepare_inpainting
    from manga_translator.manga_translator import MangaTranslator
    from manga_translator.mask_builder import build_inpaint_masks, create_mask_sources_overlay
    from manga_translator.ocr import prepare as prepare_ocr
    from manga_translator.pipeline.run import deserialize_textblocks, deserialize_textlines, serialize_regions
    from manga_translator.pipeline.translation_remap import remap_translations
    from manga_translator.rendering import get_default_eng_font, render_page
    from manga_translator.rendering.bubble_layout import restore_bubble_assignments
    from manga_translator.rendering.layout import layout_page
    from manga_translator.rendering.layout.frozen import hydrate_layout, layout_input_fingerprints, serialize_frozen_layout
    from manga_translator.rendering.paragraph_coalescing import coalesce_free_text_regions
    from manga_translator.translators import prepare as prepare_translation
    from manga_translator.utils import Context, dump_image, is_preserved_region, load_image
    from manga_translator.utils.image_storage import save_jpeg
    from server.pipeline_rerun_metadata import restore_source_metadata

    norm_stage = normalize_stage(start_stage)
    start_idx = STAGE_INDEX[norm_stage]
    output_dir.mkdir(parents=True, exist_ok=True)

    settings_overrides = (patch or {}).get("settingsOverrides") if isinstance(patch, dict) else None
    artifact_overrides = (patch or {}).get("artifacts") if isinstance(patch, dict) else None
    source_dir = reused_from_dir if reused_from_dir is not None else baseline_dir
    saved_docs = load_case_documents(source_dir, baseline_dir) if start_idx > 0 else {}
    patched_keys: set[str] = set()
    if isinstance(artifact_overrides, dict):
        for k, v in artifact_overrides.items():
            norm_k = k if k.endswith(".json") else f"{k}.json"
            saved_docs[norm_k] = copy.deepcopy(v)
            patched_keys.add(norm_k)

    config = build_local_config(
        translator_name=translator_name,
        case_dir=source_dir if start_idx > 0 else None,
        fallback_dir=baseline_dir if start_idx > 0 else None,
        settings_overrides=settings_overrides,
    )

    input_file = resolve_stage_image("input.png", source_dir, baseline_dir) if start_idx > 0 else None
    if input_file is None:
        input_file = image_path
    pil_img = Image.open(input_file)
    pil_img.load()
    pil_img.name = str(image_path)

    ctx = Context()
    ctx.input = pil_img
    ctx.result = None
    ctx.result_documents = copy.deepcopy(saved_docs)
    ctx.upscaled = pil_img
    ctx.img_colorized = pil_img

    canvas_file = resolve_stage_image("img_rgb.png", source_dir, baseline_dir) if start_idx > STAGE_INDEX["upscaling"] else None
    if canvas_file is not None and canvas_file.is_file():
        bgr = cv2.imread(str(canvas_file), cv2.IMREAD_COLOR)
        if bgr is not None:
            ctx.img_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            ctx.img_alpha = None
    if getattr(ctx, "img_rgb", None) is None:
        ctx.img_rgb, ctx.img_alpha = load_image(pil_img)

    if start_idx == 0 or not (output_dir / "input.png").is_file():
        if start_idx == 0:
            pil_img.save(output_dir / "input.png")
            cv2.imwrite(str(output_dir / "img_rgb.png"), cv2.cvtColor(ctx.img_rgb, cv2.COLOR_RGB2BGR))

    needs_translator_instance = (
        start_idx <= STAGE_INDEX["translation"]
        or norm_stage == "inpainting"
        or (start_idx <= STAGE_INDEX["mask_generation"])
        or resolve_stage_image("inpainted.png", source_dir, baseline_dir) is None
    )
    translator = None
    if needs_translator_instance:
        translator = MangaTranslator({
            "use_gpu": True,
            "verbose": False,
            "models_ttl": 0,
            "result_root": str(output_dir.parent),
        })
        translator._result_path_override = str(output_dir)

    started_total = perf_counter()
    stage_timings_ms: dict[str, float] = {}

    # 1. Colorization & Upscaling
    if start_idx <= STAGE_INDEX["colorization"] and config.colorizer.colorizer != Colorizer.none:
        t0 = perf_counter()
        ctx.img_colorized = await translator._run_colorizer(config, ctx)
        stage_timings_ms["colorization"] = round((perf_counter() - t0) * 1000.0, 2)
    if start_idx <= STAGE_INDEX["upscaling"] and config.upscale.upscale_ratio:
        t0 = perf_counter()
        ctx.upscaled = await translator._run_upscaling(config, ctx)
        ctx.img_rgb, ctx.img_alpha = load_image(ctx.upscaled)
        stage_timings_ms["upscaling"] = round((perf_counter() - t0) * 1000.0, 2)
        if start_idx == 0:
            cv2.imwrite(str(output_dir / "img_rgb.png"), cv2.cvtColor(ctx.img_rgb, cv2.COLOR_RGB2BGR))

    # 2. Detection
    if start_idx <= STAGE_INDEX["detection"]:
        t0 = perf_counter()
        await prepare_detection(config.detector.detector)
        ctx.textlines, ctx.mask_raw, ctx.mask = await translator._run_detection(config, ctx)
        stage_timings_ms["detection"] = round((perf_counter() - t0) * 1000.0, 2)
        det_doc = serialize_regions(ctx.textlines or [])
        ctx.result_documents["detection.json"] = det_doc
        if ctx.mask_raw is not None:
            cv2.imwrite(str(output_dir / "mask_raw.png"), ctx.mask_raw)
    else:
        mask_raw_file = resolve_stage_image("mask_raw.png", source_dir, baseline_dir)
        if mask_raw_file is not None:
            ctx.mask_raw = cv2.imread(str(mask_raw_file), cv2.IMREAD_GRAYSCALE)
        det_source = saved_docs.get("ocr.json") if start_idx > STAGE_INDEX["ocr"] else saved_docs.get("detection.json")
        if isinstance(det_source, list):
            ctx.textlines = deserialize_textlines(det_source)

    # 3. OCR
    if start_idx <= STAGE_INDEX["ocr"]:
        t0 = perf_counter()
        if getattr(ctx, "textlines", None):
            await prepare_ocr(config.ocr.ocr, translator.device)
            ctx.textlines = await translator._run_ocr(config, ctx)
        else:
            ctx.textlines = []
        stage_timings_ms["ocr"] = round((perf_counter() - t0) * 1000.0, 2)
        ctx.result_documents["ocr.json"] = serialize_regions(ctx.textlines or [])

    # 4. Textline Merge & Bubble Detection
    if start_idx <= STAGE_INDEX["textline_merge"]:
        t0 = perf_counter()
        if getattr(ctx, "textlines", None):
            ctx.text_regions = await translator._run_textline_merge(config, ctx)
        else:
            ctx.text_regions = []
        if bool(getattr(config.bubble_detection, "enabled", False)):
            await prepare_bubble_detection(config.bubble_detection, translator.device)
        await translator._detect_speech_bubbles(config, ctx)
        ctx.text_regions = coalesce_free_text_regions(ctx.text_regions, ctx.img_rgb)
        ctx.page_geometry, ctx.bubble_mask = prepare_page_geometry(
            ctx.img_rgb,
            ctx.text_regions,
            padding=int(getattr(config.bubble_detection, "padding", 9)),
        )
        stage_timings_ms["bubble_and_merge"] = round((perf_counter() - t0) * 1000.0, 2)
        ctx.result_documents["bubble_detections.json"] = serialize_bubble_detections(getattr(ctx, "bubble_detections", None) or [])
        ctx.result_documents["panel_detections.json"] = serialize_panel_detections(
            getattr(ctx, "panel_detections", None) or [],
            ctx.img_rgb.shape[:2] if ctx.img_rgb is not None else None,
        )
        ctx.result_documents["text_regions_merged.json"] = serialize_regions(ctx.text_regions or [])
    else:
        bubble_doc = saved_docs.get("bubble_detections.json")
        if isinstance(bubble_doc, list) and ctx.img_rgb is not None:
            restore_bubble_detections(ctx, bubble_doc, ctx.img_rgb.shape)
        panel_doc = saved_docs.get("panel_detections.json")
        if isinstance(panel_doc, list) and ctx.img_rgb is not None:
            ctx.panel_detections = deserialize_panel_detections(panel_doc, ctx.img_rgb.shape)

        region_payload = None
        if start_idx <= STAGE_INDEX["translation"]:
            region_order = ("text_regions_merged.json", "text_regions.json", "translations.json", "ocr.json")
        else:
            prioritized = [k for k in ("text_regions.json", "translations.json") if k in patched_keys]
            region_order = (*prioritized, "translations.json", "text_regions.json", "text_regions_merged.json")
        for key in region_order:
            if isinstance(saved_docs.get(key), list):
                region_payload = saved_docs[key]
                break
        ctx.text_regions = deserialize_textblocks(region_payload or [])
        for tb, raw_item in zip(ctx.text_regions, region_payload or []):
            if isinstance(raw_item, dict) and not getattr(tb, "region_id", ""):
                tb.region_id = str(raw_item.get("region_id") or raw_item.get("id") or "")
        source_payload = saved_docs.get("text_regions_merged.json") or saved_docs.get("ocr.json")
        if isinstance(source_payload, list):
            source_blocks = deserialize_textblocks(source_payload)
            for tb, raw_item in zip(source_blocks, source_payload):
                if isinstance(raw_item, dict) and not getattr(tb, "region_id", ""):
                    tb.region_id = str(raw_item.get("region_id") or raw_item.get("id") or "")
            restore_source_metadata(ctx.text_regions, source_blocks)
        if ctx.text_regions and getattr(ctx, "bubble_detections", None):
            restore_bubble_assignments(ctx.text_regions, ctx.bubble_detections)
        ctx.page_geometry, ctx.bubble_mask = prepare_page_geometry(
            ctx.img_rgb,
            ctx.text_regions,
            padding=int(getattr(config.bubble_detection, "padding", 9)),
        )

    # 5. Translation
    if start_idx <= STAGE_INDEX["translation"]:
        t0 = perf_counter()
        previous_translated = saved_docs.get("translations.json") or saved_docs.get("text_regions.json")
        if (
            start_idx > 0
            and norm_stage != "translation"
            and config.translator.translator == Translator.none
            and isinstance(previous_translated, list)
        ):
            remap_translations(ctx.text_regions, deserialize_textblocks(previous_translated))
        elif ctx.text_regions:
            await prepare_translation(config.translator.translator_gen)
            ctx.text_regions = await translator._run_text_translation(config, ctx)
        stage_timings_ms["translation"] = round((perf_counter() - t0) * 1000.0, 2)
        ctx.result_documents["translations.json"] = serialize_regions(ctx.text_regions or [])

    # 6. Mask Generation
    existing_mask_file = resolve_stage_image("mask_final.png", source_dir, baseline_dir)
    need_mask_gen = (
        start_idx <= STAGE_INDEX[" textline_merge".strip()]
        or norm_stage == "mask_generation"
        or existing_mask_file is None
    )
    if need_mask_gen:
        t0 = perf_counter()
        if ctx.text_regions:
            bundle = await build_inpaint_masks(
                image=ctx.img_rgb,
                detector_textlines=getattr(ctx, "textlines", None),
                detector_mask=getattr(ctx, "mask_raw", None),
                text_regions=ctx.text_regions or [],
                bubble_detections=getattr(ctx, "bubble_detections", None),
                config=config,
                page_geometry=getattr(ctx, "page_geometry", None),
            )
            ctx.text_mask = bundle.text_mask
            ctx.bubble_mask = bundle.bubble_cleanup_mask
            ctx.detector_rescue_mask = bundle.detector_rescue_mask
            ctx.bubble_residual_mask = bundle.bubble_residual_mask
            ctx.protected_edge_mask = bundle.protected_edge_mask
            ctx.mask_profile = bundle.profile
            ctx.page_geometry = bundle.page_geometry
            ctx.mask = bundle.final_inpaint_mask
            ctx.inpaint_mask = bundle.final_inpaint_mask
            ctx.result_documents["profiling.json"] = bundle.profile
            for mask_name, mask_arr in (
                ("text_mask.png", ctx.text_mask),
                ("bubble_mask.png", ctx.bubble_mask),
                ("detector_rescue_mask.png", ctx.detector_rescue_mask),
                ("bubble_residual_mask.png", ctx.bubble_residual_mask),
                ("protected_bubble_edge.png", ctx.protected_edge_mask),
                ("mask_final.png", ctx.mask),
                ("inpaint_mask.png", ctx.inpaint_mask),
            ):
                if mask_arr is not None:
                    cv2.imwrite(str(output_dir / mask_name), mask_arr)
            try:
                overlay = create_mask_sources_overlay(ctx.img_rgb, bundle)
                cv2.imwrite(str(output_dir / "mask_sources_overlay.png"), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
            except Exception:
                pass
        else:
            empty_mask = np.zeros(ctx.img_rgb.shape[:2], dtype=np.uint8)
            ctx.mask = empty_mask
            ctx.inpaint_mask = empty_mask
            cv2.imwrite(str(output_dir / "mask_final.png"), empty_mask)
            cv2.imwrite(str(output_dir / "inpaint_mask.png"), empty_mask)
        stage_timings_ms["mask_generation"] = round((perf_counter() - t0) * 1000.0, 2)
    else:
        ctx.mask = cv2.imread(str(existing_mask_file), cv2.IMREAD_GRAYSCALE)
        inpaint_mask_file = resolve_stage_image("inpaint_mask.png", source_dir, baseline_dir) or existing_mask_file
        ctx.inpaint_mask = cv2.imread(str(inpaint_mask_file), cv2.IMREAD_GRAYSCALE)
        prot_file = resolve_stage_image("protected_bubble_edge.png", source_dir, baseline_dir)
        if prot_file is not None:
            ctx.protected_edge_mask = cv2.imread(str(prot_file), cv2.IMREAD_GRAYSCALE)
        bmask_file = resolve_stage_image("bubble_mask.png", source_dir, baseline_dir)
        if bmask_file is not None:
            ctx.bubble_mask = cv2.imread(str(bmask_file), cv2.IMREAD_GRAYSCALE)

    # 7. Layout
    active_font = getattr(config.render, "font_path", None) or get_default_eng_font()
    layout_profile = None
    if norm_stage == "rendering" and isinstance(saved_docs.get("layout.json"), dict) and "layout.json" not in patched_keys:
        bubble_doc = ctx.result_documents.get("bubble_detections.json") or serialize_bubble_detections(getattr(ctx, "bubble_detections", None) or [])
        inputs = layout_input_fingerprints(
            ctx.text_regions or [],
            config,
            active_font,
            bubble_doc,
            getattr(ctx.img_rgb, "shape", None),
            ctx.inpaint_mask if ctx.inpaint_mask is not None else ctx.mask,
        )
        try:
            hydrate_layout(ctx, saved_docs["layout.json"], inputs["fingerprint"])
            ctx._bubble_detection_done = True
            ctx._bubble_layout_ready = True
        except ValueError:
            pass

    if not getattr(ctx, "_bubble_layout_ready", False):
        t0 = perf_counter()
        transform_text_case = getattr(config.render, "transform_text_case", None)
        if transform_text_case:
            for region in ctx.text_regions or []:
                if is_preserved_region(region):
                    region.translation = region.text
                elif getattr(region, "translation", None) and isinstance(region.translation, str):
                    region.translation = transform_text_case(region.translation)
        ctx._collect_layout_profile = bool(include_layout_profile)
        ctx._layout_queue_wait_ms = 0.0
        if ctx.text_regions and ctx.img_rgb is not None:
            layout_page(ctx, config, active_font)
        stage_timings_ms["layout"] = round((perf_counter() - t0) * 1000.0, 2)
        if include_layout_profile:
            layout_profile = getattr(ctx, "_solver_profile", None)
        bubble_doc = ctx.result_documents.get("bubble_detections.json") or serialize_bubble_detections(getattr(ctx, "bubble_detections", None) or [])
        ctx.result_documents["layout.json"] = serialize_frozen_layout(ctx, config, active_font, bubble_doc)

    # 8. Inpainting
    existing_inpainted = resolve_stage_image("inpainted.png", source_dir, baseline_dir)
    need_inpaint = (
        need_mask_gen
        or norm_stage == "inpainting"
        or existing_inpainted is None
    )
    if need_inpaint:
        t0 = perf_counter()
        if ctx.mask is not None and np.any(ctx.mask > 0):
            await prepare_inpainting(config.inpainter.inpainter, translator.device)
            ctx.img_inpainted = await translator._run_inpainting(config, ctx)
        else:
            ctx.img_inpainted = ctx.img_rgb.copy()
        stage_timings_ms["inpainting"] = round((perf_counter() - t0) * 1000.0, 2)
        cv2.imwrite(str(output_dir / "inpainted.png"), cv2.cvtColor(ctx.img_inpainted, cv2.COLOR_RGB2BGR))
        save_jpeg(ctx.img_inpainted, str(output_dir / "inpainted.jpg"))
    else:
        stored = cv2.imread(str(existing_inpainted), cv2.IMREAD_COLOR)
        ctx.img_inpainted = cv2.cvtColor(stored, cv2.COLOR_BGR2RGB) if stored is not None else ctx.img_rgb.copy()

    # 9. Rendering
    t0 = perf_counter()
    ctx.img_rendered = await render_page(ctx, config, active_font)
    ctx.result = dump_image(ctx.input, ctx.img_rendered, ctx.img_alpha)
    stage_timings_ms["rendering"] = round((perf_counter() - t0) * 1000.0, 2)

    final_regions_doc = serialize_regions(ctx.text_regions or [])
    ctx.result_documents["text_regions.json"] = final_regions_doc
    if "translations.json" not in ctx.result_documents:
        ctx.result_documents["translations.json"] = copy.deepcopy(final_regions_doc)

    rendered_png_path = output_dir / "rendered.png"
    final_jpg_path = output_dir / "final.jpg"
    cv2.imwrite(str(rendered_png_path), cv2.cvtColor(ctx.img_rendered, cv2.COLOR_RGB2BGR))
    save_jpeg(ctx.img_rendered, str(final_jpg_path))
    if render_output is not None:
        render_output.parent.mkdir(parents=True, exist_ok=True)
        if render_output.suffix.lower() == ".png":
            cv2.imwrite(str(render_output), cv2.cvtColor(ctx.img_rendered, cv2.COLOR_RGB2BGR))
        else:
            save_jpeg(ctx.img_rendered, str(render_output))

    for doc_name, doc_payload in ctx.result_documents.items():
        if doc_payload is not None:
            _write_json(output_dir / doc_name, doc_payload)
    _write_json(output_dir / "regions.json", final_regions_doc)
    _write_json(output_dir / "config.json", config.model_dump(mode="json"))

    total_ms = round((perf_counter() - started_total) * 1000.0, 2)
    files_map: dict[str, str] = {}
    for fname in GENERATED_IMAGE_NAMES:
        resolved = resolve_stage_image(fname, output_dir, baseline_dir)
        if resolved is not None and resolved.is_file():
            files_map[fname] = str(resolved.resolve())

    source_sha = _file_sha256(image_path)
    meta_payload = {
        "caseName": case_name,
        "sample_name": Path(case_name).name,
        "sourcePath": str(image_path.resolve()),
        "source_path": str(image_path.resolve()),
        "sourceSha256": source_sha,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "startStage": norm_stage,
        "baselineDir": str(baseline_dir.resolve()),
        "reusedFromDir": str(reused_from_dir.resolve()) if reused_from_dir else None,
        "reusedFromJson": reused_from_json,
        "resolution": {"width": int(ctx.img_rgb.shape[1]), "height": int(ctx.img_rgb.shape[0])},
        "regionsCount": len(final_regions_doc),
        "bubblesCount": len(ctx.result_documents.get("bubble_detections.json") or []),
        "durationMs": total_ms,
        "stageTimingsMs": stage_timings_ms,
        "files": files_map,
    }
    _write_json(output_dir / "meta.json", meta_payload)

    artifacts_with_ids = _apply_prompt_ids_to_artifacts(ctx.result_documents, add_prompt_ids_fn)
    case_payload: dict[str, Any] = {
        "caseName": case_name,
        "mode": "full" if start_idx == 0 else norm_stage,
        "startStage": norm_stage,
        "sourcePath": str(image_path.resolve()),
        "sourceSha256": source_sha,
        "caseDir": str(output_dir.resolve()),
        "baselineDir": str(baseline_dir.resolve()),
        "reusedFromDir": str(reused_from_dir.resolve()) if reused_from_dir else None,
        "reusedFromJson": reused_from_json,
        "renderFile": str((render_output or final_jpg_path).resolve()),
        "page": {
            "id": case_name,
            "folder": str(output_dir.resolve()),
            "sourcePath": str(image_path.resolve()),
            "renderFile": str((render_output or final_jpg_path).resolve()),
        },
        "config": config.model_dump(mode="json"),
        "artifacts": artifacts_with_ids,
        "files": files_map,
        "timingMs": {"total": total_ms, "stages": stage_timings_ms},
    }
    if layout_profile is not None:
        case_payload["layoutProfile"] = layout_profile

    _write_json(output_dir / "case.json", case_payload)
    return case_payload


def execute_local_pipeline(
    image_path: Path,
    *,
    case_name: str,
    start_stage: str,
    output_dir: Path,
    baseline_dir: Path,
    reused_from_dir: Path | None,
    reused_from_json: str | None,
    translator_name: str = "sugoi",
    patch: dict[str, Any] | None = None,
    include_layout_profile: bool = True,
    render_output: Path | None = None,
    add_prompt_ids_fn: Callable[[str, Any], Any],
) -> dict[str, Any]:
    return asyncio.run(
        _async_execute_local_pipeline(
            image_path,
            case_name=case_name,
            start_stage=start_stage,
            output_dir=output_dir,
            baseline_dir=baseline_dir,
            reused_from_dir=reused_from_dir,
            reused_from_json=reused_from_json,
            translator_name=translator_name,
            patch=patch,
            include_layout_profile=include_layout_profile,
            render_output=render_output,
            add_prompt_ids_fn=add_prompt_ids_fn,
        )
    )


def _resolve_rerun_output_dir(
    *,
    case_name: str,
    stage: str,
    baseline_dir: Path,
    data_root: Path,
    requested_output_dir: str | Path | None,
    single_target: bool,
    run_batch_id: str,
) -> Path:
    if requested_output_dir is not None:
        out_base = Path(requested_output_dir).expanduser().resolve()
        if out_base == baseline_dir.resolve() or _is_inside(out_base, baseline_dir):
            raise ValueError(
                f"Refusing to overwrite baseline directory '{baseline_dir}'. "
                "Specify a separate --output-dir for stage reruns or omit --output-dir."
            )
        if out_base == data_root.resolve():
            target_dir = data_root / "runs" / run_batch_id / case_name
        elif single_target and out_base.name == Path(case_name).name:
            target_dir = out_base
        else:
            target_dir = out_base / case_name
    else:
        target_dir = data_root / "runs" / run_batch_id / case_name

    if target_dir.resolve() == baseline_dir.resolve() or _is_inside(target_dir, baseline_dir):
        raise ValueError(f"Rerun output directory '{target_dir}' must be separate from baseline '{baseline_dir}'")
    return target_dir


def run_local_cases(
    targets: list[str | Path],
    *,
    stage: str = "layout",
    data_dir: str | Path = DEFAULT_DATA_DIR,
    output_dir: str | Path | None = None,
    from_run: str | Path | None = None,
    from_baseline: bool = False,
    force_full: bool = False,
    overwrite_baseline: bool = False,
    translator: str = "sugoi",
    patch: dict[str, Any] | None = None,
    include_layout_profile: bool = True,
    render_output: str | Path | None = None,
    add_prompt_ids_fn: Callable[[str, Any], Any],
) -> dict[str, Any]:
    norm_stage = normalize_stage(stage)
    data_root = Path(data_dir).expanduser().resolve()
    status = check_local_cases(
        targets,
        stage=norm_stage,
        data_dir=data_root,
        from_run=from_run,
        from_baseline=from_baseline,
    )
    cases_info = status["cases"]
    single_target = len(cases_info) == 1
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_batch_id = f"{timestamp}_{norm_stage}"

    results: list[dict[str, Any]] = []
    for item in cases_info:
        img_path = Path(item["inputPath"])
        case_name = item["caseName"]
        baseline_dir = Path(item["baselineDir"])
        baseline_exists = bool(item["baselineExists"])
        has_run_before = bool(item["hasRunBefore"])
        custom_render = Path(render_output).expanduser().resolve() if (render_output and single_target) else None

        if not has_run_before or force_full or (norm_stage == "input" and not baseline_exists):
            effective_stage = "input"
            if not baseline_exists or overwrite_baseline:
                target_out_dir = baseline_dir
            else:
                target_out_dir = _resolve_rerun_output_dir(
                    case_name=case_name,
                    stage=effective_stage,
                    baseline_dir=baseline_dir,
                    data_root=data_root,
                    requested_output_dir=output_dir,
                    single_target=single_target,
                    run_batch_id=run_batch_id,
                )
            reused_dir = None
            reused_json = None
            action_taken = "ran_full_pipeline"
        else:
            effective_stage = norm_stage
            reused_dir = Path(item["lastRunDir"]) if item.get("lastRunDir") else baseline_dir
            reused_json = item.get("lastRunJson")
            target_out_dir = _resolve_rerun_output_dir(
                case_name=case_name,
                stage=effective_stage,
                baseline_dir=baseline_dir,
                data_root=data_root,
                requested_output_dir=output_dir,
                single_target=single_target,
                run_batch_id=run_batch_id,
            )
            action_taken = "reran_stage"

        case_payload = execute_local_pipeline(
            img_path,
            case_name=case_name,
            start_stage=effective_stage,
            output_dir=target_out_dir,
            baseline_dir=baseline_dir,
            reused_from_dir=reused_dir,
            reused_from_json=reused_json,
            translator_name=translator,
            patch=patch,
            include_layout_profile=include_layout_profile,
            render_output=custom_render,
            add_prompt_ids_fn=add_prompt_ids_fn,
        )

        comparison = None
        comparison_file = None
        if action_taken == "reran_stage" or (baseline_exists and target_out_dir.resolve() != baseline_dir.resolve()):
            _record_latest_run(case_name, target_out_dir, effective_stage, data_root)
            comparison = compare_cases(
                baseline_dir,
                case_payload,
                data_dir=data_root,
                add_prompt_ids_fn=add_prompt_ids_fn,
            )
            comparison_path = target_out_dir / "comparison.json"
            _write_json(comparison_path, comparison)
            comparison_file = str(comparison_path.resolve())
            case_payload["comparison"] = comparison
            case_payload["comparisonFile"] = comparison_file
            _write_json(target_out_dir / "case.json", case_payload)

        results.append({
            "caseName": case_name,
            "inputPath": str(img_path),
            "actionTaken": action_taken,
            "requestedStage": norm_stage,
            "executedFromStage": effective_stage,
            "baselineDir": str(baseline_dir),
            "reusedFromDir": str(reused_dir) if reused_dir else None,
            "reusedFromJson": reused_json,
            "outputDir": str(target_out_dir),
            "caseJson": str((target_out_dir / "case.json").resolve()),
            "renderFile": case_payload["renderFile"],
            "comparisonFile": comparison_file,
            "comparison": comparison,
            "timingMs": case_payload.get("timingMs"),
            "artifacts": case_payload.get("artifacts") if single_target else None,
            "layoutProfile": case_payload.get("layoutProfile") if single_target else None,
        })

    if single_target:
        single = dict(results[0])
        single["dataDir"] = str(data_root)
        return single

    return {
        "dataDir": str(data_root),
        "requestedStage": norm_stage,
        "caseCount": len(results),
        "cases": results,
    }

