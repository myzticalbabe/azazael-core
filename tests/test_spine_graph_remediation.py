#!/usr/bin/env python3
# coding: utf-8
"""The spine loop must be able to repair a stale graph by itself.

Why this file exists. `tools/refresh_all.py` deliberately never rebuilds
`graphify-out/`, but two of the seven artifacts the proprioception gate checks
live there. So five artifacts were auto-repairable and the loop stayed shut on
the other two: it refused to write for ~30 hours and nothing could clear it
without a human.

The repair is now wired, and the part worth testing is the *guard* around it,
because the repair is a ~31 minute detached process:

  - a second pass must not be started while one is in flight (else every hourly
    fire adds another 31-minute job);
  - a marker left by a killed process or a reboot must NOT block forever, or the
    cure becomes the disease;
  - half an hour of work must only be launched on a DEFINITE "stale" -- an
    unreadable probe is not evidence of staleness.

`_graph_stale()` is tested for that last property directly, since returning
False there is what keeps a broken probe from spawning work.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (os.path.join(_ROOT, "scripts"), _ROOT, os.path.join(_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import graph_refresh_no_llm as GRN  # noqa: E402
import spine_cycle_apply as SCA  # noqa: E402


class TestInFlightGuard(unittest.TestCase):
    """The guard reads two facts from the marker's contents, so it is tested
    with a real file written the way `_launch_graph_refresh` writes one:
    `"<start-time> <pid>"`.

    Writing a bare byte and backdating the mtime — what this suite used to do —
    exercises nothing: the timestamp is parsed out of the FILE, and an
    unparseable body is reported as "not running" for the wrong reason.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.marker = os.path.join(self._dir.name, "graph_refresh.running")
        self._real = SCA.GRAPH_REFRESH_MARKER
        SCA.GRAPH_REFRESH_MARKER = self.marker

    def tearDown(self):
        SCA.GRAPH_REFRESH_MARKER = self._real
        self._dir.cleanup()

    def _marker_age(self, seconds: float, pid: (int | None) = None) -> None:
        """Write a marker started `seconds` ago, held by `pid` (default: us)."""
        stamp = time.time() - seconds
        with open(self.marker, "w", encoding="utf-8") as f:
            f.write("%.6f %d" % (stamp, os.getpid() if pid is None else pid))
        os.utime(self.marker, (stamp, stamp))

    def test_no_marker_means_not_running(self):
        self.assertFalse(SCA._graph_refresh_running())

    def test_fresh_marker_means_running(self):
        self._marker_age(30)
        self.assertTrue(SCA._graph_refresh_running())

    def test_marker_within_the_31_minute_pass_is_running(self):
        """A real pass is ~1860s, so that must still count as in flight."""
        self._marker_age(1800)
        self.assertTrue(SCA._graph_refresh_running())

    def test_marker_past_the_deadline_is_a_corpse_not_a_lock(self):
        """A killed process must not block the next attempt forever."""
        self._marker_age(SCA.GRAPH_REFRESH_DEADLINE_S + 60)
        self.assertFalse(SCA._graph_refresh_running())

    def test_future_marker_does_not_block(self):
        """A nonsense (future) mtime must not wedge the guard."""
        self._marker_age(-600)
        self.assertFalse(SCA._graph_refresh_running())

    def test_deadline_is_longer_than_a_real_pass(self):
        """Guard against someone shrinking it below the measured runtime."""
        self.assertGreater(SCA.GRAPH_REFRESH_DEADLINE_S, 1860)

    def test_live_pid_past_the_grace_still_holds_the_lock(self):
        """A pass that outlives the grace period is still a pass."""
        with mock.patch.object(SCA, "_pid_alive", return_value=True):
            self._marker_age(SCA.MARKER_GRACE_S + 60, pid=4242)
            self.assertTrue(SCA._graph_refresh_running())

    def test_dead_pid_past_the_grace_holds_nothing(self):
        """The measured 2026-09-21 case: holder died, marker ages out on its own.

        Paired with the live-pid test above, because "always False" would also
        make this one pass — and always-False is precisely the bug in the
        opposite direction.
        """
        with mock.patch.object(SCA, "_pid_alive", return_value=False):
            self._marker_age(SCA.MARKER_GRACE_S + 60, pid=4242)
            self.assertFalse(SCA._graph_refresh_running())

    def test_unparseable_marker_body_is_not_running(self):
        """A body this code cannot read is not evidence of a running pass."""
        with open(self.marker, "w", encoding="utf-8") as f:
            f.write("x")
        self.assertFalse(SCA._graph_refresh_running())


