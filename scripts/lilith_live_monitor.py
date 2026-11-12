#!/usr/bin/env python3
"""lilith_live_monitor.py - live wallboard for the xenomorph hive + mesh fabric.

Reads the hot Windows DB (~/.azazael/memory_store.db) and the worm registry,
refreshes every REFRESH seconds, forever. Built to run inside a console window
that stays open (see open_lilith_live_monitor.bat).

Shows, live:
  - LILITH: live election claim (role/lease/genome), host capability
  - HIVE: all 7 lifecycle worms with REAL 24h executions + success + last age
  - MESH: all 8 fabric natures with REAL 24h runs (ok/fail) from the DB
  - FRESH: event freshness ages (red when stale)
  - Verdict: one line; alerts only when firing

Fixed frame, truncate-never-wrap: every line is _fit-ed to the window width.
Detail lives in the rolling log file, not on screen.

Stdlib only. ASCII-only output (console is CP437/cp1252: no emoji, no box art).

Usage:
  python scripts/lilith_live_monitor.py [--refresh 15] [--once]
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

DB = os.environ.get("AZAZAEL_MEMORY_DIR",
                     os.path.join(os.path.expanduser("~"), ".azazael"))
if not DB.endswith(".db"):
    DB = os.path.join(DB, "memory_store.db")

REFRESH = 15
LOG_PATH = os.path.join(_ROOT, "logs", "lilith_live_monitor.log")
LOG_MAX = 1_000_000
STATE_PATH = os.path.join(_ROOT, "state", "lilith_hive.json")

# ANSI colors (Windows 10+ consoles support these)
C_RED = "\x1b[91m"
C_GRN = "\x1b[92m"
C_YEL = "\x1b[93m"
C_CYN = "\x1b[96m"
C_MAG = "\x1b[95m"
C_BLD = "\x1b[1m"
C_RST = "\x1b[0m"

# CLI parity (2026-09-14): the other three wallboards (azazael, ex machina,
# AOA) all accept --no-color; this one did not, so the only board covering the
# hive could not be driven headlessly or diffed without ANSI noise.
_COLOR_KEYS = ("C_RED", "C_GRN", "C_YEL", "C_CYN", "C_MAG", "C_BLD", "C_RST")


def _no_color() -> None:
    """Strip every ANSI constant in this module (matches aoa_monitor._no_color)."""
    g = globals()
    for key in _COLOR_KEYS:
        g[key] = ""

XENO_WORMS = ("OvomorphWorm", "FacehuggerWorm", "ChestbursterWorm", "DroneWorm",
              "PraetorianWorm", "Lilith", "ResinWorm")
MESH_NATURES = ("mesh", "synapse", "consensus", "engram", "federated",
                "servemesh", "immune", "xenomorph")


def _fit(s, width):
    """Truncate to window width; never wrap, never scroll."""
    plain = re.sub("\x1b\\[[0-9;]*m", "", s)
    if len(plain) <= width:
        return s
    return plain[:max(0, width)]


def _age(ts):
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return round((datetime.now(timezone.utc) - t).total_seconds() / 60.0, 1)
    except Exception:
        return None


def _age_txt(a):
    if a is None:
        return "--"
    if a < 0:
        return "--"
    if a < 60:
        return "%dm" % int(a)
    if a < 1440:
        return "%.1fh" % (a / 60.0)
    return "%.1fd" % (a / 1440.0)


def db_connect():
    for _ in range(3):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
            con.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
            return con
        except Exception:
            time.sleep(1)
    tmp = os.path.join(tempfile.gettempdir(), "lilith_mon.db")
    for _ in range(3):
        try:
            shutil.copy2(DB, tmp)
            con = sqlite3.connect("file:%s?mode=ro" % tmp, uri=True)
            con.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
            return con
        except Exception:
            time.sleep(2)
    return None


def _parse(d):
    try:
        return json.loads(d) if isinstance(d, str) else (d or {})
    except Exception:
        return {}


def collect():
    """Gather all wallboard data. Never raises."""
    d = {"at": time.strftime("%H:%M:%S"), "errors": []}
    con = db_connect()
    if con is None:
        d["errors"].append("db unreachable")
        con = None

    # --- per-worm executions, last 24h ---
    hive = {w: {"n": 0, "ok": 0, "last": None} for w in XENO_WORMS}
    other_worms_24h = 0
    try:
        rows = con.execute(
            "SELECT timestamp, data FROM memories "
            "WHERE event_type='clusterwormz_worm_executed' "
            "ORDER BY id DESC LIMIT 4000").fetchall() if con else []
        for ts, raw in rows:
            a = _age(ts)
            if a is None or a > 1440:
                continue
            dd = _parse(raw)
            w = dd.get("worm")
            if w in hive:
                hive[w]["n"] += 1
                if dd.get("success"):
                    hive[w]["ok"] += 1
                if hive[w]["last"] is None:
                    hive[w]["last"] = a
            else:
                other_worms_24h += 1
    except Exception as e:
        d["errors"].append("hive q: %s" % str(e)[:60])
    d["hive"] = hive
    d["other_worms_24h"] = other_worms_24h

    # --- per-nature runs, last 24h ---
    mesh = {n: {"runs": 0, "ok": 0, "fail": 0, "last": None} for n in MESH_NATURES}
    try:
        rows = con.execute(
            "SELECT timestamp, data FROM memories "
            "WHERE event_type='nature_cluster_executed' "
            "ORDER BY id DESC LIMIT 1500").fetchall() if con else []
        for ts, raw in rows:
            a = _age(ts)
            if a is None or a > 1440:
                continue
            dd = _parse(raw)
            n = dd.get("nature")
            if n in mesh:
                mesh[n]["runs"] += 1
                mesh[n]["ok"] += int(dd.get("success") or 0)
                mesh[n]["fail"] += int(dd.get("failures") or 0)
                if mesh[n]["last"] is None:
                    mesh[n]["last"] = a
    except Exception as e:
        d["errors"].append("mesh q: %s" % str(e)[:60])
    d["mesh"] = mesh

    # --- freshness of key streams ---
    d["fresh"] = {}
    if con:
        for et, label in (("nature_cluster_executed", "nature"),
                          ("clusterwormz_worm_executed", "wormexec"),
                          ("spawn_selected", "spawn"),
                          ("metacluster_orchestrated", "meta")):
            try:
                r = con.execute(
                    "SELECT timestamp FROM memories WHERE event_type=? "
                    "ORDER BY id DESC LIMIT 1", (et,)).fetchone()
                d["fresh"][label] = _age(r[0]) if r else None
            except Exception:
                d["fresh"][label] = None
    if con:
        try:
            con.close()
        except Exception:
            pass

    # --- advancement honesty: funnel deaths, oracle age, cooldown, trend ---
    # The hive lied by omission while promotions deadlocked for a day+.
    # This block names the killer gate every frame. Never raises.
    adv = {"prom24": 0, "die_pareto": 0, "die_holdout": 0, "die_oracle": 0,
           "sprt24": 0, "best_now": None, "best_24h_ago": None}
    # Own read-only connection: this block must not depend on the shared `con`
    # (already closed above). See db_connect() for the lock fallback.
    _ac = db_connect()
    try:
        if _ac:
            rows = _ac.execute(
                "SELECT timestamp, data FROM memories "
                "WHERE event_type='evolution_result' "
                "ORDER BY id DESC LIMIT 400").fetchall()
            for ts, raw in rows:
                a = _age(ts)
                if a is None or a > 1440:
                    continue
                dd = _parse(raw)
                if dd.get("promoted"):
                    adv["prom24"] += 1
                if (dd.get("sprt") or {}).get("decision") != "promote":
                    continue
                adv["sprt24"] += 1
                pg = dd.get("pareto_gate") or dd.get("pareto") or {}
                ho = dd.get("holdout")
                ho_pass = ho if isinstance(ho, bool) else (ho or {}).get("passed")
                if pg.get("passed") is not True:
                    adv["die_pareto"] += 1
                elif not ho_pass:
                    adv["die_holdout"] += 1
                else:
                    adv["die_oracle"] += 1  # passed all recorded gates, still unpromoted
                tr = dd.get("trajectory") or []
                if tr:
                    b = max(tr)
                    if adv["best_now"] is None:
                        adv["best_now"] = b
                    adv["best_24h_ago"] = b
    except Exception as e:
        d["errors"].append("adv q: %s" % str(e)[:60])
    finally:
        try:
            if _ac:
                _ac.close()
        except Exception:
            pass
    d["adv"] = adv
    try:
        op = os.path.join(_ROOT, "state", "fitness_oracle.json")
        if os.path.exists(op):
            adv["oracle_age_h"] = round((time.time() - os.path.getmtime(op)) / 3600.0, 1)
            try:
                with open(op, encoding="utf-8") as f:
                    om = json.load(f)
                _oer = ((om.get("meta") or {}).get("eras")
                        or om.get("eras") or [])
                adv["oracle_eras"] = len(_oer)
            except Exception:
                pass
        else:
            adv["oracle_age_h"] = None
        cp = os.path.join(_ROOT, "state", "holdout_cooldown.json")
        if os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as f:
                    cd = json.load(f)
                adv["cooldown"] = {"margin": cd.get("margin"),
                                   "age_h": round((time.time() - os.path.getmtime(cp)) / 3600.0, 1)}
            except Exception:
                pass
    except Exception:
        pass
    try:
        from CORE.worm_registry import discover_worms, WORM_REGISTRY, get_all_categories
        discover_worms()
        d["reg_total"] = len(WORM_REGISTRY)
        d["reg_cats"] = len(get_all_categories())
        d["spawns"] = {w: (WORM_REGISTRY.get(w, {}).get("spawn_count", 0)) for w in XENO_WORMS}
    except Exception as e:
        d["errors"].append("registry: %s" % str(e)[:60])

    # --- live Lilith claim + host ---
    try:
        from CORE.WORMS.expansion import xenomorph as X
        from CORE.WORMS.expansion import mesh as M
        d["env"] = X._local_env()
        d["describe"] = M.describe_env()
        d["lilith"] = X.Lilith().claim(peers=None)
    except Exception as e:
        d["errors"].append("lilith: %s" % str(e)[:80])

    # --- G: archive + WSL distros (best effort, never blocks) ---
    d["g_up"] = os.path.exists(r"G:\source-of-truth") or os.path.exists("/mnt/g")
    d["wsl"] = None
    if os.name == "nt":
        try:
            import subprocess
            # wsl.exe speaks UTF-16LE regardless of console codepage; decoding
            # as text mangles every name with NUL separators.
            r = subprocess.run(["wsl", "--list", "--quiet"], capture_output=True,
                               timeout=8)
            try:
                out = r.stdout.decode("utf-16-le", errors="replace")
            except Exception:
                out = r.stdout.decode("utf-8", errors="replace")
            names = [l.replace("\x00", "").strip() for l in out.splitlines()]
            d["wsl"] = [n for n in names if n]
        except Exception:
            pass

    # --- delta vs last frame (trend arrows) ---
    d["delta_exec"] = None
    try:
        prev = None
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, encoding="utf-8") as f:
                prev = json.load(f)
        total = sum(v["n"] for v in hive.values())
        if isinstance(prev, dict) and prev.get("exec24") is not None:
            d["delta_exec"] = total - int(prev["exec24"])
        try:
            os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump({"at": d["at"], "exec24": total,
                           "role": (d.get("lilith") or {}).get("role")}, f)
        except Exception:
            pass
    except Exception:
        pass
    return d


def _status(ok, word):
    return (C_GRN if ok else C_RED) + word + C_RST


def render(data, width):
    L = []
    lil = data.get("lilith") or {}
    role = lil.get("role", "?")
    role_col = C_MAG + C_BLD + role + C_RST if "queen" in str(role) else (
        C_YEL + str(role) + C_RST)
    d = data.get("describe") or {}
    head = "-- LILITH LIVE -- %s %s genome=%s lease=%s | %s cpu=%s mem=%sGB ft=%s %s" % (
        lil.get("name", "Lilith"), role_col, lil.get("genome", "-"),
        str(lil.get("lease_until", "-"))[11:19],
        data.get("env", "?"), d.get("cpu", "?"), d.get("mem_gb", "?"),
        "Y" if d.get("ft") else "n",
        _status(bool(data.get("g_up")), "G:UP" if data.get("g_up") else "G:DOWN"))
    L.append(head)

    # HIVE rows: executions 24h + success + last age + spawns from the registry.
    # The column was labelled `reg=` and invariably read as "registered". It is
    # a SPAWN count: all seven are registered under cat `xenomorph`, so `reg=0`
    # beside an idle row looked like a fleet-wide registration failure when the
    # only true reading is "this process has not spawned it".
    L.append(" HIVE 24h exec/ok  last-age  spawns:")
    for w in XENO_WORMS:
        h = data.get("hive", {}).get(w, {})
        n, ok = h.get("n", 0), h.get("ok", 0)
        cell = _status(n > 0, "%3d" % n) + "/%d" % ok
        sp = (data.get("spawns") or {}).get(w, 0)
        L.append("  %-15s %s  last=%-5s spawns=%s" % (
            w, cell, _age_txt(h.get("last")), sp))
    L.append("  (spawns = this process's spawn count, NOT registration;"
             " all 7 are registered)")
    dx = data.get("delta_exec")
    if dx is not None and dx:
        L.append("  delta since last frame: %+d exec" % dx)

    # MESH rows: runs 24h ok/fail + last age
    L.append(" MESH 24h runs ok/fail last:")
    for n in MESH_NATURES:
        m = (data.get("mesh") or {}).get(n, {})
        runs = m.get("runs", 0)
        cell = _status(runs > 0, "%4d" % runs) + " %d/%d" % (m.get("ok", 0), m.get("fail", 0))
        flag = ""
        if m.get("fail", 0) > 0:
            flag = " " + C_RED + "FAIL" + C_RST
        L.append("  %-10s %s  last=%-5s%s" % (n, cell, _age_txt(m.get("last")), flag))

    # FRESHNESS
    fr = data.get("fresh", {})
    bits = []
    for label in ("nature", "wormexec", "spawn", "meta"):
        a = fr.get(label)
        stale = (a is None) or (a > 60)
        bits.append("%s=%s%s" % (label, _age_txt(a),
                                 "" if not stale else "(STALE)"))
    L.append(" FRESH: " + "  ".join(bits) +
             "  | other-worm exec 24h: %d" % data.get("other_worms_24h", 0) +
             "  | reg: %s/%s" % (data.get("reg_total", "?"), data.get("reg_cats", "?")))
    if data.get("wsl") is not None:
        L.append(" WSL: " + (", ".join(data["wsl"]) if data["wsl"] else "(none)"))

    # ADVANCEMENT honesty: funnel deaths by gate, oracle age, cooldown, trend.
    # This board stayed green through a day-long promotion deadlock; never again.
    av = data.get("adv", {})
    bn, bo = av.get("best_now"), av.get("best_24h_ago")
    trend = "--"
    if bn is not None and bo is not None:
        trend = "%+.2f" % (bn - bo)
    oage = av.get("oracle_age_h")
    L.append(" ADVANCE 24h: sprt=%d prom=%d die(pareto=%d holdout=%d post-gate=%d)" % (
        av.get("sprt24", 0), av.get("prom24", 0), av.get("die_pareto", 0),
        av.get("die_holdout", 0), av.get("die_oracle", 0)) +
        "  best %s->%s (%s)" % (bo, bn, trend) +
        "  oracle=%s%s" % (_age_txt((oage or 0) * 60) if oage is not None else "--",
                           " STALE" if (oage or 0) > 6 else ""))
    cd = av.get("cooldown")
    if cd:
        L.append("  holdout cooldown: margin=%s age=%s" % (
            cd.get("margin"), _age_txt((cd.get("age_h") or 0) * 60)))

    # VERDICT: one line, alerts only when firing.
    #
    # 2026-09-14 fix: hive liveness used to be keyed on
    # `mesh["xenomorph"]["last"]` — the whole xenomorph NATURE, which holds 8
    # worms including RedQueenWorm. So any one of them running made the verdict
    # say "hive live" while the seven lifecycle worms named in the HIVE section
    # above could be entirely idle (observed: all seven at 0/0, reg=0, verdict
    # still OK). Liveness is now measured on exactly the worms the section
    # displays, and the OK line below reports what was checked instead of
    # asserting it.
    fails = sum((m.get("fail", 0) or 0) for m in (data.get("mesh") or {}).values())
    hive_rows = data.get("hive") or {}
    hive_n = len(XENO_WORMS)
    hive_ran = sum(1 for w in XENO_WORMS
                   if (hive_rows.get(w, {}).get("n", 0) or 0) > 0)
    alerts = []
    if fails > 0:
        alerts.append("%d mesh failures/24h" % fails)
    if not data.get("g_up"):
        alerts.append("G: archive DOWN")
    if role == "follower":
        alerts.append("Lilith is follower (bare peer or incapable host)")
    if hive_ran == 0:
        alerts.append("hive idle: 0 of %d lifecycle worms executed in 24h" % hive_n)
    elif hive_ran < hive_n:
        alerts.append("hive partial: %d of %d lifecycle worms executed in 24h"
                      % (hive_ran, hive_n))
    av = data.get("adv", {})
    if (av.get("prom24", 0) == 0) and (av.get("sprt24", 0) >= 5):
        alerts.append("PROMOTION DEADLOCK: %d sprt-promotes, 0 shipped (pareto %d/holdout %d/post %d)" % (
            av.get("sprt24", 0), av.get("die_pareto", 0),
            av.get("die_holdout", 0), av.get("die_oracle", 0)))
    if alerts:
        L.append(C_RED + C_BLD + "ALERT: " + "; ".join(alerts) + C_RST)
    else:
        # Every value named here is read, not asserted: the old string was the
        # literal "OK: hive live, %s, G: up, no failures/24h", which claimed
        # health even when the frame above it disagreed.
        L.append(C_GRN + "OK: hive %d/%d ran, %s, G: %s, 0 mesh failures/24h" % (
            hive_ran, hive_n, role, "up" if data.get("g_up") else "down") + C_RST)
    if data.get("errors"):
        L.append(C_YEL + "note: " + "; ".join(data["errors"][:2]) + C_RST)
    return [_fit(l, width) for l in L]


def log_frame(lines):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("=== %s ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            for l in lines:
                import re
                f.write(re.sub("\x1b\\[[0-9;]*m", "", l) + "\n")
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(prog="lilith_live_monitor")
    ap.add_argument("--refresh", type=float, default=REFRESH)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-color", action="store_true",
                    help="strip ANSI (parity with the other three wallboards)")
    args = ap.parse_args()
    if args.no_color:
        _no_color()

    def frame():
        try:
            width = shutil.get_terminal_size((100, 30)).columns
        except Exception:
            width = 100
        lines = render(collect(), max(40, width - 1))
        return lines

    if args.once:
        for l in frame():
            print(l)
        return 0
    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")  # scanner:ignore deliberate: fixed literal, no injection surface
            lines = frame()
            for l in lines:
                print(l)
            log_frame(lines)
            print("\nCtrl+C to stop  |  log: logs/lilith_live_monitor.log")
            time.sleep(max(2.0, args.refresh))
    except KeyboardInterrupt:
        print("\nLilith watches on.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
