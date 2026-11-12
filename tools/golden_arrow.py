#!/usr/bin/env python3
"""GOLDEN ARROW — every test file, every time, with nothing able to hang it.

WHY THIS EXISTS (measured 2026-11-02)
-------------------------------------
`tests/` holds 6,217 tests and no gate ran them. Two consequences, both seen:

  * `python -m pytest -q tests` did not finish inside 600 s, twice, with
    `-n 8 --timeout=90` as well. A suite that cannot finish is not slow, it is
    WEDGED: one test blocks and the whole run reports nothing.
  * imports were tested for months. `src/CORE/langgraph_integration.py` raised
    `NameError: TypedDict is not defined` on import and nobody noticed, because
    nothing imported it. The error was found by a real test, not by an import
    sweep -- the operator's point, recorded here so it is not re-derived.

So the arrow does not run "pytest". It runs ONE FILE PER PROCESS with its own
wall bound. A file that wedges is named and marked hung; the other hundreds
still report. That is the difference between a gate and a wish.

WHERE IT LIVES
--------------
    runner:   tools/golden_arrow.py          (this file)
    report:   logs/golden_arrow.json         (last run, machine-readable)
    gate:     tools/preflight_all.py         gate name `golden-arrow`
    hook:     .githooks/pre-commit           runs `--staged` unless ALLOW_FAST_COMMIT=1
    evidence: logs/golden_arrow.json is what a commit message quotes

AFTER THE SUITE: DEEP DIVE IS ON
--------------------------------
A full run does not stop at "tests pass". It then runs `tools/deep_dive.py`
(forensic audits + a bounded web leg) and folds the forensic verdict into this
run's exit code. Tests and audits answer different questions -- the suite says
the behaviour holds, the audit says the tree parses and its claims re-derive --
and a run that answers only the first is the state that let a `NameError` live
for months. `--no-deep-dive` turns it off; a web leg that cannot reach the
network records a skip and never fails the run.

MODES
-----
    python tools/golden_arrow.py                # every test file (the default)
    python tools/golden_arrow.py --staged       # only files touched by the index
    python tools/golden_arrow.py --file tests/test_x.py
    python tools/golden_arrow.py --strict      # fail on ANY failing file
    python tools/golden_arrow.py --baseline    # record today's failures as debt
    python tools/golden_arrow.py --status       # read the last report, no run
    python tools/golden_arrow.py --json out.json --timeout 120 --workers 8

BASELINE, AND WHY IT IS NOT A SILENCER
--------------------------------------
The first full run (2026-11-02) found 24 failing tests across 16 files -- real
ones, not noise: a vacuous type-check baseline, a dead orchestration entry
point, five credential-shaped protocol markers. **The baseline file was absent
until 2026-11-02 15:34Z** despite the docstring above claiming it existed, so
every run reported every failure as NEW: a ratchet that had never been armed,
which reads to the operator exactly like a tree that just broke. It now holds
14 files (two were fixed the same session) and is printed on every run as DEBT,
with the file names. The gate fails on a NEW failure or on a file that starts
HANGING. Arm it with `--baseline`, or `--status --baseline` to record the report
already on disk without a second full run.
That is a ratchet, not an amnesty: the list can only shrink, and `--strict`
fails on the debt too, so a lane can pay it down one file at a time.

EXIT CODES
----------
    0  every file ran; no failure that is not already recorded debt
    1  a NEW failure, a new error, or a hung file (the report names all three)
    2  the run could not be performed (no interpreter, no tests collected)

A hung file is NEVER a pass. It is not a skip either: it is a defect with a
name, because the test that hangs is the test whose assertion might have found
the next NameError.
"""
from __future__ import annotations

import argparse
import argparse as _ap
import concurrent.futures as futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "logs" / "golden_arrow.json"
BASELINE = ROOT / "logs" / "golden_arrow_baseline.json"
#: Test roots, in the order a reader would look for them.
ROOTS = ("tests", "Project_ELIZA/tests", "Project_Nurse_Ratchet/tests",
         "Project_FLAMING_SWORD/tests", "Project_CRADLE/tests",
         "Project_SHARDEX/tests")
DEFAULT_TIMEOUT = 360.0
DEFAULT_WORKERS = min(8, (os.cpu_count() or 4))


def _profile(key: str, fallback):
    """Settings from `config/defaults.json`, with the constant above as fallback."""
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import defaults_profile            # noqa: PLC0415
        return defaults_profile.read_default(key, fallback)
    except Exception:
        return fallback

_SUMMARY = re.compile(r"^(?:=+\s*)?(\d+ (?:passed|failed|error|errors|skipped))", re.M)


