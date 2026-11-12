#!/usr/bin/env python3
"""
bcc_kuzu_temporal_persist.py -- Persistent temporal KG for the BCC board-reorg facts

Loads the same 10 verified temporal facts used by the graphiti in-memory prototype
(bcc_graphiti_temporal.py) into an EMBEDDED kuzu graph database (persistent file on
disk). Runs point-in-time + window queries against the valid-time interval model.

Requires the 3.13 tooling venv (kuzu is cp314t-blocked):
    venv-313-tools/Scripts/python.exe azazael/scripts/bcc_kuzu_temporal_persist.py
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bcc_graphiti_temporal import facts  # noqa: E402

try:
    import kuzu  # noqa: E402
except ImportError as _dep_exc:
    raise SystemExit("bcc_kuzu_temporal_persist.py needs kuzu (3.13 tooling venv per docstring): " + str(_dep_exc)) from _dep_exc

DB_PATH = Path(os.path.expanduser("~")) / "bcc_temporal_kg.db"
OUT = Path(os.path.expanduser("~")) / "BCC_KUZU_TEMPORAL_evidence.json"


def point_query(conn, relation: str, obj: str, date: str) -> list:
    """Entity names active on <date> for <relation> targeting <obj> (ISO strings compare lexically)."""
    result = conn.execute(
        "MATCH (s:Entity)-[r:Rel]->(o:Entity) "
        "WHERE r.relation = $rel AND o.name = $obj "
        "AND r.valid_from <= $d AND (r.valid_to IS NULL OR r.valid_to > $d) "
        "RETURN s.name, r.relation, r.valid_from, r.valid_to",
        {"rel": relation, "obj": obj, "d": date},
    )
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    return rows


def main() -> int:
    t0 = time.time()
    if DB_PATH.exists():
        import shutil

        if DB_PATH.is_dir():
            shutil.rmtree(DB_PATH)
        else:
            DB_PATH.unlink()
    db = kuzu.Database(str(DB_PATH))
    conn = kuzu.Connection(db)

    conn.execute("CREATE NODE TABLE Entity(name STRING, kind STRING, PRIMARY KEY(name))")
    conn.execute(
        "CREATE REL TABLE Rel(FROM Entity TO Entity, relation STRING, "
        "valid_from STRING, valid_to STRING)"
    )

    loaded = 0
    seen: dict = {}
    for subject, rel, obj, vf, vt, evidence in facts():
        for ent, kind in ((subject, "person"), (obj, "org" if obj in ("Edward Jones", "BCC", "TPUC") else "person")):
            if ent not in seen:
                conn.execute(
                    "CREATE (:Entity {name: $name, kind: $kind})",
                    {"name": ent, "kind": kind},
                )
                seen[ent] = kind
        conn.execute(
            "MATCH (s:Entity {name: $s}), (o:Entity {name: $o}) "
            "CREATE (s)-[:Rel {relation: $r, valid_from: $vf, valid_to: $vt}]->(o)",
            {"s": subject, "o": obj, "r": rel, "vf": vf, "vt": vt},
        )
        loaded += 1

    checks = {
        "chair_2024_01_01": point_query(conn, "chairs board of", "BCC", "2024-01-01"),
        "chair_2025_10_01": point_query(conn, "chairs board of", "BCC", "2025-10-01"),
        "chair_2026_08_01": point_query(conn, "chairs board of", "BCC", "2026-08-01"),
        "ceo_2018_06_15": point_query(conn, "CEO of", "BCC", "2018-06-15"),
        "ceo_2019_06_15": point_query(conn, "CEO of", "BCC", "2019-06-15"),
        "treasurer_2025_10_01": point_query(conn, "treasurer of", "BCC", "2025-10-01"),
        "treasurer_2026_08_01": point_query(conn, "treasurer of", "BCC", "2026-08-01"),
        "transition_window_2026_05_20": point_query(conn, "chairs board of", "BCC", "2026-05-20"),
    }

    # Window query: everyone whose BCC officer role overlapped May 2026
    window = conn.execute(
        "MATCH (s:Entity)-[r:Rel]->(o:Entity {name: 'BCC'}) "
        "WHERE r.valid_from <= '2026-05-31' AND (r.valid_to IS NULL OR r.valid_to >= '2026-05-01') "
        "RETURN s.name, r.relation, r.valid_from, r.valid_to ORDER BY r.valid_from"
    )
    window_rows = []
    while window.has_next():
        window_rows.append(window.get_next())

    out = {
        "engine": "kuzu 0.11.3 embedded (file-backed persistent graph DB)",
        "db_path": str(DB_PATH),
        "loaded_facts": loaded,
        "entities": len(seen),
        "point_in_time_queries": {
            k: [{"who": r[0], "valid_from": r[2], "valid_to": r[3]} for r in v]
            for k, v in checks.items()
        },
        "may_2026_officer_window": [
            {"who": r[0], "relation": r[1], "valid_from": r[2], "valid_to": r[3]}
            for r in window_rows
        ],
        "elapsed_s": round(time.time() - t0, 2),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("=== Persistent kuzu temporal KG ===")
    print(f"Facts: {loaded}  Entities: {len(seen)}  DB: {DB_PATH}")
    for k, v in checks.items():
        print(f"  {k}: {[r[0] for r in v] or '—'}")
    print("May-2026 officer window:")
    for r in window_rows:
        print(f"  {r[0]} {r[1]} ({r[2]} -> {r[3] or 'present'})")
    print(f"Evidence: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
