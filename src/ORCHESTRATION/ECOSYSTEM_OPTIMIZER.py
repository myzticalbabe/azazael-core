# coding: utf-8
"""
Azazael ECOSYSTEM OPTIMIZER - Uses PYTHON modules to optimize batch operations

This module provides unified Python-based execution replacing the 40 redundant .bat files.

Usage:
    >>> from ecosystem_optimizer import AzazaelOptimizer
    >>> optimizer = AzazaelOptimizer()
    >>> optimizer.optimize_all()
    >>> optimizer.run_script("auto_install")
"""

import sys
import json
import time
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Dynamic path resolution
try:
    from CORE.paths import get_src_dir
except ImportError as exc:
    note_failure("ECOSYSTEM_OPTIMIZER:L27", exc)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from CORE.paths import get_src_dir


SCRIPT_MAP = {
    "auto_install": "ml_platforms/auto_install.py",
    "diagnostics": "ml_platforms/diagnostics.py",
    "health_monitor": "ml_platforms/health_monitor.py",
    "verify_moe": "ml_platforms/verify_moe.py",
    "convert": "ml_platforms/convert.py",
    "set_pythonpath": "ml_platforms/SET_PYTHONPATH.bat",
    "install_as_admin": "ml_platforms/INSTALL_AS_ADMIN.bat",
    "admin_activator": "PERSEPHONE/admin_activator.bat",
    "sync_azazael": "PERSEPHONE/sync_azazael.bat",
    "bat_analysis": "SHORTY/bat_analysis.bat",
    "check_external_links": "SHORTY/check_external_links.bat",
    "complete_analysis": "SHORTY/complete_analysis.bat",
    "converter": "SHORTY/converter.bat",
    "cross_platform": "SHORTY/cross_platform.bat",
    "docs_all_checks": "SHORTY/docs_all_checks.bat",
    "docs_full_report": "SHORTY/docs_full_report.bat",
    "framework_activator": "SHORTY/framework_activator.bat",
    "generate_api_docs": "SHORTY/generate_api_docs.bat",
    "generate_search_index": "SHORTY/generate_search_index.bat",
    "harness": "SHORTY/harness.bat",
    "install_debug": "SHORTY/install_debug.bat",
    "install_manager": "SHORTY/install_manager.bat",
    "launcher": "SHORTY/launcher.bat",
    "lm_studio": "SHORTY/lm_studio.bat",
    "run_analysis": "SHORTY/run_analysis.bat",
    "run_qanalysis": "SHORTY/run_qanalysis.bat",
    "run_qanalysis_full": "SHORTY/run_qanalysis_full.bat",
    "unified_analysis": "SHORTY/unified_analysis.bat",
    "validate_code_examples": "SHORTY/validate_code_examples.bat",
    "validate_docs": "SHORTY/validate_docs.bat",
}


@dataclass
class OptimizationResult:
    script: str
    success: bool
    duration_ms: float
    output: str
    error: str


