# coding: utf-8
"""Tests for the 2026-09-16 brainstem playing-field findings and their fixes.

Six groups, each one a measurement taken off the LIVE store rather than a
reading of the code:

1. G1 ``q_effective`` -- shrinkage, because the raw EMA reaches its clip in
   ~22 pulls and 29 of 76 live keys were measured pinned at |q| > 0.999999,
   so ``q_order`` was sorting primarily on a plateau.
2. G2 ``invalidate_q`` -- the brainstem cannot forget, so a penalty outlives
   its cause and a deprioritised key is never re-chosen to clear it.
3. G3 ``audit_state`` / ``purge_fixtures`` -- the live store carries 10
   synthetic keys in the same namespaces as production worms, and nothing
   can tell them apart.
4. G4 the v1 -> v2 migration -- a field was added to the persisted schema
   with no backfill, so the two routes with the most successes in the system
   reported a pull count of zero.
5. G5 ``route_weight`` -- two files hardcoded a ceiling the arithmetic cannot
   reach, and both docstrings advertised it.
6. G6 the delta/target split -- the deepest one. 31 synapse edges had
   collapsed to 3 distinct values because a TD error was applied as a target.

Every test that asserts a *fix* also has a sibling that pins the *old*
behaviour, so a future change that re-introduces the defect fails loudly
instead of quietly passing.
"""

import json
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE import spine_registry as sr


class _LedgerCase(unittest.TestCase):
    """Removes every key it created from every table it touched."""

    PREFIX = "pftest"

    def setUp(self):
        self._keys = []

    def tearDown(self):
        for key in self._keys:
            sr._Q_WEIGHTS.pop(key, None)
            sr._Q_UPDATE_COUNTS.pop(key, None)
            sr._Q_VARIANCE.pop(key, None)
            sr._HOMEOSTAT.pop(key, None)
            with sr._SCAR_LOCK:
                sr._SCARS.pop(key, None)

    def track(self, key):
        """Register a key for teardown (also used for keys built by hand)."""
        if key not in self._keys:
            self._keys.append(key)
        return key

    def key(self, suffix):
        return self.track("%s:%s" % (self.PREFIX, suffix))

    def seed(self, key, q, n=None, successes=None, failures=None):
        sr._Q_WEIGHTS[key] = q
        if n is not None:
            sr._Q_UPDATE_COUNTS[key] = n
        if successes is not None or failures is not None:
            sr._HOMEOSTAT[key] = {"successes": successes or 0,
                                  "failures": failures or 0,
                                  "last_ms": 0.0, "last_at": "",
                                  "hist_ms": [], "consec_fail": 0}
        return key


# ── G1: evidence shrinkage ──────────────────────────────────────────────

class TestEvidenceShrinkage(_LedgerCase):
    def test_shrinkage_factors(self):
        """q * n/(n+K), at K = 8."""
        self.assertAlmostEqual(sr.q_effective_from(-1.0, 4), -1.0 / 3.0, places=6)
        self.assertAlmostEqual(sr.q_effective_from(-0.9, 40), -0.75, places=6)
        self.assertEqual(sr.q_effective_from(0.5, 0), 0.0)
        self.assertEqual(sr.q_effective_from(1.0, 1), 1.0 / 9.0)

    def test_thin_evidence_outranks_thick_evidence_of_the_same_sign(self):
        """A barely-tested key must not be ranked as a proven bad one.

        This is the assertion that fails on the raw table: -1.0 sorts below
        -0.9, so the 4-pull key loses to the 40-pull key and the mesh
        starves the candidate it has barely tried.
        """
        thin = self.seed(self.key("thin"), -1.0, n=4)
        thick = self.seed(self.key("thick"), -0.9, n=40)
        self.assertLess(sr._Q_WEIGHTS[thin], sr._Q_WEIGHTS[thick])   # raw: thin loses
        self.assertGreater(sr.q_effective_from(-1.0, 4),
                           sr.q_effective_from(-0.9, 40))            # shrunk: thin wins

    def test_q_order_ranks_on_the_shrunk_value(self):
        thin = self.seed(self.key("thin"), -1.0, n=4)
        thick = self.seed(self.key("thick"), -0.9, n=40)
        ordered = sr.q_order(self.PREFIX, ["thick", "thin"])
        self.assertEqual(ordered[0], "thin",
                         "q_order must rank on evidence, not on the raw EMA")
        self.assertEqual(ordered, ["thin", "thick"])
        self.assertTrue(thick.endswith("thick"))

    def test_clip_is_a_plateau_under_raw_weights(self):
        """Pins the measured defect: distinct evidence, identical raw value."""
        vals = set()
        for i, n in enumerate((10, 22, 31)):
            key = self.key("clip%d" % i)
            self.seed(key, 0.9999997732520536, n=n)
            vals.add(round(sr._Q_WEIGHTS[key], 9))
        self.assertEqual(len(vals), 1, "the raw table is a plateau at the clip")
        eff = {round(sr.q_effective_from(0.9999997732520536, n), 6)
               for n in (10, 22, 31)}
        self.assertEqual(len(eff), 3, "shrinkage separates them by evidence")

    def test_evidence_falls_back_to_the_ledger_when_no_count_exists(self):
        key = self.seed(self.key("legacy"), 0.25, successes=7, failures=3)
        ev = sr.get_q_evidence(key)
        self.assertEqual(ev["evidence"], 10)
        self.assertEqual(ev["source"], "homeostat")
        self.assertFalse(ev["count_recorded"])

    def test_evidence_prefers_a_recorded_count_even_when_zero(self):
        """Presence is the marker; 0 evidence is a decision, not an absence."""
        key = self.seed(self.key("zeroed"), 0.25, n=0, successes=9, failures=1)
        ev = sr.get_q_evidence(key)
        self.assertEqual(ev["evidence"], 0)
        self.assertEqual(ev["source"], "measured")
        self.assertTrue(ev["count_recorded"])


