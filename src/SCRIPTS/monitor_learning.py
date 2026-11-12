#!/usr/bin/env python3
"""monitor_learning.py - Health monitor for the scheduled learning loops.

Read-only: inspects the DirectMemory/LocalMemoryStore SQLite DB for the
signals that tell whether the scheduled cycles are running, adapting,
persisting state, and steering behavior.

Loops monitored:
  DreamCycle      (every 5 min) dream_generated -> policy_applied ->
                                strategy_adaptation -> state_saved ->
                                strategy_steered
  MetacogCycle    (daily 5 AM)  mc_cycle rows fed/adapted + state_saved
  EvolutionCycle (daily 4 AM)   evolution_result with real gene variation
  TrainingOrchestrator (3 AM)   training_run_complete rows

Checks (last N minutes, default 60):
  1. Activity      - strategy_adaptation / dream_generated / policy_applied
  2. Persistence   - a state_saved event is fresh (<= 2 loop cycles)
  3. Adaptation    - strategy switches (should_adapt=true) occur
  4. Policy bridge - policy_applied events carry detail (fixed version)
  5. Steering      - strategy_steered events show the active strategy is
                     reaching the dreamer and changing its parameters
  6. Metacog cycle - mc_cycle rows exist and report state saved
  7. Evolution     - evolution_result exists; flag identical-gene plateaus

DB resolution order: --db PATH, then $AZAZAEL_MEMORY_DIR/memory_store.db,
then ~/.azazael/memory_store.db.

Modes:
  (default)  one-shot check; exit 0 = healthy, 1 = failures, 2 = warnings
  --watch N  poll every --interval seconds until a fully healthy cycle is
             seen (or N cycles elapse). Exit 0 once resolved, else 1.
"""

import argparse
import json
import os
import sqlite3
import sys
import sysconfig
import time
from datetime import datetime, timezone
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

CYCLE_MINUTES = 5  # nominal loop cadence


def _ensure_gil():
    """Free-threaded 3.14t builds have a flaky sqlite3; re-exec with GIL on."""
    if (sysconfig.get_config_var("Py_GIL_DISABLED")
            and os.environ.get("AZAZAEL_GIL_ENFORCED") != "1"):
        os.environ["AZAZAEL_GIL_ENFORCED"] = "1"
        os.execv(sys.executable, [sys.executable, "-X", "gil=1"] + sys.argv)


_ensure_gil()


def _resolve_db(path):
    if path:
        return path
    env_dir = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    if env_dir:
        return os.path.join(env_dir, "memory_store.db")
    return os.path.join(os.path.expanduser("~"), ".azazael", "memory_store.db")


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError as exc:
        note_failure("monitor_learning:L76", exc)
        return None


def _as_dict(data):
    if isinstance(data, dict):
        return data
    try:
        return json.loads(data)
    except (TypeError, json.JSONDecodeError) as exc:
        note_failure("monitor_learning:L85", exc)
        return {}


def evolution_flatness(traj, traj_avg):
    """EV-8 flatness semantics, as a pure function.

    Returns ``(flat, flat_best_only)``. ``flat`` is the WARN condition: it
    requires BOTH the best trajectory and the population mean to be unchanged,
    and a mean series to actually be present. With elitism ``best_fitness`` is
    flat BY CONSTRUCTION until a child beats the elite, so a flat best alone is
    a healthy search, not a stall. ``flat_best_only`` (best flat, mean still
    moving) names that benign case so a reader does not have to infer it.

    Measured 2026-09-28: best=[16.276]*6 while the mean moved 14.42 -> 15.38,
    and the old best-only check cried "no gradient" on a healthy search.

    Pure and public so the semantics can be pinned without a live store.
    """
    def _flat(vals):
        return bool(vals) and len(set(round(float(x), 4)
                                      for x in vals)) == 1

    flat_best = _flat(traj)
    flat_mean = _flat(traj_avg)
    flat = (flat_best and flat_mean) if traj_avg else flat_best
    return flat, (flat_best and not flat_mean)