def interpreter() -> str:
    """The production interpreter, or the one running this file.

    3.15t free-threaded is production (AGENTS.md). A gate that runs the suite on
    a different interpreter is measuring a program nobody ships.
    """
    pinned = Path("C:/Azazael/.venvs/py316t/Scripts/python.exe")
    if pinned.exists():
        return str(pinned)
    return sys.executable


def test_files(argv) -> list:
    if argv.file:
        # Resolved, because a relative `--file tests/x.py` used to reach
        # `run_one` as a RELATIVE path and `relative_to(ROOT)` raised
        # ValueError -- so the single-file mode crashed instead of running one
        # file. Measured 2026-11-02.
        return [(ROOT / argv.file).resolve()]
    if argv.staged:
        out = subprocess.run(["git", "diff", "--cached", "--name-only",
                              "--diff-filter=ACMR"], cwd=str(ROOT),
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60).stdout
        picked = [ROOT / line.strip() for line in out.splitlines()
                  if line.strip().endswith(".py") and Path(line.strip()).name.startswith("test_")]
        return sorted(set(picked))
    files = []
    for rel in ROOTS:
        base = ROOT / rel
        if not base.exists():
            continue
        files.extend(sorted(p for p in base.rglob("test_*.py")))
    return files


def run_one(path: Path, timeout: float, py: str) -> dict:
    """Run one test file in its own process. Never raises; records instead."""
    started = time.monotonic()
    cmd = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           "--timeout", str(min(120, int(timeout))), "--timeout-method=thread",
           str(path)]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              env=env)
        out = (proc.stdout or "") + (proc.stderr or "")
        rc = proc.returncode
        hung = False
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + \
              ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
        rc, hung = None, True
    seconds = round(time.monotonic() - started, 2)
    counts = {}
    for word in ("passed", "failed", "skipped", "error", "errors"):
        m = re.search(r"(\d+) %s\b" % word, out)
        if m:
            counts[word] = int(m.group(1))

    # A file that exits non-zero WITHOUT a summary line never reached a verdict:
    # pytest-timeout's thread method aborts the process (rc=1, a faulthandler
    # dump and no "N passed"), and so does a segfault. Calling that a test FAIL
    # sends the reader hunting an assertion that was never evaluated -- measured
    # 2026-11-02 on tests/test_bible_ship.py, where the real cause was a per-test
    # bound, not a defect the test found.
    no_verdict = (not hung and rc not in (0, 5)
                  and not ({"passed", "failed", "error", "errors", "skipped"} & set(counts)))
    try:
        rel = path.resolve().relative_to(ROOT)
    except ValueError:                       # outside the tree: report as given
        rel = path
    return {
        "file": str(rel).replace("\\", "/"),
        "rc": rc,
        "hung": hung,
        "no_verdict": no_verdict,
        "seconds": seconds,
        "counts": counts,
        # The six-line tail below is usually faulthandler frames (pytest.ini sets
        # faulthandler_timeout=20), so it names the THREAD that was busy and not
        # the test that failed. Measured 2026-11-04: a full run reported
        # tests/test_preflight_all.py as FAIL with no way to tell which of its
        # 165 tests failed, because the only recorded lines were stack frames.
        # pytest's own short summary carries the node id and the reason; record
        # it alongside the tail so a failure can name itself.
        "failed_tests": [line.strip()[:300] for line in out.splitlines()
                         if line.startswith(("FAILED ", "ERROR "))][:20],
        "tail": out.strip().splitlines()[-6:] if out.strip() else [],
    }


def summarise(rows: list, elapsed: float, argv) -> dict:
    failed = [r for r in rows if r["rc"] not in (0, 5) and not r["hung"]]
    hung = [r for r in rows if r["hung"]]
    killed = [r for r in rows if r.get("no_verdict")]
    passed = [r for r in rows if r["rc"] in (0, 5)]
    named = [{"file": r["file"], "test": t}
             for r in rows for t in r.get("failed_tests", [])]
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "staged" if argv.staged else ("file" if argv.file else "full"),
        "interpreter": interpreter(),
        "timeout_s": argv.timeout,
        "workers": argv.workers,
        "elapsed_s": round(elapsed, 1),
        "totals": {
            "files": len(rows),
            "passed_files": len(passed),
            "failed_files": len(failed),
            "hung_files": len(hung),
            "killed_files": len(killed),
            "tests_passed": sum(r["counts"].get("passed", 0) for r in rows),
            "tests_failed": sum(r["counts"].get("failed", 0) for r in rows),
            "tests_error": sum(r["counts"].get("error", r["counts"].get("errors", 0))
                               for r in rows),
        },
        "failed": failed,
        "hung": hung,
        # Flat, greppable list of every test pytest named as FAILED/ERROR.
        "failures": named,
        "files": rows,
    }


