# coding: utf-8
"""
CORE/paths/ — Domain-specific path resolution modules.

Split from CORE/paths.py to reduce single-point-of-failure coupling
(original: 139+ inbound deps). This __init__.py serves as the backward-
compat shim — all existing imports from CORE.paths continue to work.

Domain modules:
  - state.py     -> _cache, VERSION, clear_cache()
  - internal.py  -> get_project_root, get_src_dir, get_core_dir, etc.
  - external.py  -> get_desktop_dir, get_g_drive, get_output_dir
  - targets.py   -> get_desktop_targets, get_local_targets, etc.

Usage (UNCHANGED):
    from CORE.paths import get_project_root, get_desktop_dir
    root = get_project_root()  # azazael/
    desktop = get_desktop_dir()

Environment Variables:
    AZAZAEL_ROOT       — override project root
    AZAZAEL_OUTPUT     — override output directory
    AZAZAEL_ARCHIVE    — override archive directory (default: G:/AZAZAEL/)
"""

from .state import VERSION, clear_cache
from .internal import (
    get_project_root,
    get_project_root_safe,
    get_src_dir,
    get_core_dir,
    get_worms_dir,
    get_meta_dir,
    get_codex_dir,
    get_codex_videos_dir,
    resolve_path,
    ensure_dir,
)
from .external import get_desktop_dir, get_g_drive, get_output_dir
from .targets import (
    DESKTOP_TARGET_NAMES,
    LOCAL_TARGET_SUBDIRS,
    get_desktop_targets,
    get_local_targets,
    get_trim_targets,
    get_error_targets,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "VERSION",
    "clear_cache",
    "get_project_root",
    "get_project_root_safe",
    "get_src_dir",
    "get_core_dir",
    "get_worms_dir",
    "get_meta_dir",
    "get_codex_dir",
    "get_codex_videos_dir",
    "resolve_path",
    "ensure_dir",
    "get_desktop_dir",
    "get_g_drive",
    "get_output_dir",
    "DESKTOP_TARGET_NAMES",
    "LOCAL_TARGET_SUBDIRS",
    "get_desktop_targets",
    "get_local_targets",
    "get_trim_targets",
    "get_error_targets",
]

# Self-test when run as a module (e.g., python -m CORE.paths)
if __name__ == "__main__":
    print(f"CORE/paths/ v{VERSION} (domain-split)")
    print(f"  Project root:  {get_project_root()}")
    print(f"  Src dir:       {get_src_dir()}")
    print(f"  Output dir:    {get_output_dir()}")
    print(f"  Desktop dir:   {get_desktop_dir()}")
    print(f"  G: drive:      {get_g_drive()}")
    print(f"  CORE dir:      {get_core_dir()}")
    print(f"  WORMS dir:     {get_worms_dir()}")
    print(f"  META dir:      {get_meta_dir()}")
    print(f"  CODEX dir:     {get_codex_dir()}")
    print(f"  CODEX/VIDEOS:  {get_codex_videos_dir()}")
