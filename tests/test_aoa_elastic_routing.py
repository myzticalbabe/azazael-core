# coding: utf-8
"""Elastic AOA arm selection, and the provenance envelope that feeds it.

Two coupled defects, both measured on 2026-09-14:

1. `CrossArmRouter.route_task` required the caller to name the arm. It had
   **zero** references to the spine ledger, so the per-route Q-values that the
   rest of the mesh learns from could not influence which Arm ran. The
   learning existed; the governance did not.

2. Arm results carried no provenance. `{"status": "ok", "data": {...}}` says
   nothing about when the datum was produced or how long it stays meaningful,
   so a consumer cannot tell a live reading from a replay. This is the same
   defect class already repaired in the four live monitors.

Wiring them together is where the real hazard lives, and four live inference
providers (OpenRouter, Cohere, NVIDIA NIM, Gemini) independently identified it
when reviewing the design: a selector that ranks by Q starves any Arm it has
never tried, because `get_q_weight()` returns 0.0 for an unmeasured Arm exactly
as it does for one that has settled neutral. The untried Arm sorts last, is
never chosen, and so is never measured. `TestColdStart` is that guard.

The router also WRITES outcomes back into the table it SELECTS from, which is a
self-reinforcement loop by construction. `TestFeedbackLoopGuard` pins the three
properties that keep it bounded: a write happens only after execution, an
unmeasured Arm cannot be locked out, and one Arm cannot dominate the table.

Evidence for the design ordering: `.inference_cache/20260914_134832_*.log`
(phase `aoa_v2_design_review`).
"""
import os
import sys
import unittest

import pytest

