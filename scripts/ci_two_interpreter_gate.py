#!/usr/bin/env python3
# coding: utf-8
"""ci_two_interpreter_gate.py — run the suite on BOTH interpreters, compare.

Why this exists
---------------
The Ubuntu pillar runs two interpreters of the same version (see
`CORE/python_env.py`): a free-threaded primary (`cpython-314t`) and a GIL
sidecar (`cpython-314`) that carries the frameworks with no free-threaded wheel
(tensorflow, orjson, langchain, chromadb, polars, ray, spacy). A change can pass
on one and fail on the other, so "the suite is green" is only ever a statement
about ONE interpreter.

This gate runs `pytest tests/` once per interpreter and compares the outcome:

* a collection error on either interpreter  -> exit 1 (drift, not a pass);
* a pass count below the recorded baseline  -> exit 1 (a regression);
* primary/GIL pass counts diverging beyond
  `--max-divergence`                        -> exit 1 (optional, off by default);
* otherwise                                 -> exit 0.

Baselines are per interpreter, in
`logs/ci_two_interpreter_gate_baseline.json`, so the two interpreters are allowed
to have honestly different known-good counts (measured 2026-09-30: 3570 primary
/ 3519 GIL). A run that never completed is a failure, never a pass.

Why not just reuse `tools/aggregate_suite_gate.py`
--------------------------------------------------
That gate answers "no NEW isolation defects inside one interpreter". This one
answers a different question — "does the same tree behave the same on both
interpreters" — and needs its own per-interpreter baseline. Keeping them
separate means neither has to explain the other's baseline.

Usage
-----
    python scripts/ci_two_interpreter_gate.py                  # check (CI)
    python scripts/ci_two_interpreter_gate.py --record-baseline
    python scripts/ci_two_interpreter_gate.py --primary-only
    python scripts/ci_two_interpreter_gate.py --gil-only
    python scripts/ci_two_interpreter_gate.py --max-divergence 25
    python scripts/ci_two_interpreter_gate.py --tests tests/test_agent.py

Exit codes: 0 pass, 1 regression/drift, 2 environment error (missing
interpreter, missing pytest, or no baseline recorded).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_REPORT = ROOT / "logs" / "ci_two_interpreter_gate.json"
DEFAULT_BASELINE = ROOT / "logs" / "ci_two_interpreter_gate_baseline.json"

_COUNT_RE = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "errors": re.compile(r"(\d+) errors?\b"),
    "skipped": re.compile(r"(\d+) skipped"),
    "xfailed": re.compile(r"(\d+) xfailed"),
    "xpassed": re.compile(r"(\d+) xpassed"),
    "deselected": re.compile(r"(\d+) deselected"),
}


def _load_python_env():
    """Import seam for `CORE.python_env`.

    A seam rather than a bare import so the absent-module path can be tested on
    a host that happens to HAVE the module -- a test that depends on this
    machine lacking it would prove nothing about the code.
    """
    from CORE import python_env

    return python_env


def resolve_interpreters() -> list[tuple[str, Path]]:
    """Return the (role, path) pairs to run, primary first.

    Resolution goes through `CORE/python_env.py` so the gate and production
    agree on which interpreter is which; a missing sidecar simply drops that
    role (and the comparison reports it) rather than crashing.

    Measured 2026-10-15: that promise was not kept. The import was unguarded, so
    on a host without `src/CORE/python_env.py` -- the state of this tree -- the
    gate died with an `ImportError` traceback instead of the ``exit 2
    environment error`` its own module docstring documents. A traceback cannot
    be told apart from a bug in the gate, and it skips the `if not interpreters`
    branch that exists to report precisely this. Returning an empty list routes
    the condition there.
    """
    try:
        python_env = _load_python_env()
    except ImportError as exc:
        print("[ci-two-interpreter] cannot import CORE.python_env (%s): no "
              "interpreter roles are resolvable, reporting an environment error "
              "rather than a verdict" % exc, file=sys.stderr)
        return []

    found: list[tuple[str, Path]] = [("primary", python_env.primary_python())]
    sidecar = python_env.gil_python()
    if sidecar is not None:
        found.append(("gil", sidecar))
    return found


def parse_counts(text: str) -> dict[str, int]:
    """Extract the pytest tallies from the tail of a run's output."""
    counts = {key: 0 for key in _COUNT_RE}
    for line in text.splitlines():
        for key, pattern in _COUNT_RE.items():
            matches = pattern.findall(line)
            if matches:
                # Last match wins: a summary line is authoritative, and a
                # warning line earlier in the run must not shadow it.
                counts[key] = int(matches[-1])
    return counts


def run_suite(
    interpreter: Path, tests: str, timeout: float, extra_env: dict[str, str]
) -> dict:
    """Run pytest once on one interpreter and return a parsed result record."""
    cmd = [
        str(interpreter),
        "-m",
        "pytest",
        tests,
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "--continue-on-collection-errors",
    ]

    env = dict(os.environ)
    # A GIL build aborts when PYTHON_GIL=0 is exported (~/.bashrc sets it), so
    # clear it for the child — same rule as CORE/python_env.probe().
    env.pop("PYTHON_GIL", None)
    env.update(extra_env)

    record: dict = {
        "role": None,
        "interpreter": str(interpreter),
        "cmd": cmd,
        "started_at": time.time(),
    }
    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), env=env, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace"
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        record.update(
            {
                "rc": proc.returncode,
                "completed": True,
                "counts": parse_counts(output),
                "tail": output.strip().splitlines()[-30:],
            }
        )
        if proc.returncode not in (0, 1):
            record["error"] = f"pytest exited {proc.returncode}"
    except subprocess.TimeoutExpired:
        record.update(
            {
                "rc": None,
                "completed": False,
                "counts": {key: 0 for key in _COUNT_RE},
                "error": f"timeout after {timeout:.0f}s",
                "tail": [],
            }
        )
    except FileNotFoundError:
        record.update(
            {
                "rc": None,
                "completed": False,
                "counts": {key: 0 for key in _COUNT_RE},
                "error": "interpreter-not-found",
                "tail": [],
            }
        )
    except Exception as exc:  # pragma: no cover - defensive
        record.update(
            {
                "rc": None,
                "completed": False,
                "counts": {key: 0 for key in _COUNT_RE},
                "error": f"{type(exc).__name__}: {exc}",
                "tail": [],
            }
        )

    record["duration_s"] = round(time.time() - record["started_at"], 2)
    return record


