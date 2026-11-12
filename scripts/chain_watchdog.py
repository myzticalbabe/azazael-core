#!/usr/bin/env python3
"""chain_watchdog.py — external liveness supervisor for the chain controller.

A liveness watchdog must run OUTSIDE the process it watches (ERROR_LOG #233
prevention note). The controller's own `liveness` job cannot mourn the
controller's absence: when the controller dies, the coroner dies with it.
That is exactly what happened 2026-09-24 19:21 (controller exit 0xC000013A,
nothing revived it for 4.7h, 9 jobs piled up overdue).

This script is that outside watcher. The \\Azazael-ChainWatchdog scheduled
task fires it every 30 minutes. Each run:

  1. reads the controller heartbeat age (logs/chain_controller.log) and the
     lock owner (state/chain_controller.lock);
  2. if a live controller owns the lock, or the heartbeat is fresh, does
     nothing (exit 0);
  3. otherwise revives the chain via `schtasks /run` (single provenance: the
     controller stays owned by its task), falling back to launching the
     output-redirecting wrapper directly if the task cannot start.

Observability contract: every run appends one JSON line to
logs/chain_watchdog.log (bounded), including the "healthy" verdict, so the
watchdog's own death is detectable by its silence.

Usage:
  python scripts/chain_watchdog.py [--quiet] [--force]

  --quiet  no stdout (for the scheduled task); the log always gets a row
  --force  revive even when the chain looks healthy (manual kick)
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
HEARTBEAT_PATH = ROOT / "logs" / "chain_controller.log"
LOCK_PATH = ROOT / "state" / "chain_controller.lock"
LOG_PATH = ROOT / "logs" / "chain_watchdog.log"
LAUNCHER = ROOT / "scripts" / "run_chain_live.bat"
TASK_NAME = os.environ.get("AZAZAEL_CHAIN_TASK", "Azazael-Chain")
#: Same bound the controller reports for itself (chain_controller._heartbeat_status).
STALE_S = 900.0
LOG_MAX = 1_000_000


def _log(row: dict[str, Any]) -> None:
    row = {"at": datetime.now().isoformat(), "watchdog": "chain_watchdog", **row}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX:
            LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    except OSError:
        pass  # scanner:ignore  # a watchdog that cannot log must still act


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False  # scanner:ignore -- probe semantics: a failed liveness query means alive-ness cannot be confirmed; False is the safe answer
    try:
        os.kill(pid, 0)
    except OSError as exc:
        # EPERM = the process exists but belongs to someone else — alive.
        # Other errors (ESRCH, EINVAL...) mean it cannot be signalled — dead.
        return exc.errno == errno.EPERM
    return True


def _lock_owner() -> tuple[int | None, bool]:
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        pid = 0
    return (pid or None, _pid_alive(pid))


def _heartbeat_age_s() -> float | None:
    try:
        return max(0.0, time.time() - HEARTBEAT_PATH.stat().st_mtime)
    except OSError:
        return None  # scanner:ignore -- probe semantics: an unreadable heartbeat IS the no-heartbeat signal; None is the designed marker


def _revive() -> dict[str, Any]:
    """Start the chain again: prefer the task (single provenance), then the launcher."""
    try:
        result = subprocess.run(
            ["schtasks", "/run", "/tn", TASK_NAME],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            return {"method": "schtasks /run " + TASK_NAME, "ok": True}
        first_try = (result.stderr or result.stdout or "").strip()[:160]
    except Exception as exc:
        first_try = str(exc)[:160]
    if LAUNCHER.is_file():
        try:
            subprocess.Popen(
                ["cmd", "/c", str(LAUNCHER)],
                cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            return {"method": "launcher " + str(LAUNCHER), "ok": True,
                    "schtasks_error": first_try}
        except Exception as exc:
            return {"method": "launcher", "ok": False, "schtasks_error": first_try,
                    "launcher_error": str(exc)[:160]}
    return {"method": "schtasks /run " + TASK_NAME, "ok": False, "error": first_try}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    age = _heartbeat_age_s()
    pid, alive = _lock_owner()
    healthy = (age is not None and age <= STALE_S) or alive
    verdict = {
        "heartbeat_age_s": None if age is None else round(age, 1),
        "heartbeat_stale": age is None or age > STALE_S,
        "lock_pid": pid,
        "lock_alive": alive,
    }

    if healthy and not args.force:
        if alive and (age is None or age > STALE_S):
            # Live owner but silent heartbeat: a hang, not a death. Do NOT
            # spawn a second controller -- the lock would refuse it anyway.
            _log({**verdict, "action": "none",
                  "reason": "lock owner alive but heartbeat stale (hung?)"})
            if not args.quiet:
                print("chain_watchdog: lock owner pid=%s alive; heartbeat stale -- not reviving" % pid)
            return 0
        _log({**verdict, "action": "none", "reason": "healthy"})
        if not args.quiet:
            print("chain_watchdog: healthy (heartbeat_age_s=%s)" % verdict["heartbeat_age_s"])
        return 0

    revival = _revive()
    _log({**verdict, "action": "revive", "revive": revival})
    if not args.quiet:
        print("chain_watchdog: revived (%s ok=%s)" % (revival.get("method"), revival.get("ok")))
    return 0 if revival.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())