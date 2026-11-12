# coding: utf-8
"""DEPRECATED shim — this is the NON-canonical copy of the `azazael_pygame` pair.

Canonical implementation: `src/azazael_pygame.py`
    from azazael_pygame import AzazaelVisualizer, EnrichmentDisplay

This file previously held a near-copy of the canonical module. The two differed
only in three dead statements: the canonical's bare
`(*sys["color"], int(50 + sys["pulse"] * 100))` / `(*badge.color, ...)`
expression statements were assigned here to locals that nothing read, so the copy
carried three unused variables and no behaviour the canonical lacked.

MEASURED 2026-10-11: NOTHING in the tree imports either copy, so the duplicate was
retired to a re-export rather than deleted outright — the import path keeps
working for any out-of-tree caller while the implementation lives in exactly one
place. Registry rows D-5 and D-3.

Follows the repo's shim convention (skill `azazael-shims`, mirrored from
`src/ML/dream.py`): a module-level `FutureWarning` with `stacklevel=2`, explicit
re-export, and an `__all__` that states the API contract.
"""
import sys
import warnings as _warnings
from pathlib import Path

# ── Import-root bootstrap ──────────────────────────────────────────
#: The canonical module is `src/azazael_pygame.py`, i.e. the TOP-LEVEL name
#: `azazael_pygame` once `src/` is on the path — the canonical's own
#: `from CORE.failure_ledger import ...` shows that `src/` is the import root.
#: This shim sits two directories below it, so the root is derived from
#: `__file__` (the wave-3 convention for bootstrap modules) rather than through
#: `CORE.paths`: a bootstrap module must not depend on the package it is
#: bootstrapping, and `CORE.paths` resolves the REPO root, which is not where
#: these top-level modules live.
_SRC_DIR = Path(__file__).resolve().parents[2]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ── Deprecation ────────────────────────────────────────────────────
_warnings.warn(
    "CODEX.CORE.azazael_pygame is the NON-canonical copy of the azazael_pygame "
    "pair; import from azazael_pygame instead",
    FutureWarning, stacklevel=2,
)

#: The implementation, in ONE place. `# noqa: E402` because the warning and the
#: bootstrap above must run first; `# noqa: F401` because a re-export is
#: deliberately "unused" in the module that re-exports it.
from azazael_pygame import (  # noqa: E402, F401
    FPS,
    HEIGHT,
    WIDTH,
    AnimationState,
    AzazaelVisualizer,
    Badge,
    COLORS,
    EnrichmentDisplay,
    EnrichmentField,
    EnrichmentType,
    Particle,
    SYSTEMS,
    Tag,
    TimelineEvent,
)

import azazael_pygame as _canonical  # noqa: E402

#: NOT in `__all__` (it is underscore-private) but CONTRACTUAL: the canonical
#: module's `if _PYGAME_AVAILABLE: pygame.init()` gate and its `__main__` handler
#: both read it, and this path re-exported it before the copy was retired.
_PYGAME_AVAILABLE: bool = bool(getattr(_canonical, "_PYGAME_AVAILABLE", False))

__all__ = [
    "AnimationState",
    "AzazaelVisualizer",
    "Badge",
    "COLORS",
    "EnrichmentDisplay",
    "EnrichmentField",
    "EnrichmentType",
    "FPS",
    "HEIGHT",
    "Particle",
    "SYSTEMS",
    "Tag",
    "TimelineEvent",
    "WIDTH",
]


def __getattr__(name: str):
    """Delegate any other name to the canonical module (PEP 562).

    The retired copy also exposed whatever it imported into its own namespace
    (`random`, `math`, `time`, `dataclasses`, `note_failure`, ...). Enumerating
    those here would re-declare an implementation surface for no benefit, so
    anything not listed above resolves against the canonical module instead. A
    name that exists in NEITHER raises AttributeError, so a typo stays a typo
    rather than silently becoming `None`.
    """
    try:
        return getattr(_canonical, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
