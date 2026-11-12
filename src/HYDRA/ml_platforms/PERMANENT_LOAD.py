import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "ml_platforms"))

# coding: utf-8
"""
Permanent Load

Module for ml_platforms functionality.

Functions:
    - debug_all_errors()
    - excepthook()

Usage:
    >>> import PERMANENT_LOAD
    >>> PERMANENT_LOAD.debug_all_errors()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
PERMANENT AUTO-LOAD COMMAND FOR LM STUDIO
Copy-paste this entire block into LM Studio Python interpreter
"""

import sys
from typing import List
import traceback

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# 1. Add ML platforms to path PERMANENTLY
ML_PATH = Path.home() / "Desktop" / "Azazael" / "ml_platforms"
if str(ML_PATH) not in sys.path:
    sys.path.insert(0, str(ML_PATH))
    # Persist via user sitecustomize ONLY when run as a script. Importing
    # this module must be side-effect free: runtime trace 2026-10-18 showed
    # a bare import appending a dead Desktop path to ~/sitecustomize.py,
    # which then prepends a nonexistent dir on EVERY interpreter startup.
    if __name__ == "__main__":
        try:
            site_path = os.path.join(os.path.expanduser("~"), "sitecustomize.py")
            with open(site_path, "a", encoding="utf-8") as f:
                f.write(f"\nimport sys\nsys.path.insert(0, r'{ML_PATH}')\n")
            print("[OK] Persisted to user sitecustomize.py")
        except Exception as exc:
            note_failure("PERMANENT_LOAD:L43", exc)

# 2. Import all ML platform tools
try:
    from lm_studio_integration import (
        list_platforms,
    )

    print("[OK] All ML tools imported")
except Exception as e:
    # NOTE (runtime trace 2026-10-17): the section-4 call below used the
    # name unconditionally, so a failed import died with NameError instead
    # of the warned degradation. The flag keeps import and use consistent.
    list_platforms = None
    print(f"[WARN] Import error: {e}")


# 3. Create global error debugger
def debug_all_errors():
    """Debug any ML error automatically."""

    def excepthook(type, value, tb):
        print("\n" + "=" * 60)
        print("ML ERROR DETECTED - AUTO DEBUG")
        print("=" * 60)
        print(f"\nError: {type.__name__}: {value}\n")
        print("Traceback:")
        print("-" * 40)
        traceback.print_exception(type, value, tb)
        print("-" * 40)

        # Auto-suggest fixes
        err_msg = str(value).lower()
        fixes = []

        if "cuda" in err_msg and "memory" in err_msg:
            fixes.append("FIX: Reduce batch size or use gradient accumulation")
            fixes.append("FIX: set model.to('cpu') instead of GPU")
        elif "shape mismatch" in err_msg:
            fixes.append("FIX: Use tensor.reshape() or tensor.view()")
            fixes.append("FIX: Check input dimensions")
        elif "dtype" in err_msg:
            fixes.append("FIX: Use .float() or .long() to convert types")
        elif "module not found" in err_msg:
            fixes.append("FIX: pip install the-missing-package")
        elif "nan" in err_msg:
            fixes.append("FIX: Check data normalization")
            fixes.append("FIX: Add gradient clipping")
        elif "onnx" in err_msg:
            fixes.append("FIX: Use symbolic_trace or skip unsupported ops")

        if fixes:
            print("\nAuto-suggested fixes:")
            for f in fixes:
                print(f"  {f}")
        else:
            print("\nNo auto-fix found. Check documentation.")

        print("=" * 60)

    sys.excepthook = excepthook
    print("[OK] Global error debugger installed")


# 4. Run auto-load
print("\n" + "=" * 60)
print("ML PLATFORMS AUTO-LOADED")
print("=" * 60)
if list_platforms is not None:
    list_platforms()
else:
    print("[SKIP] platform listing unavailable (import failed above)")
print("\nAvailable commands:")
print("  convert(arr, 'pytorch')  # Convert tensors")
print("  debug('error message')   # Debug errors")
print("  optimize('vmap')         # Get patterns")
print("  track_experiment('name') # Start MLflow")
print("  tensor_info(tensor)      # Show tensor info")
print("=" * 60 + "\n")

# Enable debugging
debug_all_errors()
print("[OK] Ready to debug errors automatically!\n")
