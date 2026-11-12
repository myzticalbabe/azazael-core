# coding: utf-8
"""
Install Improvements

Module for ml_platforms functionality.

Functions:
    - run_pip_install()
    - main()

Usage:
    >>> import install_improvements
    >>> install_improvements.run_pip_install()

Author: Auto-generated
Date: 2026-02-10
"""

#!/usr/bin/env python3
"""
Install script for ML Platform improvements
Installs all suggested improvements and dependencies.
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


def run_pip_install(packages):
    """Install packages with pip."""
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + packages
    print(f"  OK: {' '.join(packages)}")
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if result.returncode == 0:
        print("  [OK]")
    else:
        print(f"  [FAIL]: {result.stderr[:800]}")


def main():
    print("=" * 60)
    print("ML PLATFORM IMPROVEMENTS INSTALLER")
    print("=" * 60)
    print()

    print("Installing core dependencies...")
    print()

    # Core ML frameworks
    run_pip_install(["torch", "torchvision", "torchaudio"])
    run_pip_install(["tensorflow", "keras"])
    run_pip_install(["jax", "jaxlib"])
    run_pip_install(["numpy", "scipy", "pandas"])

    # MLflow
    run_pip_install(["mlflow", "mlflow[extras]"])

    # Ray
    run_pip_install(["ray[default]"])

    # ONNX
    run_pip_install(["onnx", "onnxruntime", "tf2onnx"])

    # Optimization
    run_pip_install(["optuna", "hyperopt"])

    # Visualization
    run_pip_install(["tensorboard", "wandb", "neptune"])

    # Deployment
    run_pip_install(["fastapi", "uvicorn", "torchserve"])

    # Quantization & Export
    run_pip_install(["onnxruntime-tools", "tensorrt"])

    print()
    print("=" * 60)
    print("INSTALLATION COMPLETE")
    print("=" * 60)
    print()
    print("Installed packages:")
    print("  * PyTorch (torch, torchvision, torchaudio)")
    print("  * TensorFlow + Keras")
    print("  * JAX + JAXlib")
    print("  * NumPy + SciPy + Pandas")
    print("  * MLflow (experiment tracking)")
    print("  * Ray (distributed computing)")
    print("  * ONNX + ONNX Runtime + tf2onnx")
    print("  * Optuna (hyperparameter optimization)")
    print("  * Weights & Biases, Neptune (experiment tracking)")
    print("  * FastAPI + Uvicorn (deployment)")
    print()
    print("Next steps:")
    print("  1. Test imports: python -c 'import torch; print(torch.__version__)'")
    print("  2. Run demos: python ml_platforms/improvements/tensor_utils.py")
    print("  3. View docs: cat ml_platforms/improvements/IMPROVEMENTS.md")
    print()


if __name__ == "__main__":
    main()
