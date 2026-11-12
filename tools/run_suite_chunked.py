#!/usr/bin/env python3
# coding: utf-8
"""Run the full pytest suite in bounded chunks, with a per-chunk record.

Why this exists
---------------
The suite is roughly 3.2k tests and about 25 minutes end to end. A single
`pytest tests/` invocation exceeds most CI per-job limits, and when the harness
kills it the run is lost whole -- no per-file verdict, and a timeout is
indistinguishable from a genuine failure. It also hides the isolation problem:
files that pass alone and fail in a batch are evidence of shared mutable state
(the registry, the spine, or the registry cache), which a single aggregate run
cannot tell apart from a real regression.

So the suite is cut into chunks, each run as its own pytest process with its own
timeout. A chunk that dies loses only that chunk, the failure ids are captured,
and the chunk boundaries are a free isolation experiment: the same file passing
alone and failing in a chunk is a finding, not noise.

Usage
-----
    python tools/run_suite_chunked.py                    # auto chunks, full run
    python tools/run_suite_chunked.py --chunks 12
    python tools/run_suite_chunked.py --only test_mesh  # subset by filename
    python tools/run_suite_chunked.py --list            # show the plan only

Exit code is 0 only when every chunk returned 0. The JSON report is written to
`logs/suite_chunks.json` by default so a killed run still leaves a record.
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

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
DEFAULT_OUT = REPO / "logs" / "suite_chunks.json"

#: pytest's own summary line, e.g. "2 failed, 149 passed, 1 skipped in 34.8s"
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|error|errors|xfailed|xpassed)")

#: rc=5 is pytest's "no tests collected". It is only a non-failure when the file
#: says why: a module-level `pytest.skip(..., allow_module_level=True)` or
#: `pytest.importorskip(...)` guarded by a genuinely-absent optional dependency.
#: `scripts/run_full_suite.py` already reports that case as SKIP; mirror it here
#: so a chunked run does not invent a broken chunk out of a correct guard.
NO_TESTS_COLLECTED = 5
_DURATION_RE = re.compile(r"in\s+([\d.]+)s")
_FAILED_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)


def discover(only: str | None = None) -> list[str]:
    """Test files, relative to the repo root, that actually contain tests.

    Every `test_*.py` on disk is a candidate, but the parse is deliberately
    cheap and textual: importing 160 modules to ask how many tests they hold
    would cost more than the chunking saves.
    """
    if not TESTS.is_dir():
        raise SystemExit("no tests/ directory at %s" % TESTS)
    files = sorted(p.name for p in TESTS.glob("test_*.py"))
    if only:
        files = [f for f in files if only in f]
    return [str(Path("tests") / f) for f in files]


def chunk(items: list[str], chunks: int) -> list[list[str]]:
    """Split items into `chunks` contiguous groups of near-equal size."""
    chunks = max(1, min(int(chunks), len(items) or 1))
    size, extra = divmod(len(items), chunks)
    out: list[list[str]] = []
    start = 0
    for i in range(chunks):
        take = size + (1 if i < extra else 0)
        if take:
            out.append(items[start:start + take])
        start += take
    return out


def parse(stdout: str) -> dict:
    """Pull the counts and the failing ids out of a pytest run."""
    counts: dict[str, int] = {}
    for value, label in _COUNT_RE.findall(stdout):
        key = "error" if label == "errors" else label
        counts[key] = counts.get(key, 0) + int(value)
    duration = _DURATION_RE.search(stdout)
    return {
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "errors": counts.get("error", 0),
        "skipped": counts.get("skipped", 0),
        "duration_s": float(duration.group(1)) if duration else None,
        "failed_ids": _FAILED_RE.findall(stdout),
    }


def run_chunk(index: int, total: int, files: list[str],
              timeout: float, env: dict) -> dict:
    """Run one chunk in its own process. A timeout is recorded, never raised."""
    print("\n[chunk %d/%d] %d file(s): %s%s"
          % (index, total, len(files), ", ".join(f.split("/")[-1] for f in files[:4]),
             " ..." if len(files) > 4 else ""), flush=True)
    started = time.time()
    # `-p no:hypothesispytest`: the environment has no `hypothesis._native`
    # (a cp314t wheel gap), and that plugin imports it from its
    # `pytest_terminal_summary` hook. Without this the whole chunk still runs
    # but the summary hook raises at the very end, so pytest exits non-zero
    # with no counts on stdout and the runner reports a broken chunk that is
    # actually a clean pass. `pytest.ini` already disables the three plugins
    # that abort at COLLECTION time; this one only breaks the summary, which
    # is why it was not caught there.
    cmd = [sys.executable, "-m", "pytest", *files, "-q", "--no-header",
           "-p", "no:hypothesispytest"]
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True,
                              text=True, timeout=timeout, env=env, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        # A timeout is a result, not a crash: record it and keep going so the
        # remaining chunks still produce a verdict.
        stdout = (exc.stdout or b"")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        return {"index": index, "files": files, "timed_out": True,
                "returncode": None, "wall_s": round(time.time() - started, 1),
                "timeout_s": timeout, **parse(stdout), "tail": stdout[-800:]}

    wall = round(time.time() - started, 1)
    parsed = parse(proc.stdout)
    record = {"index": index, "files": files, "timed_out": False,
              "returncode": proc.returncode, "wall_s": wall,
              "timeout_s": timeout, **parsed}
    if (proc.returncode == NO_TESTS_COLLECTED
            and not any(parsed[k] for k in ("passed", "failed", "errors"))):
        record["rc5_skip"] = True
    if proc.returncode != 0 and not record.get("rc5_skip"):
        record["tail"] = (proc.stdout or "")[-1500:]
        if proc.stderr:
            record["stderr"] = proc.stderr[-600:]
    if record.get("rc5_skip"):
        print("    -> rc=5 no tests collected (module-level skip) in %ss" % wall,
              flush=True)
    else:
        print("    -> rc=%s passed=%d failed=%d errors=%d skipped=%d in %ss"
              % (proc.returncode, parsed["passed"], parsed["failed"],
                 parsed["errors"], parsed["skipped"], wall), flush=True)
    if parsed["failed_ids"]:
        for fid in parsed["failed_ids"][:6]:
            print("       FAILED %s" % fid, flush=True)
        if len(parsed["failed_ids"]) > 6:
            print("       ... and %d more" % (len(parsed["failed_ids"]) - 6),
                  flush=True)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chunks", type=int, default=8,
                    help="number of chunks (default 8, sized for a CI job limit)")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="seconds per chunk (default 600)")
    ap.add_argument("--only", default=None, help="substring filter on filenames")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="JSON report path")
    ap.add_argument("--list", action="store_true", help="print the plan and exit")
    ap.add_argument("--fail-fast", action="store_true",
                    help="stop after the first failing chunk")
    args = ap.parse_args()

    files = discover(args.only)
    groups = chunk(files, args.chunks)
    if args.list:
        for i, group in enumerate(groups, 1):
            print("chunk %d: %d file(s)" % (i, len(group)))
            for f in group:
                print("    %s" % f)
        print("total: %d file(s) across %d chunk(s)" % (len(files), len(groups)))
        return 0

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")

    print("AZAZAEL chunked suite runner")
    print("python  : %s" % sys.version.split()[0])
    print("files   : %d across %d chunk(s), %ss per chunk"
          % (len(files), len(groups), args.timeout))
    started = time.time()

    records: list[dict] = []
    for i, group in enumerate(groups, 1):
        record = run_chunk(i, len(groups), group, args.timeout, env)
        records.append(record)
        if args.fail_fast and ((record["returncode"] and not record.get("rc5_skip"))
                               or record["timed_out"]):
            print("\n--fail-fast: stopping after chunk %d" % i, flush=True)
            break

    totals = {k: sum(r.get(k) or 0 for r in records)
              for k in ("passed", "failed", "errors", "skipped")}
    bad = [r for r in records
           if (r["returncode"] and not r.get("rc5_skip")) or r["timed_out"]]
    report = {"chunks": len(groups), "chunks_run": len(records),
              "files": len(files), "wall_s": round(time.time() - started, 1),
              "totals": totals, "failing_chunks": [r["index"] for r in bad],
              "no_tests_chunks": [r["index"] for r in records
                                  if r.get("rc5_skip")],
              "records": records}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")

    print("\n" + "=" * 62)
    print("passed=%d failed=%d errors=%d skipped=%d in %ss"
          % (totals["passed"], totals["failed"], totals["errors"],
             totals["skipped"], report["wall_s"]))
    for record in records:
        if record.get("rc5_skip"):
            print("chunk %d SKIPPED (no tests collected): %s"
                  % (record["index"],
                     ", ".join(f.split("/")[-1] for f in record["files"][:6])))
    for record in bad:
        why = "TIMEOUT" if record["timed_out"] else "rc=%s" % record["returncode"]
        print("chunk %d %s: %s" % (record["index"], why,
                                   ", ".join(f.split("/")[-1]
                                             for f in record["files"][:6])))
    print("report: %s" % out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
