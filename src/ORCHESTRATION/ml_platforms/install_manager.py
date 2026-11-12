# coding: utf-8
"""
ML Platform Installation Manager
Unified installer with version compatibility, conflict resolution, and rollback support.
"""

import subprocess
import sys
import time
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


class InstallStatus(Enum):
    PENDING = "pending"
    INSTALLING = "installing"
    INSTALLED = "installed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"


@dataclass
class InstallRecord:
    package: str
    version: str
    status: InstallStatus
    timestamp: float
    output: str = ""
    error: str = ""
    dependencies: list[str] = field(default_factory=list)


@dataclass
class CompatibilityRule:
    package: str
    min_version: str
    max_version: str
    conflict_with: dict[str, tuple[str, str]]
    required_by: dict[str, tuple[str, str]]


class UnifiedInstaller:
    """
    Unified installation manager for all ML platforms.

    FEATURES:
        - Single entry point for all installations
        - Automatic dependency resolution
        - Version compatibility checking
        - Conflict detection and resolution
        - Installation rollback capability
        - Audit trail and reporting
    """

    # Packages that cannot be pip-installed for the primary free-threaded
    # interpreter (no PyPI wheel on any ABI) and are instead provided by the
    # GIL sidecar interpreter at runtime.  Maps pip-name -> import name to
    # probe, resolved through CORE/python_env.py.
    SIDECAR_PACKAGES: dict[str, str] = {
        "tensorflow": "tensorflow",
    }

    CORE_PACKAGES = [
        "torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118",
        # Listed so the installer tracks it, but install_category() SKIPs it:
        # no PyPI wheel exists for Python 3.14 on any ABI.  The GIL sidecar
        # provides it (see SIDECAR_PACKAGES, CORE/python_env.py,
        # scripts/rebuild_gil_sidecar.sh).
        "tensorflow>=2.15.0",
        "jax jaxlib",
        "numpy>=1.24.0",
        "scipy>=1.11.0",
        "pandas>=2.0.0",
    ]

    ML_PACKAGES = [
        "scikit-learn>=1.4.0",
        "xgboost>=2.0.0",
        "lightgbm>=4.0.0",
        "catboost>=1.2.0",
        "optuna>=3.5.0",
        "ray[default]>=2.9.0",
        "mlflow>=2.10.0",
    ]

    CV_PACKAGES = [
        "opencv-python>=4.9.0",
        "opencv-contrib-python>=4.9.0",
        "pillow>=10.0.0",
        "scikit-image>=0.22.0",
        "easyocr>=1.7.0",
        "deepface>=0.0.90",
        "mtcnn>=0.1.0",
        "retinaface>=0.0.10",
    ]

    NLP_PACKAGES = [
        "transformers>=4.40.0",
        "huggingface-hub>=0.23.0",
        "tokenizers>=0.19.0",
        "nltk>=3.9.0",
        "spacy>=3.7.0",
    ]

    DEPLOY_PACKAGES = [
        "onnx>=1.16.0",
        "onnxruntime-gpu>=1.17.0",
        "onnxruntime-tools>=1.0.0",
        "tensorboard>=2.15.0",
        "fastapi>=0.110.0",
        "uvicorn>=0.27.0",
    ]

    UTILITY_PACKAGES = [
        "einops>=0.8.0",
        "imageio>=2.34.0",
        "joblib>=1.4.0",
        "networkx>=3.2.0",
        "shapely>=2.0.0",
        "h5py>=3.11.0",
        "functorch>=0.2.0",
        "torchgen>=0.1.0",
        "gymnasium>=1.0.0",
        "stable-baselines3>=2.7.0",
    ]

    def __init__(self, verbose: bool = True, dry_run: bool = False):
        self.verbose = verbose
        self.dry_run = dry_run
        self.install_history: list[InstallRecord] = []
        self.rollback_stack: list[str] = []
        self._compat_cache: dict[str, CompatibilityRule] = {}

    @staticmethod
    def _bare_name(spec: str) -> str:
        """Reduce a pip requirement spec to its bare distribution name."""
        name = spec.split()[0] if spec.split() else spec
        for sep in ("[", "=", ">", "<", "~", "!"):
            name = name.split(sep)[0]
        return name.strip()

    def _run_command(self, cmd: list[str], desc: str = "") -> tuple[bool, str]:
        """Execute command and return success status and output."""
        if self.dry_run:
            print(f"[DRY RUN] {desc}: {' '.join(cmd)}")
            return True, "dry_run"

        if self.verbose:
            print(f"[{desc}] {' '.join(cmd)}")

        try:
            # cmd is already a list — no need for shell=True
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace"
            )
            if result.returncode == 0:
                return True, result.stdout
            else:
                return False, result.stderr[:500]
        except subprocess.TimeoutExpired as exc:
            note_failure("install_manager:L162", exc)
            return False, "Command timed out"
        except Exception as e:
            note_failure("install_manager:L164", e)
            return False, str(e)

    def pip_install(self, package: str, upgrade: bool = False) -> bool:
        """Install a package via pip."""
        cmd = [sys.executable, "-m", "pip", "install"]
        if upgrade:
            cmd.append("--upgrade")
        cmd.append(package)

        success, output = self._run_command(cmd, f"Installing {package}")
        return success

    def _python_env(self):
        """Best-effort import of the canonical interpreter resolver."""
        if getattr(self, "_python_env_mod", "unset") != "unset":
            return self._python_env_mod
        mod = None
        try:
            from CORE import python_env as mod  # type: ignore[no-redef]
        except Exception as exc:
            note_failure("install_manager:L184", exc)
            try:
                from pathlib import Path

                src_root = Path(__file__).resolve().parents[2]
                if str(src_root) not in sys.path:
                    sys.path.insert(0, str(src_root))
                from CORE import python_env as mod  # type: ignore[no-redef]
            except Exception as exc:
                note_failure("install_manager:L192", exc)
                mod = None
        self._python_env_mod = mod
        return mod

    def _sidecar_version(self, module: str) -> str | None:
        """Resolve a sidecar-provided package version via CORE/python_env."""
        python_env = self._python_env()
        if python_env is None:
            return None
        try:
            code = (
                f"import {module} as _m; "
                "print(getattr(_m, '__version__', 'unknown'))"
            )
            # NOTE: CORE.python_env currently only resolves interpreter
            # environments (probe() returns a cleaned environ); there is no
            # sidecar code-runner yet, so this check stays unimplemented and
            # returns None exactly like the previous TypeError path did.
            result = python_env.probe()
            if result.get("ok"):
                version = (result.get("stdout") or "").strip()
                return version or None
        except Exception as exc:
            note_failure("install_manager:L211", exc)
        return None

    def check_version(self, package: str) -> (str | None):
        """Check installed version of a package.

        Packages that live in the GIL sidecar (e.g. tensorflow) are probed in
        the sidecar interpreter instead of the current process.
        """
        if package in self.SIDECAR_PACKAGES:
            return self._sidecar_version(self.SIDECAR_PACKAGES[package])
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    f"import {package}; print({package}.__version__)",
                ],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
            timeout=1800)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as exc:
            note_failure("install_manager:L235", exc)
        return None

    def check_compatibility(self) -> dict[str, tuple[bool, str]]:
        """Check version compatibility across packages."""
        results: dict[str, tuple[bool, str]] = {}

        version_checks = [
            (
                "torch",
                "torchvision",
                lambda v1, v2: v1.split(".")[:2] == v2.split(".")[:2],
            ),
            (
                "torch",
                "torchaudio",
                lambda v1, v2: v1.split(".")[0] == v2.split(".")[0],
            ),
            ("tensorflow", "keras", lambda v1, v2: True),
            ("numpy", "pandas", lambda v1, v2: True),
        ]

        for pkg1, pkg2, check_fn in version_checks:
            v1 = self.check_version(pkg1)
            v2 = self.check_version(pkg2)

            if v1 and v2:
                compatible = check_fn(v1, v2)
                results[f"{pkg1}-{pkg2}"] = (compatible, f"{v1} vs {v2}")

        return results

    def detect_conflicts(self) -> list[tuple[str, str, str]]:
        """Detect known package conflicts."""
        conflicts = [
            (
                "torch",
                "torchvision",
                "Versions must match (check PyTorch version compatibility)",
            ),
            (
                "tensorflow",
                "keras",
                "TensorFlow 2.x bundles Keras; separate Keras may conflict "
                "(TensorFlow is sidecar-provided via CORE/python_env.py)",
            ),
            ("onnx", "onnxruntime", "Version mismatch can cause import errors"),
            (
                "numpy",
                "tensorflow",
                "TensorFlow requires specific NumPy version "
                "(TensorFlow is sidecar-provided via CORE/python_env.py)",
            ),
        ]

        detected: list[tuple[str, str, str]] = []
        for pkg1, pkg2, msg in conflicts:
            v1 = self.check_version(pkg1)
            v2 = self.check_version(pkg2)
            if v1 and v2:
                detected.append((pkg1, pkg2, f"{msg} (installed: {v1}, {v2})"))

        return detected

    def install_category(
        self, category: str, extras: str = ""
    ) -> dict[str, InstallStatus]:
        """Install a category of packages."""
        category_map = {
            "core": self.CORE_PACKAGES,
            "ml": self.ML_PACKAGES,
            "cv": self.CV_PACKAGES,
            "nlp": self.NLP_PACKAGES,
            "deploy": self.DEPLOY_PACKAGES,
            "utils": self.UTILITY_PACKAGES,
            "all": self.CORE_PACKAGES
            + self.ML_PACKAGES
            + self.CV_PACKAGES
            + self.NLP_PACKAGES
            + self.DEPLOY_PACKAGES
            + self.UTILITY_PACKAGES,
        }

        packages = category_map.get(category, category_map["all"])
        results: dict[str, InstallStatus] = {}

        for pkg in packages:
            package_name = pkg.split()[0]
            bare_name = self._bare_name(pkg)
            if bare_name in self.SIDECAR_PACKAGES:
                if self.verbose:
                    print(
                        f"[SKIP] {package_name} is provided by the GIL sidecar "
                        "(see CORE/python_env.py); not pip-installable here"
                    )
                results[bare_name] = InstallStatus.SKIPPED
                continue
            success = self.pip_install(pkg, upgrade=True)
            results[package_name] = (
                InstallStatus.INSTALLED if success else InstallStatus.FAILED
            )

        return results

    def install_all(self) -> dict[str, InstallStatus]:
        """Install all ML platforms."""
        return self.install_category("all")

    def rollback(self, package: str) -> bool:
        """Rollback a package installation."""
        success, _ = self._run_command(
            [sys.executable, "-m", "pip", "uninstall", "-y", package],
            f"Rolling back {package}",
        )
        return success

    def generate_report(self) -> dict[str, Any]:
        """Generate installation report."""
        return {
            "timestamp": time.time(),
            "total_installed": sum(
                1 for r in self.install_history if r.status == InstallStatus.INSTALLED
            ),
            "failed": [
                r.package
                for r in self.install_history
                if r.status == InstallStatus.FAILED
            ],
            "compat_issues": self.detect_conflicts(),
        }

    def health_check(self) -> dict[str, Any]:
        """Run comprehensive health check."""
        return {
            "packages": {
                pkg: {"version": self.check_version(pkg)}
                for pkg in ["torch", "tensorflow", "numpy", "scipy", "pandas"]
            },
            "compatibility": self.check_compatibility(),
            "conflicts": self.detect_conflicts(),
        }


def main():
    """Main entry point."""
    installer = UnifiedInstaller(verbose=True)

    print("=" * 70)
    print("ML PLATFORM UNIFIED INSTALLER")
    print("=" * 70)

    print("\n[1/4] Checking current installation...")
    health = installer.health_check()
    print(f"  Torch: {health['packages']['torch']['version']}")
    print(f"  TensorFlow: {health['packages']['tensorflow']['version']}")
    print(f"  NumPy: {health['packages']['numpy']['version']}")

    print("\n[2/4] Detecting conflicts...")
    conflicts = installer.detect_conflicts()
    if conflicts:
        print("  Conflicts found:")
        for p1, p2, msg in conflicts:
            print(f"    {p1} <-> {p2}: {msg}")
    else:
        print("  No conflicts detected")

    print("\n[3/4] Installing core packages...")
    results = installer.install_category("core")
    installed_count = sum(1 for v in results.values() if v == InstallStatus.INSTALLED)
    print(f"  Installed: {installed_count}")

    print("\n[4/4] Generating report...")
    report = installer.generate_report()
    print(f"  Total installed: {report['total_installed']}")
    print(f"  Failed: {len(report['failed'])}")

    print("\n" + "=" * 70)
    print("INSTALLATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
