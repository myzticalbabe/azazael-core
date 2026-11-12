#!/usr/bin/env python3
"""run_full_suite.py — run the whole test suite in parallel, resumably.

Why this exists
---------------
`pytest tests` is 3628 tests across 175 files and takes ~70 minutes end to end.
The first attempt to observe it was killed by its own 580s timeout at 14% and
looked exactly like a deadlock; it was not. `tests/test_cluster_integration_p1.py`
alone needs 147s for 21 tests and passes 21/21 when given time.

This runs test FILES across N worker processes and writes one result JSON per
file under `output/suite_results/`. A file that already has a result is SKIPPED,
so an interrupted run resumes instead of restarting. Budgeted invocations:

    python scripts/run_full_suite.py --budget 540     # ~9 min, repeat until done
    python scripts/run_full_suite.py --summary        # what is recorded so far
    python scripts/run_full_suite.py --reset          # start over

A failure recorded here is a HYPOTHESIS, not a verdict
------------------------------------------------------
Parallel workers share the machine. `tests/conftest.py` redirects the memory
store, the entrenchment snapshot, the containment pen and the spine state into
per-session temp dirs, but a test that binds a fixed TCP port, hits the network,
or asserts a wall-clock threshold can still be perturbed by a sibling worker.
Measured this session: `test_dm_advanced` and `test_perf_threshold` both failed
in parallel and both pass alone. **Re-run every failure alone before believing
it** — discovery in parallel, truth in sequence. `--only` does exactly that.

Known non-failures it recognises
--------------------------------
`tests/test_optimizers_convergence.py` deliberately skips at MODULE level
(mygrad -> numba faults at C level in a free-threaded build, and a segfault
cannot be caught), so pytest collects nothing and exits 5. That is a documented
outcome of a correct guard, not a broken file, and it is reported as `SKIP`
rather than as a failure.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = ROOT / "output" / "suite_results"
PYTHON = sys.executable
PER_FILE_TIMEOUT = 300

#: rc=5 means "no tests collected". It is only a non-failure when the file says
#: why: a module-level `pytest.skip(..., allow_module_level=True)`.
NO_TESTS_COLLECTED = 5


def test_files() -> list[pathlib.Path]:
    return sorted((ROOT / "tests").glob("test_*.py"))


def result_path(f: pathlib.Path) -> pathlib.Path:
    return RESULTS / (f.stem + ".json")


def _timeout_text(blob) -> str:
    """TimeoutExpired stdout/stderr stay bytes even under text=True."""
    if blob is None:
        return ""
    if isinstance(blob, bytes):
        return blob.decode("utf-8", "replace")
    return str(blob)


def run_one(f: pathlib.Path) -> dict:
    started = time.time()
    timeout_output = ""
    try:
        proc = subprocess.run(
            [PYTHON, "-m", "pytest", str(f.relative_to(ROOT)),
             "-q", "--tb=no", "-rf", "-p", "no:randomly"],
            cwd=str(ROOT), capture_output=True, text=True,
            timeout=PER_FILE_TIMEOUT, encoding="utf-8", errors="replace"
        )
        rc, text = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        # rc stays -9 (a distinct, greppable code) but the output the file
        # printed before it wedged is kept: "TIMEOUT" alone cannot tell a
        # deadlock from a test stuck on one slow call, and that partial text is
        # the only evidence a timeout leaves. It is stored as its own field so
        # the summary/failure parsing below is unchanged.
        rc, text = -9, f"TIMEOUT after {PER_FILE_TIMEOUT}s"
        timeout_output = _timeout_text(exc.stdout) + _timeout_text(exc.stderr)
    except Exception as exc:  # a worker crash must not kill the run
        rc, text = -8, f"runner error: {exc!r}"

    lines = [ln for ln in text.splitlines() if ln.strip()]
    failures = [ln for ln in lines if ln.startswith("FAILED ")]
    tail = [ln for ln in lines if
            ("passed" in ln or "failed" in ln or "skipped" in ln)]
    summary = tail[-1] if tail else (lines[-1] if lines else "")

    outcome = "pass" if rc == 0 else "fail"
    if rc == NO_TESTS_COLLECTED and "skipped" in summary:
        outcome = "skip"   # a documented module-level skip, see the docstring

    rec = {
        "file": str(f.relative_to(ROOT)),
        "rc": rc,
        "outcome": outcome,
        "seconds": round(time.time() - started, 1),
        "failures": failures[:40],
        "summary": summary,
        "timeout_output": timeout_output[-4000:],
    }
    try:
        result_path(f).parent.mkdir(parents=True, exist_ok=True)
        result_path(f).write_text(json.dumps(rec, indent=2), encoding="utf-8")
    except Exception:
        pass
    return rec


def summarise() -> int:
    recs = [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(RESULTS.glob("*.json"))]
    bad = [r for r in recs if r.get("outcome", "fail") == "fail"]
    skipped = [r for r in recs if r.get("outcome") == "skip"]
    print(f"recorded {len(recs)}/{len(test_files())} files; "
          f"{len(bad)} failing, {len(skipped)} module-level skips")
    if skipped:
        for r in skipped:
            print(f"  SKIP {r['file']:<56} {r['summary'][:60]}")
    for r in bad:
        print(f"  rc={r['rc']:>4} {r['file']:<56} {r['summary'][:70]}")
        for fl in r["failures"][:8]:
            print(f"        {fl[:120]}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=0.0,
                    help="stop launching new files after this many seconds")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--only", action="append", default=[],
                    help="run these paths sequentially (use to re-verify a failure)")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    if args.summary:
        return summarise()

    if args.reset and RESULTS.exists():
        for p in RESULTS.glob("*.json"):
            p.unlink()

    if args.only:
        for path in args.only:
            p = pathlib.Path(path)
            if not p.is_absolute():
                p = ROOT / path
            rec = run_one(p)
            print(f"{rec['outcome']:<4} rc={rec['rc']:<4} {rec['file']:<52} "
                  f"{rec['summary'][:70]}")
            for fl in rec["failures"][:20]:
                print("     " + fl[:140])
        return 0

    pending = [f for f in test_files() if not result_path(f).exists()]
    print(f"{len(pending)} files pending, {args.workers} workers")
    t0 = time.time()
    done = 0

    def worker(f: pathlib.Path):
        if args.budget and (time.time() - t0) > args.budget:
            return None
        return run_one(f)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for rec in ex.map(worker, pending):
            if rec is None:
                continue
            done += 1
            print(f"[{done}/{len(pending)}] {rec['outcome']:<4} "
                  f"{rec['seconds']:>6.1f}s {rec['file']}", flush=True)
    print(f"elapsed {time.time() - t0:.0f}s")
    return summarise()


if __name__ == "__main__":
    raise SystemExit(main())