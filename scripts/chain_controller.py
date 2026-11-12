#!/usr/bin/env python3
"""Clean replacement chain controller for the current Azazael control plane.

The controller is intentionally small: it owns the job table, reports scheduler
state without changing it, and can execute at most one explicitly selected job.
It does not enable, disable, or reconfigure Windows Task Scheduler.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
import csv

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LOG_DIR = ROOT / "logs"
HEALTH_PATH = ROOT / "state" / "chain_controller_health.json"
LAST_FIRE_PATH = ROOT / "state" / "chain_controller_last_fire.json"
DUE_PATH = ROOT / "state" / "chain_controller_due.json"
PLAN_PATH = ROOT / "state" / "chain_controller_plan.json"
HEARTBEAT_PATH = LOG_DIR / "chain_controller.log"
LOCK_PATH = ROOT / "state" / "chain_controller.lock"
TASK_NAME = os.environ.get("AZAZAEL_CHAIN_TASK", "Azazael-Chain")
MUTATING_JOBS = {"wiring", "hyperopt", "spine", "meta"}

# (interval seconds or nightly:HH:MM, timeout, kind, target, args)
JOBS: dict[str, tuple[Any, int, str, str, tuple[str, ...]]] = {
    # Monitor refresh (operator decision 2026-09-25): no scheduler, task, or
    # Chain job ever rendered the wallboards — monitor_azazael.html sat 21h
    # stale while the page auto-reloads every 60s. Renders are display output
    # (not mutations), so this job stays out of MUTATING_JOBS and runs even in
    # observation-only mode. Fail-open per renderer inside the wrapper.
    # Cadence re-point (T-4, 2026-10-19): the render job's inputs move on the
    # `evolution` (600 s) and `terence` (1800 s) cadences, so at 300 s the
    # walls were rendered five times per terence refresh and four of those
    # five pages drew identical input. Slowed to the slowest input cadence so
    # a render cannot fire before its inputs have refreshed. The timeout
    # stays a per-run budget, not a cadence; tests/test_chain_controller.py
    # pins the cadence as a derivation over this table.
    "render": (1800, 240, "py", "tools/render_all_monitors.py", ()),
    # TERENCE re-engineered (operator request 2026-09-30): 4 oracles over
    # MEASURED state, replacing a source-pattern sweep that reported 8,009
    # "fixes" on a tree with 0 compile failures in 1,668 files -- and whose
    # two largest-volume fixes would have deleted 2,876 prints and commented
    # out 895 control-flow statements. Scan + verify only: `--apply` mutates
    # source and stays off, so this is NOT in MUTATING_JOBS and runs in
    # observation-only mode. Every fault it reports without a checkable
    # predicate is reportable and deliberately not auto-fixable.
    "terence": (1800, 900, "py", "tools/terence_invariants.py", ()),
    # Cross-pillar TERENCE digest (2026-09-30): the environment-scoped history
    # is only a tracking signal if it is WRITTEN on a cadence. Nightly, after
    # the scan, it persists state/terence_env_digest.{json,md} so drift across
    # the four pillars is a tracked artifact rather than a thing you look for
    # by hand. Display output; non-mutating, so it stays out of MUTATING_JOBS.
    "terence-env": ("nightly:01:30", 600, "py", "tools/terence_env_digest.py", ()),
    # Operator decision 2026-09-25: meta is AUTHORIZED to mutate. With apply
    # pinned off the self-modification loop sat on verdict=DRY ("no mutation
    # authorized") every cycle -- observe -> select -> blocked, forever
    # (FORENSIC_INVESTIGATION_2026-09-24.md PART 8, Layer 2).
    "meta": (3600, 1800, "py", "meta_evolution_cycle.py", ("--apply",)),
    # The algorithm laboratory runs in production (operator decision
    # 2026-09-24): policy + diversity evidence regenerated on the same hourly
    # cadence as meta-evolution, so qualification status is never hand-run.
    "algolab": (3600, 600, "py", "algorithm_lab_cycle.py", ()),
    "evolution": (600, 1200, "bat", "run_evolution_cycle_hyper.bat", ()),
    "train": (900, 1200, "py", "run_training_orchestrator.py", ()),
    "dream": (900, 300, "py", "dream_cycle.py", ()),
    "metacog": (900, 300, "py", "metacog_cycle.py", ()),
    "wiring": (1800, 900, "py", "wiring_cycle.py", ("--apply",)),
    "hyperopt": (3600, 1800, "py", "hyperopt_cycle.py", ("--apply",)),
    # Spine is the write-authority path: it runs the mesh apply cycle and
    # self-heals the stale_self_image gate (refresh_all + detached graph
    # refresh + retry). Both spine and mesh were dropped from the 2026-09-24
    # replacement chain, leaving the gate's remedy with no scheduler
    # (PART 8, Layer 3). Mesh is the fleet perception loop at its designed
    # 1-minute cadence (run_mesh_cycle.py docstring).
    "spine": (3600, 1800, "py", "spine_cycle_apply.py", ()),
    "mesh": (60, 300, "py", "run_mesh_cycle.py", ()),
    # Data-plane listener liveness (forensic 2026-09-27): the transport seam
    # had NO server end -- zero listeners on the canonical ports, so every
    # send() would refuse. This keepalive owns exactly one listener per env:
    # fresh heartbeat + live pid, else spawn a detached replacement.
    # Supervision only; non-mutating, so it stays out of MUTATING_JOBS.
    "mesh-listener": (120, 60, "py", "mesh_listener.py", ("--keepalive",)),
    # Derived-artifact freshness gate (forensic 2026-09-27): staleness was
    # only ever discovered by the next audit. Display output, not a mutation.
    "artifacts": (1800, 300, "py", "tools/artifact_freshness.py", ()),
    # Gossip over the data plane (2026-09-27): the seam's first real workload
    # -- one signed spine payload to every peer per run. Delivery to an
    # undeployed peer is an expected env_error, not a failure. Non-mutating.
    "mesh-gossip": (600, 120, "py", "mesh_gossip_cycle.py", ()),
    # Wire-or-cut queue (2026-09-27): bounded disposition PROPOSALS from the
    # backlog, nightly. Display output; the operator decides.
    "wire-triage": ("nightly:02:30", 300, "py", "wire_or_cut_queue.py", ()),
    # Bounded graphify refresh (2026-09-27): the 829k-node graph cannot be
    # refreshed inside any gate with a deadline (a full update exceeds 600s),
    # so it gets its own nightly slot with a generous budget.
    "graphify": ("nightly:02:00", 5400, "py", "graphify_refresh.py", ()),
    "liveness": (1800, 120, "py", "audit_loop_liveness.py", ()),
    # Capability liveness (registry L-5, 2026-10-11): the instrument that
    # answers "has this mechanism ever actually RUN?" had tests and no
    # scheduler -- a rule nobody runs, so a capability could be present,
    # reachable and never executed while every other instrument read healthy.
    # It is an in-memory comparison of counters against one state store, so
    # the only unbounded part is its in-process `artifact_freshness` call,
    # which the `artifacts` job above budgets 300s; 420 leaves headroom for
    # that plus the walk and stays under the 1800s interval, so at most one
    # run is in flight. Display output, non-mutating: not in MUTATING_JOBS,
    # so it also runs in observation-only mode.
    "capability-liveness": (1800, 420, "py", "tools/capability_liveness.py", ()),
    "maint": ("nightly:03:00", 1800, "py", "nightly_maintenance.py", ()),
    # Meta-reflection distill loop (2026-09-26, skill azazael-meta-reflection):
    # one generalized instruction per day from trial trajectories, validated
    # before storing. Display output (ledger + memory row), not a mutation.
    "reflect": ("nightly:04:00", 900, "py", "meta_reflect.py", ()),
    # Tensor-genome surrogate (2026-09-28 intermesh audit): consumed by the
    # A/B gate + monitors but scheduled by nothing since FullPass died.
    # Retrains when evolution results grew; skips fresh (rc=3, carved below).
    "tensor": (21600, 1800, "py", "train_tensor_model.py", ()),
}

# Session tracking for commit limits and parallel execution.
# Moved OUT of the JOBS dict literal (2026-10-14): this block had been spliced
# into the middle of the `terence` entry's comment, splitting that comment in
# two and leaving `MAX_FILES_PER_SESSION = 8` where a dict KEY was expected --
# SyntaxError at line 48, so tests/test_chain_controller.py could not even
# import the module. The constants are unchanged; only their home is.
MAX_FILES_PER_SESSION = 8   # ≤8-file same-session commits
MAX_PARALLEL_SLICES = 4     # max parallel slices (adjust to the machine)
_session_file_count = 0       # files committed in current session
_session_parallel_slices = 0  # active parallel slices

# QD Archive path for state measurement
QD_ARCHIVE_PATH = ROOT / "state" / "qd_archive.json"

#: Honest-skip exit codes per job (see run_one): a by-design skip is ok.
#: `terence` rc=1 is not a skip but a REPORT -- the invariant scan exits 1
#: because it FOUND faults (195 on a healthy tree), which is the job working.
#: Without this it logged a false failure every 1800s and drowned the real
#: ones. The oracle that reads _SKIP_RC (silent_crashes) is told the same
#: thing, so rc=1 here is never mistaken for a silent crash.
#: `capability-liveness` rc=1 is the same shape as terence: the tool exits 1
#: when it FOUND a capability that has never executed, or when the state store
#: could not be read at all -- the instrument reporting blindness is it
#: WORKING, not crashing (its docstring fixes those two as the only meanings
#: of 1). Its row still carries returncode, stdout and stderr either way, so
#: nothing is discarded. Residual, stated rather than hidden: an unhandled
#: exception ALSO exits 1, so this carves that code out of `ok` for this job
#: and relies on `silent_crashes` reading _SKIP_RC from source plus the
#: captured stderr to tell a report from a crash -- exactly the trade
#: `terence` already carries.
_SKIP_RC: dict[str, tuple[int, ...]] = {"train": (1,), "tensor": (3,),
                                       "terence": (1,),
                                       "capability-liveness": (1,)}

JOB_PRIORITY = {name: index for index, name in enumerate(JOBS)}

# Live job override (2026-09-27: hot-reload). Scheduling fixes used to rot
# unapplied: this table was imported at controller start, so an edit was inert
# until a process restart. The override file is re-read every call, so jobs
# can be added or retuned while the controller runs:
#   {"job": [interval, timeout, "py"|"bat", "target", [args...]]}
# The table above stays the default; the override wins per-name.
JOBS_OVERRIDE = ROOT / "state" / "jobs_override.json"
SKIP_RC_OVERRIDE = ROOT / "state" / "skip_rc_override.json"


def _current_jobs() -> dict[str, tuple[Any, int, str, str, tuple[str, ...]]]:
    jobs = dict(JOBS)
    override = _read_json(JOBS_OVERRIDE, {})
    if isinstance(override, dict):
        for name, spec in override.items():
            if isinstance(spec, (list, tuple)) and len(spec) == 5:
                jobs[name] = (spec[0], int(spec[1]), str(spec[2]),
                              str(spec[3]), tuple(spec[4]))
    return jobs


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _current_skip_rc() -> dict[str, tuple[int, ...]]:
    """Honest-skip codes, re-read from state/skip_rc_override.json every call.

    Same rationale as the jobs override: a fix must not wait for a restart. The
    first version read a module constant once at import, so adding
    `terence: (1,)` to the table was INERT in the running controller -- it kept
    logging a false failure every 1800s and would have kept doing so until a
    restart. The built-in table stays the default; the override wins per-name.
    """
    codes = dict(_SKIP_RC)
    override = _read_json(SKIP_RC_OVERRIDE, {})
    if isinstance(override, dict):
        for name, rcs in override.items():
            if isinstance(rcs, (list, tuple)):
                vals = []
                for x in rcs:
                    try:
                        vals.append(int(x))
                    except (TypeError, ValueError):
                        pass
                if vals:
                    codes[str(name)] = tuple(vals)
    return codes


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _heartbeat(event: str, **fields: Any) -> dict[str, Any]:
    """Append one bounded, machine-readable controller heartbeat."""
    row = {"at": datetime.now().isoformat(), "event": event, **fields}
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HEARTBEAT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return row


def _heartbeat_status() -> dict[str, Any]:
    try:
        age = max(0.0, time.time() - HEARTBEAT_PATH.stat().st_mtime)
    except OSError:
        return {"status": "MISSING", "age_s": None, "path": str(HEARTBEAT_PATH)}
    return {"status": "ok" if age <= 900 else "STALE", "age_s": round(age, 1),
            "path": str(HEARTBEAT_PATH)}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _lock_owner() -> dict[str, Any]:
    """Return the current live-controller owner without changing the lock."""
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        pid = 0
    alive = _pid_alive(pid)
    return {
        "pid": pid or None,
        "alive": alive,
        "current": alive and pid == os.getpid(),
        "path": str(LOCK_PATH),
    }


def _last_heartbeat() -> dict[str, Any]:
    """Read the latest controller heartbeat row for live status displays."""
    row: dict[str, Any] = {}
    try:
        with HEARTBEAT_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    try:
                        parsed = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(parsed, dict):
                        row = parsed
    except OSError:
        return {}
    return row


def _remember_fire(name: str, result: Mapping[str, Any], *,
                   observation_only: bool) -> None:
    """Preserve the latest real job attempt across later idle polls."""
    _write_json(LAST_FIRE_PATH, {
        "schema": "azazael.chain_controller.last_fire.v1",
        "at": datetime.now().isoformat(),
        "job": name,
        "result": dict(result),
        "observation_only": bool(observation_only),
    })


@contextlib.contextmanager
def controller_lock() -> Iterator[None]:
    """Ensure only one live controller can fire the replacement chain."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            old_pid = int(LOCK_PATH.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            old_pid = 0
        if old_pid and _pid_alive(old_pid):
            raise RuntimeError(f"chain controller already running (PID {old_pid})")
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        try:
            if LOCK_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
                LOCK_PATH.unlink()
        except OSError:
            pass