# 2026-10-02: skip lifted. The RoutingPolicy/select_arm API was restored to
# src/AOA/core/router.py (adopted from the WSL mirror), so the spec this file
# was holding open now has its API again.

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AOA = os.path.join(_project_root, "src", "AOA")
for _p in (_project_root, _AOA, os.path.join(_project_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import router as router_mod       # noqa: E402
from core.contracts import (                # noqa: E402
    ArmResult, check_result, validate_result, ContractReport,
    parse_timestamp, now_iso, PROVENANCE_OBSERVED, PROVENANCE_DECLARED,
)
from core.router import (                    # noqa: E402
    CrossArmRouter,
    RoutingPolicy,
    ARM_ROUTE_NATURE,
)


# ── doubles ────────────────────────────────────────────────────────────
# Deliberately hand-built rather than mocked: these exist to pin the contract
# the real arms must satisfy, and a Mock would happily accept a wrong shape.

class _Cap:
    def __init__(self, name):
        self.name = name


class _Arm:
    is_initialized = True

    def __init__(self, arm_id, caps, priority=5, body=None, raises=False):
        self._arm_id = arm_id
        self._caps = [_Cap(c) for c in caps]
        self.metadata = type("M", (), {"priority": priority,
                                       "arm_id": arm_id})()
        self._body = body if body is not None else {"status": "ok",
                                                    "data": {"arm": arm_id}}
        self._raises = raises
        self.calls = 0

    def initialize(self):
        self.is_initialized = True
        return True

    def execute(self, capability, payload=None):
        self.calls += 1
        if self._raises:
            raise RuntimeError("arm exploded")
        return dict(self._body)

    def get_capabilities(self):
        return list(self._caps)


class _Registry:
    def __init__(self, arms):
        self._arms = {a._arm_id: a for a in arms}

    @property
    def arms(self):
        return self._arms

    def get_arm(self, arm_id):
        return self._arms.get(arm_id)

    def list_arms(self):
        return [{"arm_id": a, "category": "quartet", "priority": 5}
                for a in self._arms]


class _Spine:
    """Minimal stand-in for CORE.spine_registry's Q surface."""

    def __init__(self, q=None, pulls=None):
        self.q = dict(q or {})
        self.pulls = dict(pulls or {})
        self.writes = []

    def get_q_weight(self, nature, worm):
        return float(self.q.get(worm, 0.0))

    def get_q_updates(self, nature, worm):
        return int(self.pulls.get(worm, 0))

    def record_outcome(self, nature, worm, success, duration_ms,
                       censored=False):
        self.writes.append({"nature": nature, "worm": worm,
                            "success": bool(success),
                            "ms": float(duration_ms),
                            "censored": bool(censored)})


class _SpineFixture(unittest.TestCase):
    """Installs a fake spine for the duration of a test."""

    def setUp(self):
        self._orig = router_mod._spine
        self.spine = _Spine()

    def tearDown(self):
        router_mod._spine = self._orig

    def use_spine(self, spine=None):
        if spine is not None:
            self.spine = spine
        router_mod._spine = lambda: self.spine
        return self.spine

    def break_spine(self):
        def _boom():
            raise RuntimeError("ledger unavailable")
        router_mod._spine = _boom

    @staticmethod
    def router(arms, policy=None):
        return CrossArmRouter(registry=_Registry(arms), policy=policy)


# ── envelope contract ──────────────────────────────────────────────────

class TestProvenanceEnvelope(unittest.TestCase):

    def test_fresh_result_is_not_stale(self):
        r = ArmResult(status="ok", arm_id="a", capability="c", data={"x": 1},
                      produced_at=now_iso(), stale_after_s=60.0,
                      provenance=PROVENANCE_DECLARED)
        self.assertIs(r.is_stale(), False)
        self.assertLess(r.age_s(), 5.0)
        self.assertEqual(check_result(r), [])

    def test_old_result_is_stale(self):
        r = ArmResult(status="ok", arm_id="a", capability="c", data={"x": 1},
                      produced_at="2026-09-14T00:00:00", stale_after_s=60.0)
        self.assertIs(r.is_stale(), True)
        self.assertGreater(r.age_s(), 60.0)

    def test_undecidable_staleness_is_none_not_false(self):
        """The whole point: "unknown" must not be reported as "fresh".

        An Arm that declares no lifetime is not making a freshness claim, so
        answering False would assert something it never said. Three states,
        not two.
        """
        r = ArmResult(status="ok", arm_id="a", capability="c", data={"x": 1})
        self.assertIsNone(r.is_stale())
        self.assertIsNone(r.stale_after_s)
        self.assertIn("stale_after_s_missing", check_result(r))

    def test_ok_with_no_data_makes_no_freshness_claim(self):
        """An "ok" carrying nothing has no datum to age -- not a violation."""
        r = ArmResult(status="ok", arm_id="a", capability="c", data=None)
        self.assertEqual(check_result(r), [])

    def test_aware_utc_timestamp_is_normalised_not_rejected(self):
        """A UTC-stamped result must be aged correctly, not silently dropped."""
        r = ArmResult(status="ok", arm_id="a", capability="c", data=1,
                      produced_at="2026-09-14T00:00:00+00:00", stale_after_s=60.0)
        parsed = parse_timestamp(r.produced_at)
        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed.tzinfo)
        self.assertGreater(r.age_s(), 60.0)
        self.assertEqual(check_result(r), [])

    def test_unknown_status_is_a_violation(self):
        r = ArmResult(status="banana", arm_id="a", capability="c", data=1,
                      stale_after_s=5.0)
        self.assertIn("unknown_status:banana", check_result(r))

    def test_non_positive_lifetime_is_a_violation(self):
        for bad in (0, -1):
            r = ArmResult(status="ok", arm_id="a", capability="c", data=1,
                          stale_after_s=bad)
            self.assertIn("stale_after_s_not_positive", check_result(r))

    def test_future_timestamp_is_a_violation(self):
        from datetime import datetime, timedelta
        r = ArmResult(status="ok", arm_id="a", capability="c", data=1,
                      produced_at=(datetime.now() + timedelta(hours=3)).isoformat(),
                      stale_after_s=60.0)
        self.assertIn("produced_at_in_future", check_result(r))

    def test_legacy_dict_is_adopted_and_labelled_honestly(self):
        """A bare dict is wrapped, but named for what it is.

        The router observed this value now; it does NOT know when the Arm
        computed it. Recording the observation as the Arm's own claim would
        make a fabricated timestamp indistinguishable from a measured one.
        """
        r = ArmResult.from_arm_output({"status": "ok", "data": {"n": 1}},
                                      "azazael", "get_status")
        self.assertEqual(r.provenance, PROVENANCE_OBSERVED)
        self.assertIsNone(r.stale_after_s)
        self.assertIn("stale_after_s_missing", check_result(r))
        self.assertEqual(r.data, {"n": 1})

    def test_policy_default_lifetime_is_not_attributed_to_the_arm(self):
        r = ArmResult.from_arm_output({"status": "ok", "data": 1}, "a", "c",
                                      stale_after_s=120.0)
        self.assertEqual(r.stale_after_s, 120.0)
        self.assertEqual(r.provenance, PROVENANCE_OBSERVED)

    def test_non_dict_arm_output_is_an_error_not_a_false_success(self):
        r = ArmResult.from_arm_output("oops", "azazael", "get_status")
        self.assertEqual(r.status, "error")
        self.assertIn("expected dict", r.error)

    def test_to_dict_preserves_the_legacy_shape(self):
        flat = ArmResult(status="ok", arm_id="a", capability="c",
                         data=[1], stale_after_s=30.0).to_dict()
        for legacy_key in ("status", "data", "arm_id"):
            self.assertIn(legacy_key, flat)
        self.assertEqual(flat["data"], [1])
        self.assertIn("is_stale", flat)
        self.assertIn("age_s", flat)

    def test_validator_counts_but_never_raises(self):
        report = ContractReport()
        bad = ArmResult(status="banana", arm_id="a", capability="c", data=1)
        violations = validate_result(bad, report=report)
        self.assertTrue(violations)
        self.assertEqual(report.total, 1)
        self.assertFalse(report.snapshot()["clean"])
        clean = ArmResult(status="ok", arm_id="a", capability="c", data=1,
                          stale_after_s=10.0)
        self.assertEqual(validate_result(clean, report=report), [])
        self.assertEqual(report.total, 1)
        self.assertEqual(report.checked, 2)


# ── elastic selection ──────────────────────────────────────────────────

class TestCapabilityIndex(_SpineFixture):

    def test_capability_index_maps_names_to_arms(self):
        r = self.router([_Arm("x", ["alpha", "beta"]), _Arm("y", ["beta"])])
        idx = r.capability_index()
        self.assertEqual(idx["alpha"], ["x"])
        self.assertEqual(idx["beta"], ["x", "y"])

    def test_index_re_derives_when_registry_changes(self):
        """A capability index latched forever is the monitors' cached-import bug."""
        a = _Arm("x", ["alpha"])
        router = self.router([a])
        self.assertEqual(router.capability_index()["alpha"], ["x"])
        router.registry._arms["z"] = _Arm("z", ["alpha"])
        self.assertEqual(router.capability_index()["alpha"], ["x", "z"])


class TestColdStart(_SpineFixture):
    """The guard four live providers independently asked for."""

    def test_untried_arm_is_chosen_over_a_proven_good_one(self):
        """An unmeasured Arm must be exercised, not starved.

        Arm `proven` has 50 successful pulls and Q=0.9. Arm `untried` has
        never run, so Q=0.0. Ranking purely on Q selects `proven` forever and
        `untried` is never measured -- the exact collapse.
        """
        proven = _Arm("proven", ["task"])
        untried = _Arm("untried", ["task"])
        self.use_spine(_Spine(q={"task>proven": 0.9},
                              pulls={"task>proven": 50}))
        router = self.router([proven, untried],
                             RoutingPolicy(min_pulls=30, epsilon=0.0))
        decision = router.select_arm("task")
        self.assertEqual(decision["arm_id"], "untried")
        self.assertEqual(decision["reason"], "cold_start")

    def test_least_observed_arm_wins_among_several_unproven(self):
        a = _Arm("a", ["task"])
        b = _Arm("b", ["task"])
        c = _Arm("c", ["task"])
        self.use_spine(_Spine(pulls={"task>a": 3, "task>b": 9, "task>c": 1}))
        router = self.router([a, b, c], RoutingPolicy(min_pulls=30, epsilon=0.0))
        self.assertEqual(router.select_arm("task")["arm_id"], "c")

    def test_quota_boundary_is_inclusive(self):
        """At exactly min_pulls an Arm becomes rankable."""
        a = _Arm("a", ["task"])
        b = _Arm("b", ["task"])
        self.use_spine(_Spine(q={"task>a": 0.1, "task>b": 0.9},
                              pulls={"task>a": 30, "task>b": 30}))
        router = self.router([a, b], RoutingPolicy(min_pulls=30, epsilon=0.0))
        decision = router.select_arm("task")
        self.assertEqual(decision["reason"], "exploit")
        self.assertEqual(decision["arm_id"], "b")

    def test_proven_arms_are_ranked_by_learned_q(self):
        a = _Arm("a", ["task"])
        b = _Arm("b", ["task"])
        self.use_spine(_Spine(q={"task>a": 0.9, "task>b": -0.4},
                              pulls={"task>a": 40, "task>b": 40}))
        router = self.router([a, b], RoutingPolicy(min_pulls=30, epsilon=0.0))
        self.assertEqual(router.select_arm("task")["arm_id"], "a")
        self.assertEqual(self.spine.writes, [])

    def test_selection_never_writes_to_the_table_it_reads(self):
        """A decision must not alter the evidence used to judge it."""
        self.use_spine(_Spine(q={"task>a": 0.5}, pulls={"task>a": 40}))
        router = self.router([_Arm("a", ["task"])],
                             RoutingPolicy(min_pulls=30, epsilon=0.0))
        for _ in range(5):
            router.select_arm("task")
        self.assertEqual(self.spine.writes, [])

    def test_degraded_read_falls_back_to_static_priority_and_says_so(self):
        """A damaged ledger must not silently read as 'everything is equal'."""
        low = _Arm("low", ["task"], priority=1)
        high = _Arm("high", ["task"], priority=9)
        self.break_spine()
        router = self.router([low, high])
        decision = router.select_arm("task")
        self.assertEqual(decision["arm_id"], "high")
        self.assertTrue(decision["degraded"])
        self.assertEqual(decision["reason"], "degraded_static_priority")

    def test_epsilon_reason_is_recorded_when_exploration_fires(self):
        a = _Arm("a", ["task"])
        b = _Arm("b", ["task"])
        self.use_spine(_Spine(q={"task>a": 0.9, "task>b": 0.1},
                              pulls={"task>a": 50, "task>b": 50}))
        router = self.router([a, b], RoutingPolicy(min_pulls=30, epsilon=1.0))
        decision = router.select_arm("task")
        self.assertEqual(decision["reason"], "epsilon")
        self.assertIn(decision["arm_id"], ("a", "b"))

    def test_no_candidate_is_explicit(self):
        self.use_spine()
        router = self.router([_Arm("a", ["other"])])
        self.assertEqual(router.select_arm("missing")["reason"], "no_candidate")


# ── routing, back-compat, and the feedback-loop guards ─────────────────

class TestRoutingBackCompat(_SpineFixture):

    def test_pinned_route_ignores_selection_entirely(self):
        chosen = _Arm("chosen", ["task"])
        other = _Arm("other", ["task"])
        self.use_spine(_Spine(q={"task>other": 0.9}, pulls={"task>other": 99}))
        router = self.router([chosen, other])
        out = router.route_task("chosen", "task", {})
        self.assertEqual(out["arm_id"], "chosen")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(other.calls, 0)
        self.assertNotIn("routing", out)
        self.assertEqual(router.get_selection_stats()["pinned"], 1)

    def test_elastic_route_resolves_the_arm_and_explains_itself(self):
        self.use_spine()
        router = self.router([_Arm("only", ["task"])])
        out = router.route_task(None, "task", {})
        self.assertEqual(out["arm_id"], "only")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["routed_by"], "aoa.router")
        self.assertEqual(out["routing"]["reason"], "cold_start")

    def test_result_carries_provenance_fields(self):
        self.use_spine()
        router = self.router([_Arm("only", ["task"])])
        out = router.route_task(None, "task", {})
        for key in ("produced_at", "provenance", "age_s"):
            self.assertIn(key, out)
        self.assertEqual(out["provenance"], PROVENANCE_OBSERVED)

    def test_undeclared_staleness_is_counted_not_raised(self):
        """Warn-only migration: a non-compliant Arm keeps working."""
        self.use_spine()
        router = self.router([_Arm("only", ["task"])])
        out = router.route_task(None, "task", {})
        self.assertEqual(out["status"], "ok")
        self.assertIn("stale_after_s_missing",
                      out["metadata"]["contract_violations"])

    def test_unknown_capability_is_an_error_naming_the_capability(self):
        self.use_spine()
        router = self.router([_Arm("a", ["other"])])
        out = router.route_task(None, "nope", {})
        self.assertEqual(out["status"], "error")
        self.assertIn("nope", out["error"])
        self.assertEqual(out["reason"], "no_candidate")

    def test_missing_arm_by_id_is_an_error(self):
        self.use_spine()
        router = self.router([_Arm("a", ["other"])])
        out = router.route_task("ghost", "other", {})
        self.assertEqual(out["status"], "error")
        self.assertIn("ghost", out["error"])

    def test_capability_is_required(self):
        self.use_spine()
        router = self.router([_Arm("a", ["other"])])
        self.assertEqual(router.route_task("a", None)["status"], "error")

    def test_replay_log_records_choice_and_outcome(self):
        self.use_spine()
        router = self.router([_Arm("a", ["task"])])
        router.route_task(None, "task", {})
        log = router.get_routing_log()
        self.assertEqual(len(log), 1)
        entry = log[0]
        self.assertEqual(entry["arm_id"], "a")
        self.assertEqual(entry["selected_by"], "cold_start")
        self.assertTrue(entry["success"])
        self.assertTrue(entry["outcome_recorded"])

    def test_workflow_step_accepts_an_empty_arm_id_for_elastic_routing(self):
        self.use_spine()
        router = self.router([_Arm("a", ["task"])])
        wf = router.create_workflow("elastic")
        from core.router import WorkflowStep
        wf.add_step(WorkflowStep(step_id="s1", arm_id="", capability="task"))
        results = router.execute_workflow(wf.workflow_id)
        self.assertEqual(results[0]["resolved_arm_id"], "a")
        self.assertEqual(results[0]["status"], "completed")


