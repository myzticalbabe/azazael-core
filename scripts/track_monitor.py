# coding: utf-8
"""track_monitor.py — live training monitor, reengineered on track_registry.

WHY THIS EXISTS (2026-10-03): `scripts/live_monitor.py` (archived at
`archive/live-monitors-20261002/`) carried its own rotation truth: a
`TRAIN_ALGOS` tuple (with GRPOTUNE present, ALLPAR missing) plus a
hand-maintained `train_evts` tuple inside `collect()`. Both drifted from
`track_registry.ROTATION`, the declared single authority, and the suite that
pinned the drift (`test_track_registry_consumers.py`) was archived with it.

This module keeps the dashboard contract (`db_connect` / `collect` /
`explain` / `bottom_line`, same shapes `live_monitor_web.py` consumes) but
derives every track list from `track_registry.ROTATION`. A track added to
TRACKS appears in the grid, the freshness roll-up, and the `tracks[]`
section with zero edits here — and `tests/test_track_monitor.py` pins
exactly that property, so this file cannot re-drift the way its
predecessor did.

DROPPED from the predecessor (CLI-era, nothing consumes them): `render()`,
`append_log()`, `aoa_status()`, `run100_status()`, `qd_grid()`,
`mesh_status()`, sparklines. The web page reads freshness/evo/strategy/
activity/training/means/bottom_line; everything below serves exactly that.

Stdlib only (sqlite3+json), like the predecessor.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

import track_registry
from track_registry import ROTATION, TRACKS

DB = os.environ.get("AZAZAEL_MEMORY_DIR", r"C:\Users\clint\.azazael")


def _store_db() -> str:
    """Resolve the LocalMemoryStore sqlite file (mirrors local_memory)."""
    try:
        import sys
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "src"))
        from CORE.local_memory import get_memory_store  # noqa
        import CORE.local_memory as _lm
        p = getattr(_lm, "DB_PATH", "")
        if p:
            return str(p)
    except Exception:
        pass
    cand = os.path.join(DB, "memory.db")
    return cand if os.path.exists(cand) else DB


# ── DB helpers (ported unchanged; they are generic, not rotation logic) ──

def db_connect():
    """Open the DB read-only, falling back to a local copy on lock errors."""
    path = _store_db()
    if os.path.isdir(path):
        for name in ("memory.db", "memory_store.db", "local_memory.db"):
            cand = os.path.join(path, name)
            if os.path.exists(cand):
                path = cand
                break
    for _ in range(3):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
            con.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
            return con
        except Exception:
            time.sleep(1)
    tmp = os.path.join(tempfile.gettempdir(), "tm_livemon.db")
    for _ in range(3):
        try:
            shutil.copy2(path, tmp)
            con = sqlite3.connect("file:%s?mode=ro" % tmp, uri=True)
            con.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
            return con
        except Exception:
            time.sleep(2)
    return None


def parse_data(d):
    try:
        return json.loads(d) if isinstance(d, str) else d
    except Exception:
        return {}


def age_min(ts):
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return round((datetime.now(timezone.utc) - t).total_seconds() / 60.0, 1)
    except Exception:
        return None


def latest(con, event_type, n=1):
    rows = con.execute(
        "SELECT timestamp, data FROM memories WHERE event_type=? ORDER BY id DESC LIMIT ?",
        (event_type, n)).fetchall()
    return [(ts, parse_data(data)) for ts, data in rows]


def count_window(con, event_type, minutes):
    try:
        rows = con.execute(
            "SELECT timestamp FROM memories WHERE event_type=? ORDER BY id DESC LIMIT 500",
            (event_type,)).fetchall()
    except Exception:
        return 0
    n = 0
    for (ts,) in rows:
        a = age_min(ts)
        if a is None:
            continue
        if a <= minutes:
            n += 1
    return n


def age_of_latest(con, event_type):
    """Minutes since the newest event of this type, or None if never seen."""
    rows = con.execute(
        "SELECT timestamp FROM memories WHERE event_type=? ORDER BY id DESC LIMIT 1",
        (event_type,)).fetchone()
    return age_min(rows[0]) if rows else None


def avg_quality(con, minutes):
    try:
        rows = con.execute(
            "SELECT timestamp, data FROM memories WHERE event_type='dream_generated' "
            "ORDER BY id DESC LIMIT 200").fetchall()
    except Exception:
        return 0.0
    vals = []
    for ts, d in rows:
        a = age_min(ts)
        if a is None or a > minutes:
            continue
        try:
            vals.append(float((parse_data(d) or {}).get("quality", 0) or 0))
        except (ValueError, TypeError):
            continue
    return round(sum(vals) / len(vals), 3) if vals else 0.0


# ── rotation-derived event universe (THE anti-drift core) ───────────────

def _track_events() -> list[str]:
    """Every reward event the registry's tracks record, in ROTATION order."""
    evts: list[str] = []
    for name in ROTATION:
        spec = TRACKS.get(name, {})
        event = spec.get("event")
        if event and event[0] not in evts:
            evts.append(event[0])
    for legacy in ("training_run_complete", "training_complete"):
        if legacy not in evts:
            evts.append(legacy)
    return evts


