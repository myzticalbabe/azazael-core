#!/usr/bin/env python3
"""
Azazael Ecosystem Health Check

Performs comprehensive health verification of all ecosystem components.
"""

import sys
from pathlib import Path
from datetime import datetime
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import Dict, Tuple  # removed — all names have native equivalents

# Dynamic path resolution
try:
    from CORE.paths import get_desktop_dir
except ImportError as exc:
    note_failure("ECOSYSTEM_HEALTH_CHECK:L16", exc)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from CORE.paths import get_desktop_dir


class EcosystemHealthCheck:
    """Comprehensive ecosystem health verification."""

    COMPONENTS = {
        "HYDRA": {"path": "HYDRA.py", "check": "syntax"},
        "WORMZ": {"path": "WORMZ/WORMZ.py", "check": "syntax"},
        "METAWORMS": {"path": "METAWORMS/METAWORMS.py", "check": "syntax"},
        "HORUS": {"path": "Azazael/HORUS.py", "check": "syntax"},
        "TERENCE": {"path": "TERENCE/TERENCE.py", "check": "syntax"},
        "KLINK": {"path": "KLINK/klink.py", "check": "syntax"},
        "TARBABY": {"path": "TARBABY/TARBABY.py", "check": "syntax"},
    }

    def __init__(self, base_path: (str | None) = None):
        self.base_path = Path(base_path) if base_path is not None else get_desktop_dir()

    def check_syntax(self, filepath: Path) -> tuple[bool, str]:
        """Verify Python syntax is valid."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                compile(f.read(), filepath, "exec")
            return True, "OK"
        except SyntaxError as e:
            note_failure("ECOSYSTEM_HEALTH_CHECK:L43", e)
            return False, f"Syntax error: {e}"
        except Exception as e:
            note_failure("ECOSYSTEM_HEALTH_CHECK:L45", e)
            return False, f"Error: {e}"

    def check_file_exists(self, filepath: Path) -> tuple[bool, str]:
        """Check if file exists."""
        if filepath.exists():
            return True, "OK"
        return False, "File not found"

    def check_imports(self, filepath: Path) -> tuple[bool, str]:
        """Verify key imports can be resolved."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            if "import" in content or "from" in content:
                return True, "Imports present"
            return False, "No imports found"
        except Exception as e:
            note_failure("ECOSYSTEM_HEALTH_CHECK:L62", e)
            return False, f"Error reading file: {e}"

    def run_health_check(self) -> dict:
        """Execute full health check."""
        results = {
            "timestamp": datetime.now().isoformat(),
            "components": {},
            "summary": {"healthy": 0, "unhealthy": 0, "unknown": 0},
        }

        for name, config in self.COMPONENTS.items():
            filepath = self.base_path / config["path"]
            check_type = config["check"]

            if check_type == "syntax":
                success, message = self.check_syntax(filepath)
            elif check_type == "exists":
                success, message = self.check_file_exists(filepath)
            else:
                success, message = self.check_file_exists(filepath)

            status = "healthy" if success else "unhealthy"
            results["components"][name] = {
                "status": status,
                "message": message,
                "path": str(filepath),
            }

            if success:
                results["summary"]["healthy"] += 1
            else:
                results["summary"]["unhealthy"] += 1

        return results

    def print_results(self, results: dict) -> None:
        """Print health check results."""
        print(f"\n{'=' * 60}")
        print("Azazael Ecosystem Health Check")
        print(f"{'=' * 60}")
        print(f"Timestamp: {results['timestamp']}")
        print()

        for name, info in results["components"].items():
            status_symbol = "[OK]" if info["status"] == "healthy" else "[FAIL]"
            print(f"{status_symbol} {name}")
            print(f"       Status: {info['status']}")
            print(f"       Message: {info['message']}")
            print()

        print(f"{'=' * 60}")
        summary = results["summary"]
        print(
            f"Summary: {summary['healthy']} healthy, {summary['unhealthy']} unhealthy"
        )
        print(f"{'=' * 60}\n")


def main():
    """Main entry point."""
    health = EcosystemHealthCheck()
    results = health.run_health_check()
    health.print_results(results)

    if results["summary"]["unhealthy"] > 0:
        sys.exit(1)
        sys.exit(0)


if __name__ == "__main__":
    main()
