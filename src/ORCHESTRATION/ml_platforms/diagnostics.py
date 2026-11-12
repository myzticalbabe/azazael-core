# coding: utf-8
"""
Diagnostics

Module for ml_platforms functionality.

Functions:
    - check_platform_status()
    - print_platform_status()
    - list_workflows()

Usage:
    >>> import diagnostics
    >>> diagnostics.check_platform_status()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
ML Platform Diagnostics
Check platform availability and status.
"""

from .config import _get_config
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


PLATFORMS = {
    "torch": "PyTorch",
    "torchvision": "TorchVision",
    "torchaudio": "TorchAudio",
    "tensorflow": "TensorFlow",
    "keras": "Keras",
    "numpy": "NumPy",
    "pandas": "Pandas",
    "scipy": "SciPy",
    "scikit-learn": "scikit-learn",
    "opencv-python": "OpenCV",
    "pillow": "Pillow",
    "scikit-image": "Scikit-Image",
    "nltk": "NLTK",
    "transformers": "Transformers",
    "huggingface_hub": "HuggingFace Hub",
    "easyocr": "EasyOCR",
    "einops": "EinOps",
    "imageio": "ImageIO",
    "joblib": "Joblib",
    "networkx": "NetworkX",
    "shapely": "Shapely",
    "tensorboard": "TensorBoard",
    "deepface": "DeepFace",
    "mtcnn": "MTCNN",
    "retinaface": "RetinaFace",
    "h5py": "H5PY",
    "tokenizers": "Tokenizers",
    "functorch": "Functorch",
    "torchgen": "TorchGen",
    "mlflow": "MLflow",
    "ray": "Ray",
    "onnx": "ONNX",
    "onnxruntime": "ONNX Runtime",
    "optuna": "Optuna",
    "xgboost": "XGBoost",
    "lightgbm": "LightGBM",
    "catboost": "CatBoost",
    "gymnasium": "Gymnasium",
    "stable-baselines3": "Stable-Baselines3",
}


def check_platform_status() -> tuple[list, list]:
    """Check which platforms can be imported. Returns (available, unavailable) lists."""
    available = []
    unavailable = []

    for pkg, name in PLATFORMS.items():
        try:
            __import__(pkg.replace("-", "_"))
            available.append((pkg, name))
        except ImportError as exc:
            note_failure("diagnostics:L81", exc)
            unavailable.append((pkg, name))

    return available, unavailable


def print_platform_status() -> None:
    """Print formatted platform status."""
    config = _get_config()
    total = config.total_platforms if config.data else 0

    print(f"{'=' * 60}")
    print("PLATFORM STATUS CHECK")
    print(f"{'=' * 60}")
    print(f"Configured platforms: {total}\n")

    available, unavailable = check_platform_status()

    print(f"Available ({len(available)}):")
    for pkg, name in sorted(available):
        print(f"  [OK] {pkg:<25} ({name})")

    print(f"\nUnavailable ({len(unavailable)}):")
    for pkg, name in sorted(unavailable):
        print(f"  [--] {pkg:<25} ({name})")

    print(f"{'=' * 60}")


def list_workflows() -> None:
    """Print recommended workflows."""
    config = _get_config()
    workflows = config.get_workflows() if config.data else []

    print(f"\n{'=' * 60}")
    print("RECOMMENDED ML WORKFLOWS")
    print(f"{'=' * 60}")

    for wf in workflows:
        print(f"\n  * {wf['name']}")
        print(f"    Platforms: {', '.join(wf['platforms'])}")
        print(f"    {wf['description']}")

    print(f"{'=' * 60}")
