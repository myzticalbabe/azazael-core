#!/usr/bin/env python3
"""
CODEX/CORE/FILE_MANIFEST_GENERATOR.py — DEPRECATION SHIM

This file was a stale copy (hardcoded Desktop paths in __main__ block).
It now re-exports from the canonical source: azazael/src/FILE_MANIFEST_GENERATOR.py

The canonical version uses get_desktop_dir() for dynamic path resolution.

Update your imports from:
    from CODEX.CORE.FILE_MANIFEST_GENERATOR import generate_manifest
to:
    from FILE_MANIFEST_GENERATOR import generate_manifest

This shim will be removed in a future release.
"""

import sys
import warnings
from pathlib import Path

warnings.warn(
    "CODEX.CORE.FILE_MANIFEST_GENERATOR is deprecated. "
    "Use 'from FILE_MANIFEST_GENERATOR import ...' instead. "
    "This shim will be removed in a future release.",
    UserWarning,
    stacklevel=2,
)

_src_dir = Path(__file__).resolve().parent.parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))
# Remove CORE directory from path to prevent circular self-import
_own_dir = str(Path(__file__).resolve().parent)
if _own_dir in sys.path:
    sys.path.remove(_own_dir)

from FILE_MANIFEST_GENERATOR import *  # noqa: E402, F401
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