def run_check(db, minutes):
    """Run one health check. Returns (report, fails, warns)."""
    window_s = minutes * 60
    issues = []  # (level, msg)  level: FAIL/WARN

    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = con.cursor()
    except Exception as e:
        note_failure("monitor_learning:L122", e)
        return {"error": f"cannot open DB {db}: {e}"}, [f"cannot open DB {db}: {e}"], []

    def count_events(event_type, minutes_=None):
        q = (f"SELECT COUNT(*) FROM memories WHERE event_type=? AND timestamp >= "
             f"datetime('now','-{minutes_ or minutes} minutes')")
        return cur.execute(q, (event_type,)).fetchone()[0]

    def latest_event(event_type):
        return cur.execute(
            "SELECT timestamp, data FROM memories WHERE event_type=? "
            "ORDER BY id DESC LIMIT 1", (event_type,)).fetchone()

    report = {
        "db": db,
        "checked_utc": _now_utc().isoformat(),
        "window_minutes": minutes,
        "signals": {},
    }

    # --- 1. Activity ---------------------------------------------------
    n_adapt = count_events("strategy_adaptation")
    n_dream = count_events("dream_generated")
    n_policy = count_events("policy_applied")
    n_state = count_events("state_saved")
    n_steer = count_events("strategy_steered")
    report["signals"]["adaptations_in_window"] = n_adapt
    report["signals"]["dreams_in_window"] = n_dream
    report["signals"]["policy_applied_in_window"] = n_policy
    report["signals"]["state_saved_in_window"] = n_state
    report["signals"]["steering_in_window"] = n_steer

    if n_adapt == 0 and n_dream == 0 and n_policy == 0:
        issues.append(("FAIL", "no learning-loop activity in window (loop down?)"))

    # --- 2. Persistence freshness --------------------------------------
    row = latest_event("state_saved")
    age_min = None
    if row:
        ts = _parse_ts(row[0])
        if ts:
            age_min = round((_now_utc() - ts).total_seconds() / 60, 1)
    report["signals"]["state_saved_age_min"] = age_min
    if age_min is None:
        issues.append(("FAIL", "no state_saved event found (state never saved)"))
    elif age_min > 2 * CYCLE_MINUTES:
        issues.append(("FAIL", f"state_saved stale: {age_min} min old (> {2 * CYCLE_MINUTES})"))
    elif age_min > CYCLE_MINUTES:
        issues.append(("WARN", f"state_saved age {age_min} min is past one cycle"))

    # --- 3. Adaptation actually switching ------------------------------
    switch_24h = cur.execute(
        "SELECT COUNT(*) FROM memories WHERE event_type='strategy_adaptation' "
        "AND json_extract(data,'$.should_adapt') = 1 "
        "AND timestamp >= datetime('now','-24 hours')").fetchone()[0]
    report["signals"]["switches_24h"] = switch_24h

    cur_strategies = {}
    if row:
        cur_strategies = _as_dict(row[1]).get("current_strategies", {}) or {}
    report["signals"]["active_strategies"] = cur_strategies
    non_default = {w: s for w, s in cur_strategies.items() if s != "default"}
    if n_adapt > 0 and switch_24h == 0 and not non_default:
        issues.append(("WARN", "loop adapts but has never switched strategy "
                               "(no recommendations landing yet)"))
    elif switch_24h > 0 or non_default:
        report["signals"]["active_non_default"] = non_default

    # --- 4. Policy bridge detail ---------------------------------------
    prow = latest_event("policy_applied")
    report["signals"]["policy_applied"] = None
    if prow:
        d = _as_dict(prow[1])
        applied = d.get("applied", 0)
        details = d.get("details", {}) or {}
        has_detail = bool(details) and len(details.get("applied", [])) == applied
        report["signals"]["policy_applied"] = {
            "timestamp": prow[0], "applied": applied,
            "skipped": d.get("skipped", 0), "has_detail": has_detail,
        }
        if n_policy > 0 and not has_detail:
            issues.append(("FAIL", "policy_applied rows carry no detail (old bridge code running)"))

    # --- 5. Strategy steering behavior ---------------------------------
    report["signals"]["steering"] = None
    srow = latest_event("strategy_steered")
    if srow:
        d = _as_dict(srow[1])
        report["signals"]["steering"] = {
            "timestamp": srow[0],
            "strategy": d.get("strategy"),
            "params": d.get("params"),
        }
    if (n_adapt > 0 or n_dream > 0) and n_steer == 0 and non_default:
        issues.append(("WARN", "active non-default strategy but no strategy_steered events "
                               "(dreamer not executing via execute())"))
    elif n_steer > 0:
        st = report["signals"]["steering"]
        if st and st.get("strategy") != "default" and st.get("params"):
            report["signals"]["steering_active"] = True

    # --- Dream quality (context) ---------------------------------------
    q = cur.execute(
        "SELECT AVG(CAST(json_extract(data,'$.quality') AS REAL)) FROM memories "
        "WHERE event_type='dream_generated' AND timestamp >= datetime('now', '-60 minutes')").fetchone()[0]
    report["signals"]["avg_dream_quality_1h"] = round(q, 3) if q is not None else None

    # --- 6. Metacog cycle (daily) --------------------------------------
    mc_row = latest_event("mc_cycle")
    report["signals"]["metacog"] = None
    if mc_row:
        d = _as_dict(mc_row[1])
        report["signals"]["metacog"] = {
            "timestamp": mc_row[0],
            "fed": d.get("fed"),
            "worm": d.get("worm"),
            "adapted": bool(d.get("adapted")),
            "recommendation": d.get("recommendation"),
        }
        age_days = (24 * 60 * 60)  # tolerated: daily task
        ts = _parse_ts(mc_row[0])
        if ts and (_now_utc() - ts).total_seconds() > 3 * 24 * 3600:
            issues.append(("WARN", "no mc_cycle in 3 days (MetacogCycle task down?)"))

    # --- 7. Evolution / training (daily) --------------------------------
    ev_row = latest_event("evolution_result")
    report["signals"]["evolution"] = None
    if ev_row:
        d = _as_dict(ev_row[1])
        traj = d.get("trajectory") or []
        traj_avg = d.get("trajectory_avg") or []
        genes = d.get("best_genes") or {}
        # EV-8 semantics live in `evolution_flatness` (pure, unit-tested).
        flat, flat_best_only = evolution_flatness(traj, traj_avg)
        report["signals"]["evolution"] = {
            "timestamp": ev_row[0],
            "seeded_from_prior": bool(d.get("seeded_from_prior")),
            "generations": d.get("generations"),
            "best_genes": genes,
            "flat_trajectory": flat,
            "flat_best_only": flat_best_only,
            "trajectory": traj[-6:],
            "trajectory_avg": traj_avg[-6:],
        }
        if flat and traj:
            issues.append(("WARN", "evolution trajectory flat (best AND mean unchanged "
                                   "across gens - fitness has no gradient)"))
    tr_row = latest_event("training_run_complete")
    if tr_row is None:
        tr_row = latest_event("training_complete")
    report["signals"]["training"] = None
    if tr_row:
        d = _as_dict(tr_row[1])
        report["signals"]["training"] = {
            "timestamp": tr_row[0],
            "avg_reward": d.get("avg_reward"),
            "q_table_size": d.get("q_table_size"),
        }

    con.close()
    return report, [m for lv, m in issues if lv == "FAIL"], [m for lv, m in issues if lv == "WARN"]


