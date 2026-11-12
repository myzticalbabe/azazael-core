#!/usr/bin/env python3
# coding: utf-8
"""A watchdog that cannot fail is decoration.

Two real outages motivate these tests, and they failed in DIFFERENT ways:

  - `\\Azazael-Chain` was **disabled** on 2026-09-16: the loop was healthy and
    switched off. A heartbeat check alone would have seen a stale file and
    blamed the loop.
  - the hourly spine cycle was **enabled and silent** from 2026-09-18: it fired
    every hour and refused to write. A task-state check alone would have said
    "Ready" and been satisfied.

So there are paired cases for both, plus a mutation check in the suite's own
terms: a watchdog that returns "ok" unconditionally passes every positive test,
which is why each failure mode has both a positive and a negative case here.
"""
from __future__ import annotations

import os
import subprocess
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

import audit_loop_liveness as LL  # noqa: E402


def _fake_schtasks(stdout: str, rc: int = 0):
    return mock.Mock(return_value=mock.Mock(returncode=rc, stdout=stdout,
                                            stderr=""))


class TestHeartbeat(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._dir.cleanup()

    def _file_age(self, name: str, seconds: float):
        p = os.path.join(self._dir.name, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        stamp = time.time() - seconds
        os.utime(p, (stamp, stamp))
        return p

    def test_recent_heartbeat_is_ok(self):
        p = self._file_age("live.log", 60)
        status, _ = LL.heartbeat_status(__import__("pathlib").Path(p), 900)
        self.assertEqual(status, "ok")

    def test_stale_heartbeat_is_silent(self):
        """The spine cycle's failure mode: enabled, writing nothing."""
        p = self._file_age("stale.log", 40 * 3600)
        status, ev = LL.heartbeat_status(__import__("pathlib").Path(p), 900)
        self.assertEqual(status, "SILENT")
        self.assertIn("h ago", ev)

    def test_missing_file_in_an_existing_dir_is_missing(self):
        p = os.path.join(self._dir.name, "never.log")
        status, _ = LL.heartbeat_status(__import__("pathlib").Path(p), 900)
        self.assertEqual(status, "MISSING")

    def test_absent_volume_is_distinguished_from_a_missing_file(self):
        """An unmounted G: is a different problem with a different fix."""
        p = os.path.join(self._dir.name, "no_such_volume", "x.jsonl")
        status, ev = LL.heartbeat_status(__import__("pathlib").Path(p), 900)
        self.assertEqual(status, "UNREADABLE")
        self.assertIn("volume absent", ev)

    def test_age_scales_with_the_bound(self):
        """The bound is a threshold, not a hard-coded clock."""
        p = self._file_age("borderline.log", 5000)
        import pathlib
        self.assertEqual(LL.heartbeat_status(pathlib.Path(p), 900)[0], "SILENT")
        self.assertEqual(LL.heartbeat_status(pathlib.Path(p), 10000)[0], "ok")


class TestTaskState(unittest.TestCase):
    """The chain's failure mode: healthy loop, switched off."""

    def test_disabled_is_detected(self):
        with mock.patch.object(subprocess, "run",
                               _fake_schtasks('"\\Azazael-Chain","N/A","Disabled"')):
            status, ev = LL.task_status("Azazael-Chain")
        self.assertEqual(status, "DISABLED")
        self.assertEqual(ev, "Disabled")

    def test_ready_is_ok(self):
        with mock.patch.object(subprocess, "run", _fake_schtasks(
                '"\\Azazael-Chain","9/20/2026 1:57:05 AM","Ready"')):
            self.assertEqual(LL.task_status("Azazael-Chain")[0], "ok")

    def test_running_is_ok(self):
        with mock.patch.object(subprocess, "run", _fake_schtasks(
                '"\\Azazael-Chain","9/20/2026 1:57:05 AM","Running"')):
            self.assertEqual(LL.task_status("Azazael-Chain")[0], "ok")

    def test_missing_task_is_reported(self):
        with mock.patch.object(subprocess, "run",
                               _fake_schtasks("", rc=1)):
            self.assertEqual(LL.task_status("Nope")[0], "NO-TASK")

    def test_unavailable_schtasks_fails_closed(self):
        """A watchdog that cannot verify must not report healthy."""
        with mock.patch.object(subprocess, "run",
                               side_effect=FileNotFoundError()):
            status, ev = LL.task_status("Azazael-Chain")
        self.assertEqual(status, "UNKNOWN")
        self.assertIn("schtasks unavailable", ev)

    def test_unparseable_output_fails_closed(self):
        with mock.patch.object(subprocess, "run",
                               _fake_schtasks("\x00garbage")):
            self.assertEqual(LL.task_status("X")[0], "UNKNOWN")


class TestVerdict(unittest.TestCase):

    @staticmethod
    def _row(hb="ok", task="ok"):
        return {"loop": "x", "hb_status": hb, "task_status": task,
                "hb_evidence": "", "task_evidence": ""}

    def test_healthy_loop_does_not_fail(self):
        self.assertFalse(LL.failed(self._row()))

    def test_silent_heartbeat_fails_even_when_task_is_ready(self):
        """Spine cycle: task Ready the whole 30 hours it wrote nothing."""
        self.assertTrue(LL.failed(self._row(hb="SILENT", task="ok")))

    def test_disabled_task_fails_even_with_a_fresh_heartbeat(self):
        """Chain: the task was disabled while its log looked recent."""
        self.assertTrue(LL.failed(self._row(hb="ok", task="DISABLED")))

    def test_missing_and_unreadable_fail(self):
        for s in ("MISSING", "UNREADABLE"):
            self.assertTrue(LL.failed(self._row(hb=s)), s)

    def test_unknown_task_state_fails_closed(self):
        self.assertTrue(LL.failed(self._row(task="UNKNOWN")))


class TestAuditEndToEnd(unittest.TestCase):
    """Both real outages replayed through the audit, with paired controls."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._real_run = subprocess.run
        # Isolate learned state: `audit()` otherwise loads the real
        # state/loop_liveness.json, whose seen_tasks make a registry-only
        # entry look LOST (nothing is discovered in registry mode). That is a
        # runtime artifact, not a fact about the loop under test.
        self._orig_state = LL.STATE_PATH
        LL.STATE_PATH = __import__("pathlib").Path(self._dir.name) / "loop_liveness.json"

    def tearDown(self):
        subprocess.run = self._real_run
        LL.STATE_PATH = self._orig_state
        self._dir.cleanup()

    def _heartbeat(self, name, age_s):
        p = os.path.join(self._dir.name, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        stamp = time.time() - age_s
        os.utime(p, (stamp, stamp))
        return p

    def test_replays_the_chain_outage(self):
        """`Azazael-Chain` disabled: the audit must FAIL."""
        live = self._heartbeat("chain.log", 30)
        reg = (("chain", live, 900, "Azazael-Chain", "the supervisor"),)
        with mock.patch.object(subprocess, "run", _fake_schtasks(
                '"\\Azazael-Chain","N/A","Disabled"')):
            rows = LL.audit(registry=reg)
        self.assertEqual(rows[0]["task_status"], "DISABLED")
        self.assertTrue(LL.failed(rows[0]))

    def test_replays_the_spine_outage(self):
        """Enabled but silent for 40h: the audit must FAIL."""
        stale = self._heartbeat("spine.log", 40 * 3600)
        reg = (("spine", stale, 3 * 3600, "AzazaelSpineCycle", "hourly"),)
        with mock.patch.object(subprocess, "run", _fake_schtasks(
                '"\\AzazaelSpineCycle","9/20/2026 2:45:00 AM","Ready"')):
            rows = LL.audit(registry=reg)
        self.assertEqual(rows[0]["hb_status"], "SILENT")
        self.assertEqual(rows[0]["task_status"], "ok")
        self.assertTrue(LL.failed(rows[0]))

    def test_healthy_loop_passes_both_signals(self):
        live = self._heartbeat("ok.log", 30)
        reg = (("chain", live, 900, "Azazael-Chain", "supervisor"),)
        with mock.patch.object(subprocess, "run", _fake_schtasks(
                '"\\Azazael-Chain","9/20/2026 1:57:05 AM","Ready"')):
            rows = LL.audit(registry=reg)
        self.assertFalse(LL.failed(rows[0]))

    def test_no_tasks_mode_still_checks_heartbeats(self):
        stale = self._heartbeat("spine.log", 40 * 3600)
        reg = (("spine", stale, 3 * 3600, "AzazaelSpineCycle", "hourly"),)
        rows = LL.audit(check_tasks=False, registry=reg)
        self.assertNotIn("task_status", rows[0])
        self.assertTrue(LL.failed(rows[0]))


class TestTheRealRegistryIsSane(unittest.TestCase):
    """Guards against the watchdog itself drifting out of date."""

    def test_every_entry_has_a_task_and_a_heartbeat(self):
        for name, hb, max_s, task, what in LL.REGISTRY:
            self.assertTrue(hb, name)
            self.assertTrue(task, name)
            self.assertTrue(what, name)

    def test_silence_bounds_are_above_the_stated_cadence(self):
        """A bound at or below the cadence would cry wolf every cycle."""
        bounds = {"chain": 60, "spine": 3600, "exmachina": 86400,
                  "berks": 86400, "threat": 86400}
        for name, _hb, max_s, _task, _what in LL.REGISTRY:
            cadence = bounds[name]
            self.assertGreater(max_s, cadence * 2,
                               f"{name}: {max_s}s silence bound is too tight "
                               f"for a {cadence}s cadence")

    def test_the_supervisor_is_watched(self):
        names = [r[0] for r in LL.REGISTRY]
        self.assertIn("chain", names,
                      "the entry point whose silent disablement cost 4 days "
                      "must be watched")


if __name__ == "__main__":
    unittest.main(verbosity=2)
