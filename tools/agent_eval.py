#!/usr/bin/env python3
# coding: utf-8
"""agent_eval -- the regression eval suite for this repository.

Why this exists (and what it is NOT)
------------------------------------
This is not an LLM-quality benchmark. It is the **regression eval** layer that
Anthropic's guidance calls for: tasks that should pass ~100% of the time, whose
job is to catch backsliding in agent *behaviour* that no unit test expresses --
"did the gate refuse", "is the order in sync", "did the suite that used to hang
still finish".

Design rules, each bought by a defect measured on 2026-10-30:

1. **Every case has a hard timeout.** The pytest suite still has none
   (`pytest-timeout` is absent), and two unbounded cases wedged 5,380 tests for
   months. A case that exceeds `timeout_s` is a FAIL with the reason, not a hang.
2. **Grading is deterministic first.** Exit code, required/forbidden substrings,
   state assertions. No LLM judge on the gate path -- a model-based grader here
   would be a non-deterministic check gating a deterministic system.
3. **The runner ships a negative control.** `--self-test` runs a case that must
   pass and a case that must fail, and refuses to report a verdict if either is
   wrong. A harness that cannot fail grades nothing (RULE 0B-C).
4. **pass^k, not just a pass rate.** `--trials k` repeats a case and reports the
   fraction of cases that passed ALL k trials -- the consistency metric. Most
   cases here are deterministic, so k=1 suffices; the flag exists so a flaky case
   is visible as a number instead of a shrug.
5. **Specifications are visible, not implied.** Behavioural scenarios that need a
   model in the loop are listed as `kind: specification` and reported SKIPPED.
   Their absence is stated on every run; it is never a silent zero.

Usage
-----
    python tools/agent_eval.py --list
    python tools/agent_eval.py --self-test
    python tools/agent_eval.py --suite regression            # exit 1 if any fails
    python tools/agent_eval.py --suite regression --trials 3
    python tools/agent_eval.py --case canon-drift-zero --json

Exit codes: 0 all graded cases passed, 1 a case failed, 2 harness error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError) as _exc:
        sys.stderr.write(f"note: stream not reconfigured to utf-8 ({_exc})\n")

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "evals" / "cases"
RESULTS_DIR = ROOT / "logs" / "agent_eval"
DEFAULT_TIMEOUT_S = 120.0


def load_cases(only: str | None = None) -> list[dict]:
    if not CASES_DIR.is_dir():
        raise SystemExit(f"no cases directory: {CASES_DIR}")
    cases = []
    for path in sorted(CASES_DIR.glob("*.json")):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"case file is not JSON: {path}: {exc}")
        # A file may hold one case or a list of cases (specifications are grouped).
        for case in (loaded if isinstance(loaded, list) else [loaded]):
            case.setdefault("id", path.stem)
            case["_file"] = path.name
            if only and case["id"] != only:
                continue
            cases.append(case)
    if only and not cases:
        raise SystemExit(f"no case with id {only!r}")
    return cases


def grade(case: dict, rc: int, out: str, elapsed: float, timed_out: bool) -> tuple[bool, list[str]]:
    """Deterministic grader. Returns (passed, reasons-for-failure)."""
    expect = case.get("expect", {})
    reasons: list[str] = []

    if timed_out:
        return False, [f"timed out after {expect.get('timeout_s', DEFAULT_TIMEOUT_S):.0f}s"]

    want_rc = expect.get("exit_code", 0)
    if rc != want_rc:
        reasons.append(f"exit {rc} != expected {want_rc}")

    budget = expect.get("max_seconds")
    if budget is not None and elapsed > budget:
        reasons.append(f"took {elapsed:.1f}s > max_seconds {budget:.1f}")

    for needle in expect.get("stdout_contains", []):
        if needle not in out:
            reasons.append(f"missing stdout text: {needle!r}")
    for needle in expect.get("stdout_absent", []):
        if needle in out:
            reasons.append(f"forbidden stdout text present: {needle!r}")

    return (not reasons), reasons


def run_case(case: dict) -> dict:
    if case.get("kind") == "specification":
        return {
            "id": case["id"],
            "suite": case.get("suite", "capability"),
            "status": "skipped",
            "reason": case.get("requires", "needs a model in the loop"),
            "trials_passed": 0,
            "trials": case.get("trials", 1),
        }

    trials = int(case.get("trials", 1))
    timeout_s = float(case.get("expect", {}).get("timeout_s", DEFAULT_TIMEOUT_S))
    cmd = case["cmd"]
    # {python} = the interpreter running THIS harness, so a case cannot silently
    # grade itself against a different build than the one in use.
    if isinstance(cmd, list):
        cmd = [sys.executable if part == "{python}" else part for part in cmd]
    results = []
    for _ in range(trials):
        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT / case.get("cwd", ".")),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                shell=isinstance(cmd, str),
            )
            rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            rc = -1
            partial = exc.stdout or ""
            out = partial if isinstance(partial, str) else partial.decode("utf-8", "replace")
        elapsed = time.monotonic() - started
        ok, reasons = grade(case, rc, out, elapsed, timed_out)
        results.append({"ok": ok, "reasons": reasons, "elapsed_s": round(elapsed, 2)})

    passed_all = all(r["ok"] for r in results)
    return {
        "id": case["id"],
        "suite": case.get("suite", "regression"),
        "status": "pass" if passed_all else "fail",
        "trials": trials,
        "trials_passed": sum(1 for r in results if r["ok"]),
        "elapsed_s": round(sum(r["elapsed_s"] for r in results), 2),
        "reasons": [reason for r in results for reason in r["reasons"]],
    }


def self_test() -> int:
    """Prove the runner can both pass and fail. A harness that cannot fail grades nothing."""
    ok_case = {
        "id": "self-test-pass",
        "cmd": [sys.executable, "-c", "print('ok-marker')"],
        "expect": {"exit_code": 0, "stdout_contains": ["ok-marker"], "timeout_s": 20},
    }
    bad_case = {
        "id": "self-test-fail",
        "cmd": [sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"],
        "expect": {"exit_code": 0, "stdout_contains": ["ok-marker"], "timeout_s": 20},
    }
    slow_case = {
        "id": "self-test-timeout",
        "cmd": [sys.executable, "-c", "import time; time.sleep(30)"],
        "expect": {"exit_code": 0, "timeout_s": 2},
    }
    p, f, t = run_case(ok_case), run_case(bad_case), run_case(slow_case)
    checks = [
        ("passing command grades PASS", p["status"] == "pass"),
        ("failing command grades FAIL", f["status"] == "fail"),
        ("timeout grades FAIL, not a hang", t["status"] == "fail" and any("timed out" in r for r in t["reasons"])),
    ]
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'BROKEN'}  {label}")
    if all(ok for _, ok in checks):
        print("self-test: harness can distinguish pass, fail and timeout")
        return 0
    print("self-test: HARNESS IS UNVALIDATED -- its verdicts must not be believed")
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Agent regression eval suite.")
    ap.add_argument("--suite", choices=("regression", "capability", "all"), default="regression")
    ap.add_argument("--case")
    ap.add_argument("--trials", type=int, default=None, help="override every case's trial count")
    ap.add_argument("--fail-under", type=float, default=1.0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    cases = load_cases(args.case)
    if args.trials:
        for case in cases:
            case["trials"] = args.trials

    if args.list:
        for case in cases:
            kind = case.get("kind", "command")
            print(f"{case['id']:28s} {case.get('suite', 'regression'):11s} {kind:14s} {case.get('title', '')}")
        return 0

    selected = [c for c in cases if args.suite == "all" or c.get("suite", "regression") == args.suite]
    if not selected:
        print(f"no cases in suite {args.suite!r}")
        return 2

    started = time.monotonic()
    results = [run_case(case) for case in selected]
    elapsed = round(time.monotonic() - started, 1)

    graded = [r for r in results if r["status"] in ("pass", "fail")]
    skipped = [r for r in results if r["status"] == "skipped"]
    passed = [r for r in graded if r["status"] == "pass"]

    if args.json:
        print(json.dumps({"results": results, "elapsed_s": elapsed}, indent=1))
    else:
        for r in results:
            if r["status"] == "skipped":
                print(f"  SKIP  {r['id']:28s} ({r['reason']})")
                continue
            mark = "PASS" if r["status"] == "pass" else "FAIL"
            trials = f" {r['trials_passed']}/{r['trials']}" if r["trials"] > 1 else ""
            print(f"  {mark}  {r['id']:28s} {r['elapsed_s']:6.1f}s{trials}")
            for reason in r["reasons"][:4]:
                print(f"          - {reason}")
        rate = (len(passed) / len(graded)) if graded else 0.0
        print(
            f"\n{len(passed)}/{len(graded)} graded cases passed "
            f"({rate:.0%}); {len(skipped)} skipped; {elapsed}s total"
        )
        if skipped:
            print("note: skipped cases are specifications for behaviour that needs a model in the loop.")

    if not args.no_write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        (RESULTS_DIR / f"{stamp}.json").write_text(
            json.dumps({"at": stamp, "suite": args.suite, "elapsed_s": elapsed, "results": results}, indent=1),
            encoding="utf-8",
        )

    rate = (len(passed) / len(graded)) if graded else 0.0
    return 0 if rate >= args.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(main())
