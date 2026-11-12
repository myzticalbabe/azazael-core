import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

# coding: utf-8
"""
Openinterpreter Startup

Module for ml_platforms functionality.

Functions:
    - load_ml_platforms()

Usage:
    >>> import openinterpreter_startup
    >>> openinterpreter_startup.load_ml_platforms()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
Open-Interpreter Auto-Loader
Run this to auto-load ML platforms when open-interpreter starts.

Usage:
1. set environment variable: OPEN_INTERPRETER_EXTRA_PATHS
2. Or create a startup file: ~/.open-interpreter/startup.py
3. Or add to system PYTHONPATH

Quick setup (choose ONE method):
"""

import sys
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

ML_PATH = Path.home() / "Desktop" / "Azazael" / "ml_platforms"


def load_ml_platforms():
    """Load ML platforms for open-interpreter."""
    if str(ML_PATH) not in sys.path:
        sys.path.insert(0, str(ML_PATH))

    try:
        pass

        print("[OK] ML Platforms loaded for open-interpreter")
        return True
    except Exception as e:
        print(f"[FAIL] Error loading ML platforms: {e}")
        return False


# Auto-load when this file is imported
load_ml_platforms()
