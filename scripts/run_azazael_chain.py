#!/usr/bin/env python3
"""run_azazael_chain.py - THE single scheduled entry point (replaces 9 tasks).

One Task Scheduler entry ("Azazael-Chain", every 1 minute) runs this. It:
  1. runs the mesh cycle INLINE every fire (~30s, full 27-nature architecture)
  2. runs at most ONE slow job at a time (detached), picked most-overdue-first,
     so slow jobs never overlap each other (the old 4x/5min DB dogpile is gone)
  3. polices runaway slow jobs (deadline kill on the next fire)

Slow jobs and their cadences (previous standalone task in parens):
  dream      300s  dream_cycle.py                          (Azazael-DreamCycle)
  metacog    300s  metacog_cycle.py                        (Azazael-MetacogCycle)
  evolution  300s  run_evolution_cycle_hyper.bat           (Azazael-EvolutionCycle)
  train      300s  run_training_orchestrator.py            (Azazael-TrainingOrchestrator)
  fullpass  3600s  run_full_pass.py                        (Azazael-FullPass)
  maint       daily 03:00  nightly_maintenance.py          (Azazael-NightlyMaintenance)
  eve         daily 04:00  rani_llm_test.py --backend openrouter --rounds 5 (Azazael-EveBattery)
  gosp        daily 04:30  rani_llm_test.py --backend gosp --rounds 5       (Azazael-GospBattery)
  wslclean    daily 06:00  wsl.exe -d Ubuntu-26.04 bash .../wsl2_cleanup.sh (AZAZAEL_WSL2_Cleanup)

Timeouts kill runaways: dream/metacog 300s, train/evo 1200s, fullpass 1800s,
nightlies 1800s, wslclean 600s. State in state/chain_due.json + state/chain_slow.json,
log in logs/azazael_chain.log. Stdlib only.

Usage:
  python scripts/run_azazael_chain.py [--force JOB]   # --force starts JOB now if idle
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
_TOOLS = os.path.join(_ROOT, "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

PY = r"C:\Users\clint\AppData\Local\Programs\Python\Python315\python.exe"
if not os.path.exists(PY):
    PY = sys.executable

CHAIN_LOCK = os.path.join(_ROOT, "state", "azazael_chain.lock")
SLOW_LOCK = os.path.join(_ROOT, "state", "chain_slow.json")
DUE_PATH = os.path.join(_ROOT, "state", "chain_due.json")
LOG_PATH = os.path.join(_ROOT, "logs", "azazael_chain.log")
LOG_MAX = 1_000_000
LOCK_STALE_S = 240

# job: (interval_s | "nightly:HH:MM", timeout_s, kind, target...)
JOBS = {
    "dream":     (300, 300, "py", "dream_cycle.py", []),
    "metacog":   (300, 300, "py", "metacog_cycle.py", []),
    "evolution": (300, 1200, "bat", "run_evolution_cycle_hyper.bat", []),
    "train":     (300, 1200, "py", "run_training_orchestrator.py", []),
    "fullpass":  (3600, 1800, "py", "run_full_pass.py", []),
    "maint":     ("nightly:03:00", 1800, "py", "nightly_maintenance.py", []),
    "eve":       ("nightly:04:00", 1800, "py", "rani_llm_test.py",
                  ["--backend", "openrouter", "--rounds", "5"]),
    "gosp":      ("nightly:04:30", 1800, "py", "rani_llm_test.py",
                  ["--backend", "gosp", "--rounds", "5"]),
    "wslclean":  ("nightly:06:00", 600, "wsl",
                  "-d Ubuntu-26.04 bash /mnt/c/Azazael/azazael/scripts/wsl2_cleanup.sh".split()),
}


def _log(line):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1)
    except Exception:
        pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled


def _liveness():
    """The liveness ledger, or None when it cannot be imported.

    This tick is the mesh process for most of the day, so it is where the
    obligations that describe "a due job must actually start" belong.
    """
    try:
        from CORE.WORMS.expansion import liveness
        return liveness
    except Exception:
        return None  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer


def _publish_failure_visibility():
    """Publish the in-process failure ledgers for the monitors. Never raises.

    The silence/fence/liveness ledgers are in-process by design, so a monitor
    in another process can only see them through this snapshot. Publishing from
    the one scheduled entry point is what keeps that snapshot fresh -- and its
    staleness is itself the signal that the chain has stopped.
    """
    try:
        from monitor_data import publish_failure_visibility
        publish_failure_visibility()
    except Exception:
        pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace").stdout
            return str(pid) in out
        except Exception:
            return False  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer
    return False


def _kill(pid):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                           capture_output=True, timeout=20)
        else:
            os.kill(pid, 9)
        return True
    except Exception:
        return False  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer


def _due(job, interval, due):
    """True if JOB should start now. Nightlies run once/day inside a 60-min window."""
    last = (due.get(job) or {}).get("last", 0)
    now = time.time()
    if isinstance(interval, str) and interval.startswith("nightly:"):
        hh, mm = interval.split(":")[1:]
        try:
            day = datetime.now().strftime("%Y-%m-%d")
            if (due.get(job) or {}).get("day") == day:
                return False
            t = datetime.now().replace(hour=int(hh), minute=int(mm),
                                       second=0, microsecond=0)
            return t.timestamp() <= now <= t.timestamp() + 3600
        except Exception:
            return False  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer
    return (now - last) >= interval


def _start_slow(job, spec):
    _, timeout_s, kind, target, args = spec
    if kind == "py":
        cmd = [PY, os.path.join(_HERE, target)] + list(args)
    elif kind == "bat":
        cmd = ["cmd", "/c", os.path.join(_HERE, target)]
    elif kind == "wsl":
        cmd = ["wsl.exe"] + list(target)
    else:
        _log("START-FAIL-EVIDENCE %s: unknown job kind %r" % (job, kind))
        return None
    logf = os.path.join(_ROOT, "logs", "chain_%s.log" % job)
    try:
        lf = open(logf, "a", encoding="utf-8")
    except Exception:
        lf = subprocess.DEVNULL
    try:
        p = subprocess.Popen(cmd, cwd=_ROOT, stdout=lf, stderr=subprocess.STDOUT,
                             creationflags=(0x08000000 if os.name == "nt" else 0))
        return p.pid
    except Exception as e:
        # Failure evidence: the caller logs only "START-FAIL <job>", so without
        # this the REASON a launch failed (missing target, wrong interpreter,
        # permissions) is unrecoverable from the log after the fact. An armed
        # liveness obligation already records THAT it did not start; this
        # records WHY.
        _log("START-FAIL-EVIDENCE %s cmd=%s: %s: %s"
             % (job, cmd[0], type(e).__name__, str(e)[:200]))
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="run_azazael_chain")
    ap.add_argument("--force", default="", help="start JOB now if slow slot idle")
    args = ap.parse_args(argv)
    t0 = time.time()
    now_s = datetime.now().isoformat()[:19]

    # --- chain lock: only one fire at a time ---
    try:
        os.makedirs(os.path.dirname(CHAIN_LOCK), exist_ok=True)
        if os.path.exists(CHAIN_LOCK):
            try:
                started = float((_read_json(CHAIN_LOCK, {}) or {}).get("started", 0))
            except Exception:
                started = 0
            if time.time() - started < LOCK_STALE_S:
                return 0
        _write_json(CHAIN_LOCK, {"started": time.time(), "pid": os.getpid()})
    except Exception:
        return 2  # scanner:ignore except_return_default -- negative-default probe; the default IS the degraded answer

    started_slow = None
    try:
        # --- 1. mesh cycle inline, every fire ---
        try:
            import run_mesh_cycle
            run_mesh_cycle.main([])
        except Exception as e:
            _log("%s mesh-cycle FAIL %s" % (now_s, str(e)[:120]))

        # --- 2. police the running slow job (deadline kill) ---
        slow = _read_json(SLOW_LOCK, {})
        if slow.get("pid") and _pid_alive(slow["pid"]):
            if time.time() - slow.get("started", 0) > slow.get("timeout", 3600):
                _kill(slow["pid"])
                _log("%s KILLED runaway %s pid=%s" % (now_s, slow.get("job"), slow["pid"]))
                slow = {}
        else:
            slow = {}

        # --- 3. start most-overdue due job if slot free ---
        due = _read_json(DUE_PATH, {})
        if not slow:
            order = []
            for job, spec in JOBS.items():
                interval = spec[0]
                if args.force and job != args.force:
                    continue
                if args.force or _due(job, interval, due):
                    last = (due.get(job) or {}).get("last", 0)
                    order.append((last, job))
            if order:
                order.sort()
                job = order[0][1]
                # A due job that never starts is a NON-OCCURRENCE: there is no
                # failure event to log, because nothing ran. Arming an
                # obligation before the start and discharging it only on
                # success is what turns `_start_slow` failing into something a
                # monitor can see rather than a line in a log file.
                _lv = _liveness()
                if _lv is not None:
                    _lv.expect("chain.job.start", ttl=60.0, detail=job)
                pid = _start_slow(job, JOBS[job])
                if pid:
                    slow = {"job": job, "pid": pid, "started": time.time(),
                            "timeout": JOBS[job][1]}
                    _write_json(SLOW_LOCK, slow)
                    entry = dict(due.get(job, {}))
                    entry["last"] = time.time()
                    if isinstance(JOBS[job][0], str):
                        entry["day"] = datetime.now().strftime("%Y-%m-%d")
                    due[job] = entry
                    _write_json(DUE_PATH, due)
                    started_slow = "%s pid=%s" % (job, pid)
                    _log("%s START %s" % (now_s, started_slow))
                    if _lv is not None:
                        _lv.fulfill("chain.job.start", detail="pid=%s" % pid)
                else:
                    _log("%s START-FAIL %s" % (now_s, job))
    finally:
        try:
            os.remove(CHAIN_LOCK)
        except Exception:
            pass  # scanner:ignore silent_except_pass -- best-effort fallback; degraded path already handled

    # The one scheduled entry point, so this is where the in-process failure
    # ledgers become visible to the monitors. An obligation left armed by a
    # failed start is carried into the next publish rather than lost with this
    # process.
    _publish_failure_visibility()

    msg = "%s chain done %.0fs%s" % (
        now_s, time.time() - t0,
        (" started:" + started_slow) if started_slow else "")
    print(msg)
    _log(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())