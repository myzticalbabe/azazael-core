#!/usr/bin/env python3
"""live_monitor.py — persistent live dashboard for the Azazael learning loop.

Reads the hot Windows DB (~/.azazael/memory_store.db, see DB default) and
refreshes a dashboard every REFRESH seconds, forever. Built to run inside a
console window that stays open (see open_live_monitor.bat).

Shows, live, with plain-English interpretation (detail in the log file):
  - LOOPS: cycle freshness in one line (red when stale >15 min)
  - EVO: latest best + promotion standing; GENES + climb sparkline
  - META: meta-evolution champion search-params, margin EMA, live candidate
  - STEER: active strategy + dream/adapt activity, width-adaptive
  - TRAIN: fixed compact grid of all 15 tracks (reward + trend arrow)
  - MESH: fleet cycle health (state/mesh_cycle.json) + cpu/mem/db trend
  - Verdict: one line; STALE/HARDPOOL alerts only when firing

Fixed frame, truncate-never-wrap: the layout is bounded by construction
at any window size (small windows show the same sections narrower).
Detail (trajectories, fingerprints, explanations) lives in the rolling
log file, not on screen.

Every refresh is also appended to a rolling log file (logs/live_monitor.log,
rotated at 1 MB) so you can review the day's history later.

Stdlib only — runs on Windows Python 3.14 without any deps.

Usage:
  python scripts/live_monitor.py [--refresh 30] [--log logs/live_monitor.log]
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
import re
import shutil as _sh

DB = os.environ.get("AZAZAEL_MEMORY_DIR", r"C:\Users\clint\.azazael")
if not DB.endswith(".db"):
    DB = os.path.join(DB, "memory_store.db")

REFRESH = 30
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "live_monitor.log")
LOG_MAX = 1_000_000  # rotate above ~1 MB
_BEST_SEEN = None    # highest best fitness ever observed this session

# ANSI colors (Windows 10+ consoles support these)
C_RED = "\x1b[91m"
C_GRN = "\x1b[92m"
C_YEL = "\x1b[93m"
C_CYN = "\x1b[96m"
C_BLD = "\x1b[1m"
C_ORG = "\x1b[38;5;202m"   # red-orange GOSP title
C_RST = "\x1b[0m"

# rainbow wheel (256-color hue rotation) for the twinkling GOSP info line
_RAINBOW = [196, 202, 208, 214, 220, 190, 154, 118, 82, 46,
            49, 51, 45, 39, 33, 27, 93, 129, 165, 201]
_FRAME_NO = [0]   # increments per render -> colors drift = twinkle


def _rainbow(text, width, step=3):
    """Per-character rainbow gradient that shifts every frame (twinkle).

    Truncates to width FIRST (visible chars), then colorizes, so the
    frame math stays exact and lines never wrap.
    """
    plain = re.sub("\x1b\\[[0-9;]*m", "", text)[:max(0, width)]
    frame = _FRAME_NO[0]
    out = []
    for i, ch in enumerate(plain):
        out.append(f"\x1b[38;5;{_RAINBOW[(i + frame * step) % len(_RAINBOW)]}m{ch}")
    out.append(C_RST)
    return "".join(out)

# sparkline glyphs: 0..8 levels, pure ASCII so cmd codepage never mangles
_SPARK = " .:-=+*#%"

# what each steering strategy actually does (plain English)
STRATEGY_MEANING = {
    "aggressive": "high creativity + more episodes; evidence favors it (best policy-apply rate)",
    "conservative": "low creativity (0.15); tepid dreams mostly fail the 0.5 adoption bar (churn fix)",
    "batched": "dreams are queued and integrated in batches for steadier learning",
    "staged": "changes roll out in stages instead of all at once",
    "adaptive": "parameters adjust to recent outcome evidence",
    "aggressive+": "aggressive with extra exploration headroom",
    "steady": "holding current settings while evidence accumulates",
}


def db_connect():
    """Open the DB read-only, falling back to a local copy on lock errors."""
    for _ in range(3):
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            con.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
            return con
        except Exception:
            time.sleep(1)
    tmp = os.path.join(tempfile.gettempdir(), "ms_livemon.db")
    for _ in range(3):
        try:
            shutil.copy2(DB, tmp)
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
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
            # naive timestamps are local wall-clock (some writers omit the
            # offset); assuming UTC would silently shift ages by hours.
            t = t.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return round((datetime.now(timezone.utc) - t).total_seconds() / 60.0, 1)
    except Exception:
        return None


def latest(con, event_type, n=1):
    rows = con.execute(
        "SELECT timestamp, data FROM memories WHERE event_type=? ORDER BY id DESC LIMIT ?",
        (event_type, n)).fetchall()
    out = []
    for ts, data in rows:
        out.append((ts, parse_data(data)))
    return out


def count_window(con, event_type, minutes):
    """Count events in the last N minutes, robust to mixed timestamp formats.

    The store holds both offset (+00:00) and naive-local timestamps, so a
    SQL string comparison against one cutoff silently drops rows. Filter
    by parsed age in Python instead (rows come newest-first; small scan).
    """
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


def sparkline(vals, width=52):
    """ASCII sparkline of per-run best fitness (oldest -> newest).

    Outlier-robust: scale uses the 10th..90th percentile range and clamps
    so a single legacy off-scale run (e.g. 50.0 pre-reward-fix) does not
    flatten the whole strip.
    """
    if not vals:
        return "(no runs yet)"
    s = sorted(vals)
    n = len(s)
    lo = s[max(0, int(n * 0.1) - 1)]
    hi = s[min(n - 1, int(n * 0.9))]
    rng = (hi - lo) or 1.0
    cells = []
    step = max(1, int(n / width) + (1 if n % width else 0))
    for i in range(0, n, step):
        chunk = vals[i:i + step]
        v = max(chunk)
        level = int((v - lo) / rng * (len(_SPARK) - 1))
        cells.append(_SPARK[min(max(level, 0), len(_SPARK) - 1)])
    return "".join(cells)


def evo_history(con, n=20):
    """Recent evolution_result rows, newest first, as dicts (JSON-safe)."""
    out = []
    for ts, d in latest(con, "evolution_result", n):
        traj = [float(x) for x in (d.get("trajectory") or [])]
        traj_avg = [float(x) for x in (d.get("trajectory_avg") or [])]
        # promotion standing + pool drift (2026-09-08): surfaced from the
        # artifact so the wallboard shows whether the GA beats production
        # and whether the eval pool is moving (fresh hard alerts).
        pool = d.get("pool_fingerprint") or {}
        if isinstance(pool, str):
            try:
                pool = json.loads(pool)
            except Exception:
                pool = {}
        # v2.0 search secondaries (2026-09-11): CMA arm / QD archive / TPE
        # seeds / SPRT state. Pre-v2.0 rows lack these keys -> safe defaults.
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
            # EV-8: best is flat BY CONSTRUCTION under elitism; the mean
            # distinguishes plateau-at-champion from true no-gradient stall.
            "avg_flat": (len(set(round(x, 3) for x in traj_avg)) <= 1
                         if traj_avg else None),
            "genes": d.get("best_genes") or {},
            # v2-dual (2026-09-08): combined best splits into info/alert
            # parts; older rows lack these keys (v1 scale, info-only).
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
            # 'arm' is which secondary actually ran (cma|de, alternating by
            # 5-min slot in _second_arm_name). It was dropped here, so the
            # wallboard could not tell a DE duel from a CMA one (2026-09-11).
            "cma": {"ran": bool(cma.get("ran")), "winner": cma.get("winner"),
                    "arm": cma.get("arm"),
                    "cma_total": cma.get("cma_total"), "ga_total": cma.get("ga_total")},
            "qd": {"filled": qd.get("filled"), "coverage": qd.get("coverage"),
                   "qd_score": qd.get("qd_score")},
            "tpe": d.get("tpe_seeds"),
            "pareto": {"passed": pg.get("passed"), "source": pg.get("source")},
            "holdout": bool((d.get("holdout") or {}).get("passed")),
            "strategy": d.get("strategy"),
        })
    return out


AOA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "src", "AOA")
_AOA_IMPORT = {}  # cached adapter import check (once per process)


def _days_since_epoch(epoch):
    """Age in days of a POSIX timestamp, or None if it is not one."""
    try:
        return max(0.0, (time.time() - float(epoch)) / 86400.0)
    except (TypeError, ValueError):
        return None


def _days_since_iso(iso):
    """Age in days of an ISO date/timestamp, or None if it will not parse.

    Used instead of file mtime for artifacts that a producer rewrites: the
    mtime moves whenever the file is touched, so it answers "when was this
    written to" rather than "how old is what is in it".
    """
    if not iso:
        return None
    try:
        import datetime as _dt
        return _days_since_epoch(_dt.datetime.fromisoformat(str(iso)).timestamp())
    except Exception:  # noqa: BLE001
        return None


def aoa_status():
    """Armorie-of-Anubis umbrella orchestrator: adapters + milestone + bench.

    File reads only per refresh; the arms import is attempted once and
    cached (adapter imports must stay side-effect-light for this to hold).
    Returns dict with ok count, unexported files, milestone/bench ages.
    """
    out = {"adapters": None, "unexported": [], "ms_ver": None,
           "ms_n": None, "ms_age_d": None, "bench_age_d": None,
           "eve_models": None,
           # Split out of the old single "adapters" number. `dir(arms)` only
           # sees what arms/__init__ re-exports, which is NOT the same thing as
           # "how many arms exist": KaliArm is defined in kali_arm.py and is
           # registered and running (the AOA board lists it, tools 39/39), yet
           # it is absent from dir(arms). Counting only the re-exports made the
           # board report a working arm as "unexported" and disagree with the
           # AOA monitor's own arms count.
           "arm_classes": None, "reexported": None, "not_reexported": []}
    try:
        import ast as _ast
        import glob as _glob
        # What each arm file actually DEFINES, read statically. This is
        # side-effect-free and does not depend on the package __init__ deciding
        # to re-export anything, so it answers the question the line claims to
        # answer: how many arm classes exist on disk.
        arm_files = sorted(_glob.glob(os.path.join(AOA_DIR, "arms", "*.py")))
        defined = {}
        for _p in arm_files:
            _stem = os.path.basename(_p)[:-3]
            # `support_arms.py` is NOT skippable: it holds four registered arms
            # (PamsArm, PerformanceMonitorArm, LlamaServerArm, AgentSelectorArm).
            # Skipping it made this line report "5 arm classes defined" while
            # the AOA board — reading the same registry — listed 9 arms, so the
            # two panels disagreed about the same subsystem.
            if _stem == "__init__":
                continue
            try:
                with open(_p, encoding="utf-8") as _fh:
                    _tree = _ast.parse(_fh.read())
                defined[_stem] = [n.name for n in _tree.body
                                  if isinstance(n, _ast.ClassDef)
                                  and n.name.endswith("Arm")]
            except (OSError, SyntaxError):
                defined[_stem] = []
        out["arm_classes"] = sum(len(v) for v in defined.values())
        # Genuinely nothing to export == a real finding. An arm that exists but
        # is simply not re-exported by __init__ is a different, milder fact.
        out["unexported"] = sorted(k for k, v in defined.items() if not v)

        # Cache is keyed on the directory it was resolved against, and a
        # failed import is retried rather than latched. `if not _AOA_IMPORT`
        # cached whatever the FIRST call happened to see — including a
        # resolution against a different AOA_DIR, or a transient ImportError —
        # and then reported that number for the rest of the process, which is
        # a stale claim wearing a live label.
        if _AOA_IMPORT.get("dir") != AOA_DIR or _AOA_IMPORT.get("error"):
            _AOA_IMPORT.clear()
            try:
                sys.path.insert(0, os.path.join(AOA_DIR))
                # A previously imported `arms` from another root would answer
                # for the wrong tree, so drop it when the path does not match.
                _mod = sys.modules.get("arms")
                if _mod is not None:
                    _mp = os.path.normcase(os.path.abspath(
                        os.path.dirname(getattr(_mod, "__file__", "") or "")))
                    _want = os.path.normcase(os.path.abspath(
                        os.path.join(AOA_DIR, "arms")))
                    if _mp != _want:
                        sys.modules.pop("arms", None)
                import arms as _arms
                _AOA_IMPORT["names"] = [n for n in dir(_arms) if n.endswith("Arm")]
            except Exception as e:
                _AOA_IMPORT["error"] = str(e)[:80]
            _AOA_IMPORT["dir"] = AOA_DIR
        names = _AOA_IMPORT.get("names") or []
        out["adapters"] = len(names)
        # A failed `import arms` used to be recorded in _AOA_IMPORT["error"]
        # and then never read, so the board rendered a count of *zero* names as
        # "0 adapters import-ok", or worse kept reporting the pre-failure count.
        # Carry the error so the line can say what actually happened.
        out["import_error"] = _AOA_IMPORT.get("error")
        out["arms_n"] = len(names) or None
        out["reexported"] = len(names)
        _exposed = {n.lower() for n in names}
        out["not_reexported"] = sorted(
            k for k, v in defined.items()
            if v and not any(c.lower() in _exposed for c in v))
        # Age these by their own recorded dates, NOT by file mtime. Both files
        # now have a producer that rewrites them (refresh_aoa_data.py), so
        # mtime is the refresh time while the history and the baseline inside
        # them stay old — mtime reported "0d ago" for 76-day-old milestones.
        try:
            _mp = os.path.join(AOA_DIR, "AOA_MILESTONE.json")
            with open(_mp, encoding="utf-8") as f:
                ms = json.load(f)
            out["ms_ver"] = ms.get("version")
            sess = ms.get("session") or {}
            out["ms_n"] = sess.get("milestone_count")
            out["ms_mtime_d"] = _days_since_epoch(os.path.getmtime(_mp))
            _mts = [m.get("timestamp") for m in (sess.get("milestones") or [])
                    if m.get("timestamp")]
            out["ms_age_d"] = (_days_since_iso(max(_mts)) if _mts
                               else out["ms_mtime_d"])
        except Exception:
            pass
        try:
            _bp = os.path.join(AOA_DIR, "benchmark_data.json")
            with open(_bp, encoding="utf-8") as f:
                bd = json.load(f)
            # The baseline's own date beats the file's touch time.
            _bdate = ((bd.get("_meta") or {}).get("baseline_date")
                      or (bd.get("eve") or {}).get("benchmark_date"))
            out["bench_age_d"] = (_days_since_iso(_bdate) if _bdate
                                  else _days_since_epoch(os.path.getmtime(_bp)))
            out["bench_baseline_date"] = _bdate
            out["eve_models"] = (bd.get("eve") or {}).get("discovery_total_models")
        except Exception:
            pass
    except Exception:
        pass
    return out


RUN100_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "logs", "run100_progress.log")


def run100_status():
    """Background 100-run progress: (done, target) or None when no run.

    Encoding-robust: powershell Out-File defaults to UTF-16, so older
    progress logs may be UTF-16 while newer appends are UTF-8.
    """
    try:
        if not os.path.exists(RUN100_LOG):
            return None
        with open(RUN100_LOG, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8")
            if "\x00" in text:
                raise ValueError("utf16 content")
        except Exception:
            try:
                text = raw.decode("utf-16")
            except Exception:
                text = raw.decode("utf-8", errors="replace")
        done, target = 0, 100
        for line in text.splitlines():
            line = line.strip().replace("\x00", "")
            if line.startswith("run "):
                try:
                    done = max(done, int(line.split()[1].split("/")[0]))
                    target = int(line.split()[1].split("/")[1])
                except Exception:
                    pass
            elif line.startswith("END"):
                return (done, target)
        return (done, target) if done else None
    except Exception:
        return None


def qd_grid():
    """4x4 QD fitness heatmap rows (high alert at top) or None.

    Read from state/qd_archive.json (keys 'i,j', fitness per cell).
    Chars scale with fitness across filled cells; empty cells are spaces.
    """
    try:
        _p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "state", "qd_archive.json")
        with open(_p, encoding="utf-8") as f:
            data = json.load(f)
        cells = data.get("cells", {}) if isinstance(data, dict) else {}
        dims = data.get("dims") or []
        try:
            _bins = int(dims[0][1]) if dims else 4
        except Exception:
            _bins = 4
        _bins = max(2, min(_bins, 8))
        fits = [float(c.get("fitness", 0.0)) for c in cells.values()
                if isinstance(c, dict)]
        if not fits:
            return None
        lo, hi = min(fits), max(fits)
        rng = (hi - lo) or 1.0
        _scale = " .:-=+*#%@"
        # single filled cell has no contrast range — show it mid-scale
        # instead of blank (blank must mean empty).
        _flat = hi <= lo
        rows = []
        for _a in range(_bins - 1, -1, -1):
            row = ""
            for _i in range(_bins):
                c = cells.get(f"{_i},{_a}")
                if isinstance(c, dict):
                    if _flat:
                        lvl = len(_scale) // 2
                    else:
                        try:
                            lvl = int((float(c.get("fitness", lo)) - lo) / rng
                                      * (len(_scale) - 1))
                        except Exception:
                            lvl = 0
                    row += _scale[min(max(lvl, 0), len(_scale) - 1)]
                else:
                    row += " "
            rows.append(f"[{row}]")
        return rows
    except Exception:
        return None


# Training rotation — must match run_training_orchestrator.py's ROTATION
# (order matters: next_rotation uses the same slot arithmetic).
TRAIN_ALGOS = ("PPO", "DQN", "A2C", "SAC", "TD3", "DDPG", "QRDQN",
               "TRPO", "TQC", "ARS", "MaskablePPO", "GRPO", "DPO", "OFFLINE", "RL",
               "DREAMTUNE", "WORMGA", "GRPOTUNE")
TRAIN_EVENT_TYPES = ("sb3_training_orchestrated",
                     "grpo_training_orchestrated",
                     "dpo_training_orchestrated",
                     "offline_training_orchestrated",
                     "training_run_complete",
                     "dreamtune_training_orchestrated",
                     "wormga_training_orchestrated")
# training_run_complete rows from before the RL track was wired (2026-09-09)
# used a degenerate reward scale (25.0/50.0) — excluded from the wallboard.
RL_LIVE_SINCE = "2026-09-09"
# promotion-spam era (followup #11): evolution_result rows before the
# 2026-09-09 holdout gate promoted every ~5 min with no fresh-pool check,
# so pre-2026-09-09 promotion streaks/wins read hotter than the same
# margins would today. Compare margins, not win counts, across that line.
PROMOTION_GATE_SINCE = "2026-09-09"


def sb3_training(con, n=3):
    """Latest training runs per algo + reward series (dashboard + web).

    Covers all rotation tracks (SB3 PPO/DQN/A2C/SAC/TD3/DDPG/QRDQN/TRPO/TQC/
    ARS/MaskablePPO, GRPO worm-selection, DPO preference learning, OFFLINE
    holdout, RL scheduled pipeline). Same shape
    for both viewers: {algo: latest runs,
    series: reward history per algo, counts, next_rotation}. Rewards are None
    for runs recorded before the reward-tracking fix (2026-09-08). GRPO/DPO
    rewards are on a different scale than SB3 episode totals — the renderer
    notes this.
    """
    ph = ",".join("?" for _ in TRAIN_EVENT_TYPES)
    rows = con.execute(
        "SELECT timestamp, event_type, source, data FROM memories WHERE event_type IN (%s) "
        "ORDER BY id DESC LIMIT 500" % ph,
        TRAIN_EVENT_TYPES).fetchall()
    by_algo = {}
    counts = {}
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
    out = {a: by_algo.get(a, [])[:n] for a in TRAIN_ALGOS}
    out["series"] = {
        a: [{"time_utc": r["time_utc"], "reward": r["eval_mean_reward"]}
            for r in by_algo.get(a, []) if r["eval_mean_reward"] is not None][:40]
        for a in TRAIN_ALGOS
    }
    out["counts"] = counts
    out["next_rotation"] = TRAIN_ALGOS[int(time.time()) // 300 % len(TRAIN_ALGOS)]
    return out


MESH_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "state", "mesh_cycle.json")
MESH_TELEMETRY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "state", "cycle_telemetry.jsonl")


def mesh_status():
    """Latest fleet mesh cycle + cpu/mem/db telemetry tail, aged.

    This board had no mesh awareness at all, so a stalled or failing fleet
    cycle was invisible here while the HTML monitors showed it. File reads
    only (this module stays stdlib-only), and every field is optional: a
    missing or partial artifact renders as `--`, never as a plausible
    invented number.

    `mesh_cycle.json` holds one entry per nature (worms/ok/fail/ms) and a
    telemetry block; the jsonl is one sample per cycle, which is what the
    cpu trend strip is drawn from. The two are read independently so a dead
    summary writer does not hide the history and vice versa.
    """
    out = {"at": None, "age": None, "ok": None, "fail": None,
           "natures": None, "failed": [], "slowest": [], "complete": None,
           "retried": None, "discovered": None, "dur_s": None,
           "cpu": None, "mem": None, "db_mb": None, "cpu_trend": [],
           "samples": 0}
    try:
        with open(MESH_STATE, encoding="utf-8") as f:
            d = json.load(f)
        out["at"] = d.get("at")
        out["ok"] = d.get("ok")
        out["fail"] = d.get("fail")
        out["complete"] = bool(d.get("complete"))
        out["dur_s"] = d.get("duration_s")
        out["retried"] = (d.get("retried") or {}).get("retried")
        out["discovered"] = (d.get("discovered") or {}).get("worms")
        # `natures: {}` and "no natures key at all" are different facts: only
        # report a count when the artifact actually carries the mapping, or a
        # truncated write reads as "zero natures", which looks like a finding.
        _raw_nats = d.get("natures")
        nats = ({n: v for n, v in _raw_nats.items() if isinstance(v, dict)}
                if isinstance(_raw_nats, dict) else {})
        if isinstance(_raw_nats, dict):
            out["natures"] = len(nats)
        out["failed"] = sorted(n for n, v in nats.items() if (v.get("fail") or 0))
        out["slowest"] = [n for n, _ in sorted(
            ((n, v.get("ms") or 0.0) for n, v in nats.items()),
            key=lambda kv: -kv[1])[:3]]
        tel = d.get("telemetry") or {}
        out["cpu"], out["mem"] = tel.get("cpu"), tel.get("mem")
        out["db_mb"] = tel.get("db_mb")
        out["age"] = age_min(out["at"])
    except Exception:
        pass
    try:
        with open(MESH_TELEMETRY, encoding="utf-8") as f:
            tail = f.readlines()[-40:]
        for line in tail:
            try:
                s = json.loads(line)
            except Exception:
                continue
            out["samples"] += 1
            c = s.get("cpu")
            if isinstance(c, (int, float)):
                out["cpu_trend"].append(float(c))
            if out["at"] is None and s.get("at"):
                out["at"] = s.get("at")
                out["age"] = age_min(s["at"])
        if out["cpu"] is None and out["cpu_trend"]:
            out["cpu"] = out["cpu_trend"][-1]
        if out["db_mb"] is None and out["samples"]:
            out["db_mb"] = s.get("db_mb") if isinstance(s, dict) else None
    except Exception:
        pass
    return out


def collect(con):
    """Gather all dashboard data into a dict (also used by the web server)."""
    data = {}

    data["freshness"] = []
    # training freshness: watch the newest of EVERY training event type.
    # (2026-09-09 fix: the original 3-type list missed GRPO/DPO/OFFLINE/
    # DREAMTUNE/WORMGA rows, so training read stale for hours while those
    # tracks ran constantly.)
    train_evts = ("training_complete", "sb3_training_orchestrated",
                  "training_run_complete", "grpo_training_orchestrated",
                  "dpo_training_orchestrated", "offline_training_orchestrated",
                  "dreamtune_training_orchestrated",
                  "wormga_training_orchestrated")
    for et, label in [("evolution_result", "evolution"), ("strategy_steered", "metacog/steer"),
                      ("dream_generated", "dreaming"), ("training_complete", "training")]:
        rows = latest(con, et, 1)
        if et == "training_complete":
            newest_ts, newest_age = None, None
            for tet in train_evts:
                r = latest(con, tet, 1)
                if r:
                    a = age_min(r[0][0])
                    if newest_age is None or (a is not None and a < newest_age):
                        newest_age, newest_ts = a, r[0][0]
            data["freshness"].append({"label": label, "ts": newest_ts, "age": newest_age,
                                      "ok": newest_age is not None and newest_age < 15})
            continue
        if not rows:
            data["freshness"].append({"label": label, "ts": None, "age": None, "ok": False})
            continue
        ts, _ = rows[0]
        a = age_min(ts)
        data["freshness"].append({"label": label, "ts": ts, "age": a, "ok": a is not None and a < 15})

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
        # Rolling 60-min counts of an every-5-min event sit pinned at ~12 and
        # look frozen. Surface the age of the newest event instead so liveness
        # is visible; counts stay in the log.
        "adapt_age": age_of_latest(con, "strategy_adaptation"),
        "apply_age": age_of_latest(con, "policy_applied"),
    }

    # Last time the active strategy actually CHANGED (should_adapt rows), not
    # merely when an adaptation was evaluated. This is what answers "has it
    # adapted recently?"
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

    # meta-evolution state (file-based, cheap; None until the first
    # meta-scoring cycle writes config/evolved_meta_params.json).
    # meta_age_s guards against a dead copy: a frozen file with no age
    # looks identical to a live one, so the render flags STALE past 15 min
    # (3 missed 5-min cycles).
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

    # GOSP learner evidence (written by the WSL gosp_* scripts on each
    # run; absent until the first run — all fields optional).
    data["gosp"] = {"learner": None, "parity": None, "rani": None}
    try:
        _home = os.path.expanduser("~")
        _lp = os.path.join(_home, "GOSP_LEARNER_TRAIN_evidence.json")
        if os.path.exists(_lp):
            with open(_lp, encoding="utf-8") as f:
                _ld = json.load(f)
            _wb, _wa = _ld.get("native_intel_weights_before") or {}, _ld.get("native_intel_weights_after") or {}
            _mover, _mdelta = "?", 0.0
            for _k, _v0 in _wb.items():
                try:
                    _dd = abs(float(_wa.get(_k, _v0)) - float(_v0))
                except (ValueError, TypeError):
                    continue
                if _dd > abs(_mdelta):
                    _mdelta, _mover = (_wa.get(_k, _v0) - _v0), _k
            _aft = _ld.get("learner_state_after") or {}
            data["gosp"]["learner"] = {
                "docs": _ld.get("docs_fed"), "date": (_ld.get("date") or "")[:16],
                "iter": _aft.get("iterations"), "conv": _aft.get("converged"),
                "acc": _aft.get("current_accuracy"),
                "mover": _mover, "mfrom": round(float(_wb.get(_mover, 0)), 3) if _mover in _wb else None,
                "mto": round(float(_wa.get(_mover, 0)), 3) if _mover in _wa else None,
            }
        _pp = os.path.join(_home, "GOSP_PARITY_evidence.json")
        if os.path.exists(_pp):
            with open(_pp, encoding="utf-8") as f:
                _pd = json.load(f)
            _agree, _total = 0, 0
            _ag = _pd.get("agreement") or {}
            if isinstance(_ag, dict):
                for _q, _r in _ag.items():
                    if not isinstance(_r, dict):
                        continue
                    _total += 1
                    if not _r.get("rust_only") and not _r.get("python_only"):
                        _agree += 1
            data["gosp"]["parity"] = {"agree": _agree, "total": _total,
                                      "date": (_pd.get("date") or "")[:16]}
        _rp = os.path.join(_home, "GOSP_RANI_INTEGRATION_evidence.json")
        if os.path.exists(_rp):
            with open(_rp, encoding="utf-8") as f:
                _rd = json.load(f)
            data["gosp"]["rani"] = {"mode": _rd.get("mode"),
                                    "date": (_rd.get("date") or "")[:16]}
        # RANI batteries (Eve Unleashed AdversarialEngine): newest of the
        # GOSP-integration and live-LLM evidence files. Backend identity
        # is the point — every round is stamped with what answered it.
        _best, _best_ts = None, ""
        for _rf in ("GOSP_RANI_INTEGRATION_evidence.json",
                    "RANI_LLM_evidence.json"):
            _fp = os.path.join(_home, _rf)
            try:
                if not os.path.exists(_fp):
                    continue
                with open(_fp, encoding="utf-8") as f:
                    _ed = json.load(f)
            except Exception:
                continue
            _dt = str(_ed.get("date") or "")
            if _dt >= _best_ts:
                _best_ts, _best = _dt, _ed
        if _best:
            _sm = _best.get("engine_summary") or {}
            _probes = _best.get("probes") or []
            if not _sm and _probes and isinstance(_probes[0], dict):
                _sm = _probes[0].get("engine_summary") or {}
            _nr = sum(len(_p.get("rounds") or []) for _p in _probes) or len(_best.get("rounds") or [])
            _adv = _sm.get("adversarial_detected")
            if _adv is None:
                _adv = sum(1 for _p in _probes for _r in (_p.get("rounds") or [])
                           if _r.get("adversarial_flag"))
                _adv += sum(1 for _r in (_best.get("rounds") or []) if _r.get("adversarial_flag"))
            data["gosp"]["rani_batt"] = {
                "backend": _best.get("backend") or _best.get("model") or "?",
                "rounds": _nr or _sm.get("total_rounds"),
                "adv": _adv,
                "score": _sm.get("avg_score"),
                "lat": _sm.get("avg_latency_ms"),
                "tok": _best.get("max_tokens"),
                "date": _best_ts[:16],
            }
        # HYDRAMETA_UV trials (same output-base resolution as the tool
        # itself: env -> Desktop/METAWORMS -> project output/METAWORMS).
        try:
            _ub = (os.environ.get("AZAZAEL_OUTPUT_PATH")
                   or (os.path.join(_home, "Desktop", "METAWORMS")
                       if os.path.exists(os.path.join(_home, "Desktop", "METAWORMS"))
                       else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "output", "METAWORMS")))
            _ut = os.path.join(_ub, "hydrometa_uv", "trials.json")
            if os.path.exists(_ut):
                with open(_ut, encoding="utf-8") as f:
                    _td = json.load(f)
                if isinstance(_td, list) and _td:
                    _be = max((t.get("efficiency_score", 0) or 0 for t in _td
                               if isinstance(t, dict)), default=0.0)
                    data["gosp"]["uv"] = {
                        "n": len(_td),
                        "best": round(float(_be), 1),
                        "age": round((time.time() - os.path.getmtime(_ut)) / 3600.0, 1),
                    }
        except Exception:
            pass
    except Exception:
        pass

    # recent training runs across all tracks (newest first, for TRAIN2)
    data["recent_runs"] = []
    try:
        _ph = ",".join("?" for _ in TRAIN_EVENT_TYPES)
        _seen = set()
        for _ts, _et, _src, _d in con.execute(
                "SELECT timestamp, event_type, source, data FROM memories "
                f"WHERE event_type IN ({_ph}) ORDER BY id DESC LIMIT 12",
                TRAIN_EVENT_TYPES).fetchall():
            _d = parse_data(_d)
            _rid = _d.get("run_id")
            if _rid is not None:
                if _rid in _seen:
                    continue
                _seen.add(_rid)
            _algo = _d.get("algo")
            if _algo is None and _et == "training_run_complete" and _src == "ScheduledTrainingWorm":
                _algo = "RL"
            if not _algo:
                continue
            _rw = _d.get("eval_mean_reward", _d.get("avg_reward"))
            data["recent_runs"].append({"algo": _algo, "reward": _rw,
                                        "age": age_min(_ts),
                                        "ver": _d.get("model_version")})
            if len(data["recent_runs"]) >= 4:
                break
    except Exception:
        pass

    # promotion file standing (evolved hyperparams actually in production)
    # G-1: state/ is canonical since EV-1 (2026-09-10); config/ is a
    # read-only legacy fallback. Reading config/ first displayed the
    # frozen Sep-7 genome as production.
    data["promo"] = None
    try:
        _pj = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "state", "evolved_rl_params.json")
        if not os.path.exists(_pj):
            _pj = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "config", "evolved_rl_params.json")
        if os.path.exists(_pj):
            with open(_pj, encoding="utf-8") as f:
                _pd = json.load(f)
            _gg = _pd.get("genes") or {}
            _mt = os.path.getmtime(_pj)
            data["promo"] = {
                "lr": round(float(_gg.get("learning_rate", 0)), 4),
                "eps": round(float(_gg.get("epsilon", 0)), 4),
                "disc": round(float(_gg.get("discount", 0)), 4),
                "fit": _pd.get("fitness"),
                "wins": _pd.get("wins"),
                "age": round((time.time() - _mt) / 60.0, 1),
            }
    except Exception:
        pass

    # store size / rows / hourly growth (python-side age filter: the
    # store mixes offset and naive-local timestamps, so SQL string
    # comparison against one cutoff silently drops rows)
    data["store"] = None
    try:
        _dbrow = con.execute("SELECT COUNT(*) FROM memories").fetchone()
        _n = _dbrow[0] if _dbrow else 0
        _h = 0
        try:
            for (_ts,) in con.execute(
                    "SELECT timestamp FROM memories ORDER BY id DESC LIMIT 3000").fetchall():
                _a = age_min(_ts)
                if _a is None:
                    continue
                if _a <= 60:
                    _h += 1
        except Exception:
            pass
        try:
            _mb = round(os.path.getsize(DB) / 1e6, 1)
        except OSError:
            _mb = None
        data["store"] = {"mb": _mb, "rows": _n, "per_hr": _h}
    except Exception:
        pass

    # inference servers (best-effort, short timeout so refresh never hangs)
    data["servers"] = []
    try:
        import urllib.request as _url
        for _port in (18090, 18091, 18080):
            _ok, _ms = False, None
            try:
                _t0 = time.time()
                _r = _url.urlopen(f"http://127.0.0.1:{_port}/health", timeout=1)
                _ok = _r.status == 200
                _ms = int((time.time() - _t0) * 1000)
            except Exception:
                pass
            data["servers"].append({"port": _port, "ok": _ok, "ms": _ms})
    except Exception:
        pass

    # api key status per provider (names only — values never leave disk).
    # Fail-soft: without keys the RANI API path is down but nothing else cares.
    data["keys"] = []
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "src"))
        from CORE.inference.providers import get_provider
        for _pn in ("groq", "cerebras", "openrouter", "gemini", "mistral",
                    "nvidia_nim", "siliconflow", "cohere", "github",
                    "huggingface", "zhipu", "cloudflare"):
            try:
                _ok = bool(get_provider(_pn).has_valid_key())
            except Exception:
                _ok = False
            data["keys"].append({"name": _pn, "ok": _ok})
    except Exception:
        pass

    # last retention trim (from the maintenance log — warns if the
    # nightly caps stop keeping up with training exhaust).
    data["caps"] = None
    try:
        _ml = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "logs", "maintenance.log")
        if os.path.exists(_ml):
            with open(_ml, encoding="utf-8", errors="replace") as f:
                _tail = f.read()[-6000:]
            import re as _re
            _hits = _re.findall(r"'total':\s*(\d+)", _tail)
            if _hits:
                data["caps"] = {"last_trim": int(_hits[-1])}
    except Exception:
        pass

    # audit + mutation summaries (repo health, written by repo_audit.py
    # and mutate.py — file reads only, absent until first run)
    data["audit"] = None
    data["mutants"] = None
    try:
        _ap = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "state", "audit_summary.json")
        if os.path.exists(_ap):
            with open(_ap, encoding="utf-8") as f:
                _ad = json.load(f)
            _c = _ad.get("compile") or {}
            _p = _ad.get("patterns") or {}
            data["audit"] = {
                "at": _ad.get("at"),
                "files": _ad.get("files_scanned"),
                "compile_bad": len(_c.get("bad") or []),
                "bare": (_p.get("bare_except") or {}).get("count"),
                "uncovered": (_ad.get("testmap") or {}).get("uncovered_count"),
            }
        _mp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "state", "mutation_summary.json")
        if os.path.exists(_mp):
            with open(_mp, encoding="utf-8") as f:
                _md = json.load(f)
            data["mutants"] = {
                "target": os.path.basename(str(_md.get("target", "?"))),
                "score": _md.get("score"),
                "killed": _md.get("killed"),
                "mutants": _md.get("mutants"),
                "at": _md.get("at"),
                "survived": _md.get("survived") or [],
            }
    except Exception:
        pass

    # dream tuning file (what creativity the last dream cycle actually used)
    data["tuned"] = None
    try:
        _tp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "config", "tuned_creativity.json")
        if os.path.exists(_tp):
            with open(_tp, encoding="utf-8") as f:
                _td = json.load(f)
            _mt = os.path.getmtime(_tp)
            data["tuned"] = {
                "cf": _td.get("creativity_factor"),
                "sc": _td.get("scenario_count"),
                "age": round((time.time() - _mt) / 3600.0, 1),
            }
    except Exception:
        pass

    # last policy bridge verdict (what dreams actually did)
    data["policy"] = None
    try:
        _prows = con.execute(
            "SELECT timestamp, data FROM memories WHERE event_type='policy_applied' "
            "ORDER BY id DESC LIMIT 1").fetchall()
        if _prows:
            _pts, _pdd = _prows[0]
            _pdd = parse_data(_pdd)
            _det = _pdd.get("details") or {}
            _ap = _det.get("applied") or []
            _sk = _det.get("skipped") or []
            _last = _ap[-1] if _ap else {}
            data["policy"] = {
                "age": age_min(_pts),
                "napplied": len(_ap), "nskipped": len(_sk),
                "worm": _last.get("worm"), "rec": _last.get("recommended"),
                "conf": _last.get("confidence"),
                "adapted": _last.get("should_adapt"),
            }
    except Exception:
        pass

    # nightly error triage (written by run_maintenance; aged so a stale
    # file can't pose as fresh — same dead-copy guard as META).
    data["triage"] = None
    try:
        _trp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "state", "error_triage.json")
        if os.path.exists(_trp):
            with open(_trp, encoding="utf-8") as f:
                _tr = json.load(f)
            _tcl = _tr.get("clusters") or []
            data["triage"] = {
                "clusters": _tr.get("cluster_count", len(_tcl)),
                "errors": _tr.get("total_errors_exact", 0),
                "top": _tcl[0] if _tcl else None,
                "age": round((time.time() - os.path.getmtime(_trp)) / 3600.0, 1),
            }
    except Exception:
        data["triage"] = None

    # chain-of-thought corpus (thought_process rows: the local loop's
    # reasoning supervision — count, success contrast, freshness).
    data["thoughts"] = None
    try:
        _trows = con.execute(
            "SELECT timestamp FROM memories WHERE event_type='thought_process' "
            "ORDER BY id DESC LIMIT 1000").fetchall()
        if _trows:
            _srow = con.execute(
                "SELECT COUNT(*) FROM memories WHERE event_type='thought_process' "
                "AND json_extract(data, '$.success') = 1").fetchone()
            _sok = _srow[0] if _srow else 0
            data["thoughts"] = {
                "n": len(_trows),
                "ok": _sok,
                "age": age_min(_trows[0][0]),
            }
    except Exception:
        data["thoughts"] = None

    # llm benchmark scoreboard (latest row per model)
    data["llm"] = []
    try:
        _seen_models = set()
        for _ts, _dd in con.execute(
                "SELECT timestamp, data FROM memories WHERE event_type='llm_benchmark' "
                "ORDER BY id DESC LIMIT 20").fetchall():
            _dd = parse_data(_dd)
            _m = _dd.get("model") or "?"
            if _m in _seen_models:
                continue
            _seen_models.add(_m)
            data["llm"].append({
                "model": _m, "age": age_min(_ts),
                "prompt": _dd.get("prompt_tps"), "gen": _dd.get("gen_tps"),
                "think": _dd.get("think_chars"),
            })
            if len(data["llm"]) >= 5:
                break
    except Exception:
        pass

    # alert rate last hour (crit split for the human)
    data["alerts"] = {"per_hr": 0, "crit": 0}
    try:
        for _ts, _sv in con.execute(
                "SELECT timestamp, severity FROM memories WHERE event_type='db_health_alert' "
                "ORDER BY id DESC LIMIT 500").fetchall():
            _a = age_min(_ts)
            if _a is None:
                continue
            if _a > 60:
                break
            data["alerts"]["per_hr"] += 1
            if _sv == "critical":
                data["alerts"]["crit"] += 1
    except Exception:
        pass

    # latest DB-monitor verdict (the DatabaseMonitorWorm snapshot the
    # alert pool and metacog evidence both key off). Surface the flag so
    # the wallboard shows whether the DB is within its (config-aligned)
    # size/error gates — and age it like META so a dead monitor can't
    # pose as a healthy one.
    data["dbmon"] = None
    try:
        _drows = con.execute(
            "SELECT timestamp, data FROM memories WHERE event_type='db_health_snapshot' "
            "ORDER BY id DESC LIMIT 1").fetchall()
        if _drows:
            _dts, _ddd = _drows[0]
            _ddd = parse_data(_ddd)
            data["dbmon"] = {
                "age": age_min(_dts),
                "healthy": bool(_ddd.get("healthy")),
                "size": _ddd.get("db_size_mb"),
                "err7d": _ddd.get("error_count_7d"),
                "warn7d": _ddd.get("warning_count_7d"),
                "grow24h": _ddd.get("memory_growth_24h"),
                "vac": bool(_ddd.get("auto_vacuum_running")),
                "rows": _ddd.get("total_memories"),
            }
    except Exception:
        data["dbmon"] = None

    # latest tuner outputs (best params each specialist found)
    data["tuners"] = {}
    try:
        for _et, _tk in (("dreamtune_training_orchestrated", "dreamtune"),
                         ("wormga_training_orchestrated", "wormga")):
            _rows = con.execute(
                "SELECT timestamp, data FROM memories WHERE event_type=? "
                "ORDER BY id DESC LIMIT 1", (_et,)).fetchall()
            if not _rows:
                continue
            _ts, _dd = _rows[0]
            _dd = parse_data(_dd)
            data["tuners"][_tk] = {
                "age": age_min(_ts),
                "fit": _dd.get("eval_mean_reward"),
                "ver": _dd.get("model_version"),
                "params": _dd.get("best_params") or {},
                "board": _dd.get("leaderboard") or {},
            }
    except Exception:
        pass

    # cycle process liveness from their own log files (complements event
    # freshness: proves the scheduler tasks themselves are firing).
    # dbmon is special: the daemon only logs at startup/errors, so its
    # liveness comes from its snapshot events, not its log file.
    data["cycles"] = []
    try:
        _logdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "logs")
        for _fn, _lb in (("evolution_cycle.log", "evo"),
                         ("training_orchestrator.log", "train"),
                         ("dream_cycle.log", "dream"),
                         ("metacog_cycle.log", "meta"),
                         ("maintenance.log", "maint"),
                         ("db_monitor.log", "dbmon")):
            _fp = os.path.join(_logdir, _fn)
            try:
                _age = round((time.time() - os.path.getmtime(_fp)) / 60.0, 1)
            except OSError:
                _age = None
            data["cycles"].append({"label": _lb, "age": _age})
        try:
            _snap = con.execute(
                "SELECT timestamp FROM memories WHERE event_type='db_health_snapshot' "
                "ORDER BY id DESC LIMIT 1").fetchone()
            if _snap:
                for _c in data["cycles"]:
                    if _c.get("label") == "dbmon":
                        _c["age"] = age_min(_snap[0])
        except Exception:
            pass
    except Exception:
        pass

    # technique-link adoption (memory unity, Phase 1): hook invocation counts
    # straight from the canonical store. Empty when hooks never fire (an idle
    # loop reads as empty, never as healthy).
    data["links"] = None
    try:
        _rows = con.execute(
            "SELECT event_type, COUNT(*), "
            "SUM(CASE WHEN json_extract(data, '$.ok') = 1 THEN 1 ELSE 0 END), "
            "MAX(timestamp) FROM memories "
            "WHERE source='technique-link' GROUP BY event_type").fetchall()
        data["links"] = {
            "by_hook": {r[0]: {"n": r[1], "ok": r[2] or 0} for r in _rows},
            "latest": max((r[3] for r in _rows), default=None),
        }
    except Exception as exc:
        print("links block failed: %s" % type(exc).__name__, file=sys.stderr)

    # reward-ledger adoption (canonical co-tabulation intake): per-scale
    # counts and means straight from the canonical rows. Only ok=True
    # numeric rows count, mirroring scale_distributions(); empty until a
    # producer (GRPO shadow, rainbow round) emits.
    data["rewards"] = None
    try:
        _rrows = con.execute(
            "SELECT json_extract(data, '$.scale_id'), COUNT(*), "
            "AVG(CAST(json_extract(data, '$.value') AS REAL)), "
            "MAX(timestamp) FROM memories "
            "WHERE source='reward-ledger' "
            "AND json_extract(data, '$.ok') = 1 "
            "AND typeof(json_extract(data, '$.value')) IN ('integer', 'real') "
            "GROUP BY json_extract(data, '$.scale_id')").fetchall()
        data["rewards"] = {
            "by_scale": {r[0]: {"n": r[1], "mean": r[2]} for r in _rrows},
            "latest": max((r[3] for r in _rrows), default=None),
        }
    except Exception as exc:
        print("rewards block failed: %s" % type(exc).__name__, file=sys.stderr)

    # mesh fabric: the fleet cycle is the architecture the rest of the
    # system now runs on, yet this board had no mesh row at all, so a dead
    # mesh read as a healthy board. Folded into `freshness` so staleness
    # reaches the alert line and the verdict, not just a panel (2026-09-15).
    data["mesh"] = mesh_status()
    _m = data["mesh"]
    data["freshness"].append({
        "label": "mesh", "ts": _m.get("at"), "age": _m.get("age"),
        "ok": _m.get("age") is not None and _m["age"] < 15,
    })
    return data


# ---------------------------------------------------------------------------
# Interpretation layer — turn the numbers into plain English
# ---------------------------------------------------------------------------

def explain(data):
    """Return list of (verdict, detail) explanation lines about the data."""
    out = []

    # --- freshness verdict ---
    stale = [f for f in data["freshness"] if not f["ok"]]
    if not stale:
        out.append(("OK", f"All {len(data['freshness'])} loops are firing on their "
                           "5-min cadence - training is live."))
    else:
        names = ", ".join(f["label"] for f in stale)
        out.append(("WARN", f"{names} stale (>15 min) - check Windows Task Scheduler; "
                            f"the loops can silently stop when trigger windows expire."))

    # --- evolution verdict ---
    # Trend is only meaningful within one fitness scale (v1 vs v2-dual).
    evo = data["evo"]
    if len(evo) >= 3 and len({e["fit"] for e in evo[:4]}) == 1:
        recent = evo[0]["best"]
        prev3 = [e["best"] for e in evo[1:4]]
        mid = sum(prev3) / len(prev3)
        if recent > mid * 1.03:
            trend = f"climbing (recent {recent:.2f} vs avg {mid:.2f} of prior 3)"
        elif recent < mid * 0.97:
            trend = f"dip (recent {recent:.2f} vs avg {mid:.2f} of prior 3 - normal noise, re-seeds each run)"
        else:
            trend = f"steady around {mid:.2f}"
        out.append(("EVO", f"best fitness {trend}."))

    if evo and evo[0]["flat"] is False:
        out.append(("EVO", "trajectory is non-flat - reward is action-dependent, so evolution has a "
                           "real gradient to climb (the reward fix is working)."))
    elif evo and evo[0]["flat"] is True:
        # EV-8: best is flat BY CONSTRUCTION under elitism — only a flat
        # MEAN means the objective has no usable gradient here.
        if evo[0].get("avg_flat") is False:
            out.append(("EVO", "best flat but mean moved - converged to a plateau at a strong "
                               "seeded champion; search is running, not stuck."))
        elif evo[0].get("avg_flat") is True:
            out.append(("EVO", "best AND mean flat - objective has no usable gradient on this pool."))
    # promotion funnel (2026-09-11): where candidates die, over recent rows.
    if len(evo) >= 3:
        _n = len(evo)
        _beats = sum(1 for e2 in evo if e2.get("beats"))
        _sprt = sum(1 for e2 in evo if (e2.get("sprt") or {}).get("decision") == "promote")
        _par = sum(1 for e2 in evo if (e2.get("pareto") or {}).get("passed") is True)
        _hold = sum(1 for e2 in evo if e2.get("holdout"))
        _prom = sum(1 for e2 in evo if e2.get("promoted"))
        out.append(("EVO", f"funnel over {_n}: beats {_beats} -> sprt {_sprt} -> "
                           f"pareto {_par} -> holdout {_hold} -> promoted {_prom}."))
        # funnel BLOCKED warn: SPRT says promote but nothing ships — the
        # gates downstream are eating every candidate. Distinguishes *where*.
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
            out.append(("WARN", f"funnel blocked at PARETO gate ({_died_par}/{_n} SPRT-promotes died) - "
                                f"alert floor may be unsatisfiable; review PARETO_ALERT_TOL_PCT."))
        elif _died_hold >= 3:
            out.append(("WARN", f"funnel blocked at HOLDOUT gate ({_died_hold}/{_n} SPRT-promotes died) - "
                                f"wins not surviving fresh pools; check pool drift / overfitting."))
    if evo:
        eps = evo[0]["genes"].get("epsilon", -1)
        if eps is not None and eps < 0.02:
            out.append(("EVO", f"epsilon={eps:.3f} - near-pure exploitation: the policy has converged to "
                               "deterministic actions; random exploration now mostly hurts reward."))
        elif eps is not None and eps < 0.12:
            out.append(("EVO", f"epsilon={eps:.3f} - mostly exploiting, light exploration remaining."))
        elif eps is not None:
            out.append(("EVO", f"epsilon={eps:.3f} - still exploring widely; search is sampling new genes."))

    # --- steering verdict ---
    s = data["strategy"]
    if s["value"] != "?":
        meaning = STRATEGY_MEANING.get(s["value"].lower(),
                                       "no canned description - check metacog output")
        out.append(("STEER", f"strategy '{s['value']}' -> {meaning}."))

    # --- activity verdict ---
    a = data["activity"]
    if a["quality"] >= 0.6:
        out.append(("ACT", f"dream quality {a['quality']:.2f} - healthy; dreams are being adopted."))
    elif a["quality"] >= 0.45:
        out.append(("ACT", f"dream quality {a['quality']:.2f} - mediocre; many dreams may miss the 0.5 bar."))
    else:
        out.append(("ACT", f"dream quality {a['quality']:.2f} - weak; check creativity factors."))

    # --- training verdict ---
    tr = data.get("training", {})
    # RL excluded from the lead comparison (2026-09-09): its reward is a
    # Q-value average on a different scale than SB3 episode totals, but it
    # stays visible in the grid.
    scored = {a: r["eval_mean_reward"] for a in TRAIN_ALGOS if a != "RL"
              for r in (tr.get(a) or [])[:1] if r.get("eval_mean_reward") is not None}
    if len(scored) >= 2:
        best = max(scored, key=scored.get)
        others = [(a, v) for a, v in sorted(scored.items()) if a != best]
        out.append(("EVO", f"Training lead: {best} (reward {scored[best]:.1f}) vs "
                            + ", ".join(f"{a} {v:.1f}" for a, v in others)))

    return out


def bottom_line(data):
    """One-sentence overall verdict."""
    evo = data["evo"]
    s = data["strategy"]
    a = data["activity"]
    stale = [f for f in data["freshness"] if not f["ok"]]
    parts = []
    if stale:
        parts.append(f"{len(stale)} loop(s) stale")
    if evo:
        parts.append(f"evolution best {evo[0]['best']:.2f}"
                     f"{' (climbing)' if len(evo) >= 2 and evo[0]['best'] > sum(e['best'] for e in evo[1:4]) / max(1, len(evo[1:4])) else ''}")
    if s["value"] != "?":
        parts.append(f"strategy {s['value']}")
    if a["dreams"]:
        parts.append(f"{a['dreams']} dreams/hr @ {a['quality']:.2f} quality")
    tr = data.get("training", {})
    # RL excluded from the lead comparison (2026-09-09): its reward is a
    # Q-value average on a different scale than SB3 episode totals, but it
    # stays visible in the grid.
    scored = {a: r["eval_mean_reward"] for a in TRAIN_ALGOS if a != "RL"
              for r in (tr.get(a) or [])[:1] if r.get("eval_mean_reward") is not None}
    if scored:
        best = max(scored, key=scored.get)
        parts.append(f"training lead {best} ({scored[best]:.1f})")
    if not parts:
        return "No data yet - waiting for the first cycle to fire."
    # The lead phrase has to follow the data. It used to be an unconditional
    # "Training is live", which printed alongside the monitor's own
    # "STALE evolution >15 min - check Task Scheduler." line while every cycle
    # was ~21h old and three of four were stale. A verdict that asserts health
    # regardless of what the rest of the board shows is the same defect class as
    # a hardcoded one, just quieter.
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
    return f"{lead}: " + "; ".join(parts) + "."


# Display names that are shorter than the registry algo keys.
_ALGO_DISP = {"MaskablePPO": "MaskPPO"}
# last-seen latest-run id per algo, for the "new run" flash
_LAST_RUN_ID = {}


def _algo_name(a):
    return _ALGO_DISP.get(a, a)


def _algo_trend(series):
    """Up/down arrow of the two most recent rewards (series newest-first).

    Returns '^' if the newest run beat the previous, 'v' if it fell, '=' if
    equal, or '' when there aren't two usable points yet.
    """
    vals = [p["reward"] for p in series[:2] if p.get("reward") is not None]
    if len(vals) < 2:
        return ""
    return "^" if vals[0] > vals[1] else ("v" if vals[0] < vals[1] else "=")


def _last_run_meta(a, tr):
    """Return (run, color) for an algo's newest run, updating the flash map."""
    runs = tr.get(a) or []
    if not runs:
        return None, ""
    r = runs[0]
    rid = r.get("run_id")
    m = r.get("eval_mean_reward")
    fresh = _LAST_RUN_ID.get(a) not in (None, rid)
    _LAST_RUN_ID[a] = rid
    arrow = _algo_trend(tr.get("series", {}).get(a) or [])
    if fresh:
        col = C_YEL  # new run since the previous refresh
    elif arrow == "^":
        col = C_GRN
    elif arrow == "v":
        col = C_RED
    else:
        col = ""
    return r, col


