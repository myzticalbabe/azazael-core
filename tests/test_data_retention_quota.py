# coding: utf-8
"""End-to-end retention quota guard.

The gap: retention runs nightly against the live store, with unit coverage only
for log rotation / caps / thresholds -- nothing asserted the store is actually
UNDER QUOTA after a maintenance run. This test asserts that, end to end, in a
SUBPROCESS whose AZAZAEL_MEMORY_DIR points at a tmp dir, so it can never reach
~/.azazael/memory_store.db.

Seam under test (added 2026-09-28):
  * run_maintenance(db_path=...) / cmd_maintain(db_path=...) route every step
    that resolves through _get_db_path() at one store (_DB_OVERRIDE);
  * enforce_global_quota resolves DB_SIZE_QUOTA_MB at CALL time, so a test can
    lower the quota rather than materialise 500MB.
"""
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")

_CHILD = r'''
import json, os, sqlite3
from CORE.local_memory import LocalMemoryStore
from CORE import data_retention as dr

store_dir = os.environ["AZAZAEL_MEMORY_DIR"]
db = os.path.join(store_dir, "memory_store.db")
store = LocalMemoryStore(db_path=db)

# Seed an UNPROTECTED, UNCAPPED type past the (lowered) quota. Severity must
# not be error/alert/critical or the quota backstop is forbidden from trimming.
blob = "x" * 4000
for i in range(900):
    store.record_event("retention_test", "forensic_chatter",
                       {"i": i, "blob": blob}, severity="info")

before = os.path.getsize(db) / 1e6
dr.DB_SIZE_QUOTA_MB = 2          # call-time resolution makes this effective
rep = dr.run_maintenance(db_path=db)
after = os.path.getsize(db) / 1e6

con = sqlite3.connect(db)
try:
    left = con.execute(
        "SELECT COUNT(*) FROM memories WHERE event_type='forensic_chatter'"
    ).fetchone()[0]
finally:
    con.close()
print(json.dumps({"before_mb": before, "after_mb": after, "rows_left": left,
                  "quota_mb": dr.DB_SIZE_QUOTA_MB,
                  "backstop": rep.get("quota_backstop", {})}))
'''


def _run_maintenance_isolated(tmp_path):
    env = dict(os.environ)
    env["AZAZAEL_MEMORY_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    # The seed below is a BULK write (900 rows back to back), and the recorder
    # guard added 2026-10-31 intentionally throttles per-tick emitters to
    # RECORDER_GUARD_RATE. Without the documented kill switch the seed lands
    # ~25 rows, `before_mb` never exceeds the quota, and this test would pass
    # or fail on the guard rather than on retention. The assertions below are
    # unchanged: they still require the seed to exceed the quota.
    env["RECORDER_GUARD_OFF"] = "1"
    proc = subprocess.run([sys.executable, "-c", _CHILD], env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    assert proc.returncode == 0, (
        "isolated maintenance child failed:\n" + proc.stdout + "\n" + proc.stderr)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_maintenance_leaves_store_under_quota(tmp_path):
    out = _run_maintenance_isolated(tmp_path)
    # The seed must actually have exceeded the quota or the test proves nothing.
    assert out["before_mb"] > out["quota_mb"], out
    # The backstop must have run (not skipped) and reported honestly.
    assert out["backstop"], out
    assert "skipped" not in out["backstop"], out
    # The invariant: after the FULL run the store is under quota on disk.
    assert out["after_mb"] <= out["quota_mb"], out
    # And the seeded rows were actually trimmed, not merely reported.
    assert out["rows_left"] == 0, out


def test_db_path_seam_routes_and_does_not_leak(tmp_path):
    sys.path.insert(0, SRC)
    from CORE import data_retention as dr
    target = str(tmp_path / "memory_store.db")
    before = dr._DB_OVERRIDE
    try:
        assert dr._get_db_path() != target
        dr._DB_OVERRIDE = target
        assert dr._get_db_path() == target
    finally:
        dr._DB_OVERRIDE = before
    assert dr._get_db_path() != target, "override leaked"


def test_quota_resolves_at_call_time():
    sys.path.insert(0, SRC)
    from CORE import data_retention as dr
    sig = inspect.signature(dr.enforce_global_quota)
    assert sig.parameters["quota_mb"].default is None, sig