# ── G2: invalidation ────────────────────────────────────────────────────

class TestInvalidation(_LedgerCase):
    def test_invalidate_halves_evidence_and_raises_the_effective_value(self):
        key = self.seed(self.key("route_repair"), -0.270833, n=60)
        before = sr.get_q_evidence(key)["q_effective"]
        out = sr.invalidate_q(key, reason="unit test")
        self.assertTrue(out["success"])
        self.assertEqual(out["evidence_from"], 60)
        self.assertEqual(out["evidence_to"], 30)
        after = sr.get_q_evidence(key)["q_effective"]
        self.assertGreater(after, before,
                           "discounting stale evidence must raise the reading")

    def test_invalidation_cannot_be_undone_by_the_ledger_fallback(self):
        """The whole point of writing 0 instead of deleting the entry."""
        key = self.seed(self.key("no_resurrect"), -0.9, successes=30,
                        failures=30)
        self.assertEqual(sr.get_q_evidence(key)["evidence"], 60)
        sr.invalidate_q(key, factor=0.0)
        ev = sr.get_q_evidence(key)
        self.assertEqual(ev["evidence"], 0,
                         "the homeostat total must not resurrect discounted evidence")
        self.assertTrue(ev["count_recorded"])

    def test_invalidation_is_journalled_and_evented(self):
        key = self.seed(self.key("journalled"), -0.5, n=20)
        sr.invalidate_q(key, reason="fixed in commit deadbeef")
        entry = sr.get_policy_latest().get(key)
        self.assertIsNotNone(entry, "invalidation must reach the policy journal")
        self.assertEqual(entry.get("action"), "q_invalidated")
        kinds = [e.get("kind") for e in sr.get_spine_events()]
        self.assertIn("q_invalidated", kinds)

    def test_invalidate_rejects_an_empty_key(self):
        self.assertFalse(sr.invalidate_q("", reason="x")["success"])


# ── G3: provenance ──────────────────────────────────────────────────────

