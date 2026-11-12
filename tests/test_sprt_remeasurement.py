# coding: utf-8
"""The SPRT must not count one measurement many times.

`_load_or_freeze_pools` reuses a single pool snapshot for up to
`POOL_MAX_AGE_S` = 1800s so that streak comparisons are apples-to-apples. That
is deliberate — it fixed the baseline swinging 31.6 -> 26.4 on identical genes
between runs minutes apart.

The side effect was not intended and was measured on 2026-09-12: **twenty
consecutive live cycles recorded the identical margin 0.5563**, every one with
`pool_snapshot_reused: True` and the same `pool_id`. The candidate is re-found
deterministically from the champion seed, so those are not twenty observations
of "about +0.56"; they are one observation re-recorded.

`sprt_llr_step` is additive, so duplicates inflate the log-likelihood ratio
without bound. The live log reached `llr 328 at n=7` on margins 1.33-1.39 —
a repeated number presented as seven measurements. Deduping is the conservative
direction: it can only make promotion harder.
"""
import os
import sys
import unittest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_project_root, os.path.join(_project_root, "scripts"),
           os.path.join(_project_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evolution_cycle as ec  # noqa: E402


class TestSprtCollapse(unittest.TestCase):

    def test_identical_run_collapses_to_one(self):
        seq, dropped = ec._sprt_collapse([0.5563] * 20)
        self.assertEqual(seq, [0.5563])
        self.assertEqual(dropped, 19)

    def test_distinct_values_are_all_kept(self):
        seq, dropped = ec._sprt_collapse([1.0, 1.5, 1.2])
        self.assertEqual(seq, [1.0, 1.5, 1.2])
        self.assertEqual(dropped, 0)

    def test_only_CONNECTED_duplicates_are_collapsed(self):
        """A value that returns after a different value carries new
        information about the pool, so it is kept."""
        seq, dropped = ec._sprt_collapse([1.0, 1.0, 2.0, 1.0])
        self.assertEqual(seq, [1.0, 2.0, 1.0])
        self.assertEqual(dropped, 1)

    def test_tolerance_is_tight(self):
        """Two genuinely different margins must not be merged."""
        seq, dropped = ec._sprt_collapse([0.5563, 0.5564])
        self.assertEqual(len(seq), 2)
        self.assertEqual(dropped, 0)

    def test_nan_and_junk_are_skipped(self):
        seq, _ = ec._sprt_collapse([1.0, "x", None, 1.0])
        self.assertEqual(seq, [1.0])

    def test_empty_input(self):
        self.assertEqual(ec._sprt_collapse([]), ([], 0))
        self.assertEqual(ec._sprt_collapse(None), ([], 0))

    def test_single_value(self):
        self.assertEqual(ec._sprt_collapse([2.5]), ([2.5], 0))


class TestMarginRepeatStreak(unittest.TestCase):
    """Stall detection: the loop stopped producing information and said nothing.

    100 consecutive live cycles failed `beats`, twenty of them recording the
    identical margin. The SPRT, the Pareto refine, the holdout and Cranium were
    all skipped, and the log looked exactly like normal operation.
    """

    @staticmethod
    def _row(margin, fitness="v3-dual-robust"):
        return {"data": {"fitness": fitness, "margin": margin,
                         "pool_id": "p"}}

    def test_counts_consecutive_repeats_newest_first(self):
        rows = [self._row(0.5563), self._row(0.5563), self._row(0.5563),
                self._row(0.9)]
        self.assertEqual(ec._margin_repeat_streak(rows, 0.5563), 3)

    def test_stops_at_the_first_difference(self):
        rows = [self._row(0.5563), self._row(0.90), self._row(0.5563)]
        self.assertEqual(ec._margin_repeat_streak(rows, 0.5563), 1)

    def test_unrelated_rows_are_skipped_not_counted(self):
        rows = [self._row(0.5, fitness="v2-legacy"), self._row(0.5)]
        self.assertEqual(ec._margin_repeat_streak(rows, 0.5), 1)

    def test_derives_margin_when_the_key_is_absent(self):
        rows = [{"data": {"fitness": "v3", "best_total": 25.0,
                           "baseline": 24.0}}]
        self.assertEqual(ec._margin_repeat_streak(rows, 1.0), 1)

    def test_empty_and_junk_are_zero(self):
        self.assertEqual(ec._margin_repeat_streak([], 1.0), 0)
        self.assertEqual(ec._margin_repeat_streak(None, 1.0), 0)
        self.assertEqual(ec._margin_repeat_streak([{"data": "{bad"}], 1.0), 0)

    def test_tolerance_does_not_merge_distinct_margins(self):
        rows = [self._row(0.5563), self._row(0.5564)]
        self.assertEqual(ec._margin_repeat_streak(rows, 0.5563), 1)

    def test_limit_is_respected(self):
        rows = [self._row(0.5)] * 30
        self.assertEqual(ec._margin_repeat_streak(rows, 0.5, limit=5), 5)


class TestRepeatedMarginCannotPromote(unittest.TestCase):
    """End to end on the real gate: the recorded failure shape."""

    def test_twenty_identical_margins_do_not_promote(self):
        seq, dropped = ec._sprt_collapse([0.5563] * 20)
        llr, _sigma, n, decision = ec._sprt_decide(seq, salt_floor=0.12)
        self.assertEqual(n, 1)
        self.assertNotEqual(decision, "promote",
                            "one re-recorded measurement must not promote")

    def test_collapse_never_increases_the_llr(self):
        raw = [1.3, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3]
        seq, _ = ec._sprt_collapse(raw)
        llr_raw, _, _, _ = ec._sprt_decide(raw, salt_floor=0.12)
        llr_dup, _, _, _ = ec._sprt_decide(seq, salt_floor=0.12)
        self.assertLessEqual(llr_dup, llr_raw,
                             "deduping must move evidence DOWN, never up")

    def test_the_live_inflation_shape_is_reproduced_and_fixed(self):
        """The observed 1.33-1.39 stack that produced llr 328 at n=7."""
        raw = [1.39, 1.3775, 1.3775, 1.3775, 1.3813, 1.3288, 1.39]
        seq, dropped = ec._sprt_collapse(raw)
        llr_raw, _, n_raw, _ = ec._sprt_decide(raw, salt_floor=0.12)
        llr_dup, _, n_dup, _ = ec._sprt_decide(seq, salt_floor=0.12)
        self.assertEqual(dropped, 2)
        self.assertLess(n_dup, n_raw)
        self.assertLess(llr_dup, llr_raw)
        self.assertGreater(llr_raw, llr_dup,
                           "the duplication must be what inflated the LLR")

    def test_genuinely_distinct_evidence_can_still_promote(self):
        """The fix must not neuter the gate on real, varied evidence."""
        seq = [1.9, 1.6, 2.1, 1.8, 2.0]
        llr, _sigma, n, decision = ec._sprt_decide(seq, salt_floor=0.12)
        self.assertEqual(n, len(seq))
        self.assertEqual(decision, "promote")


if __name__ == "__main__":
    unittest.main()