def _training_grid(tr, width):
    """Fixed-height compact grid: one cell per algo, N columns by width.

    Cell: `PPO v3 289.4^` (model version, latest mean reward, trend vs
    previous run). Green improving / red falling / yellow = new since
    last refresh. Row count depends ONLY on width (never on data), so
    the frame height is predictable — this is what keeps the wallboard
    on one screen.
    """
    ncols = max(1, (width - 4) // 20)          # 18-char cell + 2-space separator
    rows_out = []
    if not tr.get("counts"):
        return ["    no training artifacts yet"]
    cells = []
    for a in TRAIN_ALGOS:
        r, col = _last_run_meta(a, tr)
        if r is None:
            cells.append((f"{_algo_name(a):<7}{"--":>11}", ""))
            continue
        m = r.get("eval_mean_reward")
        if m is None:
            cells.append((f"{_algo_name(a):<7}{"n/a":>11}", ""))
            continue
        arrow = _algo_trend(tr.get("series", {}).get(a) or [])
        ver = (r.get("model_version") or "?").lstrip("v") or "?"
        if len(ver) > 4 and "." in ver:
            ver = ".".join(ver.split(".")[:2])
        val = f"v{ver} {m:.1f}{arrow}"
        cells.append((f"{_algo_name(a):<7}{val:>11}", col))
    for i in range(0, len(cells), ncols):
        chunk = cells[i:i + ncols]
        parts = []
        for body, col in chunk:
            parts.append(f"{col}{body}{C_RST}" if col else body)
        rows_out.append("    " + "  ".join(parts))
    return rows_out


# ── display width (2026-09-12) ─────────────────────────────────────────
# The monitor's never-wrap invariant depends on measuring lines the way the
# TERMINAL does. `len()` is only correct while every glyph is ASCII: a wide
# character (emoji are `east_asian_width == 'W'`) occupies TWO columns while
# `len()` reports 1, so one wide glyph silently shifts every line by a column.
# `_vis_len` counts COLUMNS and `_fit` truncates in columns; the ASCII fast
# path keeps the previous behaviour exactly.
_ANSI_RE = None
_ZW = ("\ufe0e", "\ufe0f", "\u200d", "\u200b")   # variation selectors + ZWJ


def _disp_w(ch):
    """Display columns for one character, as the terminal counts them."""
    import unicodedata
    if ch in _ZW or unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _vis_len(s):
    """Visible width in COLUMNS, ignoring ANSI color codes."""
    global _ANSI_RE
    if _ANSI_RE is None:
        _ANSI_RE = re.compile("\x1b\\[[0-9;]*m")
    plain = _ANSI_RE.sub("", s)
    if plain.isascii():          # fast path: unchanged behaviour for ASCII
        return len(plain)
    return sum(_disp_w(c) for c in plain)


def _fit(s, width):
    """Truncate to window width in COLUMNS; never wrap, never scroll.

    Colors are stripped on truncation (simpler than re-closing codes, and
    truncated lines are rare edge cases on narrow windows).
    """
    global _ANSI_RE
    if _ANSI_RE is None:
        _ANSI_RE = re.compile("\x1b\\[[0-9;]*m")
    if _vis_len(s) <= width:
        return s
    plain = _ANSI_RE.sub("", s)
    ascii_only = plain.isascii()
    out, used = [], 0
    for ch in plain:
        cw = 1 if ascii_only else _disp_w(ch)
        if used + cw > max(0, width):
            break
        out.append(ch)
        used += cw
    return "".join(out)


def _control_line(data):
    """Return the first-frame control-plane summary for the wallboard.

    The monitor is read-only, so it must not imply that a fresh-looking value is
    live merely because the renderer refreshed.  This line makes the evidence
    boundary explicit before the detailed panels.

    Banner contract (pinned by tests/test_terminal_monitor_controls.py, shared
    with the AOA banner and the split-out epistemic-tier viewer's banner): the
    loop-evidence summary is always shown (`loops N/M current`, or `loop
    evidence unknown` when there is no evidence at all), and `next=` is the
    OPERATOR ACTION slot. The controller rewrite put the scheduler's next job
    name there, which answers "what will the system do" while the reader is
    looking at a DEGRADED banner for "what do I do" -- and in narrow windows
    `_fit` truncates exactly that tail away.
    """
    fresh = data.get("freshness") or []
    current = sum(1 for row in fresh if row.get("ok"))
    stale = len(fresh) - current
    if not fresh:
        state, colour = "UNKNOWN", C_YEL
        evidence = "loop evidence unknown"
        action = "refresh scheduler/producers"
    elif stale:
        state, colour = "DEGRADED", C_RED
        evidence = f"loops {current}/{len(fresh)} current"
        action = "refresh scheduler/producers"
    else:
        state, colour = "LIVE", C_GRN
        evidence = f"loops {current}/{len(fresh)} current"
        action = "continue observing evidence"

    # Control-plane state is read from the replacement controller itself. The
    # monitor is intentionally read-only: it never calls fire() or a job.
    #
    # The import is deliberately LAZY. `chain_controller` sits beside this
    # script and is on sys.path when the wallboard is launched, but this module
    # is also loaded straight from its path by tests that never add scripts/ to
    # sys.path -- a module-level import made those loads raise
    # ModuleNotFoundError, trading a missing banner for a missing module. On a
    # failed import the same degraded controller dict is used, so the banner is
    # always printable and never claims knowledge it does not have.
    try:
        import chain_controller  # noqa: E402
        controller = chain_controller.status()
    except Exception as exc:
        controller = {"task_state": "unknown", "heartbeat": {"status": "MISSING"},
                      "jobs": {}, "scheduler_mutated": False, "error": str(exc)[:60]}
    task_state = str(controller.get("task_state") or "unknown")
    heartbeat = controller.get("heartbeat") or {}
    hb_state = str(heartbeat.get("status") or "MISSING")
    hb_age = heartbeat.get("age_s")
    hb_text = hb_state if hb_age is None else f"{hb_state}@{float(hb_age) / 60:.1f}m"
    jobs = controller.get("jobs") or {}
    selected = jobs.get("selected") or "none"
    owner = controller.get("owner") or {}
    owner_pid = owner.get("pid")
    owner_text = f"{owner_pid}{'*' if owner.get('current') else ''}" if owner_pid else "none"
    pulse = controller.get("last_heartbeat") or {}
    phase = str(pulse.get("event") or "unknown")
    active_job = str(pulse.get("selected") or selected)
    last_fire = controller.get("last_fire") or {}
    last_job = str(last_fire.get("job") or "none")
    last_ok = last_fire.get("result", {}).get("ok")
    last_text = f"{last_job}:{last_ok}" if last_job != "none" else "none"
    mutated = "false" if controller.get("scheduler_mutated") is False else "unknown"
    return (f"{C_BLD}CONTROL {colour}{state}{C_RST}  {evidence}  next={action}  "
            f"ctrl=present sched={task_state} job={selected} "
            f"owner={owner_text} phase={phase}/{active_job} "
            f"hb={hb_text} last={last_text} mut={mutated}")


def render(data, now):
    """Fixed-frame one-screen wallboard (2026-09-09 redesign).

    Every section has a data-independent line budget and every line is
    truncated (never wrapped) to the window width, so the frame height is
    bounded by construction: header 3 + LOOPS 1 + EVO 2 + STEER 1 +
    TRAIN head 1 + grid ceil(15/ncols) + MESH 1 + VERDICT 1 + alerts 0..2.
    Small windows show the same sections narrower — never scrolled.
    The plain-English interpretation moved to the log file (append_log).
    """
    cols, rows = shutil.get_terminal_size((100, 30))
    # Leave the last column and the 3 header rows out of the budget: a line
    # that fills the window EXACTLY can wrap on a console that wraps on the
    # last cell, and a wrapped line shifts -- then scrolls -- the whole frame,
    # so it no longer repaints in place.
    width = max(64, min(cols - 1, 200))
    height = max(8, rows - 3)
    _FRAME_NO[0] += 1   # twinkle step for the rainbow GOSP line
    # Priority drop for short windows (2026-09-09): the bottom sections
    # (TRAIN grid, verdict) must never be cut — drop detail lines first.
    show_climb = height >= 22
    show_genes = height >= 19
    lines = []
    L = lines.append

    L("=" * width)
    L(_fit(f"  AZAZAEL LIVE MONITOR   {now.strftime('%Y-%m-%d %H:%M:%S')}   (refresh {REFRESH}s, Ctrl+C to stop)", width))
    L("=" * width)

    # --- cycle freshness, one line ---
    bits = []
    any_stale = False
    for f in data["freshness"]:
        if f["ts"] is None:
            bits.append((f"{f['label']}: --", C_RED))
            any_stale = True
            continue
        a = f["age"]
        ok = f["ok"]
        if not ok:
            any_stale = True
        label = f"{f['label']} {a:.1f}m"
        bits.append((label, C_GRN if ok else C_RED))
    L(_fit("LOOPS  " + "   ".join(f"{c}{t}{C_RST}" if c else t for t, c in bits), width))

    # --- mesh fabric: one line from the fleet cycle (2026-09-15) ---
    m = data.get("mesh") or {}
    if m.get("at") or m.get("samples"):
        _age = m.get("age")
        _acol = (C_RED if _age is None else
                 (C_GRN if _age <= 15 else (C_YEL if _age <= 60 else C_RED)))
        _ok = m.get("ok")
        _fl = m.get("fail") or 0
        _tot = (_ok or 0) + _fl
        mbits = [f"{C_GRN if not _fl else C_RED}{_ok if _ok is not None else '--'}"
                 f"/{_tot or '--'} ok{C_RST}"]
        if m.get("natures"):
            mbits.append(f"{m['natures']}n")
        # cpu first (with its spark immediately after it, so the trend can
        # never be the part _fit truncates away), then the rest.
        if m.get("cpu") is not None:
            _sp = sparkline(m.get("cpu_trend") or [], 12) if m.get("cpu_trend") else ""
            mbits.append(f"cpu {float(m['cpu']):.0f}% "
                         + (f"{C_CYN}{_sp}{C_RST}" if _sp else ""))
            mbits.append(f"mem {float(m.get('mem') or 0):.0f}%")
            mbits.append(f"db {float(m.get('db_mb') or 0):.0f}MB")
        if m.get("failed"):
            mbits.append(f"{C_RED}FAIL: {','.join(m['failed'][:3])}{C_RST}")
        if m.get("retried"):
            mbits.append(f"{C_YEL}retried {m['retried']}{C_RST}")
        _mage = f"{_age:.1f}m" if _age is not None else "--"
        L(_fit(f"MESH   {(m.get('at') or '--')[5:16]} {_acol}{_mage}{C_RST}  "
                 + "  ".join(mbits), width))

    # --- evolution ---
    evo = data["evo"]
    hard_stale = None  # hours since newest hard example (None = unknown/absent)
    if evo:
        e = evo[0]
        g = e.get("genes") or {}
        # promotion standing folded into the EVO line (0 extra rows)
        prom_txt = ""
        if e.get("baseline") is not None:
            if e["beats"]:
                prom_txt = (f"  {C_GRN}beats base +{e['best'] - e['baseline']:.2f}{C_RST}"
                            + (f"  {C_YEL}PROMOTED{C_RST}" if e["promoted"] else ""))
            else:
                prom_txt = f"  vs base {e['baseline']:.1f}"
        # SPRT gate state folded into EVO (0 extra rows): the gate that
        # actually decides promotion was invisible on the wallboard.
        sprt_txt = ""
        _sp = e.get("sprt") or {}
        if _sp.get("decision"):
            _llr = f"{_sp['llr']:.1f}" if isinstance(_sp.get("llr"), (int, float)) else "?"
            _sn = _sp.get("n", "?")
            _sd = _sp["decision"]
            _scol = C_GRN if _sd == "promote" else (C_RED if _sd == "reject" else C_YEL)
            # 'not_run' = the margin gate failed first, so the test never ran.
            # Showing "reject" for that (the old default) made an untested
            # cycle look like evidence against a promotion (2026-09-11).
            _slab = "--" if _sd == "not_run" else _sd
            sprt_txt = f"  sprt={_scol}{_slab}{C_RST}(n={_sn} llr={_llr})"
        L(_fit(f"EVO    best {C_GRN}{e['best']:.3f}{C_RST}"
                 + (f"  info={e['info'] if e['info'] is not None else '?'} "
                    f"alert={e['alert'] if e['alert'] is not None else '?'}"
                    if e.get("info") is not None or e.get("alert") is not None else "")
                 + prom_txt + sprt_txt, width))
        # --- advancement honesty (2026-09-13): funnel deaths by gate, oracle
        # age, cooldown, best trend. This board stayed green through a day-long
        # promotion deadlock (stale oracle + absolute bar production itself
        # failed); the killer gate is now named every frame. 1 line, 0 scroll.
        _sp24 = _dp = _dh = _do = _pr24 = 0
        _bnew = _bold = None
        for _er in evo:
            _tr = _er.get("traj") or []
            if _tr:
                _bb = max(_tr)
                if _bnew is None:
                    _bnew = _bb
                _bold = _bb
            if (_er.get("sprt") or {}).get("decision") != "promote":
                continue
            _sp24 += 1
            if _er.get("promoted"):
                _pr24 += 1
                continue
            _pg = _er.get("pareto") or {}
            _ho = _er.get("holdout")
            _ho_pass = _ho if isinstance(_ho, bool) else (_ho or {}).get("passed")
            if _pg.get("passed") is not True:
                _dp += 1
            elif not _ho_pass:
                _dh += 1
            else:
                _do += 1
        _oage = None
        try:
            _op = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "state", "fitness_oracle.json")
            if os.path.exists(_op):
                _oage = (time.time() - os.path.getmtime(_op)) / 3600.0
        except Exception:
            pass
        _otxt = "--" if _oage is None else (f"{_oage:.1f}h" + (" STALE" if _oage > 6 else ""))
        _trend = "--"
        if _bnew is not None and _bold is not None:
            _trend = f"{_bnew - _bold:+.2f}"
        _adv_col = C_GRN if _pr24 > 0 else (C_RED if _sp24 >= 5 else C_YEL)
        L(_fit(f"ADVANCE sprt={_sp24} prom={_adv_col}{_pr24}{C_RST} "
               f"die(p={_dp}/h={_dh}/post={_do}) "
               f"best {_bold}->{_bnew} ({_trend}) oracle={_otxt}", width))
        # --- the three learners together: META + GOSP beside EVO ---
        _m = data.get("meta") or {}
        _mp = _m.get("params") or {}
        # dead-copy guard: the file is only written by a scored meta cycle,
        # so age it — past 15 min the numbers on screen are history.
        _mage = data.get("meta_age_s")
        _mage_txt = ""
        if isinstance(_mage, (int, float)):
            _mage_txt = f" {_mage // 60:.0f}m" if _mage >= 60 else f" {_mage:.0f}s"
            if _mage > 900:
                _mage_txt = f" {C_RED}STALE{_mage_txt}{C_RST}"
        if _mp:
            _meta_txt = (f"META   mut={_mp.get('mutation_rate', 0):.2f} "
                         f"xov={_mp.get('crossover_rate', 0):.2f} "
                         f"tour={_mp.get('tournament_size', 0)} "
                         f"pat={_mp.get('early_stop_patience', 0)} "
                          f"tol={_mp.get('early_stop_tolerance', 0):.3f}  "
                          f"ema {_m.get('score_ema', 0):+.2f}/{_m.get('runs', 0)}r{_mage_txt}")
            _cand = _m.get("candidate")
            if _cand:
                _diff = [k for k in ("mutation_rate", "crossover_rate",
                                     "tournament_size", "early_stop_patience",
                                     "early_stop_tolerance")
                         if _cand.get(k) != _mp.get(k)]
                _cr, _cs = _m.get("candidate_runs", 0), _m.get("candidate_sum", 0.0)
                _cmean = f" mean{_cs / _cr:+.2f}" if _cr else ""
                if _diff:
                    _cv = _cand[_diff[0]]
                    _cv = round(_cv, 4) if isinstance(_cv, float) else _cv
                    _meta_txt += (f"  {C_YEL}testing {_diff[0]}={_cv} "
                                  f"({_cr}/3{_cmean}){C_RST}")
                else:
                    _meta_txt += f"  {C_YEL}testing candidate ({_cr}/3{_cmean}){C_RST}"
        else:
            _meta_txt = "META   no meta state yet (first scored cycle still running)"
        L(_fit(_meta_txt, width))
        _g = data.get("gosp") or {}
        _gl = _g.get("learner") or {}
        L(_fit(f"{C_ORG}{C_BLD}Gossamer Oxford Semantical Parser{C_RST}", width))
        _gtxt = "GOSP  "
        if _gl:
            _gsegs = []
            if _gl.get("iter") is not None:
                _gsegs.append(f"iter={_gl['iter']}")
            if _gl.get("conv") is not None:
                _gsegs.append(f"conv={_gl['conv']}")
            if _gl.get("acc") is not None:
                _gsegs.append(f"acc={_gl['acc']}")
            if _gl.get("docs") is not None:
                _gsegs.append(f"docs={_gl['docs']}")
            _gtxt += "  " + " ".join(_gsegs)
            if _gl.get("mover") and _gl["mover"] != "?":
                _gtxt += f"  top mover {_gl['mover']} {_gl.get('mfrom')}->{_gl.get('mto')}"
            _gp = _g.get("parity") or {}
            if _gp.get("total"):
                _gtxt += f"  parity {_gp['agree']}/{_gp['total']}"
            _gr = _g.get("rani") or {}
            if _gr.get("mode"):
                _gtxt += f"  rani {_gr['mode'].split(' ')[0]}"
            _gu = _g.get("uv") or {}
            if _gu.get("n"):
                _gtxt += f"  uv best={_gu['best']}/{_gu['n']}T {_gu.get('age', '?')}h"
        else:
            _gtxt += " no learner evidence yet (run gosp_learner_train.py in WSL)"
        L(_rainbow(_gtxt, width))
        # --- Eve Unleashed RANI: which backend answered last + battery stats ---
        _rb = (data.get("gosp") or {}).get("rani_batt")
        if _rb:
            _rparts = [f"backend={_rb.get('backend', '?')}"]
            if _rb.get("rounds") is not None:
                _rparts.append(f"{_rb['rounds']} rounds")
            if _rb.get("adv") is not None:
                _rparts.append(f"adv={_rb['adv']}")
            if isinstance(_rb.get("score"), (int, float)):
                _rparts.append(f"score={_rb['score']:.2f}")
            if isinstance(_rb.get("lat"), (int, float)):
                _lat = _rb['lat']
                _rparts.append(f"lat={_lat / 1000:.1f}s" if _lat >= 1000 else f"lat={_lat:.0f}ms")
            if _rb.get("tok") is not None:
                _rparts.append(f"tok={_rb['tok']}")
            if _rb.get("date"):
                _rparts.append(f"({_rb['date']})")
            L(_fit("EVE    " + "  ".join(_rparts), width))
        else:
            L(_fit("EVE    no RANI battery yet (run gosp_rani_integration.py or rani_llm_test.py)", width))
        # --- Armorie-of-Anubis umbrella orchestrator (1 line, file reads) ---
        _aoa = aoa_status()
        _asegs = []
        if _aoa.get("import_error"):
            # Don't dress a failed import up as a healthy count.
            _asegs.append(f"arms import FAILED ({_aoa['import_error']})")
        elif _aoa.get("arm_classes") is not None:
            # Report the two different facts separately instead of collapsing
            # them into one number: arm classes that exist on disk, and how
            # many the package re-exports. "8 arm classes exported" was read as
            # the fleet size while the AOA board showed 9 registered arms.
            _asegs.append(f"{_aoa['arm_classes']} arm classes defined"
                          f" ({_aoa.get('reexported', 0)} re-exported)")
        if _aoa.get("unexported"):
            _asegs.append(f"+{','.join(_aoa['unexported'])} unexported")
        if _aoa.get("not_reexported"):
            _asegs.append("not in arms/__init__: "
                          f"{','.join(_aoa['not_reexported'])}")
        if _aoa.get("ms_n") is not None:
            _asegs.append(f"ms v{_aoa.get('ms_ver', '?')} x{_aoa['ms_n']}")
            # "newest" so the number cannot be read as "the file was touched".
            if isinstance(_aoa.get("ms_age_d"), (int, float)):
                _asegs.append(f"newest {_aoa['ms_age_d']:.0f}d")
        if isinstance(_aoa.get("bench_age_d"), (int, float)):
            _btxt = f"bench {_aoa['bench_age_d']:.0f}d ago"
            if isinstance(_aoa.get("eve_models"), int):
                _btxt += f" eve{_aoa['eve_models']}mdl"
            _asegs.append(_btxt)
        L(_fit("AOA    " + ("  ".join(_asegs) if _asegs else "no AOA state found"), width))
        # evo genetics detail: champion re-eval, beat rate, pool mix
        _champs = [e2.get("champ") for e2 in evo[:2]]
        _champ_txt = "champ " + "->".join("%.2f" % c for c in _champs if c is not None) or "champ n/a"
        _beat_n = sum(1 for e2 in evo[:10] if e2.get("beats"))
        _pool0 = evo[0].get("pool") or {}
        _pmix = "/".join(f"{k}={_pool0[k]}" for k in ("info", "critical", "warning")
                         if isinstance(_pool0.get(k), (int, float)))
        L(_fit(f"EVO2   {_champ_txt}  beats {_beat_n}/{min(10, len(evo))}  pool {_pmix}"
               + (f"  run gens={len(evo[0].get('traj') or [])}" if evo[0].get("traj") else ""), width))
        # pool drift: hard-alert inflow (starvation would freeze this)
        pool = e.get("pool") or {}
        hard = pool.get("hard_count")
        hard_ts = (pool.get("hard_newest") or "")[11:16] if pool.get("hard_newest") else ""
        # staleness: age of the newest hard example in hours (None if unknown)
        hard_stale = None
        if pool.get("hard_newest"):
            try:
                hdt = datetime.fromisoformat(str(pool["hard_newest"]).replace("Z", "+00:00"))
                hard_stale = (now - hdt.astimezone()).total_seconds() / 3600.0
            except Exception:
                hard_stale = None
        pool_txt = ""
        if hard:
            pool_txt = f"  hard {hard}" + (f"@{hard_ts}Z" if hard_ts else "")
            if hard_stale is not None and hard_stale > 24:
                pool_txt += f"  {C_RED}(stale {hard_stale:.0f}h){C_RST}"
        # PIN flags (2026-09-09): a gene sitting on its bound means the
        # search has converged in that dimension without proving it —
        # widen mutation if a pin persists for days.
        def _pin(v, lo, hi, eps=1e-9):
            try:
                f = float(v)
            except (ValueError, TypeError):
                return ""
            return " PIN" if f <= lo + eps or f >= hi - eps else ""
        # PIN bounds mirror GENE_BOUNDS in evolution_cycle.py. They were widened
        # 2026-09-11 (lr 0.5->0.9, discount 0.8->0.5 floor, decay 0.1->0.3); the
        # literals here must move with them or the PIN flag lies.
        L(_fit(f"GENES lr={g.get('learning_rate', 0):.4f}{_pin(g.get('learning_rate'), 0.01, 0.9)} "
                 f"eps={g.get('epsilon', 0):.4f}{_pin(g.get('epsilon'), 0.0, 0.5)} "
                 f"disc={g.get('discount', 0):.4f}{_pin(g.get('discount'), 0.5, 0.999)} "
                 f"dec={g.get('epsilon_decay', 0):.4f}{_pin(g.get('epsilon_decay'), 0.0, 0.3)}"
                 f"  [{e['fit']}]{pool_txt}", width)) if show_genes else None
        bests = [e2["best"] for e2 in evo][::-1]
        if show_climb:
            L(f"CLIMB ({len(bests)} runs, newest right) {C_CYN}{sparkline(bests, width=max(10, width - 30))}{C_RST}")
        # per-generation trajectory of the latest run (how it got there)
        _tj = evo[0].get("traj") or []
        if _tj:
            _tvals = [f"{x:.2f}" for x in _tj]
            _tflag = "flat" if len(set(round(x, 3) for x in _tj)) <= 1 else f"+{_tj[-1] - _tj[0]:+.2f}"
            L(_fit(f"TRAJ   {' '.join(_tvals)}  [{_tflag} over {len(_tvals)} gens]", width))
        # --- v2.0 search arms: one line (per-arm duel + gate funnel + QD/TPE) ---
        _armed = [e2 for e2 in evo if (e2.get("cma") or {}).get("ran")]
        _segs = []
        if _armed:
            # Split the duel by the arm that actually ran (cma|de) instead of
            # labelling every row "cma" - DE slots were invisible before
            # (2026-09-11). Green = the secondary arm took at least half.
            _by = {}
            for e2 in _armed:
                _by.setdefault((e2["cma"].get("arm") or "cma"), []).append(e2)
            for _an in sorted(_by):
                _rows = _by[_an]
                _ww = sum(1 for e2 in _rows if e2["cma"].get("winner") == "cma")
                _ms = [(e2["cma"]["cma_total"] - e2["cma"]["ga_total"])
                       for e2 in _rows
                       if isinstance(e2["cma"].get("cma_total"), (int, float))
                       and isinstance(e2["cma"].get("ga_total"), (int, float))]
                _mm = f" {sum(_ms) / len(_ms):+.2f}" if _ms else ""
                _acol = C_GRN if _ww * 2 >= len(_rows) else C_YEL
                _segs.append(f"{_acol}{_an} {_ww}/{len(_rows)}{_mm}{C_RST}")
            _strip = "".join("C" if e2["cma"].get("winner") == "cma" else "G"
                             for e2 in _armed[:20][::-1])
            _segs.append(f"[{_strip}]")
        # gate funnel on the wallboard: how far the recent cycles actually got.
        # pareto.passed is None when the gate never ran (margin/SPRT stopped it
        # earlier), so the denominator is real attempts, not every row.
        _pg = [e2 for e2 in evo
               if (e2.get("pareto") or {}).get("passed") is not None]
        if _pg:
            _pp = sum(1 for e2 in _pg if e2["pareto"]["passed"])
            _hc = sum(1 for e2 in _pg if e2.get("holdout"))
            _pcol = C_GRN if _pp * 2 >= len(_pg) else C_YEL
            _hcol = C_GRN if _hc else C_RED
            _segs.append(f"{_pcol}pareto {_pp}/{len(_pg)}{C_RST} "
                         f"{_hcol}holdout {_hc}{C_RST}")
        _qd0 = (evo[0].get("qd") or {})
        if _qd0.get("filled") is not None:
            _segs.append(f"qd {_qd0['filled']}/16 cov={_qd0.get('coverage', '?')}")
        _tpe0 = evo[0].get("tpe")
        if isinstance(_tpe0, int):
            _segs.append(f"tpe={_tpe0}")
        _r100 = run100_status()
        if _r100:
            _segs.append(f"run100 {_r100[0]}/{_r100[1]}")
        if _segs:
            L(_fit("ARMS   " + "  ".join(_segs), width))
        # QD illumination heatmap (height-gated: 4 rows only on tall windows)
        if height >= 40:
            _grid = qd_grid()
            if _grid:
                L(_fit("QDMAP  " + " ".join(_grid) + "  (info ->, alert ^)", width))
    else:
        L("EVO    no evolution_result rows yet")

    # --- steering + activity on one line ---
    s = data["strategy"]
    act = data["activity"]
    flip = data.get("last_flip")
    flip_txt = ""
    if flip and flip.get("to") != s["value"]:
        # strategy currently differs from last committed flip -> show pending
        flip_txt = f"  [last change -> {C_GRN}{flip['to']}{C_RST} {flip['age']}m ago]"
    elif flip:
        fa = flip.get("age")
        if fa is not None and fa < 60:
            flip_txt = f"  [{C_YEL}flipped to {flip['to']} {flip['age']}m ago{C_RST}]"
        else:
            flip_txt = f"  [last flip -> {flip['to']} {flip['age']}m ago]"
    # STEER line is width-adaptive (2026-09-08): segments are dropped
    # right-to-left when the window is tight so the line never wraps.
    steer_segs = [f"STEER  {C_YEL}{s['value']}{C_RST} ({data['steer_24h']}/24h)"]
    if flip_txt:
        steer_segs.append(flip_txt)
    steer_segs.append(f"DREAMS {act['dreams']}/hr q={act['quality']:.3f}")
    _tn = data.get("tuned")
    if _tn and isinstance(_tn.get("cf"), (int, float)):
        steer_segs.append(f"tuned cf={_tn['cf']:.2f} sc={_tn.get('sc', '?')}")
    steer_segs.append(f"adapt {act.get('adapt_age')}m ago  apply {act.get('apply_age')}m ago")
    _steer = steer_segs[0]
    for _seg in steer_segs[1:]:
        if len(_steer) + len(_seg) + 3 <= width - 4:
            _steer += "   " + _seg
        else:
            break
    L(_fit(_steer, width))

    # --- policy verdict: what the last dream round actually did ---
    _py = data.get("policy")
    if _py:
        _ptxt = f"POLICY applied {_py.get('napplied', 0)} skipped {_py.get('nskipped', 0)}"
        # dream bar: visual adoption ratio for the last dream round.
        # Same counts, glanceable — dropped automatically if both are zero.
        try:
            _na = int(_py.get('napplied', 0) or 0)
            _ns = int(_py.get('nskipped', 0) or 0)
        except Exception:
            _na, _ns = 0, 0
        if _na + _ns > 0:
            _fill = round(10 * _na / (_na + _ns))
            _bar = "#" * _fill + "-" * (10 - _fill)
            _bcol = C_GRN if _fill >= 5 else (C_YEL if _fill >= 3 else C_RED)
            _ptxt += f"  {_bcol}[{_bar}]{C_RST}"
        if _py.get("worm") and _py.get("rec"):
            _cf = _py.get("conf")
            _cfs = f"{_cf:.3f}" if isinstance(_cf, (int, float)) else "?"
            _ad = "ADAPTED" if _py.get("adapted") else "held"
            _ptxt += f"  {_py['worm']}->{_py['rec']} conf={_cfs} {_ad}"
        _pa = _py.get("age")
        if isinstance(_pa, (int, float)):
            _ptxt += f"  {_pa:.0f}m ago"
        L(_fit(_ptxt, width))

    # --- chain-of-thought corpus liveness ---
    _th = data.get("thoughts")
    if _th:
        _ta = _th.get("age")
        _txt = f"THOUGHT {_th.get('n', 0)} traces {_th.get('ok', 0)} ok"
        _txt += f"  {_ta:.0f}m ago" if isinstance(_ta, (int, float)) else "  --"
        if isinstance(_ta, (int, float)) and _ta > 1440:
            _txt += f" {C_RED}STALE{C_RST}"
        L(_fit(_txt, width))

    # --- meta-director: who trains next + top tracks by win rate ---
    try:
        import meta_director as _md
    except Exception:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import meta_director as _md
        except Exception:
            _md = None
    if _md is not None:
        try:
            _ledger = _md.load_ledger(TRAIN_ALGOS)
            _slot = int(time.time()) // 300
            _pick = _md.pick(_slot, _ledger, TRAIN_ALGOS)
            _top = _md.top_tracks(_ledger, TRAIN_ALGOS, 3)
            L(_fit(f"DIR    next {C_CYN}{_pick}{C_RST}  "
                   + "  ".join(f"{a}({m:.2f})" for a, m in _top), width))
        except Exception:
            L(_fit("DIR    director unavailable", width))
    else:
        L(_fit("DIR    director unavailable", width))

    # --- training: fixed compact grid (row count depends only on width) ---
    tr = data.get("training", {})
    nxt = tr.get("next_rotation", "?")
    L(_fit(f"TRAIN  next slot {C_CYN}{nxt}{C_RST}   (reward ^/v vs previous run, yellow = new)", width))
    for r_ in _training_grid(tr, width):
        L(r_)

    # --- run counts per track (cycles each algo has completed) ---
    _counts = tr.get("counts") or {}
    if _counts:
        _ordered = sorted(_counts.items(), key=lambda kv: -kv[1])
        _total = sum(_counts.values())
        L(_fit(f"RUNS   {_total} total  " + "  ".join(f"{_algo_name(a)}x{n}" for a, n in _ordered), width))

    # --- recent runs: last few training cycles across all tracks ---
    _rr = data.get("recent_runs") or []
    if _rr:
        _cells = []
        for _r in _rr[:4]:
            _rw = f"{_r['reward']:.1f}" if isinstance(_r.get("reward"), (int, float)) else "?"
            _age = f"{_r['age']:.0f}m" if isinstance(_r.get("age"), (int, float)) else "?"
            _cells.append(f"{_r.get('algo', '?')} {_rw} ({_age} ago)")
        L(_fit("LAST   " + "   ".join(_cells), width))

    # --- promotion file: hyperparams actually live in production ---
    _pr = data.get("promo")
    if _pr:
        _streak, _streak_n = "", 0
        for _e2 in data.get("evo") or []:
            if str(_e2.get("fit", "")).startswith("v3") and _e2.get("beats"):
                _streak_n += 1
            else:
                break
        if _streak_n:
            _streak = f"  streak {_streak_n}/3"
        L(_fit(f"PROMO  lr={_pr['lr']} eps={_pr['eps']} disc={_pr['disc']} "
               f"fit={_pr.get('fit')} wins={_pr.get('wins')} {_pr.get('age')}m ago{_streak}", width))
    else:
        L(_fit("PROMO  no promoted params file (defaults in effect)", width))

    # --- tuner bests: what the specialist tracks found ---
    _tn = data.get("tuners") or {}
    _dt = _tn.get("dreamtune") or {}
    if _dt:
        _dp = _dt.get("params") or {}
        _cf = _dp.get("creativity_factor")
        _sc = _dp.get("scenario_count")
        _dtxt = (f"TUNE   dreamtune cf={_cf:.2f} sc={_sc:.0f}" if isinstance(_cf, (int, float)) else "TUNE   dreamtune ?")
        if isinstance(_dt.get("fit"), (int, float)):
            _dtxt += f" fit={_dt['fit']:.3f}"
        L(_fit(_dtxt, width))
    _wb = _tn.get("wormga") or {}
    _board = _wb.get("board") or {}
    if _board:
        _ab = {"SYSTEMZ": "SYS", "PROCESSZ": "PRC", "DISKZ": "DSK",
               "NETWORXZ": "NET", "CLOCKZ": "CLK", "HEALTHZ": "HLT"}
        _cells = []
        for _w in ("SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ", "CLOCKZ", "HEALTHZ"):
            _e = _board.get(_w) or {}
            _sr = _e.get("success_rate")
            _cells.append(f"{_ab.get(_w, _w[:3])} {int(round(_sr * 100))}%" if isinstance(_sr, (int, float)) else f"{_ab.get(_w, _w[:3])} ?")
        _wfit = f" fit={_wb['fit']:.3f}" if isinstance(_wb.get("fit"), (int, float)) else ""
        # These are the WORMGA leaderboard's `success_rate` values from the last
        # genetic run -- an evaluation of those worms, NOT their live health. The
        # line was labelled "WORMS", so "SYS 100%" sat two rows above "SRV DOWN"
        # and read as a contradiction. It also carried no age, unlike every other
        # line here, although the board is only as current as that GA run.
        _wage = _wb.get("age")
        _wage_txt = (f", {_wage / 60:.0f}h old"
                     if isinstance(_wage, (int, float)) else "")
        L(_fit("WBOARD " + "  ".join(_cells) + _wfit
               + f"  (WORMGA eval{_wage_txt})", width))

    # --- cycle processes: scheduler tasks proven alive by their own logs ---
    # Per-task freshness thresholds (followup: maintenance.log is nightly —
    # the 15-min rule would paint it permanently red, so maint gets 25h).
    _CYCLE_OK_MIN = {"maint": 1500.0}
    _cy = data.get("cycles") or []
    if _cy:
        _bits = []
        for _c in _cy:
            _a = _c.get("age")
            _lim = _CYCLE_OK_MIN.get(_c.get("label"), 15.0)
            if isinstance(_a, (int, float)) and _a >= 120:
                _txt = f"{_c.get('label')} {_a / 60:.1f}h"
            else:
                _txt = f"{_c.get('label')} {_a:.0f}m" if isinstance(_a, (int, float)) else f"{_c.get('label')} --"
            _bits.append((_txt, _a is not None and _a < _lim))
        L(_fit("CYCLES " + "   ".join(f"{C_GRN}{t}{C_RST}" if _ok else f"{C_RED}{t}{C_RST}" for t, _ok in _bits), width))

    # --- store size + write rate + alert load + last trim ---
    _st = data.get("store")
    if _st:
        _mb = f"{_st['mb']}MB" if _st.get("mb") is not None else "?"
        _al = data.get("alerts") or {}
        _atxt = f"  alerts {_al.get('per_hr', 0)}/hr ({_al.get('crit', 0)} crit)" if _al.get("per_hr") else ""
        _cp = data.get("caps") or {}
        _ctxt = f"  trim -{_cp['last_trim']:,}" if isinstance(_cp.get("last_trim"), int) else ""
        # backup age (2026-09-10): the restore story on one segment.
        # STALE past 30h = nightly backup missed.
        _btxt = ""
        try:
            import sys as _sys2
            _sp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "src")
            if _sp not in _sys2.path:
                _sys2.path.insert(0, _sp)
            from CORE.data_retention import newest_backup as _nb
            _bk = _nb()
            if _bk.get("exists"):
                _btxt = f"  bkup {_bk.get('age_h', '?')}h"
                if isinstance(_bk.get("age_h"), (int, float)) and _bk["age_h"] > 30:
                    _btxt += f" {C_RED}STALE{C_RST}"
            else:
                _btxt = f"  {C_RED}bkup none{C_RST}"
        except Exception:
            pass
        _tr = data.get("triage")
        _trtxt = ""
        if _tr:
            _ago = f"{_tr['age']:.0f}h" if isinstance(_tr.get("age"), (int, float)) else "?"
            _top = _tr.get("top") or {}
            if _top.get("count"):
                _trtxt = (f"  triage {_tr.get('clusters', 0)}c {_top['count']}x "
                          f"{str(_top.get('source', '?'))[:12]} {_ago}")
            else:
                _trtxt = f"  triage {_tr.get('clusters', 0)}c {_ago}"
            if isinstance(_tr.get("age"), (int, float)) and _tr["age"] > 30:
                _trtxt += f" {C_RED}STALE{C_RST}"
        # DB-monitor verdict folded into STORE (0 extra rows): the flag
        # that gates metacog's evidence and the alert pool, live. Red
        # UNHEALTHY is exactly what "everything works" must not show.
        _dm = data.get("dbmon")
        _dm_txt = ""
        if _dm:
            if _dm.get("healthy"):
                _dm_txt = f"{C_GRN}DB OK{C_RST} err{_dm.get('err7d', '?')}/7d"
            else:
                _dm_txt = f"{C_RED}DB UNHEALTHY{C_RST} err{_dm.get('err7d', '?')}/7d"
            if not _dm.get("vac"):
                _dm_txt += " vac-off"
        L(_fit(f"STORE  {_dm_txt}  {_mb} {(_st.get('rows') or 0):,} rows  "
               f"+{(_st.get('per_hr') or 0):,}/hr{_atxt}{_ctxt}{_trtxt}{_btxt}", width))

    # --- DB detail (height-gated: the snapshot facts behind DB OK) ---
    _dm = data.get("dbmon")
    if _dm and height >= 37:
        _seg = []
        _a = _dm.get("age")
        _seg.append(f"snap {_a:.0f}m ago" if isinstance(_a, (int, float)) else "snap ?")
        if isinstance(_dm.get("size"), (int, float)):
            _seg.append(f"{_dm['size']:.0f}MB")
        if isinstance(_dm.get("grow24h"), int):
            _seg.append(f"+{_dm['grow24h']:,}/24h")
        if isinstance(_dm.get("warn7d"), int):
            _seg.append(f"warn {_dm['warn7d']}/7d")
        _seg.append("vacuum RUNNING" if _dm.get("vac") else "vacuum off")
        L(_fit("DBMON  " + "  ".join(_seg), width))
    # error triage top cluster (the largest source of the err7d count)
    _tr = data.get("triage")
    if _tr and height >= 37:
        _top = _tr.get("top") or {}
        _src = str(_top.get("source", "?"))[:24] if _top.get("count") else "?"
        _ago = f"{_tr['age']:.0f}h ago" if isinstance(_tr.get("age"), (int, float)) else ""
        L(_fit(f"ERRS   {_tr.get('errors', 0)} rows/7d in {_tr.get('clusters', 0)} clusters  "
               + (f"top {_src} x{_top.get('count')} {_ago}" if _top.get("count") else _ago), width))

    # --- inference servers ---
    _sv = data.get("servers") or []
    if _sv:
        _bits = []
        for _s in _sv:
            if _s.get("ok"):
                _bits.append(f":{_s['port']} ok {_s.get('ms')}ms")
            else:
                _bits.append(f"{C_RED}:{_s['port']} DOWN{C_RST}")
        L(_fit("SRV    " + "   ".join(_bits), width))

    # --- api keys: which inference providers can actually be called ---
    _ky = data.get("keys") or []
    if _ky:
        L(_fit("KEYS   " + "  ".join(
            f"{C_GRN}{_k['name']}+{C_RST}" if _k.get("ok") else f"{C_RED}{_k['name']}-{C_RST}"
            for _k in _ky), width))

    # --- llm scoreboard: measured tok/s per local model ---
    _lm = data.get("llm") or []
    if _lm:
        _cells = []
        for _m in _lm:
            _nm = str(_m.get("model", "?"))
            for _cut in ("-it-", "-instruct", "-uncensored", ".gguf", "_GGUF"):
                _nm = _nm.split(_cut)[0]
            _nm = _nm.split("/")[-1].split("\\")[-1][:14]
            _gp = f"{_m['gen']:.0f}/s" if isinstance(_m.get("gen"), (int, float)) else "?"
            _pp = f"{_m['prompt']:.0f}" if isinstance(_m.get("prompt"), (int, float)) else "?"
            _th = "T" if (_m.get("think") or 0) > 200 else ""
            _cells.append(f"{_nm} {_pp}/{_gp}{_th}")
        L(_fit("MODELS " + "   ".join(_cells), width))

    # --- repo audit + mutation score (file reads, absent until first run) ---
    _au = data.get("audit")
    _mu = data.get("mutants")
    if _au or _mu:
        _asegs = []
        if _au:
            # meaningful colors: green = healthy, yellow = watch, red = act.
            _cb = _au.get("compile_bad", 0) or 0
            _cbtxt = f"{C_GRN}compile ok{C_RST}" if not _cb else f"{C_RED}compile {_cb} bad{C_RST}"
            _bare = _au.get("bare")
            if isinstance(_bare, int):
                _bcol = C_GRN if _bare == 0 else (C_YEL if _bare <= 5 else C_RED)
                _btxt = f"{_bcol}bare={_bare}{C_RST}"
            else:
                _btxt = "bare=?"
            _unc = _au.get("uncovered")
            if isinstance(_unc, int):
                _ucol = C_GRN if _unc < 50 else (C_YEL if _unc < 200 else C_RED)
                _utxt = f"{_ucol}uncov={_unc}{C_RST}"
            else:
                _utxt = "uncov=?"
            _asegs.append(f"audit {_au.get('files', '?')}f {_cbtxt} {_btxt} {_utxt}")
        if _mu and isinstance(_mu.get("score"), (int, float)):
            _ms = _mu["score"]
            _mcol = C_GRN if _ms >= 0.7 else (C_YEL if _ms >= 0.3 else C_RED)
            _asegs.append(f"mutants {_mu.get('target', '?')} "
                          f"{_mcol}{_mu.get('killed', '?')}/{_mu.get('mutants', '?')} "
                          f"({_ms:.2f}){C_RST}")
        L(_fit("AUDIT  " + "  ".join(_asegs), width))
        if height >= 36 and _mu and _mu.get("at"):
            _dtxt = f"last audit {_au.get('at', '?') if _au else '?'}  last mutants {_mu.get('at', '?')}"
            # survived mutants shift into the monitor: each is a ready-made
            # test case, red until the suite kills it.
            _surv = [s for s in (_mu.get("survived") or []) if isinstance(s, dict)]
            if _surv:
                _slist = " ".join(f"{s.get('kind', '?')}@{s.get('site', '?')}" for s in _surv[:6])
                _dtxt += f"  {C_RED if len(_surv) > 3 else C_YEL}survived: {_slist}{C_RST}"
            L(_fit(f"       {_dtxt}", width))

    # --- alerts + verdict: fixed lines, only what fires ---
    if any_stale:
        L(_fit(f"{C_RED}STALE  {', '.join(f['label'] for f in data['freshness'] if not f['ok'])} "
                 f">15 min - check Task Scheduler.{C_RST}", width))
    if evo and hard_stale is not None and hard_stale > 24:
        L(_fit(f"{C_RED}HARDPOOL  no fresh critical-alert examples for {hard_stale:.0f}h - "
               f"evolution is re-mastering frozen rows.{C_RST}", width))
    bl = bottom_line(data)
    L(_fit(f"    {C_BLD}Verdict:{C_RST} {bl}", width))

    return "\n".join(lines)


