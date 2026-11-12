# coding: utf-8
"""
Installation Debug

Module for ml_platforms functionality.

Functions:
    - diagnose_and_fix()
    - check_package()
    - check_all()
    - check_cuda()
    - check_tensorboard()
    - install_package()
    - fix_tensorboard()
    - fix_cuda()
    - get_missing_packages()
    - get_critical_missing()

Classes:
    - InstallationStatus
    - PackageInfo
    - InstallationDebugger
    - CompatibilityChecker

Usage:
    >>> import installation_debug
    >>> installation_debug.diagnose_and_fix()

Author: Auto-generated
Date: 2026-02-10
"""

# coding: utf-8
"""
Installation Debugger for ML Platforms
Diagnoses and resolves installation issues.
"""

import subprocess
import sys
import importlib
from typing import Any, List
from dataclasses import dataclass, field
from enum import Enum

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class InstallationStatus(Enum):
    OK = "ok"
    MISSING = "missing"
    VERSION_CONFLICT = "version_conflict"
    IMPORT_ERROR = "import_error"
    CUDA_UNAVAILABLE = "cuda_unavailable"


HAS_TORCH = False
HAS_TF = False

try:
    pass

    HAS_TORCH = True
except ImportError as exc:
    note_failure("installation_debug:L62", exc)

try:
    import tensorflow as tf  # type: ignore[reportMissingModuleSource]

    HAS_TF = True
except ImportError as exc:
    note_failure("installation_debug:L69", exc)


@dataclass
class PackageInfo:
    name: str
    display_name: str
    critical: bool = False
    install_cmd: str = ""
    dependencies: list[str] = field(default_factory=list)


