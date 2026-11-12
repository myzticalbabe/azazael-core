# coding: utf-8
"""
CODEX/CORE/CHANGELOG_GENERATOR.py — DEPRECATION SHIM

This file was a stale copy (hardcoded paths, missing dynamic path resolution).
It now re-exports from the canonical source: azazael/src/CHANGELOG_GENERATOR.py

The canonical version uses get_desktop_dir() for dynamic path resolution.

Update your imports from:
    from CODEX.CORE.CHANGELOG_GENERATOR import ChangelogGenerator
to:
    from CHANGELOG_GENERATOR import ChangelogGenerator

This shim will be removed in a future release.
"""

import sys
import warnings
from pathlib import Path

warnings.warn(
    "CODEX.CORE.CHANGELOG_GENERATOR is deprecated. "
    "Use 'from CHANGELOG_GENERATOR import ...' instead. "
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

from CHANGELOG_GENERATOR import *  # noqa: E402, F401
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