class TestFeedbackLoopGuard(_SpineFixture):
    """The router writes to the table it selects from. Pin the bounds."""

    def test_outcome_is_written_only_after_execution(self):
        arm = _Arm("a", ["task"])
        spine = self.use_spine()
        router = self.router([arm])
        router.route_task(None, "task", {})
        self.assertEqual(arm.calls, 1)
        self.assertEqual(len(spine.writes), 1)
        self.assertTrue(spine.writes[0]["success"])
        self.assertEqual(spine.writes[0]["nature"], ARM_ROUTE_NATURE)
        self.assertEqual(spine.writes[0]["worm"], "task>a")

    def test_a_failing_arm_is_recorded_as_a_failure(self):
        """Failure is evidence too, or a bad Arm can never be demoted."""
        arm = _Arm("a", ["task"], raises=True)
        spine = self.use_spine()
        router = self.router([arm])
        out = router.route_task(None, "task", {})
        self.assertEqual(out["status"], "error")
        self.assertEqual(len(spine.writes), 1)
        self.assertFalse(spine.writes[0]["success"])
        # RuntimeError is execution evidence, not infra noise: it must be
        # LEARNED as a failure, never censored (2026-09-27 convention).
        self.assertFalse(spine.writes[0]["censored"])

    def test_pinned_outcomes_are_recorded_so_the_table_is_not_starved(self):
        """Pinned calls are real observations of (capability, Arm).

        Withholding them is how `record_route_result()` ended up with zero
        callers and Q stayed 0 for the table's entire history.
        """
        arm = _Arm("a", ["task"])
        spine = self.use_spine()
        router = self.router([arm])
        router.route_task("a", "task", {})
        self.assertEqual(len(spine.writes), 1)

    def test_per_arm_hourly_cap_stops_one_arm_dominating_the_table(self):
        arm = _Arm("a", ["task"])
        spine = self.use_spine()
        router = self.router([arm],
                             RoutingPolicy(max_writes_per_arm_per_hour=2))
        for _ in range(5):
            router.route_task(None, "task", {})
        self.assertEqual(len(spine.writes), 2)
        stats = router.get_selection_stats()
        self.assertEqual(stats["write_backs"], 2)
        self.assertEqual(stats["write_backs_suppressed"], 3)
        self.assertEqual(router.write_backs_last_hour("task", "a"), 2)

    def test_write_back_is_disabled_when_policy_says_so(self):
        arm = _Arm("a", ["task"])
        spine = self.use_spine()
        router = self.router([arm], RoutingPolicy(write_back=False))
        router.route_task(None, "task", {})
        self.assertEqual(spine.writes, [])


