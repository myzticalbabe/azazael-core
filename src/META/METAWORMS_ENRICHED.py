# coding: utf-8
"""
METAWORMS - Meta Worm Management System (ENRICHED OUTPUT)

Central management for all meta worms that crawl and analyze the Azazael ecosystem.

Features:
- Rich console output with colors and formatting
- Detailed progress tracking
- Comprehensive result summaries
- JSON and HTML report generation
- Popup monitoring window

Meta Worms:
- WORMZ   : Terminology crawler (EXISTING)
- METAZ   : Metadata extractor
- DEPENDZ : Dependency mapper
- VALIDZ  : Syntax validator
- DEADZ   : Dead code detector
- METRICZ : Code metrics collector
- LOGZ    : Log analyzer
- CHANGEZ : Change tracker
- DOCUZ   : Documentation generator
- TESTZ   : Test coverage analyzer
- SECUZ   : Security scanner
- PERFZ   : Performance profiler
- LICENSEZ: License compliance
- COMPATZ : Compatibility checker

Usage:
    >>> import metaworms
    >>> metaworms.run_all()           # Run all meta worms
    >>> metaworms.run("METAZ")        # Run specific worm
    >>> metaworms.get_status()        # Get status
    >>> metaworms.generate_report()   # Generate HTML report
"""

import sys
import json
import ast
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field
from collections import defaultdict
import tkinter as tk
from tkinter import ttk, scrolledtext
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


#: REMOVED 2026-10-10 (no-op gate sweep): three `try: pass / <NAME>_AVAILABLE = True`
#: blocks for OPTIMIZ / HEALTHZ / TRIMZ. The import statement in each had been
#: replaced by `pass`, so every flag was unconditionally True and its
#: `except ImportError` branch was unreachable -- a guard that could never fail.
#: The flags had no reader anywhere in the tree (measured: every occurrence was an
#: assignment), and this module never referenced the three modules. They are alive
#: and are imported BY NAME in HYDRAMETA.py, so nothing here was load-bearing.
#: Restore a real `import OPTIMIZ` only if this module starts using one.


# ANSI Color codes for rich output
class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


@dataclass
class WormResult:
    """Result from a meta worm."""

    name: str
    success: bool
    files_processed: int = 0
    items_found: int = 0
    duration_ms: float = 0.0
    output: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class MetaWormConfig:
    """Configuration for meta worms."""

    TARGET_FOLDERS = [
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/HYDRA",
        "C:/Users/clint/Desktop/HORUS.py",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/UTILITIES/TARBABY.py",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/UTILITIES/POINDEXTER",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/UTILITIES/PERSEPHONE",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/CORE/WORMS",
        "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META",
    ]

    OUTPUT_PATH = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/output"
    REPORTS_PATH = "C:/Users/clint/Desktop/Azazael_UNIFIED/ORCHESTRATION_UNIFIED/META/reports"

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


class RichOutput:
    """Rich console output formatter."""

    @staticmethod
    def print_header(title: str, width: int = 70):
        """Print decorated header."""
        print()
        print(Colors.CYAN + "=" * width + Colors.ENDC)
        print(Colors.CYAN + f"  {title}" + Colors.ENDC)
        print(Colors.CYAN + "=" * width + Colors.ENDC)
        print()

    @staticmethod
    def print_section(title: str):
        """Print section header."""
        print()
        print(Colors.BLUE + "-" * 60 + Colors.ENDC)
        print(Colors.BOLD + Colors.BLUE + f"  {title}" + Colors.ENDC)
        print(Colors.BLUE + "-" * 60 + Colors.ENDC)
        print()

    @staticmethod
    def print_success(msg: str):
        """Print success message."""
        print(f"{Colors.GREEN}[OK]{Colors.ENDC} {msg}")

    @staticmethod
    def print_fail(msg: str):
        """Print failure message."""
        print(f"{Colors.RED}[FAIL]{Colors.ENDC} {msg}")

    @staticmethod
    def print_warning(msg: str):
        """Print warning message."""
        print(f"{Colors.YELLOW}[WARN]{Colors.ENDC} {msg}")

    @staticmethod
    def print_info(msg: str):
        """Print info message."""
        print(f"{Colors.BLUE}[INFO]{Colors.ENDC} {msg}")

    @staticmethod
    def print_metric(label: str, value: str, unit: str = ""):
        """Print formatted metric."""
        print(
            f"  {Colors.BOLD}{label}:{Colors.ENDC} {Colors.CYAN}{value}{Colors.ENDC} {unit}"
        )

    @staticmethod
    def print_table(headers: list[str], rows: list[list[str]]):
        """Print formatted table."""
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                col_widths[i] = max(col_widths[i], len(str(cell)))

        # Print header
        header_line = "  "
        for i, h in enumerate(headers):
            header_line += f"{Colors.BOLD}{h:{col_widths[i]}}{Colors.ENDC}  "
        print(header_line)

        # Print separator
        sep_line = "  "
        for w in col_widths:
            sep_line += "-" * w + "  "
        print(sep_line)

        # Print rows
        for row in rows:
            row_line = "  "
            for i, cell in enumerate(row):
                row_line += f"{str(cell):{col_widths[i]}}  "
            print(row_line)


