#!/usr/bin/env python3
"""Open the Azazael live stack in separate visible Windows consoles.

This launcher only starts terminal processes. It never starts an HTTP server
and never changes Windows Task Scheduler. On non-Windows systems it is a
no-op with a clear message.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import chain_controller  # noqa: E402


def main() -> int:
    if os.name != "nt":
        print("Visible Windows consoles require Windows.", file=sys.stderr)
        return 2
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    python = sys.executable
    commands = {
        "Azazael Live Monitor": [python, str(SCRIPTS / "live_monitor.py"),
                                 "--refresh", "30"],
        "Azazael Ex Machina Monitor": [python,
                                       str(SCRIPTS / "exmachina_live_monitor.py"),
                                       "--refresh", "30"],
        "Azazael AOA Monitor": [python, "-m", "src.AOA.aoa_monitor"],
    }
    _state, _result, task_detail = chain_controller._own_task_state()
    if "running" not in task_detail.lower():
        commands = {
            "Azazael Chain Controller": [python,
                                         str(SCRIPTS / "chain_controller.py"),
                                         "--live", "--interval", "30",
                                         "--allow-mutations"],
            **commands,
        }
    else:
        print("controller: scheduler already running; visible controller skipped")
    for title, command in commands.items():
        subprocess.Popen(
            command,
            cwd=str(ROOT),
            creationflags=creationflags,
            close_fds=True,
        )
        print(f"started: {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