class TestRealRegistryIntegration(unittest.TestCase):
    """The fake doubles must not be the only thing exercised."""

    def test_registry_populates_without_an_explicit_arms_import(self):
        """Discovery was an import side effect the CONSUMER had to remember.

        Every test in this class starts with `import arms`, which is why the
        omission survived: the suite supplied the side effect the runtime was
        supposed to guarantee. `aoa_monitor` did not, so it built an EMPTY
        registry -- 0 arms, 0 capabilities, elastic routing answering
        `no_candidate` for every request, no outcome ever written back, and
        therefore no durable `aoa_route:` key (measured: 0 on disk while an
        in-process routed call produced 1).

        A subprocess is required for this test to mean anything: the rest of
        this module has already imported `arms`, so an in-process assertion
        would pass with the bug present.
        """
        import json
        import subprocess
        import textwrap

        code = textwrap.dedent(
            """
            import json
            from core.arm_registry import ArmRegistry
            from core.router import CrossArmRouter
            reg = ArmRegistry()          # deliberately NO `import arms`
            index = CrossArmRouter(registry=reg).capability_index()
            print("SENTINEL" + json.dumps({
                "arms": len(reg.arms),
                "caps": len(index),
                "has_kali": "kali" in reg.arms,
            }))
            """
        )
        env = dict(os.environ)
        paths = [_AOA, os.path.join(_project_root, "src"),
                 os.path.join(_project_root, "packages", "azazael-core", "src")]
        env["PYTHONPATH"] = os.pathsep.join(
            paths + [env.get("PYTHONPATH", "")]).strip(os.pathsep)
        proc = subprocess.run([sys.executable, "-c", code], env=env,
                              cwd=_project_root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180)
        line = [ln for ln in (proc.stdout or "").splitlines()
                if ln.startswith("SENTINEL")]
        self.assertTrue(line, "probe produced no result:\n%s\n%s"
                        % (proc.stdout, proc.stderr))
        data = json.loads(line[-1][len("SENTINEL"):])
        self.assertGreater(data["arms"], 0,
                           "ArmRegistry() built an empty fleet without an "
                           "explicit `import arms` -- discovery is still an "
                           "import side effect the consumer must remember")
        self.assertGreater(data["caps"], 10)
        self.assertTrue(data["has_kali"])

    def test_real_arms_register_and_are_addressable_by_capability(self):
        import arms  # noqa: F401  (import side effect: registers every arm)
        from core.arm_registry import ArmRegistry, ARM_REGISTRY
        self.assertGreater(len(ARM_REGISTRY), 0)
        for required in ("azazael", "pams", "kali"):
            self.assertIn(required, ARM_REGISTRY,
                          "arm %r is not registered" % required)
        router = CrossArmRouter(registry=ArmRegistry())
        index = router.capability_index()
        self.assertIn("get_status", index)
        self.assertGreater(len(index), 10)

    def test_kali_arm_is_reachable_by_elastic_routing(self):
        """KaliArm was registered but unexported, so routing could not see it.

        Its module was absent from `arms/__init__.py`, and `@register_arm`
        fires only on import -- so `arm_id="kali"` never entered the registry
        and its tool capabilities were invisible to selection.
        """
        import arms  # noqa: F401
        from core.arm_registry import ARM_REGISTRY
        self.assertIn("kali", ARM_REGISTRY)
        router = CrossArmRouter()
        self.assertTrue(
            any("kali" in arms_for_cap
                for arms_for_cap in router.capability_index().values()))


if __name__ == "__main__":
    unittest.main()
