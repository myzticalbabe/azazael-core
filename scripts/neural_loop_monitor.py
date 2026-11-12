"""Run the neural loop on an interval for a bounded window, and RECORD every cycle.

    python scripts/neural_loop_monitor.py --minutes 60 --interval 300
    python scripts/neural_loop_monitor.py --report          # summarise the window

WHY A SEPARATE MONITOR
======================

A loop that runs unattended needs an observer that is not the loop. This one
records, per cycle, the things that actually decide whether the loop is working:

  * `rc` and `verdict` (OK / INERT / FAIL / BUSY)
  * which stages CHANGED and which were BLOCKED
  * the weights before and after, and whether they are on disk
  * every distinct stage error

and then stops early on a run of consecutive failures, because an hour of a
known-broken cycle is an hour of writing to a state that is already wrong.

The monitor never trusts the cycle's own summary alone: it re-reads the state
file after each cycle, so a cycle that reports OK while the disk table has not
moved is recorded as a durability failure regardless of what it claimed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "state" / "neural_loop_monitor.jsonl"
CYCLE_OUT = ROOT / "state" / "neural_loop_cycle.json"
RUNNER = ROOT / "scripts" / "neural_loop.py"

#: Exit code that means "another cycle holds the lock" -- not a failure.
EXIT_BUSY = 3

#: Stop the window after this many consecutive failures.
MAX_CONSECUTIVE_FAIL = 3


def log(msg: str) -> None:
    print("%s %s" % (datetime.now().isoformat(timespec="seconds"), msg), flush=True)


def disk_state():
    """(edge_count, value_digest) of the SAVED table -- read from disk.

    The digest, not the count, is what answers "did anything reach the disk".
    The first version of this monitor compared edge COUNTS and immediately
    produced a false alarm: a cycle that nudges 232 existing edges leaves the
    count at 264, so a real, persisted update was recorded as NOT-DURABLE.
    """
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from CORE import neural_loop as nl  # noqa: WPS433
        from CORE import spine_registry as sr  # noqa: WPS433
        payload = json.loads(Path(sr._STATE_FILE).read_text(encoding="utf-8"))
        qw = payload.get("q_weights") or {}
        return len(qw), nl.weights_signature(qw)
    except Exception:
        return -1, ""


def run_one(limit: int, timeout: int) -> dict:
    """One cycle, in a SUBPROCESS so a crash cannot take the monitor with it."""
    before, before_sig = disk_state()
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(RUNNER), "--apply", "--limit", str(limit),
             "--out", str(CYCLE_OUT)],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        rc = proc.returncode
        err_tail = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()][-2:]
    except subprocess.TimeoutExpired:
        rc, err_tail = -9, ["monitor timeout after %ds" % timeout]
    after, after_sig = disk_state()

    row = {"at": datetime.now().isoformat(timespec="seconds"), "rc": rc,
           "secs": round(time.time() - t0, 1),
           "edges_before": before, "edges_after": after,
           "sig_before": before_sig, "sig_after": after_sig, "stderr": err_tail}
    try:
        rep = json.loads(CYCLE_OUT.read_text(encoding="utf-8"))
    except Exception as exc:
        row["parse_error"] = str(exc)[:120]
        return row
    row.update({
        "verdict": rep.get("verdict"),
        "changed": rep.get("changed_stages") or [],
        "failed": rep.get("failed_stages") or [],
        "blocked": list((rep.get("blocked_stages") or {}).keys()),
        "errors": [s.get("error") for s in (rep.get("stages") or []) if s.get("error")],
        "notes": rep.get("notes") or [],
    })
    # Independent durability check: the cycle claiming OK is not evidence that
    # anything reached the disk. Compared on the VALUE DIGEST.
    if rep.get("verdict") == "OK" and before_sig and after_sig == before_sig and rc == 0:
        row.setdefault("errors", []).append(
            "claimed OK but the SAVED weights are byte-identical to before "
            "(edges=%d, sig=%s)" % (after, after_sig))
        row["verdict"] = "NOT-DURABLE"
    return row


def append(rows) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def monitor(minutes: float, interval: float, limit: int, timeout: int) -> int:
    deadline = time.time() + minutes * 60
    consecutive_fail = 0
    seen_errors: dict = {}
    n = 0
    log("monitor start: %.0f min, interval %.0fs, executor limit %ds"
        % (minutes, interval, limit))
    while time.time() < deadline:
        n += 1
        row = run_one(limit, timeout)
        append([row])
        verdict = row.get("verdict")
        log("cycle %-3d verdict=%-11s rc=%-3s edges %s->%s sig %s->%s  %.0fs  changed=%s"
            % (n, verdict, row.get("rc"), row.get("edges_before"),
               row.get("edges_after"), row.get("sig_before"), row.get("sig_after"),
               row.get("secs") or 0,
               ",".join(row.get("changed") or []) or "-"))
        for err in row.get("errors") or []:
            seen_errors[err] = seen_errors.get(err, 0) + 1
            log("   ERROR: %s" % str(err)[:150])
        if row.get("parse_error"):
            seen_errors[row["parse_error"]] = seen_errors.get(row["parse_error"], 0) + 1
            log("   PARSE ERROR: %s" % row["parse_error"])

        if verdict in ("FAIL", "NOT-DURABLE"):
            consecutive_fail += 1
        elif verdict == "BUSY":
            consecutive_fail = 0            # contention is not a cycle failure
        else:
            consecutive_fail = 0
        if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
            log("STOPPING EARLY: %d consecutive failures -- the window is spent on a "
                "known-broken cycle until it is fixed" % consecutive_fail)
            break

        sleep = min(interval, max(1.0, deadline - time.time()))
        if time.time() + sleep >= deadline:
            break
        time.sleep(sleep)

    log("monitor done: %d cycles" % n)
    if seen_errors:
        log("distinct errors seen:")
        for err, count in sorted(seen_errors.items(), key=lambda kv: -kv[1]):
            log("   %3dx %s" % (count, str(err)[:150]))
    return 0


def report() -> int:
    if not LEDGER.exists():
        print("no monitor ledger at %s" % LEDGER)
        return 1
    rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print("monitor ledger is empty")
        return 1
    verdicts = Counter(r.get("verdict") for r in rows)
    print("cycles recorded: %d   (%s .. %s)" % (len(rows), rows[0]["at"], rows[-1]["at"]))
    print("verdicts:", dict(verdicts))
    first, last = rows[0], rows[-1]
    print("edges on disk: %s -> %s" % (first.get("edges_before"), last.get("edges_after")))
    errs = Counter(e for r in rows for e in (r.get("errors") or []))
    if errs:
        print("errors:")
        for e, c in errs.most_common(12):
            print("   %3dx %s" % (c, str(e)[:160]))
    blocked = Counter(b for r in rows for b in (r.get("blocked") or []))
    if blocked:
        print("stages blocked (by cycle count):", dict(blocked))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--limit", type=int, default=300, help="executor budget per cycle")
    ap.add_argument("--timeout", type=int, default=420, help="subprocess kill timeout")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)
    if args.report:
        return report()
    return monitor(args.minutes, args.interval, args.limit, args.timeout)


if __name__ == "__main__":
    sys.exit(main())