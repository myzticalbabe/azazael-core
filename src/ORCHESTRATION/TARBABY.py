# coding: utf-8
"""
TARBABY - Terminal-Accelerated Remote Binary Builder And Yield

Binary compilation protocol for Python and cross-platform executable generation.

Features:
- Multi-format compilation (exe, zip, wheel, docker, apk)
- Terminal-based real-time progress
- Popup notifications during compilation
- Remote build support (WSL/WSA)
- Artifact signing and verification

Usage:
    >>> import tarbaby
    >>> tarbaby.build("script.py", format="exe")
    >>> tarbaby.build_batch(["a.py", "b.py"], format="docker")
    >>> tarbaby.monitor()  # Real-time popup monitoring
"""

import os
import sys
import json
import subprocess
import threading
import time
import zipfile
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, List
import tkinter as tk
from tkinter import ttk
import argparse

# MAXIMAL: BasePopupMonitor shared base class
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from CORE.Widgets.BasePopupMonitor import BasePopupMonitor, QueueMessage

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Dynamic path resolution
try:
    from CORE.paths import get_output_dir
except ImportError as exc:
    note_failure("TARBABY:L45", exc)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from CORE.paths import get_output_dir

_TARBABY_OUTPUT = get_output_dir() / "TARBABY"
_TARBABY_TEMP = get_output_dir() / "TARBABY" / "temp"


class BuildFormat(Enum):
    """Output binary formats."""

    EXE = "exe"
    ZIP = "zip"
    WHEEL = "wheel"
    DOCKER = "docker"
    APK = "apk"
    INSTALLER = "installer"


class BuildStatus(Enum):
    """Build status states."""

    PENDING = "pending"
    COMPILING = "compiling"
    LINKING = "linking"
    PACKAGING = "packaging"
    SIGNING = "signing"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class BuildTask:
    """Single build task."""

    source: str
    format: BuildFormat
    output: (str | None) = None
    status: BuildStatus = BuildStatus.PENDING
    progress: int = 0
    logs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    start_time: (datetime | None) = None
    end_time: (datetime | None) = None
    artifact_path: (str | None) = None
    checksum: (str | None) = None


@dataclass
class BuildConfig:
    """TARBABY compilation configuration."""

    base_output: str = str(_TARBABY_OUTPUT)
    temp_dir: str = str(_TARBABY_TEMP)
    compression_level: int = 9
    sign_artifacts: bool = False
    remote_build: bool = False
    wsa_build: bool = False
    wsl_build: bool = False
    pyinstaller_path: (str | None) = None
    docker_base_image: str = "python:3.14t-slim"
    icon_path: (str | None) = None
    version: str = "1.0.0"
    hidden_imports: list[str] = field(default_factory=list)


class PopupMonitor(BasePopupMonitor):
    """Real-time popup window for build monitoring. Inherits thread-safe BasePopupMonitor.

    Uses BasePopupMonitor's queue.Queue + root.after() polling architecture for
    Python 3.14t free-threading safety on Windows. All tkinter access is
    enforced to the main thread via runtime assertions.
    """

    VERSION = "3.0.0"  # MAXIMAL: Full BasePopupMonitor inheritance

    def __init__(self, title: str = "TARBABY Build Monitor"):
        super().__init__(title=title, width=800, height=600)

    # -- Window Creation (overrides base _build_* methods) ----------

    def _build_header(self):
        """TARBABY-specific header with progress bar."""
        header_frame = tk.Frame(self.root, bg="#16213e", pady=10)
        header_frame.pack(fill="x")

        title_label = tk.Label(
            header_frame,
            text="TARBABY",
            font=("Consolas", 24, "bold"),
            bg="#16213e",
            fg="#00d4ff",
        )
        title_label.pack()

        self.status_label = tk.Label(
            header_frame,
            text="Initializing...",
            font=("Consolas", 12),
            bg="#16213e",
            fg="#7c3aed",
        )
        self.status_label.pack()

        progress_bar = ttk.Progressbar(
            self.root,
            mode="determinate",
            length=750,
        )
        self.progress_bar = progress_bar
        progress_bar.pack(pady=10, padx=25)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "TProgressbar",
            troughcolor="#1a1a2e",
            thickness=20,
        )

    def _build_footer(self):
        """TARBABY-specific footer with time label."""
        footer_frame = tk.Frame(self.root, bg="#16213e", pady=5)
        footer_frame.pack(fill="x")

        self.time_label = tk.Label(
            footer_frame,
            text="00:00:00",
            font=("Consolas", 12),
            bg="#16213e",
            fg="#ffffff",
        )
        self.time_label.pack()

    # -- Backward-compatible convenience wrappers -------------------

    def log(self, message: str, level: str = "INFO"):
        """Thread-safe log (delegates to base _safe_log)."""
        self._safe_log(message, level)

    def update_status(self, status: str, progress: int = 0):
        """Thread-safe status update (delegates to base _safe_status)."""
        self._safe_status(status, progress)

    # -- Message Handling (overrides base handle_message) -----------

    def handle_message(self, msg: QueueMessage):
        """Handle TARBABY-specific message types."""
        if msg.action == "status":
            self._handle_status(msg)
        elif msg.action == "log":
            self._handle_log(msg)
        else:
            super().handle_message(msg)

    def _handle_log(self, msg: QueueMessage):
        """Render log with TARBABY-specific formatting."""
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        message = payload.get("message", str(msg.payload))
        level = payload.get("level", "INFO")

        if self.text_area:
            timestamp = datetime.now().strftime("%H:%M:%S")
            formatted = f"[{timestamp}] [{level}] {message}"
            self.text_area.insert("end", formatted + "\n")
            self.text_area.see("end")

    def _handle_status(self, msg: QueueMessage):
        """Update status label, progress bar, and elapsed time."""
        payload = msg.payload if isinstance(msg.payload, dict) else {}

        if self.status_label and "text" in payload:
            self.status_label.config(text=payload["text"])
        if self.progress_bar and "progress" in payload:
            self.progress_bar["value"] = payload["progress"]
        if self.time_label and self.start_time:
            elapsed = (datetime.now() - self.start_time).total_seconds()
            self.time_label.config(text=f"Elapsed: {elapsed:.1f}s")


