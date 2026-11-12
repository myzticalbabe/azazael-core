# coding: utf-8
"""
CLUSTER INSPECTOR - Multi-File Type Analysis System
Morphing worm cluster for checking .txt, .md, .ps1, .html files
Validates pathways, environmental variables, and installations
"""

import os
import re
import json
import subprocess
from pathlib import Path
from typing import Any, List
from datetime import datetime
from dataclasses import dataclass, field

from CORE.paths import get_project_root

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class InspectionResult:
    file_type: str
    file_path: str
    issues: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    valid: bool = True


class PathwayValidator:
    """Validates file paths and directories."""

    def __init__(self):
        self.valid_paths: set[str] = set()
        self.invalid_paths: list[dict] = []

    def check_path_exists(self, path: str, context: str = "") -> (dict | None):
        if os.path.exists(path):
            return None
        return {
            "type": "MISSING_PATH",
            "path": path,
            "context": context,
            "severity": "HIGH",
        }

    def check_drive_exists(self, path: str) -> (dict | None):
        if path.startswith("C:") and not os.path.exists("C:"):
            return {"type": "MISSING_DRIVE", "path": path, "severity": "CRITICAL"}
        return None

    def validate_windows_path(self, path: str, context: str = "") -> list[dict]:
        issues = []
        if not path:
            return issues
        issues.append(self.check_drive_exists(path))
        issues.append(self.check_path_exists(path, context))
        return [i for i in issues if i]

    def extract_and_validate_paths(self, text: str) -> list[dict]:
        issues = []
        path_patterns = [
            r'[A-Za-z]:\\[^\s<>"|?*]+',
            r"%[A-Z_]+%",
            r"\$[A-Za-z_]+",
        ]
        for pattern in path_patterns:
            for match in re.finditer(pattern, text):
                path = match.group()
                if path.startswith("%"):
                    env_var = path.strip("%")
                    if env_var not in os.environ:
                        issues.append(
                            {
                                "type": "MISSING_ENV_VAR",
                                "var": path,
                                "severity": "MEDIUM",
                            }
                        )
        return issues


