#!/usr/bin/env python3
"""Fit OCR text back onto pages and fast-render from captured step data in devscripts/data.

Usage:
    python devscripts/fast_render.py [-i <sample_dirs...>] [--all] [options]

Examples:
    python devscripts/fast_render.py --all
    python devscripts/fast_render.py -i devscripts/data/english_page1 devscripts/data/english_page2
    python devscripts/fast_render.py --all --renderer manga2eng --line-spacing 0.1
    python devscripts/fast_render.py --all --font-path fonts/anime_ace.ttf --letter-case upper
"""

import sys
from pathlib import Path

# Add project root and devscripts to path
DEV_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DEV_DIR.parent
if str(DEV_DIR) not in sys.path:
    sys.path.insert(0, str(DEV_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline_step_runner import main

if __name__ == "__main__":
    if len(sys.argv) == 1 or "-h" in sys.argv or "--help" in sys.argv:
        if "-h" in sys.argv:
            sys.argv.remove("-h")
        if "--help" in sys.argv:
            sys.argv.remove("--help")
        sys.argv = [sys.argv[0], "render", "--help"]
    elif sys.argv[1] != "render":
        sys.argv.insert(1, "render")
    main()
