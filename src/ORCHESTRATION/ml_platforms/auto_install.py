# coding: utf-8
"""
Auto Install for ml_platforms
Automatically installs missing packages using uv.

Usage:
    python auto_install.py
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

REQUIRED_PACKAGES = [
    # Deep Learning
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "jax",
    "flax",
    "keras",
    # Scientific Computing
    "numpy",
    "scipy",
    "scikit-learn",
    "pandas",
    "polars",
    # ONNX & Interoperability
    "onnx",
    "onnxruntime",
    "onnxruntime-tools",
    "tf2onnx",
    # MLflow & Experiment Tracking
    "mlflow",
    "tensorboard",
    # Distributed Computing
    "ray",
    "dask",
    # NLP
    "transformers",
    "tokenizers",
    "huggingface-hub",
    "nltk",
    "spacy",
    "sentence-transformers",
    # Computer Vision
    "opencv-python",
    "opencv-python-headless",
    "pillow",
    "scikit-image",
    "easyocr",
    "deepface",
    "mmcv",
    "mmdetection",
    "detectron2",
    # Reinforcement Learning
    "gymnasium",
    "stable-baselines3",
    "sb3-contrib",
    # Optimization
    "optuna",
    "hyperopt",
    "ray[tune]",
    # Utilities
    "pyyaml",
    "tqdm",
    "requests",
    "aiohttp",
    "python-dotenv",
]


def check_package(package):
    """Check if a package is installed."""
    try:
        __import__(package.replace("-", "_"))
        return True, None
    except ImportError as exc:
        note_failure("auto_install:L78", exc)
        return False, None


def install_package(package):
    """Install a single package using uv."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "uv", "pip", "install", package],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        timeout=1800)
        if result.returncode == 0:
            return True, "OK"
        else:
            error_msg = (
                result.stderr.strip().split("\n")[-1]
                if result.stderr
                else "Unknown error"
            )
            return False, error_msg
    except Exception as e:
        note_failure("auto_install:L99", e)
        return False, str(e)


def main():
    """Main auto-install function."""
    print("=" * 60)
    print("ML PLATFORMS AUTO-INSTALL")
    print("=" * 60)
    print()
    print(f"Python: {sys.version.split()[0]}")
    print(f"Location: {sys.executable}")
    print()
    print("Using uv for package management")
    print()

    installed = []
    failed = []
    already_ok = []

    print("Checking and installing packages...")
    print("-" * 60)

    for pkg in REQUIRED_PACKAGES:
        pkg_name = pkg.split("[")[0]

        ok, _ = check_package(pkg_name)
        if ok:
            already_ok.append(pkg_name)
            print(f"  [OK] {pkg_name}")
            continue

        print(f"  [INSTALLING] {pkg}...", end=" ", flush=True)
        success, msg = install_package(pkg)

        if success:
            installed.append(pkg)
            print("OK")
        else:
            failed.append((pkg, msg))
            print(f"FAILED: {msg[:50]}")

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Already installed: {len(already_ok)}")
    print(f"Installed: {len(installed)}")
    print(f"Failed: {len(failed)}")

    if failed:
        print()
        print("Failed packages:")
        for pkg, msg in failed:
            print(f"  - {pkg}: {msg[:60]}")

        print()
        print("=" * 60)
        print("AUTO-INSTALL COMPLETE")
        print("=" * 60)


if __name__ == "__main__":
    main()
