# coding: utf-8
"""
CODEX/CORE/enrichment_display.py — DEPRECATION SHIM

This file was a near-duplicate of the root enrichment_display.py (identical
except for a CSS typo "100% { ... opacity: 1;Draw {").
It now re-exports from the canonical source: azazael/src/enrichment_display.py

The canonical version has correct CSS syntax.

Update your imports from:
    from CODEX.CORE.enrichment_display import ...
to:
    from enrichment_display import ...

This shim will be removed in a future release.
"""

import sys
import warnings
from pathlib import Path

warnings.warn(
    "CODEX.CORE.enrichment_display is deprecated. "
    "Use 'from enrichment_display import ...' instead. "
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

from enrichment_display import *  # noqa: E402, F401
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