def print_report(report, fails, warns):
    print("== Learning-loop monitor ==")
    s = report.get("signals", {})
    if "error" in report:
        print(f"   [{report['error']}]")
        return
    print(f"   db: {report['db']} | window {report['window_minutes']}m")
    print(f"   adaptations: {s.get('adaptations_in_window')} | dreams: {s.get('dreams_in_window')} "
          f"| policy_applied: {s.get('policy_applied_in_window')} "
          f"| state_saved: {s.get('state_saved_in_window')} | steering: {s.get('steering_in_window')}")
    print(f"   state_saved age: {s.get('state_saved_age_min')} min | switches(24h): {s.get('switches_24h')} "
          f"| avg dream quality(1h): {s.get('avg_dream_quality_1h')}")
    print(f"   active strategies: {s.get('active_strategies') or '{}'}")
    pa = s.get("policy_applied")
    if pa:
        print(f"   latest policy_applied: applied={pa['applied']} skipped={pa['skipped']} "
              f"detail={pa['has_detail']} @ {pa['timestamp']}")
    st = s.get("steering")
    if st:
        print(f"   latest steering: strategy={st['strategy']} params={st['params']} @ {st['timestamp']}")
    mc = s.get("metacog")
    if mc:
        print(f"   metacog cycle: fed={mc['fed']} worm={mc['worm']} adapted={mc['adapted']} "
              f"rec={mc['recommendation']} @ {mc['timestamp']}")
    ev = s.get("evolution")
    if ev:
        print(f"   evolution: seeded={ev['seeded_from_prior']} gens={ev['generations']} "
              f"flat={ev['flat_trajectory']} best={json.dumps(ev['best_genes'])[:90]} @ {ev['timestamp']}")
    tr = s.get("training")
    if tr:
        print(f"   training: avg_reward={tr['avg_reward']} q_table={tr['q_table_size']} @ {tr['timestamp']}")
    for m in fails:
        print(f"   [FAIL] {m}")
    for m in warns:
        print(f"   [WARN] {m}")
    if not fails and not warns:
        print("   verdict: [OK] all learning-loop signals healthy")
    else:
        print(f"   verdict: {len(fails)} FAIL, {len(warns)} WARN")


