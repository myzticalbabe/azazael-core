#!/usr/bin/env python3
# coding: utf-8
"""Holdout yield must be judged per estimator, not across an estimator change.

Why this file exists. `scripts/benchmark_evolution.py` section 6 asserted "the
search produced a promotable candidate (>= 1 holdout pass)" over every stored
`evolution_result` row. Rows are stamped with the estimator that produced them
(`evolution_cycle.MARGIN_ESTIMATOR`), and `evolution_cycle._sprt_history`
already refuses to compare across a change rather than mixing scales -- but the
audit did not read the stamp.

So it judged the CURRENT estimator with rows the PREVIOUS one wrote. The
`0/5 ... frozen 1.84x ... fresh 0.17x` verdict is the 2026-09-16
unpaired-estimator measurement, and it was reported for four days as though it
described `paired-min-delta` (which landed 2026-09-18 precisely to fix it).

Both directions are wrong, and both are tested here:
  - a superseded FAILURE must not fail the current estimator;
  - a superseded SUCCESS must not pass it.
The second is the more dangerous one: it would let a lucky pre-fix run be read
as evidence that the fix works.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (os.path.join(_ROOT, "scripts"), _ROOT, os.path.join(_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import benchmark_evolution as BE  # noqa: E402

CURRENT = "paired-min-delta"
OLD = "unpaired-min-diff"


def row(estimator, held, fresh, passed, cooldown=False, fresh_info=True):
    """One stored evolution_result row, as query_memories returns it."""
    h = {"margin": fresh, "passed": passed}
    if not cooldown:
        # a real fresh-pool run records the fresh pool it scored on
        if fresh_info:
            h["fresh_info"] = {"n": 40}
    else:
        h["cooldown"] = True
    data = {"best_total": 30.0 + held, "baseline": 30.0, "holdout": h}
    if estimator is not None:
        data["margin_estimator"] = estimator
    return {"data": data, "timestamp": "2026-09-16T00:00:00"}


class TestEstimatorEraPartition(unittest.TestCase):

    def test_current_estimator_rows_land_in_current(self):
        y = BE.holdout_yield([row(CURRENT, 2.0, 0.5, True)], CURRENT)
        self.assertEqual(len(y["current"]["verdicts"]), 1)
        self.assertEqual(len(y["superseded"]["verdicts"]), 0)

    def test_other_estimator_rows_land_in_superseded_and_are_named(self):
        y = BE.holdout_yield([row(OLD, 2.0, 0.1, False)], CURRENT)
        self.assertEqual(len(y["current"]["verdicts"]), 0)
        self.assertEqual(len(y["superseded"]["verdicts"]), 1)
        self.assertEqual(y["estimators"], {OLD})

    def test_unstamped_rows_are_superseded_and_labelled(self):
        """Rows written before the stamp existed were made by the old estimator."""
        y = BE.holdout_yield([row(None, 2.0, 0.1, False)], CURRENT)
        self.assertEqual(len(y["superseded"]["verdicts"]), 1)
        self.assertIn("<unstamped: pre-estimator-stamp>", y["estimators"])

    def test_superseded_failure_does_not_touch_current_yield(self):
        """The actual defect: 0/5 from the old estimator must not be today's 0/5."""
        rows = [row(OLD, 2.0, 0.05, False) for _ in range(5)]
        y = BE.holdout_yield(rows, CURRENT)
        self.assertEqual(len(y["current"]["verdicts"]), 0,
                         "superseded rows must not populate the current bucket")
        self.assertEqual(len(y["superseded"]["verdicts"]), 5)

    def test_superseded_success_does_not_pass_current(self):
        """Negative control. A lucky pre-fix run is not evidence the fix works."""
        rows = [row(OLD, 3.0, 2.5, True)]
        y = BE.holdout_yield(rows, CURRENT)
        self.assertEqual(len(y["current"]["verdicts"]), 0,
                         "a superseded pass must not count as a current pass")

    def test_mixed_history_splits_by_stamp(self):
        rows = [row(OLD, 2.0, 0.1, False), row(CURRENT, 1.0, 0.9, True),
                row(None, 2.0, 0.1, False), row(CURRENT, 0.5, 0.2, False)]
        y = BE.holdout_yield(rows, CURRENT)
        self.assertEqual(y["current"]["verdicts"], [True, False])
        self.assertEqual(y["superseded"]["verdicts"], [False, False])
        self.assertEqual(y["estimators"], {OLD, "<unstamped: pre-estimator-stamp>"})

    def test_held_and_fresh_track_their_own_rows(self):
        """Averages must come from the right era's numbers."""
        rows = [row(OLD, 9.0, 0.01, False), row(CURRENT, 1.5, 0.75, True)]
        y = BE.holdout_yield(rows, CURRENT)
        self.assertEqual(y["current"]["held"], [1.5])
        self.assertEqual(y["current"]["fresh"], [0.75])
        self.assertEqual(y["superseded"]["held"], [9.0])

    def test_cooldown_is_not_a_fresh_measurement(self):
        """A cooldown re-reports a PRIOR margin; counting it would double-count."""
        y = BE.holdout_yield([row(CURRENT, 2.0, 0.5, True, cooldown=True)], CURRENT)
        self.assertEqual(len(y["current"]["verdicts"]), 0)

    def test_holdout_without_fresh_info_is_not_counted(self):
        y = BE.holdout_yield([row(CURRENT, 2.0, 0.5, True, fresh_info=False)], CURRENT)
        self.assertEqual(len(y["current"]["verdicts"]), 0)

    def test_rows_without_best_total_are_skipped(self):
        bad = {"data": {"baseline": 30.0, "holdout": {"margin": 1.0, "passed": True,
                                                     "fresh_info": {"n": 1}},
                        "margin_estimator": CURRENT}}
        y = BE.holdout_yield([bad], CURRENT)
        self.assertEqual(len(y["current"]["verdicts"]), 0)

    def test_string_data_is_parsed(self):
        """Rows can carry data as a JSON string."""
        r = row(CURRENT, 2.0, 0.5, True)
        r["data"] = json.dumps(r["data"])
        y = BE.holdout_yield([r], CURRENT)
        self.assertEqual(y["current"]["verdicts"], [True])

    def test_empty_input(self):
        y = BE.holdout_yield([], CURRENT)
        self.assertEqual(y["current"]["verdicts"], [])
        self.assertEqual(y["estimators"], set())


