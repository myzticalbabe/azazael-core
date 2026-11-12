# coding: utf-8
"""
METAWORMS - Meta Worm Management System

Central management for all meta worms that crawl and analyze the Azazael ecosystem.

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
"""

import sys
import json
import ast
import hashlib
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


class METAZ:
    """METAZ - Metadata Extractor Worm."""

    VERSION = "1.0.0"

    def __init__(self):
        self.results = []

    def run(self) -> WormResult:
        """Extract metadata from all files."""
        start = time.time()
        files = 0
        items = 0

        metadata = {}

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
                        metadata[str(f)] = {
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                            "ctime": stat.st_ctime,
                            "checksum": self._checksum(f),
                            "extension": f.suffix,
                        }
                        items += 1
                    except Exception as exc:
                        note_failure("METAWORMS:L125", exc)

        return WormResult(
            name="METAZ",
            success=True,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"metadata": metadata},
        )

    def _checksum(self, path: Path) -> str:
        """Compute file checksum."""
        try:
            return hashlib.md5(path.read_bytes()).hexdigest()[:16]
        except Exception as exc:
            note_failure("METAWORMS:L141", exc)
            return ""


class DEPENDZ:
    """DEPENDZ - Dependency Mapper Worm."""

    VERSION = "1.0.0"

    def run(self) -> WormResult:
        """Map import dependencies."""
        start = time.time()
        files = 0
        items = 0

        dependencies = defaultdict(list)

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
                            deps = line.strip().split()
                            if len(deps) >= 2:
                                dep = deps[1].split(".")[0]
                                dependencies[str(f)].append(dep)
                                items += 1
                except Exception as exc:
                    note_failure("METAWORMS:L181", exc)

        return WormResult(
            name="DEPENDZ",
            success=True,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"dependencies": dict(dependencies)},
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
                    items += 1
                except SyntaxError as e:
                    note_failure("METAWORMS:L221", e)
                    errors.append(f"{f}:{e.lineno} - {e.msg}")
                except Exception as exc:
                    note_failure("METAWORMS:L223", exc)

        return WormResult(
            name="VALIDZ",
            success=len(errors) == 0,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"valid": items, "errors": errors},
            errors=errors,
        )


class DEADZ:
    """DEADZ - Dead Code Detector Worm."""

    VERSION = "1.0.0"

    def run(self) -> WormResult:
        """Find unused code."""
        start = time.time()
        files = 0
        items = 0

        # Simple dead code detection
        unused = []

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

                    # Check for __pycache__ references
                    if "__pycache__" in content:
                        unused.append((str(f), "__pycache__ reference"))

                    # Check for debug prints
                    for i, line in enumerate(content.split("\n"), 1):
                        if 'print("DEBUG' in line or "print('DEBUG" in line:
                            unused.append((str(f), f"Line {i}: debug print"))

                    items += 1
                except Exception as exc:
                    note_failure("METAWORMS:L275", exc)

        return WormResult(
            name="DEADZ",
            success=True,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"unused": unused},
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
            "total_lines": 0,
            "code_lines": 0,
            "comment_lines": 0,
            "blank_lines": 0,
            "classes": 0,
            "functions": 0,
            "imports": 0,
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

                    metrics["total_lines"] += len(content.split("\n"))

                    for line in content.split("\n"):
                        stripped = line.strip()
                        if not stripped:
                            metrics["blank_lines"] += 1
                        elif stripped.startswith("#"):
                            metrics["comment_lines"] += 1
                        else:
                            metrics["code_lines"] += 1

                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ClassDef):
                            metrics["classes"] += 1
                        elif isinstance(node, ast.FunctionDef):
                            metrics["functions"] += 1

                    items += 1
                except Exception as exc:
                    note_failure("METAWORMS:L342", exc)

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

        logs = defaultdict(int)

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
                            if "ERROR" in line:
                                logs["ERROR"] += 1
                            elif "WARNING" in line:
                                logs["WARNING"] += 1
                            elif "INFO" in line:
                                logs["INFO"] += 1
                            items += 1
                except Exception as exc:
                    note_failure("METAWORMS:L386", exc)

        return WormResult(
            name="LOGZ",
            success=True,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"log_counts": dict(logs)},
        )


class CHANGEZ:
    """CHANGEZ - Change Tracker Worm."""

    VERSION = "1.0.0"

    def run(self) -> WormResult:
        """Track file changes."""
        start = time.time()
        files = 0
        items = 0

        changes = {}

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
                        changes[str(f)] = {
                            "mtime": stat.st_mtime,
                            "size": stat.st_size,
                            "recent": stat.st_mtime > (time.time() - 86400),
                        }
                        if changes[str(f)]["recent"]:
                            items += 1
                    except Exception as exc:
                        note_failure("METAWORMS:L431", exc)

        return WormResult(
            name="CHANGEZ",
            success=True,
            files_processed=files,
            items_found=items,
            duration_ms=(time.time() - start) * 1000,
            output={"changes": changes, "recent_count": items},
        )


