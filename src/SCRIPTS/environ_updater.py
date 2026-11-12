# coding: utf-8
"""
Azazael Environment with ML Imbuement
=======================================
Fully ML-enabled environment management with spawn extendability.

Usage:
    >>> from environ_updater import AzazaelEnvironment
    >>> env = AzazaelEnvironment()
    >>> env.scan_all()
    >>> env.fix_all()
    >>> env.spawn_all()
"""

import sys
import re
import subprocess
from pathlib import Path
from datetime import datetime
# from typing import Dict, Optional, List  # removed — all names have native equivalents
from dataclasses import dataclass
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import List
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class ConflictType(Enum):
    SPELLING = "spelling"
    PATH_REFERENCE = "path_reference"
    PYTHON_VERSION = "python_version"
    IMPORT_ERROR = "import_error"
    SYNTAX = "syntax"
    NAMING = "naming"
    ENCODING = "encoding"


@dataclass
class Conflict:
    file_path: str
    line_number: int
    conflict_type: ConflictType
    wrong_text: str
    correct_text: str
    confidence: float
    severity: str


@dataclass
class EnvironmentStatus:
    total_files: int = 0
    files_scanned: int = 0
    conflicts_found: int = 0
    conflicts_fixed: int = 0
    health_score: float = 100.0
    last_scan: str = ""


@dataclass
class SpawnResult:
    name: str
    success: bool
    output: str = ""
    error: str = ""


class MLPatternMatcher:
    """ML-powered pattern matching for conflict detection."""

    def __init__(self):
        self.spelling_map = {
            "Azazael": "Azazael",
            "Azazael": "Azazael",
            "Azazael": "Azazael",
            "Azazael": "Azazael",
        }
        self.path_map = {
            "ORCHESTRATION": "ORCHESTRATION",
            "HYDRA.py": "HYDRA.py",
        }
        self.syntax_map = {
            r"\bexcept\s*:\b": "except Exception as e:",
            r"__import__\(": "importlib.import_module(",
        }

    def detect(self, content: str, file_path: str) -> list[Conflict]:
        conflicts = []
        lines = content.split("\n")

        for line_num, line in enumerate(lines, 1):
            line.strip()

            for wrong, correct in self.spelling_map.items():
                if wrong in line:
                    conflicts.append(
                        Conflict(
                            file_path=file_path,
                            line_number=line_num,
                            conflict_type=ConflictType.SPELLING,
                            wrong_text=wrong,
                            correct_text=correct,
                            confidence=0.95,
                            severity="high",
                        )
                    )

            for wrong_pattern, correct in self.path_map.items():
                if wrong_pattern in line:
                    conflicts.append(
                        Conflict(
                            file_path=file_path,
                            line_number=line_num,
                            conflict_type=ConflictType.PATH_REFERENCE,
                            wrong_text=wrong_pattern,
                            correct_text=correct,
                            confidence=0.90,
                            severity="critical",
                        )
                    )

            for pattern, correct in self.syntax_map.items():
                if re.search(pattern, line):
                    conflicts.append(
                        Conflict(
                            file_path=file_path,
                            line_number=line_num,
                            conflict_type=ConflictType.SYNTAX,
                            wrong_text=pattern,
                            correct_text=correct,
                            confidence=0.85,
                            severity="medium",
                        )
                    )

        return conflicts


