"""Replay captured layout cases offline and report layout, queue, and output data."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _folder_from_run(run: dict) -> str:
    return urlsplit(run.get("url", "")).path.rstrip("/").split("/")[-1]


def _line_signature(region: dict) -> tuple:
    return tuple(
        (line.get("text", ""), line.get("x"), line.get("y"), line.get("width"), line.get("height"))
        for segment in region.get("segments", [])
        for line in segment.get("lines", [])
    )


def _compare_snapshots(actual: dict | None, baseline: dict | None) -> dict:
    """Summarize font, wrapping, and placement changes keyed by region identity."""
    if not isinstance(actual, dict) or not isinstance(baseline, dict):
        return {"status": "unavailable", "changed": [], "added": [], "removed": []}
    current = {str(r.get("region_id", i)): r for i, r in enumerate(actual.get("regions", []))}
    expected = {str(r.get("region_id", i)): r for i, r in enumerate(baseline.get("regions", []))}
    changed, added, removed = [], sorted(current.keys() - expected.keys()), sorted(expected.keys() - current.keys())
    for region_id in sorted(current.keys() & expected.keys()):
        now, before = current[region_id], expected[region_id]
        changes = {}
        for name in ("font_size", "layout_bounds"):
            if now.get(name) != before.get(name):
                changes[name] = {"baseline": before.get(name), "current": now.get(name)}
        now_lines, old_lines = _line_signature(now), _line_signature(before)
        if tuple(line[0] for line in now_lines) != tuple(line[0] for line in old_lines):
            changes["wrapping"] = {
                "baseline": [line[0] for line in old_lines],
                "current": [line[0] for line in now_lines],
            }
        if now_lines != old_lines:
            changes["line_positions"] = {"baseline": old_lines, "current": now_lines}
        if changes:
            changed.append({"region_id": region_id, "changes": changes})
    return {"status": "compared", "changed": changed, "added": added, "removed": removed}


def _compare_fingerprints(actual: dict, baseline: dict) -> dict:
    return {
        key: {"baseline": baseline.get(key), "current": actual.get(key)}
        for key in sorted(actual.keys() | baseline.keys())
        if actual.get(key) != baseline.get(key)
    }


def _summary(runs: list[dict]) -> dict:
    groups = {}
    for run in runs:
        if "error" in run:
            continue
        key = f"{run['mode']}:{run['phase']}"
        groups.setdefault(key, []).append(run)
    modes = {}
    for key, rows in groups.items():
        modes[key] = {
            "runs": len(rows),
            "median_caller_wall_ms": round(statistics.median(r["caller_wall_ms"] for r in rows), 3),
            "median_layout_execution_ms": round(statistics.median(
                r["layout_execution_ms"] for r in rows if r.get("layout_execution_ms") is not None
            ), 3) if any(r.get("layout_execution_ms") is not None for r in rows) else None,
            "median_queue_wait_ms": round(statistics.median(r.get("queue_wait_ms", 0.0) for r in rows), 3),
        }
    by_page = {}
    for run in runs:
        if run.get("phase") != "measured" or "error" in run:
            continue
        row = by_page.setdefault(run["folder"], {"page_id": run["page_id"], "modes": {}})
        row["modes"].setdefault(run["mode"], []).append(run)
    for row in by_page.values():
        for mode, rows in row["modes"].items():
            comparisons = [r.get("comparison", r.get("comparison_to_baseline", {})) for r in rows]
            row["modes"][mode] = {
                "runs": len(rows),
                **{f"median_{key}": round(statistics.median(r[key] for r in rows), 3)
                   for key in ("caller_wall_ms", "layout_execution_ms", "queue_wait_ms")},
                "mask_matches_baseline": all(r.get("mask_matches_baseline", False) for r in rows),
                **{f"median_{key}_regions": round(statistics.median(len(c.get(name, [])) for c in comparisons), 1)
                   for key, name in (("changed", "changed"), ("added", "added"), ("removed", "removed"))},
            }
    return {"by_mode_phase": modes, "by_page": by_page}


def _snapshot(ctx, config, font_path, bubble_doc) -> dict:
    regions = []
    for region in ctx.text_regions or []:
        segments = []
        for segment in getattr(region, "layout_segments", []) or []:
            lines = [
                {key: line.get(key) for key in ("text", "x", "y", "width", "height")}
                for line in segment.get("lines", []) or []
            ]
            bounds = segment.get("bounds")
            if bounds is None:
                x, y = int(segment.get("x", 0)), int(segment.get("y", 0))
                bounds = [x, y, x + int(segment.get("width", 0)), y + int(segment.get("height", 0))]
            segments.append({"bounds": list(bounds), "font_size": segment.get("font_size"), "lines": lines})
        regions.append({
            "region_id": str(getattr(region, "region_id", "")),
            "font_size": int(getattr(region, "font_size", 0) or 0),
            "layout_bounds": list(getattr(region, "layout_bounds", []) or []),
            "segments": segments,
            "solver_path": getattr(region, "_solver_path", None),
            "solver_status": getattr(region, "_solver_status", None),
        })
    from manga_translator.rendering.layout.frozen import layout_input_fingerprints

    fingerprints = layout_input_fingerprints(
        ctx.text_regions, config, font_path, bubble_doc,
        getattr(ctx.img_rgb, "shape", None), getattr(ctx, "inpaint_mask", None),
    )
    return {"input_fingerprints": fingerprints, "regions": regions}


def _historical_result(folder: str, historical_dir: Path) -> Path | None:
    suffix = folder.split("-", 1)[-1]
    matches = sorted(historical_dir.glob(f"*-{suffix}")) if historical_dir.is_dir() else []
    return matches[0] if matches else None


def _materialize(case: dict, source_dir: Path, historical_dir: Path, scratch: Path) -> dict:
    from PIL import Image

    folder = str(case["pipeline"]["folder"])
    filename = str(case["pipeline"]["source"]["filename"])
    source = source_dir / filename
    if not source.is_file():
        raise FileNotFoundError(f"source image missing: {source}")
    result_dir = scratch / folder
    result_dir.mkdir(parents=True)
    shutil.copyfile(source, result_dir / f"input{source.suffix.lower()}")
    for name, payload in (case.get("artifacts") or {}).items():
        if isinstance(payload, (dict, list)):
            (result_dir / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    old = _historical_result(folder, historical_dir)
    mask_provenance = "reconstructed"
    historical_mask = None
    if old is not None and (old / "mask_final.png").is_file():
        with Image.open(source) as current_img, Image.open(old / "mask_final.png") as old_mask:
            if current_img.size == old_mask.size:
                historical_mask = old / "mask_final.png"
                shutil.copyfile(historical_mask, result_dir / "mask_final.png")
                mask_provenance = "historical_reused_unverified"
    return {
        "folder": folder,
        "page_id": case["page"]["id"],
        "source": str(source),
        "source_filename": filename,
        "result_dir": result_dir,
        "historical_dir": old,
        "mask_provenance": mask_provenance,
    }


async def _load_context(record: dict):
    import cv2
    import numpy as np
    from manga_translator.config import Config
    from manga_translator.detection.bubble import serialize_bubble_detections
    from manga_translator.mask_builder import build_inpaint_masks
    from manga_translator.pipeline.run import deserialize_textblocks
    from manga_translator.rendering import get_default_eng_font
    from server.pipeline_rerun_context import load_rerun_context
    from server.pipeline_rerun_plan import resolve_rerun_plan

    case = record["case"]
    config = Config(**case["pipeline"]["config"])
    plan = resolve_rerun_plan("typesetting")
    ctx, _ = await load_rerun_context(record["result_dir"], plan, config)
    active_font = getattr(config.render, "font_path", None) or get_default_eng_font()
    if ctx.inpaint_mask is None or ctx.inpaint_mask.shape != ctx.img_rgb.shape[:2]:
        if ctx.inpaint_mask is not None:
            ctx.inpaint_mask = None
            ctx.mask = None
        bundle = await build_inpaint_masks(
            image=ctx.img_rgb,
            detector_textlines=None,
            detector_mask=None,
            text_regions=ctx.text_regions or [],
            bubble_detections=ctx.bubble_detections or [],
            config=config,
        )
        ctx.mask_bundle = bundle
        ctx.inpaint_mask = bundle.final_inpaint_mask
        ctx.mask = bundle.final_inpaint_mask
        ctx.text_mask = bundle.text_mask
        ctx.bubble_mask = bundle.bubble_cleanup_mask
        ctx.protected_edge_mask = bundle.protected_edge_mask
        ctx.bubble_residual_mask = bundle.bubble_residual_mask
        ctx.page_geometry = bundle.page_geometry
    mask = np.asarray(ctx.inpaint_mask, dtype=np.uint8)
    if mask.ndim != 2 or mask.shape != ctx.img_rgb.shape[:2]:
        raise ValueError(f"bad inpaint mask for {record['folder']}: {mask.shape}")
    ctx.img_inpainted = cv2.inpaint(ctx.img_rgb, mask, 3, cv2.INPAINT_TELEA)
    ctx._collect_layout_profile = True
    ctx._layout_queue_wait_ms = 0.0
    bubble_doc = serialize_bubble_detections(ctx.bubble_detections or [])
    return ctx, config, active_font, bubble_doc


def _run_layout(ctx, config, font_path, options=None):
    from manga_translator.rendering.layout.engine import layout_page

    started = perf_counter()
    result = layout_page(ctx, config, font_path, options=options)
    wall_ms = (perf_counter() - started) * 1000.0
    profile = getattr(ctx, "_solver_profile", {}) or {}
    snapshot = _snapshot(ctx, config, font_path, getattr(ctx, "_benchmark_bubbles", []))
    return {
        "caller_wall_ms": wall_ms,
        "layout_execution_ms": profile.get("layout_total_ms"),
        "queue_wait_ms": profile.get("queue_wait_ms", 0.0),
        "profile": profile,
        "snapshot": snapshot,
        "diagnostics": {
            "errors": list(result.diagnostics.errors),
            "warnings": list(result.diagnostics.warnings),
            "metrics": result.diagnostics.metrics,
        },
    }


def _render(ctx, config, font_path, path: Path, historical_dir: Path | None):
    from manga_translator.rendering import render_page

    path.parent.mkdir(parents=True, exist_ok=True)
    image = asyncio.run(render_page(ctx, config, font_path))
    import cv2

    cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    result = {"path": str(path), "rgb_sha256": hashlib.sha256(image.tobytes()).hexdigest()}
    if historical_dir is not None:
        old_final = historical_dir / "final.jpg"
        if old_final.is_file():
            import numpy as np
            from PIL import Image

            with Image.open(old_final) as old:
                old_rgb = np.asarray(old.convert("RGB"))
            if old_rgb.shape == image.shape:
                result["historical_final_rgb_mae"] = float(np.abs(old_rgb.astype(np.int16) - image.astype(np.int16)).mean())
                result["historical_final_exact_pixels"] = int(np.count_nonzero(np.any(old_rgb != image, axis=2)))
    return result


def _case_run(record: dict, phase: str, index: int, mode: str, baseline: dict, *, render_path=None, options=None):
    started = perf_counter()
    try:
        ctx, config, font_path, bubble_doc = asyncio.run(_load_context(record))
        ctx._benchmark_bubbles = bubble_doc
        prep_ms = (perf_counter() - started) * 1000.0
        result = _run_layout(ctx, config, font_path, options)
        result.update({
            "folder": record["folder"], "page_id": record["page_id"], "phase": phase,
            "run": index, "mode": mode, "preparation_ms": prep_ms,
            "mask_provenance": record["mask_provenance"],
            "mask_sha256": result["snapshot"]["input_fingerprints"].get("mask"),
            "mask_matches_baseline": result["snapshot"]["input_fingerprints"].get("mask")
                == (baseline.get("layout_snapshot", {}).get("input_fingerprints", {}).get("mask")),
            "comparison": _compare_snapshots(result["snapshot"], baseline.get("layout_snapshot")),
            "input_fingerprint_changes": _compare_fingerprints(
                result["snapshot"]["input_fingerprints"],
                baseline.get("layout_snapshot", {}).get("input_fingerprints", {}),
            ),
        })
        if render_path is not None:
            try:
                result["render"] = _render(ctx, config, font_path, render_path, record["historical_dir"])
            except Exception as error:
                result["render_error"] = f"{type(error).__name__}: {error}"
        return result
    except Exception as error:
        return {
            "folder": record["folder"], "page_id": record["page_id"], "phase": phase,
            "run": index, "mode": mode, "error": f"{type(error).__name__}: {error}",
            "preparation_ms": (perf_counter() - started) * 1000.0,
            "mask_provenance": record["mask_provenance"],
        }


async def _background_batch(items: list[tuple[dict, dict, dict, int, dict, str]]) -> list[dict]:
    from manga_translator.pipeline.cpu import (
        CPU_PRIORITY_BACKGROUND, run_cpu_stage, shutdown_cpu_stage_executor,
    )
    from manga_translator.rendering.layout.engine import layout_page
    async def timed_stage(ctx, config, font_path):
        started = perf_counter()
        try:
            value = await run_cpu_stage(layout_page, ctx, config, font_path, priority=CPU_PRIORITY_BACKGROUND)
            return value, (perf_counter() - started) * 1000.0
        except Exception as error:
            return error, (perf_counter() - started) * 1000.0

    try:
        results = await asyncio.gather(*(
            timed_stage(ctx, config, ctx.get("_benchmark_font"))
            for _, ctx, config, _, _, _ in items
        ), return_exceptions=True)
    finally:
        await shutdown_cpu_stage_executor()
    output = []
    for (record, ctx, config, index, baseline, phase), stage_call in zip(items, results):
        if isinstance(stage_call, BaseException):
            stage_result, caller_wall_ms = stage_call, None
        else:
            stage_result, caller_wall_ms = stage_call
        if isinstance(stage_result, BaseException):
            output.append({"folder": record["folder"], "page_id": record["page_id"], "phase": phase,
                           "run": index, "mode": "background_cpu_stage", "caller_wall_ms": caller_wall_ms,
                           "error": f"{type(stage_result).__name__}: {stage_result}"})
            continue
        profile = getattr(ctx, "_solver_profile", {}) or {}
        font_path = ctx.get("_benchmark_font")
        bubble_doc = ctx.get("_benchmark_bubbles", [])
        snapshot = _snapshot(ctx, config, font_path, bubble_doc)
        run = {
            "folder": record["folder"], "page_id": record["page_id"], "phase": phase,
            "run": index, "mode": "background_cpu_stage",
            "caller_wall_ms": caller_wall_ms,
            "layout_execution_ms": profile.get("layout_total_ms"),
            "queue_wait_ms": profile.get("queue_wait_ms", getattr(ctx, "_layout_queue_wait_ms", 0.0)),
            "profile": profile, "snapshot": snapshot,
            "diagnostics": {"errors": list(stage_result.diagnostics.errors),
                            "warnings": list(stage_result.diagnostics.warnings),
                            "metrics": stage_result.diagnostics.metrics},
            "mask_provenance": record["mask_provenance"],
            "mask_sha256": snapshot["input_fingerprints"].get("mask"),
            "mask_matches_baseline": snapshot["input_fingerprints"].get("mask")
                == baseline.get("layout_snapshot", {}).get("input_fingerprints", {}).get("mask"),
            "comparison": _compare_snapshots(snapshot, baseline.get("layout_snapshot")),
            "input_fingerprint_changes": _compare_fingerprints(
                snapshot["input_fingerprints"], baseline.get("layout_snapshot", {}).get("input_fingerprints", {})
            ),
        }
        output.append(run)
    return output


def _prepare_case(record: dict):
    started = perf_counter()
    ctx, config, font_path, bubble_doc = asyncio.run(_load_context(record))
    ctx._benchmark_bubbles = bubble_doc
    ctx._benchmark_font = font_path
    return ctx, config, font_path, bubble_doc, (perf_counter() - started) * 1000.0


def benchmark(baseline_path: Path, case_paths: list[Path], source_dir: Path, historical_dir: Path, output_dir: Path,
              *, warmups=1, repeats=3, limit=None, render=True, raster_parity=False, modes=None) -> dict:
    import cv2

    cv2.setNumThreads(1)
    baseline_doc = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_by_folder = {_folder_from_run(run): run for run in baseline_doc["runs"]}
    cases = {}
    for path in case_paths:
        case = json.loads(path.read_text(encoding="utf-8"))
        cases[str(case["pipeline"]["folder"])] = case
    folders = [folder for folder in baseline_by_folder if folder in cases]
    missing = sorted(set(baseline_by_folder) - set(cases))
    if missing:
        raise ValueError(f"missing case artifacts for baseline pages: {missing}")
    if limit:
        folders = folders[:limit]
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix="layout-page-replay-") as temporary:
        scratch = Path(temporary)
        os.environ["YOLO_CONFIG_DIR"] = str(scratch / "ultralytics-config")
        for folder in folders:
            record = _materialize(cases[folder], source_dir, historical_dir, scratch)
            record["case"] = cases[folder]
            record["baseline"] = baseline_by_folder[folder]
            records.append(record)

        output = {
            "benchmark": "offline-layout-page",
            "baseline": str(baseline_path),
            "warmups": warmups,
            "repeats": repeats,
            "background_workers": 2, "opencv_threads": cv2.getNumThreads(),
            "mask_caveat": "Historical masks are reused only when dimensions match; their pixels may be stale. Missing masks are rebuilt from saved text and bubble geometry without detector masks. Compare mask_matches_baseline before treating a replay as exact.",
            "search_diagnostic_caveat": "progressive_search=False disables progressive search budgeting, but solver caches and deduplication remain enabled; this is not a pure raster-cache parity check.",
            "pages": [{key: record[key] for key in ("folder", "page_id", "source", "source_filename", "mask_provenance")} for record in records],
            "runs": [],
        }

        for mode in modes or ("isolated_layout_page", "background_cpu_stage"):
            for phase, count in (("warmup", warmups), ("measured", repeats)):
                for index in range(1, count + 1):
                    if mode == "isolated_layout_page":
                        for record in records:
                            run = _case_run(
                                record, phase, index, mode, record["baseline"],
                                render_path=(output_dir / mode / record["folder"] / f"run-{index}.png")
                                    if render and phase == "measured" and index == count else None,
                            )
                            output["runs"].append(run)
                    else:
                        from manga_translator.pipeline.cpu import configure_cpu_stage_workers
                        configure_cpu_stage_workers(2)
                        prepared, setup_errors = [], []
                        for record in records:
                            try:
                                ctx, config, font_path, bubble_doc, prep_ms = _prepare_case(record)
                                prepared.append((record, ctx, config, index, record["baseline"], phase))
                                ctx._benchmark_font = font_path
                                ctx._benchmark_bubbles = bubble_doc
                                ctx._benchmark_prep_ms = prep_ms
                            except Exception as error:
                                setup_errors.append({"folder": record["folder"], "page_id": record["page_id"],
                                                     "phase": phase, "run": index, "mode": mode,
                                                     "error": f"{type(error).__name__}: {error}"})
                        round_runs = asyncio.run(_background_batch(prepared)) if prepared else []
                        for run in round_runs:
                            item = next((entry for entry in prepared if entry[0]["folder"] == run["folder"]), None)
                            if item is not None:
                                record, ctx, config = item[:3]
                                run["preparation_ms"] = ctx._benchmark_prep_ms
                                if render and phase == "measured" and index == count and "error" not in run:
                                    try:
                                        run["render"] = _render(ctx, config, ctx._benchmark_font,
                                            output_dir / mode / record["folder"] / f"run-{index}.png", record["historical_dir"])
                                    except Exception as error:
                                        run["render_error"] = f"{type(error).__name__}: {error}"
                        output["runs"].extend(setup_errors)
                        output["runs"].extend(round_runs)

        if raster_parity:
            for record in records:
                run = _case_run(record, "diagnostic", 1, "search_budget_disabled", record["baseline"],
                                options={"progressive_search": False})
                run["comparison_to_baseline"] = run.pop("comparison", None)
                output["runs"].append(run)
        output["summary"] = _summary(output["runs"])
    from manga_translator.pipeline.cpu import shutdown_shared_cpu_stage_executor

    shutdown_shared_cpu_stage_executor()
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path("/tmp/layout-seven-baseline.json"))
    parser.add_argument("--cases-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--case-pattern", default="layout-case-*.json")
    parser.add_argument("--source-dir", type=Path, default=Path("/Users/ice-h/Downloads/slow"))
    parser.add_argument("--historical-dir", type=Path, default=Path("/tmp/layout-preview-current/results"))
    parser.add_argument("--output", type=Path, default=Path("/tmp/layout-seven-offline-benchmark.json"))
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--limit", type=int, help="Replay only the first N baseline pages (useful for a smoke run).")
    parser.add_argument("--warmup-only", action="store_true", help="Run warmups only; implies --repeats 0.")
    parser.add_argument("--skip-render", action="store_true", help="Skip final measured-run render outputs.")
    parser.add_argument("--mode", choices=("isolated_layout_page", "background_cpu_stage"), action="append")
    parser.add_argument("--search-diagnostic", "--raster-parity", dest="search_diagnostic", action="store_true",
                        help="Add a progressive_search=False comparison; caches and deduplication remain enabled.")
    args = parser.parse_args(argv)
    result = benchmark(
        args.baseline, sorted(args.cases_dir.glob(args.case_pattern)), args.source_dir, args.historical_dir, args.output.parent, warmups=args.warmups,
        repeats=0 if args.warmup_only else args.repeats, limit=args.limit,
        render=not args.skip_render, raster_parity=args.search_diagnostic, modes=args.mode,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered + "\n", encoding="utf-8")
    summary = {
        "output": str(args.output), "pages": len(result["pages"]), "runs": len(result["runs"]),
        "errors": sum("error" in run for run in result["runs"]),
        "warmup_only": args.warmup_only,
    }
    print(json.dumps(summary, indent=2))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
