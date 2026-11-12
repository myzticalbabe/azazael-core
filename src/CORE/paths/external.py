# coding: utf-8
"""
CORE/paths/external.py — External path resolution.

Resolves paths outside the project tree:
  - get_desktop_dir()    -> User's Desktop
  - get_g_drive()        -> G: drive (or None)
  - get_output_dir()     -> Output directory for generated files

All functions cache results in the shared _cache dict from .state.
"""

import os
from pathlib import Path
# from typing import Optional  # removed — all names have native equivalents

from .state import _cache
from .internal import get_project_root
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def get_desktop_dir() -> Path:
    """Get the user's Desktop directory."""
    if "desktop_dir" not in _cache:
        _cache["desktop_dir"] = Path.home() / "Desktop"
    return _cache["desktop_dir"]


def get_g_drive() -> (Path | None):
    """Get the G: drive path if available.

    Resolution order:
    1. AZAZAEL_ARCHIVE environment variable
    2. G:/AZAZAEL/ if G: exists
    3. None if G: is not accessible
    """
    if "g_drive" not in _cache:
        env_arch = os.environ.get("AZAZAEL_ARCHIVE")
        if env_arch:
            _cache["g_drive"] = Path(env_arch)
        else:
            g = Path("G:/")
            _cache["g_drive"] = g / "AZAZAEL" if g.exists() else None
    return _cache["g_drive"]


def get_output_dir() -> Path:
    """Get the output directory for generated files.

    Resolution order:
    1. AZAZAEL_OUTPUT environment variable
    2. azazael/output/
    """
    if "output_dir" not in _cache:
        env_out = os.environ.get("AZAZAEL_OUTPUT")
        out = Path(env_out) if env_out else get_project_root() / "output"
        out.mkdir(parents=True, exist_ok=True)
        _cache["output_dir"] = out
    return _cache["output_dir"]
