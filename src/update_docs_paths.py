#!/usr/bin/env python3
"""
Update Docs Paths — Thin wrapper around AZAZAEL_TOOLKIT canonical version.

Canonical source at src/AZAZAEL_TOOLKIT/update_docs_paths.py.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # package parent (src/)
# A bare `from update_docs_paths import main` re-enters this shim (same name,
# already in sys.modules) before `main` exists -> circular import. But the
# canonical module exposes NO `main` either, so importing one is equally broken;
# delegate to its `__main__` block instead.
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
    """Run the canonical module's own entry point."""
    runpy.run_module("AZAZAEL_TOOLKIT.update_docs_paths", run_name="__main__")
    return 0


if __name__ == '__main__':
    main()
