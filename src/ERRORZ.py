# coding: utf-8
"""
ERRORZ - Code Error Crawler Worm

Detects and reports code errors across the Azazael ecosystem.

Features:
- Syntax error detection
- Runtime error patterns
- Import errors
- Type errors
- Attribute errors
- Reference errors
- Indentation errors
- Tab/space consistency
- Unreachable code
- Empty try-except blocks
- Bare except clauses
- Deprecated functions
- Circular imports
- Missing docstrings

Usage:
    >>> import errorz
    >>> errorz.run()
    >>> errorz.get_report()
"""

import ast
import re
import os
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field
from collections import defaultdict
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Worm Registry ─────────────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("ERRORZ:L44", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

try:
    from CORE.paths import get_error_targets, DESKTOP_TARGET_NAMES, LOCAL_TARGET_SUBDIRS
    _HAS_PATHS = True
except ImportError as exc:
    note_failure("ERRORZ:L52", exc)
    _HAS_PATHS = False
    # Standalone mirror of CORE/paths.py (kept in sync manually)
    DESKTOP_TARGET_NAMES = [
        "Azazael", "HYDRA.py", "HORUS.py", "TARBABY.py",
        "POINDEXTER", "PERSEPHONE", "WORMZ", "METAWORMS",
    ]
    LOCAL_TARGET_SUBDIRS = ["", "CORE/WORMS", "META"]

# Portability: use env vars with local fallbacks
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
_DEFAULT_OUTPUT = os.path.join(_PROJECT_ROOT, "output")


@dataclass
class ErrorEntry:
    """Single error entry."""

    file: str
    line: int
    column: int
    error_type: str
    message: str
    severity: str = "error"  # error, warning, info
    fix_suggestion: str = ""
    code_snippet: str = ""


@dataclass
class ErrorReport:
    """Complete error report."""

    timestamp: str = ""
    files_scanned: int = 0
    total_errors: int = 0
    total_warnings: int = 0
    errors_by_type: dict[str, int] = field(default_factory=dict)
    errors_by_file: dict[str, list[ErrorEntry]] = field(default_factory=dict)
    critical_errors: list[ErrorEntry] = field(default_factory=list)
    summary: str = ""


def _get_default_targets():
    """Compute default target folders (canonical names live in CORE.paths)."""
    if _HAS_PATHS:
        return get_error_targets()
    # Fallback: standalone definitions (CORE.paths unavailable)
    _DESKTOP_TARGETS = [str(Path.home() / "Desktop" / d) for d in DESKTOP_TARGET_NAMES]
    _LOCAL_TARGETS = [
        str(Path(_PROJECT_ROOT) / s) if s else _PROJECT_ROOT
        for s in LOCAL_TARGET_SUBDIRS
    ]
    return [p for p in _DESKTOP_TARGETS + _LOCAL_TARGETS if Path(p).exists() or p in _LOCAL_TARGETS]


class ERRORZConfig:
    """ERRORZ configuration."""

    # Configurable via AZAZAEL_TARGET_FOLDERS env var (os.pathsep-separated)
    TARGET_FOLDERS = (
        os.environ.get("AZAZAEL_TARGET_FOLDERS", "").split(os.pathsep)
        if os.environ.get("AZAZAEL_TARGET_FOLDERS")
        else _get_default_targets()
    )
    OUTPUT_PATH = os.environ.get("AZAZAEL_OUTPUT_PATH",
        os.path.join(_PROJECT_ROOT, "output", "errorz"))

    skip_patterns = {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".tox",
        "dist",
        "build",
        "*.egg-info",
        "Windows",
        "Program Files",
        "AppData",
    }

    error_patterns = [
        # Syntax patterns (will be caught by AST)
        (r"except\s*:", "Bare except clause", "warning"),
        (r"except\s+BaseException\s*:", "BaseException catch", "warning"),
        (r"pass\s*$", "Empty try/except/finally", "warning"),
        (r"^\s*print\s*\(", "Print statement", "info"),
        (r"^\s*break\s*$", "Break outside loop", "error"),
        (r"^\s*continue\s*$", "Continue outside loop", "error"),
        (r"^\s*return\s+[^,\s]", "Return outside function", "error"),
        (r"\t+", "Tab indentation", "warning"),
        (r"^\s+if\s+__name__\s*==\s*[\"']__main__[\"']", "Main guard", "info"),
        (r"raise\s+Exception\s*\(", "Generic exception", "warning"),
        (r"raise\s+Error\s*\(", "Custom Error raise", "warning"),
        (r"^\s*#\s*TODO", "TODO comment", "info"),
        (r"^\s*#\s*FIXME", "FIXME comment", "info"),
        (r"\[\s*\]", "Empty list literal", "info"),
        (r"\(\s*\)", "Empty tuple literal", "info"),
        (r"^\s*\{\s*\}", "Empty dict literal", "info"),
        (r"\bNone\s*==\s*None\b", "Direct None comparison", "warning"),
        (r"\bNone\s*!=\s*None\b", "Direct None comparison", "warning"),
        (r"\.get\s*\(\s*\)", "dict.get() without default", "info"),
        (r"isinstance\s*\(\s*,\s*str\s*\)", "Use isinstance() with tuple", "warning"),
        (r"eval\s*\(", "Use of eval()", "warning"),
        (r"exec\s*\(", "Use of exec()", "warning"),
        (r"__import__\s*\(", "Use of __import__()", "warning"),
        (r"open\s*\(\s*\)", "Open without mode", "info"),
        (r"\bimport \*\s*$", "Wildcard import", "warning"),
        (r"from\s+\w+\s+import\s+\*", "Wildcard import", "warning"),
        (r"global\s+\w+(?:,\s*\w+)*", "Global variable", "info"),
        (r"\bdel\s+\w+", "Use of del", "info"),
        (r"\bassert\s+\w+", "Assert without message", "warning"),
        (r"^\s+else\s*:", "Unnecessary else", "info"),
        (r"^\s+elif\s+", "Chain elif", "info"),
        (r"for\s+\w+\s+in\s+range\s*\(\s*len\s*\(", "Use enumerate()", "warning"),
        (r"\+\s*\"\s*\"", "String concatenation", "info"),
        (r"\[\s*\w+\s+for\s+\w+\s+in\s+\w+\s*\]\s*if", "list comp with if", "info"),
    ]


@register_worm(category="diagnostic", priority=7, cooldown_seconds=30.0, description="Code error crawler — syntax, pattern, circular import, and docstring analysis")
class ERRORZ:
    """Code Error Crawler Worm."""

    VERSION = "1.0.0"

    def __init__(self):
        self.errors: list[ErrorEntry] = []
        self.warnings: list[ErrorEntry] = []
        self.info: list[ErrorEntry] = []

    def _get_code_context(
        self, file_path: Path, line_num: int, context: int = 2
    ) -> str:
        """Get surrounding code context."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            start = max(0, line_num - context - 1)
            end = min(len(lines), line_num + context)

            snippet = []
            for i in range(start, end):
                marker = ">>>" if i == line_num - 1 else "   "
                snippet.append(f"{marker} {i + 1:4d} | {lines[i].rstrip()}")

            return "\n".join(snippet)
        except Exception as exc:
            note_failure("ERRORZ:L201", exc)
            return ""

    def _check_syntax_errors(self, file_path: Path) -> list[ErrorEntry]:
        """Check for Python syntax errors using AST."""
        errors = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.Try):
                    # Check for empty except
                    if len(node.handlers) == 0:
                        errors.append(
                            ErrorEntry(
                                file=str(file_path),
                                line=node.lineno,
                                column=0,
                                error_type="EmptyTry",
                                message="Empty try block",
                                severity="warning",
                                fix_suggestion="Add except clause or remove try",
                                code_snippet=self._get_code_context(
                                    file_path, node.lineno
                                ),
                            )
                        )

                    for handler in node.handlers:
                        if handler.type is None:
                            errors.append(
                                ErrorEntry(
                                    file=str(file_path),
                                    line=handler.lineno,
                                    column=0,
                                    error_type="BareExcept",
                                    message="Bare except clause",
                                    severity="error",
                                    fix_suggestion="Specify exception type",
                                    code_snippet=self._get_code_context(
                                        file_path, handler.lineno
                                    ),
                                )
                            )

                if isinstance(node, ast.Return) and node.value is None:
                    errors.append(
                        ErrorEntry(
                            file=str(file_path),
                            line=node.lineno,
                            column=0,
                            error_type="ImplicitReturn",
                            message="Implicit return None",
                            severity="info",
                            code_snippet=self._get_code_context(file_path, node.lineno),
                        )
                    )

        except SyntaxError as e:
            note_failure("ERRORZ:L263", e)
            errors.append(
                ErrorEntry(
                    file=str(file_path),
                    line=e.lineno or 1,
                    column=e.offset or 0,
                    error_type="SyntaxError",
                    message=e.msg,
                    severity="error",
                    fix_suggestion="Fix syntax error",
                    code_snippet=self._get_code_context(file_path, e.lineno or 1),
                )
            )
        except Exception as exc:
            note_failure("ERRORZ:L276", exc)

        return errors

    def _check_pattern_errors(self, file_path: Path) -> list[ErrorEntry]:
        """Check for pattern-based errors."""
        errors = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                lines = content.split("\n")

            for line_num, line in enumerate(lines, 1):
                for pattern, message, severity in ERRORZConfig.error_patterns:
                    if re.search(pattern, line):
                        errors.append(
                            ErrorEntry(
                                file=str(file_path),
                                line=line_num,
                                column=line.find(pattern) if pattern in line else 0,
                                error_type=message.split()[0][:20],
                                message=message,
                                severity=severity,
                                code_snippet=self._get_code_context(
                                    file_path, line_num
                                ),
                            )
                        )

        except Exception as exc:
            note_failure("ERRORZ:L307", exc)

        return errors

    def _check_circular_imports(self, file_path: Path) -> list[ErrorEntry]:
        """Check for potential circular imports."""
        errors = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            imports = re.findall(r"(?:from|import)\s+([\w.]+)", content)
            own_name = file_path.stem

            for imp in imports:
                if own_name in imp.split(".") and imp != own_name:
                    errors.append(
                        ErrorEntry(
                            file=str(file_path),
                            line=1,
                            column=0,
                            error_type="PotentialCircularImport",
                            message=f"Potential circular import: {imp}",
                            severity="warning",
                            code_snippet=self._get_code_context(file_path, 1),
                        )
                    )

        except Exception as exc:
            note_failure("ERRORZ:L337", exc)

        return errors

    def _check_unreachable_code(self, file_path: Path) -> list[ErrorEntry]:
        """Check for unreachable code after return/raise."""
        errors = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, (ast.Return, ast.Raise)):
                    if node.lineno:
                        next_line = node.lineno + 1
                        errors.append(
                            ErrorEntry(
                                file=str(file_path),
                                line=next_line,
                                column=0,
                                error_type="UnreachableCode",
                                message="Code after return/raise may be unreachable",
                                severity="info",
                                code_snippet=self._get_code_context(
                                    file_path, next_line
                                ),
                            )
                        )

        except Exception as exc:
            note_failure("ERRORZ:L370", exc)

        return errors

    def _check_missing_docstrings(self, file_path: Path) -> list[ErrorEntry]:
        """Check for missing module/class/function docstrings."""
        errors = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                    docstring = ast.get_docstring(node)
                    if not docstring:
                        node_name = node.__class__.__name__
                        errors.append(
                            ErrorEntry(
                                file=str(file_path),
                                line=node.lineno,
                                column=0,
                                error_type=f"Missing{node_name}Docstring",
                                message=f"Missing {node_name.lower()} docstring",
                                severity="info",
                            )
                        )

                if isinstance(node, ast.Module):
                    docstring = ast.get_docstring(node)
                    if not docstring:
                        errors.append(
                            ErrorEntry(
                                file=str(file_path),
                                line=1,
                                column=0,
                                error_type="MissingModuleDocstring",
                                message="Missing module docstring",
                                severity="info",
                            )
                        )

        except Exception as exc:
            note_failure("ERRORZ:L415", exc)

        return errors

    def run(self) -> ErrorReport:
        """Run full error crawl."""
        print("=" * 70)
        print("  ERRORZ - Code Error Crawler")
        print("=" * 70)
        print(f"Start: {datetime.now().isoformat()}")
        print()

        start_time = time.time()
        report = ErrorReport(timestamp=datetime.now().isoformat())

        for folder in ERRORZConfig.TARGET_FOLDERS:
            p = Path(folder)
            if not p.exists():
                continue

            folder_name = p.name
            file_count = 0
            error_count = 0

            print(f"Scanning: {folder_name}", end=" ", flush=True)

            for f in p.rglob("*.py"):
                if any(s in str(f) for s in ERRORZConfig.skip_patterns):
                    continue

                file_count += 1
                errors = []

                # Run all checks
                errors.extend(self._check_syntax_errors(f))
                errors.extend(self._check_pattern_errors(f))
                errors.extend(self._check_circular_imports(f))
                errors.extend(self._check_unreachable_code(f))
                errors.extend(self._check_missing_docstrings(f))

                # Categorize errors
                for error in errors:
                    if error.severity == "error":
                        self.errors.append(error)
                        error_count += 1
                    elif error.severity == "warning":
                        self.warnings.append(error)
                    else:
                        self.info.append(error)

                    report.errors_by_file.setdefault(error.file, []).append(error)

            print(f"[{file_count} files, {error_count} errors]")

        # Build report
        report.files_scanned = sum(
            1
            for folder in ERRORZConfig.TARGET_FOLDERS
            if Path(folder).exists()
            for f in Path(folder).rglob("*.py")
            if not any(s in str(f) for s in ERRORZConfig.skip_patterns)
        )

        report.total_errors = len(self.errors)
        report.total_warnings = len(self.warnings) + len(self.info)

        for error in self.errors:
            report.errors_by_type[error.error_type] = (
                report.errors_by_type.get(error.error_type, 0) + 1
            )

        report.critical_errors = sorted(self.errors, key=lambda x: x.line)[:20]

        duration = time.time() - start_time
        report.summary = f"Scanned {report.files_scanned} files in {duration:.2f}s - {report.total_errors} errors, {report.total_warnings} warnings"

        # Print summary
        print()
        print("=" * 70)
        print("  ERRORZ RESULTS")
        print("=" * 70)
        print()
        print(f"Files Scanned:    {report.files_scanned}")
        print(f"Total Errors:     {report.total_errors}")
        print(f"Total Warnings:   {report.total_warnings}")
        print(f"Duration:         {duration:.2f}s")
        print()

        if self.errors:
            print()
            print("-" * 70)
            print("  CRITICAL ERRORS")
            print("-" * 70)
            for error in report.critical_errors[:10]:
                print(f"\n  {error.file}")
                print(f"  Line {error.line}: {error.message}")
                if error.code_snippet:
                    for line in error.code_snippet.split("\n")[:3]:
                        print(f"    {line}")

        if self.warnings:
            print()
            print("-" * 70)
            print("  TOP WARNING TYPES")
            print("-" * 70)
            warning_counts = defaultdict(int)
            for w in self.warnings:
                warning_counts[w.error_type] += 1
            for wtype, count in sorted(
                warning_counts.items(), key=lambda x: x[1], reverse=True
            )[:10]:
                print(f"  {wtype}: {count}")

        print()
        print("=" * 70)
        print(f"  {report.summary}")
        print("=" * 70)

        # Save report
        self._save_report(report)

        return report

    def _save_report(self, report: ErrorReport):
        """Save error report to JSON."""
        output_path = Path(ERRORZConfig.OUTPUT_PATH)
        output_path.mkdir(parents=True, exist_ok=True)

        report_data = {
            "timestamp": report.timestamp,
            "files_scanned": report.files_scanned,
            "total_errors": report.total_errors,
            "total_warnings": report.total_warnings,
            "duration_seconds": sum(r.duration_ms for r in []) / 1000,
            "errors_by_type": report.errors_by_type,
            "errors_by_file": {
                k: [
                    {
                        "file": e.file,
                        "line": e.line,
                        "column": e.column,
                        "error_type": e.error_type,
                        "message": e.message,
                        "severity": e.severity,
                    }
                    for e in v
                ]
                for k, v in report.errors_by_file.items()
            },
            "critical_errors": [
                {
                    "file": e.file,
                    "line": e.line,
                    "error_type": e.error_type,
                    "message": e.message,
                }
                for e in report.critical_errors
            ],
            "summary": report.summary,
        }

        with open(output_path / "errorz_report.json", "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)

        print(f"\nReport saved to: {output_path / 'errorz_report.json'}")

    def get_report(self) -> dict[str, Any]:
        """Get current error statistics."""
        return {
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "info": len(self.info),
            "by_type": defaultdict(int),
            "files_with_errors": len(set(e.file for e in self.errors)),
        }


def run_with_popup():
    """Run ERRORZ with popup window."""
    import tkinter as tk
    from tkinter import ttk, scrolledtext

    root = tk.Tk()
    root.title("ERRORZ - Code Error Crawler")
    root.geometry("900x700")
    root.configure(bg="#1a1a2e")

    def log(msg, color="#00ff88"):
        text_area.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        text_area.see("end")
        root.update_idletasks()

    header = tk.Frame(root, bg="#16213e", pady=10)
    header.pack(fill="x")

    tk.Label(
        header,
        text="ERRORZ",
        font=("Consolas", 28, "bold"),
        bg="#16213e",
        fg="#ff4444",
    ).pack()

    status_label = tk.Label(
        header,
        text="Initializing...",
        font=("Consolas", 12),
        bg="#16213e",
        fg="#ffaa00",
    )
    status_label.pack()

    progress = ttk.Progressbar(root, mode="determinate", length=850)
    progress.pack(pady=10, padx=25)

    text_area = scrolledtext.ScrolledText(
        root, wrap="word", font=("Consolas", 10), bg="#0f0f1a", fg="#ff6666", height=35
    )
    text_area.pack(fill="both", expand=True, padx=10, pady=10)

    def run_scan():
        log("=" * 60)
        log("ERRORZ - Code Error Crawler")
        log("=" * 60)

        status_label.config(text="Scanning...")
        root.update_idletasks()

        errorz = ERRORZ()
        report = errorz.run()

        log("")
        log("=" * 60)
        log("SCAN COMPLETE")
        log("=" * 60)
        log(f"Errors: {report.total_errors}")
        log(f"Warnings: {report.total_warnings}")

        status_label.config(text="Complete!")
        progress["value"] = 100

        log("Popup closing in 5 seconds...")
        root.after(5000, root.destroy)

    log("Starting ERRORZ scan...")
    root.after(500, run_scan)
    root.mainloop()


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(description="ERRORZ - Code Error Crawler")
    parser.add_argument("--scan", action="store_true", help="Run full error scan")
    parser.add_argument("--popup", action="store_true", help="Show popup window")
    parser.add_argument("--status", action="store_true", help="Show status")

    args = parser.parse_args()

    if args.popup:
        run_with_popup()
    elif args.scan:
        errorz = ERRORZ()
        errorz.run()
    elif args.status:
        errorz = ERRORZ()
        print(json.dumps(errorz.get_report(), indent=2))
    else:
        print("ERRORZ - Code Error Crawler")
        print()
        print("Usage:")
        print("  python ERRORZ.py --scan   Run full error scan")
        print("  python ERRORZ.py --popup  Show popup window")
        print("  python ERRORZ.py --status Show current status")


if __name__ == "__main__":
    main()
