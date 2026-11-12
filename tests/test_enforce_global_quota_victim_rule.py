# coding: utf-8
"""Victim-selection rule pin for `enforce_global_quota`.

The gap: `enforce_global_quota` **deletes rows**. Its victim rule is pure
size/age -- the largest unprotected type, oldest id first. But the method's own
neighbour documents the intended next change as

    "gap": "composite rule is an edit to enforce_global_quota (M)"

and a composite-similarity rule changes WHICH ROWS GET DELETED. Nothing pinned
the current rule, so that edit would land silently. These tests pin it.

Every assertion below is read off `src/CORE/data_retention.py`, never assumed:

  * victim type = largest `SUM(LENGTH(data))` among unprotected, non-error
    types. A type with MORE rows but FEWER bytes must NOT win.
                                            -- :344 "ORDER BY 2 DESC LIMIT 1"
  * victims taken OLDEST-FIRST (lowest id first) -- :355 "ORDER BY id ASC"
  * `PROTECTED_EVENTS` spared even when they are the largest type
                                            -- :342 "event_type NOT IN (...)"
  * severity error/alert/critical spared even inside the chosen victim type
                                            -- :343 (victim SELECT) and
                                               :354 (id SELECT)
  * at most `max_batches` batches per run   -- :337 "batches < max_batches"
  * honest report when eligible rows run out -- :348 / :356 break the loop,
    and the result carries the key **"enforced"** (:410), so running out of
    eligible rows reports `False` instead of a bare success claim.

Isolation: every case runs in a SUBPROCESS whose `AZAZAEL_MEMORY_DIR` points at
a tmp dir, so it can never reach `~/.azazael/memory_store.db`. `quota_mb=` is
passed explicitly rather than lowering `DB_SIZE_QUOTA_MB`, because these tests
pin *who is chosen*, not call-time resolution of the constant (that separate
contract is already covered by `tests/test_data_retention_quota.py`).

Seeding turns `src/CORE/recorder_guard.py` OFF for the child, via its own
documented kill switch. Measured 2026-11-02: the guard's default bucket for an
unlisted type is 8 rows/s with a burst of 200, and it DROPS what exceeds that,
so `seed(..., 300, ...)` produced 206-208 rows and every count assertion below
compared against a number the fixture never wrote. Turning the switch off is
the fixture controlling its premise, not a weakened assertion -- the row counts
asserted here are the same 300/400/50 they always were, and the guard's own
behaviour stays covered by tests/test_recorder_guard.py.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")

#: Child helpers. `checkpoint()` matters: `enforce_global_quota` measures the
#: MAIN db file (`_db_size_mb`, :152), so rows still sitting in the -wal file
#: would make each test's "over quota" premise unprovable.
_PREAMBLE = r'''
import json, os, sqlite3
from CORE.local_memory import LocalMemoryStore
from CORE import data_retention as dr

db = os.path.join(os.environ["AZAZAEL_MEMORY_DIR"], "memory_store.db")
store = LocalMemoryStore(db_path=db)

def seed(event_type, n, size, severity="info"):
    blob = "x" * size
    for i in range(n):
        store.record_event("victim_rule_test", event_type,
                           {"i": i, "blob": blob}, severity=severity)

def checkpoint():
    c = sqlite3.connect(db, timeout=30)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()

def size_mb():
    return os.path.getsize(db) / 1e6

def rows(event_type, severity=None):
    c = sqlite3.connect(db, timeout=30)
    try:
        if severity is None:
            return c.execute("SELECT COUNT(*) FROM memories WHERE event_type=?",
                             (event_type,)).fetchone()[0]
        return c.execute(
            "SELECT COUNT(*) FROM memories WHERE event_type=? AND severity=?",
            (event_type, severity)).fetchone()[0]
    finally:
        c.close()

def ids_of(event_type):
    c = sqlite3.connect(db, timeout=30)
    try:
        return [r[0] for r in c.execute(
            "SELECT id FROM memories WHERE event_type=? ORDER BY id",
            (event_type,))]
    finally:
        c.close()
'''


def _run(tmp_path, body):
    """Run `_PREAMBLE + body` in an isolated subprocess; return its JSON."""
    env = dict(os.environ)
    env["AZAZAEL_MEMORY_DIR"] = str(tmp_path)
    # See the module docstring: without this the guard drops ~1/3 of a 300-row
    # import, so the counts these tests assert are never written.
    env["RECORDER_GUARD_OFF"] = "1"
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, "-c", _PREAMBLE + body], env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    assert proc.returncode == 0, (
        "isolated quota child failed:\n" + proc.stdout + "\n" + proc.stderr)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_victim_is_largest_by_bytes_not_by_row_count(tmp_path):
    """Rule :344 -- `ORDER BY 2 DESC` over `SUM(LENGTH(data))`.

    `t_many` has MORE rows than `t_big`; only `t_big` has more BYTES. A rule
    changed to count rows would pick `t_many` and this must fail.
    """
    out = _run(tmp_path, r'''
seed("t_big",  300, 8000)   # 2.4 MB in 300 rows
seed("t_many", 400,  100)   # 40 KB, but more rows than t_big
seed("t_tiny",  50,  200)   # 10 KB
checkpoint()
before = size_mb()
rep = dr.enforce_global_quota(quota_mb=0.01, db_path=db, max_batches=1)
print(json.dumps({"before_mb": before, "rep": rep,
                  "left": {t: rows(t) for t in ("t_big", "t_many", "t_tiny")}}))
''')
    # premise: the store really was over quota, or this proves nothing
    assert out["before_mb"] > 0.01, out
    assert set(out["rep"]["trimmed"]) == {"t_big"}, out
    assert out["rep"]["trimmed"]["t_big"] == 300, out
    assert out["left"] == {"t_big": 0, "t_many": 400, "t_tiny": 50}, out


def test_victims_are_taken_oldest_first(tmp_path):
    """Rule :355 -- `ORDER BY id ASC`. Survivors must be the newest ids."""
    out = _run(tmp_path, r'''
seed("t_old", 300, 4000)
checkpoint()
ids_before = ids_of("t_old")
rep = dr.enforce_global_quota(quota_mb=0.01, db_path=db,
                              max_batches=1, batch=50)
print(json.dumps({"ids_before": ids_before, "ids_after": ids_of("t_old"),
                  "rep": rep}))
''')
    assert len(out["ids_before"]) == 300, out
    assert out["rep"]["batches"] == 1, out
    assert out["rep"]["trimmed"] == {"t_old": 50}, out
    # exactly the 50 lowest ids were deleted; the survivors are the newest 250
    assert out["ids_after"] == out["ids_before"][50:], out


def test_protected_events_are_spared_even_when_largest(tmp_path):
    """Rule :342 -- `event_type NOT IN (PROTECTED_EVENTS)` in the victim SELECT."""
    out = _run(tmp_path, r'''
protected = dr.PROTECTED_EVENTS[0]
seed(protected, 300, 8000)      # the LARGEST type -- and protected
seed("t_unprot", 100, 1000)
checkpoint()
before_prot = rows(protected)
rep = dr.enforce_global_quota(quota_mb=0.01, db_path=db, max_batches=1)
print(json.dumps({"protected": protected, "before_prot": before_prot,
                  "after_prot": rows(protected), "left_unprot": rows("t_unprot"),
                  "rep": rep}))
''')
    assert out["before_prot"] == 300, out
    # the protected type was the largest, but was never a candidate at all
    assert set(out["rep"]["trimmed"]) == {"t_unprot"}, out
    assert out["after_prot"] == 300, out
    assert out["left_unprot"] == 0, out


def test_error_rows_survive_inside_the_chosen_victim_type(tmp_path):
    """Rules :343 and :354 -- error/alert/critical is filtered TWICE.

    The victim type is chosen from its non-error rows only; the id list is then
    filtered the same way, so error evidence survives even in a trimmed type.
    """
    out = _run(tmp_path, r'''
seed("t_mixed", 100, 4000, severity="info")
seed("t_mixed",  50, 4000, severity="error")
checkpoint()
rep = dr.enforce_global_quota(quota_mb=0.01, db_path=db, max_batches=1)
print(json.dumps({"rep": rep, "info_left": rows("t_mixed", "info"),
                  "error_left": rows("t_mixed", "error")}))
''')
    # t_mixed is the only type with eligible rows, so it is the chosen victim
    assert set(out["rep"]["trimmed"]) == {"t_mixed"}, out
    assert out["rep"]["trimmed"]["t_mixed"] == 100, out
    assert out["info_left"] == 0, out
    assert out["error_left"] == 50, out


def test_at_most_max_batches_batches_per_run(tmp_path):
    """Rule :337 -- `batches < max_batches` bounds one pass."""
    out = _run(tmp_path, r'''
seed("t_deep", 300, 4000)
checkpoint()
r1 = dr.enforce_global_quota(quota_mb=0.01, db_path=db, max_batches=1, batch=10)
after1 = rows("t_deep")
r3 = dr.enforce_global_quota(quota_mb=0.01, db_path=db, max_batches=3, batch=10)
after3 = rows("t_deep")
print(json.dumps({"batches1": r1["batches"], "after1": after1,
                  "batches3": r3["batches"], "after3": after3}))
''')
    assert out["batches1"] == 1, out
    assert out["after1"] == 290, out      # exactly one batch of 10 deleted
    assert out["batches3"] == 3, out
    assert out["after3"] == 260, out      # three more batches of 10 deleted


def test_reports_honestly_when_eligible_rows_run_out(tmp_path):
    """Rules :348 / :356 (break) and :410 (`enforced`), not a bare success claim.

    Every seeded row is severity `error`, so nothing passes the severity filter
    and the loop breaks immediately. The result must say so.
    """
    out = _run(tmp_path, r'''
seed("t_allerr", 100, 4000, severity="error")
checkpoint()
rep = dr.enforce_global_quota(quota_mb=0.01, db_path=db, max_batches=10)
print(json.dumps({"rep": rep, "left": rows("t_allerr")}))
''')
    rep = out["rep"]
    assert rep["batches"] == 0, out
    assert rep["trimmed"] == {}, out
    # it does NOT claim success while still over quota
    assert rep["enforced"] is False, out
    assert rep["size_mb"] > rep["quota_mb"], out
    assert out["left"] == 100, out