class METAZ:
    """METAZ - Metadata Extractor Worm."""

    VERSION = "1.0.0"

    def run(self) -> WormResult:
        """Extract metadata from all files."""
        start = time.time()
        files = 0
        items = 0
        errors = []
        warnings = []

        metadata = {
            "total_size_bytes": 0,
            "file_counts": defaultdict(int),
            "recent_files": [],
            "large_files": [],
            "by_extension": defaultdict(lambda: {"count": 0, "size": 0}),
        }

        for folder in MetaWormConfig.TARGET_FOLDERS:
            p = Path(folder)
            if not p.exists():
                continue

            for f in p.rglob("*"):
                if f.is_file() and not any(
                    s in str(f) for s in MetaWormConfig.skip_patterns
                ):
                    files += 1
                    try:
                        stat = f.stat()
                        size = stat.st_size
                        metadata["total_size_bytes"] += size

                        ext = f.suffix.lower() or "(no ext)"
                        metadata["by_extension"][ext]["count"] += 1
                        metadata["by_extension"][ext]["size"] += size
                        metadata["file_counts"][ext] += 1

                        if size > 1024 * 1024:  # > 1MB
                            metadata["large_files"].append(
                                {
                                    "path": str(f),
                                    "size_mb": round(size / (1024 * 1024), 2),
                                }
                            )

                        if stat.st_mtime > (time.time() - 86400):  # Last 24h
                            metadata["recent_files"].append(
                                {
                                    "path": str(f),
                                    "mtime": datetime.fromtimestamp(
                                        stat.st_mtime
                                    ).isoformat(),
                                }
                            )

                        items += 1
                    except Exception as e:
                        note_failure("METAWORMS_ENRICHED:L270", e)
                        errors.append(f"{f}: {str(e)}")

        # Convert large_files to sorted list
        metadata["large_files"] = sorted(
            metadata["large_files"], key=lambda x: x["size_mb"], reverse=True
        )[:20]
        metadata["recent_files"] = metadata["recent_files"][:20]

        return WormResult(
            name="METAZ",
            success=len(errors) == 0,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"metadata": metadata},
            errors=errors,
            warnings=warnings,
        )


class DEPENDZ:
    """DEPENDZ - Dependency Mapper Worm."""

    VERSION = "1.0.0"

    def run(self) -> WormResult:
        """Map import dependencies."""
        start = time.time()
        files = 0
        items = 0
        errors = []

        dependencies = defaultdict(lambda: {"files": set(), "imports": []})

        for folder in MetaWormConfig.TARGET_FOLDERS:
            p = Path(folder)
            if not p.exists():
                continue

            for f in p.rglob("*.py"):
                if any(s in str(f) for s in MetaWormConfig.skip_patterns):
                    continue

                files += 1
                try:
                    with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                        content = fp.read()

                    for line in content.split("\n"):
                        if line.strip().startswith(
                            "import "
                        ) or line.strip().startswith("from "):
                            parts = line.strip().split()
                            if len(parts) >= 2:
                                dep = parts[1].split(".")[0]
                                dependencies[dep]["files"].add(str(f))
                                dependencies[dep]["imports"].append(line.strip())
                                items += 1
                except Exception as e:
                    note_failure("METAWORMS_ENRICHED:L329", e)
                    errors.append(f"{f}: {str(e)}")

        # Convert sets to lists for JSON serialization
        for dep in dependencies:
            dependencies[dep]["files"] = list(dependencies[dep]["files"])
            dependencies[dep]["imports"] = dependencies[dep]["imports"][:10]

        return WormResult(
            name="DEPENDZ",
            success=len(errors) == 0,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"dependencies": dict(dependencies)},
            errors=errors,
        )