def track_status(con) -> list[dict]:
    """Per-track liveness, one row per ROTATION entry, in rotation order.

    This is the section the predecessor could not have: its track list was
    a hand-maintained tuple that drifted. Here a track added to TRACKS
    appears with no edit to this file.
    """
    rows = []
    for name in ROTATION:
        spec = TRACKS.get(name, {})
        event = (spec.get("event") or [None])[0]
        ts, age, ok, detail = None, None, False, spec.get("kind", "?")
        if event:
            got = latest(con, event, 1)
            if got:
                ts = got[0][0]
                age = age_min(ts)
                ok = age is not None and age < 60
        else:
            # SB3 tracks share sb3_training_orchestrated, matched by algo.
            got = latest(con, "sb3_training_orchestrated", 20)
            for rts, d in got:
                if isinstance(d, dict) and d.get("algo") == name:
                    ts, age = rts, age_min(rts)
                    ok = age is not None and age < 60
                    break
        rows.append({"track": name, "kind": spec.get("kind", "?"),
                     "event": event, "ts": ts, "age": age, "ok": ok,
                     "detail": detail})
    return rows


def evo_history(con, n=20):
    """Recent evolution_result rows, newest first, as dicts (JSON-safe)."""
    out = []
    for ts, d in latest(con, "evolution_result", n):
        traj = [float(x) for x in (d.get("trajectory") or [])]
        traj_avg = [float(x) for x in (d.get("trajectory_avg") or [])]
        pool = d.get("pool_fingerprint") or {}
        if isinstance(pool, str):
            try:
                pool = json.loads(pool)
            except Exception:
                pool = {}
        sprt = d.get("sprt") or {}
        cma = d.get("cma_arm") or {}
        qd = d.get("qd_archive") or {}
        pg = d.get("pareto_gate") or {}
        out.append({
            "ts": ts,
            "traj": traj,
            "traj_avg": traj_avg,
            "best": max(traj) if traj else 0.0,
            "flat": len(set(round(x, 3) for x in traj)) <= 1 if traj else None,
            "avg_flat": (len(set(round(x, 3) for x in traj_avg)) <= 1
                         if traj_avg else None),
            "genes": d.get("best_genes") or {},
            "info": d.get("best_info"),
            "alert": d.get("best_alert"),
            "fit": d.get("fitness", "v1"),
            "baseline": d.get("baseline"),
            "beats": bool(d.get("beats_baseline")),
            "promoted": bool(d.get("promoted")),
            "champ": d.get("champion_reeval"),
            "pool": pool,
            "sprt": {"decision": sprt.get("decision"), "n": sprt.get("n"),
                     "llr": sprt.get("llr")},
            "cma": {"ran": bool(cma.get("ran")), "winner": cma.get("winner"),
                    "arm": cma.get("arm"),
                    "cma_total": cma.get("cma_total"),
                    "ga_total": cma.get("ga_total")},
            "qd": {"filled": qd.get("filled"), "coverage": qd.get("coverage"),
                   "qd_score": qd.get("qd_score")},
            "tpe": d.get("tpe_seeds"),
            "pareto": {"passed": pg.get("passed"), "source": pg.get("source")},
            "holdout": bool((d.get("holdout") or {}).get("passed")),
            "strategy": d.get("strategy"),
        })
    return out


RL_LIVE_SINCE = "2026-09-09"