def _python() -> str:
    return os.environ.get("AZAZAEL_PYTHON") or sys.executable


def _resolve_target(target: str) -> Path | None:
    candidate = Path(target)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    for base in (SCRIPTS, ROOT):
        path = base / target
        if path.is_file():
            return path
    return None


def _own_task_state() -> tuple[str, int | None, str]:
    """Read the scheduler state; never enable, disable, or start the task."""

    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "CSV", "/v"],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace"
        )
    except Exception as exc:
        return "unknown", None, str(exc)[:160]
    if result.returncode != 0:
        return "unknown", result.returncode, (result.stderr or result.stdout or "")[:160]
    import io

    rows = [row for row in csv.reader(io.StringIO(result.stdout or "")) if row and any(row)]
    if len(rows) < 2:
        return "unknown", result.returncode, "unparsable scheduler table"
    header = [value.strip().lstrip("\ufeff").lower() for value in rows[0]]
    row = rows[1]

    def column(name: str) -> str:
        index = header.index(name) if name in header else -1
        return row[index].strip() if 0 <= index < len(row) else ""

    raw_status = column("status") or column("scheduled task state")
    status = raw_status.lower()
    if "disabled" in status:
        normalized = "disabled"
    elif any(word in status for word in ("ready", "running", "queued", "enabled")):
        normalized = "ok"
    else:
        normalized = "unknown"
    raw_result = column("last result")
    try:
        last_result = int(raw_result) if raw_result else None
    except ValueError:
        last_result = None
    return normalized, last_result, raw_status


