#!/usr/bin/env python3
"""
AZAZAEL Auto-Downloader Module
================================
Persistent, robust auto-downloader for ML frameworks with extendable characteristics.

Features:
    - Persistent state management (survives restarts)
    - Robust error handling with retries and recovery
    - Plugin architecture for extendable frameworks
    - Framework detection and auto-installation
    - Health verification after install
    - Cross-platform support (Windows/Linux/WSL)
    - Progress tracking and resume capability

    Usage:
    >>> from AUTO_DOWNLOADER import AutoDownloader
    >>> downloader = AutoDownloader()
    >>> downloader.detect_missing()
    >>> downloader.auto_install_all()
    >>> downloader.verify_installations()
"""

import json
import time
import logging
import subprocess
import threading
import signal
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, List
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

VERSION = "1.0.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.FileHandler("AUTO_DOWNLOADER.log"), logging.StreamHandler()],
)
logger = logging.getLogger("AUTO_DOWNLOADER")


class DownloadState(Enum):
    PENDING = "pending"


    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    INSTALLING = "installing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    PAUSED = "paused"


@dataclass
class FrameworkInfo:
    name: str


    pip_name: str
    conda_name: (str | None) = None
    min_version: str = "latest"
    max_version: str = "latest"
    python_versions: list[str] = field(
        default_factory=lambda: ["3.12", "3.13", "3.14"]
    )
    os_support: list[str] = field(default_factory=lambda: ["windows", "linux", "darwin"])
    dependencies: list[str] = field(default_factory=list)
    category: str = "ml"
    priority: int = 1
    description: str = ""


@dataclass
class DownloadProgress:
    framework: str


    state: DownloadState
    bytes_downloaded: int = 0
    total_bytes: int = 0
    percent_complete: float = 0.0
    speed_bytes_per_sec: float = 0.0
    eta_seconds: float = 0.0
    attempts: int = 0
    max_attempts: int = 3
    error_message: (str | None) = None


@dataclass
class InstallationResult:
    framework: str


    success: bool
    version_installed: (str | None) = None
    install_time_seconds: float = 0.0
    output: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