def sb3_training(con, n=3):
    """Latest training runs per ROTATION track + reward series.

    Same shape as the predecessor's grid, but the algo axis IS the
    rotation: no second list to drift.
    """
    evts = _track_events()
    ph = ",".join("?" for _ in evts)
    rows = con.execute(
        "SELECT timestamp, event_type, source, data FROM memories WHERE event_type IN (%s) "
        "ORDER BY id DESC LIMIT 500" % ph,
        evts).fetchall()
    by_algo: dict = {}
    counts: dict = {}
    for ts, et, src, data in rows:
        d = parse_data(data)
        algo = d.get("algo")
        if algo is None and et == "training_run_complete" and src == "ScheduledTrainingWorm":
            if ts < RL_LIVE_SINCE:
                continue
            algo = "RL"
            d = {"algo": "RL",
                 "run_id": d.get("run_id"),
                 "model_version": d.get("model_version"),
                 "eval_mean_reward": d.get("avg_reward"),
                 "eval_std_reward": None,
                 "records_used": d.get("records"),
                 "total_timesteps": None,
                 "dpo_loss": None}
        if not algo:
            continue
        counts[algo] = counts.get(algo, 0) + 1
        by_algo.setdefault(algo, []).append({
            "time_utc": ts,
            "run_id": d.get("run_id"),
            "model_version": d.get("model_version"),
            "eval_mean_reward": d.get("eval_mean_reward"),
            "eval_std_reward": d.get("eval_std_reward"),
            "records_used": d.get("records_used"),
            "total_timesteps": d.get("total_timesteps"),
            "dpo_loss": d.get("dpo_loss"),
        })
    out = {a: by_algo.get(a, [])[:n] for a in ROTATION}
    out["series"] = {
        a: [{"time_utc": r["time_utc"], "reward": r["eval_mean_reward"]}
            for r in by_algo.get(a, []) if r["eval_mean_reward"] is not None][:40]
        for a in ROTATION
    }
    out["counts"] = counts
    out["next_rotation"] = ROTATION[int(time.time()) // 300 % len(ROTATION)]
    return out


def collect(con):
    """Gather all dashboard data into a dict (also used by the web server)."""
    data: dict = {}
    data["tracks"] = track_status(con)

    data["freshness"] = []
    for et, label in [("evolution_result", "evolution"),
                      ("strategy_steered", "metacog/steer"),
                      ("dream_generated", "dreaming"),
                      ("training_complete", "training")]:
        rows = latest(con, et, 1)
        if et == "training_complete":
            newest_ts, newest_age = None, None
            for tet in _track_events():
                r = latest(con, tet, 1)
                if r:
                    a = age_min(r[0][0])
                    if newest_age is None or (a is not None and a < newest_age):
                        newest_age, newest_ts = a, r[0][0]
            data["freshness"].append({"label": label, "ts": newest_ts,
                                      "age": newest_age,
                                      "ok": newest_age is not None and newest_age < 15})
            continue
        if not rows:
            data["freshness"].append({"label": label, "ts": None,
                                      "age": None, "ok": False})
            continue
        ts, _ = rows[0]
        a = age_min(ts)
        data["freshness"].append({"label": label, "ts": ts, "age": a,
                                  "ok": a is not None and a < 15})

    data["evo"] = evo_history(con, 20)

    rows = latest(con, "strategy_steered", 1)
    if rows:
        ts, d = rows[0]
        data["strategy"] = {"value": d.get("strategy", "?"), "ts": ts}
    else:
        data["strategy"] = {"value": "?", "ts": None}
    data["steer_24h"] = count_window(con, "strategy_steered", 1440)

    data["activity"] = {
        "dreams": count_window(con, "dream_generated", 60),
        "quality": round(avg_quality(con, 60), 3),
        "training": count_window(con, "training_complete", 60),
        "adapt_age": age_of_latest(con, "strategy_adaptation"),
        "apply_age": age_of_latest(con, "policy_applied"),
    }

    data["last_flip"] = None
    flip_rows = latest(con, "strategy_adaptation", 30)
    for ts, d in flip_rows:
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                continue
        if d.get("should_adapt"):
            data["last_flip"] = {
                "ts": ts, "age": age_min(ts),
                "to": d.get("recommended_strategy") or d.get("current_strategy"),
                "reason": (d.get("rationale") or "")[:60],
            }
            break

    data["training"] = sb3_training(con)

    data["meta"] = None
    data["meta_age_s"] = None
    try:
        meta_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "config", "evolved_meta_params.json")
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                data["meta"] = json.load(f)
            data["meta_age_s"] = int(time.time() - os.path.getmtime(meta_path))
    except Exception:
        data["meta"] = None
        data["meta_age_s"] = None

    data["gosp"] = {"learner": None, "parity": None, "rani": None}
    try:
        _home = os.path.expanduser("~")
        _lp = os.path.join(_home, "GOSP_LEARNER_TRAIN_evidence.json")
        if os.path.exists(_lp):
            with open(_lp, encoding="utf-8") as f:
                _ld = json.load(f)
            _aft = _ld.get("learner_state_after") or {}
            data["gosp"]["learner"] = {
                "docs": _ld.get("docs_fed"),
                "date": (_ld.get("date") or "")[:16],
                "iter": _aft.get("iterations"),
                "conv": _aft.get("converged"),
                "acc": _aft.get("current_accuracy"),
            }
    except Exception:
        pass
    return data


