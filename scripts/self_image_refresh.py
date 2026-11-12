#!/usr/bin/env python3
# coding: utf-8
"""Keep the derived self-image current, so the write gate can stop refusing.

WHY THIS IS A CHAIN JOB AND NOT A HAND-RUN TOOL

`run_cycle(apply=True)` refuses to write while any derived artifact is stale
(`stale_self_image`). Seven artifacts are checked; `tools/refresh_all.py`
regenerates five of them and deliberately does not touch `graphify-out/`. Until
now the only thing that called it was `scripts/spine_cycle_apply.py` -- and only
*after* a refusal, i.e. reactively, and only when the separate hourly
`AzazaelSpineCycle` task happened to be alive. So the write path depended on a
task outside the chain, and the normal state was "refused, then repaired, then
refused again on the next edit".

This job makes repair PROACTIVE and puts it inside the one entry point:

  1. audit -- which artifacts are stale, and is the graph/report pair consistent
  2. derived maps stale  -> run `tools/refresh_all.py` (bounded, ~1 min)
  3. graph stale         -> launch `scripts/graph_refresh_no_llm.py` DETACHED
                            (no LLM; ~31 min on this tree, so it cannot be
                            awaited inside a chain slot) and never a second copy
  4. re-audit and say what is still stale, or that authority is restored

Exit 0 = every artifact current (write authority is available).
Exit 1 = something is still stale or a refresh step failed.
Exit 2 = the probe itself could not run (never reported as "current").

Usage:
    python scripts/self_image_refresh.py            # refresh what is stale
    python scripts/self_image_refresh.py --status   # report only, no writes
    python scripts/self_image_refresh.py --force    # refresh even if current
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
sys.path.insert(0, str(ROOT / "tools"))

GRAPH_REFRESH_SCRIPT = _HERE / "graph_refresh_no_llm.py"

#: The refresh script clears this on every exit path; we set it before launching
#: so a second fire cannot start a duplicate pass.
GRAPH_REFRESH_MARKER = Path(os.path.expanduser("~")) / ".azazael" / "graph_refresh.running"

#: A pass takes ~31 min. An older marker means a killed process or a reboot, not
#: a live run -- it must not block forever, which is the same corpse-marker trap
#: `spine_cycle_apply.py` documents.
GRAPH_REFRESH_DEADLINE_S = 3 * 3600

#: Past this age the marker's recorded pid must still be alive, or the marker is
#: a corpse. See `_graph_refresh_running`: a dead holder's lock could otherwise
#: only be released by the process it was blocking.
MARKER_GRACE_S = 180.0


def _pid_alive(pid: int) -> bool:
    """Does this pid exist? Fails SAFE (True) when the question is unanswerable."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/fi", "PID eq %d" % pid, "/fo", "csv"],
                capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
            text = out.stdout or ""
            return ("\"%d\"" % pid) in text or (" %d " % pid) in text
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

REFRESH_ALL = ROOT / "tools" / "refresh_all.py"


def log(msg: str) -> None:
    print("%s %s" % (datetime.now().isoformat(timespec="seconds"), msg), flush=True)


def _audit() -> dict | None:
    """Freshness rows + graph verdict, or None when the probe cannot run.

    None is deliberately distinct from "nothing stale": an instrument that
    failed is not evidence that the image is current.
    """
    try:
        import artifact_freshness as af
    except Exception as exc:
        log("FAIL: artifact_freshness not importable (%s)" % str(exc)[:120])
        return None
    try:
        rows = af.audit(ROOT)
        graph = af.graph_report_mismatch(ROOT)
    except Exception as exc:
        log("FAIL: audit raised (%s)" % str(exc)[:120])
        return None
    derived = [r for r in rows if not r["artifact"].startswith("graphify-out/")]
    graph_rows = [r for r in rows if r["artifact"].startswith("graphify-out/")]
    return {
        "derived_stale": [r["artifact"] for r in derived if r["stale"]],
        "graph_stale": [r["artifact"] for r in graph_rows if r["stale"]],
        "graph_mismatch": bool(graph.get("mismatch")),
        "graph_reason": graph.get("reason", ""),
        "stale_count": len([r for r in rows if r["stale"]]),
    }