class VALIDZ:
    """VALIDZ - Syntax Validator Worm."""

    VERSION = "1.0.0"

    def run(self) -> WormResult:
        """Validate Python syntax."""
        start = time.time()
        files = 0
        items = 0
        errors = []
        warnings = []

        valid_files = []
        invalid_files = []

        for folder in MetaWormConfig.TARGET_FOLDERS:
            p = Path(folder)
            if not p.exists():
                continue

            for f in p.rglob("*.py"):
                if any(s in str(f) for s in MetaWormConfig.skip_patterns):
                    continue

                files += 1
                try:
                    with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                        content = fp.read()
                    ast.parse(content)
                    valid_files.append(str(f))
                    items += 1
                except SyntaxError as e:
                    note_failure("METAWORMS_ENRICHED:L380", e)
                    invalid_files.append(
                        {
                            "file": str(f),
                            "line": e.lineno,
                            "column": e.offset,
                            "message": e.msg,
                        }
                    )
                    errors.append(f"{f}:{e.lineno} - {e.msg}")
                except Exception as e:
                    warnings.append(f"{f}: {str(e)}")

        return WormResult(
            name="VALIDZ",
            success=len(invalid_files) == 0,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={
                "valid": valid_files,
                "invalid": invalid_files,
                "valid_count": len(valid_files),
                "invalid_count": len(invalid_files),
            },
            errors=errors,
            warnings=warnings,
        )


class DEADZ:
    """DEADZ - Dead Code Detector Worm."""

    VERSION = "1.0.0"

    def run(self) -> WormResult:
        """Find unused code."""
        start = time.time()
        files = 0
        items = 0
        warnings = []

        issues = {
            "debug_prints": [],
            "pycache_refs": [],
            "TODO_comments": [],
            "FIXME_comments": [],
            "deprecated_patterns": [],
        }

        for folder in MetaWormConfig.TARGET_FOLDERS:
            p = Path(folder)
            if not p.exists():
                continue

            for f in p.rglob("*.py"):
                if any(s in str(f) for s in MetaWormConfig.skip_patterns):
                    continue

                files += 1
                try:
                    with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                        content = fp.read()
                        lines = content.split("\n")

                    for i, line in enumerate(lines, 1):
                        stripped = line.strip()
                        if 'print("DEBUG' in line or "print('DEBUG" in line:
                            issues["debug_prints"].append({"file": str(f), "line": i})
                            items += 1
                        if "__pycache__" in stripped:
                            issues["pycache_refs"].append({"file": str(f), "line": i})
                            items += 1
                        if "# TODO" in stripped:
                            issues["TODO_comments"].append(
                                {"file": str(f), "line": i, "text": stripped}
                            )
                            items += 1
                        if "# FIXME" in stripped or "# BUG" in stripped:
                            issues["FIXME_comments"].append(
                                {"file": str(f), "line": i, "text": stripped}
                            )
                            items += 1

                    items += 1
                except Exception as e:
                    warnings.append(f"{f}: {str(e)}")

        return WormResult(
            name="DEADZ",
            success=True,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"issues": issues, "total_issues": items},
            warnings=warnings,
        )