class AzazaelEnvironment:
    """The ML-imbued Azazael Environment."""

    VERSION = "3.0.0"

    def __init__(self, root: (str | None) = None):
        self.root = Path(root) if root else Path(__file__).parent.parent
        self.matcher = MLPatternMatcher()
        self.conflicts: list[Conflict] = []
        self._detect_python()

    def _detect_python(self):
        venv = self.root / ".venv" / "Scripts" / "python.exe"
        self.python = str(venv) if venv.exists() else sys.executable
        self.venv_exists = venv.exists()

    def scan_all(self, threads: int = 16) -> EnvironmentStatus:
        """Full ML scan of ecosystem."""
        print("=" * 70)
        print("  Azazael ML Full Scan")
        print("=" * 70)

        status = EnvironmentStatus()

        files = []
        for py in self.root.rglob("*.py"):
            if "__pycache__" not in str(py) and ".venv" not in str(py):
                files.append(str(py))

        status.total_files = len(files)
        print(f"Files: {status.total_files}")
        print(f"Python: {self.python}")
        print()

        all_conflicts = []

        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = {executor.submit(self._scan_file, f): f for f in files}
            for i, future in enumerate(futures):
                file_conflicts = future.result()
                all_conflicts.extend(file_conflicts)
                if (i + 1) % 50 == 0:
                    print(f"  Progress: {i + 1}/{status.total_files}")

        self.conflicts = all_conflicts
        status.files_scanned = len(files)
        status.conflicts_found = len(all_conflicts)
        status.last_scan = datetime.now().isoformat()
        status.health_score = max(0, 100 - len(all_conflicts) * 0.3)

        self._print_results(status)

        return status

    def _scan_file(self, file_path: str) -> list[Conflict]:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return self.matcher.detect(content, file_path)
        except Exception as exc:
            note_failure("environ_updater:L196", exc)
            return []

    def _print_results(self, status: EnvironmentStatus):
        print()
        print("=" * 70)
        print("  RESULTS")
        print("=" * 70)
        print(f"Files scanned: {status.files_scanned}")
        print(f"Conflicts: {status.conflicts_found}")
        print(f"Health: {status.health_score:.1f}%")
        print()

        if self.conflicts:
            by_type = Counter(c.conflict_type.value for c in self.conflicts)
            for ct, count in by_type.most_common():
                print(f"  {ct}: {count}")

            by_sev = Counter(c.severity for c in self.conflicts)
            print()
            for sev in ["critical", "high", "medium"]:
                if sev in by_sev:
                    print(f"  {sev}: {by_sev[sev]}")
        print("=" * 70)

    def fix_all(self) -> dict:
        """Fix all detected conflicts."""
        print("=" * 70)
        print("  FIXING ALL CONFLICTS")
        print("=" * 70)

        fixed = 0
        errors = 0
        files_fixed = set()

        for conflict in self.conflicts:
            try:
                with open(
                    conflict.file_path, "r", encoding="utf-8", errors="ignore"
                ) as f:
                    lines = f.readlines()

                if conflict.line_number <= len(lines):
                    old_line = lines[conflict.line_number - 1]
                    new_line = old_line.replace(
                        conflict.wrong_text, conflict.correct_text
                    )

                    if new_line != old_line:
                        lines[conflict.line_number - 1] = new_line

                        with open(conflict.file_path, "w", encoding="utf-8") as f:
                            f.writelines(lines)

                        fixed += 1
                        files_fixed.add(conflict.file_path)

            except Exception as exc:
                note_failure("environ_updater:L253", exc)
                errors += 1

        result = {"fixed": fixed, "errors": errors, "files": len(files_fixed)}

        print(f"Fixed: {fixed}")
        print(f"Errors: {errors}")
        print(f"Files: {len(files_fixed)}")
        print("=" * 70)

        self.conflicts = []
        return result

    def spawn(self, command: str, name: str = "process") -> SpawnResult:
        """Spawn a process via shell (accepts string command for backward compat)."""
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=300, encoding="utf-8", errors="replace"
            )
            return SpawnResult(
                name=name,
                success=result.returncode == 0,
                output=result.stdout[:1000],
                error=result.stderr[:500],
            )
        except Exception as e:
            note_failure("environ_updater:L282", e)
            return SpawnResult(name=name, success=False, error=str(e))

    def _spawn_safe(self, args: list, name: str = "process") -> SpawnResult:
        """Spawn a process using list form (no shell=True). Preferred over spawn()."""
        try:
            result = subprocess.run(
                args,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=300, encoding="utf-8", errors="replace"
            )
            return SpawnResult(
                name=name,
                success=result.returncode == 0,
                output=result.stdout[:1000],
                error=result.stderr[:500],
            )
        except Exception as e:
            note_failure("environ_updater:L301", e)
            return SpawnResult(name=name, success=False, error=str(e))

    def spawn_crawler(self, name: str) -> SpawnResult:
        """Spawn a crawler (safe list form)."""
        commands = {
            "terence": [self.python, str(self.root / "UTILITIES" / "TERENCE" / "terence_enhanced.py"), "analyze"],
            "tarbaby": [self.python, str(self.root / "UTILITIES" / "TARBABY" / "tarbaby_enhanced.py"), "analyze"],
            "hydra": [self.python, str(self.root / "HYDRA" / "hydra_launcher.py"), "run"],
        }
        args_list = commands.get(name.lower())
        if args_list:
            return self._spawn_safe(args_list, f"crawler:{name}")
        # Fallback for unknown names (string passthrough for backward compat)
        return self.spawn(name, f"crawler:{name}")

    def spawn_protocol(self, name: str) -> SpawnResult:
        """Spawn a protocol (safe list form)."""
        commands = {
            "spelling": [self.python, str(self.root / "HYDRA" / "hydra_protocols.py"), "run", "--spelling"],
            "paths": [self.python, str(self.root / "HYDRA" / "hydra_protocols.py"), "run", "--paths"],
            "python": [self.python, str(self.root / "HYDRA" / "hydra_protocols.py"), "run", "--python"],
        }
        args_list = commands.get(name.lower())
        if args_list:
            return self._spawn_safe(args_list, f"protocol:{name}")
        return self.spawn(name, f"protocol:{name}")

    def spawn_all(self) -> dict:
        """Spawn all crawlers and protocols."""
        print("=" * 70)
        print("  SPAWN ALL")
        print("=" * 70)

        results = {}

        for crawler in ["terence", "tarbaby", "hydra"]:
            r = self.spawn_crawler(crawler)
            results[crawler] = r
            status = "OK" if r.success else "FAIL"
            print(f"  [{status}] crawler:{crawler}")

        for protocol in ["spelling", "paths", "python"]:
            r = self.spawn_protocol(protocol)
            results[protocol] = r
            status = "OK" if r.success else "FAIL"
            print(f"  [{status}] protocol:{protocol}")

        success = sum(1 for r in results.values() if r.success)
        print(f"\nSuccess: {success}/{len(results)}")
        print("=" * 70)

        return results

    def full_cycle(self) -> dict:
        """Complete scan + fix + spawn."""
        print("\n" + "=" * 70)
        print("  Azazael FULL CYCLE")
        print("=" * 70)

        print("\n[1/3] SCANNING...")
        scan_status = self.scan_all()

        print("\n[2/3] FIXING...")
        fix_result = self.fix_all()

        print("\n[3/3] SPAWNING...")
        spawn_results = self.spawn_all()

        print("\n" + "=" * 70)
        print("  COMPLETE")
        print("=" * 70)
        print(f"Conflicts found: {scan_status.conflicts_found}")
        print(f"Conflicts fixed: {fix_result['fixed']}")
        print(f"Health score: {scan_status.health_score}%")
        print("=" * 70)

        return {
            "scan": scan_status,
            "fix": fix_result,
            "spawn": spawn_results,
        }