STRATEGY_MEANING = {
    "aggressive": "high creativity + more episodes; evidence favors it (best policy-apply rate)",
    "conservative": "low creativity (0.15); tepid dreams mostly fail the 0.5 adoption bar (churn fix)",
    "batched": "dreams are queued and integrated in batches for steadier learning",
    "staged": "changes roll out in stages instead of all at once",
    "adaptive": "parameters adjust to recent outcome evidence",
    "aggressive+": "aggressive with extra exploration headroom",
    "steady": "holding current settings while evidence accumulates",
}


def explain(data):
    """Return list of (verdict, detail) explanation lines about the data."""
    out = []

    stale = [f for f in data["freshness"] if not f["ok"]]
    if not stale:
        out.append(("OK", "All %d loops are firing on their "
                          "5-min cadence - training is live."
                    % len(data["freshness"])))
    else:
        names = ", ".join(f["label"] for f in stale)
        out.append(("WARN", "%s stale (>15 min) - check Windows Task Scheduler; "
                            "the loops can silently stop when trigger windows expire."
                    % names))
    dead_tracks = [t["track"] for t in data.get("tracks", []) if not t["ok"]]
    if dead_tracks:
        out.append(("WARN", "tracks with no fresh run: %s."
                    % ", ".join(dead_tracks)))

    evo = data["evo"]
    if len(evo) >= 3 and len({e["fit"] for e in evo[:4]}) == 1:
        recent = evo[0]["best"]
        prev3 = [e["best"] for e in evo[1:4]]
        mid = sum(prev3) / len(prev3)
        if recent > mid * 1.03:
            trend = "climbing (recent %.2f vs avg %.2f of prior 3)" % (recent, mid)
        elif recent < mid * 0.97:
            trend = ("dip (recent %.2f vs avg %.2f of prior 3 - normal noise, "
                     "re-seeds each run)" % (recent, mid))
        else:
            trend = "steady around %.2f" % mid
        out.append(("EVO", "best fitness %s." % trend))

    if evo and evo[0]["flat"] is False:
        out.append(("EVO", "trajectory is non-flat - reward is action-dependent, so evolution has a "
                           "real gradient to climb (the reward fix is working)."))
    elif evo and evo[0]["flat"] is True:
        if evo[0].get("avg_flat") is False:
            out.append(("EVO", "best flat but mean moved - converged to a plateau at a strong "
                               "seeded champion; search is running, not stuck."))
        elif evo[0].get("avg_flat") is True:
            out.append(("EVO", "best AND mean flat - objective has no usable gradient on this pool."))
    if len(evo) >= 3:
        _n = len(evo)
        _beats = sum(1 for e2 in evo if e2.get("beats"))
        _sprt = sum(1 for e2 in evo if (e2.get("sprt") or {}).get("decision") == "promote")
        _par = sum(1 for e2 in evo if (e2.get("pareto") or {}).get("passed") is True)
        _hold = sum(1 for e2 in evo if e2.get("holdout"))
        _prom = sum(1 for e2 in evo if e2.get("promoted"))
        out.append(("EVO", "funnel over %d: beats %d -> sprt %d -> "
                           "pareto %d -> holdout %d -> promoted %d."
                    % (_n, _beats, _sprt, _par, _hold, _prom)))
        _died_par = sum(1 for e2 in evo
                        if (e2.get("sprt") or {}).get("decision") == "promote"
                        and not e2.get("promoted")
                        and (e2.get("pareto") or {}).get("passed") is not True)
        _died_hold = sum(1 for e2 in evo
                         if (e2.get("sprt") or {}).get("decision") == "promote"
                         and not e2.get("promoted")
                         and (e2.get("pareto") or {}).get("passed") is True
                         and not e2.get("holdout"))
        if _died_par >= 3:
            out.append(("WARN", "funnel blocked at PARETO gate (%d/%d SPRT-promotes died) - "
                                "alert floor may be unsatisfiable; review PARETO_ALERT_TOL_PCT."
                        % (_died_par, _n)))
        elif _died_hold >= 3:
            out.append(("WARN", "funnel blocked at HOLDOUT gate (%d/%d SPRT-promotes died) - "
                                "wins not surviving fresh pools; check pool drift / overfitting."
                        % (_died_hold, _n)))
    if evo:
        eps = evo[0]["genes"].get("epsilon", -1)
        if eps is not None and eps < 0.02:
            out.append(("EVO", "epsilon=%.3f - near-pure exploitation: the policy has converged to "
                               "deterministic actions; random exploration now mostly hurts reward." % eps))
        elif eps is not None and eps < 0.12:
            out.append(("EVO", "epsilon=%.3f - mostly exploiting, light exploration remaining." % eps))
        elif eps is not None:
            out.append(("EVO", "epsilon=%.3f - still exploring widely; search is sampling new genes." % eps))

    s = data["strategy"]
    if s["value"] != "?":
        meaning = STRATEGY_MEANING.get(s["value"].lower(),
                                       "no canned description - check metacog output")
        out.append(("STEER", "strategy '%s' -> %s." % (s["value"], meaning)))

    a = data["activity"]
    if a["quality"] >= 0.6:
        out.append(("ACT", "dream quality %.2f - healthy; dreams are being adopted." % a["quality"]))
    elif a["quality"] >= 0.45:
        out.append(("ACT", "dream quality %.2f - mediocre; many dreams may miss the 0.5 bar." % a["quality"]))
    else:
        out.append(("ACT", "dream quality %.2f - weak; check creativity factors." % a["quality"]))

    tr = data.get("training", {})
    scored = {a: r["eval_mean_reward"] for a in ROTATION if a != "RL"
              for r in (tr.get(a) or [])[:1] if r.get("eval_mean_reward") is not None}
    if len(scored) >= 2:
        best = max(scored, key=scored.get)
        others = [(a, v) for a, v in sorted(scored.items()) if a != best]
        out.append(("EVO", "Training lead: %s (reward %.1f) vs "
                           % (best, scored[best])
                           + ", ".join("%s %.1f" % (a, v) for a, v in others)))
    return out


