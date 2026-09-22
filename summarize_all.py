#!/usr/bin/env python3
"""Convenience root entrypoint for batch manga summarization."""

import sys
from pathlib import Path

# Add project root and devscripts to path
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from devscripts.summarize_all import main

if __name__ == "__main__":
    sys.exit(main())
