#!/usr/bin/env python3
"""ONE CLICK -- the whole gate stack behind a single invocation.

WHY THIS EXISTS
---------------
Every gate this tree owns now has a command, and the operator has to remember
five of them, in the right order, on the right interpreter. That is five chances
to skip one, and a skipped gate is indistinguishable from a passing one at
report time. This is one command:

    ONE_CLICK.cmd            # or: python tools/one_click.py

STEPS, IN ORDER, EACH INDEPENDENT
---------------------------------
  1. `mcp_startup_gate.py --fix`   the stack is live (daemon, :8766, lanes, canon)
  2. `golden_arrow.py`             every test file (staged-only in --fast)
  3. `deep_dive.py`                forensic audits + bounded web leg

Each step is a child process with its own bound. A step that fails does **not**
stop the ones after it: a red stack and a red suite are usually unrelated, and
stopping at the first failure means the operator runs the command three times to
learn three facts. Every step's exit code, seconds and the tail of its output is
printed and written to `logs/one_click.json`.

VERDICT
-------
    PASS  every step exited 0
    FAIL  any step exited non-zero (the step is named)
    BLIND a step could not be performed at all (missing tool, no interpreter)
          -- reported separately, because "could not run" is not "passed" and
          is also not "failed": it is the state the operator has to fix first.

EXIT CODES
----------
    0 PASS   1 FAIL   2 BLIND

MODES
-----
    --fast     staged test files only (the commit path); forensics still run
    --no-web   deep dive without the web leg (offline, planes, dead DNS)
    --json P   where the report lands (default logs/one_click.json)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "logs" / "one_click.json"
PY = Path("C:/Azazael/.venvs/py316t/Scripts/python.exe")


def interpreter() -> str:
    return str(PY) if PY.exists() else sys.executable


# The index probe has THREE answers, and two of them are not the same fact.
# `False` is "git answered, and nothing test-shaped is staged"; UNKNOWN is "git
# could not be asked". Collapsing the second into the first reads a broken git as
# an empty index, which is how a --fast run comes to test nothing while printing
# that it was narrow on purpose.
UNKNOWN = object()


def staged_tests():
    """True / False from git, or UNKNOWN when the question could not be asked."""
    try:
        proc = subprocess.run(["git", "diff", "--cached", "--name-only",
                               "--diff-filter=ACMR"], cwd=str(ROOT),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    if proc.returncode != 0:
        return UNKNOWN
    return any(line.strip().endswith(".py") and Path(line.strip()).name.startswith("test_")
               for line in (proc.stdout or "").splitlines())


def step(name: str, argv: list, bound: float) -> dict:
    """Run one step. Never raises; a failure is a return value, not an exception."""
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=bound)
    except FileNotFoundError as exc:
        return {"step": name, "status": "BLIND", "rc": None,
                "detail": "not found: %s" % exc, "seconds": 0.0, "tail": []}
    except subprocess.TimeoutExpired:
        return {"step": name, "status": "BLIND", "rc": None,
                "detail": "exceeded its %.0fs bound" % bound,
                "seconds": round(time.monotonic() - started, 1), "tail": []}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"step": name, "status": "BLIND", "rc": None,
                "detail": "%s: %s" % (type(exc).__name__, exc),
                "seconds": round(time.monotonic() - started, 1), "tail": []}
    lines = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return {
        "step": name,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "rc": proc.returncode,
        "seconds": round(time.monotonic() - started, 1),
        "tail": lines[-12:],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fast", action="store_true",
                    help="staged test files only (the commit path)")
    ap.add_argument("--no-web", action="store_true", help="deep dive without the web leg")
    ap.add_argument("--json", default=str(REPORT))
    args = ap.parse_args(argv)

    py = interpreter()
    staged = staged_tests()
    fast = bool(args.fast and staged is True)
    if args.fast and staged is UNKNOWN:
        print("ONE CLICK: --fast could not read the index (git failed or is absent); "
              "running every test file instead -- an unreadable index is not an "
              "empty one, and a narrower run may never stand in for the suite.")
    elif args.fast and staged is False:
        print("ONE CLICK: --fast was asked for but the index holds no test files; "
              "running every test file instead (a narrower run is never allowed to "
              "stand in for the full suite).")
    arrow = [py, "tools/golden_arrow.py", "--no-deep-dive"]
    if fast:
        arrow.append("--staged")
    dive = [py, "tools/deep_dive.py"]
    if args.no_web:
        dive.append("--offline")

    plan = [
        ("stack", [py, "tools/mcp_startup_gate.py", "--fix"], 600),
        ("tests", arrow, 3600),
        ("forensics", dive, 900),
    ]

    started = time.monotonic()
    results = []
    print("ONE CLICK -- %d step(s), tests=%s%s"
          % (len(plan), "staged" if fast else "every test file",
             ", web leg off" if args.no_web else ""))
    for name, cmd, bound in plan:
        print("  ... %s" % name, flush=True)
        row = step(name, cmd, bound)
        results.append(row)
        print("  %-4s %-10s %6ss  %s"
              % (row["status"], row["step"], row["seconds"],
                 "" if row["status"] != "BLIND" else row["detail"]), flush=True)
        for line in row["tail"][-4:]:
            print("        %s" % line)

    failed = [r for r in results if r["status"] == "FAIL"]
    blind = [r for r in results if r["status"] == "BLIND"]
    verdict = "FAIL" if failed else ("BLIND" if blind else "PASS")
    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_s": round(time.monotonic() - started, 1),
        "mode": "fast" if fast else "full",
        "interpreter": py,
        "steps": results,
        "verdict": verdict,
    }
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("")
    print("ONE CLICK: %s%s%s   report: %s"
          % (verdict,
             " -- failed: %s" % ", ".join(r["step"] for r in failed) if failed else "",
             " -- could not run: %s" % ", ".join(r["step"] for r in blind) if blind else "",
             out))
    return {"PASS": 0, "FAIL": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
