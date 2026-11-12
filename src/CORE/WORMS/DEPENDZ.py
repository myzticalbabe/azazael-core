# coding: utf-8
"""
DEPENDZ - Dependency Analysis Worm v2.2

Analyzes package dependencies: requirements.txt, pyproject.toml, imports.
Detects unused, outdated, and missing dependencies. Tracks version drift.
Registered for the 'analysis' cluster in CLUSTER_REGISTRY.

Usage:
    from CORE.WORMS.DEPENDZ import DEPENDZ
    d = DEPENDZ()
    result = d.run()
"""

import sys
import re
import json
import subprocess
from pathlib import Path
from collections import defaultdict
from typing import Any, List

from CORE.worm_registry import register_worm

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__version__ = "2.2.0"


def _is_nuitka() -> bool:
    """Detect if running as a Nuitka-compiled binary."""
    return getattr(__builtins__, "__compiled__", False)


@register_worm(
    category="analysis", priority=4, cooldown_seconds=60.0,
    description="Dependency analysis worm — package deps, unused imports, version drift, pip-audit"
)
class DEPENDZ:
    """Dependency analyzer for the Azazael codebase.

    Scans requirements.txt, pyproject.toml, and import statements across
    the codebase. Detects unused, missing, outdated packages and version drift.
    """

    VERSION = "2.2.0"
    KNOWN_STDLIB = {
        "os", "sys", "re", "json", "time", "threading", "pathlib", "typing",
        "collections", "datetime", "subprocess", "argparse", "ast", "io",
        "math", "random", "hashlib", "itertools", "functools", "logging",
        "warnings", "traceback", "unittest", "textwrap", "shutil", "glob",
        "tempfile", "socket", "http", "urllib", "email", "xml", "csv",
        "configparser", "sqlite3", "struct", "pickle", "base64", "copy",
        "enum", "dataclasses", "abc", "contextlib", "asyncio", "concurrent",
        "multiprocessing", "queue", "ctypes", "platform", "signal", "atexit",
        "getpass", "getopt", "inspect", "pprint", "pdb", "profile", "statistics",
        "string", "types", "gc", "weakref", "mmap", "zlib", "gzip", "zipfile",
        "__future__", "importlib", "winreg", "encodings", "codecs",
        "locale", "operator", "distutils", "antigravity",
        "token", "tokenize", "keyword", "symbol", "tabnanny",
        "msvcrt", "nt", "ntpath", "_overlapped",
        # v2.1 expansion — stdlib modules missed by v2.0
        "cprofile", "html", "pstats", "resource", "sched",
        "secrets", "ssl", "sysconfig", "tarfile", "tkinter",
        "tracemalloc", "uuid", "webbrowser",
        # v2.2 expansion — Python 2/3 remnants, C-extensions, built-ins
        "__main__", "__pypy__", "_imp", "_speedups", "_string", "_winreg",
        "array", "binascii", "cgi", "cmd", "copyreg", "cpickle",
        "cstringio", "dbm", "decimal", "errno", "fcntl", "fnmatch",
        "gettext", "imp", "linecache", "marshal", "numbers", "optparse",
        "pkgutil", "plistlib", "posixpath", "readline", "scandir", "sets",
        "shlex", "stat", "stringio", "termios", "ttk", "unicodedata",
        "userdict", "userlist", "zipimport",
    }

    # Pip name -> import name aliases (packages whose install name differs from import name)
    KNOWN_ALIASES: dict[str, str] = {
        "pyyaml": "yaml", "pillow": "pil", "scikit-learn": "sklearn",
        "pywin32": "win32com", "beautifulsoup4": "bs4",
        "opencv-python": "cv2", "python-dotenv": "dotenv",
        "psycopg2-binary": "psycopg2", "azure-storage-blob": "azure",
        "llama-cpp-python": "llama_cpp",
    }

    # Known project-internal modules that may not have .py files in the scan tree
    # (referenced from tests/klink via import or subprocess but files are in META/ or elsewhere)
    KNOWN_INTERNAL: set[str] = {
        "debugz", "distillz", "fleetz", "inferencebridge",
        "hydrameta", "freethread_utils", "gguf",
    }

    # CLI/dev tools that are never imported but appear in requirements
    DEV_TOOLS_IGNORE: set[str] = {
        "black", "isort", "jupyter", "ipython", "ipywidgets",
        "bpython", "docker", "pytest", "mypy", "ruff",
        "pip-audit", "pre-commit", "tox", "nox", "coverage",
        "sphinx", "mkdocs", "twine", "build", "wheel",
    }

    def __init__(self, root: (str | None) = None):
        self._root = Path(root) if root else Path(__file__).resolve().parents[3]  # azazael/ project root
        self._deps: dict[str, str] = {}
        self._imports_found: set[str] = set()
        self._local_modules: set[str] = set()
        self._reverse_aliases: dict[str, str] = {}
        self._results: dict[str, Any] = {}

    # -- Core Analysis ------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Run full dependency analysis."""
        # Pre-compute reverse alias map
        self._reverse_aliases = {v: k for k, v in self.KNOWN_ALIASES.items()}
        self._parse_requirements()
        self._parse_pyproject()
        self._scan_imports()
        self._detect_local_modules()

        unused = self._detect_unused()
        missing = self._detect_missing()
        drift = self._detect_version_drift()
        outdated = self._check_outdated() if not _is_nuitka() else []

        self._results = {
            "version": self.VERSION,
            "root": str(self._root),
            "dependencies": len(self._deps),
            "dependency_list": sorted(self._deps.keys()),
            "unique_imports": len(self._imports_found),
            "unused_packages": unused,
            "missing_packages": missing,
            "version_drift": drift,
            "outdated_packages": outdated,
            "status": "ok",
        }
        return self._results

    @staticmethod
    def _parse_dep_line(line: str) -> (tuple | None):
        """Parse a requirement line into (package, version)."""
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            return None
        match = re.match(r"^([a-zA-Z0-9_.-]+)\s*([><=~!]+\s*[\d.*]+)?", line)
        if match:
            pkg = match.group(1).lower().replace("_", "-")
            ver = match.group(2).strip() if match.group(2) else "any"
            return (pkg, ver)
        return None

    def _parse_requirements(self) -> None:
        """Parse all requirements*.txt files."""
        for req_file in self._root.rglob("requirements*.txt"):
            if any(skip in str(req_file).lower() for skip in ["__pycache__", ".venv", "venv", "site-packages", "nuitka_build_backup", "_backup", "archive"]):
                continue
            try:
                content = req_file.read_text(encoding="utf-8-sig", errors="replace")
                for line in content.split("\n"):
                    parsed = self._parse_dep_line(line)
                    if parsed and parsed[0] not in self._deps:
                        self._deps[parsed[0]] = parsed[1]
            except Exception as exc:
                note_failure("DEPENDZ:L160", exc)

    def _parse_pyproject(self) -> None:
        """Parse pyproject.toml for dependencies."""
        pyproject = self._root / "pyproject.toml"
        if not pyproject.exists():
            return
        try:
            content = pyproject.read_text(encoding="utf-8", errors="replace")
            in_deps = False
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("[") and "dependencies" in line.lower():
                    in_deps = True
                    continue
                if in_deps and line.startswith("["):
                    in_deps = False
                    continue
                if in_deps:
                    match = re.match(r'"([a-zA-Z0-9_.-]+)\s*([><=~!]+\s*[\d.*]+)?"', line)
                    if match:
                        pkg = match.group(1).lower().replace("_", "-")
                        ver = match.group(2).strip() if match.group(2) else "any"
                        if pkg not in self._deps:
                            self._deps[pkg] = ver
        except Exception as exc:
            note_failure("DEPENDZ:L186", exc)

    def _scan_imports(self) -> None:
        """Scan all .py files for import statements."""
        import ast
        for py_file in self._root.rglob("*.py"):
            if any(skip in str(py_file).lower() for skip in ["__pycache__", ".venv", "venv", "site-packages", "nuitka_build_backup", "_backup"]):
                continue
            try:
                content = py_file.read_text(encoding="utf-8-sig", errors="replace")
                tree = ast.parse(content, filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self._imports_found.add(alias.name.split(".")[0].lower())
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            self._imports_found.add(node.module.split(".")[0].lower())
            except (SyntaxError, UnicodeDecodeError) as exc:
                note_failure("DEPENDZ:L205", exc)

    def _detect_unused(self) -> list[str]:
        """Find packages in requirements that are never imported."""
        unused = []
        for pkg in self._deps:
            # Skip CLI/dev tools that are never imported
            if pkg in self.DEV_TOOLS_IGNORE:
                continue
            normalized = pkg.lower().replace("-", "_")
            if normalized in self._imports_found or pkg in self._imports_found:
                continue
            # Check alias — does this pip name map to an import we found?
            alias = self.KNOWN_ALIASES.get(pkg, "")
            if alias and (alias in self._imports_found or alias.replace("-", "_") in self._imports_found):
                continue
            unused.append(pkg)
        return sorted(unused)

    def _detect_missing(self) -> list[str]:
        """Find imports that have no corresponding requirement."""
        missing = []
        for imp in sorted(self._imports_found):
            if imp in self.KNOWN_STDLIB or imp in self.KNOWN_INTERNAL:
                continue
            if imp in self._local_modules:
                continue
            if imp in self._deps:
                continue
            mapped = imp.replace("_", "-")
            if mapped in self._deps:
                continue
            # Check reverse alias — is this import covered by a pip package?
            pip_name = self._reverse_aliases.get(imp, "")
            if pip_name and pip_name in self._deps:
                continue
            missing.append(imp)
        return sorted(missing)

    def _detect_local_modules(self) -> None:
        """Auto-detect local project modules by scanning .py filenames.
        Any .py file in the project whose basename matches an import
        is classified as a local module (not a missing pip package).

        Also registers top-level directory names for ALL .py files
        (not just __init__.py parents) to catch namespace packages
        and subdirectory imports like 'from META import ...'.
        """
        for py_file in self._root.rglob("*.py"):
            if any(skip in str(py_file).lower() for skip in ["__pycache__", ".venv", "venv", "site-packages", "nuitka_build_backup", "_backup"]):
                continue
            # Register every parent directory relative to project root (catches
            # namespace packages and subdirectory imports like 'from META import ...')
            try:
                rel_parts = py_file.relative_to(self._root).parent.parts
                for part in rel_parts:
                    if part:
                        self._local_modules.add(part.lower())
            except (ValueError, IndexError) as exc:
                note_failure("DEPENDZ:L264", exc)
            if py_file.name == "__init__.py":
                parent = py_file.parent.name
                if parent:
                    self._local_modules.add(parent.lower())
            # Add the stem (filename without .py)
            stem = py_file.stem.lower()
            if stem and not stem.startswith("__"):
                self._local_modules.add(stem)
                self._local_modules.add(stem.replace("_", "-"))

    def _detect_version_drift(self) -> list[dict[str, Any]]:
        """Detect conflicting version requirements across multiple files."""
        versions: dict[str, list[tuple]] = defaultdict(list)

        for req_file in self._root.rglob("requirements*.txt"):
            if any(skip in str(req_file).lower() for skip in ["__pycache__", ".venv", "venv", "site-packages", "nuitka_build_backup", "_backup", "archive"]):
                continue
            try:
                content = req_file.read_text(encoding="utf-8-sig", errors="replace")
                for line in content.split("\n"):
                    parsed = self._parse_dep_line(line)
                    if parsed:
                        versions[parsed[0]].append((str(req_file.name), parsed[1]))
            except Exception as exc:
                note_failure("DEPENDZ:L289", exc)

        drift = []
        for pkg, ver_list in versions.items():
            unique_versions = set(v[1] for v in ver_list)
            if len(unique_versions) > 1:
                drift.append({
                    "package": pkg,
                    "versions": [{"file": f, "version": v} for f, v in ver_list],
                })
        return drift

    def _check_outdated(self) -> list[dict[str, str]]:
        """Check for outdated packages via pip list --outdated."""
        if _is_nuitka():
            return []
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if result.returncode == 0:
                packages = json.loads(result.stdout)
                return [
                    {"name": p["name"], "current": p["version"], "latest": p["latest_version"]}
                    for p in packages
                    if p["name"].lower().replace("-", "_") in self._deps or
                       p["name"].lower() in self._deps
                ]
        except (subprocess.TimeoutExpired, Exception) as exc:
            note_failure("DEPENDZ:L319", exc)
        return []

    # -- Standard Worm Methods ----------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Get summary — auto-runs if no results yet."""
        if not self._results:
            # Fast summary: parse requirements only, skip full scan
            self._parse_requirements()
            self._parse_pyproject()
            self._results = {
                "version": self.VERSION,
                "root": str(self._root),
                "dependencies": len(self._deps),
                "dependency_list": sorted(self._deps.keys()),
                "unique_imports": 0,
                "unused_packages": [],
                "missing_packages": [],
                "version_drift": [],
                "outdated_packages": [],
                "status": "summary_only",
            }
        return self._results

    def execute(self) -> dict[str, Any]:
        """Worm execute() standard method."""
        return self.run()

    def __repr__(self) -> str:
        return f"DEPENDZ(v{self.VERSION}, {len(self._deps)} deps tracked)"


if __name__ == "__main__":
    d = DEPENDZ()
    print(f"DEPENDZ v{DEPENDZ.VERSION}")
    result = d.run()
    print(f"Tracked deps: {result['dependencies']}")
    print(f"Unique imports: {result['unique_imports']}")
    print(f"Unused: {result['unused_packages']}")
    print(f"Missing: {result['missing_packages'][:10]}")
    print(f"Version drift: {len(result['version_drift'])} conflicts")
    print(f"Outdated: {len(result['outdated_packages'])} packages")
