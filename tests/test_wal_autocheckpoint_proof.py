"""WAL autocheckpoint proof (#684 closeout) — tmp-DB only, never the live store.

Acceptance for the #684 headline (PRAGMA wal_autocheckpoint on store
open): the discipline is armed on every connection AND the checkpoint
machinery demonstrably fires. Both are measured on an isolated tmp DB.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from CORE.local_memory import DB_PATH, LocalMemoryStore


@pytest.fixture(autouse=True)
# start of the recorder guard release
#
# The checkpoint proof writes 400 rows; the recorder guard throttles to 8/s with
# a burst of 200 and its bucket is process-wide, so record_event() returns 0 for
# the rows past the burst and the loop's `> 0` assertion fails on the throttle
# rather than on the checkpoint. Released here: this file measures WAL growth,
# not write admission.
def _recorder_guard_released(monkeypatch):
    from CORE import recorder_guard
    monkeypatch.setattr(recorder_guard, "_DISABLED", True, raising=False)
    recorder_guard.guard().reset()


def _tmp_store(tmp_path):
    return LocalMemoryStore(db_path=str(tmp_path / "proof.db"))


def test_autocheckpoint_armed_on_tmp_store(tmp_path):
    store = _tmp_store(tmp_path)
    assert store.record_event("proof", "checkpoint_probe",
                              {"n": 1}) > 0
    conn = sqlite3.connect(str(tmp_path / "proof.db"))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
        armed = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
        assert int(armed) == 1000
    finally:
        conn.close()


def test_checkpoint_fires_on_tmp_store(tmp_path):
    store = _tmp_store(tmp_path)
    payload = {"blob": "x" * 12000}
    written = 0
    for i in range(400):
        assert store.record_event("proof", "checkpoint_load",
                                  dict(payload, i=i)) > 0
        written += 12200
    db = str(tmp_path / "proof.db")
    wal = db + "-wal"
    # Each record_event opens and closes its connection, so every close
    # checkpoints into the main DB: the proof is that ~4.8MB of payload
    # landed in proof.db itself while the WAL stayed bounded or vanished
    # (fully checkpointed on last close) — never an ever-growing -wal.
    assert os.path.getsize(db) > written // 2
    assert (not os.path.exists(wal)
            or os.path.getsize(wal) < written // 2)
    conn = sqlite3.connect(db)
    try:
        busy, log, done = conn.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert busy == 0
        armed = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
        assert int(armed) == 1000
    finally:
        conn.close()


def test_live_db_untouched_by_proof(tmp_path):
    # Contract: the proof's writes land ONLY in its tmp store, never live.
    # Proven with a per-run unique marker, not with live file size/mtime:
    # under parallel xdist other suites legitimately write the live store
    # concurrently, so a global-quiescence assert is inherently racy (failed
    # 2026-10-20 with +20KB from a concurrent writer). Concurrent writers
    # never carry our uuid, so the marker's absence from live is exact.
    if not os.path.exists(DB_PATH):
        pytest.skip("no live store on this machine")
    import uuid
    marker = "no_touch_probe_%s" % uuid.uuid4().hex
    store = _tmp_store(tmp_path)
    assert str(store.db_path) != str(DB_PATH)
    assert store.record_event("proof", marker) > 0
    tmp_conn = sqlite3.connect(str(tmp_path / "proof.db"), timeout=30)
    try:
        tmp_hits = tmp_conn.execute(
            "SELECT COUNT(*) FROM memories WHERE event_type = ?",
            (marker,)).fetchone()[0]
        assert int(tmp_hits) == 1
    finally:
        tmp_conn.close()
    live_conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        live_hits = live_conn.execute(
            "SELECT COUNT(*) FROM memories WHERE event_type = ?",
            (marker,)).fetchone()[0]
        assert int(live_hits) == 0, "proof leaked into the live store"
    finally:
        live_conn.close()
