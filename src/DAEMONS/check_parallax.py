#!/usr/bin/env python3
"""
check_parallax.py — MANDATORY PRE-FLIGHT CHECK

This script MUST be run as Step 0 of SESSION_STARTUP.md before any Codebuff
session work begins. It verifies that the Parallax Validator Daemon is running.

Exit codes:
    0 = Daemon is running — session may proceed
    1 = Daemon is NOT running — session MUST NOT proceed
    2 = Daemon is installed but not running (needs restart)
    3 = Daemon is not installed at all

The LLM should refuse to proceed with any file modifications if this check
fails. This is the architectural enforcement that makes the gate mandatory.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

_STARTUP_NAME = "AzazaelParallaxValidator.bat"


def get_startup_dir() -> Path:
    """Get the Windows Startup folder path."""
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def check_daemon_running() -> bool:
    """Check if the parallax_validator process is currently running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        return "parallax_validator" in result.stdout
    except Exception as exc:
        note_failure("check_parallax:L44", exc)
        return False


def check_startup_installed() -> bool:
    """Check if the launcher exists in the Windows Startup folder."""
    launcher_path = get_startup_dir() / _STARTUP_NAME
    return launcher_path.exists()


def main():
    daemon_running = check_daemon_running()
    startup_installed = check_startup_installed()

    if daemon_running:
        print("[PARALLAX CHECK] [OK] Daemon is RUNNING -- session may proceed.")
        sys.exit(0)

    if startup_installed:
        print("[PARALLAX CHECK] [FAIL] Daemon is INSTALLED but NOT RUNNING.")
        print("[PARALLAX CHECK]    The daemon should auto-start on login.")
        print("[PARALLAX CHECK]    To start it now:")
        print("[PARALLAX CHECK]      azazael\\start_parallax_daemon.bat")
        print("[PARALLAX CHECK]    Or:")
        print("[PARALLAX CHECK]      start \"Parallax Validator\" python azazael/src/DAEMONS/parallax_validator.py --watch")
        print("[PARALLAX CHECK] ** SESSION MUST NOT PROCEED UNTIL DAEMON IS RUNNING **")
        sys.exit(2)

    # Not installed at all
    print("[PARALLAX CHECK] [FAIL] Daemon is NOT INSTALLED.")
    print("[PARALLAX CHECK]    The Pre-Action Gate is architecturally mandatory.")
    print("[PARALLAX CHECK]    Install it once:")
    print("[PARALLAX CHECK]      python azazael/src/DAEMONS/install_parallax_startup.py")
    print("[PARALLAX CHECK] ** SESSION MUST NOT PROCEED UNTIL DAEMON IS INSTALLED **")
    sys.exit(3)


if __name__ == "__main__":
    main()