class AzazaelOptimizer:
    """
    Unified Python optimizer replacing 40+ batch files.

    Benefits:
        - Single point of entry for all operations
        - Centralized logging and error handling
        - No batch file redundancy
        - Better error handling and output formatting
    """

    BASE_DIR = str(get_src_dir() / "ORCHESTRATION")
    VERSION = "1.0.0"

    def __init__(self):
        self.base_path = Path(self.BASE_DIR)
        self.log_dir = self.base_path / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.results: list[OptimizationResult] = []

    def _log(self, script: str, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] [{level}] {script}: {message}\n"
        log_file = self.log_dir / "optimizer.log"
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(log_entry)
        print(log_entry.strip())

    def _run_python_script(
        self, script_path: str, args: list[str] | None = None
    ) -> OptimizationResult:
        start = time.time()

        try:
            cmd = [sys.executable, str(self.base_path / script_path)] + (args or [])
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

            return OptimizationResult(
                script=script_path,
                success=result.returncode == 0,
                duration_ms=(time.time() - start) * 1000,
                output=result.stdout[:5000],
                error=result.stderr if result.returncode != 0 else "",
            )
        except Exception as e:
            note_failure("ECOSYSTEM_OPTIMIZER:L119", e)
            return OptimizationResult(
                script=script_path,
                success=False,
                duration_ms=(time.time() - start) * 1000,
                output="",
                error=str(e),
            )

    def _run_batch_script(
        self, script_path: str, args: list[str] | None = None
    ) -> OptimizationResult:
        start = time.time()

        try:
            full_path = self.base_path / script_path
            if not full_path.exists():
                return OptimizationResult(
                    script=script_path,
                    success=False,
                    duration_ms=0,
                    output="",
                    error=f"Script not found: {full_path}",
                )

            cmd = [str(full_path)] + (args or [])
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

            return OptimizationResult(
                script=script_path,
                success=result.returncode == 0,
                duration_ms=(time.time() - start) * 1000,
                output=result.stdout[:5000],
                error=result.stderr if result.returncode != 0 else "",
            )
        except Exception as e:
            note_failure("ECOSYSTEM_OPTIMIZER:L154", e)
            return OptimizationResult(
                script=script_path,
                success=False,
                duration_ms=(time.time() - start) * 1000,
                output="",
                error=str(e),
            )

    def run_script(
        self, script_name: str, args: list[str] | None = None
    ) -> OptimizationResult:
        """Run a script by name using unified optimizer."""
        script_name = script_name.lower().replace(".bat", "")

        if script_name not in SCRIPT_MAP:
            return OptimizationResult(
                script=script_name,
                success=False,
                duration_ms=0,
                output="",
                error=f"Unknown script: {script_name}",
            )

        script_path = SCRIPT_MAP[script_name]

        self._log(script_name, f"Starting execution: {script_path}")

        if script_path.endswith(".py"):
            result = self._run_python_script(script_path, args)
        else:
            result = self._run_batch_script(script_path, args)

        self.results.append(result)

        status = "SUCCESS" if result.success else "FAILED"
        self._log(script_name, f"{status} in {result.duration_ms:.0f}ms")

        return result

    def run_all(self) -> dict[str, Any]:
        """Run all scripts and generate optimization report."""
        start = time.time()

        report = {
            "timestamp": datetime.now().isoformat(),
            "base_directory": self.BASE_DIR,
            "scripts_count": len(SCRIPT_MAP),
            "results": {},
            "summary": {
                "total": len(SCRIPT_MAP),
                "success": 0,
                "failed": 0,
                "total_duration_ms": 0,
            },
        }

        for script_name, script_path in SCRIPT_MAP.items():
            print(f"\n[{'=' * 60}]")
            print(f"Running: {script_name}")
            print(f"Path: {script_path}")

            result = self.run_script(script_name)

            report["results"][script_name] = {
                "path": script_path,
                "success": result.success,
                "duration_ms": result.duration_ms,
                "error": result.error[:500] if result.error else None,
            }

            report["summary"]["total_duration_ms"] += result.duration_ms
            if result.success:
                report["summary"]["success"] += 1
            else:
                report["summary"]["failed"] += 1

            status_icon = "\u2713" if result.success else "\u2717"
            print(f"[{status_icon}] {script_name}: {result.duration_ms:.0f}ms")
            if result.error:
                print(f"    Error: {result.error[:800]}...")

        report["total_duration_ms"] = (time.time() - start) * 1000
        report["summary"]["avg_duration_ms"] = report["total_duration_ms"] / len(
            SCRIPT_MAP
        )

        self._save_report(report)

        return report

    def _save_report(self, report: dict[str, Any]):
        report_path = self.base_path / "OPTIMIZATION_REPORT.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\n{'=' * 60}")
        print(f"Report saved: {report_path}")

    def list_scripts(self) -> list[str]:
        """list all available scripts."""
        return list(SCRIPT_MAP)

    def get_status(self) -> dict[str, Any]:
        """Get optimizer status."""
        return {
            "version": self.VERSION,
            "base_directory": self.BASE_DIR,
            "scripts_count": len(SCRIPT_MAP),
            "python_scripts": len(
                [s for s in SCRIPT_MAP.values() if s.endswith(".py")]
            ),
            "batch_scripts": len(
                [s for s in SCRIPT_MAP.values() if s.endswith(".bat")]
            ),
            "results_collected": len(self.results),
        }

    def print_status(self):
        """Print current status."""
        status = self.get_status()

        print("\n" + "=" * 60)
        print("  Azazael OPTIMIZER STATUS")
        print("=" * 60)
        print(f"  Version: {status['version']}")
        print(f"  Base Directory: {status['base_directory']}")
        print(f"  Total Scripts: {status['scripts_count']}")
        print(f"    - Python: {status['python_scripts']}")
        print(f"    - Batch: {status['batch_scripts']}")
        print(f"  Results Collected: {status['results_collected']}")
        print("=" * 60 + "\n")

    def optimize_ecosystem(self) -> dict[str, Any]:
        """
        Run comprehensive ecosystem optimization.

        This replaces all 40+ batch files with unified Python execution.
        """
        print("\n" + "=" * 60)
        print("  Azazael ECOSYSTEM OPTIMIZATION")
        print("=" * 60)

        self._log("OPTIMIZER", "Starting ecosystem optimization")

        status = self.get_status()
        print(f"\nFound {status['scripts_count']} scripts to optimize")
        print(f"  Python scripts: {status['python_scripts']}")
        print(f"  Batch scripts: {status['batch_scripts']}")

        print("\n[1/3] Running Python scripts...")
        py_results = []
        for script_name, script_path in SCRIPT_MAP.items():
            if script_path.endswith(".py"):
                result = self.run_script(script_name)
                py_results.append(result)

        print("\n[2/3] Running batch scripts...")
        bat_results = []
        for script_name, script_path in SCRIPT_MAP.items():
            if script_path.endswith(".bat"):
                result = self.run_script(script_name)
                bat_results.append(result)

        print("\n[3/3] Generating optimization report...")

        report = {
            "timestamp": datetime.now().isoformat(),
            "optimization_type": "full_ecosystem",
            "scripts_analyzed": len(SCRIPT_MAP),
            "python_results": {
                "total": len(py_results),
                "success": sum(1 for r in py_results if r.success),
                "failed": sum(1 for r in py_results if not r.success),
                "total_duration_ms": sum(r.duration_ms for r in py_results),
            },
            "batch_results": {
                "total": len(bat_results),
                "success": sum(1 for r in bat_results if r.success),
                "failed": sum(1 for r in bat_results if not r.success),
                "total_duration_ms": sum(r.duration_ms for r in bat_results),
            },
            "recommendations": [
                "Replace batch files with Python wrappers for better error handling",
                "Use unified logger instead of timestamp logging in each file",
                "Centralize script mapping for easier maintenance",
                "Add progress callbacks for long-running operations",
            ],
        }

        self._save_report(report)

        print(f"\n{'=' * 60}")
        print("  OPTIMIZATION COMPLETE")
        print(f"{'=' * 60}")
        print(
            f"  Python Scripts: {report['python_results']['success']}/{report['python_results']['total']} successful"
        )
        print(
            f"  Batch Scripts: {report['batch_results']['success']}/{report['batch_results']['total']} successful"
        )
        print("=" * 60 + "\n")

        return report


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Azazael Ecosystem Optimizer")
    parser.add_argument("--script", "-s", help="Run specific script")
    parser.add_argument("--list", "-l", action="store_true", help="list all scripts")
    parser.add_argument("--all", "-a", action="store_true", help="Run all scripts")
    parser.add_argument(
        "--optimize", "-o", action="store_true", help="Run full optimization"
    )
    parser.add_argument("--status", action="store_true", help="Show status")

    args = parser.parse_args()

    optimizer = AzazaelOptimizer()

    if args.status:
        optimizer.print_status()
    elif args.list:
        print("\nAvailable Scripts:")
        for script in optimizer.list_scripts():
            print(f"  - {script}")
    elif args.script:
        result = optimizer.run_script(args.script)
        print(f"\nResult: {'SUCCESS' if result.success else 'FAILED'}")
        print(f"Duration: {result.duration_ms:.0f}ms")
        if result.error:
            print(f"Error: {result.error}")
    elif args.all:
        report = optimizer.run_all()
        print(
            f"\nTotal: {report['summary']['success']}/{report['summary']['total']} successful"
        )
    elif args.optimize:
        optimizer.optimize_ecosystem()
    else:
        optimizer.print_status()
        print("\nUsage:")
        print("  python ecosystem_optimizer.py --status       Show status")
        print("  python ecosystem_optimizer.py --list         list scripts")
        print("  python ecosystem_optimizer.py -s <script>   Run specific script")
        print("  python ecosystem_optimizer.py -a            Run all scripts")
        print("  python ecosystem_optimizer.py -o            Full optimization")


if __name__ == "__main__":
    main()
