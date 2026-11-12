# coding: utf-8
"""
CORE/paths/targets.py — Target list functions for TRIMZ, ERRORZ, etc.

Provides pre-computed lists of target directories used by filesystem
scanning worms. Imports from sibling domain modules at top level
(state → internal → external → targets forms a clean DAG with no cycles).
"""

from pathlib import Path

from .state import _cache
from .external import get_desktop_dir
from .internal import get_src_dir, get_worms_dir, get_meta_dir
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# Canonical target folder names (single source of truth for TRIMZ, ERRORZ, etc.).
# Consumers import these directly instead of re-declaring their own lists.
DESKTOP_TARGET_NAMES = [
    "Azazael", "HYDRA.py", "HORUS.py", "TARBABY.py",
    "POINDEXTER", "PERSEPHONE", "WORMZ", "METAWORMS",
]

# Canonical local target subdirectories (relative to the src/ dir).
# "" == the src/ dir itself (matches _PROJECT_ROOT in TRIMZ.py / ERRORZ.py).
LOCAL_TARGET_SUBDIRS = [
    "",           # src/ itself
    "CORE/WORMS",
    "META",
]


def get_desktop_targets() -> list:
    """Get standard Desktop target folders used by TRIMZ, ERRORZ, etc."""
    if "desktop_targets" not in _cache:
        _cache["desktop_targets"] = [
            str(get_desktop_dir() / d) for d in DESKTOP_TARGET_NAMES
        ]
    return list(_cache["desktop_targets"])


def get_local_targets() -> list:
    """Get standard local project target folders (matches _PROJECT_ROOT from TRIMZ/ERRORZ)."""
    if "local_targets" not in _cache:
        _cache["local_targets"] = [
            str(get_src_dir() / s) if s else str(get_src_dir())
            for s in LOCAL_TARGET_SUBDIRS
        ]
    return list(_cache["local_targets"])


def get_trim_targets() -> list:
    """Get TRIMZ-specific target folders (desktop + local, filtered for existence)."""
    if "trim_targets" not in _cache:
        desktop = [p for p in get_desktop_targets() if Path(p).exists()]
        _cache["trim_targets"] = desktop + get_local_targets()[:1]
    return list(_cache["trim_targets"])


def get_error_targets() -> list:
    """Get ERRORZ-specific target folders (desktop + local, filtered for existence)."""
    if "error_targets" not in _cache:
        desktop = [p for p in get_desktop_targets() if Path(p).exists()]
        _cache["error_targets"] = desktop + get_local_targets()
    return list(_cache["error_targets"])
