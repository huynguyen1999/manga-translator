#!/usr/bin/env python3
"""Capture OCR + Detection + Bubble Segmentation + Inpainting on English pages (No Translation).

Usage:
    python devscripts/capture_step_data.py -i <images...> [-o devscripts/data]

Examples:
    python devscripts/capture_step_data.py -i english_page1.png english_page2.png
    python devscripts/capture_step_data.py -i "test_images/*.png" --use-gpu
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
        sys.argv = [sys.argv[0], "capture", "--help"]
    elif sys.argv[1] != "capture":
        sys.argv.insert(1, "capture")
    main()
