#!/usr/bin/env python3
"""
COMPREHENSIVE_FIX — Thin wrapper around AZAZAEL_TOOLKIT canonical version.

This file is a convenience alias for the canonical source at
src/AZAZAEL_TOOLKIT/COMPREHENSIVE_FIX.py, kept here for backward
compatibility with code that imports or runs this from the root.

Canonical source is used by Desktop/AZAZAEL_Standalone/build_all.py.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # package parent (src/)
# NOT `from COMPREHENSIVE_FIX import main`: a bare self-name import re-enters THIS
# shim (its name is already on sys.path and in sys.modules) before `main` is defined
# -> partially-initialised circular import. Nor `from AZAZAEL_TOOLKIT.X import main`:
# the canonical module exposes NO `main` (its entry is a class plus a module-level
# `if __name__ == '__main__'` block), so that spelling raised
# `ImportError: cannot import name 'main'` just as loudly.
import runpy  # module-level: the ratchet flags imports inside functions
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

def main() -> int:
    """Run the canonical module's own entry point.

    `AZAZAEL_TOOLKIT.COMPREHENSIVE_FIX` has no callable `main`; running it from the
    root means executing its `__main__` block, which `runpy` does without the shim
    having to duplicate the tool's logic.
    """
    runpy.run_module("AZAZAEL_TOOLKIT.COMPREHENSIVE_FIX", run_name="__main__")
    return 0


if __name__ == '__main__':
    main()