class InstallationDebugger:
    """Debug and fix ML platform installations."""

    PACKAGES = {
        "torch": PackageInfo(
            "torch", "PyTorch", True, "torch torchvision torchaudio", []
        ),
        "torchvision": PackageInfo("torchvision", "TorchVision", False, "", ["torch"]),
        "torchaudio": PackageInfo("torchaudio", "TorchAudio", False, "", ["torch"]),
        "tensorflow": PackageInfo("tensorflow", "TensorFlow", True, "tensorflow", []),
        "keras": PackageInfo("keras", "Keras", True, "keras", ["tensorflow"]),
        "numpy": PackageInfo("numpy", "NumPy", True, "numpy>=1.24", []),
        "pandas": PackageInfo("pandas", "Pandas", True, "pandas>=2.0", ["numpy"]),
        "scipy": PackageInfo("scipy", "SciPy", True, "scipy>=1.10", ["numpy"]),
        "scikit-learn": PackageInfo(
            "sklearn", "scikit-learn", True, "scikit-learn>=1.3", ["numpy", "scipy"]
        ),
        "opencv-python": PackageInfo("cv2", "OpenCV", True, "opencv-python", []),
        "pillow": PackageInfo("PIL", "Pillow", False, "pillow", []),
        "scikit-image": PackageInfo(
            "skimage", "Scikit-Image", False, "scikit-image", ["numpy", "scipy"]
        ),
        "nltk": PackageInfo("nltk", "NLTK", False, "nltk", []),
        "transformers": PackageInfo(
            "transformers", "Transformers", True, "transformers", ["numpy"]
        ),
        "huggingface_hub": PackageInfo(
            "huggingface_hub", "HuggingFace Hub", False, "huggingface-hub", []
        ),
        "easyocr": PackageInfo(
            "easyocr", "EasyOCR", False, "easyocr", ["torch", "opencv-python"]
        ),
        "einops": PackageInfo("einops", "EinOps", False, "einops", []),
        "imageio": PackageInfo("imageio", "ImageIO", False, "imageio", ["numpy"]),
        "joblib": PackageInfo("joblib", "Joblib", False, "joblib", []),
        "networkx": PackageInfo("networkx", "NetworkX", False, "networkx", []),
        "shapely": PackageInfo("shapely", "Shapely", False, "shapely", []),
        "tensorboard": PackageInfo(
            "tensorboard", "TensorBoard", False, "tensorboard", []
        ),
        "deepface": PackageInfo(
            "deepface", "DeepFace", False, "deepface", ["opencv-python", "tensorflow"]
        ),
        "mtcnn": PackageInfo("mtcnn", "MTCNN", False, "mtcnn", ["torch"]),
        "retinaface": PackageInfo(
            "retinaface", "RetinaFace", False, "retinaface", ["torch"]
        ),
        "h5py": PackageInfo("h5py", "H5PY", False, "h5py", ["numpy"]),
        "tokenizers": PackageInfo("tokenizers", "Tokenizers", False, "tokenizers", []),
        "functorch": PackageInfo(
            "functorch", "Functorch", False, "functorch", ["torch"]
        ),
        "torchgen": PackageInfo("torchgen", "TorchGen", False, "torchgen", ["torch"]),
        "mlflow": PackageInfo("mlflow", "MLflow", False, "mlflow", []),
        "ray": PackageInfo("ray", "Ray", False, "ray", []),
        "onnx": PackageInfo("onnx", "ONNX", False, "onnx", []),
        "onnxruntime": PackageInfo(
            "onnxruntime", "ONNX Runtime", True, "onnxruntime", ["onnx"]
        ),
        "optuna": PackageInfo("optuna", "Optuna", False, "optuna", []),
        "xgboost": PackageInfo(
            "xgboost", "XGBoost", False, "xgboost", ["numpy", "scipy"]
        ),
        "lightgbm": PackageInfo("lightgbm", "LightGBM", False, "lightgbm", ["numpy"]),
        "catboost": PackageInfo("catboost", "CatBoost", False, "catboost", ["numpy"]),
        "gymnasium": PackageInfo("gymnasium", "Gymnasium", False, "gymnasium", []),
        "stable_baselines3": PackageInfo(
            "stable_baselines3",
            "Stable-Baselines3",
            False,
            "stable-baselines3",
            ["gymnasium", "torch"],
        ),
    }

    def __init__(self):
        self.results: dict[str, tuple[InstallationStatus, str]] = {}
        self.cuda_info: dict[str, Any] = {}

    def check_package(self, package_name: str) -> tuple[InstallationStatus, str]:
        """Check a single package installation status."""
        pkg_info = self.PACKAGES.get(package_name)
        if pkg_info is None:
            return (InstallationStatus.MISSING, "Unknown package")

        import_name = pkg_info.name

        try:
            mod = importlib.import_module(import_name)
            version = getattr(mod, "__version__", "unknown")
            return (InstallationStatus.OK, version)
        except ImportError as e:
            note_failure("installation_debug:L173", e)
            return (InstallationStatus.IMPORT_ERROR, str(e)[:50])

    def check_all(self) -> dict[str, tuple[InstallationStatus, str]]:
        """Check all known packages."""
        self.results = {}
        for pkg_name in self.PACKAGES:
            self.results[pkg_name] = self.check_package(pkg_name)
        return self.results

    def check_cuda(self) -> dict[str, Any]:
        """Check CUDA availability and configuration."""
        self.cuda_info = {
            "cuda_available": False,
            "cudnn_available": False,
            "device_count": 0,
            "device_name": "N/A",
            "cuda_version": "N/A",
            "driver_version": "N/A",
        }

        if HAS_TORCH:
            import torch

            self.cuda_info["cuda_available"] = torch.cuda.is_available()
            if self.cuda_info["cuda_available"]:
                self.cuda_info["device_count"] = torch.cuda.device_count()
                self.cuda_info["device_name"] = (
                    torch.cuda.get_device_name(0)
                    if self.cuda_info["device_count"] > 0
                    else "N/A"
                )
                self.cuda_info["cuda_version"] = (
                    torch.version.cuda if hasattr(torch.version, "cuda") else "N/A"
                )

        if HAS_TF:
            self.cuda_info["cudnn_available"] = (
                len(tf.config.list_physical_devices("GPU")) > 0
            )

        return self.cuda_info

    def check_tensorboard(self) -> tuple[InstallationStatus, str]:
        """Check TensorBoard installation."""
        try:
            import tensorboard

            return (InstallationStatus.OK, tensorboard.__version__)
        except ImportError as exc:
            note_failure("installation_debug:L222", exc)
            return (InstallationStatus.MISSING, "TensorBoard not installed")

    def install_package(self, package_name: str, upgrade: bool = False) -> bool:
        """Install a package using pip."""
        cmd = [sys.executable, "-m", "pip", "install"]
        if upgrade:
            cmd.append("--upgrade")
        cmd.append(package_name)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
            return result.returncode == 0
        except subprocess.TimeoutExpired as exc:
            note_failure("installation_debug:L235", exc)
            return False

    def fix_tensorboard(self) -> bool:
        """Fix TensorBoard installation issues."""
        return self.install_package("tensorboard", upgrade=True)

    def fix_cuda(self) -> dict[str, str]:
        """Attempt to fix CUDA issues."""
        results = {}

        if not HAS_TORCH:
            results["pytorch"] = (
                "Run: pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118"
            )

        if not HAS_TF:
            results["tensorflow"] = (
                "Install GPU version: pip install tensorflow[and-cuda]"
            )

        return results

    def get_missing_packages(self) -> list[str]:
        """Get list of missing packages."""
        return [
            pkg
            for pkg, (status, _) in self.results.items()
            if status != InstallationStatus.OK
        ]

    def get_critical_missing(self) -> list[str]:
        """Get list of missing critical packages."""
        return [
            pkg
            for pkg in self.PACKAGES
            if self.PACKAGES[pkg].critical
            and pkg in self.results
            and self.results[pkg][0] != InstallationStatus.OK
        ]

    def print_status(self):
        """Print formatted installation status."""
        print("=" * 70)
        print("ML PLATFORM INSTALLATION STATUS")
        print("=" * 70)

        available = [
            (p, v) for p, (s, v) in self.results.items() if s == InstallationStatus.OK
        ]
        unavailable = [
            (p, v) for p, (s, v) in self.results.items() if s != InstallationStatus.OK
        ]

        print(f"\nAvailable ({len(available)}):")
        for pkg, version in sorted(available):
            print(f"  [OK] {pkg:<25} v{version}")

        print(f"\nUnavailable ({len(unavailable)}):")
        for pkg, msg in sorted(unavailable):
            print(f"  [--] {pkg:<25} {str(msg)[:40]}")

        print("\n" + "=" * 70)
        print(f"CRITICAL MISSING: {len(self.get_critical_missing())}")
        print("=" * 70)