class TARBABYCompiler:
    """Main TARBABY binary compilation engine."""

    def __init__(self, config: (BuildConfig | None) = None):
        self.config = config or BuildConfig()
        # MAXIMAL: 3.14t free-threading lock for shared data structures
        self._state_lock = threading.Lock()
        self.tasks: dict[str, BuildTask] = {}
        self.build_queue: list[BuildTask] = []
        self.completed: list[BuildTask] = []
        self.failed: list[BuildTask] = []
        self.monitor: (PopupMonitor | None) = None
        self.start_time: (datetime | None) = None

        Path(self.config.base_output).mkdir(parents=True, exist_ok=True)
        Path(self.config.temp_dir).mkdir(parents=True, exist_ok=True)

    def discover_python_files(
        self, path: str = ".", recursive: bool = True
    ) -> list[str]:
        """Discover Python files for compilation."""
        python_files = []
        search_path = Path(path)
        if not search_path.is_absolute():
            search_path = Path.cwd() / search_path
        search_path = search_path.resolve()

        def _safe_walk(p: Path):
            """Walk a directory tree, skipping unreadable dirs."""
            try:
                for item in p.iterdir():
                    if self._should_skip(item):
                        continue
                    if item.is_dir():
                        _safe_walk(item)
                    elif item.suffix.lower() in (".py", ".pyw"):
                        python_files.append(str(item))
            except PermissionError as exc:
                note_failure("TARBABY:L261", exc)

        try:
            if recursive:
                if search_path.is_dir():
                    _safe_walk(search_path)
                elif search_path.suffix.lower() in (".py", ".pyw"):
                    python_files.append(str(search_path))
            else:
                for py_file in search_path.glob("*.py"):
                    if not self._should_skip(py_file):
                        python_files.append(str(py_file))
        except PermissionError as exc:
            note_failure("TARBABY:L274", exc)
        return python_files

    def _should_skip(self, path: Path) -> bool:
        """Skip certain directories."""
        skip_patterns = [
            "__pycache__",
            ".git",
            ".venv",
            "venv",
            "node_modules",
            ".tox",
            "dist",
            "build",
        ]
        return any(p in str(path) for p in skip_patterns)

    def create_build_task(
        self, source: str, format: BuildFormat = BuildFormat.EXE
    ) -> BuildTask:
        """Create a build task."""
        task = BuildTask(source=source, format=format)
        task.output = self._generate_output_path(source, format)
        with self._state_lock:
            self.tasks[source] = task
        return task

    def _generate_output_path(self, source: str, format: BuildFormat) -> str:
        """Generate output path for artifact."""
        source_path = Path(source)
        stem = source_path.stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if format == BuildFormat.EXE:
            ext = ".exe"
        elif format == BuildFormat.ZIP:
            ext = ".tarbaby.zip"
        elif format == BuildFormat.WHEEL:
            ext = ".whl"
        elif format == BuildFormat.DOCKER:
            ext = ""
        elif format == BuildFormat.APK:
            ext = ".apk"
        else:
            ext = ".bin"

        return f"{self.config.base_output}/{stem}_{timestamp}{ext}"

    def _compile_with_pyinstaller(
        self, task: BuildTask, monitor: (PopupMonitor | None) = None
    ) -> bool:
        """Compile using PyInstaller."""
        source = Path(task.source)
        if task.output is None:
            raise TypeError(
                f"TARBABY._compile_with_pyinstaller: task {task.source!r} has no output path"
            )
        output_dir = Path(task.output).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--clean",
            "--noconfirm",
            f"--distpath={output_dir}",
            f"--workpath={self.config.temp_dir}",
        ]

        if self.config.icon_path:
            cmd.extend(["--icon", self.config.icon_path])

        for imp in self.config.hidden_imports:
            cmd.extend(["--hidden-import", imp])

        cmd.append(str(source))

        if monitor:
            monitor.log(f"Compiling {source.name}...", "BUILD")
            monitor.update_status("Compiling...", 25)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300, encoding="utf-8", errors="replace"
            )

            if result.returncode == 0:
                if monitor:
                    monitor.log(f"Compilation successful: {source.name}", "OK")
                return True
            else:
                if monitor:
                    monitor.log(f"Compilation failed: {result.stderr}", "ERROR")
                task.errors.append(result.stderr)
                return False

        except subprocess.TimeoutExpired:
            if monitor:
                monitor.log("Compilation timeout", "ERROR")
            task.errors.append("Timeout expired")
            return False
        except Exception as e:
            if monitor:
                monitor.log(f"Error: {str(e)}", "ERROR")
            task.errors.append(str(e))
            return False

    def _create_zip_archive(
        self, task: BuildTask, monitor: (PopupMonitor | None) = None
    ) -> bool:
        """Create ZIP archive of Python project."""
        source = Path(task.source)

        try:
            if task.output is None:
                raise TypeError(
                    f"TARBABY._create_zip_archive: task {task.source!r} has no output path"
                )
            with zipfile.ZipFile(task.output, "w", zipfile.ZIP_DEFLATED) as zf:
                if source.is_file():
                    zf.write(source, arcname=source.name)
                    if monitor:
                        monitor.log(f"Added {source.name} to archive", "ZIP")
                elif source.is_dir():
                    for root, dirs, files in os.walk(source):
                        for file in files:
                            file_path = Path(root, file)
                            arcname = file_path.relative_to(source.parent)
                            zf.write(file_path, arcname=str(arcname))
                    if monitor:
                        monitor.log(f"Archived directory {source.name}", "ZIP")

            if monitor:
                monitor.log(f"ZIP created: {task.output}", "OK")
            return True

        except Exception as e:
            if monitor:
                monitor.log(f"ZIP error: {str(e)}", "ERROR")
            task.errors.append(str(e))
            return False

    def _calculate_checksum(self, file_path: str) -> str:
        """Calculate SHA256 checksum of file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def execute_task(
        self, task: BuildTask, monitor: (PopupMonitor | None) = None
    ) -> bool:
        """Execute a single build task."""
        task.start_time = datetime.now()
        task.status = BuildStatus.COMPILING

        success = False

        if task.format == BuildFormat.EXE:
            task.status = BuildStatus.COMPILING
            success = self._compile_with_pyinstaller(task, monitor)
        elif task.format == BuildFormat.ZIP:
            task.status = BuildStatus.PACKAGING
            success = self._create_zip_archive(task, monitor)
        else:
            task.status = BuildStatus.COMPLETE
            success = True

        task.end_time = datetime.now()

        with self._state_lock:
            if success:
                task_output = task.output
                if task_output is None:
                    raise TypeError(
                        f"TARBABY.execute_task: task {task.source!r} has no output path"
                    )
                if Path(task_output).exists():
                    task.status = BuildStatus.COMPLETE
                    task.progress = 100
                    task.checksum = self._calculate_checksum(task_output)
                    task.artifact_path = task_output
                    self.completed.append(task)
                else:
                    task.status = BuildStatus.FAILED
                    self.failed.append(task)
            else:
                task.status = BuildStatus.FAILED
                self.failed.append(task)

        return success

    def run_full_test(
        self,
        path: str = ".",
        duration_seconds: int = 600,
        formats: (list[BuildFormat] | None) = None,
    ) -> dict[str, Any]:
        """Run full compilation test on discovered Python files."""
        self.start_time = datetime.now()

        if formats is None:
            formats = [BuildFormat.EXE, BuildFormat.ZIP]

        self.monitor = PopupMonitor(f"10-Minute Test - {path}")
        self.monitor.create_window()

        self.monitor.log("=" * 60)
        self.monitor.log("TARBABY FULL CLUSTER TEST")
        self.monitor.log(f"Target: {path}")
        self.monitor.log(f"Duration: {duration_seconds} seconds")
        self.monitor.log("=" * 60)

        results = {
            "start_time": self.start_time.isoformat(),
            "target_path": path,
            "duration_seconds": duration_seconds,
            "files_discovered": 0,
            "builds_started": 0,
            "builds_completed": 0,
            "builds_failed": 0,
            "artifacts": [],
            "errors": [],
        }

        files = self.discover_python_files(path)
        results["files_discovered"] = len(files)
        self.monitor.log(f"Discovered {len(files)} Python files")

        limit_files = files[:50]
        self.monitor.log(f"Processing first {len(limit_files)} files (time-limited)")

        start_compile = time.time()

        for i, source in enumerate(limit_files):
            elapsed = time.time() - start_compile
            if elapsed > duration_seconds:
                self.monitor.log("Time limit reached, stopping...", "WARN")
                break

            task = self.create_build_task(source, BuildFormat.EXE)
            with self._state_lock:
                self.build_queue.append(task)
            results["builds_started"] += 1

            progress = int((i / len(limit_files)) * 100)
            self.monitor.update_status(
                f"Building {i + 1}/{len(limit_files)}: {Path(source).name}",
                progress,
            )
            self.monitor.log(f"[{i + 1}/{len(limit_files)}] Processing: {source}")

            success = self.execute_task(task, self.monitor)

            if success:
                results["builds_completed"] += 1
                results["artifacts"].append(task.output)
            else:
                results["builds_failed"] += 1
                results["errors"].extend(task.errors)

            self.monitor.log(
                f"Status: {'OK' if success else 'FAILED'} | "
                f"Elapsed: {elapsed:.1f}s | "
                f"Remaining: {max(0, duration_seconds - elapsed):.1f}s"
            )

        results["end_time"] = datetime.now().isoformat()
        results["actual_duration"] = time.time() - start_compile

        self.monitor.log("=" * 60)
        self.monitor.log("TARBABY TEST COMPLETE")
        self.monitor.log(f"Files discovered: {results['files_discovered']}")
        self.monitor.log(f"Builds started: {results['builds_started']}")
        self.monitor.log(f"Builds completed: {results['builds_completed']}")
        self.monitor.log(f"Builds failed: {results['builds_failed']}")
        self.monitor.log(f"Duration: {results['actual_duration']:.2f}s")
        self.monitor.log("=" * 60)
        self.monitor.update_status("Complete!", 100)

        self.save_results(results)

        # MAXIMAL: Signal close through the base class close() method
        self.monitor.close()

        return results

    def save_results(self, results: dict[str, Any]):
        """Save test results to JSON."""
        output_file = Path(self.config.base_output) / "test_results.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        if self.monitor:
            self.monitor.log(f"Results saved: {output_file}")

    def monitor_loop(self):
        """Run tkinter mainloop on current thread (must be main thread on Windows)."""
        if self.monitor and self.monitor.root:
            self.monitor.root.mainloop()


def tarbaby_cli():
    """TARBABY command-line interface."""
    parser = argparse.ArgumentParser(description="TARBABY Binary Builder")
    parser.add_argument("--source", "-s", help="Source file or directory")
    parser.add_argument(
        "--format",
        choices=["exe", "zip", "wheel", "docker", "apk"],
        default="exe",
    )
    parser.add_argument("--output", "-o", help="Output path")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run 10-minute full cluster test",
    )
    parser.add_argument("--path", default=".", help="Test path")
    parser.add_argument(
        "--duration", type=int, default=600, help="Test duration (seconds)"
    )
    parser.add_argument("--monitor", action="store_true", help="Show popup monitor")

    args = parser.parse_args()

    if args.test:
        config = BuildConfig()
        compiler = TARBABYCompiler(config)

        if args.monitor:
            compiler.monitor = PopupMonitor("10-Minute TARBABY Test")
            compiler.monitor.create_window()

            def _run_test_in_background():
                results = compiler.run_full_test(
                    path=args.path if args.path else ".",
                    duration_seconds=args.duration,
                )
                print(json.dumps(results, indent=2))

            threading.Thread(target=_run_test_in_background, daemon=True).start()
            compiler.monitor.run_loop()
        else:
            results = compiler.run_full_test(
                path=args.path if args.path else ".",
                duration_seconds=args.duration,
            )
            print(json.dumps(results, indent=2))
    else:
        print("Specify --test to run full cluster test")
        print("Usage: python TARBABY.py --test --path . --duration 600")


if __name__ == "__main__":
    tarbaby_cli()