class METRICZ:
    """METRICZ - Code Metrics Collector."""

    VERSION = "1.0.0"

    def run(self) -> WormResult:
        """Collect code metrics."""
        start = time.time()
        files = 0
        items = 0

        metrics = {
            "totals": {
                "lines": 0,
                "code_lines": 0,
                "comment_lines": 0,
                "blank_lines": 0,
                "classes": 0,
                "functions": 0,
                "imports": 0,
            },
            "by_file": [],
            "complexity": {
                "avg_functions_per_class": 0,
                "max_nesting_depth": 0,
            },
        }

        classes_by_file = []
        functions_by_file = []

        for folder in MetaWormConfig.TARGET_FOLDERS:
            p = Path(folder)
            if not p.exists():
                continue

            for f in p.rglob("*.py"):
                if any(s in str(f) for s in MetaWormConfig.skip_patterns):
                    continue

                files += 1
                try:
                    with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                        content = fp.read()

                    lines = content.split("\n")
                    file_metrics = {
                        "file": str(f),
                        "lines": len(lines),
                        "code_lines": 0,
                        "comment_lines": 0,
                        "blank_lines": 0,
                        "classes": 0,
                        "functions": 0,
                    }

                    for line in lines:
                        stripped = line.strip()
                        if not stripped:
                            file_metrics["blank_lines"] += 1
                        elif stripped.startswith("#"):
                            file_metrics["comment_lines"] += 1
                        else:
                            file_metrics["code_lines"] += 1

                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ClassDef):
                            file_metrics["classes"] += 1
                            metrics["totals"]["classes"] += 1
                            classes_by_file.append((str(f), node.name))
                        elif isinstance(node, ast.FunctionDef):
                            file_metrics["functions"] += 1
                            metrics["totals"]["functions"] += 1
                            functions_by_file.append((str(f), node.name))

                    metrics["totals"]["lines"] += file_metrics["lines"]
                    metrics["totals"]["code_lines"] += file_metrics["code_lines"]
                    metrics["totals"]["comment_lines"] += file_metrics["comment_lines"]
                    metrics["totals"]["blank_lines"] += file_metrics["blank_lines"]

                    metrics["by_file"].append(file_metrics)
                    items += 1
                except Exception as exc:
                    note_failure("METAWORMS_ENRICHED:L562", exc)

        # Calculate averages
        if metrics["totals"]["classes"] > 0:
            metrics["complexity"]["avg_functions_per_class"] = round(
                metrics["totals"]["functions"] / metrics["totals"]["classes"], 2
            )

        return WormResult(
            name="METRICZ",
            success=True,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"metrics": metrics},
        )


class LOGZ:
    """LOGZ - Log Analyzer Worm."""

    VERSION = "1.0.0"

    def run(self) -> WormResult:
        """Analyze log files."""
        start = time.time()
        files = 0
        items = 0

        logs = defaultdict(lambda: defaultdict(int))

        for folder in MetaWormConfig.TARGET_FOLDERS:
            p = Path(folder)
            if not p.exists():
                continue

            for f in p.rglob("*.log"):
                files += 1
                try:
                    with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                        content = fp.read()

                    for line in content.split("\n"):
                        if not line.strip():
                            continue
                        if "ERROR" in line or "error" in line.lower():
                            logs["errors"]["total"] += 1
                            items += 1
                        if "WARNING" in line or "warning" in line.lower():
                            logs["warnings"]["total"] += 1
                            items += 1
                        if "INFO" in line:
                            logs["info"]["total"] += 1
                            items += 1
                        if "DEBUG" in line:
                            logs["debug"]["total"] += 1
                            items += 1
                except Exception as exc:
                    note_failure("METAWORMS_ENRICHED:L620", exc)

        return WormResult(
            name="LOGZ",
            success=True,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"log_analysis": dict(logs)},
        )


class CHANGEZ:
    """CHANGEZ - Change Tracker Worm."""

    VERSION = "1.0.0"

    def run(self) -> WormResult:
        """Track file changes."""
        start = time.time()
        files = 0
        items = 0

        changes = {
            "recent": [],
            "by_day": defaultdict(int),
            "by_size": {
                "tiny": 0,  # < 1KB
                "small": 0,  # 1KB - 10KB
                "medium": 0,  # 10KB - 100KB
                "large": 0,  # 100KB - 1MB
                "huge": 0,  # > 1MB
            },
        }

        now = time.time()

        for folder in MetaWormConfig.TARGET_FOLDERS:
            p = Path(folder)
            if not p.exists():
                continue

            for f in p.rglob("*"):
                if f.is_file() and not any(
                    s in str(f) for s in MetaWormConfig.skip_patterns
                ):
                    files += 1
                    try:
                        stat = f.stat()
                        size = stat.st_size
                        mtime = stat.st_mtime
                        age_hours = (now - mtime) / 3600

                        # Size categorization
                        if size < 1024:
                            changes["by_size"]["tiny"] += 1
                        elif size < 10240:
                            changes["by_size"]["small"] += 1
                        elif size < 102400:
                            changes["by_size"]["medium"] += 1
                        elif size < 1048576:
                            changes["by_size"]["large"] += 1
                        else:
                            changes["by_size"]["huge"] += 1

                        # Age categorization
                        day = int((now - mtime) / 86400)
                        changes["by_day"][f"{day}d ago"] += 1

                        if age_hours < 24:
                            changes["recent"].append(
                                {
                                    "path": str(f),
                                    "size_kb": round(size / 1024, 2),
                                    "age_hours": round(age_hours, 1),
                                }
                            )
                            items += 1
                    except Exception as exc:
                        note_failure("METAWORMS_ENRICHED:L699", exc)

        changes["recent"] = sorted(changes["recent"], key=lambda x: x["age_hours"])[:30]

        return WormResult(
            name="CHANGEZ",
            success=True,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"changes": changes},
        )


