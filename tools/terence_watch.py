#!/usr/bin/env python3
# coding: utf-8
"""terence_watch.py — give TERENCE a caller, and give its findings a ledger.

WHY THIS EXISTS
---------------
`src/UTILITIES/TERENCE/TERENCE.py` is a complete error-resolution protocol
(34.6 KB, `Fix`, `FixStats`, `scan`/`preview`/`fix_all`/`pattern_scores`/
`_record_outcome`, its own CLI) and it was modified TODAY. It has:

    calls from scripts/ or tools/ :  0
    @register_worm                :  none
    __init__.py                   :  0 bytes

So it is a self-improving fixer (`_record_outcome` feeds `pattern_scores`, and
`fix_all` selects against patterns with a proven hit rate) with nothing
invoking it. It is also the same failure class this evening turned up six
times: built, maintained, unconsumed. And it explains the OTHER orphan from
the same survey — PMB's `error_log` table has 0 rows against 227 events,
because the system meant to close error loops has no caller and the ledger
meant to receive them has no writer.

This file is the seam. It runs TERENCE's scan, and writes the results into
`error_log` so the fixer's output has somewhere to land and can be queried
later. It does NOT auto-apply fixes: `fix_all(apply=True)` rewrites source, and
scheduling that unattended is a separate decision with a different blast
radius. `--apply` is available here but off by default.

HONESTY
-------
A scan that found nothing reports `NO_FINDINGS`, not `OK`. An empty result and
a healthy result are different states, and collapsing them is the defect that
let a dead reward arm read as `alert: 0.0` for 35 cycles.

Usage:
    python tools/terence_watch.py
    python tools/terence_watch.py --json
    python tools/terence_watch.py --apply      # opt in to source mutation
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

AZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(AZ, "src"))

WORKSPACE = os.environ.get(
    "PMB_WORKSPACE_DIR", r"C:\Users\clint\.pmb\workspaces\04a151a73354")
ERROR_DB = os.path.join(WORKSPACE, "events.sqlite")
REPORT = os.path.join(AZ, "state", "terence_report.json")


def load_terence():
    from UTILITIES.TERENCE.TERENCE import TERENCE
    return TERENCE()


def as_dict(obj):
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    if hasattr(obj, "_asdict"):
        return dict(obj._asdict())
    return {"value": str(obj)}


def write_error_log(rows):
    """Insert into PMB's error_log. That table is the SOP's designated home
    and has never been written; this is its first writer."""
    if not rows:
        return 0
    con = sqlite3.connect(ERROR_DB, timeout=20)
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='error_log'")
        if cur.fetchone() is None:
            return 0
        cur.executemany(
            "INSERT INTO error_log (ts, component, message, trace_head, note) "
            "VALUES (?,?,?,?,?)", rows)
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="apply fixes (mutates source; off by default)")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    try:
        t = load_terence()
    except Exception as e:
        print(f"TERENCE_UNAVAILABLE: {type(e).__name__}: {e}")
        return 2

    t0 = time.time()
    try:
        stats = as_dict(t.scan())
    except Exception as e:
        print(f"SCAN_FAILED: {type(e).__name__}: {e}")
        return 2
    scan_s = time.time() - t0

    applied = 0
    apply_error = None
    if a.apply:
        try:
            applied = int(t.fix_all(apply=True, max_fixes=a.limit) or 0)
        except Exception as e:
            apply_error = f"{type(e).__name__}: {e}"

    try:
        patterns = as_dict(t.pattern_scores())
    except Exception as e:
        patterns = {"error": f"{type(e).__name__}: {e}"}
    try:
        status = as_dict(t.status())
    except Exception as e:
        status = {"error": f"{type(e).__name__}: {e}"}

    found = int(stats.get("errors_found", 0) or 0)
    scanned = int(stats.get("files_scanned", 0) or 0)
    now = time.time()
    rows = []
    if found:
        # Labelled PATTERNS, not ERRORS, and deliberately not "in N files".
        # TERENCE's own field is `errors_found` (8009 measured 2026-09-30) and
        # its top hits are lint, not defects: 3012 "replace magic numbers with
        # constants", 2876 "comment out print statement", 895 "comment out
        # continue/break outside loop", 475 "use tuple for isinstance". Two of
        # those three largest would CORRUPT the tree if applied -- deleting
        # 2876 prints and commenting out 895 control-flow statements. The
        # count is also internally inconsistent: files_scanned reports 0 while
        # errors_found reports 8009, so it is not a defect measure and must not
        # be recorded as one.
        rows.append((now, "terence_scan",
                     f"{found} lint pattern(s) matched"
                     + (f" across {scanned} file(s)" if scanned else
                        " (scanner reported files_scanned=0 -- count unverified)"),
                     f"fixes_previewed={stats.get('fixes_previewed')}",
                     f"applied={applied} scan_seconds={scan_s:.2f} "
                     f"apply_error={apply_error} "
                     f"DESTRUCTIVE_PATTERNS_PRESENT=yes "
                     f"(print/control-flow commenting must not be auto-applied)"))
    n = write_error_log(rows)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scan_seconds": round(scan_s, 2),
        "stats": stats,
        "patterns": patterns,
        "status": status,
        "applied": applied,
        "apply_error": apply_error,
        "error_log_rows_written": n,
        "verdict": ("FINDINGS" if found else "NO_FINDINGS"),
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)

    if a.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"terence_watch: {report['verdict']}  ({scan_s:.2f}s)")
        for k, v in stats.items():
            print(f"    {k}: {v}")
        print(f"  pattern_scores      : {len(patterns) if isinstance(patterns, dict) else '?'} entries")
        if applied:
            print(f"  APPLIED             : {applied}")
        if apply_error:
            print(f"  apply_error         : {apply_error}")
        print(f"  error_log rows      : {n}")
        print(f"  report              : {REPORT}")
        if not found:
            print("  (no findings is NOT the same as healthy; it means the")
            print("   pattern set matched nothing in this scan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
