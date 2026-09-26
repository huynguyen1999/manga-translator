#!/usr/bin/env python3
"""Keep legacy application files from growing and new ones below 500 lines."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "repository-state" / "line-count-baseline.json"
ROOTS = ("server", "manga_translator", "front/app")
SUFFIXES = {".py", ".ts", ".tsx"}
EXCLUDED_DIRS = {"node_modules", "build", "dist", "ldm"}
EXCLUDED_FILES = {
    "manga_translator/detection/dbnet_convnext.py",
    "manga_translator/ocr/model_32px.py",
    "manga_translator/ocr/model_48px.py",
    "manga_translator/upscaling/esrgan_pytorch.py",
}


def source_files():
    for root in ROOTS:
        for path in (ROOT / root).rglob("*"):
            relative = path.relative_to(ROOT).as_posix()
            if not path.is_file() or path.suffix not in SUFFIXES:
                continue
            if relative in EXCLUDED_FILES or any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            if path.name.startswith("test_") or ".test." in path.name or path.name.endswith(".d.ts"):
                continue
            yield relative, path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="optional Git base for baseline-change validation")
    args = parser.parse_args()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    limits = baseline["files"]
    limit = baseline["limit"]
    failures = []
    for relative, path in source_files():
        count = len(path.read_bytes().splitlines())
        previous = limits.get(relative)
        allowed = previous if previous is not None else limit
        if count > allowed:
            failures.append(f"{relative}: {count} lines; ratchet allows {allowed}")

    if args.base:
        previous_baseline_result = subprocess.run(
            ["git", "show", f"{args.base}:repository-state/line-count-baseline.json"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if previous_baseline_result.returncode == 0:
            previous_baseline = json.loads(
                previous_baseline_result.stdout
            )
        else:
            previous_baseline = None
        if previous_baseline is not None:
            old_files = previous_baseline["files"]
            if limit > previous_baseline["limit"]:
                failures.append(
                    f"baseline raised the new-file limit from {previous_baseline['limit']} to {limit}"
                )
            for path, count in limits.items():
                old_count = old_files.get(path)
                if old_count is not None and count > old_count:
                    failures.append(f"baseline raised {path} from {old_count} to {count} lines")
                elif old_count is None and count > limit and path not in baseline.get("oversized_extractions", {}):
                    failures.append(f"oversized baseline entry lacks a reason: {path} ({count} lines)")
            for path in old_files.keys() - limits.keys():
                if (ROOT / path).is_file():
                    failures.append(f"baseline dropped an existing source entry: {path}")

    if failures:
        print("Line-count ratchet failed:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in failures), file=sys.stderr)
        return 1
    print(f"Line-count ratchet passed ({len(limits)} baseline files; {limit}-line new-file limit).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
