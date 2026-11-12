"""Tests for the adaptive budget policy (2026-09-12).

A budget must be able to GROW when a run hits it and SHRINK when it does not,
must stay inside its floor/ceiling, and every change must be recorded. The
regression this guards: fixed caps silently returned less work than asked for
(the uncovered-module list was truncated to 50 of 262 and nothing expanded).

Every test redirects the ledger to a tmp path so the real
state/budget_policy.json is never touched.
"""
import json
import os
import sys
import tempfile
import unittest

_scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import budget_policy as bp  # noqa: E402


class BudgetPolicyTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._real_state = bp.STATE
        bp.STATE = os.path.join(self._tmp.name, "budget_policy.json")

    def tearDown(self):
        bp.STATE = self._real_state
        self._tmp.cleanup()

    def _write(self, doc):
        with open(bp.STATE, "w", encoding="utf-8") as f:
            json.dump(doc, f)


class TestDefaults(BudgetPolicyTestCase):
    def test_missing_ledger_uses_floors(self):
        for name, (floor, initial, _ceiling) in bp.DEFAULTS.items():
            self.assertEqual(bp.get(name), initial,
                             "%s should start at its initial value" % name)
            self.assertEqual(bp.bounds(name)[0], floor)

    def test_unknown_name_is_zero_not_a_crash(self):
        self.assertEqual(bp.get("no.such.budget"), 0)

    def test_corrupt_ledger_degrades_to_defaults(self):
        with open(bp.STATE, "w", encoding="utf-8") as f:
            f.write("{not json at all")
        self.assertEqual(bp.get("audit.pattern_cap"), 25)

    def test_inverted_bounds_are_repaired(self):
        self._write({"budgets": {"audit.pattern_cap":
                                 {"floor": 900, "value": 5, "ceiling": 10}}})
        floor, value, ceiling = bp.bounds("audit.pattern_cap")
        self.assertLessEqual(floor, ceiling)
        self.assertGreaterEqual(value, floor)
        self.assertLessEqual(value, ceiling)


class TestEscalation(BudgetPolicyTestCase):
    def test_escalate_doubles_and_records(self):
        new = bp.escalate("audit.pattern_cap", "hit the cap")
        self.assertEqual(new, 50)
        self.assertEqual(bp.get("audit.pattern_cap"), 50)
        hist = bp.load()["history"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["action"], "escalate")
        self.assertEqual((hist[0]["from"], hist[0]["to"]), (25, 50))
        self.assertIn("hit the cap", hist[0]["reason"])

    def test_escalate_jumps_to_needed_in_one_step(self):
        new = bp.escalate("audit.pattern_cap", "7242 hits", needed=7242)
        self.assertEqual(new, 1000, "should clamp to the ceiling")

    def test_escalate_is_bounded_by_the_ceiling(self):
        for _ in range(20):
            bp.escalate("audit.pattern_cap", "still hitting it")
        self.assertEqual(bp.get("audit.pattern_cap"), 1000)

    def test_settle_returns_to_the_floor(self):
        bp.escalate("mutate.max_mutants", "sampled")
        bp.escalate("mutate.max_mutants", "sampled again")
        self.assertEqual(bp.get("mutate.max_mutants"), 80)
        bp.settle("mutate.max_mutants", "not hit")
        bp.settle("mutate.max_mutants", "not hit")
        bp.settle("mutate.max_mutants", "not hit")
        self.assertEqual(bp.get("mutate.max_mutants"), 20)
        self.assertEqual(bp.get("mutate.max_mutants"),
                         bp.bounds("mutate.max_mutants")[0])


class TestExplicitSet(BudgetPolicyTestCase):
    def test_set_value_clamps_and_records(self):
        got = bp.set_value("audit.pattern_cap", 99999, "explicit --pattern-cap")
        self.assertEqual(got, 1000)
        actions = [h["action"] for h in bp.load()["history"]]
        self.assertEqual(actions, ["set"])

    def test_snapshot_lists_every_budget(self):
        snap = bp.snapshot()
        self.assertEqual(set(snap), set(bp.DEFAULTS))


class TestWiredConsumers(unittest.TestCase):
    """The consumers must actually call the policy, not re-hardcode the caps."""

    def test_repo_audit_reads_adaptive_budgets(self):
        import importlib
        ra = importlib.import_module("repo_audit")
        self.assertTrue(hasattr(ra, "bp"))
        self.assertTrue(hasattr(ra, "testmap_coverage"))
        # the display slice must be a budget, never a hard-coded [:50]
        with open(ra.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("uncovered[:50]", src)
        self.assertIn("uncovered_shown", src)
        # coverage must come from the import graph, not name substrings
        self.assertIn("import_map", src)
        self.assertNotIn("if m not in blob", src)

    def test_repo_audit_uses_per_pattern_budgets(self):
        import importlib
        ra = importlib.import_module("repo_audit")
        with open(ra.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"audit.pattern." + name', src)
        # the seed must be the FLOOR of the shared budget, not its value:
        # a value-inherited seed handed every pattern the noise-driven cap.
        self.assertIn('bp.bounds("audit.pattern_cap")[0]', src)


class TestDynamicBudgets(BudgetPolicyTestCase):
    def test_ensure_registers_once_and_is_idempotent(self):
        first = bp.ensure("audit.pattern.todo", 25, 25, 1000)
        bp.escalate("audit.pattern.todo", "hit it", needed=400)
        second = bp.ensure("audit.pattern.todo", 25, 25, 1000)
        self.assertEqual(first, 25)
        self.assertEqual(second, 400, "an existing budget must keep its value")

    def test_dynamic_names_survive_a_reload(self):
        bp.ensure("audit.pattern.custom", 25, 25, 500)
        self.assertIn("audit.pattern.custom", bp.snapshot())
        self.assertIn("audit.pattern.custom", bp.load()["budgets"])

    def test_dynamic_budget_is_isolated_from_its_siblings(self):
        bp.ensure("audit.pattern.noise", 25, 25, 1000)
        bp.ensure("audit.pattern.risk", 25, 25, 1000)
        bp.escalate("audit.pattern.noise", "7000 hits", needed=7000)
        self.assertEqual(bp.get("audit.pattern.noise"), 1000)
        self.assertEqual(bp.get("audit.pattern.risk"), 25,
                         "one pattern must not spend another's budget")

    def test_repo_audit_settles_only_with_headroom(self):
        """Regression: settling at count <= cap oscillated (165 -> 82 -> 165)."""
        import importlib
        ra = importlib.import_module("repo_audit")
        with open(ra.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('v["count"] * 2 <= v["cap"]', src)
        self.assertIn("len(files) * 2 <= max_files", src)

    def test_mutate_reads_adaptive_budgets(self):
        import importlib
        mut = importlib.import_module("mutate")
        self.assertTrue(hasattr(mut, "bp"))
        with open(mut.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("default=20", src)
        self.assertNotIn("default=300", src)


if __name__ == "__main__":
    unittest.main()