def main():
    ap = argparse.ArgumentParser(description="Learning-loop health monitor")
    ap.add_argument("--db", help="Path to the memory_store.db (default: auto)")
    ap.add_argument("--minutes", type=int, default=60, help="Look-back window")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--watch", type=int, default=0,
                    help="Poll every --interval seconds until healthy or N cycles")
    ap.add_argument("--interval", type=int, default=300,
                    help="Seconds between watch polls (default 300)")
    args = ap.parse_args()

    db = _resolve_db(args.db)

    if args.watch <= 0:
        report, fails, warns = run_check(db, args.minutes)
        if args.json:
            print(json.dumps({"report": report, "fails": fails, "warns": warns}, indent=2))
        else:
            print_report(report, fails, warns)
        return 1 if fails else (2 if warns else 0)

    # --- watch mode: poll until resolved (no FAIL) or cycles exhausted ---
    print(f"[watch] monitoring {db} every {args.interval}s, "
          f"up to {args.watch} cycles (resolve = 0 FAIL)")
    seen_ok = False
    for cycle in range(1, args.watch + 1):
        stamp = datetime.now().strftime("%H:%M:%S")
        report, fails, warns = run_check(db, args.minutes)
        if args.json:
            print(json.dumps({"cycle": cycle, "stamp": stamp,
                              "report": report, "fails": fails, "warns": warns}))
        else:
            print(f"\n--- cycle {cycle}/{args.watch} @ {stamp} ---")
            print_report(report, fails, warns)
        signals = report.get("signals", {}) or {}
        active = signals.get("active_strategies") or {}
        non_default_active = bool(active) and any(
            s != "default" for s in active.values())
        steering_ok = signals.get("steering_in_window", 0) > 0
        # Resolved = no FAILs, and steering confirmed whenever a non-default
        # strategy is active (policy must actually reach the dreamer).
        resolved = not fails and (not non_default_active or steering_ok)
        if resolved:
            print(f"[watch] RESOLVED after {cycle} cycle(s) "
                  f"(steering_ok={steering_ok})")
            seen_ok = True
            break
        sys.stdout.flush()
        if cycle < args.watch:
            time.sleep(args.interval)

    if seen_ok:
        return 0
    print("[watch] NOT resolved within cycle budget")
    return 1


if __name__ == "__main__":
    sys.exit(main())