def write_baseline(rep: dict) -> dict:
    """Record the report's failing files as known debt. Returns the known map."""
    # `tests` names what the debt actually is, so paying a file down does not
    # require re-running it to rediscover which test was failing.
    known = {r["file"]: {"counts": r["counts"],
                          "tests": r.get("failed_tests", []),
                          "since": rep["generated"]}
             for r in rep["failed"]}
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps({
        "generated": rep["generated"],
        "note": ("known failing test files. The gate fails on a NEW failure and on "
                 "any HUNG file; --strict fails on this list too. It may only shrink: "
                 "pay a file down, run --baseline again, and it leaves the list."),
        "known": known,
    }, indent=2) + "\n", encoding="utf-8")
    print("baseline recorded: %d known failing file(s) -> %s"
          % (len(known), BASELINE.name))
    return known


def deep_dive_stage(py: str, verbose: bool) -> tuple:
    """Run `tools/deep_dive.py` as a child. Returns (rc, detail); never raises."""
    script = ROOT / "tools" / "deep_dive.py"
    if not script.exists():
        return 2, "tools/deep_dive.py missing"
    cmd = [py, str(script), "--json", str(ROOT / "logs" / "deep_dive.json")]
    if verbose:
        cmd.append("--verbose")
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        return 2, "deep dive exceeded its 600s bound"
    lines = (proc.stdout or "").strip().splitlines()
    for line in lines[-10:]:
        print(line)
    if proc.returncode not in (0, 1):
        err = (proc.stderr or "").strip().splitlines()[-2:]
        return proc.returncode, " | ".join(lines[-1:] + err)
    return proc.returncode, (lines[-1] if lines else "no output")


def load_baseline() -> dict:
    """Recorded debt: `{file: {counts, since}}`. Absent file = no debt recorded."""
    if not BASELINE.exists():
        return {}
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8")).get("known", {})
    except ValueError:
        return {}


def split_debt(rep: dict, known: dict) -> tuple:
    """(new failures, known debt). A HUNG file is never known debt.

    Hanging is the one state that must not be grandfathered: a wedged test stops
    reporting on everything queued behind it, so "it hung yesterday too" is an
    argument for fixing it today, not for recording it.
    """
    new, debt = [], []
    for row in list(rep["failed"]) + list(rep["hung"]):
        # A killed file is never known debt, for the same reason a hung one is
        # not: "it died yesterday too" is an argument to fix it, not to record it.
        if row["hung"] or row.get("no_verdict") or row["file"] not in known:
            new.append(row)
        else:
            debt.append(row)
    return new, debt