class TestProvenance(_LedgerCase):
    def test_fixture_detection_is_declared_and_category_scoped(self):
        self.assertTrue(sr._is_fixture_key("mesh:BadWorm"))
        self.assertTrue(sr._is_fixture_key("mesh:W0"))
        self.assertTrue(sr._is_fixture_key("learning:W1"))
        # A composite name is an edge/site, never a bare fixture worm: deleting
        # these would destroy real routing history over synthetic endpoints.
        self.assertFalse(sr._is_fixture_key("synapse:W0>W1"))
        self.assertFalse(sr._is_fixture_key("route:W0>W1"))
        # Real worms whose shape resembles a fixture must survive.
        self.assertFalse(sr._is_fixture_key("mesh:MeshRouterWorm"))
        self.assertFalse(sr._is_fixture_key("diagnostic:ChaosWorm"))

    def test_witnessed_fixtures_are_backed_by_real_test_references(self):
        """A witnessed declaration with no fixture behind it is rot.

        Only the witnessed subset is checked, because only it is a checkable
        claim: the purge set legitimately contains ORPHANED synthetic names
        (`mesh:T0/T1/T2`) whose creating fixture no longer exists anywhere in
        the tree. Conflating the two would either fail here or leave the
        orphans live in the brainstem.
        """
        tests_dir = Path(__file__).resolve().parent
        blob = ""
        for path in tests_dir.rglob("*.py"):
            if path.name == Path(__file__).name:
                continue
            try:
                blob += path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
        self.assertTrue(sr._FIXTURE_WORMS_WITNESSED <= sr._FIXTURE_WORMS)
        for name in sorted(sr._FIXTURE_WORMS_WITNESSED):
            self.assertIn(name, blob,
                          "witnessed fixture %r appears in no test file" % name)

    def test_the_mirrored_triple_is_declared_without_a_living_fixture(self):
        """Pins the orphan: synthetic keys whose creator is gone.

        `mesh:T0` at -0.9217 and `mesh:T1`/`T2` at +0.9217 is an exact
        mirror-image triple. It is purged (so it stops ranking against real
        mesh worms) but NOT witnessed (nothing in the tree creates it), and
        that gap is the reason the two sets are separate declarations.
        """
        for name in ("T0", "T1", "T2"):
            self.assertIn(name, sr._FIXTURE_WORMS)
            self.assertNotIn(name, sr._FIXTURE_WORMS_WITNESSED)

    def test_audit_state_classifies_every_bucket(self):
        self.seed(self.key("measured"), 0.3, n=5)
        self.seed(self.key("bootstrapped"), 0.3, successes=4, failures=1)
        self.seed(self.key("unwitnessed"), 0.3)
        self.track(self.seed("mesh:BadWorm", -0.85))
        report = sr.audit_state()
        buckets = report["buckets"]
        self.assertIn("%s:measured" % self.PREFIX, buckets["measured"])
        self.assertIn("%s:bootstrapped" % self.PREFIX, buckets["bootstrapped"])
        self.assertIn("%s:unwitnessed" % self.PREFIX, buckets["unwitnessed"])
        self.assertIn("mesh:BadWorm", buckets["fabricated"])
        self.assertFalse(report["trustworthy"])

    def test_purge_fixtures_removes_only_fixtures(self):
        real = self.seed(self.key("real"), 0.42, n=9)
        fixture = self.track(self.seed("mesh:T0", -0.1, n=2))
        out = sr.purge_fixtures()
        self.assertTrue(out["success"])
        self.assertGreaterEqual(out["removed_count"], 1)
        self.assertIn(fixture, out["removed"]["q_weights"])
        self.assertIn(real, sr._Q_WEIGHTS, "a real key must survive the purge")
        self.assertNotIn(fixture, sr._Q_WEIGHTS)
        self.assertNotIn(fixture, sr._Q_UPDATE_COUNTS)
        self.assertNotIn(fixture, sr._HOMEOSTAT)


# ── G4: the missing migration ───────────────────────────────────────────

class TestStateMigration(_LedgerCase):
    def _v1_payload(self):
        return {
            "version": "1.0.0",
            "q_weights": {"%s:route_a" % self.PREFIX: 0.9999,
                          "%s:route_b" % self.PREFIX: -0.27,
                          "%s:orphan" % self.PREFIX: 0.5},
            "q_update_counts": {},
            "homeostat": {"%s:route_a" % self.PREFIX: {"successes": 27, "failures": 0},
                          "%s:route_b" % self.PREFIX: {"successes": 30, "failures": 30}},
        }

    def test_migrate_backfills_from_the_payloads_own_ledger(self):
        for s in ("route_a", "route_b", "orphan"):
            self.key(s)
        payload = self._v1_payload()
        report = sr._migrate_state(payload)
        self.assertTrue(report["previous_schema"])
        self.assertEqual(report["from_version"], 0)
        self.assertEqual(report["to_version"], sr._STATE_SCHEMA_VERSION)
        backfilled = {b["key"]: b["evidence"] for b in report["backfilled"]}
        self.assertEqual(backfilled["%s:route_a" % self.PREFIX], 27)
        self.assertEqual(backfilled["%s:route_b" % self.PREFIX], 60)
        self.assertNotIn("%s:orphan" % self.PREFIX, backfilled,
                         "a key with no ledger record has no evidence to recover")

    def test_migration_is_idempotent(self):
        """The second pass must find NOTHING left to do.

        `backfilled` is the work done, not a persistent property, so the
        idempotency assertion is that the first pass repairs and the second
        is a no-op -- the repaired payload is what makes it a no-op.
        """
        for s in ("route_a", "route_b", "orphan"):
            self.key(s)
        payload = self._v1_payload()
        first = sr._migrate_state(payload)
        counts_after_first = dict(payload["q_update_counts"])
        second = sr._migrate_state(payload)
        self.assertEqual(len(first["backfilled"]), 2)
        self.assertEqual(second["backfilled"], [])
        self.assertEqual(payload["q_update_counts"], counts_after_first)

    def test_load_applies_the_migration_end_to_end(self):
        key = self.key("route_a")
        sr._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        sr._STATE_FILE.write_text(json.dumps(self._v1_payload()), encoding="utf-8")
        sr.load_spine_state()
        ev = sr.get_q_evidence(key)
        self.assertEqual(ev["evidence"], 27)
        self.assertTrue(ev["count_recorded"],
                        "the count must be materialised, not re-derived per read")

    def test_save_declares_the_schema_version(self):
        out = sr.save_spine_state()
        self.assertTrue(out.get("saved"))
        payload = json.loads(sr._STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("schema_version"), sr._STATE_SCHEMA_VERSION)


