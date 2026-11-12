# coding: utf-8
"""
CORE/paths/internal.py — Core internal directory resolution.

Resolves paths within the Azazael project tree:
  - get_project_root()    -> azazael/
  - get_src_dir()         -> azazael/src/
  - get_core_dir()        -> azazael/src/CORE/
  - get_worms_dir()       -> azazael/src/CORE/WORMS/
  - get_meta_dir()        -> azazael/src/META/
  - get_codex_dir()       -> azazael/src/CODEX/
  - get_codex_videos_dir()-> azazael/src/CODEX/VIDEOS/
  - resolve_path(path)    -> resolve relative/absolute paths
  - ensure_dir(path)      -> mkdir -p

All functions cache results in the shared _cache dict from .state.
"""

import os
from pathlib import Path

from .state import _cache
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _resolve_project_root() -> Path:
    """Determine the project root (azazael/) from this file's location.

    IMPORTANT: The parent chain depth depends on the file's location in
    the tree. This file is at azazael/src/CORE/paths/internal.py, so
    project root is 4 levels up. If this file is moved, update the count.
    """
    root = Path(__file__).resolve().parent.parent.parent.parent
    # Sentinel: verify the resolved path actually contains the expected structure
    if not (root / "src" / "CORE").is_dir():
        raise RuntimeError(
            f"Project root detection failed from {__file__}: "
            f"resolved to {root} but src/CORE/ not found there"
        )
    return root


def get_project_root() -> Path:
    """Get the Azazael project root directory.

    Resolution order:
    1. AZAZAEL_ROOT environment variable
    2. Computed from this file's location

    Returns: Path to azazael/
    """
    if "project_root" not in _cache:
        env_root = os.environ.get("AZAZAEL_ROOT")
        _cache["project_root"] = Path(env_root) if env_root else _resolve_project_root()
    return _cache["project_root"]


def get_project_root_safe() -> Path:
    """Project root that CANNOT raise -- the fail-open sibling of
    :func:`get_project_root`.

    MEASURED 2026-10-11. Twenty-two sites across ~20 modules carried the fallback
    ``Path.home() / "azazael"`` for their project root, which on this host names a
    DIFFERENT, stale checkout from the one the module ships in (``C:/Azazael/azazael``),
    so with ``AZAZAEL_ROOT`` unset they read and wrote the wrong tree. Each site fixed
    itself that way because ``get_project_root()`` RAISES on a sentinel failure and a
    monitor must not die for it.

    This exists so that decision is made ONCE. It shares ``get_project_root()``'s
    precedence exactly -- ``AZAZAEL_ROOT`` first, then the location-derived root -- and
    only the sentinel failure is answered differently: instead of the stale home-relative
    path it returns this file's own tree root (``src/CORE/paths/internal.py`` is four
    levels below the root, so ``parents[3]``), which is by construction the tree the
    caller is running from.
    """
    try:
        return get_project_root()
    except RuntimeError:
        return Path(__file__).resolve().parents[3]


def get_src_dir() -> Path:
    """Get the azazael/src/ directory."""
    if "src_dir" not in _cache:
        _cache["src_dir"] = get_project_root() / "src"
    return _cache["src_dir"]


def get_core_dir() -> Path:
    """Get the azazael/src/CORE/ directory."""
    if "core_dir" not in _cache:
        _cache["core_dir"] = get_src_dir() / "CORE"
    return _cache["core_dir"]


def get_worms_dir() -> Path:
    """Get the azazael/src/CORE/WORMS/ directory."""
    if "worms_dir" not in _cache:
        _cache["worms_dir"] = get_core_dir() / "WORMS"
    return _cache["worms_dir"]


def get_meta_dir() -> Path:
    """Get the azazael/src/META/ directory."""
    if "meta_dir" not in _cache:
        _cache["meta_dir"] = get_src_dir() / "META"
    return _cache["meta_dir"]


def get_codex_dir() -> Path:
    """Get the azazael/src/CODEX/ directory."""
    if "codex_dir" not in _cache:
        _cache["codex_dir"] = get_src_dir() / "CODEX"
    return _cache["codex_dir"]


def get_codex_videos_dir() -> Path:
    """Get the azazael/src/CODEX/VIDEOS/ directory."""
    if "codex_videos_dir" not in _cache:
        _cache["codex_videos_dir"] = get_codex_dir() / "VIDEOS"
    return _cache["codex_videos_dir"]


def resolve_path(path: str) -> Path:
    """Resolve a possibly-relative path against the project root.

    If the path is absolute, return it as-is.
    If it starts with ~, expand it.
    Otherwise, resolve relative to the project root.
    """
    p = Path(path)
    if p.is_absolute():
        return p
    if str(path).startswith("~"):
        return p.expanduser()
    if str(path).startswith("G:"):
        return p
    return get_project_root() / p


def ensure_dir(path: Path) -> Path:
    """Ensure a directory exists, creating it if needed. Returns the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path