def bottom_line(data):
    """One-sentence overall verdict."""
    evo = data["evo"]
    s = data["strategy"]
    a = data["activity"]
    stale = [f for f in data["freshness"] if not f["ok"]]
    parts = []
    if stale:
        parts.append("%d loop(s) stale" % len(stale))
    if evo:
        parts.append("evolution best %.2f"
                     % evo[0]["best"]
                     + (" (climbing)" if len(evo) >= 2 and evo[0]["best"] > sum(e["best"] for e in evo[1:4]) / max(1, len(evo[1:4])) else ""))
    if s["value"] != "?":
        parts.append("strategy %s" % s["value"])
    if a["dreams"]:
        parts.append("%d dreams/hr @ %.2f quality" % (a["dreams"], a["quality"]))
    tr = data.get("training", {})
    scored = {a: r["eval_mean_reward"] for a in ROTATION if a != "RL"
              for r in (tr.get(a) or [])[:1] if r.get("eval_mean_reward") is not None}
    if scored:
        best = max(scored, key=scored.get)
        parts.append("training lead %s (%.1f)" % (best, scored[best]))
    if not parts:
        return "No data yet - waiting for the first cycle to fire."
    all_fresh = data["freshness"]
    if stale and len(stale) == len(all_fresh):
        lead = "Training is stalled (every cycle stale)"
    elif any(not f["ok"] and f["label"].lower() in ("training", "evolution")
             for f in all_fresh):
        lead = "Training is stalled"
    elif stale:
        lead = "Training is live but partially stale"
    else:
        lead = "Training is live"
    return "%s: " % lead + "; ".join(parts) + "."