def compare(
    results: dict[str, dict],
    baseline: dict,
    tolerance: int,
    max_divergence: int | None,
    allow_collection_errors: bool,
) -> list[str]:
    """Return a list of failure reasons; empty list means the gate passed."""
    problems: list[str] = []

    for role, record in results.items():
        label = f"{role} ({record['interpreter']})"
        if not record.get("completed"):
            problems.append(f"{label}: run did not complete ({record.get('error')})")
            continue
        counts = record["counts"]
        if counts["errors"] and not allow_collection_errors:
            problems.append(
                f"{label}: {counts['errors']} collection error(s) "
                "— drift, not a pass"
            )
        base = baseline.get(role)
        if not base:
            problems.append(f"{label}: no baseline recorded for this role")
            continue
        floor = int(base.get("passed", 0)) - tolerance
        if counts["passed"] < floor:
            problems.append(
                f"{label}: {counts['passed']} passed < {floor} "
                f"(baseline {base.get('passed')} - tolerance {tolerance})"
            )

    if max_divergence is not None and {"primary", "gil"} <= set(results):
        passed = {
            role: results[role]["counts"]["passed"] for role in ("primary", "gil")
        }
        delta = abs(passed["primary"] - passed["gil"])
        if delta > max_divergence:
            problems.append(
                f"primary/GIL pass counts diverge by {delta} "
                f"(> --max-divergence {max_divergence}): "
                f"primary {passed['primary']}, gil {passed['gil']}"
            )

    return problems


def _print_summary(results: dict[str, dict], problems: list[str]) -> None:
    print("=" * 70)
    print("TWO-INTERPRETER SUITE GATE")
    print("=" * 70)
    for role, record in results.items():
        counts = record["counts"]
        status = "OK" if record.get("completed") else "INCOMPLETE"
        print(
            f"  {role:<8} {status:<10} "
            f"{counts['passed']:>5} passed, {counts['failed']:>3} failed, "
            f"{counts['skipped']:>3} skipped, {counts['errors']:>3} errors "
            f"({record['duration_s']}s)"
        )
        print(f"           {record['interpreter']}")
    if problems:
        print("\nFAIL:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("\nPASS: both interpreters are within their recorded envelopes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests", default="tests", help="test path (default: tests)")
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--tolerance", type=int, default=0,
                        help="allowed pass-count drop below baseline")
    parser.add_argument("--max-divergence", type=int, default=None,
                        help="fail when primary/GIL pass counts differ by more")
    parser.add_argument("--allow-collection-errors", action="store_true")
    parser.add_argument("--primary-only", action="store_true")
    parser.add_argument("--gil-only", action="store_true")
    parser.add_argument("--record-baseline", action="store_true")
    parser.add_argument("--report-file", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--baseline-file", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args(argv)

    interpreters = resolve_interpreters()
    if args.primary_only:
        interpreters = [item for item in interpreters if item[0] == "primary"]
    if args.gil_only:
        interpreters = [item for item in interpreters if item[0] == "gil"]
    if not interpreters:
        print("no interpreter selected/available", file=sys.stderr)
        return 2

    # Never let the gate itself re-exec into the sidecar; it drives children.
    os.environ["AZAZAEL_GIL_REEXEC"] = "1"

    results: dict[str, dict] = {}
    for role, interpreter in interpreters:
        record = run_suite(interpreter, args.tests, args.timeout, {})
        record["role"] = role
        results[role] = record

    baseline: dict = {}
    if args.baseline_file.exists():
        try:
            baseline = json.loads(
                args.baseline_file.read_text(encoding="utf-8")
            )
        except Exception as exc:  # pragma: no cover - defensive
            print(f"unreadable baseline {args.baseline_file}: {exc}", file=sys.stderr)

    if args.record_baseline:
        baseline = {
            role: {"passed": record["counts"]["passed"]}
            for role, record in results.items()
        }
        baseline["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        args.baseline_file.parent.mkdir(parents=True, exist_ok=True)
        args.baseline_file.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        print(f"baseline recorded -> {args.baseline_file}")

    payload = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tests": args.tests,
        "results": results,
    }

    # Run-to-run: a completed-run / baseline check needs a baseline.
    if not baseline and not args.record_baseline:
        print(
            f"no baseline at {args.baseline_file}; "
            "run --record-baseline first",
            file=sys.stderr,
        )
        problems = ["missing baseline"]
        exit_code = 2
    else:
        problems = compare(
            results, baseline, args.tolerance, args.max_divergence,
            args.allow_collection_errors,
        )
        exit_code = 1 if problems else 0

    payload["problems"] = problems
    args.report_file.parent.mkdir(parents=True, exist_ok=True)
    args.report_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    _print_summary(results, problems)
    print(f"report -> {args.report_file}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