# ── G5: the derived route-weight bound ──────────────────────────────────

class TestRouteWeight(_LedgerCase):
    def test_the_declared_ceiling_was_unreachable(self):
        """Pins the measured defect, so the claim cannot quietly return."""
        rng = sr.route_weight_range(1.0)
        self.assertEqual(rng["max_reachable"], 2.0)
        self.assertEqual(rng["declared_max"], 5.0)
        self.assertLess(rng["max_reachable"], rng["declared_max"])

    def test_route_weight_clamps_to_the_reachable_range(self):
        """Inside the clip, the output never escapes the reachable range.

        The declared 5.0 is only reachable by feeding a q outside
        [_Q_MIN, _Q_MAX] -- i.e. by an input the brainstem cannot produce --
        which is the whole shape of the defect: the bound was written for a
        quantity that does not exist.
        """
        rng = sr.route_weight_range(1.0)
        self.assertEqual(sr.route_weight(sr._Q_MAX, 1.0), rng["max_reachable"])
        self.assertEqual(sr.route_weight(sr._Q_MIN, 1.0), rng["min_reachable"])
        for q in (-1.0, -0.5, 0.0, 0.5, 1.0):
            w = sr.route_weight(q, 1.0)
            self.assertGreaterEqual(w, rng["min_reachable"])
            self.assertLessEqual(w, rng["max_reachable"])
        self.assertEqual(sr.route_weight(5.0, 1.0), rng["declared_max"],
                         "only an impossible q reaches the declared ceiling")

    def test_heavy_registered_weights_move_the_reachable_ceiling(self):
        rng = sr.route_weight_range(4.0)
        self.assertEqual(rng["max_reachable"], 5.0)
        self.assertEqual(rng["min_reachable"], 0.1)

    def test_route_weight_learned_reports_its_evidence(self):
        key = self.seed("route:%s>dst" % self.PREFIX, -1.0, n=4)
        out = sr.route_weight_learned(self.PREFIX, "dst", 1.0)
        self.assertEqual(out["key"], key)
        self.assertEqual(out["evidence"], 4)
        self.assertAlmostEqual(out["q_effective"], -1.0 / 3.0, places=4)
        self.assertGreater(out["effective_weight"], sr.route_weight(-1.0, 1.0),
                           "a thin penalty must not floor the route")


# ── G6: delta vs target ─────────────────────────────────────────────────