def _read_marker():
    """(started_at, pid) from the marker; tolerant of the old time-only format."""
    try:
        parts = GRAPH_REFRESH_MARKER.read_text(encoding="utf-8").split()
    except OSError:
        return None
    if not parts:
        return None
    try:
        ts = float(parts[0])
    except ValueError:
        return None
    pid = 0
    if len(parts) > 1:
        try:
            pid = int(parts[1])
        except ValueError:
            pid = 0
    return ts, pid


def _graph_refresh_running(now: float | None = None) -> bool:
    """Is a pass really in flight? Age AND owner, not just age.

    mtime alone was measurably wrong: a marker whose worker had died still read
    "in flight" for three hours, and the only thing that clears it lives in the
    script this guard refuses to launch.
    """
    parsed = _read_marker()
    if parsed is None:
        return False
    ts, pid = parsed
    age = (time.time() if now is None else now) - ts
    if not (0 <= age < GRAPH_REFRESH_DEADLINE_S):
        return False
    if pid > 0 and age > MARKER_GRACE_S and not _pid_alive(pid):
        log("graph refresh: marker is a corpse (pid %d is gone) -- holding nothing"
            % pid)
        return False
    return True


def _launch_graph_refresh() -> bool:
    """Start the no-LLM graph re-extract detached. At most one at a time."""
    if _graph_refresh_running():
        log("graph refresh: already in flight, not starting a second")
        return False
    flags = 0
    for name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"):
        flags |= getattr(subprocess, name, 0)
    try:
        GRAPH_REFRESH_MARKER.parent.mkdir(parents=True, exist_ok=True)
        # Our own pid first (claims the slot against a racing launcher), then
        # the CHILD's pid -- that is the one whose liveness answers "still going".
        GRAPH_REFRESH_MARKER.write_text(
            "%.6f %d" % (time.time(), os.getpid()), encoding="utf-8")
        child = subprocess.Popen(
            [sys.executable, str(GRAPH_REFRESH_SCRIPT)], cwd=str(ROOT),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        GRAPH_REFRESH_MARKER.write_text(
            "%.6f %d" % (time.time(), int(child.pid)), encoding="utf-8")
        log("graph refresh: launched detached (no LLM, ~31 min); the gate stays "
            "refused for the graph half until it lands")
        return True
    except Exception as exc:
        log("graph refresh: launch failed (%s)" % str(exc)[:120])
        return False


def _run_refresh_all(timeout: float = 1200.0) -> int:
    """Rebuild the five derived maps. Returns the child's exit code (-1 = raised).

    `refresh_all.py` owns the dependency order (census -> verdict -> map) and
    audits its own output, so the order is not re-implemented here. Its exit code
    cannot be trusted as the verdict: it also fails on an unrelated hygiene
    regression. It is logged, and the *audit below* decides whether the image is
    current -- the instrument, not the intent.
    """
    try:
        res = subprocess.run([sys.executable, str(REFRESH_ALL)], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except Exception as exc:
        log("refresh_all: raised (%s)" % str(exc)[:120])
        return -1
    tail = [ln for ln in (res.stdout or "").strip().splitlines() if ln.strip()]
    log("refresh_all: rc=%s %s" % (res.returncode, (tail[-1][:140] if tail else "no-output")))
    return res.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true", help="report only, never write")
    ap.add_argument("--force", action="store_true", help="refresh even if current")
    args = ap.parse_args(argv)

    before = _audit()
    if before is None:
        return 2

    derived, graph = before["derived_stale"], before["graph_stale"]
    if not derived and not graph and not before["graph_mismatch"]:
        log("current: no derived artifact is stale (write authority available)")
        return 0

    log("stale: derived=%s graph=%s mismatch=%s"
        % (derived or "none", graph or "none", before["graph_mismatch"]))

    if args.status:
        return 1

    acted = False
    if derived or before["graph_mismatch"] or args.force:
        _run_refresh_all()
        acted = True
    if graph or args.force:
        _launch_graph_refresh()
        acted = True

    after = _audit()
    if after is None:
        return 2
    still = after["derived_stale"] + after["graph_stale"]
    if not still and not after["graph_mismatch"]:
        log("recovered: the self-image is current (acted=%s, graph_in_flight=%s)"
            % (acted, _graph_refresh_running()))
        return 0
    log("STILL STALE after refresh: %s%s"
        % (still,
           " | graph/report mismatch" if after["graph_mismatch"] else ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())