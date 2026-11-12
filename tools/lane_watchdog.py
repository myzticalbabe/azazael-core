"""lane_watchdog.py -- heartbeat for lanes: page when claims outlive their lease.

A lane agent died 2026-11-07 holding 5 claims while every instrument read
empty: tasklist flags mangled by Git-bash read as 'no processes', the board
CLI hides claim states, and the lease (30 min) expired with nobody watching.
This tool is the watcher.

    python tools/lane_watchdog.py            # scan: leases vs live processes
    python tools/lane_watchdog.py --report   # scan + post dead-lane row(s)

A lane is DEAD when it holds an unretired claim older than the lease AND no
agent process for that lane exists. A live process with old claims is STUCK
(named, not paged). Exit 0 always on a completed scan; exit 2 when the scan
itself could not run (store missing/unreadable) -- that is not a clean bill.

--report posts via _board.py `report <lane> dead ...` (read back, as always).
Read-only without it.
"""
from __future__ import annotations

import argparse
import calendar
import os
import sqlite3
import subprocess
import sys
import time

STORE = r"C:\Users\clint\.azazael\telemetry_store.db"
LEASE_S = 30 * 60  # CLAIM_LEASE_SECONDS, per the lane-queue canon
LANE_PROC = {"kilo": ("kilo",), "opencode": ("opencode",),
             "freebuff": ("freebuff", "codebuff")}


def _env() -> dict:
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    return env


class ProcListBlind(RuntimeError):
    """The process list itself is unavailable (tasklist wedged -- measured
    2026-11-07, 60s timeout on a plain TABLE read). Raised, never returned
    as an empty set: emptiness pages every lane, blindness must shout."""


def live_procs() -> set[str]:
    """Lower-cased process image names alive right now (plain TABLE output)."""
    try:
        out = subprocess.run(["tasklist.exe"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=60, env=_env()).stdout.lower()
    except Exception as exc:  # noqa: BLE001 -- becomes BLIND, not empty
        raise ProcListBlind(f"tasklist failed: {exc}") from exc
    return set(out.split())


def held_claims() -> list[tuple]:
    """Unretired claims: (board_id, lane, claimed_by, claimed_at)."""
    db = sqlite3.connect(STORE, timeout=30)
    try:
        return db.execute(
            "SELECT board_id, lane, claimed_by, claimed_at FROM lane_task_state"
            " WHERE retired_at IS NULL AND claimed_by IS NOT NULL").fetchall()
    finally:
        db.close()


def scan(now: float | None = None) -> tuple[list[str], list[str]]:
    """Returns (dead_lines, stuck_lines). Raises RuntimeError if unscannable."""
    if not os.path.exists(STORE):
        raise RuntimeError(f"store missing: {STORE}")
    now = now if now is not None else time.time()
    procs = live_procs()  # raises ProcListBlind (a RuntimeError) when blind
    dead, stuck = [], []
    for bid, lane, by, at in held_claims():
        try:
            # Board stamps are UTC (+00:00); timegm, not mktime (local).
            # mktime here mis-ages every claim by the UTC offset (~5h) and
            # pages every lane -- measured once, never again.
            age = now - calendar.timegm(
                time.strptime(at[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:  # noqa: BLE001 -- bad timestamp reads as newborn
            age = 0
        if age < LEASE_S:
            continue
        names = LANE_PROC.get(str(lane).lower(), (str(lane).lower(),))
        alive = any(any(n in p for p in procs) for n in names)
        line = (f"board {bid} lane={lane} claimed_by={by} age={age/60:.0f}min"
                f" (lease {LEASE_S//60}min)")
        (stuck if alive else dead).append(line)
    return dead, stuck


def main() -> int:
    ap = argparse.ArgumentParser(description="Page dead lanes, name stuck ones.")
    ap.add_argument("--report", action="store_true",
                    help="post dead-lane row(s) via _board.py report")
    args = ap.parse_args()
    try:
        dead, stuck = scan()
    except Exception as exc:  # noqa: BLE001 -- exit 2, never a false clean
        print(f"watchdog BLIND: {exc}")
        return 2
    for line in stuck:
        print(f"STUCK (process alive, claim expired): {line}")
    for line in dead:
        print(f"DEAD (no process, claim expired): {line}")
    if not dead and not stuck:
        print("watchdog: all held claims within lease (or none held)")
    if args.report and dead:
        board = r"C:\Users\clint\_board.py"
        for line in dead:
            lane = line.split("lane=")[1].split()[0]
            detail = ("WATCHDOG page: agent process gone with claim past lease. "
                      + line + ". Re-claim or release, then check waiting work.")
            subprocess.run([sys.executable, board, "report", lane, "dead",
                            detail], capture_output=True, timeout=90,
                           env=_env())
            print(f"reported: {lane} dead")
    return 0


if __name__ == "__main__":
    sys.exit(main())