class CompatibilityChecker:
    """Check package compatibility issues."""

    def check_conflicts(self) -> list[tuple[str, str, str]]:
        """Check for known package conflicts."""
        conflicts = [
            ("torch", "torchvision", "torchvision version must match torch"),
            ("tensorflow", "keras", "TensorFlow 2.x includes Keras"),
            ("numpy", "pandas", "Upgrade numpy first: pip install numpy>=1.24"),
        ]

        detected = []
        for pkg1, pkg2, message in conflicts:
            try:
                importlib.import_module(pkg1)
                try:
                    importlib.import_module(pkg2)
                except ImportError as exc:
                    note_failure("installation_debug:L319", exc)
                    detected.append((pkg1, pkg2, message))
            except ImportError as exc:
                note_failure("installation_debug:L321", exc)
        return detected


def diagnose_and_fix():
    """Run full diagnosis and attempt fixes."""
    debugger = InstallationDebugger()

    print("Running installation diagnostics...\n")

    print("Checking packages...")
    debugger.check_all()
    debugger.print_status()

    print("\nChecking CUDA...")
    cuda_info = debugger.check_cuda()
    print(f"  CUDA Available: {cuda_info['cuda_available']}")
    if cuda_info["cuda_available"]:
        print(f"  GPU Count: {cuda_info['device_count']}")
        print(f"  GPU Name: {cuda_info['device_name']}")

    print("\nChecking TensorBoard...")
    tb_status, tb_msg = debugger.check_tensorboard()
    print(f"  Status: {tb_status.value} ({tb_msg})")

    missing = debugger.get_critical_missing()
    if missing:
        print(f"\nCritical missing packages: {', '.join(missing)}")
        install_cmds = [
            debugger.PACKAGES[pkg].install_cmd
            for pkg in missing
            if debugger.PACKAGES[pkg].install_cmd
        ]
        if install_cmds:
            print("Run: pip install " + " ".join(install_cmds))


if __name__ == "__main__":
    diagnose_and_fix()
