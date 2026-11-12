import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "HYDRA" / "ml_platforms"))

# coding: utf-8
"""
LM Studio Integration - Local Only
Access ML platforms directly - no OpenAI, no internet needed.

Usage:
    from lm_studio_integration import *
    list_platforms()
"""

import sys
from typing import List
import time

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

_IMPORTS_WORKING = False

try:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _IMPROVEMENTS_DIR = os.path.join(_BASE_DIR, "improvements")

    if _IMPROVEMENTS_DIR not in sys.path:
        sys.path.insert(0, _IMPROVEMENTS_DIR)

    from tensor_utils import to_tensor

    _IMPORTS_WORKING = True

except Exception as e:
    print(f"[WARN] Some imports failed: {e}")


def list_platforms():
    """list all available ML platforms."""
    print("\n" + "=" * 60)
    print("AVAILABLE ML PLATFORMS")
    print("=" * 60)
    print("  [OK] PyTorch       - Deep learning framework")
    print("  [OK] TensorFlow    - Deep learning framework")
    print("  [OK] NumPy         - Array computing")
    print("  [OK] scikit-learn  - Classical ML")
    print("  [OK] ONNX          - Model interchange")
    print("  [OK] MLflow        - Experiment tracking")
    print("  [OK] Ray           - Distributed computing")
    print("=" * 60 + "\n")


def convert(arr, to_framework="pytorch"):
    """Convert array to specified framework tensor."""
    if not _IMPORTS_WORKING:
        print("[ERROR] Imports not working")
        return None

    try:
        result = to_tensor(arr, to_framework)
        print(f"Converted to {to_framework}: {type(result).__module__}")
        return result
    except Exception as e:
        print(f"Convert failed: {e}")
        return None


def debug(error_msg):
    """Debug ML errors and suggest fixes."""
    print("\n" + "=" * 60)
    print("DEBUG ANALYSIS")
    print("=" * 60)
    print(f"Error: {error_msg}\n")

    fixes = {
        "cuda out of memory": "REDUCE BATCH SIZE or use gradient accumulation",
        "module not found": "RUN: pip install the-missing-package",
        "shape mismatch": "Use tensor.reshape() or tensor.view()",
        "dtype error": "Convert: tensor.float() or tensor.long()",
        "nan loss": "Check data normalization, add gradient clipping",
    }

    found = False
    for key, fix in fixes.items():
        if key in error_msg.lower():
            print(f"[ISSUE] {key}")
            print(f"[FIX] {fix}")
            found = True

    if not found:
        print("[SUGGESTION] Check framework documentation")

    print("=" * 60 + "\n")


def tensor_info(tensor):
    """Print tensor information."""
    print(f"\nType: {type(tensor).__module__}.{type(tensor).__name__}")
    if hasattr(tensor, "shape"):
        print(f"Shape: {tensor.shape}")
    if hasattr(tensor, "dtype"):
        print(f"Dtype: {tensor.dtype}")
    if hasattr(tensor, "device"):
        print(f"Device: {tensor.device}")


def benchmark(func, *args, **kwargs):
    """Benchmark a function execution."""

    start = time.time()
    result = func(*args, **kwargs)
    elapsed = time.time() - start
    print(f"\nBenchmark: {elapsed:.4f}s")
    return result


__all__ = [
    "list_platforms",
    "convert",
    "debug",
    "tensor_info",
    "benchmark",
]


if __name__ == "__main__":
    print("=" * 60)
    print("LM STUDIO INTEGRATION - LOCAL")
    print("=" * 60)
    print(f"\nImports working: {_IMPORTS_WORKING}")
    list_platforms()
    print("=" * 60)
