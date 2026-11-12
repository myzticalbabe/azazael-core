#!/usr/bin/env python3
# coding: utf-8
"""Refresh graphify-out/ WITHOUT an LLM, then clear the in-flight marker.

Why this entry point exists. The spine cycle's proprioception gate refuses to
write while any derived artifact is stale, and `graphify-out/graph.json` plus
`GRAPH_REPORT.md` are two of the seven it checks. `tools/refresh_all.py`
deliberately does not rebuild them, so the gate could only ever be satisfied by
hand -- which is exactly what happened: the loop refused hourly from
2026-09-18T20:45 to 2026-09-20T02:00 and reported nothing wrong.

Two things made the graph look un-refreshable, and neither is true:

1. `tools/graph_rebuild.py` runs the SEMANTIC pass, which on the free tier is
   limited to 5 requests/minute and loses chunks to 429s. That is the quota
   problem, and it is real -- for that pass.
   `graphify update` re-extracts CODE and needs no model at all.

2. `graphify` is installed in the SYSTEM Python 3.14, while the repo's refresh
   path runs under the 3.14t venv. `sys.executable -m graphify` therefore can
   never reach it. This script takes the interpreter explicitly instead.

Order matters: `update` rewrites graph.json, then `cluster-only` regenerates
GRAPH_REPORT.md from it. `graph_rebuild.py` records that running extract alone
left the report 49 sources stale -- the rebuild reproducing the very
report/artifact mismatch the freshness guard exists to catch.

Takes ~31 minutes on this tree (101k files), so it is meant to be launched
detached, not awaited.

Usage:
    python scripts/graph_refresh_no_llm.py            # run it here, in front
    python scripts/graph_refresh_no_llm.py --status   # report staleness only
    python scripts/graph_refresh_no_llm.py --force    # allow a SMALLER graph

The corpus is scoped by `.graphifyignore`, which graphify reads in addition to
`.gitignore`. It has to be a separate file: `.gitignore` already listed
`output/` and `graphify-out/`, and neither exclusion ever took effect, because
gitignore does not apply to tracked paths and both trees are committed. Before
that file existed the graph resolved 28,279 files to 451,307 nodes, 27,489 of
those files being the vendored onnxruntime checkouts under `archived/`.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: graphify lives outside the venv, and WHICH install is not a detail -- it was
#: the whole defect this comment used to paper over. The interpreter was
#: hard-coded to the pip install in the system Python 3.14, chosen because
#: inheriting `sys.executable` (the 3.14t venv) reaches nothing. Right instinct
#: about the venv, wrong conclusion about the target: that install is a flat
#: package with no `exporters/` directory, which is exactly what
#: `scripts/graphify_node_limit_patch.py` rewrites and where
#: `graphify_huge_mode.py` is read from. `tools/graph_rebuild.py` had the same
#: class of bug from the other side (bare `sys.executable`). Both now call the
#: one resolver, so neither can drift: capability, not path. See Error #208.
for _p in (ROOT / "tools",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graphify_interpreter import (  # noqa: E402
    HUGEMODE_MARKER,
    LEGACY_GRAPHIFY_PY,
    REQUIRED_INSTALL_PARTS,
    SKILL_VERSION_FILE,
    graphify_argv,
    graphify_python_candidates,
    resolve_graphify_python,
)

__all__ = [
    "HUGEMODE_MARKER", "LEGACY_GRAPHIFY_PY", "REQUIRED_INSTALL_PARTS",
    "SKILL_VERSION_FILE", "graphify_argv", "graphify_python_candidates",
    "resolve_graphify_python",
]

#: The cycle drops this while a pass is in flight so the next fire does not
#: start a second one. Cleared here on every exit path.
MARKER = Path(os.path.expanduser("~")) / ".azazael" / "graph_refresh.running"

#: Full per-step output is kept here. On 2026-09-21 the `update .` step died
#: with a single reported line ("EOF marker not found") after 1522s and the
#: very next question -- *what* failed? -- could not be answered, because every
#: earlier line had been discarded. That is the one-line diagnostic cap the
#: audits keep re-finding, applied to this script's own reporting.
LOGS = ROOT / "logs"

STEPS: tuple[tuple[str, ...], ...] = (
    ("-m", "graphify", "update", "."),
    ("-m", "graphify", "cluster-only", "--out", "graphify-out"),
)


def log(msg: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def graph_stale(root: Path | None = None) -> bool | None:
    """Is graphify-out/ behind its sources? None if the probe cannot answer.

    None is deliberately not False: an unreadable probe is not evidence of
    freshness, and the caller decides what to do about that.
    """
    root = root or ROOT
    try:
        sys.path.insert(0, str(root / "tools"))
        import artifact_freshness as af  # noqa: PLC0415

        rows = af.audit(root)
    except Exception:
        return None
    graph = [r for r in rows
             if str(r.get("artifact", "")).startswith("graphify-out/")]
    return any(r.get("stale") for r in graph) if graph else None


def read_marker() -> tuple[float, int] | None:
    """The in-flight claim as (start_time, pid), or None if absent/unreadable.

    The reader parses "<start-time> <pid>", so the body must be written in
    exactly that shape -- a live pid is what lets the guard tell a real pass
    from a corpse left by a killed one.
    """
    try:
        parts = MARKER.read_text(encoding="utf-8").split()
        if len(parts) != 2:
            return None
        return float(parts[0]), int(parts[1])
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """True when a pid names a live process. Best effort, never raises."""
    if pid <= 0:
        return False
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                      False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def in_flight() -> tuple[float, int] | None:
    """A claim held by a process that is actually running, else None.

    A marker whose pid is dead is a corpse, not a claim: refusing to start
    because of it would wedge the refresh permanently after any crash.
    """
    claim = read_marker()
    if claim is None:
        return None
    _, pid = claim
    if pid == os.getpid():
        return claim
    return claim if _pid_alive(pid) else None


def write_marker() -> None:
    """Claim the in-flight slot so the hourly cycle does not start a second pass.

    `spine_cycle_apply._launch_graph_refresh()` writes this marker before it
    spawns us, but a standalone run (`python scripts/graph_refresh_no_llm.py`)
    never did. So the scheduled cycle, which only consults the marker, could see
    "nothing in flight" and launch a competing ~31 minute re-extract over the
    same tree.
    """
    try:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.write_text("%.6f %d" % (time.time(), os.getpid()), encoding="utf-8")
    except OSError:
        pass


def clear_marker() -> None:
    """Release the claim -- but only the one this process actually holds.

    2026-09-29. This used to unlink unconditionally, in a `finally`. Two passes
    over one artifact is exactly the situation the marker exists to prevent,
    and unconditional clearing is how the second pass made the first one
    invisible: a manual scoped rebuild finished at 10:25:51 and cleared the
    marker, and the hourly spine refresh that had been running since 10:00:55
    wrote graph.json at 10:27 -- 638 MB and 451,315 nodes, the full unscoped
    corpus, over the 46 MB scoped graph that had just been built and verified.
    The rebuild reported success and the clobber was invisible until a later
    `graphify diagnose` disagreed with the node count the rebuild had printed.

    A marker is a claim, and only the claimant may release it -- or a corpse
    left by a crashed pass, which is not a claim at all. Checking liveness
    rather than identity alone is what makes both true: a live foreign claim
    is left strictly alone, while a dead one is cleaned up instead of sitting
    on disk until the next successful write happens to overwrite it.
    """
    claim = read_marker()
    if claim is not None and claim[1] != os.getpid() and _pid_alive(claim[1]):
        return
    try:
        MARKER.unlink()
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true",
                    help="report graphify-out/ staleness and exit")
    ap.add_argument("--force", action="store_true",
                    help="let `update` overwrite graph.json even when the "
                         "rebuild finds fewer nodes than it is replacing")
    args = ap.parse_args(argv)

    if args.status:
        st = graph_stale()
        print("graphify-out:", {True: "STALE", False: "current",
                                None: "unknown (probe failed)"}[st])
        return 0 if st is False else 1

    steps = STEPS
    if args.force:
        # `update` only ADDS nodes, so narrowing the corpus does not by itself
        # remove what the old corpus put there. --force is the documented way
        # to say "the graph legitimately got smaller", which is exactly the
        # situation when a tree stops indexing its own vendored archives.
        # Without it the 2026-09-29 scoping would have changed every future
        # refresh and left all 451k vendored nodes in place forever.
        steps = (("-m", "graphify", "update", ".", "--force"),) + STEPS[1:]
        log("FORCE rebuild: graph.json may shrink (corpus was narrowed)")

    # Refuse to start a second pass over one artifact. The marker alone was not
    # enough in either direction: a standalone run never checked it, and
    # `clear_marker` used to release a claim it did not own. Both halves are
    # fixed above; this is the guard that uses them.
    claim = in_flight()
    if claim is not None and claim[1] != os.getpid():
        age = max(0.0, time.time() - claim[0])
        log("REFUSING: a graph refresh is already in flight "
            f"(pid={claim[1]}, started {age:.0f}s ago). Two passes over one "
            "artifact race, and the loser silently overwrites the winner.")
        return 3

    log("resolving the graphify install (capability, not path):")
    graphify_py = resolve_graphify_python(report=log)
    if graphify_py is None:
        log("FAIL: no graphify install on this box can render this tree.")
        log("      Wanted: an install with %s (the file the huge-mode "
            "patch rewrites)." % " + ".join(REQUIRED_INSTALL_PARTS))
        log("      Checked: %s" % ", ".join(
            str(p) for p in graphify_python_candidates()))
        log("      Fix: uv tool install graphifyy --force, then run "
            "scripts/graphify_node_limit_patch.py")
        clear_marker()
        return 2

    write_marker()
    started = time.time()
    log(f"start (interpreter={graphify_py} pid={os.getpid()})")
    rc_all = 0
    try:
        for i, step in enumerate(steps, start=1):
            t0 = time.perf_counter()
            try:
                proc = subprocess.run([str(graphify_py), *step], cwd=str(ROOT),
                                      capture_output=True, text=True, timeout=5400, encoding="utf-8", errors="replace")
                rc = proc.returncode
                text = (proc.stdout or "") + (proc.stderr or "")
            except subprocess.TimeoutExpired as exc:
                rc = None
                text = "timed out after 5400s\n%s" % (
                    (exc.stdout if isinstance(exc.stdout, str) else "") or "")
            tail = [ln for ln in text.splitlines() if ln.strip()]
            dump = LOGS / ("graph_refresh_step%d.log" % i)
            try:
                LOGS.mkdir(parents=True, exist_ok=True)
                dump.write_text(text or "(no output)\n", encoding="utf-8",
                                errors="replace")
            except OSError:
                dump = None
            log(f"{' '.join(step[2:])!r:34} rc={rc} in {time.perf_counter() - t0:.0f}s"
                f" | {tail[-1][:140] if tail else 'no output'}"
                + (" | full: %s" % dump.relative_to(ROOT) if dump else ""))
            if rc != 0:
                rc_all = rc or 1
    finally:
        # Always clear, so a failed pass does not block the next attempt; the
        # caller's mtime-based backstop covers a process that dies here.
        clear_marker()

    st = graph_stale()
    log("graphify-out: %s" % {True: "STILL STALE", False: "current",
                              None: "unknown (probe failed)"}[st])
    log(f"done in {time.time() - started:.0f}s rc={rc_all}")
    return 0 if (rc_all == 0 and st is False) else 1


if __name__ == "__main__":
    sys.exit(main())