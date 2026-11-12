import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "ml_platforms"))

# coding: utf-8
"""
HYDRA Cluster Auto-Protocol Launcher
=====================================
Unified entry point for all HYDRA cluster auto-protocols and conflict resolution.

Usage:
    # Run all protocols
    python hydra_launcher.py run

    # Run specific protocol
    python hydra_launcher.py run --spelling
    python hydra_launcher.py run --paths
    python hydra_launcher.py run --python
    python hydra_launcher.py run --conflicts

    # Analyze only
    python hydra_launcher.py analyze

    # Show status
    python hydra_launcher.py status
"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime
# from typing import Dict, List  # removed — all names have native equivalents
from dataclasses import dataclass, field
import argparse
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

ROOT = Path(__file__).parent.parent
HYDRA_ROOT = Path(__file__).parent


@dataclass
class ProtocolResult:
    """Result of running a protocol."""

    name: str
    success: bool
    duration_ms: float
    conflicts_resolved: int = 0
    files_modified: int = 0
    output: str = ""
    errors: list[str] = field(default_factory=list)


class HydraLauncher:
    """Launcher for HYDRA cluster auto-protocols."""

    VERSION = "1.0.0"

    def __init__(self):
        self.results: list[ProtocolResult] = []
        self.python = self._find_python()

    def _find_python(self) -> Path:
        """Find the correct Python interpreter."""
        venv_python = HYDRA_ROOT / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return venv_python
        return Path(sys.executable)

    def run_spelling_fix(self) -> ProtocolResult:
        """Run spelling correction protocol."""
        start = time.time()
        result = ProtocolResult(name="spelling_fix", success=True, duration_ms=0)

        try:
            from hydra_protocols import HydraProtocols

            protocols = HydraProtocols(str(ROOT))
            protocol_result = protocols.fix_spelling_errors()
            result.conflicts_resolved = protocol_result.conflicts_resolved
            result.files_modified = len(protocol_result.files_modified)
        except Exception as e:
            note_failure("hydra_launcher:L83", e)
            result.success = False
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def run_path_fix(self) -> ProtocolResult:
        """Run path reference fix protocol."""
        start = time.time()
        result = ProtocolResult(name="path_fix", success=True, duration_ms=0)

        try:
            from hydra_protocols import HydraProtocols

            protocols = HydraProtocols(str(ROOT))
            protocol_result = protocols.fix_old_paths()
            result.conflicts_resolved = protocol_result.conflicts_resolved
            result.files_modified = len(protocol_result.files_modified)
        except Exception as e:
            note_failure("hydra_launcher:L102", e)
            result.success = False
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def run_python_resolution(self) -> ProtocolResult:
        """Run Python path resolution protocol."""
        start = time.time()
        result = ProtocolResult(name="python_resolution", success=True, duration_ms=0)

        try:
            from hydra_protocols import HydraProtocols

            protocols = HydraProtocols(str(ROOT))
            protocol_result = protocols.resolve_python_conflict()
            result.conflicts_resolved = protocol_result.conflicts_resolved
        except Exception as e:
            note_failure("hydra_launcher:L120", e)
            result.success = False
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def run_terence_analysis(self) -> ProtocolResult:
        """Run TERENCE enhanced conflict analysis."""
        start = time.time()
        result = ProtocolResult(name="terence_analysis", success=True, duration_ms=0)

        try:
            sys.path.insert(0, str(ROOT / "UTILITIES" / "TERENCE"))
            from terence_enhanced import TerenceEnhanced

            terence = TerenceEnhanced(str(ROOT))
            report = terence.analyze_conflicts()
            result.conflicts_resolved = report.total_conflicts
        except Exception as e:
            note_failure("hydra_launcher:L139", e)
            result.success = False
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def run_tarbaby_analysis(self) -> ProtocolResult:
        """Run TARBABY enhanced conflict analysis."""
        start = time.time()
        result = ProtocolResult(name="tarbaby_analysis", success=True, duration_ms=0)

        try:
            tarbaby_path = str(ROOT / "UTILITIES" / "TARBABY")
            sys.path.insert(0, tarbaby_path)
            from tarbaby_enhanced import TarbabyEnhanced

            tarbaby = TarbabyEnhanced(tarbaby_path)
            report = tarbaby.analyze_binary_conflicts()
            result.conflicts_resolved = report.conflicts_found
        except Exception as e:
            note_failure("hydra_launcher:L159", e)
            result.success = False
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def run_ml_framework_check(self) -> ProtocolResult:
        """Run AUTO_DOWNLOADER ML framework check and auto-install."""
        start = time.time()
        result = ProtocolResult(name="ml_framework_check", success=True, duration_ms=0)

        try:
            sys.path.insert(0, str(ROOT / "CODEX" / "CORE"))
            from AUTO_DOWNLOADER import AutoDownloader

            downloader = AutoDownloader(str(ROOT / "CODEX" / "CORE"))
            missing = downloader.detect_missing()

            result.output = f"Missing frameworks: {len(missing)}"

            if missing:
                print(f"\n[INFO] Found {len(missing)} missing frameworks")
                for plugin in missing[:5]:
                    print(f"  - {plugin.info.name}")

            status = downloader.get_status()
            result.conflicts_resolved = status.get("frameworks_installed", 0)
            result.output = json.dumps(status)

        except Exception as e:
            note_failure("hydra_launcher:L189", e)
            result.success = False
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def run_ml_optimization(self) -> ProtocolResult:
        """Run full ML optimization: frameworks + clustering + HYDRA protocols."""
        start = time.time()
        result = ProtocolResult(name="ml_optimization", success=True, duration_ms=0)

        try:
            print()
            print("=" * 70)
            print("  HYDRA ML OPTIMIZATION PROTOCOL")
            print("=" * 70)

            sys.path.insert(0, str(ROOT / "CODEX" / "CORE"))
            from AUTO_DOWNLOADER import AutoDownloader
            from hydra_protocols import HydraProtocols

            downloader = AutoDownloader(str(ROOT / "CODEX" / "CORE"))
            protocols = HydraProtocols(str(ROOT))

            print("\n[1/3] Checking ML Frameworks...")
            missing = downloader.detect_missing()
            print(f"      Found {len(missing)} missing frameworks")

            print("\n[2/3] Running cluster protocols...")
            pf_result = protocols.fix_spelling_errors()
            path_result = protocols.fix_old_paths()

            print("\n[3/3] Generating optimization report...")
            ml_status = downloader.get_status()

            result.conflicts_resolved = (
                pf_result.conflicts_resolved
                + path_result.conflicts_resolved
                + ml_status.get("frameworks_installed", 0)
            )
            result.output = json.dumps(
                {
                    "frameworks": ml_status,
                    "spelling_fixed": pf_result.conflicts_resolved,
                    "paths_fixed": path_result.conflicts_resolved,
                }
            )

        except Exception as e:
            note_failure("hydra_launcher:L238", e)
            result.success = False
            result.errors.append(str(e))

        result.duration_ms = (time.time() - start) * 1000
        return result

    def run_all_protocols(self) -> list[ProtocolResult]:
        """Run all HYDRA cluster protocols."""
        print()
        print("=" * 70)
        print("  HYDRA CLUSTER AUTO-PROTOCOLS - FULL SUITE")
        print("=" * 70)
        print(f"Python: {self.python}")
        print(f"Root: {ROOT}")
        print()

        self.results = [
            self.run_spelling_fix(),
            self.run_path_fix(),
            self.run_python_resolution(),
            self.run_ml_framework_check(),
            self.run_terence_analysis(),
            self.run_tarbaby_analysis(),
        ]

        total_resolved = sum(r.conflicts_resolved for r in self.results)
        total_files = sum(r.files_modified for r in self.results)
        successful = sum(1 for r in self.results if r.success)

        print()
        print("=" * 70)
        print("  PROTOCOL SUITE COMPLETE")
        print("=" * 70)
        print(f"Protocols Run: {len(self.results)}")
        print(f"Successful: {successful}/{len(self.results)}")
        print(f"Total Conflicts Found/Resolved: {total_resolved}")
        print(f"Files Modified: {total_files}")
        print("=" * 70)

        return self.results

    def analyze_only(self) -> dict:
        """Analyze conflicts without fixing."""
        print()
        print("=" * 70)
        print("  HYDRA CLUSTER - CONFLICT ANALYSIS ONLY")
        print("=" * 70)
        print()

        analysis = {
            "timestamp": datetime.now().isoformat(),
            "python": str(self.python),
            "python_exists": self.python.exists(),
            "venv_exists": (HYDRA_ROOT / ".venv" / "Scripts" / "python.exe").exists(),
            "protocols_available": [],
            "conflicts_by_source": {},
        }

        terence_path = ROOT / "UTILITIES" / "TERENCE" / "terence_enhanced.py"
        tarbaby_path = ROOT / "UTILITIES" / "TARBABY" / "tarbaby_enhanced.py"
        hydra_protocols_path = HYDRA_ROOT / "hydra_protocols.py"

        analysis["protocols_available"] = [
            str(terence_path) if terence_path.exists() else None,
            str(tarbaby_path) if tarbaby_path.exists() else None,
            str(hydra_protocols_path) if hydra_protocols_path.exists() else None,
        ]

        if terence_path.exists():
            try:
                sys.path.insert(0, str(ROOT / "UTILITIES" / "TERENCE"))
                from terence_enhanced import TerenceEnhanced

                terence = TerenceEnhanced(str(ROOT))
                report = terence.analyze_conflicts()
                analysis["conflicts_by_source"]["terence"] = {
                    "total": report.total_conflicts,
                    "by_category": report.conflicts_by_category,
                    "by_severity": report.conflicts_by_severity,
                }
            except Exception as e:
                note_failure("hydra_launcher:L319", e)
                analysis["conflicts_by_source"]["terence"] = {"error": str(e)}

        print(json.dumps(analysis, indent=2))
        return analysis

    def get_status(self) -> dict:
        """Get launcher status."""
        return {
            "version": self.VERSION,
            "python": str(self.python),
            "python_exists": self.python.exists(),
            "hydra_root": str(HYDRA_ROOT),
            "root": str(ROOT),
            "protocols": [
                "spelling_fix",
                "path_fix",
                "python_resolution",
                "ml_framework_check",
                "ml_optimization",
                "terence_analysis",
                "tarbaby_analysis",
            ],
        }

    def export_report(self, output_path: str):
        """Export full protocol report."""
        report = {
            "timestamp": datetime.now().isoformat(),
            "launcher_version": self.VERSION,
            "python": str(self.python),
            "results": [
                {
                    "name": r.name,
                    "success": r.success,
                    "duration_ms": r.duration_ms,
                    "conflicts_resolved": r.conflicts_resolved,
                    "files_modified": r.files_modified,
                    "errors": r.errors,
                }
                for r in self.results
            ],
            "summary": {
                "total_protocols": len(self.results),
                "successful": sum(1 for r in self.results if r.success),
                "total_conflicts": sum(r.conflicts_resolved for r in self.results),
                "total_files_modified": sum(r.files_modified for r in self.results),
            },
        }

        Path(output_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report exported to: {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="HYDRA Cluster Auto-Protocol Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"HYDRA Launcher v{HydraLauncher.VERSION}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    p_run = subparsers.add_parser("run", help="Run all protocols")
    p_run.add_argument("--spelling", action="store_true", help="Run spelling fix")
    p_run.add_argument("--paths", action="store_true", help="Run path fix")
    p_run.add_argument("--python", action="store_true", help="Run Python resolution")
    p_run.add_argument("--conflicts", action="store_true", help="Run conflict analysis")
    p_run.add_argument("--apply", action="store_true", help="Apply fixes")

    p_analyze = subparsers.add_parser("analyze", help="Analyze only (no fixes)")

    p_status = subparsers.add_parser("status", help="Show status")

    p_export = subparsers.add_parser("export", help="Export report")
    p_export.add_argument("output", help="Output file path")

    args = parser.parse_args()

    launcher = HydraLauncher()

    if args.command == "run":
        if args.spelling:
            result = launcher.run_spelling_fix()
            print(
                json.dumps({"result": result.name, "success": result.success}, indent=2)
            )
        elif args.paths:
            result = launcher.run_path_fix()
            print(
                json.dumps({"result": result.name, "success": result.success}, indent=2)
            )
        elif args.python:
            result = launcher.run_python_resolution()
            print(
                json.dumps({"result": result.name, "success": result.success}, indent=2)
            )
        elif args.conflicts:
            launcher.run_terence_analysis()
        else:
            launcher.run_all_protocols()

    elif args.command == "analyze":
        launcher.analyze_only()

    elif args.command == "status":
        print(json.dumps(launcher.get_status(), indent=2))

    elif args.command == "export":
        if not launcher.results:
            launcher.run_all_protocols()
        launcher.export_report(args.output)

    else:
        print("HYDRA Cluster Auto-Protocol Launcher")
        print()
        print("Usage:")
        print("  python hydra_launcher.py run              Run all protocols")
        print("  python hydra_launcher.py run --spelling   Spelling fix only")
        print("  python hydra_launcher.py run --paths      Path fix only")
        print("  python hydra_launcher.py run --python     Python resolution only")
        print("  python hydra_launcher.py run --conflicts  Conflict analysis")
        print("  python hydra_launcher.py analyze          Analyze only (no fixes)")
        print("  python hydra_launcher.py status           Show status")
        print("  python hydra_launcher.py export <file>    Export report")


if __name__ == "__main__":
    main()
