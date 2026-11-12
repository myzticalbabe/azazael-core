# coding: utf-8
"""
Install Light

Module for ml_platforms functionality.

Functions:
    - install()
    - main()

Usage:
    >>> import install_light
    >>> install_light.install()

Author: Auto-generated
Date: 2026-02-10
"""

#!/usr/bin/env python3
"""
Lightweight install - only essential packages
Run: python install_light.py
"""

import subprocess
import sys
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

ESSENTIALS = [
    ("mlflow", "MLflow tracking"),
    ("ray", "Distributed computing"),
    ("onnx", "ONNX export"),
    ("onnxruntime", "ONNX inference"),
]


def install(pkg, desc):
    print(f"Installing {pkg}...")
    subprocess.run([sys.executable, "-m", "pip", "install", pkg], capture_output=True, timeout=1800)
    print(f"  [OK] {desc}")


def main():
    print("=" * 60)
    print("INSTALLING ESSENTIAL IMPROVEMENTS")
    print("=" * 60)
    for pkg, desc in ESSENTIALS:
        install(pkg, desc)
        print()
        print("Optional frameworks (manual install):")
        print("  pip install torch tensorflow jax")
        print("  pip install tensorrt coremltools")
        print("=" * 60)


if __name__ == "__main__":
    main()
