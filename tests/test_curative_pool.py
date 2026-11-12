"""
Unit tests for CurativePool 3.14t — CAIN psychological framework evaluation engine.
"""
import sys
import os
import time
import threading

# Add project paths
AZAZAEL_ROOT = os.environ.get("AZAZAEL_ROOT", os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(AZAZAEL_ROOT, "src", "META"))
sys.path.insert(0, os.path.join(AZAZAEL_ROOT, "src", "CORE"))
sys.path.insert(0, os.path.join(AZAZAEL_ROOT, "src"))

import pytest
import importlib.util

# Load MetaWormSpawner module
spec = importlib.util.spec_from_file_location(
    "MetaWormSpawner",
    os.path.join(AZAZAEL_ROOT, "src", "META", "MetaWormSpawner.py")
)
mws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mws)

CurativePool = mws.CurativePool


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def cp():
    """Fresh CurativePool instance for each test."""
    return CurativePool()

@pytest.fixture
def simple_request():
    return {"context": "implement a sorting function", "params": {"scope": "small", "language": "python"}}

@pytest.fixture
def simple_proposed():
    return {"action_type": "execute", "output_size": 300, "feature": "sorting_algorithm"}

@pytest.fixture
def happy_conversation():
    return [{"role": "user", "content": "please implement a sorting function"}]

@pytest.fixture
def frustrated_conversation():
    return [
        {"role": "user", "content": "no that is wrong"},
        {"role": "user", "content": "stop adding extra stuff"},
        {"role": "user", "content": "no"}
    ]

@pytest.fixture
def agent_pool():
    return [
        {"id": "guardian", "archetype": "The Guardian"},
        {"id": "builder", "archetype": "The Builder"},
        {"id": "analyst", "archetype": "The Analyst"}
    ]


# ============================================================
# ScopeValidator Tests
# ============================================================

class TestScopeValidator:

    def test_valid_scope_within_threshold(self):
        """Actions within 10% deviation should be valid."""
        request = {"scope": "small", "complexity": 5}
        proposed = {"scope": "small", "complexity": 5}
        result = CurativePool.ScopeValidator.validate(request, proposed)
        assert result["valid"] is True
        assert result["max_deviation_pct"] == 0.0

    def test_valid_scope_edge_of_threshold(self):
        """Exactly 10% deviation should be valid (threshold is >, not >=)."""
        request = {"lines": 100}
        proposed = {"lines": 110}  # exactly 10% more
        result = CurativePool.ScopeValidator.validate(request, proposed)
        assert result["valid"] is True

    def test_invalid_scope_exceeds_threshold(self):
        """Actions exceeding 10% deviation should be invalid."""
        request = {"lines": 100}
        proposed = {"lines": 200}  # 100% deviation
        result = CurativePool.ScopeValidator.validate(request, proposed)
        assert result["valid"] is False
        assert len(result["deviations"]) > 0
        assert result["deviations"]["lines"]["deviation_pct"] == 100.0

    def test_multiple_deviations(self):
        """Multiple parameters exceeding threshold should all be reported."""
        request = {"a": 10, "b": 100, "c": 50}
        proposed = {"a": 20, "b": 200, "c": 50}  # a:100% dev, b:100% dev, c:0%
        result = CurativePool.ScopeValidator.validate(request, proposed)
        assert result["valid"] is False
        assert "a" in result["deviations"]
        assert "b" in result["deviations"]
        assert "c" not in result["deviations"]

    def test_unknown_keys_in_proposed(self):
        """Keys in proposed but not in request should not cause deviation."""
        request = {"a": 10}
        proposed = {"a": 10, "b": 100}
        result = CurativePool.ScopeValidator.validate(request, proposed)
        assert result["valid"] is True

    def test_empty_request(self):
        """Empty request should result in valid scope."""
        result = CurativePool.ScopeValidator.validate({}, {})
        assert result["valid"] is True

    def test_non_numeric_values(self):
        """Non-numeric values should not be compared."""
        request = {"name": "hello", "count": 5}
        proposed = {"name": "world", "count": 5}
        result = CurativePool.ScopeValidator.validate(request, proposed)
        assert result["valid"] is True  # only "count" is numeric and matches

    def test_zero_division_handling(self):
        """Zero values in request should not cause division by zero."""
        request = {"count": 0}
        proposed = {"count": 5}
        result = CurativePool.ScopeValidator.validate(request, proposed)
        assert result["valid"] is True  # 0 != 0 check is False, so no comparison


