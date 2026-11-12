#!/usr/bin/env python3
"""inject_alert_drill.py - Bounded, LABELLED chaos-drill alerts for the hard pool.

WHY THIS EXISTS
---------------
The evolution cycle's hard (critical-alert) training pool is fed by
`db_health_alert` rows emitted by DatabaseMonitorWorm. Those are
threshold-driven, so when the system is genuinely healthy the pool starves and
the alert objective re-masters frozen rows.

That is exactly what happened on 2026-09-09/10: `error_count_7d` fell from 635
to 53 (warn threshold is 100), the database stayed under `max_size_mb_warn`, the
monitor verdict went to healthy=True, and alerts stopped at
2026-09-09T15:47:02Z. The pool did not break - the incident class went away.

The ONLY honest way to refill that pool without waiting for a real incident is a
drill, so every row written here is unmistakably a drill:

  * source        = "ChaosDrill"     (never "DatabaseMonitorWorm")
  * data.drill    = True
  * data.drill_batch = <ISO timestamp>
  * message       = "[DRILL] ..."    (prefix)
  * tags          = ["chaos_drill", ...]

so no drill can be mistaken for a real incident, and the whole batch can be
removed in one statement with --purge.

Metrics, severities, message templates and thresholds mirror
DatabaseMonitorWorm's real vocabulary, and values are spread across the real
ranges - so the alert objective gets a genuine gradient instead of N identical
rows. Where DatabaseMonitorWorm is importable its live threshold constants are
used, so the drill tracks config changes.

Usage
-----
    python scripts/inject_alert_drill.py --count 24             # dry run (default)
    python scripts/inject_alert_drill.py --count 24 --apply     # write the rows
    python scripts/inject_alert_drill.py --purge --apply        # remove ALL drills
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_src = str(Path(__file__).resolve().parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

# Fallback vocabulary — overridden by DatabaseMonitorWorm's live constants.
FALLBACK = {
    "warn_db_size_mb": 100.0,
    "critical_db_size_mb": 500.0,
    "warn_errors_7d": 100,
    "critical_errors_7d": 400,
    "warn_growth_24h": 10000,
    "critical_growth_24h": 50000,
}

INTEGRITY_FAILURES = (
    "malformed database schema (memories_fts_idx)",
    "database disk image is malformed (page 41021)",
    "fts5: corruption found in segment index",
    "row 881402 missing from FTS5 index",
)


def _thresholds():
    """Live thresholds from the worm, else the fallback table."""
    out = dict(FALLBACK)
    try:
        from CORE.WORMS.expansion.db_monitor import DatabaseMonitorWorm
        w = DatabaseMonitorWorm()
        out.update({
            "warn_db_size_mb": float(getattr(w, "WARN_DB_SIZE_MB", 100.0)),
            "critical_db_size_mb": float(getattr(w, "CRITICAL_DB_SIZE_MB", 500.0)),
            "warn_errors_7d": int(getattr(w, "WARN_ERRORS_7D", 100)),
            "critical_errors_7d": int(getattr(w, "CRITICAL_ERRORS_7D", 400)),
            "warn_growth_24h": int(getattr(w, "WARN_GROWTH_24H", 10000)),
            "critical_growth_24h": int(getattr(w, "CRITICAL_GROWTH_24H", 50000)),
        })
    except Exception as e:  # noqa: BLE001 - fallback is the documented behaviour
        print(f"[warn] using fallback thresholds: {str(e)[:100]}")
    return out


def build_drill(count, seed, batch_id, th):
    """Deterministic spread of labelled drill alerts across the real metrics."""
    rng = random.Random(seed)
    rows = []
    metrics = ("db_size_mb", "error_count_7d", "growth_24h_actual", "integrity_check")
    for i in range(count):
        metric = metrics[i % len(metrics)]
        # Two thirds critical: the hard pool is the critical-alert skill.
        severity = "critical" if rng.random() < 0.66 else "warning"
        if metric == "db_size_mb":
            if severity == "critical":
                value = round(rng.uniform(th["critical_db_size_mb"],
                                          th["critical_db_size_mb"] * 1.8), 1)
            else:
                value = round(rng.uniform(th["warn_db_size_mb"],
                                          th["critical_db_size_mb"] - 1), 1)
            threshold = th["critical_db_size_mb"] if severity == "critical" else th["warn_db_size_mb"]
            message = f"Database size {value:.1f} MB exceeds {severity} threshold"
        elif metric == "error_count_7d":
            if severity == "critical":
                value = rng.randint(th["critical_errors_7d"],
                                    th["critical_errors_7d"] * 3)
            else:
                value = rng.randint(th["warn_errors_7d"],
                                    th["critical_errors_7d"] - 1)
            threshold = th["critical_errors_7d"] if severity == "critical" else th["warn_errors_7d"]
            message = f"{value} errors in last 7 days exceeds {severity} threshold"
        elif metric == "growth_24h_actual":
            if severity == "critical":
                value = rng.randint(th["critical_growth_24h"],
                                    th["critical_growth_24h"] * 3)
                message = f"High memory growth in last 24h: {value} rows/day"
            else:
                value = rng.randint(th["warn_growth_24h"],
                                    th["critical_growth_24h"] - 1)
                message = f"Elevated memory growth in last 24h: {value} rows/day"
            threshold = th["critical_growth_24h"] if severity == "critical" else th["warn_growth_24h"]
        else:
            severity = "critical"
            value = 0
            threshold = 0
            message = f"Database integrity failure: {rng.choice(INTEGRITY_FAILURES)}"
        rows.append({
            "severity": severity,
            "message": f"[DRILL] {message}",
            "metric": metric,
            "value": value,
            "threshold": threshold,
            "drill": True,
            "drill_batch": batch_id,
        })
    return rows


def apply_drill(rows, batch_id):
    """Write via the store API so uuid/audit-hash/schema fields stay consistent."""
    from CORE.local_memory import get_memory_store
    store = get_memory_store()
    written = 0
    for row in rows:
        store.record_event(
            source="ChaosDrill",
            event_type="db_health_alert",
            data={
                "alert_id": f"drill_{batch_id}_{written}",
                "severity": row["severity"],
                "message": row["message"],
                "metric": row["metric"],
                "value": row["value"],
                "threshold": row["threshold"],
                "drill": True,
                "drill_batch": row["drill_batch"],
            },
            severity=row["severity"],
            tags=["db_monitor", "alert", row["severity"], "chaos_drill"],
        )
        written += 1
    return written


def purge_drills(db_path, apply):
    """Remove only rows explicitly marked drill:true (never touches real alerts)."""
    import sqlite3
    con = sqlite3.connect(db_path, timeout=30)
    try:
        sel = ("SELECT COUNT(*) FROM memories WHERE event_type='db_health_alert' "
               "AND json_extract(data,'$.drill') = 1")
        n = con.execute(sel).fetchone()[0]
        print(f"[purge] drill rows found: {n}")
        if not apply:
            print("[purge] dry run - pass --apply to delete them")
            return 0
        con.execute("DELETE FROM memories WHERE event_type='db_health_alert' "
                    "AND json_extract(data,'$.drill') = 1")
        con.commit()
        print(f"[purge] deleted {n} drill rows (real alerts untouched)")
        return n
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=24, help="drill alerts to write (default 24)")
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    ap.add_argument("--purge", action="store_true", help="delete all drill rows instead")
    ap.add_argument("--seed", type=int, default=0, help="RNG seed (default: derived from time)")
    ap.add_argument("--db", default=None, help="memory_store.db path (purge only)")
    args = ap.parse_args()

    db_path = args.db or os.path.join(os.path.expanduser("~"), ".azazael", "memory_store.db")

    if args.purge:
        purge_drills(db_path, args.apply)
        return 0

    th = _thresholds()
    batch_id = time.strftime("%Y%m%dT%H%M%S")
    seed = args.seed or int(time.time())
    rows = build_drill(args.count, seed, batch_id, th)
    crit = sum(1 for r in rows if r["severity"] == "critical")
    print(f"[drill] batch={batch_id} count={len(rows)} critical={crit} "
          f"warning={len(rows) - crit} seed={seed}")
    print("[drill] metrics: " + ", ".join(sorted({r['metric'] for r in rows})))
    for r in rows[:3]:
        print(f"    {r['severity']:8s} {r['metric']:20s} value={r['value']} "
              f"thr={r['threshold']} | {r['message'][:58]}")
    print("    ...")
    if not args.apply:
        print("[drill] dry run - pass --apply to write these rows")
        return 0
    written = apply_drill(rows, batch_id)
    print(f"[drill] wrote {written} labelled rows (source=ChaosDrill, tags include chaos_drill)")
    print(f"[drill] purge with: python {Path(__file__).name} --purge --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