class METAWORMS:
    """Main meta worms management system."""

    VERSION = "3.1.0"
    WORMS = {
        "METAZ": METAZ,
        "DEPENDZ": DEPENDZ,
        "VALIDZ": VALIDZ,
        "DEADZ": DEADZ,
        "METRICZ": METRICZ,
        "LOGZ": LOGZ,
        "CHANGEZ": CHANGEZ,
        # NOTE (runtime trace 2026-10-17): the "ERRORZ": ERRORZ entry born
        # in the tracking snapshot referenced a class that never existed in
        # this file, so the module could never import (NameError at class
        # creation). src/ERRORZ.py's ERRORZ is interface-incompatible
        # (ErrorReport lacks WormResult's success/files_processed/items_found
        # fields), so it cannot fill the slot. Entry removed, not replaced.
    }

    EXTENDED_WORMS = {}

    @classmethod
    def register_worm(cls, name: str, worm_class):
        """Register an additional worm."""
        cls.EXTENDED_WORMS[name] = worm_class

    def __init__(self):
        self.results: dict[str, WormResult] = {}
        self.start_time = None

    def run_all(self) -> dict[str, WormResult]:
        """Run all meta worms with rich output."""
        self.start_time = datetime.now()

        RichOutput.print_header("METAWORMS - Meta Worm Management System")

        print(f"{Colors.BOLD}Version:{Colors.ENDC} {self.VERSION}")
        print(
            f"{Colors.BOLD}Start Time:{Colors.ENDC} {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        print(f"{Colors.BOLD}Worms:{Colors.ENDC} {len(self.WORMS)}")
        print(
            f"{Colors.BOLD}Targets:{Colors.ENDC} {len(MetaWormConfig.TARGET_FOLDERS)} folders"
        )

        RichOutput.print_section("Executing Meta Worms")

        for name, worm_class in self.WORMS.items():
            print(f"\n{Colors.BOLD}[{name}]{Colors.ENDC}", end=" ")
            sys.stdout.flush()

            worm = worm_class()
            result = worm.run()
            self.results[name] = result

            if result.success:
                RichOutput.print_success(
                    f"{result.files_processed} files, {result.items_found} items, {result.duration_ms:.0f}ms"
                )
            else:
                RichOutput.print_fail(
                    f"{result.files_processed} files, {result.items_found} items, {result.duration_ms:.0f}ms"
                )
                for error in result.errors[:3]:
                    print(f"  {Colors.RED}  - {error[:80]}{Colors.ENDC}")

        self._print_summary()
        self._save_results()
        self._generate_report()

        return self.results

    def run(self, name: str) -> WormResult:
        """Run a single meta worm by name."""
        worm_class = self.WORMS.get(name, self.EXTENDED_WORMS.get(name))
        if worm_class is None:
            raise ValueError(f"Unknown meta worm: {name}")
        worm = worm_class()
        result = worm.run()
        self.results[name] = result
        return result

    def _print_summary(self):
        """Print execution summary."""
        start_time = self.start_time
        if start_time is None:
            return
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        RichOutput.print_section("Execution Summary")

        # Summary table
        headers = ["Worm", "Status", "Files", "Items", "Duration"]
        rows = []
        for name, result in self.results.items():
            status = (
                f"{Colors.GREEN}OK{Colors.ENDC}"
                if result.success
                else f"{Colors.RED}FAIL{Colors.ENDC}"
            )
            rows.append(
                [
                    name,
                    status,
                    result.files_processed,
                    result.items_found,
                    f"{result.duration_ms:.0f}ms",
                ]
            )

        RichOutput.print_table(headers, rows)

        print()
        RichOutput.print_metric("Total Duration", f"{duration:.2f}", "seconds")
        RichOutput.print_metric(
            "Total Files Processed",
            str(sum(r.files_processed for r in self.results.values())),
        )
        RichOutput.print_metric(
            "Total Items Found", str(sum(r.items_found for r in self.results.values()))
        )

        # Save summary to file
        summary_file = Path(MetaWormConfig.OUTPUT_PATH) / "enriched_summary.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": start_time.isoformat(),
                    "duration_seconds": duration,
                    "results": {
                        name: {
                            "success": r.success,
                            "files": r.files_processed,
                            "items": r.items_found,
                            "duration_ms": r.duration_ms,
                        }
                        for name, r in self.results.items()
                    },
                },
                f,
                indent=2,
            )

        print(
            f"\n{Colors.CYAN}Results saved to:{Colors.ENDC} {MetaWormConfig.OUTPUT_PATH}"
        )

    def _save_results(self):
        """Save all results."""
        Path(MetaWormConfig.OUTPUT_PATH).mkdir(parents=True, exist_ok=True)

        for name, result in self.results.items():
            output_file = (
                Path(MetaWormConfig.OUTPUT_PATH) / f"{name.lower()}_enriched.json"
            )
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "name": result.name,
                        "success": result.success,
                        "files_processed": result.files_processed,
                        "items_found": result.items_found,
                        "duration_ms": result.duration_ms,
                        "output": result.output,
                        "errors": result.errors,
                        "warnings": result.warnings,
                    },
                    f,
                    indent=2,
                )

    def _generate_report(self):
        """Generate HTML report."""
        Path(MetaWormConfig.REPORTS_PATH).mkdir(parents=True, exist_ok=True)

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>METAWORMS Report</title>
    <style>
        body {{ font-family: Consolas, monospace; background: #1a1a2e; color: #00ff88; padding: 20px; }}
        h1 {{ color: #00d4ff; }}
        h2 {{ color: #7c3aed; border-bottom: 1px solid #7c3aed; }}
        .worm {{ background: #16213e; padding: 15px; margin: 10px 0; border-radius: 5px; }}
        .success {{ color: #00ff88; }}
        .fail {{ color: #ff4444; }}
        .metric {{ margin: 5px 0; }}
        pre {{ background: #0f0f1a; padding: 10px; overflow-x: auto; }}
    </style>
</head>
<body>
    <h1>METAWORMS Report</h1>
    <p>Generated: {datetime.now().isoformat()}</p>
    
    <h2>Summary</h2>
    <div class="worm">
        <p><strong>Total Duration:</strong> {sum(r.duration_ms for r in self.results.values()) / 1000:.2f}s</p>
        <p><strong>Files Processed:</strong> {sum(r.files_processed for r in self.results.values())}</p>
        <p><strong>Items Found:</strong> {sum(r.items_found for r in self.results.values())}</p>
    </div>
    
    <h2>Results by Worm</h2>
"""

        for name, result in self.results.items():
            status_class = "success" if result.success else "fail"
            html += f"""
    <div class="worm">
        <h3 class="{status_class}">{name}</h3>
        <p><strong>Status:</strong> <span class="{status_class}">{"OK" if result.success else "FAIL"}</span></p>
        <p><strong>Files:</strong> {result.files_processed} | <strong>Items:</strong> {result.items_found} | <strong>Duration:</strong> {result.duration_ms:.0f}ms</p>
    </div>
"""

        html += """
</body>
</html>
"""

        report_file = Path(MetaWormConfig.REPORTS_PATH) / "metaworms_report.html"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"{Colors.CYAN}HTML Report:{Colors.ENDC} {report_file}")

    def get_status(self) -> dict[str, Any]:
        """Get meta worms status."""
        return {
            "version": self.VERSION,
            "worms_available": list(self.WORMS.keys()),
            "worms_run": list(self.results.keys()),
            "output_path": MetaWormConfig.OUTPUT_PATH,
            "reports_path": MetaWormConfig.REPORTS_PATH,
        }


def run_popup():
    """Run meta worms with enriched popup window."""
    root = tk.Tk()
    root.title("METAWORMS - Meta Worm Management")
    root.geometry("1000x800")
    root.configure(bg="#1a1a2e")

    def log(msg, level="INFO"):
        colors = {
            "OK": "#00ff88",
            "FAIL": "#ff4444",
            "WARN": "#ffaa00",
            "INFO": "#00aaff",
        }
        timestamp = datetime.now().strftime("%H:%M:%S")
        text_area.insert("end", f"[{timestamp}] [{level}] {msg}\n")
        text_area.see("end")
        root.update_idletasks()

    # Header
    header = tk.Frame(root, bg="#16213e", pady=15)
    header.pack(fill="x")

    tk.Label(
        header,
        text="METAWORMS",
        font=("Consolas", 32, "bold"),
        bg="#16213e",
        fg="#00d4ff",
    ).pack()

    status_label = tk.Label(
        header,
        text="Initializing...",
        font=("Consolas", 14),
        bg="#16213e",
        fg="#7c3aed",
    )
    status_label.pack()

    # Progress
    progress_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    progress_frame.pack(fill="x", padx=20)

    progress = ttk.Progressbar(progress_frame, mode="determinate", length=950)
    progress.pack(fill="x")

    progress_label = tk.Label(
        progress_frame,
        text="0 / 7 worms",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00ff88",
    )
    progress_label.pack()

    # Text area
    text_area = scrolledtext.ScrolledText(
        root, wrap="word", font=("Consolas", 10), bg="#0f0f1a", fg="#00ff88", height=40
    )
    text_area.pack(fill="both", expand=True, padx=20, pady=10)

    def run_worms():
        log("=" * 60, "INFO")
        log("METAWORMS - Meta Worm Management System", "INFO")
        log("=" * 60, "INFO")

        metaworms = METAWORMS()
        worms = list(metaworms.WORMS.keys())

        for i, name in enumerate(worms):
            status_label.config(text=f"Running {name}...")
            progress["value"] = ((i + 1) / len(worms)) * 100
            progress_label.config(text=f"{i + 1} / {len(worms)} worms")
            root.update_idletasks()

            log(f"Starting {name}...", "INFO")
            result = metaworms.run(name)

            if result.success:
                log(
                    f"[OK] {name}: {result.files_processed} files, {result.items_found} items",
                    "OK",
                )
            else:
                log(
                    f"[FAIL] {name}: {result.files_processed} files, {result.items_found} items",
                    "FAIL",
                )

        log("", "INFO")
        log("=" * 60, "INFO")
        log("ALL WORMS COMPLETE", "OK")
        log("=" * 60, "INFO")

        status_label.config(text="Complete!")
        progress["value"] = 100
        progress_label.config(text="7 / 7 worms")

        log(f"Results: {MetaWormConfig.OUTPUT_PATH}", "INFO")

        root.after(5000, root.destroy)

    log("Starting METAWORMS...", "INFO")
    root.after(500, run_worms)
    root.mainloop()


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(description="METAWORMS - Meta Worm Management")
    parser.add_argument("--all", action="store_true", help="Run all meta worms")
    parser.add_argument(
        "--worm", type=str, help="Run specific worm (METAZ, DEPENDZ, etc.)"
    )
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--popup", action="store_true", help="Show popup window")
    parser.add_argument("--report", action="store_true", help="Generate HTML report")

    args = parser.parse_args()

    if args.popup:
        run_popup()
    elif args.status:
        mw = METAWORMS()
        print(json.dumps(mw.get_status(), indent=2))
    elif args.worm:
        mw = METAWORMS()
        result = mw.run(args.worm)
        if result:
            print(
                json.dumps(
                    {
                        "name": result.name,
                        "success": result.success,
                        "files_processed": result.files_processed,
                        "items_found": result.items_found,
                        "duration_ms": result.duration_ms,
                    },
                    indent=2,
                )
            )
    elif args.all:
        mw = METAWORMS()
        mw.run_all()
    elif args.report:
        mw = METAWORMS()
        mw.run_all()
    else:
        print("METAWORMS - Meta Worm Management System")
        print()
        print("Usage:")
        print("  python METAWORMS.py --all      Run all meta worms")
        print("  python METAWORMS.py --worm METAZ   Run specific worm")
        print("  python METAWORMS.py --status  Show status")
        print("  python METAWORMS.py --popup   Show popup window")
        print("  python METAWORMS.py --report  Run all and generate HTML report")
        print()
        print("Available Worms:")
        for name in METAWORMS.WORMS.keys():
            print(f"  - {name}")


if __name__ == "__main__":
    main()