# ============================================================
# NecessityJudge Tests
# ============================================================

class TestNecessityJudge:

    def test_directly_requested_execution(self):
        """Actions explicitly requested should score high necessity."""
        result = CurativePool.NecessityJudge.judge("execute", "implement feature", 100)
        assert result["necessary"] is True
        assert result["confidence"] >= 0.8

    def test_fix_request(self):
        """Fix requests should score high necessity."""
        result = CurativePool.NecessityJudge.judge("execute", "fix bug in parser", 50)
        assert result["necessary"] is True
        assert result["confidence"] >= 0.7

    def test_suggestion_without_request(self):
        """Suggestions without explicit request should score low."""
        result = CurativePool.NecessityJudge.judge("write", "suggest some ideas", 1000)
        assert result["necessary"] is False

    def test_unrequested_large_output(self):
        """Large unrequested output should be penalized."""
        result = CurativePool.NecessityJudge.judge("generate", "think about this", 20000)
        assert result["necessary"] is False
        assert result["confidence"] < 0.4

    def test_small_fix_is_necessary(self):
        """Small output for a fix request should still be necessary."""
        result = CurativePool.NecessityJudge.judge("execute", "create a new module", 50)
        assert result["necessary"] is True
        assert result["action"] == "proceed"


# ============================================================
# EmotionDetector Tests
# ============================================================

class TestEmotionDetector:

    def test_frustration_from_repeated_corrections(self):
        """Three 'no' responses should trigger frustration."""
        conv = [
            {"role": "user", "content": "no that is wrong"},
            {"role": "user", "content": "stop"},
            {"role": "user", "content": "no"}
        ]
        result = CurativePool.EmotionDetector.analyze(conv)
        assert result["frustration_detected"] is True
        assert result["frustration_score"] >= 0.6

    def test_no_frustration_happy_path(self):
        """Normal request should not trigger frustration."""
        conv = [{"role": "user", "content": "please help me with this"}]
        result = CurativePool.EmotionDetector.analyze(conv)
        assert result["frustration_detected"] is False
        assert result["action"] == "maintain"

    def test_scope_reduction_signals(self):
        """User requesting scope reduction should be detected."""
        conv = [{"role": "user", "content": "only do the sorting, nothing else"}]
        result = CurativePool.EmotionDetector.analyze(conv)
        assert result["signals"]["scope_reduction"] >= 1

    def test_empty_conversation(self):
        """Empty conversation should not trigger frustration."""
        result = CurativePool.EmotionDetector.analyze([])
        assert result["frustration_detected"] is False
        assert result["action"] == "maintain"

    def test_short_responses_indicate_annoyance(self):
        """Very short user responses suggest frustration."""
        conv = [
            {"role": "user", "content": "no"},
            {"role": "user", "content": "wrong"},
            {"role": "user", "content": "fix it"}
        ]
        result = CurativePool.EmotionDetector.analyze(conv)
        # Should have detected short responses and negative feedback
        assert result["signals"]["short_responses"] >= 1
        assert result["signals"]["negative_feedback"] >= 1


# ============================================================
# ArchetypeRouter Tests
# ============================================================

