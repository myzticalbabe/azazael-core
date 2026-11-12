# coding: utf-8
"""Unit tests for data_retention — log rotation, caps, thresholds.

Covers the B6 test gap: retention runs against the live DB nightly with
zero unit coverage. All tests use sandbox dirs; nothing touches the store.
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE import data_retention as dr


class TestRotateLogs:
    def test_rotates_past_cap(self, tmp_path):
        big = tmp_path / "a.log"
        big.write_bytes(b"x" * (2 * 1024 * 1024 + 10))
        (tmp_path / "b.log").write_bytes(b"small")
        r = dr.rotate_logs(log_dir=tmp_path)
        assert r["rotated"] == ["a.log"]
        assert r["skipped"] == ["b.log"]
        assert not (tmp_path / "a.log").exists()
        assert (tmp_path / "a.log.1").stat().st_size > 2 * 1024 * 1024

    def test_shift_chain_and_drop(self, tmp_path):
        (tmp_path / "a.log").write_bytes(b"n" * (3 * 1024 * 1024))
        (tmp_path / "a.log.1").write_bytes(b"old1")
        (tmp_path / "a.log.5").write_bytes(b"dropme")
        r = dr.rotate_logs(log_dir=tmp_path, keep=5)
        assert r["rotated"] == ["a.log"]
        assert (tmp_path / "a.log.2").read_bytes() == b"old1"
        assert not (tmp_path / "a.log.6").exists()
        assert (tmp_path / "a.log.1").stat().st_size > 0

    def test_missing_dir(self, tmp_path):
        r = dr.rotate_logs(log_dir=tmp_path / "nope")
        assert r["rotated"] == [] and r["skipped"] == []

    def test_custom_cap(self, tmp_path):
        (tmp_path / "c.log").write_bytes(b"y" * 100)
        r = dr.rotate_logs(log_dir=tmp_path, max_mb=0, keep=2)
        assert r["rotated"] == ["c.log"]


class TestCapsAndThresholds:
    def test_never_caps_selection_history(self):
        for protected in ("evolution_result", "evolution_promotion"):
            assert protected not in dr.TYPE_CAPS

    def test_caps_cover_bookkeeping(self):
        for t in ("training_complete", "q_table_persisted",
                  "model_registered", "db_health_snapshot", "db_health_alert"):
            assert dr.TYPE_CAPS[t] > 0

    def test_sane_thresholds(self):
        assert dr.EVENT_TTL_DAYS > 0
        assert dr.DB_SIZE_QUOTA_MB > 0
        assert 0 < dr.VACUUM_THRESHOLD_PCT < 100
        assert dr.LOG_ROTATE_KEEP >= 1 and dr.LOG_ROTATE_MB > 0
