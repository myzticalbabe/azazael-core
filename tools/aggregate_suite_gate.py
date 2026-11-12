#!/usr/bin/env python3
# coding: utf-8
"""Aggregate pytest gate — fail on NEW aggregate-only failures.

Why this exists
---------------
`run_tests.py` and the CI job run each test FILE in its own process. That shape
cannot see the defect class this repository actually has: measured 2026-09-22, a
single-process run of `tests/` returned 72 failures and 69 errors while **every
failing file passed green when run alone** (test_sb3_agent 42, test_nature_
metacluster_unit 74, test_fs_probe_backend 13, test_wiring_cycle 19,
test_evolution_gates 61, test_import_map 8, ...). Isolation defects — shared
registry/discovery state, a process-global write guard, module identity — only
exist *across* time inside one interpreter, so a per-file runner is blind to
them by construction.

Why a baseline rather than a plain exit code
--------------------------------------------
The remaining failures are known facts, and this project's own principle is that
"a check that fails on facts gets switched off". So the gate compares against
`logs/aggregate_suite_baseline.json`:

* a failure id that is NOT in the baseline -> exit 1 (a regression),
* a baseline id that no longer fails      -> reported, exit 0 (an improvement),
  with a hint to re-baseline,
* a run that never completed              -> exit 1 (a timeout is not a pass).

Usage
-----
    python tools/aggregate_suite_gate.py --check       # CI: exit 1 on new ids
    python tools/aggregate_suite_gate.py --baseline    # record the known set
    python tools/aggregate_suite_gate.py --report      # run + write the report
    python tools/aggregate_suite_gate.py --check --only test_wiring  # subset
    python tools/aggregate_suite_gate.py --diff-from logs/report.json

A narrowed run writes somewhere else, on purpose
-------------------------------------------------
`--only` selects a SUBSET, so its report is not the profile's report and its
baseline is not the profile's baseline. Measured 2026-09-22: a one-file smoke
test of the stall watchdog (`--only tests/test_aggregate_suite_gate.py --profile
isolation`) overwrote `logs/aggregate_isolation_report.json` with
`1 passed in 1s`, replacing a 299-passed baseline with a run that never touched
seven of the eight profile files -- a success-shaped artifact answering for a
profile it no longer described. So a narrowed run with no explicit
`--report-file` / `--baseline-file` is redirected to a `.partial` sibling and
says so, which also makes a narrowed `--check` refuse rather than compare
against a baseline it does not correspond to.

CI uses the isolation profile, which is the same contract over the
 dependency-light subset whose cross-file failures are reproducible off the
development machine (see ISOLATION_PROFILE for the selection rule):

    python tools/aggregate_suite_gate.py --check --profile isolation

Both entry points are intentionally the SAME script. A second, thinner
implementation for CI is how a gate stops describing what runs locally.
"""
from __future__ import annotations

import argparse
import json
import queue as queue_mod
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: `tools/` is sys.path[0] when this file is run directly, and the tests that
#: import it put `tools/` on sys.path first -- so the shared ADR-012 rule resolves
#: the same way from both entry points.
import evidence_paths as evidence

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "logs" / "aggregate_suite_baseline.json"
REPORT = REPO / "logs" / "aggregate_suite_report.json"

