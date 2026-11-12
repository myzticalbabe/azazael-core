# coding: utf-8
"""Tests for F-P0-2: empty-band tie-break + BLOCKED-streak escalation.

Falsifiable claims:
  1. Degenerate (all-zero EI) grains: best = largest reachable support, tie=True.
  2. Distinct EIs: tie=False, best = max EI.
  3. _consecutive_blocked counts only the trailing same-reason BLOCKED run.
  4. _maybe_escalate writes escalation.json at streak>=3, never before, and
     always stamps blocked_streak on the report.
"""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion import exmachina as ex  # noqa: E402


def _load_cycle(tmpdir):
    import tempfile
    state = Path(tempfile.mkdtemp(dir=str(tmpdir))) / "state"
    spec = importlib.util.spec_from_file_location(
        "meta_evolution_cycle_test", ROOT / "scripts" / "meta_evolution_cycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.STATE_DIR = state
    mod.REPORT_PATH = state / "current.json"
    mod.LINEAGE_PATH = state / "lineage.jsonl"
    mod.PATCH_DIR = state / "patches"
    mod.ESCALATION_PATH = state / "escalation.json"
    return mod


class TestEmptyBandTie(unittest.TestCase):
    def test_degenerate_grains_tie_with_support_winner(self):
        # Constant series: every projection visits one state -> all EI 0.0.
        out = ex.causal_emergence([5] * 40, n_bits=3, include_pairs=False)
        self.assertTrue(out["tie"])
        self.assertFalse(out["emerges"])
        self.assertIsNotNone(out["best_grain"])

    def test_distinct_eis_no_tie(self):
        import random
        rng = random.Random(4)
        series = [rng.getrandbits(4) for _ in range(300)]
        out = ex.causal_emergence(series, n_bits=4, include_pairs=False)
        self.assertFalse(out["tie"])
        eis = [c["ei"] for c in out["candidates"]]
        self.assertEqual(out["ei_best"], max(eis))

    def test_short_series_unchanged_shape(self):
        out = ex.causal_emergence([1, 2, 3], n_bits=2)
        self.assertIn("note", out)
        self.assertNotIn("tie", out)


class TestBlockedEscalation(unittest.TestCase):
    def _lineage(self, mod, rows):
        mod.LINEAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with mod.LINEAGE_PATH.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def test_consecutive_counts_trailing_run_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mod = _load_cycle(td)
            self._lineage(mod, [
                {"verdict": "BLOCKED", "reason": "A"},
                {"verdict": "OK", "reason": ""},
                {"verdict": "BLOCKED", "reason": "B"},
                {"verdict": "BLOCKED", "reason": "B"},
            ])
            self.assertEqual(mod._consecutive_blocked("B"), 2)
            self.assertEqual(mod._consecutive_blocked("A"), 0)

    def test_dry_rows_skipped_not_breaking(self):
        """A manual observation-only run between apply runs must not reset a
        live BLOCKED streak (regression test for my own 2026-09-26 probe)."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mod = _load_cycle(td)
            self._lineage(mod, [
                {"verdict": "BLOCKED", "reason": "R"},
                {"verdict": "DRY", "reason": "observation complete"},
                {"verdict": "BLOCKED", "reason": "R"},
            ])
            self.assertEqual(mod._consecutive_blocked("R"), 2)

    def test_no_escalation_before_third_strike(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mod = _load_cycle(td)
            self._lineage(mod, [{"verdict": "BLOCKED", "reason": "R"}])
            rep = {"verdict": "BLOCKED", "reason": "R"}
            mod._maybe_escalate(rep)
            self.assertEqual(rep["blocked_streak"], 1)
            self.assertNotIn("escalation", rep)
            self.assertFalse(mod.ESCALATION_PATH.exists())

    def test_escalation_on_third_strike(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mod = _load_cycle(td)
            self._lineage(mod, [
                {"verdict": "BLOCKED", "reason": "R"},
                {"verdict": "BLOCKED", "reason": "R"},
            ])
            rep = {"verdict": "BLOCKED", "reason": "R",
                   "candidate": {"id": "abc123"}}
            mod._maybe_escalate(rep)
            self.assertEqual(rep["blocked_streak"], 2)
            # Only 2 in a row -> no escalation yet.
            self.assertNotIn("escalation", rep)
            # Third consecutive BLOCKED -> escalates.
            with mod.LINEAGE_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"verdict": "BLOCKED", "reason": "R"}) + "\n")
            rep2 = {"verdict": "BLOCKED", "reason": "R",
                    "candidate": {"id": "abc123"}}
            mod._maybe_escalate(rep2)
            self.assertEqual(rep2["blocked_streak"], 3)
            self.assertTrue(mod.ESCALATION_PATH.exists())
            esc = json.loads(mod.ESCALATION_PATH.read_text(encoding="utf-8"))
            self.assertEqual(esc["consecutive_blocked"], 3)
            self.assertIn("owner_action", esc)
            self.assertIn("escalation", rep2)

    def test_ok_verdict_never_escalates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mod = _load_cycle(td)
            rep = {"verdict": "OK", "reason": ""}
            mod._maybe_escalate(rep)
            self.assertNotIn("blocked_streak", rep)
            self.assertFalse(mod.ESCALATION_PATH.exists())


if __name__ == "__main__":
    unittest.main()