def _job_due(name: str, now: float, state: Mapping[str, Any]) -> bool:
    interval = _current_jobs()[name][0]
    last = float((state.get(name) or {}).get("last", 0) or 0)
    if isinstance(interval, str) and interval.startswith("nightly:"):
        hour, minute = (int(part) for part in interval.split(":", 1)[1].split(":"))
        current = datetime.fromtimestamp(now)
        scheduled = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return now >= scheduled.timestamp() and (state.get(name) or {}).get("day") != current.date().isoformat()
    return (now - last) >= float(interval)


def plan(now: float | None = None, *, job: str | None = None,
         observation_only: bool = False) -> dict[str, Any]:
    """Return a deterministic execution plan without running anything."""

    timestamp = time.time() if now is None else float(now)
    due = _read_json(DUE_PATH, {})
    jobs = _current_jobs()
    candidates = [job] if job else [name for name in jobs if _job_due(name, timestamp, due)]
    candidates = [name for name in candidates if name in jobs]
    if observation_only:
        candidates = [name for name in candidates if name not in MUTATING_JOBS]
    candidates.sort(key=lambda name: (JOB_PRIORITY.get(name, 999), name))
    selected = candidates[0] if candidates else None
    result = {
        "schema": "azazael.chain_controller.plan.v1",
        "at": datetime.fromtimestamp(timestamp).isoformat(),
        "selected": selected,
        "due": candidates,
        "observation_only": bool(observation_only),
        "jobs": {name: {"target": jobs[name][3], "kind": jobs[name][2],
                         "mutating": name in MUTATING_JOBS} for name in jobs},
    }
    _write_json(PLAN_PATH, result)
    return result