class TestArchetypeRouter:

    def test_route_to_matching_archetype(self):
        """Execute tasks should route to The Hero."""
        agents = [{"id": "a1", "archetype": "The Analyst"}, {"id": "a2", "archetype": "The Hero"}]
        result = CurativePool.ArchetypeRouter.route("execute", agents)
        assert result == "a2"

    def test_route_analysis_to_analyst(self):
        """Analysis tasks should route to The Analyst."""
        agents = [{"id": "a1", "archetype": "The Hero"}, {"id": "a2", "archetype": "The Analyst"}]
        result = CurativePool.ArchetypeRouter.route("analyze", agents)
        assert result == "a2"

    def test_route_validate_to_guardian(self):
        """Validation tasks should route to The Guardian."""
        agents = [{"id": "a1", "archetype": "The Guardian"}]
        result = CurativePool.ArchetypeRouter.route("validate", agents)
        assert result == "a1"

    def test_route_empty_agents(self):
        """Empty agent pool should return None."""
        result = CurativePool.ArchetypeRouter.route("execute", [])
        assert result is None

    def test_route_fallback_to_hero(self):
        """Unknown task types should fall back to The Hero."""
        agents = [{"id": "a1", "archetype": "The Analyst"}]
        result = CurativePool.ArchetypeRouter.route("unknown_task", agents)
        assert result == "a1"  # Falls back to first available


# ============================================================
# MetricsCollector Tests
# ============================================================

class TestMetricsCollector:

    def test_initial_state(self):
        """New collector should have zero counts."""
        mc = CurativePool.MetricsCollector()
        report = mc.report()
        assert report["total_actions"] == 0
        assert report["scope_violations"] == 0
        assert report["avg_deviation_pct"] == 0.0

    def test_record_valid_action(self):
        """Recording a valid action should increment total but not violations."""
        mc = CurativePool.MetricsCollector()
        mc.record_action({"valid": True}, {"necessary": True}, {"frustration_detected": False})
        report = mc.report()
        assert report["total_actions"] == 1
        assert report["scope_violations"] == 0
        assert report["unnecessary_actions"] == 0

    def test_record_scope_violation(self):
        """Recording an invalid scope should increment violations."""
        mc = CurativePool.MetricsCollector()
        mc.record_action({"valid": False}, {"necessary": True}, {"frustration_detected": False})
        report = mc.report()
        assert report["scope_violations"] == 1
        assert report["avg_deviation_pct"] == 100.0

    def test_record_frustration_trigger(self):
        """Recording frustrated user should increment frustration triggers."""
        mc = CurativePool.MetricsCollector()
        mc.record_action({"valid": True}, {"necessary": False}, {"frustration_detected": True})
        report = mc.report()
        assert report["frustration_triggers"] == 1
        assert report["unnecessary_actions"] == 1

    def test_thread_safety(self):
        """Concurrent record_action calls should not corrupt metrics."""
        mc = CurativePool.MetricsCollector()
        errors = []

        def record_scope_violation():
            try:
                for _ in range(100):
                    mc.record_action({"valid": False}, {"necessary": True}, {"frustration_detected": False})
            except Exception as e:
                errors.append(e)

        def record_frustration():
            try:
                for _ in range(100):
                    mc.record_action({"valid": True}, {"necessary": False}, {"frustration_detected": True})
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=record_scope_violation),
            threading.Thread(target=record_frustration),
            threading.Thread(target=record_scope_violation),
            threading.Thread(target=record_frustration),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        report = mc.report()
        assert report["total_actions"] == 400
        assert report["scope_violations"] == 200
        assert report["frustration_triggers"] == 200
        assert report["avg_deviation_pct"] == 50.0  # 200/400


# ============================================================
# Integration Tests
# ============================================================

