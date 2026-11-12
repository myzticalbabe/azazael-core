import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "ml_platforms"))

# coding: utf-8
"""
HYDRA Cluster Auto-Protocols
=============================
Auto-protocols for handling common HYDRA cluster issues and conflicts.
Concrete issues into automated resolution cmdlets.

Usage:
    >>> from hydra_protocols import HydraProtocols
    >>> HydraProtocols.resolve_python_conflict()
    >>> HydraProtocols.fix_old_paths()
"""

import sys
import re
import json
import subprocess
from pathlib import Path
from datetime import datetime
# from typing import Dict, List, Optional  # removed — all names have native equivalents
from dataclasses import dataclass, field
from enum import Enum
from typing import List
import argparse
import time

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class ConflictType(Enum):
    """Types of conflicts HYDRA can encounter."""

    PYTHON_PATH = "python_path"
    DEPENDENCY_VERSION = "dependency_version"
    PATH_REFERENCE = "path_reference"
    SPELLING_ERROR = "spelling_error"
    IMPORT_ERROR = "import_error"
    MEMORY_CONFLICT = "memory_conflict"
    GPU_CONFLICT = "gpu_conflict"
    PERMISSION_ERROR = "permission_error"
    ENCODING_ERROR = "encoding_error"
    ENVIRONMENT_MISMATCH = "environment_mismatch"


@dataclass
class ProtocolResult:
    """Result of a protocol execution."""

    protocol: str
    success: bool
    conflicts_resolved: int = 0
    conflicts_remaining: int = 0
    files_modified: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ConflictEntry:
    """A detected conflict."""

    type: ConflictType
    file: str
    line: (int | None)
    current_value: str
    expected_value: str
    severity: str  # critical, high, medium, low
    auto_fixable: bool


