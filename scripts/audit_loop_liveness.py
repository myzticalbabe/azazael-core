#!/usr/bin/env python3
# coding: utf-8
"""audit_loop_liveness.py — does every scheduled loop still actually run?

Why this exists. On 2026-09-16T01:54 `\\Azazael-Chain` — the single scheduled
entry point that replaced nine tasks — was disabled. Its own log stopped
mid-flight and nothing else changed: no error, no alert, no non-zero exit
anywhere. Four days of self-improvement simply did not happen. Separately, the
hourly spine cycle kept firing while refusing to write from 2026-09-18T20:45,
also silently. Both were "a loop stopped and nothing said so".

WHY THIS IS NOT A LIST OF LOOPS
-------------------------------
The first version of this file hard-coded a table of (loop, heartbeat path,
silence bound, task name). That is the same defect as the stale maps it exists
to catch: a hand-maintained constant drifts, and it drifts *silently* — a
renamed log path reads as a dead loop, a loosened bound switches the check off,
and a loop added tomorrow is watched by nobody, because it was never typed in.

So the audit adapts instead, from the machine's own state:

  DISCOVERY   Every scheduled task is enumerated in one `schtasks` call, and
              the ones that belong to this project are watched automatically.
              A loop added tomorrow is covered without editing this file.
  CADENCE     The silence bound is DERIVED from each task's declared schedule
              (Minute / Hourly / Daily) rather than typed. Change a task from
              hourly to daily and the bound follows.
  SUPERSEDE   Which tasks are retired ON PURPOSE is derived from the chain's
              own JOBS table, not declared here. The chain replaced nine
              standalone tasks; whether a Disabled task is retired or
              accidentally switched off is decided by asking whether any chain
              job runs the same script. Retire a tenth task tomorrow and it is
              judged correctly without editing this file.
  BINDINGS    Real write-heartbeats are learned and persisted, then replayed.
              They are the only signal that can see "task is Ready and the loop
              is writing nothing" — the spine cycle's exact failure.
  SHRINKAGE   Every task ever seen is remembered. One that disappears is
              reported as LOST rather than quietly dropping out of scope, so
              coverage cannot shrink without being noticed.

Two independent signals per loop, because the two real outages differed:
`\\Azazael-Chain` was healthy-but-DISABLED (task state caught it), and the
spine cycle was enabled-and-SILENT (heartbeat caught it). Either signal alone
would have missed one.

`evolution_audit.py` states the principle: *"A guard nobody runs is not a
guard."* This runs as a `liveness` job inside the chain, so it watches every
sibling loop unattended. It cannot detect its own death by running — nothing
can — which is what the SHRINKAGE and task-state checks are for.

Exit 0 = every discovered loop is enabled, recent, and writing.
Exit 1 = at least one loop is silent, disabled, missing, or lost.

Usage:
    python scripts/audit_loop_liveness.py
    python scripts/audit_loop_liveness.py --json
    python scripts/audit_loop_liveness.py --no-tasks    # WSL/Linux: no schtasks
    python scripts/audit_loop_liveness.py --grace 2.0   # scale every bound
    python scripts/audit_loop_liveness.py --discover    # show what it found
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: Task states that mean "this loop is not scheduled to run".
DEAD_STATES = {"disabled"}

#: Task states that mean "this loop is scheduled to run".
#:
#: Both sets are needed, and an unrecognised word is neither. Failing closed on
#: an unknown state is load-bearing: `csv.reader` parses arbitrary garbage as a
#: single field, so treating "the last field is not Disabled" as healthy made a
#: garbage response read as a working task. The first version of this file did
#: exactly that, and its own test caught it.
#: `schtasks` uses TWO vocabularies and they must not be confused:
#:   `Status`                -> Ready | Running | Disabled
#:   `Scheduled Task State`  -> Enabled | Disabled
#: Reading only the first left every seed loop reported as "unrecognised state
#: 'Enabled'", i.e. the watchdog failed on healthy tasks.
LIVE_STATES = {"ready", "running", "queued", "enabled"}

#: Declared cadence unit -> seconds. Parsed out of the task's Schedule Type.
CADENCE_UNITS: dict[str, float] = {
    "minute": 60.0,
    "hourly": 3600.0,
    "daily": 86400.0,
    "weekly": 604800.0,
    "monthly": 2592000.0,
}

#: Slack per unit, as a multiple of the cadence.
#:
#: Deliberately loose: a check that fails on facts gets switched off, so the
#: smallest thing this should ever report is a loop that has STOPPED, never one
#: that is merely slow. Minute-cadence loops get the most slack because the
#: chain runs its mesh cycle INLINE on every fire and that alone can take
#: 30-150s (measured: the first fire after a four-day gap took 154s).
SLACK_BY_UNIT: dict[str, float] = {
    "minute": 15.0,
    "hourly": 3.0,
    "daily": 3.0,
    "weekly": 2.0,
    "monthly": 2.0,
}
DEFAULT_SLACK = 3.0

#: Seed bindings: heartbeats already established by hand, which discovery
#: cannot infer (a task's command does not declare where it logs).
#:
#: Layout is (name, heartbeat, silence bound, task name, what writes it). Once
#: a heartbeat is confirmed it is persisted to STATE_PATH, so this table is the
#: starting knowledge, not the ongoing source of truth.
REGISTRY: tuple[tuple[str, str, float, str, str], ...] = (
    ("chain", "logs/chain_controller.log", 900, "Azazael-Chain",
     "chain_controller.py, read-only controller state"),
    ("spine", "~/.azazael/spine_cycle.log", 3 * 3600, "AzazaelSpineCycle",
     "spine_cycle_apply.py, hourly"),
    ("exmachina", "state/exmachina_health.json", 3 * 86400, "Ex Machina Health",
     "exmachina_health_alert.py, daily"),
    ("berks", "G:/source-of-truth/azazael_models/bcc_status.jsonl", 3 * 86400,
     "AzazaelBerksMonitor", "berks_monitor.py, daily"),
    ("threat", "G:/source-of-truth/azazael_models/threat_status.jsonl", 3 * 86400,
     "AzazaelThreatMonitor", "threat_monitor.py, daily"),
)

#: The supersession AUTHORITY: the chain's own job table.
#:
#: Nine tasks predate `Azazael-Chain`, which replaced them -- the chain's
#: docstring lists each with its "previous standalone task in parens" and
#: records that the single-supervisor design removed "the old 4x/5min DB
#: dogpile". So their being Disabled is correct, and a watchdog that flags them
#: every run would be crying wolf on facts -- which is how a check gets switched
#: off. But a hand-typed list of WHICH ones is the same drift class this file
#: exists to catch, so the list is not typed: it is read back out of the chain.
#:
#: The check stays meaningful in BOTH directions: if one of these is ever
#: ENABLED again it is running alongside the chain's copy of the same work,
#: which is the dogpile returning, and that is a real fault.
CHAIN_SCRIPT = ROOT / "scripts" / "chain_controller.py"

#: Files that ARE the thing being run -- matched on basename between a task's
#: command and a chain job's target.
_SCRIPT_EXT = re.compile(r"\.(py|bat|cmd|sh|ps1)$", re.I)

#: Interpreters are never the binding: every python task would match every
#: python job on "python.exe" alone.
_INTERPRETERS = {"python", "python.exe", "py.exe", "wsl", "wsl.exe",
                 "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh.exe"}

#: Learned state: bound heartbeats and every task name ever seen. Untracked, so
#: a checkout cannot revert what the audit has learned.
STATE_PATH = ROOT / "state" / "loop_liveness.json"


# --------------------------------------------------------------------------
# Pure parsing / derivation. No I/O, so each piece is directly testable.
# --------------------------------------------------------------------------

def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p)) if not os.path.isabs(p) else Path(p)


#: Unit words as `Repeat: Every` actually writes them -- "1 Hour(s), 0 Minute(s)",
#: not "hourly". Longest word first so "min" cannot shadow "month".
_REPEAT_UNITS: tuple[tuple[str, float], ...] = (
    ("month", 2592000.0), ("week", 604800.0), ("day", 86400.0),
    ("hour", 3600.0), ("min", 60.0), ("sec", 1.0),
)


def cadence_from_repeat(repeat_every: str) -> float | None:
    """Seconds between fires from the task's `Repeat: Every` field.

    The field is a PAIR, not a number: "1 Hour(s), 0 Minute(s)". Two bugs in an
    earlier version were both caused by treating it as one value -- it took the
    first token, and matched unit words that `Repeat:` never writes ("hourly"
    is "hour(s)" here). So "1 Hour(s), 0 Minute(s)" parsed as the 0-minute
    remainder (60s), reporting an hourly loop as a one-minute one.

    Every recognised component is therefore summed. Nothing recognised => None,
    which the caller treats as "unknown" rather than as a default guess.
    """
    if not repeat_every:
        return None
    low = repeat_every.lower()
    total = 0.0
    for word, secs in _REPEAT_UNITS:
        m = re.search(r"(\d+)\s*" + word, low)
        if m:
            total += int(m.group(1)) * secs
    return total if total > 0 else None


def cadence_seconds(schedule_type: str, repeat_every: str = "") -> float | None:
    """Seconds between fires, from the declared schedule.

    `schtasks` renders Schedule Type as e.g. "One Time Only, Hourly" (the
    "/sc hourly" form), possibly with an embedded newline. Returns None when no
    unit is recognisable, which the caller must treat as "bound unknown" rather
    than as a default guess.
    """
    explicit = cadence_from_repeat(repeat_every)
    if explicit is not None:
        return explicit
    if not schedule_type:
        return None
    low = schedule_type.lower()
    for unit, secs in sorted(CADENCE_UNITS.items(),
                             key=lambda kv: -kv[1]):  # longest unit first
        if unit in low:
            return secs
    return None


def bound_from_schedule(schedule_type: str, grace: float = 1.0,
                        repeat_every: str = "") -> float | None:
    """Silence bound derived from the declared cadence. None if undeterminable."""
    secs = cadence_seconds(schedule_type, repeat_every)
    if secs is None:
        return None
    unit = next((u for u, s in CADENCE_UNITS.items() if s == secs), "")
    if unit not in SLACK_BY_UNIT:          # an explicit interval like 5 minutes
        unit = next((u for u, s in CADENCE_UNITS.items()
                     if s <= secs <= s * 60), "")
    return secs * SLACK_BY_UNIT.get(unit, DEFAULT_SLACK) * grace


def chain_job_targets(chain_path: Path | None = None) -> dict[str, list[str]]:
    """{job: [string literals it runs]} read from the chain's JOBS table.

    Parsed with `ast` rather than imported. Importing the chain mutates
    `sys.path` as a side effect, and an audit must not depend on the behaviour
    of the thing it audits to decide whether that thing is healthy.

    Returns {} when the table cannot be read; the caller treats that as
    "supersession is unjudgeable" rather than as "nothing is superseded".
    """
    path = chain_path or CHAIN_SCRIPT
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    for node in tree.body:
        # JOBS is annotated (`JOBS: dict[...] = {...}`), which is an AnnAssign
        # rather than an Assign.  The audit parses the controller as source so
        # it can identify retired jobs without importing/running it; missing
        # AnnAssign support made the real controller look unreadable and caused
        # false liveness failures.
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        elif isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "JOBS" for t in targets):
            continue
        if not isinstance(value, ast.Dict):
            continue
        out: dict[str, list[str]] = {}
        for key, val in zip(value.keys, value.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                out[key.value] = [n.value for n in ast.walk(val)
                                  if isinstance(n, ast.Constant)
                                  and isinstance(n.value, str)]
        return out
    return {}


def chain_job_specs(chain_path: Path | None = None) -> dict[str, dict]:
    """{job: {"interval": int|str|None, "target": str}} from the chain's JOBS table.

    Parsed with `ast` (never imported) for the same reason as
    `chain_job_targets`: an audit must not run the thing it audits to decide
    whether that thing is healthy. `JOBS` values are tuples whose first element
    is the interval and whose fourth is the target script.
    """
    path = chain_path or CHAIN_SCRIPT
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        elif isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "JOBS" for t in targets):
            continue
        if not isinstance(value, ast.Dict):
            continue
        out: dict[str, dict] = {}
        for key, val in zip(value.keys, value.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            if not isinstance(val, ast.Tuple) or not val.elts:
                continue
            first = val.elts[0]
            interval = first.value if isinstance(first, ast.Constant) else None
            target = ""
            if len(val.elts) >= 4 and isinstance(val.elts[3], ast.Constant):
                target = str(val.elts[3].value)
            out[key.value] = {"interval": interval, "target": target}
        return out
    return {}


def chain_job_bindings(chain_path: Path | None = None) -> list[tuple]:
    """Per-job heartbeat bindings derived from the chain's own JOBS table.

    The chain runs its jobs (dream/metacog/evolution/train/...) one at a time,
    each writing `logs/chain_<job>.log`. Binding those files is what makes a
    STARVED job visible: on 2026-09-20 `dream`/`metacog`/`evolution` had not run
    since 2026-09-16 while every watchdog still printed OK, because no heartbeat
    was bound to them (`unbound` = watched by task state only, which cannot see
    a job that never gets its turn).

    A job whose log has never been written is skipped, not reported MISSING:
    "never scheduled yet" and "starved" must not look alike, or the check cries
    wolf on a fresh machine and gets switched off.
    """
    out: list[tuple] = []
    for job, spec in sorted(chain_job_specs(chain_path).items()):
        hb_path = ROOT / "logs" / f"chain_{job}.log"
        if not hb_path.exists():
            continue
        interval = spec.get("interval")
        if isinstance(interval, (int, float)) and interval > 0:
            # Generous floor on purpose: every interval job shares ONE slow
            # slot, so its effective cadence is far longer than its own
            # interval. A tight bound would fail on ordinary scheduling jitter
            # -- and a check that cries wolf gets switched off. The chain's own
            # heartbeat already catches the chain being down; this binding is
            # for a job the chain keeps skipping while it is otherwise healthy.
            bound = max(float(interval) * 12.0, 6 * 3600.0)
        else:
            bound = 3 * 86400.0            # nightly jobs (interval is a string)
        out.append((f"chain:{job}", str(hb_path), bound, "Azazael-Chain",
                    f"chain job '{job}' runs {spec.get('target') or job}"))
    return out


def command_scripts(text: str) -> set[str]:
    """Basenames of the scripts a command line runs.

    Paths are compared by basename on purpose: the same script appears as
    `C:\\...\\scripts\\dream_cycle.py` in a task and as `dream_cycle.py` in the
    chain table, and as a `/mnt/c/...` path under WSL.
    """
    out: set[str] = set()
    for tok in re.split(r"[\s\"']+", text or ""):
        base = tok.strip().strip("\"'").replace("\\", "/").rsplit("/", 1)[-1]
        if base.lower() in _INTERPRETERS:
            continue
        if _SCRIPT_EXT.search(base):
            out.add(base.lower())
    return out


def _flag_pairs(text: str) -> set[str]:
    """Flags and their values, to break ties between jobs on one script.

    `rani_llm_test.py` is run by two chain jobs that differ only in
    `--backend openrouter` vs `--backend gosp`; without this both retired
    batteries would be attributed to whichever job sorts first.
    """
    toks = [t.strip().strip("\"'") for t in re.split(r"\s+", text or "")
            if t.strip()]
    out: set[str] = set()
    for i, tok in enumerate(toks):
        if tok.startswith("-"):
            out.add(tok.lower())
            if i + 1 < len(toks) and not toks[i + 1].startswith("-"):
                out.add(toks[i + 1].lower())
    return out


def superseded_by(rec: dict | None,
                  chain_jobs: dict[str, list[str]] | None = None) -> str | None:
    """Why this task's work is already done by a chain job, or None.

    Derived from the chain's JOBS table rather than declared in this file. That
    is the difference between a watchdog that adapts and one that drifts until
    it cries wolf on a legitimate retirement and gets switched off.
    """
    if not rec:
        return None
    if chain_jobs is None:
        chain_jobs = chain_job_targets()
    cmd = rec.get("command") or ""
    scripts = command_scripts(cmd)
    if not scripts:
        return None
    best: tuple[str, set[str]] | None = None
    best_score = 0
    for job in sorted(chain_jobs):
        blob = " ".join(chain_jobs[job])
        hits = scripts & command_scripts(blob)
        if not hits:
            continue
        score = 100 * len(hits) + len(_flag_pairs(cmd) & _flag_pairs(blob))
        if score > best_score:
            best, best_score = (job, hits), score
    if best is None:
        return None
    job, hits = best
    return f"chain job '{job}' runs {', '.join(sorted(hits))}"


def is_project_task(rec: dict, root: Path | None = None) -> bool:
    """Does this scheduled task belong to this project?

    Matched on the task name, or on the command it runs referencing the repo
    root. The command test is what makes discovery adapt: a task named anything
    at all is picked up if it drives this codebase.
    """
    root = root or ROOT
    name = (rec.get("name") or "").strip("\\").lower()
    if "azazael" in name:
        return True
    cmd = (rec.get("command") or "").replace("\\", "/").lower()
    return str(root).replace("\\", "/").lower() in cmd


def parse_task_table(text: str) -> list[dict]:
    """Parse `schtasks /query /fo CSV /v` output into records.

    Columns are located by header name when a header row is present, and fall
    back to the documented positions otherwise. Both paths are needed: the
    header makes it robust to a `schtasks` build that reorders or adds columns,
    and the positional fallback keeps it working with `/nh`.
    """
    rows = [r for r in csv.reader(io.StringIO(text)) if r and any(r)]
    if not rows:
        return []

    wanted = {"taskname": "name", "status": "status", "last run time": "last_run",
              "task to run": "command",
              "scheduled task state": "scheduled_state",
              "schedule type": "schedule_type",
              "repeat: every": "repeat_every"}
    idx: dict[str, int] = {}
    body = rows
    header = [c.strip().lower() for c in rows[0]]
    if "taskname" in header:
        for col, canon in wanted.items():
            if col in header:
                idx[canon] = header.index(col)
        body = rows[1:]
    if "name" not in idx:                      # positional fallback
        # Verified against this machine's `schtasks` (28 columns): index 18 is
        # Schedule Type and 19 is Start Time. A first attempt used 19 and read
        # every task's start time as its cadence, so no bound ever derived.
        idx = {"name": 1, "status": 3, "last_run": 5, "command": 8,
               "scheduled_state": 11, "schedule_type": 18, "repeat_every": 24}

    out = []
    for r in body:
        if len(r) <= max(idx.values()):
            continue
        rec = {k: (r[i].strip() if i < len(r) else "") for k, i in idx.items()}
        if not rec.get("name"):
            continue
        rec["name"] = rec["name"].strip("\\")
        out.append(rec)
    return out


def heartbeat_status(path: Path, max_silence: float) -> tuple[str, str]:
    """(status, evidence) for one heartbeat file. Never raises."""
    try:
        st = path.stat()
    except FileNotFoundError:
        # Distinguish "the loop has never written" from "the volume is absent":
        # an unmounted G: is a different problem with a different fix.
        if not path.parent.exists():
            return "UNREADABLE", f"parent volume absent: {path.parent}"
        return "MISSING", "never written"
    except OSError as exc:
        return "UNREADABLE", str(exc)[:80]
    age = time.time() - st.st_mtime
    if age > max_silence:
        return "SILENT", f"last write {age / 3600:.1f}h ago (limit {max_silence / 3600:.1f}h)"
    return "ok", f"{age / 60:.1f}min ago"


def task_status(name: str) -> tuple[str, str]:
    """(status, evidence) for one scheduled task. 'UNKNOWN' fails closed."""
    try:
        proc = subprocess.run(
            ["schtasks", "/query", "/tn", name, "/fo", "CSV", "/nh"],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "UNKNOWN", "schtasks unavailable (use --no-tasks off Windows)"
    except Exception as exc:
        return "UNKNOWN", str(exc)[:80]
    out = (proc.stdout or "").strip()
    if not out:
        err = (proc.stderr or "").strip()
        if "cannot find" in err.lower():
            return "NO-TASK", "task does not exist"
        return "NO-TASK", err[:80] or "no output"
    try:
        row = next(csv.reader(io.StringIO(out)))
        state = row[-1].strip()
    except Exception:
        return "UNKNOWN", f"could not parse: {out[:60]}"
    low = state.lower()
    if low in DEAD_STATES:
        return "DISABLED", state
    if low in LIVE_STATES:
        return "ok", state
    return "UNKNOWN", f"unrecognised state {state[:40]!r}"


def state_status(rec: dict | None) -> tuple[str, str]:
    """(status, evidence) for a discovered task record."""
    if rec is None:
        return "NO-TASK", "not present in the task table"
    raw = (rec.get("scheduled_state") or rec.get("status") or "").strip()
    low = raw.lower()
    if low in DEAD_STATES:
        return "DISABLED", raw
    if low in LIVE_STATES:
        return "ok", raw
    return "UNKNOWN", f"unrecognised state {raw[:40]!r}"


def last_run_status(rec: dict | None, bound: float | None) -> tuple[str, str]:
    """Is the task engine still firing this task? Parsed from Last Run Time.

    This is the signal that needs no heartbeat at all, so it covers a discovered
    loop the moment it appears. It cannot see a loop that fires and writes
    nothing -- heartbeat bindings exist for exactly that gap.
    """
    if rec is None:
        return "NO-TASK", "not present in the task table"
    raw = (rec.get("last_run") or "").strip()
    if not raw or raw.upper() == "N/A":
        return "NEVER-RUN", "no last run recorded"
    if bound is None:
        return "unknown", "no cadence declared, cannot bound"
    try:
        ts = time.mktime(time.strptime(raw, "%m/%d/%Y %I:%M:%S %p"))
    except ValueError:
        return "unknown", f"unparsed last run {raw[:30]!r}"
    age = time.time() - ts
    if age > bound:
        return "STALE", f"last run {age / 3600:.1f}h ago (bound {bound / 3600:.1f}h)"
    return "ok", f"{age / 60:.1f}min ago"


#: Operator-policy retirements (2026-09-28): tasks deliberately OFF for a
#: stated reason that is NOT supersession. Judged, not ignored -- shown in
#: the coverage block like retired tasks, never failed. Format: loop name
#: -> reason. Reason one: the zero-spend policy (no paid inference).
POLICY_RETIRED: dict[str, str] = {
    "Azazael-EveBattery": "zero-spend policy: openrouter backend costs; "
                          "use GospBattery (local) for battery signal",
}


def failed(row: dict) -> bool:
    """One loop fails if ANY independent signal says it is not running.

    A declared-superseded task inverts: being OFF is correct, and being ON is
    the fault, because it is then duplicating a chain job.
    """
    if row.get("superseded"):
        return row.get("task_status") == "ok"
    if row.get("run_status") == "policy-retired":
        return False
    if row["hb_status"] not in ("ok", "unbound"):
        return True
    if row.get("task_status", "ok") != "ok":
        return True
    if row.get("run_status", "ok") not in ("ok", "unknown", "superseded"):
        return True
    return row.get("lost") is True


def merge_entries(discovered: list[dict], seed=None, learned=None) -> list[dict]:
    """One entry per loop: discovered tasks plus seed/learned heartbeats.

    Ordering is: every seed binding, then every other discovered project task.
    A discovered task with no heartbeat comes back `unbound` and is reported as
    such -- visible coverage, never silence.
    """
    seed = REGISTRY if seed is None else seed
    learned = learned or {}
    by_task: dict[str, dict] = {}
    for discovered_rec in discovered:
        by_task.setdefault(discovered_rec["name"].lower(), discovered_rec)

    entries: list[dict] = []
    seen_tasks: set[str] = set()
    for name, hb, bound, task, what in seed:
        task_rec: dict[str, Any] | None = by_task.get(task.lower())
        if task_rec is not None:
            seen_tasks.add(task.lower())
        known_hb = learned.get(task) or hb
        entries.append({"loop": name, "task": task, "heartbeat": known_hb,                         "what": what, "bound": bound, "rec": task_rec,
                        "source": "seed"})
    for rec in discovered:
        low = rec["name"].lower()
        if low in seen_tasks:
            continue
        bound = bound_from_schedule(rec.get("schedule_type", ""),
                                    repeat_every=rec.get("repeat_every", ""))
        cmd = (rec.get("command") or "").split()
        script = next((c for c in cmd if c.lower().endswith(".py")), "")
        entries.append({
            "loop": rec["name"], "task": rec["name"], "heartbeat": "",
            "what": f"discovered: {os.path.basename(script) or rec.get('command', '')[0:60]}",
            "bound": bound, "rec": rec, "source": "discovered"})
    return entries


# --------------------------------------------------------------------------
# Learned state
# --------------------------------------------------------------------------

def load_state(path: Path | None = None) -> dict:
    path = path or STATE_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"seen_tasks": [], "bindings": {}}


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------
# The audit itself
# --------------------------------------------------------------------------

def audit(grace: float = 1.0, check_tasks: bool = True,
          registry=None, table_text: str | None = None,
          state: dict | None = None,
          chain_jobs: dict[str, list[str]] | None = None) -> list[dict]:
    """Rows, one per loop. `registry` given => that exact list (tests use this);
    omitted => discover from the machine and merge with seed + learned bindings.

    `chain_jobs` is the supersession authority; omitted => read from the chain.
    """
    state = state if state is not None else load_state()
    lost = {t.lower() for t in state.get("seen_tasks", [])}
    chain_jobs = chain_job_targets() if chain_jobs is None else chain_jobs

    if registry is not None:
        entries = [{"loop": n, "task": t, "heartbeat": hb, "what": what,
                    "bound": b, "rec": None, "source": "explicit"}
                   for n, hb, b, t, what in registry]
        discovered: list[dict] = []
    else:
        text = table_text
        if text is None and check_tasks:
            text = _query_all_tasks()
        discovered = [r for r in parse_task_table(text or "")
                      if is_project_task(r)]
        entries = merge_entries(discovered, learned=state.get("bindings", {}))
        # Derived per-job chain bindings (discovery mode only; explicit-registry
        # mode is what the tests use). A chain job that stops getting its turn
        # is a non-occurrence -- there is no failure event to log, only an old
        # heartbeat -- so this is the only thing that can see it.
        for _n, _hb, _b, _task, _what in chain_job_bindings():
            entries.append({"loop": _n, "task": _task, "heartbeat": _hb,
                            "what": _what, "bound": _b, "rec": None,
                            "source": "chain-job"})

    by_task = {r["name"].lower(): r for r in discovered}
    rows: list[dict] = []
    for e in entries:
        row = {"loop": e["loop"], "task": e["task"], "heartbeat": e["heartbeat"],
               "what": e["what"], "source": e["source"],
               "declared_bound": e["bound"]}
        bound = (e["bound"] or 0.0) * grace
        if e["rec"] is not None:
            derived = bound_from_schedule(e["rec"].get("schedule_type", ""), grace,
                                          e["rec"].get("repeat_every", ""))
            if derived is not None:
                bound = derived
                row["derived_bound"] = derived
        row["bound"] = bound

        if e["heartbeat"]:
            row["hb_status"], row["hb_evidence"] = heartbeat_status(
                _expand(e["heartbeat"]), bound if bound else float("inf"))
        else:
            row["hb_status"], row["hb_evidence"] = "unbound", "no heartbeat known"

        reason = superseded_by(e.get("rec"), chain_jobs)
        row["superseded"] = bool(reason)
        row["superseded_reason"] = reason or ""
        if check_tasks:
            if e["rec"] is not None:
                row["task_status"], row["task_evidence"] = state_status(e["rec"])
                if (e.get("source") == "seed" and cadence_seconds(
                        e["rec"].get("schedule_type", ""),
                        e["rec"].get("repeat_every", "")) is None):
                    # 2026-09-28: one-shot/logon-fired seed loop (Azazael-Chain
                    # fires once per logon; the live loop then runs for days).
                    # Judging its scheduler last-run against the heartbeat
                    # bound flagged a healthy loop every ~15min. Firing
                    # recency is unjudgeable here; the heartbeat (900s) is the
                    # stall signal and still applies.
                    row["run_status"] = "unknown"
                    row["run_evidence"] = ("one-shot schedule; last-run recency "
                                           "unjudgeable, heartbeat governs")
                else:
                    row["run_status"], row["run_evidence"] = last_run_status(
                        e["rec"], bound or None)
            else:
                row["task_status"], row["task_evidence"] = task_status(e["task"])
            if reason and row["task_status"] != "ok":
                # Retired on purpose: judged, not ignored -- and not failed.
                row["run_status"], row["run_evidence"] = "superseded", reason
        elif reason:
            row["run_status"], row["run_evidence"] = "superseded", reason
        low = e["task"].lower()
        row["lost"] = bool(low in lost and low not in by_task) if check_tasks else False
        rows.append(row)
    return rows


def _query_all_tasks() -> str:
    """One `schtasks` call for every task on the machine.

    The header row is KEPT on purpose: `parse_task_table` locates columns by
    name when it can, which survives a build that reorders or adds columns.
    With `/nh` there is no header and the positional fallback is the only path.
    """
    try:
        proc = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/v"],
                              capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
        return proc.stdout or ""
    except Exception:
        return ""


def refresh_learned_state(table_text: str | None = None,
                          path: Path | None = None) -> dict:
    """Record every project task ever seen, and re-confirm known bindings.

    Persisting the seen-set is what makes the SHRINKAGE check possible: a task
    that vanishes is reported LOST instead of silently leaving scope.
    """
    text = table_text if table_text is not None else _query_all_tasks()
    discovered = [r for r in parse_task_table(text) if is_project_task(r)]
    st = load_state(path)
    seen = {t.lower(): t for t in st.get("seen_tasks", [])}
    for rec in discovered:
        seen[rec["name"].lower()] = rec["name"]
    st["seen_tasks"] = sorted(seen.values())
    bindings = dict(st.get("bindings", {}))
    for name, hb, _b, task, _what in REGISTRY:
        if any(r["name"].lower() == task.lower() for r in discovered):
            bindings[task] = hb
    st["bindings"] = bindings
    save_state(st, path)
    return st


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-tasks", action="store_true",
                    help="skip scheduled-task checks (WSL/Linux)")
    ap.add_argument("--grace", type=float, default=1.0,
                    help="multiply every silence bound (load tolerance)")
    ap.add_argument("--discover", action="store_true",
                    help="show the discovered task table and exit")
    args = ap.parse_args(argv)

    if args.discover:
        text = _query_all_tasks()
        recs = [r for r in parse_task_table(text) if is_project_task(r)]
        print(f"discovered {len(recs)} project task(s) of "
              f"{len(parse_task_table(text))} total")
        for r in recs:
            b = bound_from_schedule(r.get("schedule_type", ""),
                                    repeat_every=r.get("repeat_every", ""))
            print(f"  {r['name']:<28} state={r.get('scheduled_state', ''):<9}"
                  f" last={r.get('last_run', ''):<22}"
                  f" cadence={(r.get('schedule_type') or '').strip()!r}"
                  f" bound={'-' if b is None else f'{b:.0f}s'}")
        return 0

    # Learn first, so bindings and the seen-set are current before judging.
    state = load_state()
    if not args.no_tasks:
        state = refresh_learned_state()

    # An unreadable chain table must be LOUD: with no authority to consult, a
    # retired task is indistinguishable from one that was switched off, and the
    # silently-switched-off case is the one that cost four days.
    chain_jobs = chain_job_targets()
    if not args.no_tasks and not chain_jobs:
        print(f"WARNING: could not read the chain's JOBS table "
              f"({CHAIN_SCRIPT.name}) -- supersession is unjudgeable, so "
              "retired tasks will be reported as disabled.", file=sys.stderr)

    rows = audit(grace=args.grace, check_tasks=not args.no_tasks, state=state,
                 chain_jobs=chain_jobs)
    # Operator-policy retirements are judged here (not in failed() alone):
    # a disabled-by-policy loop must read policy-retired BEFORE the bad
    # computation below, or its STALE run_status fails the run.
    for r in rows:
        if r["loop"] in POLICY_RETIRED and r.get("task_status") != "ok":
            r["run_status"] = "policy-retired"
            r["run_evidence"] = POLICY_RETIRED[r["loop"]]
    bad = [r for r in rows if failed(r)]

    if args.json:
        print(json.dumps({"generated": time.time(), "loops": rows,
                          "failed": [r["loop"] for r in bad]}, indent=1))
    else:
        print("LOOP LIVENESS — discovered from the scheduler, not listed by hand")
        print(f"grace={args.grace:g}  tasks={'off' if args.no_tasks else 'on'}"
              f"  loops={len(rows)}")
        print()
        print(f"  {'loop':<20} {'src':<11} {'hb':<9} {'task':<9} {'run':<8} evidence")
        print("  " + "-" * 92)
        for r in rows:
            ev = (r["hb_evidence"] if r["hb_status"] not in ("ok", "unbound")
                  else r.get("run_evidence", "") or r.get("task_evidence", ""))
            print(f"  {str(r['loop'])[:20]:<20} {r['source']:<11}"
                  f" {r['hb_status']:<9} {r.get('task_status', '-'):<9}"
                  f" {r.get('run_status', '-'):<8} {ev}")
        print()
        retired = [r for r in rows if r.get("superseded") and r.get("task_status") != "ok"]
        if retired:
            print(f"  coverage: {len(retired)} task(s) retired on purpose, judged"
                  " NOT failed (a chain job does the same work):")
            for r in retired:
                print(f"    {r['loop']:<28} -> {r['superseded_reason']}")
        policy = [r for r in rows
                  if r["loop"] in POLICY_RETIRED and r.get("task_status") != "ok"]
        if policy:
            print(f"  coverage: {len(policy)} task(s) off by operator policy,"
                  " judged NOT failed:")
            for r in policy:
                print(f"    {r['loop']:<28} -> {POLICY_RETIRED[r['loop']]}")
        unbound = [r for r in rows
                   if r["hb_status"] == "unbound" and not r.get("superseded")]
        if unbound:
            print(f"  coverage: {len(unbound)} live loop(s) watched by task state"
                  " and last-run only (no heartbeat bound):")
            print(f"    {', '.join(str(r['loop']) for r in unbound)}")
            print("    these can be seen to stop, but not to stall while firing.")
        lost = [r for r in rows if r.get("lost")]
        if lost:
            print(f"  coverage SHRANK: {len(lost)} previously-seen task(s) are gone")
        if bad:
            print()
            print(f"FAIL: {len(bad)} loop(s) not running: "
                  f"{', '.join(str(r['loop']) for r in bad)}")
            for r in bad:
                print(f"  - {r['loop']}: {r['what']}")
                for k in ("hb", "task", "run"):
                    if k == "hb" and r["hb_status"] not in ("ok", "unbound"):
                        print(f"      heartbeat {r['hb_status']}: {r['hb_evidence']}")
                    elif k == "task" and r.get("task_status", "ok") != "ok":
                        print(f"      task {r['task_status']}: {r.get('task_evidence')}")
                    elif k == "run" and r.get("run_status", "ok") not in ("ok", "unknown"):
                        print(f"      last run {r['run_status']}: {r.get('run_evidence')}")
                if r.get("lost"):
                    print("      LOST: was watched before, no longer present")
            print()
            print("  A disabled task is the state \\Azazael-Chain sat in from")
            print("  2026-09-16T01:54 for four days with nothing red.")
            print("  Re-enable:  schtasks /change /tn \"<name>\" /enable")
        else:
            print()
            print("OK: every discovered loop is enabled, recent, and writing.")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())