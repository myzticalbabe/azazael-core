import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

# coding: utf-8
"""
Lm Studio Autoload

Module for ml_platforms functionality.

Functions:

Usage:
    >>> import lm_studio_autoload
    >>> lm_studio_autoload.function()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
r"""
LM Studio Auto-Loader
Run this BEFORE starting LM Studio, or configure in System Prompt.

Usage:
1. Quick load (paste this command in LM Studio chat):
            import sys;sys.path.insert(0,Path.home() / "Desktop" / "Azazael");from lm_studio_integration import*

2. Or run server first:
            python lm_studio_server.py

3. Then access via HTTP API at http://localhost:5000

Auto-load methods for LM Studio:
- Method 1: Add to System Prompt (Settings > General > System Prompt)
- Method 2: Use API mode - start server first
- Method 3: Create desktop shortcut
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


def auto_load():
    """Load ML platforms for LM Studio."""
    if str(ML_PATH) not in sys.path:
        sys.path.insert(0, str(ML_PATH))

    try:
        pass

        print("[OK] ML Platforms loaded successfully")
        print("\nAvailable functions:")
        print("  list_platforms()   - Show all ML platforms")
        print("  convert(arr, 'pytorch')  - Convert tensors")
        print("  debug('error message')    - Debug ML errors")
        print("  optimize('vmap')          - Get optimization patterns")
        print("  track_experiment('name')  - Start MLflow tracking")
        return True
    except Exception as e:
        print(f"[FAIL] Error loading: {e}")
        return False


if __name__ == "__main__":
    auto_load()
