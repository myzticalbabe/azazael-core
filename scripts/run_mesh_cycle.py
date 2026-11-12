#!/usr/bin/env python3
"""run_mesh_cycle.py - full-architecture execution cycle for the 1-minute cadence.

Runs EVERY registered category (flat registry, U3) via
ClusterWORMZ with tight per-worm timeouts, retries *errors* once, and
records the outcome to state/mesh_cycle.json + logs/mesh_cycle.log.

A worm that hits the per-worm budget is counted as `deferred`, not retried:
re-running it on the identical budget fails identically. Before this, the
cycle printed `fail=35 retry={'retried': 0}` with nothing in the dead-letter
queue and nothing named in the failure summary -- the timeouts were invisible.

Safety:
  - lock file: skips cleanly if the previous cycle is still running
  - per-worm timeout: 12s base, ADAPTIVE per category (2x the slowest worm that
    category needed last cycle, capped at 60s) — see BUDGET_CEILING_S. Disable
    with --no-adaptive.
  - parallel within each category (8 workers)
  - never raises to the scheduler: infra failures are recorded, exit stays 0
    unless the run itself could not start (exit 2)
  - read-only monitors keep painting from the DB/state while this actuates

Usage:
  python scripts/run_mesh_cycle.py [--timeout 12] [--retries 1]
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
import time as _t

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

LOCK_PATH = os.path.join(_ROOT, "state", "mesh_cycle.lock")
STATE_PATH = os.path.join(_ROOT, "state", "mesh_cycle.json")
BUDGET_PATH = os.path.join(_ROOT, "state", "mesh_budget.json")
LOG_PATH = os.path.join(_ROOT, "logs", "mesh_cycle.log")
LOG_MAX = 1_000_000
LOCK_STALE_S = 300

# Adaptive per-worm budget. A flat 12s is right for most worms but wrong for a
# handful whose cost is instantiation or a DB read (DatabaseMonitorWorm,
# DEPENDZ, DiagnosticWorm, VideoAnalyzerWorm, and the training worms under
# load). Those are then reported as failures every cycle although nothing is
# broken -- on 2026-09-20T03:16 a contended cycle deferred 35 worms. Instead of
# raising the budget for everyone (which lets one hung worm stall its category),
# each CATEGORY remembers what its slowest worm actually needed and budgets 2x
# that, bounded by [--timeout, BUDGET_CEILING_S]. Healthy categories stay at the
# base; only the ones that demonstrably need room grow.
BUDGET_CEILING_S = 60.0

# U3: orchestration infrastructure must not be driven by a generic sweep
# (was ClusterWORMZ._NO_EXECUTE). Matched by worm NAME so a rename is a
# loud miss, not a silent execution. Module-level so tests can pin it.
_NO_EXECUTE = frozenset({
    "ClusterWORMZ", "MetaCluster", "WormFleet",
    "ConcurrencyManager", "ErrorRecoveryOrchestrator", "SpineCycleWorm",
})


def _pid_alive(pid):
    """Best-effort liveness for the lock owner.

    Windows has no harmless signal 0: `os.kill(pid, 0)` takes the documented
    unconditional-TerminateProcess path, so probing a live process that way
    could kill it. Use `tasklist` there instead.
    """
    if not pid:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
                capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace").stdout
            return str(int(pid)) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _log(line):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _load_budgets() -> dict:
    try:
        with open(BUDGET_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_budgets(budgets: dict) -> None:
    try:
        os.makedirs(os.path.dirname(BUDGET_PATH), exist_ok=True)
        with open(BUDGET_PATH, "w", encoding="utf-8") as f:
            json.dump(budgets, f, indent=1, sort_keys=True)
    except Exception:
        pass


def _sample_telemetry():
    """Cycle-level efficiency sample. Runs ONCE per cycle (never per worm:
    per-eval hooks would 1000x the cost). State file, not DB: zero lock
    contention on the hot store. Budget: single-digit milliseconds."""
    t0 = _t.perf_counter()
    s = {}
    try:
        import psutil
        s["cpu"] = round(psutil.cpu_percent(interval=None), 1)
        s["mem"] = round(psutil.virtual_memory().percent, 1)
    except Exception:
        s["cpu"] = None
        try:
            s["load"] = round(os.getloadavg()[0], 2)
        except Exception:
            pass
    try:
        s["db_mb"] = round(os.path.getsize(
            os.path.join(os.path.expanduser("~"), ".azazael",
                         "memory_store.db")) / 1e6, 1)
    except Exception:
        pass
    s["sample_ms"] = round((_t.perf_counter() - t0) * 1000, 2)
    return s


def _log_telemetry(entry):
    try:
        p = os.path.join(_ROOT, "state", "cycle_telemetry.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p) and os.path.getsize(p) > 200000:
            os.replace(p, p + ".1")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="run_mesh_cycle")
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--no-adaptive", action="store_true",
                    help="use a flat --timeout for every category")
    args = ap.parse_args(argv)
    t0 = time.time()

    # --- lock: skip if previous cycle still alive ---
    try:
        os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
        if os.path.exists(LOCK_PATH):
            held = {}
            try:
                with open(LOCK_PATH, encoding="utf-8") as f:
                    held = json.load(f) or {}
            except Exception:
                held = {}
            started = float(held.get("started", 0))
            owner = held.get("pid")
            # A dead owner's lock is stale immediately, however recently it was
            # written; a live owner holds for the full window. Time alone let a
            # long chain fire's mesh be joined by a second one.
            if (time.time() - started < LOCK_STALE_S
                    and (owner is None or _pid_alive(owner))):
                msg = "%s SKIP previous cycle still running" % datetime.now().isoformat()[:19]
                print(msg)
                _log(msg)
                return 0
        with open(LOCK_PATH, "w", encoding="utf-8") as f:
            json.dump({"started": time.time(), "pid": os.getpid()}, f)
    except Exception as e:
        print("LOCK FAIL %s" % e)
        return 2

    outcome = {"at": datetime.now().isoformat()[:19], "natures": {},
               "ok": 0, "fail": 0, "deferred": 0, "errors": [], "retried": {},
               "duration_s": 0.0, "complete": False}
    try:
        # Flat dispatch (U3): categories from the registry, not natures.
        # outcome["natures"] keeps its KEY (chain/state readers consume it)
        # but rows are registry categories — noted here and in the ledger.
        from CORE.worm_registry import (discover_worms, get_all_categories,
                                        get_worms_by_category)
        from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
        from CORE.WORMS.dead_letter_queue import DeadLetterQueue
        discover_worms()
        categories = sorted(get_all_categories())
        outcome["discovered"] = {"natures": len(categories),
                                 "worms": sum(len(get_worms_by_category(c))
                                             for c in categories)}
        budgets = _load_budgets() if not args.no_adaptive else {}
        for category in categories:
            worms = sorted(n for n in get_worms_by_category(category)
                           if n not in _NO_EXECUTE)
            if not worms:
                continue
            cluster = ClusterWORMZ.build_from_registry(worms)
            cluster.set_parallel(True)
            # Budget = 2x what this category's slowest worm needed last time.
            need_s = float((budgets.get(category) or {}).get("need_s", 0) or 0)
            budget = (min(BUDGET_CEILING_S, max(args.timeout, 2.0 * need_s))
                      if not args.no_adaptive else args.timeout)
            cluster.set_timeout(int(round(budget)))
            try:
                cluster.execute_all()
                agg = cluster.aggregate_results()
                results = agg.worm_results or {}
                errs = [str(v.get("error", ""))[:100] for v in results.values()
                        if isinstance(v, dict) and not v.get("success", True)
                        and v.get("error")]
                outcome["natures"][category] = {
                    "worms": len(worms), "ok": agg.success_count,
                    "fail": agg.failure_count,
                    "deferred": 0,
                    "budget_s": budget,
                    "ms": agg.duration_ms,
                    "errors": errs[:5],
                }
                obs = max([float((v or {}).get("duration_ms") or 0) / 1000.0
                           for v in results.values()] + [0.0])
                budgets[category] = {"need_s": round(obs, 2),
                                     "budget_s": float(budget)}
                outcome["ok"] += agg.success_count
                outcome["fail"] += agg.failure_count
                outcome["errors"].extend(
                    "%s:%s" % (category, e[:100]) for e in errs[:5])
            except Exception as e:
                outcome["natures"][category] = {"error": str(e)[:120]}
                outcome["errors"].append("%s:RUNNER %s" % (category, str(e)[:100]))
        _save_budgets(budgets)
        outcome["budget_total_s"] = round(
            sum(float((b or {}).get("budget_s", 0) or 0)
                for b in budgets.values()), 1)

        # one retry pass over the dead-letter queue (flat store)
        try:
            dlq = DeadLetterQueue()
            retry = dlq.retry_all_failed_worms(max_retries=max(0, args.retries))
            outcome["retried"] = {
                "retried": retry.get("total_retried", 0),
                "succeeded": retry.get("total_succeeded", 0),
                "still_failed": retry.get("still_failed", 0),
                "deferred": retry.get("total_deferred", 0),
            }
            outcome["errors"].extend(
                str(e)[:100] for e in retry.get("errors", [])[:10])
        except Exception as e:
            outcome["errors"].append("retry:%s" % str(e)[:100])
        outcome["complete"] = True
    except Exception as e:
        outcome["errors"].append("FATAL:%s" % str(e)[:160])
    finally:
        outcome["duration_s"] = round(time.time() - t0, 1)
        tel = _sample_telemetry()
        tel.update({"at": outcome["at"], "ok": outcome["ok"],
                    "fail": outcome["fail"], "dur_s": outcome["duration_s"]})
        outcome["telemetry"] = tel
        _log_telemetry(tel)
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(outcome, f, indent=1)
        except Exception:
            pass
        try:
            os.remove(LOCK_PATH)
        except Exception:
            pass

    line = "%s cycle natures=%d ok=%d fail=%d deferred=%d retry=%s dur=%.0fs%s" % (
        outcome["at"], len(outcome["natures"]), outcome["ok"], outcome["fail"],
        outcome.get("deferred", 0), outcome.get("retried", {}),
        outcome["duration_s"],
        "" if outcome["complete"] else " INCOMPLETE")
    print(line)
    _log(line)
    for e in outcome["errors"][:15]:
        print("  ERR " + e)
        _log("  ERR " + e)
    return 0


if __name__ == "__main__":
    sys.exit(main())