def run_one(name: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Run one named job, or return the exact command without running it."""

    jobs = _current_jobs()
    if name not in jobs:
        raise ValueError(f"unknown job: {name}")
    _interval, timeout, kind, target, args = jobs[name]
    path = _resolve_target(target)
    if path is None:
        return {"job": name, "ok": False, "error": f"target not found: {target}"}
    if kind == "py":
        command = [_python(), str(path), *args]
    elif kind == "bat":
        command = ["cmd", "/c", str(path), *args]
    else:
        return {"job": name, "ok": False, "error": f"unsupported job kind: {kind}"}
    row: dict[str, Any] = {
        "job": name,
        "ok": True,
        "dry_run": bool(dry_run),
        "command": command,
        "started_at": datetime.now().isoformat(),
    }
    if dry_run:
        return row
    # Duration telemetry (2026-09-30). `started_at` was ALREADY being recorded
    # as an ISO string and simply never carried forward, while the state file
    # wrote `last_attempt` as an epoch float -- so any consumer computing a
    # duration got (str, float) and produced nothing. Measured: tools/chain_ops
    # reported "0/20 jobs measurable", which is what a job with no duration
    # looks like. You cannot choose a cadence for a process you have never
    # timed, so this is measured HERE, in float seconds, and propagated.
    _t0 = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=str(ROOT), capture_output=True,
                                text=True, timeout=timeout, encoding="utf-8", errors="replace")
        row.update({"returncode": result.returncode,
                    "stdout": (result.stdout or "")[-2000:],
                    "stderr": (result.stderr or "")[-2000:]})
        # Per-job honest-skip codes (2026-09-28: train rc=1, tensor rc=3).
        # A by-design skip (plateaued training set, fresh model, no data)
        # is not a failure, but a bare nonzero check cannot tell. Skips
        # are carved out per job; every other nonzero stays loud so real
        # failures never hide inside a skip.
        if result.returncode in _current_skip_rc().get(name, ()):
            row["ok"] = True
            row["skipped"] = True
        else:
            row["ok"] = result.returncode == 0
    except Exception as exc:
        row.update({"ok": False, "error": str(exc)[:300]})
    row["duration_s"] = round(time.perf_counter() - _t0, 3)
    row["timed_out"] = False
    if row.get("error") and "timed out" in str(row.get("error", "")).lower():
        row["timed_out"] = True
    row["finished_at"] = datetime.now().isoformat()
    row["finished_epoch"] = time.time()
    return row


def status() -> dict[str, Any]:
    task_state, scheduler_last_result, detail = _own_task_state()
    health = _read_json(HEALTH_PATH, {})
    last_fire = _read_json(LAST_FIRE_PATH, {})
    if not last_fire and isinstance(health.get("result"), dict):
        last_fire = {
            "schema": "azazael.chain_controller.last_fire.v1",
            "at": health.get("at"),
            "job": health.get("selected"),
            "result": health.get("result"),
            "observation_only": bool(health.get("observation_only", False)),
        }
    last_result = last_fire.get("result") or {}
    observation_only = bool(
        last_fire.get("observation_only", health.get("observation_only", False))
    )
    current_plan = plan(observation_only=observation_only)
    last_heartbeat = _last_heartbeat()
    owner = _lock_owner()
    if not owner.get("pid"):
        try:
            heartbeat_pid = int(last_heartbeat.get("pid") or 0)
        except (TypeError, ValueError):
            heartbeat_pid = 0
        if heartbeat_pid:
            owner = {
                "pid": heartbeat_pid,
                "alive": _pid_alive(heartbeat_pid),
                "current": heartbeat_pid == os.getpid(),
                "source": "heartbeat",
            }
    return {
        "schema": "azazael.chain_controller.status.v1",
        "at": datetime.now().isoformat(),
        "task": TASK_NAME,
        "task_state": task_state,
        "task_last_result": scheduler_last_result,
        "last_result": last_result,
        "task_detail": detail,
        "jobs": current_plan,
        "heartbeat": _heartbeat_status(),
        "last_heartbeat": last_heartbeat,
        "owner": owner,
        "last_health": health,
        "last_fire": last_fire or None,
        "scheduler_mutated": False,
    }


def _mark_completed(name: str, result: Mapping[str, Any]) -> None:
    """Record every real attempt, including failures, so one bad job cannot monopolize the controller."""
    if result.get("dry_run"):
        return
    due = _read_json(DUE_PATH, {})
    row = dict(due.get(name) or {})
    _now = time.time()
    row.update({
        "last": _now,
        "last_attempt": _now,
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        # Epoch floats on BOTH sides. The previous state file wrote
        # `finished_at` as an ISO string while `last_attempt` was an epoch
        # float, so every duration consumer had to bail on a type mismatch and
        # report "unmeasurable" for all 20 jobs. Recorded here once, correctly.
        "started_epoch": _now - float(result.get("duration_s") or 0.0),
        "finished_epoch": float(result.get("finished_epoch") or _now),
        "duration_s": result.get("duration_s"),
        "timed_out": bool(result.get("timed_out")),
        "returncode": result.get("returncode"),
        # Persist the honest-skip flag. _run_job sets ok=True, skipped=True for
        # a BY-DESIGN non-zero exit (train rc=1, tensor rc=3), but the flag was
        # computed here and never written, so the due file showed only `rc!=0`
        # with `ok=true` -- indistinguishable from a copied verdict. That is why
        # the liveness auditor could not tell an honest skip from a hidden
        # failure, and why TERENCE's verdict_disagreement fired on a designed
        # skip. The distinction now survives the trip into the artifact.
        "skipped": bool(result.get("skipped")),
        "ok": bool(result.get("ok")),
        "error": result.get("error", ""),
    })
    if isinstance(JOBS[name][0], str):
        row["day"] = datetime.now().date().isoformat()
    due[name] = row
    _write_json(DUE_PATH, due)


def _write_job_heartbeat(name: str, result: Mapping[str, Any]) -> None:
    """Write the per-job heartbeat expected by the liveness auditor."""
    path = LOG_DIR / f"chain_{name}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": result.get("finished_at") or datetime.now().isoformat(),
        "job": name,
        "ok": bool(result.get("ok")),
        "returncode": result.get("returncode"),
        # Same reason as the due file: a skip that is not recorded cannot be
        # told apart from a failure by anyone reading the heartbeat.
        "skipped": bool(result.get("skipped")),
        "error": result.get("error", ""),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def catch_up(*, dry_run: bool = False, observation_only: bool = True,
            include_mutations: bool = False) -> dict[str, Any]:
    """Run the safe backlog once, sequentially, without waiting for intervals.

    Catch-up intentionally excludes mutation-capable jobs and nightly maintenance.
    It records every attempt and continues after a failed job, so a transient
    liveness failure cannot prevent the remaining backlog from progressing.
    """
    names = [name for name in JOBS if name != "maint"]
    if not include_mutations:
        names = [name for name in names if name not in MUTATING_JOBS]
    results: list[dict[str, Any]] = []
    with controller_lock():
        for name in names:
            _heartbeat("catch_up", selected=name, dry_run=bool(dry_run))
            result = run_one(name, dry_run=dry_run)
            _mark_completed(name, result)
            if not dry_run:
                _write_job_heartbeat(name, result)
            results.append(result)
    report = {
        "schema": "azazael.chain_controller.catch_up.v1",
        "at": datetime.now().isoformat(),
        "ran": not dry_run,
        "observation_only": bool(observation_only),
        "include_mutations": bool(include_mutations),
        "jobs": results,
        "ok": all(row.get("ok") for row in results),
        "scheduler_mutated": False,
    }
    _write_json(HEALTH_PATH, report)
    if results and not dry_run:
        _remember_fire(results[-1].get("job", "unknown"), results[-1],
                       observation_only=observation_only)
    _heartbeat("catch_up_complete", ok=report["ok"], dry_run=bool(dry_run))
    return report


#: Post-job follow-ups (2026-09-28): wiring --apply mutates sources, which
#: immediately dirties the derived maps the artifacts gate judges -- the
#: chronic wiring -> stale -> spine-skip loop. After a SUCCESSFUL wiring
#: fire, rebuild the maps at once (same refresh_all.py spine self-heals
#: with) instead of waiting for the next artifacts FAIL + spine SKIP.
#: Best-effort and bounded: a failed follow-up is logged, never fails the
#: fire (the gate remains the authority). No chaining: follow-ups run no
#: further hooks. Format: {job: (target, timeout_s)}.
FOLLOW_UPS: dict[str, tuple[str, int]] = {
    "wiring": ("tools/refresh_all.py", 900),
}


def _maybe_followup(name: str, result: dict[str, Any], *,
                    dry_run: bool = False,
                    observation_only: bool = False) -> dict[str, Any]:
    """Run the follow-up for a just-fired job, if any. Never raises."""
    if dry_run or observation_only:
        return {"ran": False, "reason": "dry/observation run"}
    spec = FOLLOW_UPS.get(name)
    if spec is None:
        return {"ran": False, "reason": "no follow-up registered"}
    if not result.get("ok"):
        return {"ran": False, "reason": "job not ok, follow-up skipped"}
    target, timeout = spec
    path = _resolve_target(target)
    if path is None:
        return {"ran": False, "reason": f"target not found: {target}"}
    try:
        proc = subprocess.run([_python(), str(path)], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        tail = [ln for ln in (proc.stdout or "").strip().splitlines()
                if ln.strip()]
        return {"ran": True, "target": target, "rc": proc.returncode,
                "tail": tail[-1][:160] if tail else "no-output"}
    except Exception as exc:
        return {"ran": False, "reason": str(exc)[:160]}


def fire(*, dry_run: bool = False, observation_only: bool = False) -> dict[str, Any]:
    """Plan and optionally execute at most one job."""

    with controller_lock():
        selected = plan(observation_only=observation_only).get("selected")
        _heartbeat("fire", pid=os.getpid(), selected=selected,
                   dry_run=bool(dry_run), observation_only=bool(observation_only))
        if not selected:
            report = {"schema": "azazael.chain_controller.fire.v1",
                      "at": datetime.now().isoformat(), "ran": False,
                      "reason": "no due job", "selected": None,
                      "observation_only": bool(observation_only),
                      "scheduler_mutated": False}
            _write_json(HEALTH_PATH, report)
            return report
        result = run_one(selected, dry_run=dry_run)
    _mark_completed(selected, result)
    if not dry_run:
        _write_job_heartbeat(selected, result)
        _remember_fire(selected, result, observation_only=observation_only)
    followup = _maybe_followup(selected, result, dry_run=dry_run,
                               observation_only=observation_only)
    report = {
        "schema": "azazael.chain_controller.fire.v1",
        "at": datetime.now().isoformat(),
        "ran": not dry_run,
        "selected": selected,
        "result": result,
        "followup": followup,
        "observation_only": bool(observation_only),
        "scheduler_mutated": False,
    }
    _write_json(HEALTH_PATH, report)
    _heartbeat("completed", pid=os.getpid(), selected=selected,
               ok=result.get("ok"), dry_run=bool(dry_run))
    return report


def _run_live(args: argparse.Namespace) -> int:
    """Run one resilient live-controller owner until stopped."""
    _heartbeat("live_start", pid=os.getpid(), interval=args.interval,
               allow_mutations=bool(args.allow_mutations),
               catch_up_on_start=bool(args.catch_up_on_start))
    try:
        if args.catch_up_on_start:
            try:
                result = catch_up(dry_run=args.dry_run,
                                  observation_only=not args.allow_mutations,
                                  include_mutations=args.allow_mutations)
                print(json.dumps(result, sort_keys=True), flush=True)
            except RuntimeError as exc:
                if "already running" in str(exc):
                    _heartbeat("live_start_skipped", reason=str(exc))
                    print(str(exc), flush=True)
                    return 0
                raise
        cycles = 0
        while True:
            try:
                result = fire(dry_run=args.dry_run,
                              observation_only=not args.allow_mutations)
            except RuntimeError as exc:
                if "already running" in str(exc):
                    _heartbeat("live_poll_skipped", pid=os.getpid(), reason=str(exc))
                    print(str(exc), flush=True)
                    return 0
                raise
            print(json.dumps(result, sort_keys=True), flush=True)
            cycles += 1
            if args.max_cycles and cycles >= args.max_cycles:
                return 0
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        _heartbeat("live_stop", pid=os.getpid(), reason="interrupt")
        return 0
    finally:
        _heartbeat("live_exit", pid=os.getpid())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="read scheduler and job state only")
    parser.add_argument("--dry-run", action="store_true", help="plan without executing a job")
    parser.add_argument("--job", choices=tuple(JOBS), help="execute one named job")
    parser.add_argument("--live", action="store_true",
                        help="run the controller loop; observation-only by default")
    parser.add_argument("--interval", type=float, default=60.0,
                        help="seconds between live controller checks")
    parser.add_argument("--max-cycles", type=int, default=0,
                        help="stop after this many live cycles (0 means unlimited)")
    parser.add_argument("--allow-mutations", action="store_true",
                        help="allow wiring/hyperopt --apply in live mode")
    parser.add_argument("--catch-up", action="store_true",
                        help="run the safe backlog once without interval waits")
    parser.add_argument("--catch-up-on-start", action="store_true",
                        help="run the safe backlog once when a live process starts")
    args = parser.parse_args(argv)
    if args.status:
        print(json.dumps(status(), indent=2, sort_keys=True))
        return 0
    if args.job:
        result = run_one(args.job, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 1
    if args.catch_up:
        result = catch_up(dry_run=args.dry_run,
                          observation_only=not args.allow_mutations,
                          include_mutations=args.allow_mutations)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") or args.dry_run else 1
    if args.live:
        return _run_live(args)
    result = fire(dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ran") or args.dry_run else 0


if __name__ == "__main__":
    raise SystemExit(main())
