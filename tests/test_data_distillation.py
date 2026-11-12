# coding: utf-8
"""Self-distillation: the store summarizes before it deletes.

Deletion-only retention loses history at the TTL boundary. These tests pin
the distill-then-delete contract on sandbox DBs (never the live store):
victims are rolled up per (source, event_type, day) first, protected
selection history is never touched, and the quota backstop trims
oldest-first from the largest unprotected types after distilling them.
"""

import json
import sqlite3
import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE import data_retention as dr

SCHEMA = """
CREATE TABLE memories (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL, source TEXT NOT NULL, event_type TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}', severity TEXT NOT NULL DEFAULT 'info',
  tags TEXT NOT NULL DEFAULT '[]', timestamp TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  event_uuid TEXT, correlation_id TEXT, audit_hash TEXT,
  schema_version INTEGER NOT NULL DEFAULT 1, trace_id TEXT);
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, created_at TEXT);
"""


def _db(tmp_path, rows):
    p = str(tmp_path / "mem.db")
    conn = sqlite3.connect(p)
    conn.executescript(SCHEMA)
    for r in rows:
        conn.execute(
            "INSERT INTO memories (session_id, source, event_type, data,"
            " severity, tags, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)", r)
    conn.commit()
    conn.close()
    return p


def _old(days, **kw):
    ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return ("s1", kw.get("source", "W"), kw.get("event", "op"),
            json.dumps(kw.get("data", {"duration_ms": 10.0})),
            kw.get("sev", "info"), "[]", ts)


def _count(db, where="1=1", args=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM memories WHERE %s" % where, args).fetchone()[0]
    finally:
        conn.close()


def test_distill_then_ttl_is_lossless_at_rollup_grain(tmp_path):
    rows = [_old(100, source="A", event="op") for _ in range(3)]
    rows += [_old(100, source="A", event="op", sev="error")]
    rows += [_old(100, source="B", event="op") for _ in range(2)]
    rows += [_old(100, source="W", event="evolution_result")]
    rows += [_old(10, source="A", event="op")]
    db = _db(tmp_path, rows)
    d = dr.distill_aged_memories(db_path=db)
    assert d["rows_summarized"] == 6  # protected evolution_result excluded
    assert d["groups"] == 2  # (A,op,day) + (B,op,day)
    assert _count(db, "event_type=?", (dr.DISTILLED_EVENT,)) == 2
    conn = sqlite3.connect(db)
    try:
        roll = conn.execute(
            "SELECT data FROM memories WHERE event_type=? AND data LIKE ?",
            (dr.DISTILLED_EVENT, '%"source": "A"%')).fetchone()[0]
    finally:
        conn.close()
    body = json.loads(roll)
    assert body["count"] == 4 and body["errors"] == 1
    t = dr.enforce_event_ttl(db_path=db)
    assert t["events_deleted"] == 6  # the summarized victims, nothing else
    assert _count(db, "event_type=? AND source=?", (dr.DISTILLED_EVENT, "retention")) == 2
    assert _count(db, "event_type=?", ("evolution_result",)) == 1
    assert _count(db, "event_type=? AND source=?", ("op", "A")) == 1  # fresh


def test_distill_idempotent_across_pair(tmp_path):
    db = _db(tmp_path, [_old(100) for _ in range(4)])
    first = dr.distill_aged_memories(db_path=db)
    assert first["groups"] == 1
    # Simulate the TTL half of the pair, then re-distill: nothing new.
    conn = sqlite3.connect(db)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        conn.execute("DELETE FROM memories WHERE timestamp < ? "
                     "AND event_type != ?", (cutoff, dr.DISTILLED_EVENT))
        conn.commit()
    finally:
        conn.close()
    second = dr.distill_aged_memories(db_path=db)
    assert second["groups"] == 0 and second["rows_summarized"] == 0


def test_quota_spares_protected_and_errors(tmp_path):
    big = "x" * 20000
    rows = [_old(10, source="W", event="op", data={"duration_ms": 1.0,
                                                  "pad": big})
            for _ in range(6)]
    rows += [_old(10, source="W", event="evolution_result",
                  data={"pad": big}) for _ in range(2)]
    rows += [_old(10, source="W", event="op", sev="error",
                  data={"pad": big}) for _ in range(2)]
    db = _db(tmp_path, rows)
    r = dr.enforce_global_quota(quota_mb=0.01, db_path=db)
    assert r["batches"] <= 4
    assert _count(db, "event_type=?", ("evolution_result",)) == 2
    assert _count(db, "severity=?", ("error",)) == 2
    assert _count(db, "event_type=?", (dr.DISTILLED_EVENT,)) >= 1
    ordinary = _count(db, "event_type=? AND severity!=?", ("op", "error"))
    assert ordinary < 6  # oldest-first trim happened


def test_distilled_horizon_outlives_raw(tmp_path):
    rows = [_old(400, source="retention", event=dr.DISTILLED_EVENT),
            _old(10, source="retention", event=dr.DISTILLED_EVENT),
            _old(100, source="A", event="op")]
    db = _db(tmp_path, rows)
    dr.enforce_event_ttl(db_path=db)
    left = _count(db, "event_type=?", (dr.DISTILLED_EVENT,))
    assert left == 1  # 10-day rollup survives, 400-day rollup ages out
    assert _count(db, "event_type=?", ("op",)) == 0


def test_cap_victims_distilled_before_trim(tmp_path):
    rows = [_old(10, source="A", event="op") for _ in range(8)]
    rows += [_old(10, source="A", event="op", sev="error")]
    db = _db(tmp_path, rows)
    d = dr.distill_cap_victims(caps={"op": 3}, db_path=db)
    assert d["rows_summarized"] == 6  # the 6 beyond newest 3
    assert d["groups"] == 1
    t = dr.enforce_type_caps(caps={"op": 3}, db_path=db)
    assert t["total"] == 6
    assert _count(db, "event_type=?", ("op",)) == 3
    conn = sqlite3.connect(db)
    try:
        body = json.loads(conn.execute(
            "SELECT data FROM memories WHERE event_type=?",
            (dr.DISTILLED_EVENT,)).fetchone()[0])
    finally:
        conn.close()
    assert body["count"] == 6 and body["via"] == "cap_distill"


def test_cap_never_touches_protected(tmp_path):
    db = _db(tmp_path, [_old(10, source="W", event="evolution_result")
                        for _ in range(4)])
    d = dr.distill_cap_victims(
        caps={"evolution_result": 1}, db_path=db)
    assert d["rows_summarized"] == 0  # capped types exclude protected...
    # ...but enforce_type_caps takes raw caps: callers must not pass
    # protected types. The module constant obeys this:
    assert "evolution_result" not in dr.TYPE_CAPS
    assert "evolution_promotion" not in dr.TYPE_CAPS
