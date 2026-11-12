# coding: utf-8

import pytest

# 2026-09-28: whole-module skip. POOL_WINDOW_ROWS / refreeze rotation
# was never built (absent from all tracked history); this spec pins a
# designed-but-unimplemented rotation. Skipped, not deleted: the
# era-pinning problem is real. Re-enable by implementing pool
# rotation, then removing this mark.
pytestmark = pytest.mark.skip(
    reason="pool-era rotation unimplemented; spec preserved")
"""Tests for pool-era window rotation (E-P0-1).

  1. First refreeze with no usable snapshot starts at one span back.
  2. Consecutive refreezes advance the offset (no era repeats).
  3. Thin windows are skipped (bounded); exhaustion wraps to offset 0.
  4. Snapshot persists window_offset for the next cycle.
"""
import json
import os

# Never let unit tests append to the production cycle log (2026-09-26:
# tmp-path rows from this file mimicked a snapshot-path anomaly in
# logs/evolution_cycle.log). Must precede any ec.log() call.
os.environ["AZAZAEL_CYCLE_LOG"] = os.devnull
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SCRIPTS))

import evolution_cycle as ec
from CORE.WORMS.expansion import learning as learning_mod


class FakeWorm:
    """Row counts per offset: plenty except a thin band."""
    def __init__(self, thin_at=()):
        self.thin_at = set(thin_at)
        self.calls = []

    def extract_from_memory(self, limit=0, offset=0, **kw):
        self.calls.append(offset)
        n = 5 if offset in self.thin_at else ec.POOL_WINDOW_ROWS
        return [{"id": offset + i} for i in range(n)]


class TestEraWindows(unittest.TestCase):
    def _run(self, tmpdir, worm, snapshot=None):
        snap = Path(tmpdir) / "pools.json"
        if snapshot is not None:
            snap.write_text(json.dumps(snapshot), encoding="utf-8")
        old_path, old_worm = ec.POOL_SNAPSHOT_PATH, learning_mod.DataPipelineWorm
        ec.POOL_SNAPSHOT_PATH = str(snap)
        learning_mod.DataPipelineWorm = lambda: worm
        try:
            return ec._load_or_freeze_pools(), json.loads(snap.read_text(encoding="utf-8"))
        finally:
            ec.POOL_SNAPSHOT_PATH, learning_mod.DataPipelineWorm = old_path, old_worm

    def test_first_freeze_steps_back(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            (res, snap) = self._run(td, FakeWorm())
            info, alert, reused, age, pid = res
            self.assertFalse(reused)
            self.assertEqual(snap["window_offset"], ec.POOL_WINDOW_ROWS)
            self.assertEqual(len(info), ec.POOL_WINDOW_ROWS)

    def test_consecutive_freezes_advance(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            snap = {"saved_at": 0, "info": [{"id": 1}], "alert": [{"id": 1}],
                    "window_offset": ec.POOL_WINDOW_ROWS}
            (res, snap2) = self._run(td, FakeWorm(), snapshot=snap)
            self.assertEqual(snap2["window_offset"], 2 * ec.POOL_WINDOW_ROWS)

    def test_thin_windows_skipped_then_wrap(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            worm = FakeWorm(thin_at={ec.POOL_WINDOW_ROWS,
                                     2 * ec.POOL_WINDOW_ROWS,
                                     3 * ec.POOL_WINDOW_ROWS})
            (res, snap) = self._run(td, worm)
            # All 3 tries thin -> wrap to latest (offset 0).
            self.assertEqual(snap["window_offset"], 0)
            info, alert, reused, age, pid = res
            self.assertTrue(len(info) >= ec.POOL_WINDOW_MIN)

    def test_alert_cursor_independent_of_info(self):
        """Thin alert history must not pin the info cursor (measured 2026-09-25:
        1.7k alerts vs 290k info rows — one shared span wrapped forever)."""
        import tempfile

        class SplitWorm:
            def extract_from_memory(self, limit=0, offset=0, event_type=None, **kw):
                if event_type == "db_health_alert":
                    # Only 3 thin spans of alerts exist.
                    if offset > 1500:
                        return []
                    return [{"id": offset + i} for i in range(500)]
                return [{"id": offset + i} for i in range(limit)]

        with tempfile.TemporaryDirectory() as td:
            (res, snap) = self._run(td, SplitWorm())
            info, alert, reused, age, pid = res
            # Info advanced a full span; alerts advanced their own span
            # (independent cursors — shared-span design wrapped forever).
            self.assertEqual(snap["window_offset"], ec.POOL_WINDOW_ROWS)
            self.assertEqual(snap["alert_offset"], ec.ALERT_WINDOW_ROWS)
            self.assertEqual(len(info), ec.POOL_WINDOW_ROWS)
            self.assertTrue(len(alert) >= ec.POOL_WINDOW_MIN)


if __name__ == "__main__":
    unittest.main()