def append_log(data, now):
    """Append one refresh to the rolling log file (rotate at 1 MB)."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX:
            bak = LOG_PATH + ".1"
            if os.path.exists(bak):
                os.remove(bak)
            os.rename(LOG_PATH, bak)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"=== {now.isoformat()} ===\n")
            for fr in data["freshness"]:
                a = fr["age"] if fr["age"] is not None else -1
                f.write(f"  {fr['label']:<18} age {a:>6} min  {'OK' if fr['ok'] else 'STALE'}\n")
            _m = data.get("mesh") or {}
            f.write(f"  mesh               cycle {_m.get('at')} age {_m.get('age')} min  "
                    f"ok {_m.get('ok')}/{(_m.get('ok') or 0) + (_m.get('fail') or 0)}  "
                    f"natures {_m.get('natures')}  failed {_m.get('failed')}  "
                    f"cpu {_m.get('cpu')} mem {_m.get('mem')} db {_m.get('db_mb')}\n")
            evo = data["evo"]
            if evo:
                e = evo[0]
                f.write(f"  evolution best {e['best']:.3f} eps {e['genes'].get('epsilon', 0):.4f} "
                        f"traj {[round(x, 2) for x in e['traj']]} [{e['fit']}]"
                        + (f" info={e['info']} alert={e['alert']}"
                           if e["info"] is not None or e["alert"] is not None else "")
                        + "\n")
            s = data["strategy"]
            f.write(f"  strategy {s['value']}  steer24h {data['steer_24h']}  "
                    f"activity {data['activity']}\n")
            tr = data.get("training", {})
            scored = {}
            if tr.get("counts"):
                scored = {a: r["eval_mean_reward"] for a in TRAIN_ALGOS
                           for r in (tr.get(a) or [])[:1] if r.get("eval_mean_reward") is not None}
            f.write("  train " + ", ".join(f"{a}={v:.1f}" for a, v in sorted(scored.items()))
                    + f"  next={tr.get('next_rotation')}\n")
            lk = data.get("links") or {}
            if lk.get("by_hook"):
                f.write("  links " + ", ".join(
                    f"{h.split(':')[-1]}={v['ok']}/{v['n']}"
                    for h, v in sorted(lk["by_hook"].items()))
                    + f"  latest_age={age_min(lk.get('latest'))}min\n")
            for tag, msg in explain(data):
                f.write(f"  {tag:<6} {msg}\n")
            f.write(f"  >> {bottom_line(data)}\n")
    except Exception as e:
        print(f"log write failed: {e}", file=sys.stderr)


def beep_if_high(data):
    """Beep when a new all-time-best fitness appears this session."""
    global _BEST_SEEN
    if not data["evo"]:
        return False
    best = data["evo"][0]["best"]
    if _BEST_SEEN is None or best > _BEST_SEEN:
        is_new = _BEST_SEEN is not None
        _BEST_SEEN = best
        if is_new:
            sys.stdout.write("\a")  # console bell
            sys.stdout.flush()
            return True
    return False


def _paint_in_place(frame_lines):
    """Redraw the dashboard in place: no full-screen clear, no scrolling.

    Moves the cursor home, overwrites each row (erasing to end-of-line so a
    shorter row never leaves stale text), then erases only the rows below the
    frame. Never emits a trailing newline past the bottom row, so the console
    never scrolls -- everything stays on one screen and updates in place.
    """
    size = _sh.get_terminal_size((100, 30))
    # Use the FULL window height: the width margin in render() already makes a
    # wrapped line impossible, which was the original reason to keep the last
    # row free. Reserving it silently dropped the final line -- the VERDICT,
    # the one line the wallboard exists to show.
    max_rows = max(4, size.lines)
    rows = frame_lines[:max_rows]          # never exceed the visible window
    if len(rows) < max_rows:
        rows = rows + [""] * (max_rows - len(rows))  # wipe any longer old frame

    # Reset first: without it the frame starts in whatever colour the previous
    # one ended in, so the banner can change colour on the second paint.
    out = ["\033[H", C_RST]                # home, then clear any inherited SGR
    for i, line in enumerate(rows):
        if i:
            out.append("\r\n")
        out.append(line)
        out.append(C_RST)                  # never let one row's colour bleed
        out.append("\033[K")               # erase to end of this line
    out.append("\033[J")                   # erase everything below the frame
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def _pid_alive(pid):
    """Windows liveness check (2026-09-09 fix): os.kill(pid, 0) is not
    supported on Windows (WinError 87) — it always raised, so every guard
    using it never blocked. OpenProcess is the correct probe."""
    try:
        import ctypes
        _SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(_SYNCHRONIZE, False, int(pid))
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def _single_instance():
    """Exit if another live monitor is already running (2026-09-09).

    Two painters would fight over consoles and double every log line.
    Stale PID files are stolen (crash-safe), like dream_cycle's guard.
    """
    lock = os.path.join(tempfile.gettempdir(), "azazael_live_monitor.lock")
    try:
        with open(lock, encoding="utf-8") as f:
            old = int((f.read() or "0").strip() or 0)
        if old and _pid_alive(old):
            print(f"Another live monitor is already running (PID {old}) - not starting a second one.",
                  file=sys.stderr)
            return False
    except (OSError, ValueError):
        pass
    try:
        with open(lock, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    return True


def main():
    global REFRESH, LOG_PATH
    ap = argparse.ArgumentParser(description="Persistent Azazael live dashboard")
    ap.add_argument("--refresh", type=int, default=30)
    ap.add_argument("--log", default=LOG_PATH)
    ap.add_argument("--once", action="store_true",
                    help="render a single frame to stdout and exit (for screenshots/reports)")
    ap.add_argument("--no-color", action="store_true",
                    help="strip ANSI colors (pair with --once for clean captures)")
    args = ap.parse_args()
    REFRESH = max(5, args.refresh)
    LOG_PATH = os.path.abspath(args.log)
    if args.no_color:
        global C_RED, C_GRN, C_YEL, C_CYN, C_BLD, C_ORG, C_RST
        C_RED = C_GRN = C_YEL = C_CYN = C_BLD = C_ORG = C_RST = ""

    if args.once:
        # read-only snapshot: bypass the single-instance lock so a capture
        # never fights the running wallboard, and skip the rolling log.
        con = db_connect()
        if con is None:
            print(f"FATAL: cannot open {DB}", file=sys.stderr)
            return 2
        try:
            print(render(collect(con), datetime.now()))
        except Exception as e:
            print(f"render error: {e}", file=sys.stderr)
            return 1
        return 0

    if not _single_instance():
        return 2

    con = db_connect()
    if con is None:
        print(f"FATAL: cannot open {DB}", file=sys.stderr)
        return 2

    first = True
    while True:
        now = datetime.now()
        try:
            data = collect(con)
            text = render(data, now)
            new_high = beep_if_high(data)
            if new_high:
                text = text.replace(
                    f"  AZAZAEL LIVE MONITOR   {now.strftime('%Y-%m-%d %H:%M:%S')}",
                    f"  AZAZAEL LIVE MONITOR   {now.strftime('%Y-%m-%d %H:%M:%S')}"
                    f"{C_YEL}   *** NEW BEST {_BEST_SEEN:.3f} ***{C_RST}")
            append_log(data, now)
        except Exception as e:
            text = f"render error: {e}"
            con = db_connect()  # reconnect on next loop

        if first:
            # clear once on startup so the first frame starts from a clean screen
            sys.stdout.write("\033[2J\033[H")
            first = False
        _paint_in_place(text.split("\n"))
        time.sleep(REFRESH)


if __name__ == "__main__":
    sys.exit(main())