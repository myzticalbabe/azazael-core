# coding: utf-8
"""
METAWORMS — DEPRECATED SHIM

This file is DEPRECATED. All implementations have moved to METAWORMS_ENRICHED.py.

For backward compatibility, all classes are re-exported from METAWORMS_ENRICHED.
Existing code that does `from METAWORMS import METAZ, METAWORMS` will continue
working but will transparently use the enriched implementations.

New code should import directly from METAWORMS_ENRICHED:
    from METAWORMS_ENRICHED import METAZ, METAWORMS, WormResult

Meta Worms:
- METAZ   : Metadata extractor
- DEPENDZ : Dependency mapper
- VALIDZ  : Syntax validator
- DEADZ   : Dead code detector
- METRICZ : Code metrics collector
- LOGZ    : Log analyzer
- CHANGEZ : Change tracker

Usage (via shim):
    >>> import METAWORMS
    >>> mw = METAWORMS.METAWORMS()
    >>> mw.run_all()
"""

import warnings

_DEPRECATION_MSG = (
    "METAWORMS.py is deprecated. Use METAWORMS_ENRICHED.py instead. "
    "The shim is transparent — all classes are re-exported from METAWORMS_ENRICHED."
)
warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)

# Re-export from METAWORMS_ENRICHED
# NOTE: RichOutput and Colors are deliberately excluded — they are internal
# formatting helpers not present in the original METAWORMS.py API surface.
from METAWORMS_ENRICHED import (  # noqa: F401, E402
    # Core classes
    WormResult,
    MetaWormConfig,
    # Worm classes
    METAZ,
    DEPENDZ,
    VALIDZ,
    DEADZ,
    METRICZ,
    LOGZ,
    CHANGEZ,
    # Manager
    METAWORMS,
    # Functions
    run_popup,
    main,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Keep old imports working for any code that does `from METAWORMS import *`
__all__ = [
    "WormResult",
    "MetaWormConfig",
    "METAZ",
    "DEPENDZ",
    "VALIDZ",
    "DEADZ",
    "METRICZ",
    "LOGZ",
    "CHANGEZ",
    "METAWORMS",
    "run_popup",
    "main",
]
