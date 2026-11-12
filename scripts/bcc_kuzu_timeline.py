#!/usr/bin/env python3
"""
bcc_kuzu_timeline.py -- Date-stamp every temporal edge in the persistent kuzu KG

Opens the persisted ~/bcc_temporal_kg.db (no writes) and resolves each relation's
active holder across milestone dates (incl. boundary tests exactly on the
2026-05-15 reorg day), producing a full role timeline.

    venv-313-tools/Scripts/python.exe azazael/scripts/bcc_kuzu_timeline.py
"""
import json
import os
from pathlib import Path

import kuzu
import time

HOME = Path.home()
OUT = HOME / "BCC_KUZU_TIMELINE_evidence.json"
DB_PATH = HOME / "bcc_temporal_kg.db"

DATES = [
    "2015-07-01", "2018-10-01", "2019-02-01", "2025-07-01",
    "2026-05-15", "2026-05-16", "2026-08-01", "2026-09-05",
]
RELS = [
    "chairs board of", "CEO of", "CFO of", "treasurer of",
    "financial advisor for", "employed at", "co-employed with",
]


def active(conn, rel: str, date: str) -> list:
    result = conn.execute(
        "MATCH (s:Entity)-[r:Rel]->(o:Entity) "
        "WHERE r.relation = $rel "
        "AND r.valid_from <= $d AND (r.valid_to IS NULL OR r.valid_to > $d) "
        "RETURN s.name, o.name, r.valid_from, r.valid_to ORDER BY s.name",
        {"rel": rel, "d": date},
    )
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    return rows


def main() -> int:
    if not DB_PATH.exists():
        raise SystemExit(f"DB missing — run bcc_kuzu_temporal_persist.py first: {DB_PATH}")
    db = kuzu.Database(str(DB_PATH))
    conn = kuzu.Connection(db)

    timeline = {}
    for rel in RELS:
        cols = {}
        for date in DATES:
            rows = active(conn, rel, date)
            if rows:
                cols[date] = [
                    {"who": r[0], "target": r[1], "since": r[2],
                     "until": r[3] or "present"}
                    for r in rows
                ]
        if cols:
            timeline[rel] = cols

    out = {
        "engine": "kuzu embedded (persistent ~/bcc_temporal_kg.db)",
        "dates_queried": DATES,
        "elapsed_s": 0.0,
        "timeline": timeline,
    }
    t0 = time.time()
    out["elapsed_s"] = round(time.time() - t0, 2)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("=== kuzu role timelines (active holder per milestone date) ===")
    for rel, cols in timeline.items():
        print(f"  {rel}:")
        for date, rows in cols.items():
            who = ", ".join(f"{r['who']} ({r['since']}->{r['until']})" for r in rows)
            print(f"    {date}: {who}")
    print(f"Evidence: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