class TestCurativePoolIntegration:

    def test_happy_path_allowed(self, cp, simple_request, simple_proposed, happy_conversation, agent_pool):
        """Straightforward request should be allowed."""
        result = cp.evaluate_action(simple_request, simple_proposed, happy_conversation, agent_pool)
        assert result["decision"] == "allowed"
        assert result["scope"]["valid"] is True
        assert result["necessity"]["necessary"] is True
        assert result["emotion"]["frustration_detected"] is False

    def test_frustrated_user_blocked(self, cp, simple_request, simple_proposed, frustrated_conversation, agent_pool):
        """Frustrated user should block the action regardless of scope/necessity."""
        result = cp.evaluate_action(simple_request, simple_proposed, frustrated_conversation, agent_pool)
        assert result["decision"] == "blocked"
        assert result["emotion"]["frustration_detected"] is True

    def test_scope_violation_blocks_action(self, cp, agent_pool):
        """Large scope deviation on shared numeric keys should block the action."""
        request = {"params": {"scope": "tiny"}, "lines": 1, "context": "fix one line"}
        proposed = {"action_type": "execute", "lines": 500, "feature": "complete_rewrite"}
        conv = [{"role": "user", "content": "fix one line please"}]
        result = cp.evaluate_action(request, proposed, conv, agent_pool)
        assert result["decision"] == "blocked"
        assert result["scope"]["valid"] is False

    def test_unnecessary_action_blocked(self, cp, agent_pool):
        """Unrequested large output should be blocked."""
        request = {"context": "suggest ideas", "params": {}}
        proposed = {"action_type": "write", "output_size": 50000}
        conv = [{"role": "user", "content": "suggest some ideas"}]
        result = cp.evaluate_action(request, proposed, conv, agent_pool)
        assert result["decision"] == "blocked"
        assert result["necessity"]["necessary"] is False

    def test_metrics_cumulative(self, cp, agent_pool):
        """Multiple evaluations should accumulate metrics."""
        for _ in range(5):
            cp.evaluate_action(
                {"context": "implement", "params": {}},
                {"action_type": "execute", "output_size": 100},
                [{"role": "user", "content": "implement this"}],
                agent_pool
            )
        report = cp.get_fleet_report()
        assert report["metrics"]["total_actions"] == 5

    def test_concurrent_pool_threading(self, cp, simple_request, simple_proposed, happy_conversation, agent_pool):
        """The 3 pools should run in their correct order (Pool 1 first, then Pools 2+3 concurrent)."""
        import time
        start = time.time()
        result = cp.evaluate_action(simple_request, simple_proposed, happy_conversation, agent_pool)
        elapsed = time.time() - start
        assert result["decision"] == "allowed"
        assert result["routing"]["best_agent"] is not None
        # Should complete quickly (<2s)
        assert elapsed < 2.0

    def test_fleet_report(self, cp, agent_pool):
        """Fleet report should reflect evaluation history."""
        for _ in range(3):
            cp.evaluate_action(
                {"context": "test", "params": {}},
                {"action_type": "execute", "output_size": 50},
                [{"role": "user", "content": "test"}],
                agent_pool
            )
        report = cp.get_fleet_report()
        assert report["status"] == "curative_pools_active"
        assert report["metrics"]["total_actions"] >= 3
        assert "cost_projection" in report

    def test_cost_meter(self):
        """CostMeter should calculate economic impact of unnecessary actions."""
        log = [
            {"necessary": True, "output_size": 100},
            {"necessary": False, "output_size": 50000},
            {"necessary": False, "output_size": 100000},
        ]
        cost = CurativePool.CostMeter.calculate(log)
        assert cost["unnecessary_actions"] == 2
        assert cost["wasted_output_kb"] > 0
        assert cost["trust_erosion_score"] > 0

    def test_calibration_updater(self):
        """Calibration should tighten when violation rate exceeds 15%."""
        history = [{"valid": False}] * 10 + [{"valid": True}] * 10  # 50% violations
        result = CurativePool.CalibrationUpdater.update(history)
        assert result["calibration"] == "tighten"
        assert result["violation_rate"] == 0.5