class TestDeltaVersusTarget(_LedgerCase):
    def test_target_form_collapses_every_key_to_the_target(self):
        """Characterises the live defect: 25 synapse edges at one value.

        A near-constant signal applied as a TARGET drags every key to the same
        number, because the target is what q converges to -- the key's history
        is discarded rather than accumulated.
        """
        for i in range(10):
            sr.record_outcome(self.PREFIX, "tgt%d" % i, True, 0.0, reward=0.0909)
        vals = {round(sr._Q_WEIGHTS["%s:tgt%d" % (self.PREFIX, i)], 6)
                for i in range(10)}
        for i in range(10):
            self.key("tgt%d" % i)
        self.assertEqual(len(vals), 1,
                         "a target applied to 10 keys yields ONE value")

    def test_delta_form_keeps_distinct_histories_distinct(self):
        """The assertion that would have caught the live collapse."""
        n = 20
        for i in range(n):
            sr.record_outcome(self.PREFIX, "d%d" % i, True, 0.0,
                              delta=0.09 + 0.01 * i)
        vals = {round(sr._Q_WEIGHTS["%s:d%d" % (self.PREFIX, i)], 6)
                for i in range(n)}
        self.assertEqual(len(vals), n,
                         "20 distinct TD errors must leave 20 distinct weights")

    def test_delta_accumulates_and_rises_monotonically(self):
        """Q += alpha*delta, so a positive delta must RAISE q, not approach it."""
        key = self.key("accum")
        seen = []
        for _ in range(6):
            sr.record_outcome(self.PREFIX, "accum", True, 0.0, delta=0.08)
            seen.append(sr._Q_WEIGHTS[key])
        self.assertEqual(seen, sorted(seen), "the edge must accumulate")
        self.assertGreater(seen[-1], 0.08 * 0.05,
                           "a delta is not a ceiling on the value")

    def test_delta_semantics_are_not_a_target(self):
        """Pins the distinction: target 0.09 vs delta 0.09 are different updates."""
        sr.record_outcome(self.PREFIX, "as_target", True, 0.0, reward=0.09)
        sr.record_outcome(self.PREFIX, "as_delta", True, 0.0, delta=0.09)
        target = sr._Q_WEIGHTS[self.key("as_target")]
        delta = sr._Q_WEIGHTS[self.key("as_delta")]
        self.assertAlmostEqual(target, 0.036, places=4)   # 0.4 * (0.09 - 0)
        self.assertAlmostEqual(delta, 0.036, places=4)    # 0.4 * 0.09 -- equal once
        # ... but only on the FIRST step. They diverge immediately after, which
        # is the whole reason the parameter needs a name.
        for _ in range(4):
            sr.record_outcome(self.PREFIX, "as_target", True, 0.0, reward=0.09)
            sr.record_outcome(self.PREFIX, "as_delta", True, 0.0, delta=0.09)
        target = sr._Q_WEIGHTS["%s:as_target" % self.PREFIX]
        delta = sr._Q_WEIGHTS["%s:as_delta" % self.PREFIX]
        self.assertLess(target, 0.09 + 1e-9, "a target cannot exceed itself")
        self.assertGreater(delta, 0.09, "a delta accumulates past its own value")

    def test_synapse_edge_learns_the_value_not_a_fraction_of_it(self):
        """THE regression test for the live collapse, at the CALL SITE.

        Fails on the pre-fix source. The two conventions have different fixed
        points, and that is the observable that the live store recorded:

            delta  form:  q += a*lr*(reward - q)        -> q -> reward      (0.4000)
            target form:  q += a*(lr*(reward - q) - q)  -> q -> lr*r/(1+lr) (0.0364)

        An 11x underestimate. The live synapse table held ~0.0909 where its
        outcomes implied a far larger value, which is what "31 edges, 3
        distinct values" was actually telling us.

        Note that *distinctness* is NOT the discriminator: with distinct
        rewards both forms stay distinct. Magnitude is, because a target caps
        the value at a fraction of the evidence while a delta accumulates it.
        """
        from CORE.WORMS.expansion.synapse import SynapseWeightWorm
        worm = SynapseWeightWorm(lr=0.1, gamma=0.9)
        self.track("synapse:pfval>dst")
        for _ in range(80):
            worm.update("pfval", "dst", reward=0.4)
        w = sr.get_q_weight("synapse", "pfval>dst")
        self.assertGreater(
            w, 0.3,
            "an edge whose outcomes are worth 0.4 must not settle at "
            "lr*r/(1+lr) = 0.036; got %.4f" % w)

    def test_synapse_update_keeps_edge_weights_distinct(self):
        """Guard, not a regression test: both conventions pass this.

        Kept because a future change that re-couples the edges (a shared
        target, a shared key) should fail here even though it would not be
        caught by the magnitude test above.
        """
        from CORE.WORMS.expansion.synapse import SynapseWeightWorm
        worm = SynapseWeightWorm(lr=0.1, gamma=0.9)
        n = 12
        for i in range(n):
            src = "pfedge%d" % i
            self.track("synapse:%s>dst" % src)
            for _ in range(3):
                worm.update(src, "dst", reward=0.4 + 0.05 * i)
        vals = {sr.get_q_weight("synapse", "pfedge%d>dst" % i) for i in range(n)}
        self.assertEqual(len(vals), n,
                         "12 distinct edge histories must yield 12 distinct weights")
        self.assertTrue(all(v > 0.0 for v in vals),
                        "positive-mean outcomes must leave positive weights")

    def test_invalid_delta_is_a_no_op_not_a_sign_fallback(self):
        """Passing garbage must not silently substitute a target for a delta."""
        key = self.key("bad_delta")
        sr.record_outcome(self.PREFIX, "bad_delta", True, 0.0, delta=None)
        # delta=None means "not supplied", so this is the target path.
        self.assertIn(key, sr._Q_WEIGHTS)
        sr._Q_WEIGHTS[key] = 0.0
        sr.record_outcome(self.PREFIX, "bad_delta", True, 0.0, delta="nonsense")
        self.assertEqual(sr._Q_WEIGHTS[key], 0.0)


if __name__ == "__main__":
    unittest.main()