#: The dependency-light ISOLATION PROFILE, for CI.
#:
#: The full suite cannot gate in CI: it needs torch / stable-baselines3 /
#: transformers, and a missing dependency shows up as hundreds of collection
#: errors indistinguishable from real regressions. But the defect class this
#: gate exists for -- two files that are green alone and red together -- does
#: not need the heavy stack. These files were MEASURED to fail in the aggregate
#: run of 2026-09-22 while passing alone, and they import only stdlib + CORE, so
#: the same set gates identically on a laptop and on a CI runner.
#:
#: Selection rule, applied when extending the list: (1) the file failed in a
#: single-process run and passed alone, (2) `grep -lE "torch|stable_baselines3|
#: transformers|sklearn"` does not match it, (3) it imports no other test file's
#: help except through CORE. Rule 2 is what makes the CI baseline honest.
#: The 2026-10-02 U3 surgery removed the nature-cluster test family
#: (``test_nature_clusters*``, ``test_cross_nature_orchestration``,
#: ``test_nature_metacluster_unit``). The first profile entry named one of
#: them, so the profile drifted against the tree and
#: ``test_the_isolation_profile_names_only_files_that_exist`` went red. The
#: entry is dropped rather than restored: those tests targeted the combined
#: ``CORE.WORMS.NATURE_CLUSTERS`` module that the surgery retired, and its
#: coverage now lives in ``test_spine_mesh`` (the intermesh mapper and its
#: ``test_nature_integration_map`` were removed with the taxonomy on 2026-10-09).
ISOLATION_PROFILE = (
    "tests/test_wiring_cycle.py",
    "tests/test_registry_importable.py",
    "tests/test_spine_mesh.py",
    "tests/test_import_map.py",
    "tests/test_evolution_gates.py",
    "tests/test_mesh_wire_or_cut.py",
    "tests/test_learning_closure.py",
)

#: `pytest -q` prints `FAILED tests/test_x.py::test_y - AssertionError: ...`
#: and `ERROR tests/test_x.py::TestY::test_z - ...`. Ids are the nodeid only,
#: so a message change does not read as a new failure.
_ID_RE = re.compile(r"^(?P<kind>FAILED|ERROR)\s+(?P<nodeid>\S+)")
_SUMMARY_RE = re.compile(
    r"(?P<failed>\d+) failed|(?P<passed>\d+) passed|(?P<errors>\d+) error|"
    r"(?P<skipped>\d+) skipped")
_FINAL_RE = re.compile(r"^(?P<line>.*\b(?:failed|passed|error).*)\s+in\s+[\d.]+s")


def profile_targets(profile: str) -> List[str]:
    """The target set a profile run MUST cover to be that profile's evidence."""
    return list(ISOLATION_PROFILE) if profile == "isolation" else ["tests/"]


def is_narrowed(profile: str, select: Optional[Sequence[str]]) -> bool:
    """True when an explicit selection is smaller than the profile's own set.

    Thin wrapper over the shared rule (ADR-012), so the profile's declared target
    set is the only thing this gate has to know about.
    """
    return evidence.is_narrowed(profile_targets(profile), select)


#: One place where a profile's evidence files are named. Two environments (no
#: torch in CI, the full stack locally) must not compare against each other's
#: baseline, so the profile -- not the caller -- chooses the file.
DEFAULT_EVIDENCE = {
    "full": {"report": "aggregate_suite_report.json",
             "baseline": "aggregate_suite_baseline.json"},
    "isolation": {"report": "aggregate_isolation_report.json",
                  "baseline": "aggregate_isolation_baseline.json"},
}


def default_evidence(kind: str, profile: str) -> Path:
    """Where a WHOLE-profile run of `profile` writes its `kind` evidence.

    A single function so tests can redirect it: a test that writes a tracked
    report is how `logs/aggregate_isolation_report.json` came to hold
    `1 passed in 1s` for a real profile.
    """
    if kind not in ("report", "baseline"):
        raise ValueError("unknown evidence kind %r" % (kind,))
    if profile not in DEFAULT_EVIDENCE:
        raise ValueError("unknown profile %r" % (profile,))
    return REPO / "logs" / DEFAULT_EVIDENCE[profile][kind]


def evidence_path(kind: str, profile: str, narrowed: bool) -> Path:
    """`default_evidence`, as a narrowed run is allowed to name it.

    A narrowed run gets a `.partial` sibling so it cannot leave a subset's
    counts in the file a whole-profile run reads (module docstring, "A narrowed
    run writes somewhere else").
    """
    path = default_evidence(kind, profile)
    return _partial(path) if narrowed else path


def _partial(path: Path) -> Path:
    """The sibling a narrowed run is allowed to write (ADR-012)."""
    return evidence.partial(path)