def print_report(rep: dict, verbose: bool, known: dict = None, strict: bool = False) -> int:
    t = rep["totals"]
    known = known or {}
    new, debt = split_debt(rep, known)
    print("GOLDEN ARROW -- %s run, %d file(s) in %ss on %s"
          % (rep["mode"], t["files"], rep["elapsed_s"],
             Path(rep["interpreter"]).name))
    print("  files:  %d passed, %d failed, %d HUNG, %d KILLED (no verdict)"
          % (t["passed_files"], t["failed_files"], t["hung_files"],
             t.get("killed_files", 0)))
    print("  tests:  %d passed, %d failed, %d error(s)"
          % (t["tests_passed"], t["tests_failed"], t["tests_error"]))
    for row in new:
        mark = "HUNG" if row["hung"] else ("KILLED" if row.get("no_verdict") else "FAIL")
        reason = " -- no summary line: killed or aborted, NOT a test verdict" \
            if row.get("no_verdict") else " -- NOT in the baseline"
        print("  %s  %s (rc=%s, %ss)%s"
              % (mark, row["file"], row["rc"], row["seconds"], reason))
        named = row.get("failed_tests") or []
        for line in named:
            print("        %s" % line)
        if not named:
            # No node id: the file died before pytest could name a test. Say so
            # rather than printing stack frames as if they were the diagnosis.
            for line in (row["tail"][-3:] if not row["hung"] else []):
                print("        %s" % line)
    if debt:
        print("  DEBT: %d file(s) recorded in %s" % (len(debt), BASELINE.name))
        for row in debt:
            print("        %-58s %s" % (row["file"], row["counts"]))
    if verbose:
        for row in rep["files"]:
            if row["rc"] in (0, 5) and not row["hung"]:
                print("  ok    %-58s %5ss %s" % (row["file"], row["seconds"], row["counts"]))
    if new:
        print("DEAD-SHOT: %d NEW defect(s). A hung file is not a pass." % len(new))
        return 1
    if debt and strict:
        print("DEAD-SHOT (strict): %d recorded defect(s) still open." % len(debt))
        return 1
    if debt:
        print("CLEAN of new defects; %d file(s) of recorded debt remain (--strict fails on them)."
              % len(debt))
        return 0
    print("CLEAN: every collected test file ran and passed.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--staged", action="store_true", help="only test files in the index")
    ap.add_argument("--file", default="", help="one test file")
    ap.add_argument("--timeout", type=float,
                    default=float(_profile("golden_arrow.timeout_s", DEFAULT_TIMEOUT)),
                    help="wall bound per file, seconds")
    ap.add_argument("--workers", type=int,
                    default=int(_profile("golden_arrow.workers", DEFAULT_WORKERS)))
    ap.add_argument("--json", default="",
                    help="where the report lands (default: logs/golden_arrow[_mode].json)")
    ap.add_argument("--baseline", action="store_true",
                    help="record this run's failing files as known debt")
    # `--no-strict` exists so a config-level `golden_arrow.strict=true` can still
    # be relaxed for one run: a flag that can only turn a gate ON leaves the
    # operator editing the shared file other lanes read.
    ap.add_argument("--strict", action=argparse.BooleanOptionalAction,
                    default=bool(_profile("golden_arrow.strict", False)),
                    help="fail on recorded debt as well as new failures "
                         "(default: golden_arrow.strict)")
    ap.add_argument("--force", action="store_true",
                    help="run even when golden_arrow.enabled is false")
    ap.add_argument("--status", action="store_true", help="print the last report only")
    ap.add_argument("--no-deep-dive", action="store_true",
                    help="skip the forensic + web stage after the suite")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.status:
        if not REPORT.exists():
            print("no report at %s -- run the arrow first" % REPORT)
            return 2
        last = json.loads(REPORT.read_text(encoding="utf-8"))
        if args.baseline:
            # `--status --baseline` records debt from the report already on disk.
            # Without it, arming the ratchet costs a second full suite run, and a
            # ratchet nobody arms is a gate that fails on everything -- which is
            # how a gate gets switched off.
            write_baseline(last)
        return print_report(last, args.verbose, load_baseline(), args.strict)

    # HED-8: the switch that turns the arrow off lives in the shared config, and
    # it reports its own state. Exiting 2 is deliberate -- `could not run` is the
    # one code a caller must not read as success, and a disabled gate is exactly
    # that. --force is the escape hatch, so nobody has to edit the shared file.
    if not args.force and not bool(_profile("golden_arrow.enabled", True)):
        print("GOLDEN ARROW: disabled by golden_arrow.enabled=false. Nothing ran -- "
              "a switched-off gate is not a pass. Re-enable with `defaults_profile.py "
              "--set golden_arrow.enabled true`, or run --force.")
        return 2

    py = interpreter()
    files = test_files(args)
    if not files:
        print("GOLDEN ARROW: no test files collected -- the arrow cannot fly empty")
        return 2

    started = time.monotonic()
    rows = []
    with futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        jobs = {pool.submit(run_one, f, args.timeout, py): f for f in files}
        for job in futures.as_completed(jobs):
            row = job.result()
            rows.append(row)
            mark = ("HUNG" if row["hung"] else
                    ("KILLED" if row.get("no_verdict") else
                     ("ok" if row["rc"] in (0, 5) else "FAIL")))
            print("  %-4s %-58s %6ss" % (mark, row["file"], row["seconds"]), flush=True)
    rows.sort(key=lambda r: r["file"])
    rep = summarise(rows, time.monotonic() - started, args)

    # A --file or --staged run must NOT overwrite the full-suite report: the
    # deep dive's `golden_arrow_status` leg and every commit message quote
    # logs/golden_arrow.json, and a one-file run standing in for the whole suite
    # is a stale artifact reading as evidence. Measured 2026-11-02.
    if args.json:
        out = Path(args.json)
    elif rep["mode"] == "full":
        out = REPORT
    else:
        out = ROOT / "logs" / ("golden_arrow_%s.json" % rep["mode"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")

    if args.baseline:
        write_baseline(rep)

    print("")
    rc = print_report(rep, args.verbose, load_baseline(), args.strict)
    print("report: %s" % out)

    # Deep dive defaults ON for a full run (a --staged run is a commit check and
    # must stay fast; it reports that forensics did not run rather than implying
    # they did).
    wants_dive = (not args.no_deep_dive and rep["mode"] == "full"
                  and bool(_profile("deep_dive.enabled", True)))
    if wants_dive:
        print("")
        d_rc, d_detail = deep_dive_stage(py, args.verbose)
        if d_rc == 1:
            print("DEAD-SHOT (deep dive): a forensic leg failed -- see logs/deep_dive.json")
            rc = 1
        elif d_rc not in (0, 1):
            print("DEEP DIVE: not performed (%s). A missing reading is not a pass."
                  % d_detail)
    elif not args.no_deep_dive:
        print("deep dive: not run in %s mode (forensics run on a full pass)"
              % rep["mode"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