class PersistentState:
    STATE_FILE = "AUTO_DOWNLOADER_state.json"

    def __init__(self, state_dir: str | Path = "."):
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / self.STATE_FILE
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self._load_state()

    def _load_state(self) -> dict:
        defaults = {
            "version": VERSION,
            "last_updated": datetime.now().isoformat(),
            "frameworks": {},
            "completed_installations": [],
            "failed_installations": [],
            "statistics": {
                "total_downloads": 0,
                "successful_downloads": 0,
                "failed_downloads": 0,
                "total_bytes_downloaded": 0,
                "total_time_seconds": 0.0,
            },
            "settings": {
                "max_concurrent_downloads": 3,
                "retry_attempts": 3,
                "retry_delay_seconds": 5,
                "timeout_seconds": 300,
                "verify_after_install": True,
            },
        }
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    defaults.update(loaded)
                    logger.info(f"Loaded state from {self.state_file}")
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")
        return defaults


    def save_state(self):
        with self._lock:
            self._state["last_updated"] = datetime.now().isoformat()
            try:
                with open(self.state_file.with_suffix(".tmp"), "w", encoding="utf-8") as f:
                    json.dump(self._state, f, indent=2, default=str)
                self.state_file.with_suffix(".tmp").replace(self.state_file)
            except Exception as e:
                logger.error(f"Failed to save state: {e}")


    def update_framework_state(self, name: str, state: dict):
        self._state["frameworks"][name] = state
        self.save_state()


    def add_completed_installation(self, result: InstallationResult):
        self._state["completed_installations"].append(
            {
                "framework": result.framework,
                "version": result.version_installed,
                "timestamp": result.timestamp.isoformat(),
                "install_time": result.install_time_seconds,
            }
        )
        self._state["statistics"]["total_downloads"] += 1
        self._state["statistics"]["successful_downloads"] += 1
        self.save_state()


    def add_failed_installation(self, framework: str, error: str):
        self._state["failed_installations"].append(
            {
                "framework": framework,
                "error": error,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self._state["statistics"]["total_downloads"] += 1
        self._state["statistics"]["failed_downloads"] += 1
        self.save_state()


    def get_statistics(self) -> dict:
        return self._state["statistics"]


    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)


class FrameworkPlugin(ABC):
    def __init__(self, info: FrameworkInfo):
        self.info = info
        self.progress = DownloadProgress(
            framework=info.name, state=DownloadState.PENDING
        )

    @abstractmethod
    def get_install_command(self, version: str = "latest") -> list[str]:
        pass


    @abstractmethod
    def get_verify_command(self) -> list[str]:
        pass


    @abstractmethod
    def get_version(self) -> (str | None):
        pass


    @abstractmethod
    def is_installed(self) -> bool:
        pass


class PIPFrameworkPlugin(FrameworkPlugin):
    def get_install_command(self, version: str = "latest") -> list[str]:
        if version == "latest":
            return ["pip", "install", "--upgrade", self.info.pip_name]
        return ["pip", "install", f"{self.info.pip_name}=={version}"]


    def get_verify_command(self) -> list[str]:
        return ["pip", "show", self.info.pip_name]


    def get_version(self) -> (str | None):
        try:
            result = subprocess.run(
                self.get_verify_command(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
            )
            for line in result.stdout.split("\n"):
                if line.startswith("Version:"):
                    return line.split(":")[1].strip()
        except Exception as exc:
            note_failure("AUTO_DOWNLOADER:L249", exc)
        return None


    def is_installed(self) -> bool:
        try:
            subprocess.run(self.get_verify_command(), capture_output=True, timeout=10)
            return True
        except Exception as exc:
            note_failure("AUTO_DOWNLOADER:L258", exc)
            return False


class FrameworkRegistry:
    def __init__(self):
        self._plugins: dict[str, FrameworkInfo] = {}
        self._instances: dict[str, FrameworkPlugin] = {}
        self._register_default_frameworks()


    def _register_default_frameworks(self):
        frameworks = [
            FrameworkInfo(
                name="pytorch",
                pip_name="torch",
                category="deep_learning",
                priority=10,
                description="PyTorch deep learning",
            ),
            FrameworkInfo(
                name="tensorflow",
                pip_name="tensorflow",
                category="deep_learning",
                priority=10,
                description="TensorFlow ML",
            ),
            FrameworkInfo(
                name="jax",
                pip_name="jax",
                category="deep_learning",
                priority=8,
                description="JAX ML research",
            ),
            FrameworkInfo(
                name="sklearn",
                pip_name="scikit-learn",
                category="machine_learning",
                priority=5,
                description="Scikit-learn",
            ),
            FrameworkInfo(
                name="ray",
                pip_name="ray",
                category="distributed",
                priority=7,
                description="Ray distributed",
            ),
            FrameworkInfo(
                name="mlflow",
                pip_name="mlflow",
                category="mlops",
                priority=6,
                description="MLflow tracking",
            ),
            FrameworkInfo(
                name="optuna",
                pip_name="optuna",
                category="optimization",
                priority=5,
                description="Optuna optimization",
            ),
            FrameworkInfo(
                name="xgboost",
                pip_name="xgboost",
                category="machine_learning",
                priority=5,
                description="XGBoost",
            ),
            FrameworkInfo(
                name="lightgbm",
                pip_name="lightgbm",
                category="machine_learning",
                priority=5,
                description="LightGBM",
            ),
            FrameworkInfo(
                name="catboost",
                pip_name="catboost",
                category="machine_learning",
                priority=4,
                description="CatBoost",
            ),
            FrameworkInfo(
                name="transformers",
                pip_name="transformers",
                category="nlp",
                priority=9,
                description="HuggingFace Transformers",
            ),
            FrameworkInfo(
                name="spacy",
                pip_name="spacy",
                category="nlp",
                priority=4,
                description="spaCy NLP",
            ),
            FrameworkInfo(
                name="opencv",
                pip_name="opencv-python",
                category="computer_vision",
                priority=6,
                description="OpenCV",
            ),
            FrameworkInfo(
                name="onnx",
                pip_name="onnx",
                category="interoperability",
                priority=5,
                description="ONNX",
            ),
            FrameworkInfo(
                name="shap",
                pip_name="shap",
                category="explainability",
                priority=4,
                description="SHAP",
            ),
            FrameworkInfo(
                name="wandb",
                pip_name="wandb",
                category="mlops",
                priority=5,
                description="Weights & Biases",
            ),
            FrameworkInfo(
                name="fastapi",
                pip_name="fastapi",
                category="deployment",
                priority=4,
                description="FastAPI",
            ),
            FrameworkInfo(
                name="faiss",
                pip_name="faiss-cpu",
                category="search",
                priority=4,
                description="FAISS",
            ),
        ]
        for info in frameworks:
            self.register(info)


    def register(self, info: FrameworkInfo):
        self._plugins[info.name] = info
        logger.info(f"Registered framework: {info.name}")


    def get_plugin(self, name: str) -> (FrameworkPlugin | None):
        if name not in self._instances:
            if name in self._plugins:
                self._instances[name] = PIPFrameworkPlugin(self._plugins[name])
        return self._instances.get(name)


    def get_all_plugins(self) -> list[FrameworkPlugin]:
        return [
            p
            for name in self._plugins.keys()
            if (p := self.get_plugin(name)) is not None
        ]


    def get_category(self, category: str) -> list[FrameworkPlugin]:
        return [
            p
            for name, info in self._plugins.items()
            if info.category == category
            and (p := self.get_plugin(name)) is not None
        ]


    def get_by_priority(self, descending: bool = True) -> list[FrameworkPlugin]:
        sorted_names = sorted(
            self._plugins.keys(),
            key=lambda n: self._plugins[n].priority,
            reverse=descending,
        )
        return [
            p
            for name in sorted_names
            if (p := self.get_plugin(name)) is not None
        ]


class AutoDownloader:
    def __init__(self, state_dir: str | None = None):
        self.state_dir = Path(state_dir) if state_dir else Path(__file__).parent
        self.state = PersistentState(self.state_dir)
        self.registry = FrameworkRegistry()
        self._callbacks: dict[str, list[Callable]] = {
            k: [] for k in ["on_start", "on_complete", "on_error", "on_progress"]
        }
        self._setup_signal_handlers()


    def _setup_signal_handlers(self):
        try:
            signal.signal(signal.SIGINT, lambda s, f: self._cleanup())
            signal.signal(signal.SIGTERM, lambda s, f: self._cleanup())
        except Exception as e:
            logger.warning(f"Could not setup signal handlers: {e}")


    def _cleanup(self):
        logger.info("Cleaning up...")
        self.state.save_state()


    def register_callback(self, event: str, callback: Callable):
        if event in self._callbacks:
            self._callbacks[event].append(callback)


    def _emit(self, event: str, *args, **kwargs):
        for cb in self._callbacks.get(event, []):
            try:
                cb(*args, **kwargs)
            except Exception as e:
                logger.error(f"Callback error: {e}")


    def detect_missing(self, categories: list[str] | None = None) -> list[FrameworkPlugin]:
        missing = []
        for plugin in self.registry.get_all_plugins():
            if categories and plugin.info.category not in categories:
                continue
            if not plugin.is_installed():
                missing.append(plugin)
                logger.info(f"Missing: {plugin.info.name}")
        return missing


    def _install_with_retry(
        self, plugin: FrameworkPlugin, max_retries: int = 3
    ) -> InstallationResult:
        settings = self.state.get("settings", {})
        retry_attempts = settings.get("retry_attempts", max_retries)
        retry_delay = settings.get("retry_delay_seconds", 5)

        for attempt in range(retry_attempts):
            self._emit("on_start", plugin.info.name)
            try:
                command = plugin.get_install_command()
                start_time = time.time()

                result = subprocess.run(
                    command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600
                )

                install_time = time.time() - start_time

                if result.returncode == 0:
                    version = plugin.get_version()
                    res = InstallationResult(
                        framework=plugin.info.name,
                        success=True,
                        version_installed=version,
                        install_time_seconds=install_time,
                        output=result.stdout,
                    )
                    self.state.add_completed_installation(res)
                    self._emit("on_complete", res)
                    return res
                else:
                    error_msg = result.stderr
                    logger.warning(f"Attempt {attempt + 1} failed: {error_msg}")
                    if attempt < retry_attempts - 1:
                        time.sleep(retry_delay)

            except Exception as e:
                logger.error(f"Install error: {e}")
                if attempt < retry_attempts - 1:
                    time.sleep(retry_delay)

        error_msg = f"Failed after {retry_attempts} attempts"
        res = InstallationResult(
            framework=plugin.info.name, success=False, errors=[error_msg]
        )
        self.state.add_failed_installation(plugin.info.name, error_msg)
        self._emit("on_error", plugin.info.name, error_msg)
        return res


    def install(self, frameworks: list[str]) -> dict[str, InstallationResult]:
        results = {}
        for name in frameworks:
            plugin = self.registry.get_plugin(name)
            if plugin:
                results[name] = self._install_with_retry(plugin)
        self.state.save_state()
        return results


    def auto_install_all(
        self, categories: list[str] | None = None
    ) -> dict[str, InstallationResult]:
        missing = self.detect_missing(categories)
        results = {}
        for plugin in missing:
            results[plugin.info.name] = self._install_with_retry(plugin)
        self.state.save_state()
        return results


    def verify_installations(self, frameworks: list[str] | None = None) -> dict[str, bool]:
        if frameworks is None:
            frameworks = list(self.registry._plugins.keys())
        results = {}
        for name in frameworks:
            plugin = self.registry.get_plugin(name)
            if plugin:
                installed = plugin.is_installed()
                version = plugin.get_version() if installed else None
                results[name] = installed
                status = "[OK]" if installed else "[X]"
                logger.info(f"{status} {name}: {version or 'not installed'}")
        return results


    def get_status(self) -> dict:
        stats = self.state.get_statistics()
        installed = sum(1 for p in self.registry.get_all_plugins() if p.is_installed())
        return {
            "statistics": stats,
            "frameworks_registered": len(self.registry._plugins),
            "frameworks_installed": installed,
            "frameworks_missing": len(self.registry._plugins) - installed,
        }


    def get_statistics(self) -> dict:
        return self.state.get_statistics()


    def reset_state(self):
        self.state.save_state()
        backup = self.state.state_file.with_suffix(".backup")
        if self.state.state_file.exists():
            self.state.state_file.replace(backup)
        self.state._state = {}
        self.state.save_state()
        logger.info("State reset complete")


    def create_report(self) -> str:
        stats = self.state.get_statistics()
        status = self.get_status()
        installed = [
            p.info.name for p in self.registry.get_all_plugins() if p.is_installed()
        ]
        failed = self.state._state.get("failed_installations", [])[-10:]

        report = f"""
    ================================================================================
    AUTO-DOWNLOADER REPORT
    ================================================================================
    Generated: {datetime.now().isoformat()}

    STATISTICS
    ----------
    Total Downloads: {stats["total_downloads"]}
    Successful: {stats["successful_downloads"]}
    Failed: {stats["failed_downloads"]}
    Total Time: {stats["total_time_seconds"]:.2f}s

    STATUS
    ------
    Registered: {status["frameworks_registered"]}
    Installed: {status["frameworks_installed"]}
    Missing: {status["frameworks_missing"]}

    INSTALLED FRAMEWORKS
    --------------------
    """
        for name in sorted(installed):
            report += f"  {name}\n"

        if failed:
            report += "\nRECENT FAILURES\n---------------\n"
            for f in failed:
                report += f"  {f['framework']}: {f['error'][:60]}...\n"

        return report


def main():
    """CLI entry point for AUTO_DOWNLOADER."""
    parser = argparse.ArgumentParser(description="AZAZAEL Auto-Downloader")
    parser.add_argument(
        "--detect", action="store_true", help="Detect missing frameworks"
    )
    parser.add_argument("--install", nargs="+", help="Install specific frameworks")
    parser.add_argument("--category", help="Install by category")
    parser.add_argument("--auto", action="store_true", help="Auto-install missing")
    parser.add_argument("--verify", action="store_true", help="Verify installations")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--report", action="store_true", help="Generate report")
    parser.add_argument("--reset", action="store_true", help="Reset state")
    args = parser.parse_args()

    downloader = AutoDownloader()

    if args.reset:
        downloader.reset_state()
        print("State reset complete.")
        return

    if args.status:
        status = downloader.get_status()
        print(f"Registered: {status['frameworks_registered']}")
        print(f"Installed: {status['frameworks_installed']}")
        print(f"Missing: {status['frameworks_missing']}")
        return

    if args.report:
        print(downloader.create_report())
        return

    if args.verify:
        results = downloader.verify_installations()
        for name, installed in results.items():
            status = "[OK]" if installed else "[X]"
            print(f"{status} {name}")
        return

    if args.detect:
        missing = downloader.detect_missing()
        print(f"Missing frameworks ({len(missing)}):")
        for plugin in missing:
            print(f"  - {plugin.info.name} ({plugin.info.category})")
        return

    if args.auto:
        missing = downloader.detect_missing()
        print(f"Auto-installing {len(missing)} missing frameworks...")
        results = downloader.auto_install_all()
        for name, result in results.items():
            status = "[OK]" if result.success else "[X]"
            print(f"{status} {name}: {result.version_installed or result.errors}")
        return

    if args.install:
        print(f"Installing {len(args.install)} frameworks...")
        results = downloader.install(args.install)
        for name, result in results.items():
            status = "[OK]" if result.success else "[X]"
            print(f"{status} {name}: {result.version_installed or result.errors}")
        return

    if args.category:
        print(f"Installing frameworks in category: {args.category}")
        results = downloader.auto_install_all(categories=[args.category])
        for name, result in results.items():
            status = "[OK]" if result.success else "[X]"
            print(f"{status} {name}: {result.version_installed or result.errors}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
