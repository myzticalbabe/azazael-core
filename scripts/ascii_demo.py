#!/usr/bin/env python3
"""ASCII Flowchart quick demo — prints Azazael architecture and pipeline diagrams."""

import sys
from pathlib import Path

# Ensure src/ is importable
_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from CODEX.CORE.ascii_flowchart import azazael_architecture, azazael_pipeline

print("=== AZAZAEL 7-TIER ARCHITECTURE ===")
print(azazael_architecture())

print()
print("=== DATA PROCESSING PIPELINE ===")
print(azazael_pipeline())
