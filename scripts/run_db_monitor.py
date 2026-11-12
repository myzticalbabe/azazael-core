#!/usr/bin/env python3
"""run_db_monitor.py — Keep the DatabaseMonitorWorm continuously running.

The DatabaseMonitorWorm produces db_health_snapshot and db_health_alert
events that feed the evolution cycle's hard (alert) training pool. It only
runs while some process holds its daemon thread alive — when nothing does,
the alert pool freezes (it stalled for 2 days in Sept 2026 for exactly this
reason). This script starts the monitor and keeps the process alive so the
hard pool keeps receiving fresh examples.

Usage:
    python scripts/run_db_monitor.py                 # default: check every 15 min
    python scripts/run_db_monitor.py --interval 3600 # check every hour
    python scripts/run_db_monitor.py --once          # single check, then exit

Designed to be run by a Windows scheduled task (Azazael-DbMonitor) that
starts at logon and restarts on failure.
"""

import argparse
import os
import sys
import time
from pathlib import Path

_src = str(Path(__file__).resolve().parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "db_monitor.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # scanner:ignore -- the line was already emitted to stdout above; file persistence is best-effort


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=900,
                        help="Seconds between health checks (default: 900)")
    parser.add_argument("--once", action="store_true",
                        help="Run a single health check, then exit")
    args = parser.parse_args()

    os.makedirs(_LOG_PATH.parent, exist_ok=True)

    try:
        from CORE.WORMS.expansion.db_monitor import DatabaseMonitorWorm
    except Exception as e:  # noqa: BLE001 — fail with a clear message
        log(f"[ERROR] cannot import DatabaseMonitorWorm: {e}")
        return 1

    monitor = DatabaseMonitorWorm()

    if args.once:
        log("running single health check")
        try:
            report = monitor.execute()
            log(f"single check complete (interval={args.interval}s)")
        except Exception as e:  # noqa: BLE001
            log(f"[ERROR] health check failed: {e}")
            return 1
        return 0

    log(f"starting continuous monitor (interval={args.interval}s)")
    try:
        monitor.start_monitor(interval_seconds=args.interval)
    except Exception as e:  # noqa: BLE001
        log(f"[ERROR] failed to start monitor: {e}")
        return 1

    log("monitor started; keeping process alive")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log("stopping monitor (KeyboardInterrupt)")
        try:
            monitor.stop_monitor()
        except Exception:  # noqa: BLE001 — best-effort stop
            pass  # scanner:ignore -- shutdown stop is best-effort; the process is exiting regardless
        return 0


if __name__ == "__main__":
    sys.exit(main())