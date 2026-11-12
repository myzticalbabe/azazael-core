# coding: utf-8
"""
CODEX/CORE/HORUS_INTEGRATION.py — DEPRECATION SHIM

This file was a stale copy (hardcoded paths, broken Path(__file__).parent
relative path resolution).
It now re-exports from the canonical source: azazael/src/HORUS_INTEGRATION.py

The canonical version uses get_src_dir() and get_desktop_dir() for proper
dynamic path resolution.

Update your imports from:
    from CODEX.CORE.HORUS_INTEGRATION import HORUSAnimationBridge
to:
    from HORUS_INTEGRATION import HORUSAnimationBridge

This shim will be removed in a future release.
"""

import sys
import warnings
from pathlib import Path

warnings.warn(
    "CODEX.CORE.HORUS_INTEGRATION is deprecated. "
    "Use 'from HORUS_INTEGRATION import ...' instead. "
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

from HORUS_INTEGRATION import *  # noqa: E402, F401
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
