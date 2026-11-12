"""lane_watch.py -- the observability window: live lane activity in one terminal.

`python tools/lane_watch.py [seconds]` (default 15): clears the screen each
tick and prints board tail, unretired claims, agent processes, fresh files,
HEAD, and watchdog verdict. Ctrl-C to quit. Read-only; posts nothing.

Built 2026-11-07 because the operator had no window into the box: a lane
died holding 6 claims and the only evidence lived in scattered instruments.
"""
from __future__ import annotations

import subprocess
import sys
import time

import lane_forensics as LF
import lane_watchdog as WD


def one_tick() -> str:
    parts = [f"=== lane_watch {time.strftime('%H:%M:%S')} (Ctrl-C quits) ==="]
    parts.append("-- board tail --")
    try:
        parts.extend(LF.board_tail(6))
    except Exception as exc:  # noqa: BLE001 -- a leg never kills the window
        parts.append(f"board unreadable: {exc}")
    parts.append("-- claims held --")
    try:
        dead, stuck = WD.scan()
        parts.extend([f"DEAD: {d}" for d in dead] or ["(none dead)"])
        parts.extend([f"STUCK: {s}" for s in stuck] or ["(none stuck)"])
    except RuntimeError as exc:
        parts.append(f"watchdog BLIND: {exc}")
    except Exception as exc:  # noqa: BLE001
        parts.append(f"watchdog error: {exc}")
    parts.append("-- processes --")
    try:
        parts.extend(LF.procs())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"procs unreadable: {exc}")
    parts.append("-- fresh (15 min) --")
    try:
        parts.extend(LF.fresh(15))
    except Exception as exc:  # noqa: BLE001
        parts.append(f"fresh unreadable: {exc}")
    parts.append(f"-- head: {LF._head()}")
    return "\n".join(parts)


def main() -> int:
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    while True:
        try:
            subprocess.run(["cls" if sys.platform == "win32" else "clear"],
                           timeout=10)
        except Exception:  # noqa: BLE001 -- no clear, just print
            print("\n" * 2)
        print(one_tick(), flush=True)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nlane_watch: closed.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