class EnvironmentalVariableInspector:
    """Inspects and validates environmental variables."""

    def __init__(self):
        self.required_vars = {
            "PATH": "System PATH variable",
            "PYTHONPATH": "Python path",
            "HOMEPATH": "User home directory",
            "USERPROFILE": "User profile directory",
        }
        # Production Scripts dir. The 3.14 install dir was listed here while
        # 3.14t was the production interpreter; it retired 2026-10-19.
        self.important_paths = [
            "C:/Azazael/.venvs/py315t/Scripts",
        ]

    def check_required_vars(self) -> list[dict]:
        issues = []
        for var, desc in self.required_vars.items():
            if var not in os.environ:
                issues.append(
                    {
                        "type": "MISSING_ENV_VAR",
                        "var": var,
                        "description": desc,
                        "severity": "HIGH",
                    }
                )
        return issues

    def check_path_directories(self) -> list[dict]:
        issues = []
        path_var = os.environ.get("PATH", "")
        for expected in self.important_paths:
            if expected not in path_var:
                issues.append(
                    {
                        "type": "PATH_MISSING_DIR",
                        "directory": expected,
                        "severity": "LOW",
                    }
                )
        return issues

    def validate_python_installation(self) -> list[dict]:
        issues = []
        # Production first: 3.15t is the runtime, then its same-version GIL
        # sidecar, then the ONE sanctioned 3.14 fallback. The 3.14t venv
        # (C:/Users/clint/venv-314t) was retired and archived 2026-10-19 and
        # is deliberately absent here: a retired interpreter must not be
        # validated as if it were a live option.
        python_paths = [
            "C:/Azazael/.venvs/py315t/Scripts/python.exe",
            "C:/Azazael/.venvs/py315/Scripts/python.exe",
            "C:/Azazael/.venvs/py314m/Scripts/python.exe",
            # WSL pillar roles (same doctrine, Linux paths).
            "/root/py315t/bin/python3",
            "/opt/python315t/bin/python3.15",
            "/usr/local/bin/python3.15t",
            "/usr/local/bin/python3.14-gil",
        ]
        for pypath in python_paths:
            if os.path.exists(pypath):
                try:
                    result = subprocess.run(
                        [pypath, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
                    )
                    if result.returncode != 0:
                        issues.append(
                            {
                                "type": "PYTHON_VERSION_FAIL",
                                "path": pypath,
                                "severity": "MEDIUM",
                            }
                        )
                except Exception as e:
                    note_failure("CLUSTER_INSPECTOR:L145", e)
                    issues.append(
                        {
                            "type": "PYTHON_EXEC_FAIL",
                            "path": pypath,
                            "error": str(e),
                            "severity": "MEDIUM",
                        }
                    )
        return issues

    def run_inspection(self) -> dict[str, Any]:
        results = {
            "timestamp": datetime.now().isoformat(),
            "env_vars": {},
            "issues": [],
            "warnings": [],
        }

        for var in list(os.environ.keys()):
            if "PATH" in var or "PYTHON" in var:
                results["env_vars"][var] = (
                    os.environ[var][:800] + "..."
                    if len(os.environ[var]) > 100
                    else os.environ[var]
                )

        results["issues"].extend(self.check_required_vars())
        results["issues"].extend(self.check_path_directories())
        results["issues"].extend(self.validate_python_installation())

        return results


class TextFileInspector:
    """Inspects .txt files for wrong information."""

    def __init__(self):
        self.path_validator = PathwayValidator()

    def inspect(self, file_path: Path) -> InspectionResult:
        result = InspectionResult(file_type=".txt", file_path=str(file_path))

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            result.issues.extend(
                self.path_validator.extract_and_validate_paths(content)
            )

        except Exception as e:
            note_failure("CLUSTER_INSPECTOR:L196", e)
            result.issues.append(
                {"type": "READ_ERROR", "error": str(e), "severity": "LOW"}
            )
            result.valid = False

        return result


class MarkdownFileInspector:
    """Inspects .md files for wrong information."""

    def __init__(self):
        self.path_validator = PathwayValidator()

    def inspect(self, file_path: Path) -> InspectionResult:
        result = InspectionResult(file_type=".md", file_path=str(file_path))

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            result.issues.extend(
                self.path_validator.extract_and_validate_paths(content)
            )

            if file_path.name.lower() in ["readme.md", "readme.txt"]:
                if (
                    "azazael" in content.lower()
                    and "installation" not in content.lower()
                ):
                    result.warnings.append(
                        "Readme may be missing installation instructions"
                    )

        except Exception as e:
            note_failure("CLUSTER_INSPECTOR:L231", e)
            result.issues.append(
                {"type": "READ_ERROR", "error": str(e), "severity": "LOW"}
            )
            result.valid = False

        return result


class PowerShellInspector:
    """Inspects .ps1 files for wrong pathways and issues."""

    def __init__(self):
        self.path_validator = PathwayValidator()

    def inspect(self, file_path: Path) -> InspectionResult:
        result = InspectionResult(file_type=".ps1", file_path=str(file_path))

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            path_patterns = [
                r"[A-Za-z]:\\[^\s]+",
                r"\\$Env:[A-Za-z_]+",
                r"\\$[A-Za-z_]+(?:\\[A-Za-z_]+)*",
            ]

            for pattern in path_patterns:
                for match in re.finditer(pattern, content):
                    path = match.group()
                    if not path.startswith("$"):
                        result.issues.extend(
                            self.path_validator.validate_windows_path(path)
                        )

            # 3.14t retired 2026-10-19. A file that still names the retired
            # venv (or its python3.14t.exe) as a runtime is stale; report it
            # whether or not the archive still sits on disk, because the
            # reference itself is what must not survive.
            retired = (
                Path.home() / "venv-314t",
                Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python314" / "python3.14t.exe",
            )
            for cand in retired:
                if str(cand) in content:
                    result.issues.append(
                        {"type": "REFERENCED_RETIRED_PYTHON", "path": str(cand), "severity": "LOW"}
                    )

        except Exception as e:
            note_failure("CLUSTER_INSPECTOR:L276", e)
            result.issues.append(
                {"type": "READ_ERROR", "error": str(e), "severity": "LOW"}
            )
            result.valid = False

        return result


class HTMLInspector:
    """Inspects .html files for wrong information."""

    def __init__(self):
        self.path_validator = PathwayValidator()

    def inspect(self, file_path: Path) -> InspectionResult:
        result = InspectionResult(file_type=".html", file_path=str(file_path))

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            src_pattern = r'src=["\']([^"\']+)["\']'
            href_pattern = r'href=["\']([^"\']+)["\']'

            for pattern in [src_pattern, href_pattern]:
                for match in re.finditer(pattern, content):
                    ref = match.group(1)
                    if ref.startswith("http"):
                        continue
                    result.issues.extend(
                        self.path_validator.validate_windows_path(ref, "HTML reference")
                    )

        except Exception as e:
            note_failure("CLUSTER_INSPECTOR:L310", e)
            result.issues.append(
                {"type": "READ_ERROR", "error": str(e), "severity": "LOW"}
            )
            result.valid = False

        return result


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("CLUSTER_INSPECTOR:L321", exc)
    register_worm = lambda **kw: (lambda cls: cls)


@register_worm(category="analysis", priority=5, cooldown_seconds=45.0, description="Multi-file type analysis system for txt, md, ps1, html inspection")
class ClusterInspector:
    """Main cluster that orchestrates all inspectors."""

    VERSION = "1.0.0"

    def __init__(self, root: (str | None) = None):
        self.root = Path(root) if root else get_project_root()
        self.inspectors = {
            ".txt": TextFileInspector(),
            ".md": MarkdownFileInspector(),
            ".ps1": PowerShellInspector(),
            ".html": HTMLInspector(),
        }
        self.env_inspector = EnvironmentalVariableInspector()
        self.stats = {
            "files_scanned": 0,
            "issues_found": 0,
            "warnings_found": 0,
            "valid_files": 0,
        }

    def find_files_by_type(self, extensions: list[str]) -> list[Path]:
        files: list[Path] = []
        for ext in extensions:
            files.extend(self.root.rglob(f"*{ext}"))
        return list(set(files))

    def run_env_inspection(self) -> dict[str, Any]:
        results = self.env_inspector.run_inspection()
        issues = len(results.get("issues", []))
        print(f"  Environment: {issues} issues found")
        return results

    def run_file_inspection(
        self, extensions: (list[str] | None) = None
    ) -> list[InspectionResult]:
        extensions = extensions if extensions is not None else [".txt", ".md", ".ps1", ".html"]

        files = self.find_files_by_type(extensions)
        print(f"  Files inspected: {len(files)}")

        results: list[InspectionResult] = []

        for i, fp in enumerate(files):
            if i % 100 == 0:  # Progress every 100 files to reduce I/O
                print(f"  Progress: {i}/{len(files)}")

            ext = fp.suffix.lower()
            inspector = self.inspectors.get(ext)
            if inspector is not None:
                result = inspector.inspect(fp)
                results.append(result)
                self.stats["files_scanned"] += 1
                self.stats["issues_found"] += len(result.issues)
                self.stats["warnings_found"] += len(result.warnings)
                if result.valid and not result.issues:
                    self.stats["valid_files"] += 1

        print(f"  Final: {len(files)}/{len(files)}")
        return results

    def generate_report(
        self, file_results: list[InspectionResult], env_results: dict
    ) -> dict:
        report = {
            "timestamp": datetime.now().isoformat(),
            "root": str(self.root),
            "file_results": {
                "total_scanned": self.stats["files_scanned"],
                "issues": self.stats["issues_found"],
                "warnings": self.stats["warnings_found"],
                "valid": self.stats["valid_files"],
            },
            "env_results": {
                "issues": len(env_results.get("issues", [])),
            },
            "details": {"critical": [], "high": [], "medium": [], "low": []},
        }

        for result in file_results:
            for issue in result.issues:
                severity = issue.get("severity", "LOW")
                report["details"][severity].append(
                    {
                        "file": result.file_path,
                        "type": issue["type"],
                        "path": issue.get("path", issue.get("var", "N/A")),
                    }
                )

        return report

    def print_summary(self, file_results: list[InspectionResult], env_results: dict):
        print("=" * 70)
        print("CLUSTER INSPECTION SUMMARY")
        print("=" * 70)
        print()

        print(f"Files scanned: {self.stats['files_scanned']}")
        print(f"Issues found: {self.stats['issues_found']}")
        print(f"Warnings: {self.stats['warnings_found']}")
        print(f"Valid files: {self.stats['valid_files']}")
        print()
        print(f"Environmental issues: {len(env_results.get('issues', []))}")
        print()

        critical = [
            r
            for r in file_results
            if any(i.get("severity") == "CRITICAL" for i in r.issues)
        ]
        if critical:
            print("CRITICAL ISSUES:")
            for r in critical[:5]:
                print(f"  {r.file_path}")
                for i in r.issues:
                    print(f"    [{i['severity']}] {i['type']}: {i.get('path', 'N/A')}")
        else:
            print("[OK] No critical file issues")

        print()
        high = sum(
            1 for r in file_results for i in r.issues if i.get("severity") == "HIGH"
        )
        medium = sum(
            1 for r in file_results for i in r.issues if i.get("severity") == "MEDIUM"
        )

        print(f"High priority issues: {high}")
        print(f"Medium priority issues: {medium}")
        print()

        env_issues = [
            i
            for i in env_results.get("issues", [])
            if i.get("severity") in ["HIGH", "CRITICAL"]
        ]
        if env_issues:
            print("ENVIRONMENTAL ISSUES:")
            for i in env_issues:
                print(
                    f"  [{i['severity']}] {i['type']}: {i.get('var', i.get('directory', 'N/A'))}"
                )
        else:
            print("[OK] No critical environmental issues")

        print()

    def run(self):
        try:
            print(f"CLUSTER INSPECTOR v{self.VERSION} - Root: {self.root}")
            print("-" * 50)

            env_results = self.run_env_inspection()
            file_results = self.run_file_inspection([".txt", ".md", ".ps1", ".html"])

            self.print_summary(file_results, env_results)

            report = self.generate_report(file_results, env_results)

            report_file = self.root / "CLUSTER_INSPECTION_REPORT.json"
            with open(report_file, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"Report saved: {report_file}")

            self._last_report = report
            return report
        except Exception as e:
            note_failure("CLUSTER_INSPECTOR:L492", e)
            self._last_report = {"status": "error", "error": str(e)[:200],
                                 "root": str(self.root)}
            return self._last_report
    def get_summary(self):
        # Summarise the inspection that actually ran. `run()` records it; with
        # no record there is nothing to report but the absence of one.
        report = getattr(self, "_last_report", None)
        if report is None:
            return {"worm": "ClusterInspector", "status": "not_run",
                    "run_recorded": False}
        status = report.get("status", "unknown") if isinstance(report, dict) else "unknown"
        return {"worm": "ClusterInspector", "status": status,
                "run_recorded": True,
                "report_file": str(self.root / "CLUSTER_INSPECTION_REPORT.json")}



if __name__ == "__main__":
    inspector = ClusterInspector()
    inspector.run()