class METAWORMS:
    """Main meta worms management system."""

    VERSION = "1.0.0"
    WORMS = {
        "METAZ": METAZ,
        "DEPENDZ": DEPENDZ,
        "VALIDZ": VALIDZ,
        "DEADZ": DEADZ,
        "METRICZ": METRICZ,
        "LOGZ": LOGZ,
        "CHANGEZ": CHANGEZ,
    }

    def __init__(self):
        self.results: dict[str, WormResult] = {}

    def run_all(self) -> dict[str, WormResult]:
        """Run all meta worms."""
        print("=" * 70)
        print("  METAWORMS - Meta Worm Management System")
        print("=" * 70)
        print(f"Start: {datetime.now().isoformat()}")
        print(f"Worms: {len(self.WORMS)}")
        print("=" * 70)
        print()

        for name, worm_class in self.WORMS.items():
            print(f"[{name}] Running...", end=" ")
            sys.stdout.flush()

            worm = worm_class()
            result = worm.run()
            self.results[name] = result

            status = "OK" if result.success else "FAIL"
            print(
                f"[{status}] {result.files_processed} files, {result.items_found} items, {result.duration_ms:.0f}ms"
            )

        self._save_results()

        print()
        print("=" * 70)
        print("  METAWORMS COMPLETE")
        print("=" * 70)
        for name, result in self.results.items():
            status = "OK" if result.success else "FAIL"
            print(f"  [{status}] {name}: {result.items_found} items")

        return self.results

    def run(self, worm_name: str) -> (WormResult | None):
        """Run specific meta worm."""
        if worm_name not in self.WORMS:
            print(f"Unknown worm: {worm_name}")
            return None

        print(f"Running {worm_name}...")
        worm = self.WORMS[worm_name]()
        result = worm.run()
        self.results[worm_name] = result
        return result

    def _save_results(self):
        """Save all results."""
        Path(MetaWormConfig.OUTPUT_PATH).mkdir(parents=True, exist_ok=True)

        for name, result in self.results.items():
            output_file = Path(MetaWormConfig.OUTPUT_PATH) / f"{name.lower()}.json"
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
                    },
                    f,
                    indent=2,
                )

        # Save summary
        summary_file = Path(MetaWormConfig.OUTPUT_PATH) / "summary.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": datetime.now().isoformat(),
                    "worms_run": len(self.results),
                    "results": {
                        name: {
                            "success": r.success,
                            "items": r.items_found,
                            "duration_ms": r.duration_ms,
                        }
                        for name, r in self.results.items()
                    },
                },
                f,
                indent=2,
            )

    def get_status(self) -> dict[str, Any]:
        """Get meta worms status."""
        return {
            "version": self.VERSION,
            "worms_available": list(self.WORMS.keys()),
            "worms_run": list(self.results.keys()),
            "output_path": MetaWormConfig.OUTPUT_PATH,
        }


def run_popup():
    """Run meta worms with popup window."""
    root = tk.Tk()
    root.title("METAWORMS - Meta Worm Management")
    root.geometry("900x700")
    root.configure(bg="#1a1a2e")

    header = tk.Frame(root, bg="#16213e", pady=10)
    header.pack(fill="x")

    tk.Label(
        header,
        text="METAWORMS",
        font=("Consolas", 28, "bold"),
        bg="#16213e",
        fg="#00d4ff",
    ).pack()

    status_label = tk.Label(
        header,
        text="Initializing...",
        font=("Consolas", 12),
        bg="#16213e",
        fg="#7c3aed",
    )
    status_label.pack()

    progress = ttk.Progressbar(root, mode="determinate", length=850)
    progress.pack(pady=10, padx=25)

    text_area = scrolledtext.ScrolledText(
        root, wrap="word", font=("Consolas", 10), bg="#0f0f1a", fg="#00ff88", height=35
    )
    text_area.pack(fill="both", expand=True, padx=10, pady=10)

    def log(msg):
        text_area.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        text_area.see("end")
        root.update_idletasks()

    def run_worms():
        log("=" * 60)
        log("METAWORMS - Meta Worm Management System")
        log("=" * 60)

        metaworms = METAWORMS()
        worms = list(metaworms.WORMS.keys())

        for i, name in enumerate(worms):
            status_label.config(text=f"Running {name}...")
            progress["value"] = ((i + 1) / len(worms)) * 100
            root.update_idletasks()

            log(f"[{name}] Starting...")
            result = metaworms.run(name)
            if result is None:
                log(f"[{name}] [FAIL] no result returned")
                continue
            status = "OK" if result.success else "FAIL"
            log(
                f"[{name}] [{status}] {result.files_processed} files, {result.items_found} items"
            )

        log("")
        log("=" * 60)
        log("METAWORMS COMPLETE")
        log("=" * 60)

        status_label.config(text="Complete!")
        progress["value"] = 100

        log(f"Results saved to: {MetaWormConfig.OUTPUT_PATH}")

        log("Popup will close in 5 seconds...")
        root.after(5000, root.destroy)

    log("Starting meta worms...")
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
    else:
        print("METAWORMS - Meta Worm Management System")
        print()
        print("Usage:")
        print("  python METAWORMS.py --all      Run all meta worms")
        print("  python METAWORMS.py --worm METAZ   Run specific worm")
        print("  python METAWORMS.py --status  Show status")
        print("  python METAWORMS.py --popup   Show popup window")
        print()
        print("Available Worms:")
        for name in METAWORMS.WORMS.keys():
            print(f"  - {name}")


if __name__ == "__main__":
    main()


