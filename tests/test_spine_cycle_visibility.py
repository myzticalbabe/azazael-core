#!/usr/bin/env python3
# coding: utf-8
"""A refused self-improvement cycle must not be reported as a success.

Why this file exists. `scripts/spine_cycle_apply.py` is the hourly self-
improvement entry point. It logged only cycles/scanned/proposals/applied/queen,
which are all None/0 on an early return from `SpineCycleWorm.run_cycle`. The
proprioception gate refuses a write with:

    {"success": False, "skipped": "stale_self_image", "reason": "...", "cycles": 0}

so from 2026-09-18T20:45 every hourly run printed a success-shaped line while
the cycle wrote nothing, for ~30 hours, with nothing red anywhere.

The gate was correct -- refusing to act on a stale map of itself is the whole
point -- but its refusal was invisible. These tests pin the mapping from a
cycle report to a log line and an exit code, because that mapping is what
turned a working gate into an unnoticed outage.

Note the third case: `run_cycle` returns `success: True` for its skip paths, so
`success` alone cannot be the signal. This wrapper runs with apply=True, and a
cycle that skipped did no work, so it must exit non-zero regardless of
`success` -- otherwise "we skipped" and "we ran" are the same colour again.
"""
from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (os.path.join(_ROOT, "scripts"), _ROOT, os.path.join(_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import spine_cycle_apply as SCA  # noqa: E402


class LogCapture:
    """Capture log() output instead of appending to the live hourly log."""

    def __init__(self):
        self.lines = []

    def __call__(self, msg):
        self.lines.append(str(msg))

    @property
    def text(self):
        return "\n".join(self.lines)


class TestRefusalIsVisible(unittest.TestCase):

    def setUp(self):
        self._real_log = SCA.log
        self.cap = LogCapture()
        SCA.log = self.cap

    def tearDown(self):
        SCA.log = self._real_log

    def test_stale_self_image_refusal_exits_nonzero(self):
        rep = {"success": False, "skipped": "stale_self_image",
               "reason": "stale artifacts: graphify-out/graph.json",
               "cycles": 0}
        code = SCA._log_cycle_outcome(rep)
        self.assertEqual(code, 1, "a refusal must not exit 0")

    def test_refusal_log_names_the_skip_and_the_reason(self):
        rep = {"success": False, "skipped": "stale_self_image",
               "reason": "stale artifacts: graphify-out/graph.json",
               "cycles": 0}
        SCA._log_cycle_outcome(rep)
        t = self.cap.text
        self.assertIn("SKIPPED", t, "the skip must be named in the log")
        self.assertIn("stale_self_image", t)
        self.assertIn("graphify-out/graph.json", t,
                      "the reason must survive to the log, not be dropped")
        self.assertNotIn("scanned=56", t)

    def test_success_true_skip_still_exits_nonzero(self):
        """The skip paths report success=True; that must not read as work done."""
        rep = {"success": True, "skipped": "cycle_already_running_global",
               "cycles": 0}
        self.assertEqual(SCA._log_cycle_outcome(rep), 1)

    def test_every_skip_reason_is_treated_as_a_skip(self):
        for reason in ("cycle_already_running", "cycle_already_running_global",
                       "min_interval", "stale_self_image", "something_new"):
            rep = {"success": True, "skipped": reason, "cycles": 0}
            self.assertEqual(SCA._log_cycle_outcome(rep), 1,
                             f"{reason} must exit non-zero")

    def test_normal_cycle_exits_zero_and_reports_work(self):
        rep = {"success": True, "cycles": 1,
               "homeostat": {"scanned": 56, "proposals": [1, 2, 22],
                             "applied": True},
               "red_queen": {"queen_role": "queen_interim"}}
        code = SCA._log_cycle_outcome(rep)
        self.assertEqual(code, 0)
        t = self.cap.text
        self.assertIn("scanned=56", t)
        self.assertIn("proposals=3", t)
        self.assertIn("applied=True", t)
        self.assertIn("queen=queen_interim", t)
        self.assertNotIn("SKIPPED", t)

    def test_a_normal_cycle_is_not_reported_as_a_skip(self):
        """Negative control: the refusal branch must not fire on real work."""
        rep = {"success": True, "cycles": 1, "homeostat": {"scanned": 4,
               "proposals": [], "applied": True}}
        SCA._log_cycle_outcome(rep)
        self.assertNotIn("SKIPPED", self.cap.text)


class TestReplyIsNotFabricated(unittest.TestCase):
    """The report dict is echoed, never invented."""

    def setUp(self):
        self._real_log = SCA.log
        self.cap = LogCapture()
        SCA.log = self.cap

    def tearDown(self):
        SCA.log = self._real_log

    def test_missing_homeostat_does_not_claim_scanned(self):
        rep = {"success": True, "cycles": 1}
        SCA._log_cycle_outcome(rep)
        self.assertIn("scanned=None", self.cap.text)

    def test_refresh_helper_exists_and_is_callable(self):
        self.assertTrue(callable(SCA._refresh_derived_maps))


if __name__ == "__main__":
    unittest.main(verbosity=2)