def parse_pytest_output(text: str) -> Dict[str, object]:
    """Extract failing nodeids and the summary counts from pytest output.

    Pure function so the gate's decision logic is testable without a 20-minute
    run: the caller supplies the text.
    """
    failures: Set[str] = set()
    errors: Set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        match = _ID_RE.match(line)
        if not match:
            continue
        nodeid = match.group("nodeid")
        (failures if match.group("kind") == "FAILED" else errors).add(nodeid)
    counts = {"failed": 0, "passed": 0, "errors": 0, "skipped": 0}
    final = ""
    for line in text.splitlines():
        if _FINAL_RE.match(line.strip()):
            final = line.strip()
    for match in _SUMMARY_RE.finditer(final):
        for key in ("failed", "passed", "errors", "skipped"):
            value = match.group(key)
            if value:
                counts[key] += int(value)
    return {
        "failed_ids": sorted(failures),
        "error_ids": sorted(errors),
        "counts": counts,
        "summary_line": final,
    }


def all_ids(parsed: Dict[str, object]) -> Set[str]:
    """Every blocking nodeid: failures and errors, as one set."""
    return set(parsed.get("failed_ids") or []) | set(parsed.get("error_ids") or [])


def diff_against_baseline(parsed: Dict[str, object],
                          baseline_ids: Iterable[str]) -> Dict[str, List[str]]:
    """New (regressed) ids, fixed (disappeared) ids, and the unchanged count."""
    current = all_ids(parsed)
    known = set(baseline_ids or ())
    new = sorted(current - known)
    fixed = sorted(known - current)
    return {"new": new, "fixed": fixed,
            "unchanged": sorted(current & known)}


def baseline_payload(parsed: Dict[str, object]) -> Dict[str, object]:
    """The JSON body written by `--baseline`."""
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "suite": "tests/ (single process, -p no:randomly)",
        "counts": parsed.get("counts"),
        "summary_line": parsed.get("summary_line"),
        "failed_ids": parsed.get("failed_ids") or [],
        "error_ids": parsed.get("error_ids") or [],
    }


