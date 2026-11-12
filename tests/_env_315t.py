# coding: utf-8
"""Shared test environment helper for Python 3.15t free-threading stability.

Import this at the TOP of every test file's ``if __name__ == "__main__"``
block (before any src/ imports) to:
  1. Set C-extension thread-limiting env vars (OpenBLAS, MKL, etc.)
  2. Mock requests/chardet to prevent MemoryError on import
  3. Mock PamsRecorder to prevent import-time HTTP library crashes

Usage in each test file:
    if __name__ == "__main__":
        from tests._env_315t import setup_315t
        setup_315t()

See: Error #155, Error #156, Lesson #82.
"""

import os
import sys


def _is_free_threaded() -> bool:
    """Detect Python 3.15 free-threading build."""
    try:
        return not sys._is_gil_enabled()
    except Exception:
        return "t" in sys.version.lower()


def _constrain_c_extensions() -> None:
    """Force C-extension thread pools to single-threaded.

    Python 3.15t free-threading clashes with C-libraries that pre-allocate
    memory based on CPU count (OpenBLAS, MKL, etc.). Single-threaded mode
    prevents allocation failures and deadlocks.
    """
    for var in (
        "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
    ):
        os.environ.setdefault(var, "1")


def _mock_requests_stack() -> None:
    """Pre-mock requests/chardet/pytest/pygments to prevent MemoryError on import.

    Under Python 3.15t, C-extensions (chardet, pygments) have race conditions
    that cause MemoryError during module compilation. Pre-loading dummy modules
    into sys.modules bypasses the crash entirely.
    """
    import unittest.mock
    for mod in ("requests", "chardet", "pygments", "pygments.lexers",
                "pygments.lexers._mapping", "pygments.util", "pygments.token",
                "pygments.lexer", "pygments.filter", "pygments.formatter",
                "pygments.style", "pygments.plugin", "pygments.modeline"):
        if mod not in sys.modules:
            sys.modules[mod] = unittest.mock.MagicMock()


def _mock_pams_recorder() -> None:
    """Pre-mock PamsRecorder to prevent import-time HTTP crashes.

    PamsRecorder imports requests for HTTP calls. Under 3.15t this
    can MemoryError. Replace with a no-op stub so worm execution
    tests can still run without real PAMS connectivity.
    """
    import unittest.mock
    # CORE.PamsRecorder module mock — all attribute access returns MagicMocks
    if "CORE.PamsRecorder" not in sys.modules:
        sys.modules["CORE.PamsRecorder"] = unittest.mock.MagicMock()
        # PamsRecorder() returns a MagicMock whose methods are auto-generated
        sys.modules["CORE.PamsRecorder"].PamsRecorder = unittest.mock.MagicMock()


def setup_315t(mock_pams: bool = True) -> bool:
    """Set up the 3.15t test environment.

    Args:
        mock_pams: If True, also mock PamsRecorder (breaks PAMS recording
                   but prevents MemoryError in orchestration tests).

    Returns:
        True if running under 3.15t free-threading, False otherwise.
    """
    if not _is_free_threaded():
        return False

    _constrain_c_extensions()
    _mock_requests_stack()
    if mock_pams:
        _mock_pams_recorder()
    return True


# Backward-compat alias (tests still importing the 3.14-era name keep working).
def setup_314t(mock_pams: bool = True) -> bool:
    """Deprecated alias of setup_315t (3.14-era import name)."""
    return setup_315t(mock_pams=mock_pams)
