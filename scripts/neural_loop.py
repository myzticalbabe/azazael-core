"""Run one cycle of the canonical neural loop.

    python scripts/neural_loop.py                 # dry-run: full chain, no writes
    python scripts/neural_loop.py --apply          # a real learning cycle
    python scripts/neural_loop.py --apply --limit 900
    python scripts/neural_loop.py --contract       # print the stage contract

EXIT CODES ARE THE INTERFACE:

    0  OK     -- at least one stage changed durable state
    0  DRY    -- a dry run: no writes were attempted (expected)
    1  FAIL   -- a stage failed, or a stage claimed a change the weights deny
    2  INERT  -- an APPLY cycle ran and changed NOTHING

INERT is a non-zero exit on purpose. It is the failure this command exists to
make visible: the previous scheduler reported success for fires that ran
fifteen jobs and moved no weight, so "the loop ran" and "the loop learned" were
indistinguishable. A caller that treats exit 0 as "learning happened" is now
correct.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from CORE import neural_loop  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_INERT, EXIT_BUSY = 0, 1, 2, 3
#: DRY exits 0: it did exactly what it was asked. INERT exits non-zero because
#: an applying cycle that moved no weight did NOT do what it was asked.
VERDICT_EXIT = {"OK": EXIT_OK, "FAIL": EXIT_FAIL, "INERT": EXIT_INERT,
                "DRY": EXIT_OK}

DEFAULT_LEDGER = ROOT / "state" / "neural_loop.jsonl"
LOCK_PATH = ROOT / "state" / "neural_loop.lock"


def acquire_single_flight(path=None):
    """Exclusive OS lock, so two cycles cannot interleave on one brain state.

    Needed the moment this loop is scheduled AND monitored at the same time or
    run twice inside its own duration: both processes read the same table, both
    write it back, and the second save silently reverts the first -- an
    interleave that reports success in both logs. The lock is an OS lock on a
    descriptor rather than a file-existence convention, so a hard-killed cycle
    holds nothing (the convention-fails-open trap the chain documents).

    Returns the open handle (keep it alive for the process lifetime) or None if
    another cycle holds it.
    """
    target = Path(path) if path else LOCK_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    fh = open(target, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def print_contract() -> None:
    print("NEURAL LOOP contract (v%s) -- stages run in this order" % neural_loop.VERSION)
    print()
    for i, name in enumerate(neural_loop.STAGES, 1):
        tag = "WRITES" if name in neural_loop.WRITE_STAGES else "  reads"
        print("  %2d. %-12s %s" % (i, name, tag))
    print()
    print("  A cycle is FAIL if any stage fails, and INERT if no stage changed")
    print("  durable state. `verify` re-reads the weights and reports the delta.")


def render(rep: dict) -> None:
    before, after = rep.get("state_before") or {}, rep.get("state_after") or {}
    print("neural loop  verdict=%s  apply=%s  %.0fms"
          % (rep.get("verdict"), rep.get("apply"), rep.get("ms") or 0.0))
    print("  weights before: edges=%s distinct=%s l2=%s"
          % (before.get("edges"), before.get("distinct"), before.get("l2")))
    print("  weights after : edges=%s distinct=%s l2=%s"
          % (after.get("edges"), after.get("distinct"), after.get("l2")))
    print()
    print("  %-12s %-6s %-8s %7s  %s" % ("STAGE", "OK", "CHANGED", "ms", "DETAIL"))
    for st in rep.get("stages") or []:
        detail = ""
        if st.get("blocked"):
            detail = "blocked: " + st["blocked"]
        elif st.get("error"):
            detail = "ERROR: " + st["error"]
        elif st.get("evidence"):
            ev = st["evidence"]
            keys = ("observed", "nonzero", "updates", "ordered", "patterns",
                    "promoted", "graded", "edges")
            parts = ["%s=%s" % (k, ev[k]) for k in keys if k in ev]
            if not parts:
                parts = ["%s=%s" % (k, ev[k]) for k in list(ev)[:3]]
            detail = " ".join(parts)
        print("  %-12s %-6s %-8s %7.1f  %s"
              % (st.get("stage"), "yes" if st.get("ok") else "NO",
                 "yes" if st.get("changed") else "-", st.get("ms") or 0.0,
                 detail[:88]))
    if rep.get("notes"):
        print()
        for n in rep["notes"]:
            print("  note: %s" % n)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write: run the learning cycle for real")
    ap.add_argument("--limit", type=int, default=200,
                    help="seconds allowed for the executor stage (default 200)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--out", default="",
                    help="write the JSON report to this path (stage scripts print "
                         "to stdout, so a piped --json is not safe on its own)")
    ap.add_argument("--contract", action="store_true", help="print the stage contract and exit")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER),
                    help="cycle ledger path ('' to disable)")
    ap.add_argument("--no-lock", action="store_true",
                    help="skip the single-flight lock (tests only)")
    args = ap.parse_args(argv)

    if args.contract:
        print_contract()
        return EXIT_OK

    hold = None
    if not args.no_lock:
        hold = acquire_single_flight()
        if hold is None:
            print("another neural loop cycle holds the lock -- not starting a second")
            return EXIT_BUSY
    rep = neural_loop.run_cycle(apply=args.apply, limit=args.limit,
                                ledger=args.ledger or None)
    blob = json.dumps(rep, indent=2, sort_keys=True)
    if args.out:
        # The reliable channel. Several stage scripts log to STDOUT (the
        # hyperopt cycle among them), so a redirected `--json` stream can carry
        # a log line before the report -- measured, not theoretical: the first
        # full e2e run produced a file whose first line was a hyperopt dwell
        # message and which `json.load` therefore rejected with "Extra data".
        Path(args.out).write_text(blob, encoding="utf-8")
    if args.json and not args.out:
        print(blob)
    elif not args.json:
        render(rep)
    if args.out:
        print("report: %s" % args.out)
        # Cycle summary for the wallboards (E-P1-1): state/
        # neural_loop_cycle.json was previously written only by the heavyweight
        # supervisor (neural_loop_monitor.py, unscheduled since 9/21), so the
        # scheduled loop ran fresh while its summary went stale. Same blob the
        # task already persists — second write, fail-open, never breaks a run.
        try:
            _cycle_out = (Path(__file__).resolve().parents[1]
                          / "state" / "neural_loop_cycle.json")
            _tmp = str(_cycle_out) + ".tmp"
            Path(_tmp).write_text(blob, encoding="utf-8")
            os.replace(_tmp, _cycle_out)
        except Exception as exc:
            print("cycle summary write failed (non-fatal): %s" % str(exc)[:120])
    return VERDICT_EXIT.get(rep.get("verdict"), EXIT_FAIL)


if __name__ == "__main__":
    sys.exit(main())
