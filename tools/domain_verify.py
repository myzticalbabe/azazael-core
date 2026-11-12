#!/usr/bin/env python3
"""domain_verify.py -- Aether A-6: verification as a heterogeneous micro-swarm.

One gate, one auditor set, every domain: recall-layer topology gets the same
reading as canon sync, and a failure in either hides behind the other's pass.
So each domain carries the checks its matter needs -- recall gets ordering
and topology tests, evolution gets metric and calibration tests, canon gets
the sync drift gate -- and the swarm merges one verdict per domain plus a
rolled-up whole.

Hot keys earn fast solids; hot domains earn strict verbs: a domain fails on
exit != 0, and a domain that cannot run is blind (exit 2 shapes the whole
run, because an unanswerable question is not a pass).

Exit: 0 every domain held, 1 a domain failed, 2 a domain could not run and
none failed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

DOMAINS = {
    "recall": [[PY, "-m", "pytest", "tests/test_dodeca_recall.py", "-q"]],
    "evolution": [[PY, "-m", "pytest", "tests/test_trainer.py",
                   "tests/test_eve_intuition.py", "-q"]],
    "canon": [[PY, "tools/agent_orders_sync.py", "--check"]],
}

BOUND = 300


def run(cmd: list, timeout: int = BOUND) -> dict:
    """Run one check as a bounded child; a launch failure is blindness."""
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "blind", "detail": "%s: %s" % (type(exc).__name__, exc)}
    tail = [l for l in (proc.stdout or "").strip().splitlines() if l.strip()]
    line = tail[-1][:160] if tail else (proc.stderr or "")[:160].strip()
    if proc.returncode == 0:
        return {"status": "pass", "detail": line}
    return {"status": "fail", "detail": "exit %d: %s" % (proc.returncode, line)}


def merge(results: dict) -> int:
    """Roll per-domain verdicts into one exit: fail dominates, blind is 2."""
    states = {r["status"] for r in results.values()}
    if "fail" in states:
        return 1
    if "blind" in states:
        return 2
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", action="append", default=None)
    ap.add_argument("--timeout", type=int, default=BOUND)
    args = ap.parse_args(argv)
    names = args.domain or sorted(DOMAINS)
    results = {}
    for name in names:
        cmds = DOMAINS.get(name)
        if not cmds:
            results[name] = {"status": "blind",
                             "detail": "no such domain (known: %s)"
                                       % ", ".join(sorted(DOMAINS))}
            continue
        parts = [run(cmd, args.timeout) for cmd in cmds]
        bad = [p for p in parts if p["status"] != "pass"]
        results[name] = bad[0] if bad else parts[0]
        print("  %-10s %-5s %s" % (name, results[name]["status"],
                                   results[name]["detail"]))
    rc = merge(results)
    print("domain_verify: %s (%s)"
          % ({0: "HELD", 1: "FAILED", 2: "BLIND"}[rc],
             ", ".join("%s=%s" % (k, v["status"]) for k, v in results.items())))
    return rc


if __name__ == "__main__":
    sys.exit(main())
