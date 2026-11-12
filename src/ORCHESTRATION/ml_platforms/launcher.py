# coding: utf-8
"""
Launcher

Module for ml_platforms functionality.

Functions:
    - main()

Usage:
    >>> import launcher
    >>> launcher.main()

Author: Auto-generated
Date: 2026-02-10
"""

#!/usr/bin/env python3
"""
ML Platform Session Launcher
Unified entry point for ML platform configuration and diagnostics.
"""

from .config import (
    load_config,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def main():
    config = load_config()
    if not config:
        print("ERROR: Could not load ml_platform_config.json")
        return 1

        total = config["learning_protocol"]["total_platforms"]

        print("=" * 50)
        print("KILO ML SESSION LAUNCHER")
        print("=" * 50)
        print(f"\nLoaded {total} ML Platforms:\n")

        for env_name, env_data in config["python_environments"].items():
            print(f"[{env_name}]")
            for pkg in env_data["packages"]:
                v = pkg["version"] or "N/A"
                print(f"  {pkg['name']:<20} v{v:<10} ({pkg['category']})")
                print()

                print("Recommended Workflows:")
                for wf in config["learning_protocol"]["recommended_workflows"]:
                    print(f"  - {wf['name']}")
                    print()

                    print("Available Functions:")
                    print("  list_platforms()        - list all platforms")
                    print("  list_platforms('env')    - Filter by environment")
                    print("  list_workflows()        - Show workflow details")
                    print("  get_env_path('name')    - Get environment path")
                    print("  check_platform_status()  - Check imports")
                    print()
                    print("=" * 50)
                    print("Config ready. Starting Kilo session...")
                    print("=" * 50)
                    return 0


if __name__ == "__main__":
    exit(main())
