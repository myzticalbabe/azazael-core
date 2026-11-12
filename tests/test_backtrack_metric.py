"""test_backtrack_metric.py — Hermetic tests for backtrack_metric.py.

Pure-function tests on fabricated `evolution_result`-like rows. No store
reads/writes; no inference. Pins the flip / reversal / archive-direction
definitions per Engran #826.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtrack_metric as bm  # noqa: E402


def _row(best_genes=None, promoted=None, qd=None, pool="era1", ts="2026-01-01T00:00:00+00:00", rid=0):
    return {
        "id": rid,
        "timestamp": ts,
        "data": {
            "pool_id": pool,
            "best_genes": dict(best_genes) if best_genes else None,
            "promoted": promoted,
            "qd_archive": dict(qd) if qd else None,
        },
    }


def _qd(filled, added=0):
    return {"added": added, "filled": filled}


class TestArgmax(unittest.TestCase):
    def test_max_gene_wins(self):
        self.assertEqual(bm.argmax_genes({"a": 0.1, "b": 0.9, "c": 0.5}), "b")

    def test_tie_first_key_wins(self):
        self.assertEqual(bm.argmax_genes({"a": 1.0, "b": 1.0}), "a")

    def test_empty_returns_none(self):
        self.assertIsNone(bm.argmax_genes({}))
        self.assertIsNone(bm.argmax_genes(None))

    def test_non_numeric_skipped(self):
        self.assertEqual(bm.argmax_genes({"a": "x", "b": 0.5}), "b")


class TestFlip(unittest.TestCase):
    def test_argmax_change_is_flip(self):
        prev = _row({"a": 0.9, "b": 0.1}, True)
        cur = _row({"a": 0.2, "b": 0.8}, True)
        self.assertTrue(bm.is_flip(prev, cur))

    def test_stable_argmax_is_not_flip(self):
        prev = _row({"a": 0.9, "b": 0.1}, True)
        cur = _row({"a": 0.8, "b": 0.2}, True)
        self.assertFalse(bm.is_flip(prev, cur))

    def test_missing_genes_returns_none(self):
        self.assertIsNone(bm.is_flip(_row({"a": 0.9}, True), _row({}, True)))
        self.assertIsNone(bm.is_flip(_row(None, True), _row({"a": 0.9}, True)))


class TestReversal(unittest.TestCase):
    def test_promoted_toggle_is_reversal(self):
        self.assertTrue(bm.is_reversal(_row({"a": 0.9}, True), _row({"a": 0.8}, False)))
        self.assertTrue(bm.is_reversal(_row({"a": 0.9}, False), _row({"a": 0.8}, True)))

    def test_stable_promoted_is_not_reversal(self):
        self.assertFalse(bm.is_reversal(_row({"a": 0.9}, True), _row({"a": 0.8}, True)))
        self.assertFalse(bm.is_reversal(_row({"a": 0.9}, False), _row({"a": 0.8}, False)))

    def test_missing_promoted_returns_none(self):
        self.assertIsNone(bm.is_reversal(_row({"a": 0.9}, None), _row({"a": 0.8}, True)))


class TestArchiveDirection(unittest.TestCase):
    def test_filled_increase_is_up(self):
        self.assertEqual(bm.archive_direction(_row(qd=_qd(3)), _row(qd=_qd(5))), "up")

    def test_filled_decrease_is_down(self):
        self.assertEqual(bm.archive_direction(_row(qd=_qd(5)), _row(qd=_qd(3))), "down")

    def test_filled_unchanged_is_stable(self):
        self.assertEqual(bm.archive_direction(_row(qd=_qd(5)), _row(qd=_qd(5))), "stable")

    def test_missing_qd_is_stable(self):
        self.assertEqual(bm.archive_direction(_row(qd=None), _row(qd=None)), "stable")


class TestEraMetric(unittest.TestCase):
    def test_flip_and_reversal_counting(self):
        rows = [
            _row({"a": 0.9, "b": 0.1}, True, _qd(3), rid=1),
            _row({"a": 0.2, "b": 0.8}, True, _qd(3), rid=2),   # flip
            _row({"a": 0.2, "b": 0.8}, False, _qd(5), rid=3),  # reversal + up
            _row({"a": 0.9, "b": 0.1}, False, _qd(5), rid=4),  # flip
            _row({"a": 0.9, "b": 0.1}, True, _qd(2), rid=5),   # reversal + down
        ]
        m = bm.era_metric(rows)
        self.assertEqual(m["n_rows"], 5)
        self.assertEqual(m["transitions"], 4)
        self.assertEqual(m["flips"], 2)
        self.assertEqual(m["reversals"], 2)
        self.assertEqual(m["archive"]["direction"], {"up": 1, "down": 1, "stable": 2})

    def test_single_row_zero_rates(self):
        m = bm.era_metric([_row({"a": 0.9}, True)])
        self.assertEqual(m["n_rows"], 1)
        self.assertEqual(m["flips"], 0)
        self.assertEqual(m["reversals"], 0)
        self.assertEqual(m["flip_rate"], 0.0)
        self.assertEqual(m["reversal_rate"], 0.0)

    def test_empty_zero_rates(self):
        m = bm.era_metric([])
        self.assertEqual(m["n_rows"], 0)


class TestBacktrackMetric(unittest.TestCase):
    def test_groups_by_era_and_computes_overall(self):
        rows = [
            _row({"a": 0.9, "b": 0.1}, True, _qd(3), pool="era1", rid=1),
            _row({"a": 0.2, "b": 0.8}, True, _qd(3), pool="era1", rid=2),   # era1 flip
            _row({"a": 0.9, "b": 0.1}, True, _qd(3), pool="era2", rid=3),
            _row({"a": 0.2, "b": 0.8}, False, _qd(5), pool="era2", rid=4),  # era2 reversal
        ]
        report = bm.backtrack_metric(rows)
        self.assertEqual(report["n_rows"], 4)
        self.assertEqual(report["n_eras"], 2)
        # era1: rid1->rid2 flip (a->b); era2: rid3->rid4 flip (a->b) + reversal
        self.assertEqual(report["overall"]["flips"], 2)
        self.assertEqual(report["overall"]["reversals"], 1)
        self.assertEqual(report["eras"]["era1"]["flips"], 1)
        self.assertEqual(report["eras"]["era2"]["reversals"], 1)

    def test_degenerate_rows_without_pool(self):
        rows = [
            _row({"a": 0.9}, True, pool=None, rid=1),
            _row({"a": 0.2, "b": 0.8}, True, pool="era1", rid=2),
        ]
        report = bm.backtrack_metric(rows)
        self.assertEqual(report["degenerate_rows"], 1)
        self.assertEqual(report["n_eras"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