class TestLaunchIsGuarded(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.marker = os.path.join(self._dir.name, "graph_refresh.running")
        self._real = SCA.GRAPH_REFRESH_MARKER
        SCA.GRAPH_REFRESH_MARKER = self.marker

    def tearDown(self):
        SCA.GRAPH_REFRESH_MARKER = self._real
        self._dir.cleanup()

    def test_does_not_launch_when_one_is_in_flight(self):
        with open(self.marker, "w", encoding="utf-8") as f:
            f.write("%.6f %d" % (time.time(), os.getpid()))
        with mock.patch.object(SCA.subprocess, "Popen") as popen:
            SCA._launch_graph_refresh()
        popen.assert_not_called()

    def test_launches_and_writes_the_marker_when_idle(self):
        with mock.patch.object(SCA.subprocess, "Popen") as popen:
            SCA._launch_graph_refresh()
        popen.assert_called_once()
        self.assertTrue(os.path.exists(self.marker),
                        "the marker must exist before the next fire can check it")

    def test_launches_the_no_llm_script(self):
        with mock.patch.object(SCA.subprocess, "Popen") as popen:
            SCA._launch_graph_refresh()
        argv = popen.call_args[0][0]
        self.assertTrue(any("graph_refresh_no_llm" in str(a) for a in argv),
                        f"unexpected command: {argv}")

    def test_launch_failure_is_reported_not_raised(self):
        with mock.patch.object(SCA.subprocess, "Popen",
                               side_effect=OSError("nope")):
            SCA._launch_graph_refresh()  # must not raise


class TestOnlyDefiniteStalenessLaunches(unittest.TestCase):
    """Half an hour of work is a big hammer; the trigger must be certain."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.marker = os.path.join(self._dir.name, "graph_refresh.running")
        self._real_marker = SCA.GRAPH_REFRESH_MARKER
        SCA.GRAPH_REFRESH_MARKER = self.marker

    def tearDown(self):
        SCA.GRAPH_REFRESH_MARKER = self._real_marker
        self._dir.cleanup()

    @staticmethod
    def _run_refresh(graph_stale):
        with mock.patch.object(SCA.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="ok")), \
             mock.patch.object(SCA, "_graph_stale", return_value=graph_stale), \
             mock.patch.object(SCA, "_launch_graph_refresh") as launch:
            SCA._refresh_derived_maps()
        return launch

    def test_definitely_stale_launches_the_graph_pass(self):
        self._run_refresh(True).assert_called_once()

    def test_current_graph_does_not_launch(self):
        self._run_refresh(False).assert_not_called()

    def test_unknown_staleness_does_not_launch(self):
        """A broken probe must not spawn 31 minutes of work."""
        self._run_refresh(None).assert_not_called()

    def test_graph_stale_returns_none_when_the_probe_is_unusable(self):
        with mock.patch.dict(sys.modules, {"graph_refresh_no_llm": None}):
            self.assertIsNone(SCA._graph_stale())


class TestStandaloneRunClaimsTheMarker(unittest.TestCase):
    """A standalone `python scripts/graph_refresh_no_llm.py` must claim the slot.

    `_launch_graph_refresh()` writes the marker before it spawns the script, but
    the script itself did not -- so a hand-started pass was invisible to the
    hourly cycle, which then saw "nothing in flight" and started a second ~31
    minute re-extract over the same tree. The body must be the
    "<start-time> <pid>" shape `_graph_refresh_running()` parses: asserting
    only that the file exists would pass for a body the guard reads as idle.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.marker = os.path.join(self._dir.name, "graph_refresh.running")
        self._real_sca = SCA.GRAPH_REFRESH_MARKER
        self._real_grn = GRN.MARKER
        SCA.GRAPH_REFRESH_MARKER = self.marker
        GRN.MARKER = pathlib.Path(self.marker)

    def tearDown(self):
        SCA.GRAPH_REFRESH_MARKER = self._real_sca
        GRN.MARKER = self._real_grn
        self._dir.cleanup()

    def test_write_marker_is_read_as_in_flight(self):
        GRN.write_marker()
        self.assertTrue(os.path.exists(self.marker),
                        "a standalone pass must claim the slot it occupies")
        self.assertTrue(SCA._graph_refresh_running())

    def test_write_then_clear_leaves_nothing(self):
        """Paired: a writer that always looked "running" would also pass above."""
        GRN.write_marker()
        GRN.clear_marker()
        self.assertFalse(SCA._graph_refresh_running())


if __name__ == "__main__":
    unittest.main(verbosity=2)
