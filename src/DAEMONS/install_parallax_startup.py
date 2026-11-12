#!/usr/bin/env python3
"""
install_parallax_startup.py — Install the Parallax Validator Daemon for
automatic startup via the Windows Startup folder. NO ADMIN REQUIRED.

Creates a .bat launcher shortcut in:
    %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\
This ensures the daemon auto-starts whenever the user logs into Windows,
completely independent of any Codebuff session. The LLM cannot disable,
uninstall, or override this installation.

This makes the PRE-ACTION GATE ARCHITECTURALLY MANDATORY.

Usage:
    python install_parallax_startup.py              # Install
    python install_parallax_startup.py --uninstall   # Remove
    python install_parallax_startup.py --status      # Check
"""

import argparse
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

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WORKSPACE = _PROJECT_ROOT.parent  # C:/Users/clint/
_STARTUP_NAME = "AzazaelParallaxValidator.bat"


def get_startup_dir() -> Path:
    """Get the Windows Startup folder path (no admin required)."""
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def get_python_path() -> str:
    """Get the absolute path to the Python interpreter."""
    return sys.executable


def get_script_path() -> str:
    """Get the absolute path to parallax_validator.py."""
    parallax_script = _PROJECT_ROOT / "DAEMONS" / "parallax_validator.py"
    return str(parallax_script.resolve())


def install() -> bool:
    """Install the daemon launcher in the Windows Startup folder.

    Creates a .bat file that starts the daemon in its own minimized window.

    Returns:
        True if successfully installed.
    """
    startup_dir = get_startup_dir()
    startup_dir.mkdir(parents=True, exist_ok=True)

    python_path = get_python_path()
    script_path = get_script_path()
    workspace = str(_WORKSPACE.resolve())

    if not Path(script_path).exists():
        print(f"ERROR: parallax_validator.py not found at {script_path}", file=sys.stderr)
        return False

    # Create the launcher .bat file
    launcher_path = startup_dir / _STARTUP_NAME
    launcher_content = f"""@echo off
REM Azazael Parallax Validator Daemon — Auto-started on login
REM DO NOT DELETE — this is architecturally mandatory for the Pre-Action Gate
cd /d {workspace}
start "Parallax Validator" /MIN "{python_path}" "{script_path}" --watch
"""

    try:
        launcher_path.write_text(launcher_content, encoding="utf-8")
        print(f"[INSTALL] Launcher created: {launcher_path}")
        print(f"[INSTALL] Daemon will auto-start on next login.")
    except OSError as e:
        print(f"[INSTALL] ERROR: Cannot write to Startup folder: {e}", file=sys.stderr)
        return False

    # Try to start immediately
    try:
        subprocess.Popen(
            [python_path, script_path, "--watch"],
            cwd=str(workspace),
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[INSTALL] Daemon started immediately (new console window).")
    except Exception as e:
        print(f"[INSTALL] Daemon scheduled for next login. "
              f"(Could not start immediately: {e})")

    return True


def uninstall() -> bool:
    """Remove the launcher from the Startup folder."""
    startup_dir = get_startup_dir()
    launcher_path = startup_dir / _STARTUP_NAME

    if launcher_path.exists():
        try:
            launcher_path.unlink()
            print(f"[UNINSTALL] Removed: {launcher_path}")
        except OSError as e:
            print(f"[UNINSTALL] ERROR: Cannot remove: {e}", file=sys.stderr)
            return False
    else:
        print(f"[UNINSTALL] Launcher was not installed.")

    return True


def check_status() -> dict:
    """Check installation and running status.

    Returns:
        dict with status details.
    """
    status = {
        "startup_installed": False,
        "daemon_running": False,
        "startup_path": str(get_startup_dir() / _STARTUP_NAME),
    }

    # Check if launcher exists in Startup folder
    launcher_path = get_startup_dir() / _STARTUP_NAME
    status["startup_installed"] = launcher_path.exists()

    # Check if the daemon process is running
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        status["daemon_running"] = "parallax_validator" in result.stdout
    except Exception as exc:
        note_failure("install_parallax_startup:L147", exc)
        status["daemon_running"] = False

    return status


def main():
    parser = argparse.ArgumentParser(
        description="Install Parallax Validator in Windows Startup folder (NO ADMIN REQUIRED)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python install_parallax_startup.py              # Install
  python install_parallax_startup.py --uninstall  # Remove
  python install_parallax_startup.py --status     # Check
        """
    )
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove from Startup folder")
    parser.add_argument("--status", action="store_true",
                        help="Check installation and running status")

    args = parser.parse_args()

    if args.uninstall:
        success = uninstall()
        sys.exit(0 if success else 1)

    if args.status:
        status = check_status()
        installed = "YES" if status["startup_installed"] else "NO"
        running = "YES" if status["daemon_running"] else "NO"
        print(f"Parallax Validator Startup Status:")
        print(f"  Startup folder: {status['startup_path']}")
        print(f"  Launcher installed: {installed}")
        print(f"  Daemon running: {running}")
        if not status["startup_installed"]:
            print(f"\n  Run without --status to install.")
        sys.exit(0)

    # Default: install
    success = install()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