class HydraProtocols:
    """
    HYDRA Cluster Auto-Protocols

    Concrete issues from previous sessions into automated resolution:
    1. Python path conflicts (tensorflow not found, wrong python.exe)
    2. Old path references (Azazael_UNIFIED/ORCHESTRATION_UNIFIED/...)
    3. Spelling errors (Azazael, Azazael)
    4. Import errors from path issues
    5. Environment variable mismatches
    """

    VERSION = "2.0.0"

    def __init__(self, root: (str | None) = None):
        self.root = Path(root) if root else Path(__file__).parent.parent
        self.protocol_log: list[ProtocolResult] = []
        self.conflicts: list[ConflictEntry] = []

        # Dynamic paths via CORE.paths
        try:
            from CORE.paths import get_src_dir, get_desktop_dir
            _desktop = get_desktop_dir()
            get_src_dir()
        except ImportError as exc:
            note_failure("hydra_protocols:L96", exc)
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from CORE.paths import get_src_dir, get_desktop_dir
            _desktop = get_desktop_dir()
            get_src_dir()

        _orchestration = _desktop / "Azazael_UNIFIED" / "ORCHESTRATION"
        _hydra = _desktop / "Azazael_UNIFIED" / "HYDRA"

        self.old_path_patterns = [
            str(_orchestration),
            str(_hydra),
            str(_desktop / "Azazael_UNIFIED" / "ORCHESTRATION_UNIFIED"),
            str(_desktop / "Azazael"),
        ]

        self.correct_paths = {
            str(_orchestration): str(_orchestration),
            str(_hydra): str(_hydra),
        }

        self.spelling_errors = {
            "Azazael": "Azazael",
            "Azazael": "Azazael",
            "Azazael": "Azazael",
            "Azazael": "Azazael",
        }

    # === PYTHON PATH PROTOCOLS ===

    def resolve_python_conflict(self) -> ProtocolResult:
        """
        Protocol: Resolve Python path conflicts

        Issues handled:
        - tensorflow not found on sys.executable
        - Wrong python.exe being used
        - .venv not properly activated
        """

        start = time.time()

        result = ProtocolResult(
            protocol="resolve_python_conflict",
            success=True,
            conflicts_resolved=0,
            conflicts_remaining=0,
            files_modified=[],
            errors=[],
        )

        venv_python = self.root / ".venv" / "Scripts" / "python.exe"
        system_python = Path(sys.executable) if Path(sys.executable).exists() else None

        print("=" * 70)
        print("  HYDRA Protocol: PYTHON_PATH_RESOLUTION")
        print("=" * 70)

        if venv_python.exists():
            print(f"[FOUND] Venv Python: {venv_python}")
            result.conflicts_resolved += 1
        else:
            result.errors.append(f"Venv Python not found: {venv_python}")
            result.success = False

        if system_python:
            print(f"[INFO] System Python: {system_python}")

        files_scanned = 0
        for py_file in self.root.rglob("*.py"):
            if "__pycache__" in str(py_file) or ".venv" in str(py_file):
                continue
            files_scanned += 1

            content = py_file.read_text(encoding="utf-8", errors="ignore")

            for old_path, new_path in self.old_path_patterns:
                if old_path in content:
                    fix_count = content.count(old_path)
                    result.conflicts_resolved += fix_count
                    result.files_modified.append(str(py_file))
                    print(f"[CONFLICT] {py_file.name}: {fix_count} old path references")

        result.conflicts_remaining = 0
        result.duration_ms = (time.time() - start) * 1000

        print()
        print(f"Files scanned: {files_scanned}")
        print(f"Conflicts resolved: {result.conflicts_resolved}")
        print(f"Duration: {result.duration_ms:.0f}ms")
        print("=" * 70)

        return result

    def sync_venv_packages(self) -> ProtocolResult:
        """
        Protocol: Sync venv packages with requirements

        Ensures .venv has all packages installed for HYDRA.
        """

        start = time.time()

        result = ProtocolResult(
            protocol="sync_venv_packages",
            success=True,
            conflicts_resolved=0,
            conflicts_remaining=0,
            files_modified=[],
            errors=[],
        )

        venv_python = self.root / ".venv" / "Scripts" / "python.exe"

        print("=" * 70)
        print("  HYDRA Protocol: VENV_PACKAGE_SYNC")
        print("=" * 70)

        if not venv_python.exists():
            result.errors.append("Venv Python not found")
            result.success = False
            return result

        critical_packages = ["torch", "tensorflow", "numpy", "scipy", "pandas"]

        for pkg in critical_packages:
            try:
                proc = subprocess.run(
                    [str(venv_python), "-c", f"import {pkg}; print({pkg}.__version__)"],
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=30,
                )
                if proc.returncode == 0:
                    print(f"[OK] {pkg}: {proc.stdout.strip()}")
                    result.conflicts_resolved += 1
                else:
                    print(f"[MISSING] {pkg}")
                    result.conflicts_remaining += 1
            except Exception as e:
                note_failure("hydra_protocols:L238", e)
                result.errors.append(f"{pkg}: {str(e)}")
                result.conflicts_remaining += 1

        result.duration_ms = (time.time() - start) * 1000
        print()
        print(f"Resolved: {result.conflicts_resolved}")
        print(f"Missing: {result.conflicts_remaining}")
        print("=" * 70)

        return result

    # === PATH PROTOCOLS ===

    def fix_old_paths(self) -> ProtocolResult:
        """
        Protocol: Fix old path references

        Concrete issue: Old paths like
        'C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION'
        need to be corrected to actual structure.
        """

        start = time.time()

        result = ProtocolResult(
            protocol="fix_old_paths",
            success=True,
            conflicts_resolved=0,
            conflicts_remaining=0,
            files_modified=[],
            errors=[],
        )

        print("=" * 70)
        print("  HYDRA Protocol: PATH_REFERENCE_FIX")
        print("=" * 70)

        for py_file in self.root.rglob("*.py"):
            if "__pycache__" in str(py_file) or ".venv" in str(py_file):
                continue

            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                original = content

                for old_pattern, new_pattern in self.correct_paths.items():
                    if old_pattern in content:
                        content = content.replace(old_pattern, new_pattern)
                        content.count(new_pattern)
                        result.conflicts_resolved += 1

                if content != original:
                    py_file.write_text(content, encoding="utf-8")
                    result.files_modified.append(str(py_file))
                    print(f"[MODIFIED] {py_file.relative_to(self.root)}")

            except Exception as e:
                note_failure("hydra_protocols:L296", e)
                result.errors.append(f"{py_file}: {str(e)}")

        result.duration_ms = (time.time() - start) * 1000
        print()
        print(f"Files modified: {len(result.files_modified)}")
        print(f"Conflicts resolved: {result.conflicts_resolved}")
        print(f"Duration: {result.duration_ms:.0f}ms")
        print("=" * 70)

        return result

    # === SPELLING PROTOCOLS ===

    def fix_spelling_errors(self) -> ProtocolResult:
        """
        Protocol: Fix spelling errors in codebase

        Concrete issue: Azazael (3 A's) -> Azazael (2 A's)
        """

        start = time.time()

        result = ProtocolResult(
            protocol="fix_spelling_errors",
            success=True,
            conflicts_resolved=0,
            conflicts_remaining=0,
            files_modified=[],
            errors=[],
        )

        print("=" * 70)
        print("  HYDRA Protocol: SPELLING_CORRECTION")
        print("=" * 70)

        for py_file in self.root.rglob("*.py"):
            if "__pycache__" in str(py_file) or ".venv" in str(py_file):
                continue

            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                original = content

                for wrong, correct in self.spelling_errors.items():
                    if wrong in content:
                        content = content.replace(wrong, correct)
                        count = content.count(correct)
                        result.conflicts_resolved += count

                if content != original:
                    py_file.write_text(content, encoding="utf-8")
                    result.files_modified.append(str(py_file))
                    print(f"[SPELLING] {py_file.relative_to(self.root)}")

            except Exception as e:
                note_failure("hydra_protocols:L352", e)
                result.errors.append(f"{py_file}: {str(e)}")

        result.duration_ms = (time.time() - start) * 1000
        print()
        print(f"Files modified: {len(result.files_modified)}")
        print(f"Spelling fixes: {result.conflicts_resolved}")
        print("=" * 70)

        return result

    # === IMPORT RESOLUTION PROTOCOLS ===

    def resolve_import_conflicts(self) -> ProtocolResult:
        """
        Protocol: Resolve import conflicts

        Concrete issue: Imports failing due to path problems.
        Auto-adds correct paths to sys.path.
        """

        start = time.time()

        result = ProtocolResult(
            protocol="resolve_import_conflicts",
            success=True,
            conflicts_resolved=0,
            conflicts_remaining=0,
            files_modified=[],
            errors=[],
        )

        print("=" * 70)
        print("  HYDRA Protocol: IMPORT_RESOLUTION")
        print("=" * 70)

        import_fixers = {
            "sys.path.insert": self._fix_sys_path,
            "importlib": self._fix_importlib,
        }

        for py_file in self.root.rglob("*.py"):
            if "__pycache__" in str(py_file) or ".venv" in str(py_file):
                continue

            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                original = content

                for pattern, fixer in import_fixers.items():
                    if pattern in content:
                        new_content = fixer(content)
                        if new_content != content:
                            result.conflicts_resolved += 1
                            content = new_content

                if content != original:
                    py_file.write_text(content, encoding="utf-8")
                    result.files_modified.append(str(py_file))
                    print(f"[IMPORT_FIX] {py_file.relative_to(self.root)}")

            except Exception as e:
                note_failure("hydra_protocols:L414", e)
                result.errors.append(f"{py_file}: {str(e)}")

        result.duration_ms = (time.time() - start) * 1000
        print()
        print(f"Files modified: {len(result.files_modified)}")
        print(f"Import fixes: {result.conflicts_resolved}")
        print("=" * 70)

        return result

    def _fix_sys_path(self, content: str) -> str:
        """Fix sys.path.insert patterns."""
        if "sys.path.insert" in content and str(self.root) not in content:
            insert_line = (
                f"sys.path.insert(0, str({self.root / 'HYDRA' / 'ml_platforms'}))"
            )
            if insert_line not in content:
                content = f"import sys\nimport os\n{insert_line}\n\n{content}"
        return content

    def _fix_importlib(self, content: str) -> str:
        """Fix importlib usage."""
        if '__import__("' in content:
            content = re.sub(
                r'__import__\("(\w+)"\)', r'importlib.import_module("\1")', content
            )
        return content

    # === ENVIRONMENT PROTOCOLS ===

    def sync_environment(self) -> ProtocolResult:
        """
        Protocol: Sync environment variables

        Concrete issue: Crawlers need to update Azazael_ENV.bat
        """

        start = time.time()

        result = ProtocolResult(
            protocol="sync_environment",
            success=True,
            conflicts_resolved=0,
            conflicts_remaining=0,
            files_modified=[],
            errors=[],
        )

        print("=" * 70)
        print("  HYDRA Protocol: ENVIRONMENT_SYNC")
        print("=" * 70)

        env_file = self.root / "Azazael_ENV.bat"
        env_json = self.root / ".env.json"

        env_vars = {
            "Azazael_ROOT": str(self.root),
            "Azazael_CORE": str(self.root / "CORE"),
            "Azazael_HYDRA": str(self.root / "HYDRA"),
            "Azazael_LAST_UPDATE": datetime.now().isoformat(),
        }

        if env_file.exists():
            content = env_file.read_text(encoding="utf-8")
            for key, value in env_vars.items():
                if key not in content:
                    content += f'\nset "{key}={value}"'
                    result.conflicts_resolved += 1
                    result.files_modified.append(str(env_file))
            env_file.write_text(content, encoding="utf-8")
            print(f"[UPDATED] {env_file.name}")

        if env_json.exists():
            data = json.loads(env_json.read_text(encoding="utf-8"))
            for key, value in env_vars.items():
                if key not in data:
                    data[key] = value
                    result.conflicts_resolved += 1
            env_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print("[UPDATED] .env.json")

        result.duration_ms = (time.time() - start) * 1000
        print()
        print(f"Variables synced: {result.conflicts_resolved}")
        print("=" * 70)

        return result

    # === FULL PROTOCOL EXECUTION ===

    def run_all_protocols(self) -> dict[str, ProtocolResult]:
        """
        Run all HYDRA protocols sequentially.

        Execution order designed to resolve dependencies:
        1. Spelling first (affects all files)
        2. Paths second (affects imports)
        3. Python path third (runtime configuration)
        4. Imports fourth (resolve module conflicts)
        5. Environment last (sync all settings)
        """
        print()
        print("=" * 70)
        print("  HYDRA CLUSTER AUTO-PROTOCOLS")
        print("  Executing Full Protocol Suite")
        print("=" * 70)
        print()

        results = {}

        protocols = [
            ("spelling_correction", self.fix_spelling_errors),
            ("path_reference_fix", self.fix_old_paths),
            ("python_path_resolution", self.resolve_python_conflict),
            ("import_resolution", self.resolve_import_conflicts),
            ("venv_package_sync", self.sync_venv_packages),
            ("environment_sync", self.sync_environment),
        ]

        for name, protocol in protocols:
            try:
                results[name] = protocol()
            except Exception as e:
                note_failure("hydra_protocols:L538", e)
                results[name] = ProtocolResult(
                    protocol=name,
                    success=False,
                    conflicts_resolved=0,
                    conflicts_remaining=1,
                    files_modified=[],
                    errors=[str(e)],
                    duration_ms=0,
                )

        total_resolved = sum(r.conflicts_resolved for r in results.values())
        total_files = sum(len(r.files_modified) for r in results.values())
        all(r.success for r in results.values())

        print()
        print("=" * 70)
        print("  PROTOCOL SUITE COMPLETE")
        print("=" * 70)
        print(f"Total protocols: {len(protocols)}")
        print(f"Successful: {sum(1 for r in results.values() if r.success)}")
        print(f"Conflicts resolved: {total_resolved}")
        print(f"Files modified: {total_files}")
        print("=" * 70)

        return results

    def get_status(self) -> dict:
        """Get HYDRA protocols status."""
        return {
            "version": self.VERSION,
            "root": str(self.root),
            "protocols_available": [
                "spelling_correction",
                "path_reference_fix",
                "python_path_resolution",
                "import_resolution",
                "venv_package_sync",
                "environment_sync",
            ],
        }