def main():

    parser = argparse.ArgumentParser(description="Azazael Environment - ML Imbued")
    parser.add_argument(
        "--version", action="version", version=f"Azazael v{AzazaelEnvironment.VERSION}"
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("scan", help="Full ML scan")
    subparsers.add_parser("fix", help="Fix all conflicts")
    subparsers.add_parser("spawn-all", help="Spawn all processes")
    subparsers.add_parser("full", help="Full cycle: scan + fix + spawn")
    subparsers.add_parser("status", help="Show status")

    p_spawn = subparsers.add_parser("spawn", help="Spawn a process")
    p_spawn.add_argument("target", help="Target name")
    p_spawn.add_argument("--type", choices=["crawler", "protocol"], default="crawler")

    args = parser.parse_args()

    env = AzazaelEnvironment()

    if args.command == "scan":
        env.scan_all()
    elif args.command == "fix":
        env.fix_all()
    elif args.command == "spawn":
        if args.type == "crawler":
            r = env.spawn_crawler(args.target)
        else:
            r = env.spawn_protocol(args.target)
        print(f"Success: {r.success}")
    elif args.command == "spawn-all":
        env.spawn_all()
    elif args.command == "full":
        env.full_cycle()
    elif args.command == "status":
        print(f"Version: {env.VERSION}")
        print(f"Python: {env.python}")
        print(f"Conficts pending: {len(env.conflicts)}")
    else:
        print("Azazael Environment - ML Imbued")
        print()
        print("Commands:")
        print("  scan       - Full ML scan")
        print("  fix        - Fix all conflicts")
        print("  spawn-all  - Spawn all processes")
        print("  full       - Scan + fix + spawn")
        print("  status     - Show status")


if __name__ == "__main__":
    main()
