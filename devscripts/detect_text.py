#!/usr/bin/env python3
"""Unified Manga Text Detection and Segmentation CLI.

Supports switching between multiple state-of-the-art text detection models:
- PP-OCRv6_manga v0.2 ('ppocrv6'): Fast, lightweight ONNX detector with Apple Silicon / CUDA acceleration.
- Core Translation App Default ('default'): ResNet-34 DBNet (detect-20241225.ckpt).
- Comic Text Detector ('ctd'): YOLOv5 + DBNet text line detector (comictextdetector.pt).
- ContemporaryCat Manga-Text-Segmentation ('contemporarycat'): Dense character stroke segmentation and text region clustering.

Includes corner confidence badges, tight polygon boundaries, high-resolution text masks,
coarse-to-fine glyph/stroke refinement, and side-by-side comparison overlays.
"""

from __future__ import annotations
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from devscripts.detect_text_ppocrv6 import main

if __name__ == "__main__":
    main()
