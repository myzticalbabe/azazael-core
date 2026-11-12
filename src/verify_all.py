#!/usr/bin/env python3
# coding: utf-8
"""
verify_all.py — CI-style verification runner for Azazael.

Chains the project's own checkers into a single pass so the class of bug
caught manually during the 2026-08-28 hardening session (inference-worm
dispatcher loops, stdlib-logging shadowing, import cycles, missing exports)
is caught automatically on every change.

Layers run, in order:
  1. doctor verify   — read-only health checks (DB integrity/size, audit
                       trail, sessions, Ollama) via CORE.doctor
  2. compliance      — RULE 0X + 0Y self-test (13/13) via
                       CORE.compliance.run_self_test
  3. gate self-tests — pre_action_gate (13 heuristic), enforcement_harness,
                       sentinel_enforcer (5/5 via import invocation)
  4. import sweep    — every CORE / CORE.WORMS / SCRIPTS module imports
                       cleanly (subprocess per module for isolation)
  5. audit chain     — hash-chain verification of every .audit session file

All checks are READ-ONLY with respect to the memory DB (no purge, no
retention, no writes to memory_store.db or its WAL/SHM).

Usage:
    python verify_all.py                 # full run
    python verify_all.py --quick         # doctor + compliance + gates only
    python verify_all.py --import-only   # import sweep only
    python verify_all.py --json          # machine-readable summary

Exit codes:
    0  all checks passed
    1  one or more checks failed
    2  environment error (bad arguments, runner bug)
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

ROOT = Path(__file__).resolve().parent
PY = sys.executable

# ---------------------------------------------------------------------------
# Check runner helpers
# ---------------------------------------------------------------------------


def run_cmd(args: list[str], timeout: int = 120, label: str = "") -> tuple[bool, str]:
    """Run a subprocess and return (ok, tail of combined output)."""
    try:
        proc = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        note_failure("verify_all:L64", exc)
        return False, f"[TIMEOUT after {timeout}s] {label}"
    except OSError as exc:
        note_failure("verify_all:L66", exc)
        return False, f"[OSError: {exc}] {label}"
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(line for line in out.splitlines() if line.strip())[-600:]
    return proc.returncode == 0, tail


def py_cmd(code: str, timeout: int = 120) -> tuple[bool, str]:
    """Run a short python snippet inside the project."""
    return run_cmd([PY, "-c", code], timeout=timeout)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_doctor() -> tuple[bool, str]:
    """Layer 1 — doctor read-only health checks."""
    return run_cmd(
        [PY, "-m", "CORE.doctor", "--quick"],
        timeout=180,
        label="doctor verify",
    )


def check_compliance() -> tuple[bool, str]:
    """Layer 2 — compliance self-test (RULE 0X + 0Y, 13/13)."""
    return run_cmd(
        [PY, "CORE/compliance/run_self_test.py"],
        timeout=180,
        label="compliance self-test",
    )


def check_pre_action_gate() -> tuple[bool, str]:
    """Layer 3a — pre-action gate heuristic self-test (13/13)."""
    return py_cmd(
        "import sys; sys.path.insert(0, '.'); "
        "from CORE.compliance.pre_action_gate import self_test; "
        "sys.exit(0 if self_test() else 1)",
        timeout=120,
    )


def check_enforcement_harness() -> tuple[bool, str]:
    """Layer 3b — enforcement harness self-test (6/6)."""
    return py_cmd(
        "import sys; sys.path.insert(0, '.'); "
        "from CORE.enforcement_harness import self_test; "
        "sys.exit(0 if self_test() else 1)",
        timeout=120,
    )


def check_sentinel_enforcer() -> tuple[bool, str]:
    """Layer 3c — sentinel enforcer self-test (5/5).

    Invoked via import (the way doctor/orchestrator use it). The
    `python CORE/sentinel_enforcer.py --test` entrypoint has a pre-existing
    double-audit-hook issue and is intentionally not used here.
    """
    return py_cmd(
        "import sys; sys.path.insert(0, '.'); "
        "from CORE.sentinel_enforcer import self_test; "
        "sys.exit(0 if self_test() else 1)",
        timeout=120,
    )


def _module_candidates() -> list[str]:
    """Enumerate CORE / CORE.WORMS / SCRIPTS modules to import-check."""
    packages = [("CORE", "CORE"), ("CORE.WORMS", "CORE/WORMS"), ("SCRIPTS", "SCRIPTS")]
    mods: set[str] = set()
    for pkg, rel in packages:
        mods.add(pkg)
        base = ROOT / rel
        if not base.is_dir():
            continue
        for f in sorted(base.glob("*.py")):
            if f.name.startswith(".") or f.name == "__init__.py":
                continue
            if "backup" in f.name or f.name.endswith(".pre_gate_backup"):
                continue
            mods.add(f"{pkg}.{f.stem}")
    return sorted(mods)


def check_import_sweep() -> tuple[bool, str]:
    """Layer 4 — every src module imports cleanly (isolated subprocesses)."""
    mods = _module_candidates()
    failures: list[tuple[str, str]] = []
    for i, mod in enumerate(mods, 1):
        ok, out = py_cmd(
            f"import sys; sys.path.insert(0, '.'); import {mod}",
            timeout=60,
        )
        if not ok:
            # pull the meaningful error line (last non-empty)
            lines = [ln for ln in out.splitlines() if ln.strip()]
            err = lines[-1][:160] if lines else "(no output)"
            failures.append((mod, err))
        if i % 25 == 0:
            print(f"    import sweep {i}/{len(mods)}", flush=True)
    summary = (
        f"import sweep: {len(mods) - len(failures)}/{len(mods)} modules OK"
        + (f", {len(failures)} FAILED" if failures else "")
    )
    if failures:
        detail = "\n".join(f"    {m}: {e}" for m, e in failures[:25])
        if len(failures) > 25:
            detail += f"\n    ... and {len(failures) - 25} more"
        return False, summary + "\n" + detail
    return True, summary


def check_regression_tests() -> tuple[bool, str]:
    """Layer 4b — regression tests for session fixes."""
    return run_cmd(
        [PY, "-m", "unittest", "tests.test_verify_fixes", "-v"],
        timeout=180,
        label="regression tests",
    )


def check_audit_chain() -> tuple[bool, str]:
    """Layer 5 — verify hash chains of every .audit session file."""
    audit_dir = ROOT / ".audit"
    if not audit_dir.is_dir():
        return False, "audit dir missing: .audit/"
    return py_cmd(
        "import sys, glob; sys.path.insert(0, '.'); "
        "from CORE.audit_trail import verify_chain; "
        "files = sorted(glob.glob('.audit/session_*.jsonl')); "
        "bad = [f for f in files if not verify_chain(f)[0]]; "
        "print(f'audit chains: {len(files) - len(bad)}/{len(files)} ok'); "
        "sys.exit(1 if bad else 0)",
        timeout=180,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Azazael CI-style verification runner")
    parser.add_argument("--quick", action="store_true", help="skip the import sweep and audit chain")
    parser.add_argument("--import-only", action="store_true", help="run only the import sweep")
    parser.add_argument("--json", action="store_true", help="emit machine-readable summary")
    args = parser.parse_args()

    if args.import_only:
        ok, out = check_import_sweep()
        print(out)
        return 0 if ok else 1

    suite: list[tuple[str, object]] = [
        ("doctor verify", check_doctor),
        ("compliance self-test", check_compliance),
        ("pre_action_gate", check_pre_action_gate),
        ("enforcement_harness", check_enforcement_harness),
        ("sentinel_enforcer", check_sentinel_enforcer),
    ]
    if not args.quick:
        suite.append(("import sweep", check_import_sweep))
        suite.append(("regression tests", check_regression_tests))
        suite.append(("audit chain", check_audit_chain))

    results: list[tuple[str, bool, str]] = []
    started = time.time()
    for label, fn in suite:
        print(f"\n==> {label}", flush=True)
        ok, out = fn()  # type: ignore[operator]
        results.append((label, ok, out))
        status = "PASS" if ok else "FAIL"
        print(f"    [{status}] {label}")
        if not ok and out:
            # show the failure detail immediately
            print(out[-500:])
        if not ok:
            # fail fast on the first broken layer to keep runs short
            print("\nverify_all: FAILED at " + label + " — stopping early.")
            break

    elapsed = time.time() - started
    failures = [r for r in results if not r[1]]
    summary = {
        "runner": "verify_all",
        "elapsed_s": round(elapsed, 1),
        "checks": {label: "PASS" if ok else "FAIL" for label, ok, _ in results},
        "passed": len(results) - len(failures),
        "failed": len(failures),
        "healthy": not failures,
    }

    if args.json:
        print("\n" + json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("\n" + "=" * 56)
        print(f"  verify_all: {'ALL PASS' if summary['healthy'] else 'FAILURES FOUND'}"
              f" ({summary['passed']}/{summary['passed'] + summary['failed']} checks, {elapsed:.1f}s)")
        for label, ok, _ in results:
            print(f"    [{'PASS' if ok else 'FAIL'}] {label}")
        print("=" * 56)

    return 0 if summary["healthy"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nverify_all: interrupted.")
        sys.exit(130)