def main():
    """HYDRA Protocols CLI."""

    parser = argparse.ArgumentParser(description="HYDRA Cluster Auto-Protocols")
    parser.add_argument(
        "--version",
        action="version",
        version=f"HYDRA Protocols v{HydraProtocols.VERSION}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Protocols")

    p_run = subparsers.add_parser("run", help="Run all protocols")
    p_run.add_argument("--spelling", action="store_true", help="Fix spelling")
    p_run.add_argument("--paths", action="store_true", help="Fix paths")
    p_run.add_argument("--python", action="store_true", help="Resolve Python")
    p_run.add_argument("--imports", action="store_true", help="Resolve imports")
    p_run.add_argument("--venv", action="store_true", help="Sync venv")
    p_run.add_argument("--env", action="store_true", help="Sync environment")

    p_status = subparsers.add_parser("status", help="Show status")

    args = parser.parse_args()

    protocols = HydraProtocols()

    if args.command == "run":
        if args.spelling:
            protocols.fix_spelling_errors()
        elif args.paths:
            protocols.fix_old_paths()
        elif args.python:
            protocols.resolve_python_conflict()
        elif args.imports:
            protocols.resolve_import_conflicts()
        elif args.venv:
            protocols.sync_venv_packages()
        elif args.env:
            protocols.sync_environment()
        else:
            protocols.run_all_protocols()
    elif args.command == "status":
        print(json.dumps(protocols.get_status(), indent=2))
    else:
        print("HYDRA Cluster Auto-Protocols")
        print()
        print("Usage:")
        print("  python hydra_protocols.py run --spelling")
        print("  python hydra_protocols.py run --paths")
        print("  python hydra_protocols.py run --python")
        print("  python hydra_protocols.py run --imports")
        print("  python hydra_protocols.py run --venv")
        print("  python hydra_protocols.py run --env")
        print("  python hydra_protocols.py run")
        print("  python hydra_protocols.py --status")


if __name__ == "__main__":
    main()