def load_ids(path: Path) -> List[str]:
    """Baseline/report ids, tolerating a missing or malformed file."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(set(payload.get("failed_ids") or [])
                  | set(payload.get("error_ids") or []))


#: Return codes that mean "the run did not produce a verdict". 124 is the hard
#: cap; 126 is a stall. Neither is a pass, and neither may be read as one.
NO_VERDICT_CODES = (124, 126)

#: A run whose output has not advanced for this many seconds is STALLED, not
#: slow. Measured 2026-09-22: a run sat at 9% for over ten minutes with a
#: byte-frozen output file while four of the files in that region passed in 75
#: seconds on their own, and the only way to learn that was to watch the file and
#: kill it by hand. A wall-clock cap alone cannot report this -- it fires 40
#: minutes later and says "timeout", which is true and useless.
DEFAULT_STALL_SECONDS = 240.0


def _stream_command(cmd: Sequence[str], timeout: int,
                    raw_path: Optional[Path] = None,
                    stall_seconds: float = DEFAULT_STALL_SECONDS,
                    ) -> Tuple[int, str]:
    """Run a command, tee its output to disk, and fail on a stall.

    Returns (returncode, output). 124 = hard timeout, 126 = stall detected.

    Output is teed LINE BY LINE into `raw_path` so the evidence exists even when
    the run is killed -- the alternative (read it all at the end) is what made a
    killed run leave nothing behind.
    """
    chunks: List[str] = []
    handle = None
    if raw_path is not None:
        try:
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(raw_path, "w", encoding="utf-8", errors="replace")
        except OSError:
            handle = None
    started = time.monotonic()
    last_progress = started
    last_line = ""
    try:
        proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)
    except OSError as exc:
        if handle:
            handle.close()
        return 126, "could not start: %s" % exc

    def _record(text: str) -> None:
        chunks.append(text)
        if handle is not None:
            handle.write(text)
            handle.flush()

    # The pipe is drained by a THREAD, not by readline() in the loop below.
    # Measured 2026-09-22: a blocking readline() makes the watchdog dead code --
    # it parks until the child writes again, so the first check happens only
    # after the child has already produced the output that was supposed to
    # reveal the stall (the test above took 30 s for a 1.5 s stall threshold).
    # A queue is used instead of `selectors` because Windows cannot select on a
    # pipe.
    queue: "queue.Queue" = queue_mod.Queue()

    def _drain(stream) -> None:
        try:
            for raw_line in stream:
                queue.put(raw_line)
        except Exception:
            pass
        finally:
            queue.put(None)          # sentinel: the child's output is closed

    reader = None
    if proc.stdout is not None:
        reader = threading.Thread(target=_drain, args=(proc.stdout,),
                                  name="aggregate-gate-reader", daemon=True)
        reader.start()

    stall_reason = ""
    try:
        while True:
            try:
                line = queue.get(timeout=0.25)
            except queue_mod.Empty:
                line = ""
            if line is None:
                break                     # EOF: the child closed its output
            if line:
                _record(line)
                stripped = line.strip()
                # pytest's progress line is dots/percent; the last line that
                # names something is the best available "where was it".
                if stripped and not stripped.endswith("%"):
                    last_line = stripped[:200]
                last_progress = time.monotonic()
                continue
            if proc.poll() is not None:
                break
            now = time.monotonic()
            if now - started > timeout:
                stall_reason = "TIMEOUT after %ss" % timeout
                proc.kill()
                break
            if stall_seconds and now - last_progress > stall_seconds:
                stall_reason = ("STALL: no output for %.0fs after %s"
                                % (now - last_progress, last_line or "start"))
                proc.kill()
                break
    finally:
        try:
            # Short on purpose. The verdict is already decided; this only reaps
            # the child, and measured on Windows a kill() is not always
            # immediate, so waiting 30 s here added 30 s to every stall and
            # timeout test while changing nothing about the answer.
            proc.wait(timeout=5)
        except Exception:
            pass
        if handle is not None:
            handle.close()

    output = "".join(chunks)
    if stall_reason:
        # The reason goes into the output AND the raw file: a killed run's last
        # line is the only clue to where it stopped, and it must survive the kill.
        trailer = "\n%s\n" % stall_reason
        output += trailer
        if raw_path is not None:
            try:
                with open(raw_path, "a", encoding="utf-8", errors="replace") as fh:
                    fh.write(trailer)
                    fh.flush()
            except OSError:
                print("aggregate_suite_gate: trailer write failed", file=sys.stderr)
        return (124 if stall_reason.startswith("TIMEOUT") else 126), output
    return (proc.returncode or 0), output


def run_pytest(select: Optional[Sequence[str]] = None,
               timeout: int = 2400,
               raw_path: Optional[Path] = None,
               stall_seconds: float = DEFAULT_STALL_SECONDS,
               ) -> Tuple[int, str]:
    """One aggregate pytest process. Returns (returncode, combined output).

    A single process on purpose: chunking it would recreate exactly the
    per-file blind spot this gate exists to remove.
    """
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly"]
    if select:
        cmd += list(select)
    else:
        cmd += ["tests/"]
    return _stream_command(cmd, timeout, raw_path, stall_seconds)


def _last_evidence_line(output: str) -> str:
    """The last line that names a test, for the killed-run message."""
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        # `::` alone is not enough: the STALL/TIMEOUT line quotes a nodeid too,
        # so it would name itself as the last test and hide the real one.
        if stripped.startswith(("STALL", "TIMEOUT")):
            continue
        if stripped and "::" in stripped:
            return "last named test: %s" % stripped[:200]
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped:
            return "last output: %s" % stripped[:200]
    return "last output: (none)"


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    # Declared first: the `--*-file` defaults read the module-level names, and
    # Python rejects a `global` that follows a use in the same function.
    global BASELINE, REPORT
    parser = argparse.ArgumentParser(
        description="Run the aggregate suite and gate on NEW failures")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="exit 1 when an id is not in the baseline")
    group.add_argument("--baseline", action="store_true",
                       help="record the current failing ids as known")
    group.add_argument("--report", action="store_true",
                       help="run and write the report without gating")
    group.add_argument("--diff-from", metavar="JSON",
                       help="skip the run and diff a saved report/baseline")
    # Deliberately NOT in the mutually-exclusive group: this one selects the
    # INPUT (a saved run instead of a fresh one) and composes with --check,
    # --baseline and --report, which select what to DO with it.
    parser.add_argument("--parse-from", metavar="RAW",
                        help="parse a saved RAW pytest output instead of "
                             "running; composes with --check/--baseline/--report")
    parser.add_argument("--only", nargs="+", metavar="PATH",
                        help="pytest targets (default: tests/)")
    parser.add_argument("--profile", choices=("full", "isolation"),
                        default="full",
                        help="'isolation' runs the dependency-light CI subset "
                             "that reproduces the cross-file defect class")
    parser.add_argument("--baseline-file", metavar="JSON",
                        help="where --baseline writes / --check reads "
                             "(default: %s)" % BASELINE.name)
    parser.add_argument("--report-file", metavar="JSON",
                        help="where the run report is written "
                             "(default: %s)" % REPORT.name)
    parser.add_argument("--timeout", type=int, default=2400,
                        help="seconds for the whole aggregate run")
    parser.add_argument("--stall-seconds", type=float,
                        default=DEFAULT_STALL_SECONDS,
                        help="report STALL if output does not advance for this "
                             "long (0 disables); a stalled run is not a slow one")
    args = parser.parse_args(argv)

    # Module-level names, overridable: `--check` MUST read the same file
    # `--baseline` wrote, including when a profile pinned it elsewhere, so the
    # two resolve through one place rather than repeating the default.
    select = args.only
    if select is None and args.profile == "isolation":
        select = list(ISOLATION_PROFILE)

    # A subset run must not answer for the profile (module docstring): it would
    # otherwise leave `logs/aggregate_isolation_report.json` claiming to describe
    # a run of all eight profile files while having executed one of them.
    # Only the DEFAULT paths are redirected -- an explicit --report-file /
    # --baseline-file is obeyed as written, because then the caller has said
    # where the evidence is meant to live.
    narrowed = is_narrowed(args.profile, select)
    if args.baseline_file:
        BASELINE = Path(args.baseline_file).resolve()
    elif args.profile == "isolation":
        BASELINE = default_evidence("baseline", args.profile)
    if args.report_file:
        REPORT = Path(args.report_file).resolve()
    elif args.profile == "isolation":
        REPORT = default_evidence("report", args.profile)
    if narrowed:
        # Applied AFTER the profile/default resolution, so it narrows whatever
        # path was chosen (including a monkeypatched one) rather than a lookup.
        if not args.baseline_file:
            BASELINE = _partial(BASELINE)
        if not args.report_file:
            REPORT = _partial(REPORT)
    if narrowed:
        print(evidence.narrowed_note(args.profile, REPORT, BASELINE))

    if args.diff_from:
        parsed = {"failed_ids": load_ids(Path(args.diff_from)), "error_ids": []}
        rc = 0
        out = ""
    elif args.parse_from:
        # A run that was streamed to a file (so its progress was watchable) is
        # the same evidence as a captured one; re-running it just to re-parse it
        # is the cost this option exists to remove -- ADR-010 rule 5.
        raw_path = Path(args.parse_from)
        try:
            out = raw_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print("cannot read raw output %s: %s" % (raw_path, exc),
                  file=sys.stderr)
            return 1
        parsed = parse_pytest_output(out)
        rc = 0
    else:
        rc, out = run_pytest(select, args.timeout,
                             REPORT.with_suffix(".raw.txt"),
                             args.stall_seconds)
        parsed = parse_pytest_output(out)
        if rc in NO_VERDICT_CODES:
            verdict = "TIMEOUT" if rc == 124 else "STALL"
            print("%s: the aggregate run produced no verdict -- treated as a "
                  "failure, because a killed run is not a pass" % verdict,
                  file=sys.stderr)
            print(_last_evidence_line(out), file=sys.stderr)
            return 1

    # NO SUMMARY LINE MEANS NO VERDICT, and a run without a verdict does not
    # overwrite the evidence. `parse_pytest_output` leaves every count at zero
    # when the text has no `N passed` summary line -- an interrupted run, a
    # pytest that died before printing one, or an empty file handed to
    # `--parse-from` -- so the write below would store 0 failed / 0 passed /
    # 0 errors with an empty summary_line, which at a glance is what a clean
    # suite looks like, replacing a real baseline. Measured 2026-10-15:
    # `logs/aggregate_suite_report.json` went from 3698 passed / 2 failed with
    # failed_ids to all-zero with summary_line "". This is the same rule as
    # NO_VERDICT_CODES above: neither a killed run nor an unparsed one is a
    # pass, and neither may be read as one.
    if not args.diff_from and not parsed.get("summary_line"):
        print("no pytest summary line in the output: the run produced no "
              "verdict, so %s is left untouched -- an all-zero report cannot "
              "be told apart from a clean suite" % REPORT, file=sys.stderr)
        print(_last_evidence_line(out), file=sys.stderr)
        return 1

    # The RAW OUTPUT is persisted next to the report, unconditionally.
    # Reason: the report stores nodeids only, and a nodeid names the failure but
    # says nothing about its cause -- so the previous runs left ~50 failures that
    # could be counted and not diagnosed, and re-paying 22 minutes was the only
    # way to read an assertion message. 1.5 MB of text is cheaper than that.
    if not args.diff_from and not args.parse_from:
        raw = REPORT.with_suffix(".raw.txt")
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(out, encoding="utf-8", errors="replace")
        print("raw output: %s" % raw)
    elif args.parse_from:
        # Not copied: the streamed file IS the evidence, and duplicating it under
        # the report's name would suggest the gate had produced it.
        print("parsed from: %s" % args.parse_from)

    counts = parsed.get("counts") or {}
    ids = all_ids(parsed)
    print("aggregate suite: %s failed / %s passed / %s errors / %s skipped"
          % (counts.get("failed"), counts.get("passed"),
             counts.get("errors"), counts.get("skipped")))
    print("blocking ids: %d" % len(ids))

    if not args.diff_from:
        _write_json(REPORT, {"recorded_at": datetime.now(
            timezone.utc).isoformat(timespec="seconds"),
            "profile": args.profile,
            "targets": list(select or ["tests/"]),
            "raw_output": REPORT.with_suffix(".raw.txt").name,
            "counts": counts, "summary_line": parsed.get("summary_line"),
            "failed_ids": parsed.get("failed_ids") or [],
            "error_ids": parsed.get("error_ids") or []})

    if args.baseline:
        _write_json(BASELINE, baseline_payload(parsed))
        print("baseline recorded: %d known ids -> %s" % (len(ids), BASELINE))
        return 0

    if not args.check:
        return 0

    baseline_ids = load_ids(BASELINE)
    if not baseline_ids and not BASELINE.exists():
        print("no baseline at %s -- run --baseline first" % BASELINE,
              file=sys.stderr)
        return 1

    delta = diff_against_baseline(parsed, baseline_ids)
    for nodeid in delta["new"]:
        print("NEW FAILURE: %s" % nodeid)
    for nodeid in delta["fixed"]:
        print("no longer failing: %s" % nodeid)
    if delta["fixed"]:
        print("(%d baseline id(s) are gone -- re-run --baseline to tighten the "
              "gate)" % len(delta["fixed"]))
    if delta["new"]:
        print("FAIL: %d new aggregate failure(s) vs %s"
              % (len(delta["new"]), BASELINE))
        return 1
    print("OK: no new aggregate failures (%d known, %d unchanged)"
          % (len(baseline_ids), len(delta["unchanged"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