class TestTheAssertionUsesOnlyCurrentEra(unittest.TestCase):
    """End-to-end on the decision, not just the partition."""

    @staticmethod
    def _yield_decides(rows, estimator=CURRENT):
        y = BE.holdout_yield(rows, estimator)
        cur = y["current"]["verdicts"]
        if not cur:
            return "no-evidence"          # honest: neither pass nor fail
        return "pass" if any(cur) else "fail"

    def test_five_old_failures_give_no_evidence_not_a_failure(self):
        rows = [row(OLD, 2.0, 0.05, False) for _ in range(5)]
        self.assertEqual(self._yield_decides(rows), "no-evidence")

    def test_old_success_does_not_produce_a_pass(self):
        self.assertEqual(self._yield_decides([row(OLD, 3.0, 2.5, True)]),
                         "no-evidence")

    def test_current_failure_fails(self):
        rows = [row(CURRENT, 2.0, 0.05, False) for _ in range(3)]
        self.assertEqual(self._yield_decides(rows), "fail")

    def test_current_success_passes(self):
        self.assertEqual(self._yield_decides([row(CURRENT, 2.0, 1.5, True)]),
                         "pass")

    def test_old_history_does_not_mask_a_current_failure(self):
        rows = [row(OLD, 9.0, 4.0, True)] + [row(CURRENT, 1.0, 0.0, False)]
        self.assertEqual(self._yield_decides(rows), "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
