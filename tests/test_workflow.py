#!/usr/bin/env python3
"""Tests for CORE/workflow.py — WorkflowEngine, DependencyGraph, ConditionalRouter."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestDependencyGraph(unittest.TestCase):
    """Test dependency graph resolution, cycle detection, and ordering."""

    def setUp(self):
        from CORE.workflow import DependencyGraph, WorkflowStep
        self.DependencyGraph = DependencyGraph
        self.WorkflowStep = WorkflowStep

    def _make_step(self, step_id, depends_on=None):
        return self.WorkflowStep(
            id=step_id, action="sleep",
            params={"seconds": 0.01},
            depends_on=depends_on or [],
        )

    def test_empty_graph(self):
        """Empty graph should return empty layers."""
        graph = self.DependencyGraph()
        graph.build([])
        layers = graph.get_execution_order()
        self.assertEqual(layers, [])

    def test_single_step(self):
        """Single step with no deps should be one layer."""
        graph = self.DependencyGraph()
        graph.build([self._make_step("s1")])
        layers = graph.get_execution_order()
        self.assertEqual(layers, [["s1"]])

    def test_sequential_deps(self):
        """Sequential deps should produce ordered layers."""
        graph = self.DependencyGraph()
        graph.build([
            self._make_step("s1"),
            self._make_step("s2", ["s1"]),
            self._make_step("s3", ["s2"]),
        ])
        layers = graph.get_execution_order()
        self.assertEqual(layers, [["s1"], ["s2"], ["s3"]])

    def test_parallel_deps(self):
        """Independent steps should be in the same layer."""
        graph = self.DependencyGraph()
        graph.build([
            self._make_step("s1"),
            self._make_step("s2"),
            self._make_step("s3"),
        ])
        layers = graph.get_execution_order()
        self.assertEqual(len(layers), 1)
        self.assertCountEqual(layers[0], ["s1", "s2", "s3"])

    def test_diamond_deps(self):
        """Diamond dependency should resolve correctly."""
        graph = self.DependencyGraph()
        graph.build([
            self._make_step("s1"),
            self._make_step("s2a", ["s1"]),
            self._make_step("s2b", ["s1"]),
            self._make_step("s3", ["s2a", "s2b"]),
        ])
        layers = graph.get_execution_order()
        self.assertEqual(layers[0], ["s1"])
        self.assertCountEqual(layers[1], ["s2a", "s2b"])
        self.assertEqual(layers[2], ["s3"])

    def test_detect_cycle(self):
        """Self-referencing step should be detected as a cycle."""
        graph = self.DependencyGraph()
        graph.build([self._make_step("s1", ["s1"])])
        cycles = graph.detect_cycles()
        self.assertEqual(len(cycles), 1)

    def test_detect_mutual_cycle(self):
        """Mutual dependency should be detected as a cycle."""
        graph = self.DependencyGraph()
        graph.build([
            self._make_step("s1", ["s2"]),
            self._make_step("s2", ["s1"]),
        ])
        cycles = graph.detect_cycles()
        self.assertTrue(len(cycles) > 0)

    def test_get_execution_order_raises_on_cycle(self):
        """get_execution_order should raise on cycle."""
        graph = self.DependencyGraph()
        graph.build([
            self._make_step("s1", ["s2"]),
            self._make_step("s2", ["s1"]),
        ])
        with self.assertRaises(RuntimeError):
            graph.get_execution_order()

    def test_missing_dep_handled_gracefully(self):
        """Step with missing dep should not hang."""
        graph = self.DependencyGraph()
        graph.build([
            self._make_step("s1", ["nonexistent"]),
        ])
        layers = graph.get_execution_order()
        # s1 is unresolved so it doesn't appear in layers
        self.assertEqual(layers, [])

    def test_get_dependents(self):
        """get_dependents should return steps that depend on given step."""
        graph = self.DependencyGraph()
        graph.build([
            self._make_step("s1"),
            self._make_step("s2", ["s1"]),
            self._make_step("s3", ["s1"]),
        ])
        deps = graph.get_dependents("s1")
        self.assertCountEqual(deps, ["s2", "s3"])

    def test_to_mermaid(self):
        """Mermaid graph should be valid."""
        graph = self.DependencyGraph()
        graph.build([
            self._make_step("s1"),
            self._make_step("s2", ["s1"]),
        ])
        mermaid = graph.to_mermaid()
        self.assertIn("s1[s1] --> s2[s2]", mermaid)


class TestConditionalRouter(unittest.TestCase):
    """Test condition evaluation."""

    def setUp(self):
        from CORE.workflow import ConditionalRouter
        self.ConditionalRouter = ConditionalRouter

    def test_empty_condition(self):
        """Empty condition should return True."""
        self.assertTrue(self.ConditionalRouter.evaluate("", {}, {}))

    def test_none_condition(self):
        """None condition should return True."""
        self.assertTrue(self.ConditionalRouter.evaluate(None, {}, {}))

    def test_success_equals_true(self):
        """step.success == true should evaluate correctly."""
        results = {"step1": {"success": True, "result": {}}}
        self.assertTrue(self.ConditionalRouter.evaluate(
            "step1.success == true", results, {}
        ))

    def test_success_equals_false(self):
        """step.success == false should evaluate correctly."""
        results = {"step1": {"success": False, "error": "fail"}}
        self.assertTrue(self.ConditionalRouter.evaluate(
            "step1.success == false", results, {}
        ))

    def test_numeric_gt(self):
        """Numeric > comparison should work."""
        results = {"step1": {"success": True, "result": {"count": 10}}}
        self.assertTrue(self.ConditionalRouter.evaluate(
            "step1.result.count > 5", results, {}
        ))
        self.assertFalse(self.ConditionalRouter.evaluate(
            "step1.result.count > 15", results, {}
        ))

    def test_numeric_lt(self):
        """Numeric < comparison should work."""
        results = {"step1": {"success": True, "result": {"value": 3}}}
        self.assertTrue(self.ConditionalRouter.evaluate(
            "step1.result.value < 10", results, {}
        ))

    def test_and_operator(self):
        """AND operator should require both conditions."""
        results = {"a": {"success": True}, "b": {"success": True}}
        self.assertTrue(self.ConditionalRouter.evaluate(
            "a.success == true AND b.success == true", results, {}
        ))
        results2 = {"a": {"success": True}, "b": {"success": False}}
        self.assertFalse(self.ConditionalRouter.evaluate(
            "a.success == true AND b.success == true", results2, {}
        ))

    def test_or_operator(self):
        """OR operator should pass if either condition is true."""
        results = {"a": {"success": False}, "b": {"success": True}}
        self.assertTrue(self.ConditionalRouter.evaluate(
            "a.success == true OR b.success == true", results, {}
        ))
        results2 = {"a": {"success": False}, "b": {"success": False}}
        self.assertFalse(self.ConditionalRouter.evaluate(
            "a.success == true OR b.success == true", results2, {}
        ))

    def test_param_reference(self):
        """${param} references should be substituted."""
        self.assertTrue(self.ConditionalRouter.evaluate(
            "${register_model} == true",
            {},
            {"register_model": True},
        ))
        self.assertFalse(self.ConditionalRouter.evaluate(
            "${register_model} == true",
            {},
            {"register_model": False},
        ))


class TestWorkflowTemplates(unittest.TestCase):
    """Test built-in workflow templates."""

    def setUp(self):
        from CORE.workflow import WorkflowEngine
        self.engine = WorkflowEngine()

    def test_list_templates(self):
        """list_templates should return built-in template names."""
        templates = self.engine.list_templates()
        self.assertIn("daily_training", templates)
        self.assertIn("system_health_check", templates)
        self.assertIn("security_audit", templates)

    def test_get_template(self):
        """get_template should return the template data."""
        template = self.engine.get_template("daily_training")
        self.assertIsNotNone(template)
        self.assertEqual(template.name, "daily_training")

    def test_get_template_nonexistent(self):
        """Getting a nonexistent template should return None."""
        template = self.engine.get_template("nonexistent")
        self.assertIsNone(template)

    def test_register_custom_template(self):
        """Registering a custom template should make it available."""
        from CORE.workflow import WorkflowTemplate
        self.engine.register_template(WorkflowTemplate(
            name="custom_flow",
            description="Custom test flow",
            steps=[{"id": "s1", "action": "sleep", "params": {"seconds": 0.01}}],
        ))
        self.assertIn("custom_flow", self.engine.list_templates())

    def test_create_from_template(self):
        """Creating a workflow from template should produce valid Workflow."""
        wf = self.engine.create_from_template("daily_training")
        self.assertIsNotNone(wf)
        self.assertEqual(wf.name, "daily_training")
        self.assertTrue(len(wf.steps) > 0)

    def test_create_from_template_with_params(self):
        """Creating with params should override defaults."""
        wf = self.engine.create_from_template(
            "daily_training", {"n_episodes": 100}
        )
        self.assertEqual(wf.params["n_episodes"], 100)

    def test_create_from_template_nonexistent(self):
        """Creating from nonexistent template should return None."""
        wf = self.engine.create_from_template("nonexistent")
        self.assertIsNone(wf)


class TestWorkflowDSL(unittest.TestCase):
    """Test DSL parsing."""

    def setUp(self):
        from CORE.workflow import WorkflowEngine
        self.engine = WorkflowEngine()

    def test_parse_dsl_basic(self):
        """Basic DSL should parse into Workflow."""
        wf = self.engine.parse_dsl({
            "name": "test_flow",
            "steps": [
                {"id": "s1", "action": "sleep", "params": {"seconds": 0.01}},
                {"id": "s2", "action": "sleep", "params": {"seconds": 0.02}, "depends_on": ["s1"]},
            ],
        })
        self.assertEqual(wf.name, "test_flow")
        self.assertEqual(len(wf.steps), 2)

    def test_parse_dsl_empty_steps(self):
        """DSL with no steps should still parse."""
        wf = self.engine.parse_dsl({"name": "empty_flow"})
        self.assertEqual(wf.name, "empty_flow")
        self.assertEqual(len(wf.steps), 0)

    def test_parse_dsl_with_params(self):
        """DSL with params should populate workflow params."""
        wf = self.engine.parse_dsl({
            "name": "param_flow",
            "params": {"threshold": 10},
            "steps": [],
        })
        self.assertEqual(wf.params["threshold"], 10)

    def test_parse_dsl_with_tags(self):
        """DSL with tags should populate workflow tags."""
        wf = self.engine.parse_dsl({
            "name": "tagged_flow",
            "tags": ["test", "demo"],
            "steps": [],
        })
        self.assertEqual(wf.tags, ["test", "demo"])


class TestWorkflowExecution(unittest.TestCase):
    """Test basic workflow execution (sleep/log actions)."""

    def setUp(self):
        from CORE.workflow import WorkflowEngine, Workflow, WorkflowStep
        self.WorkflowEngine = WorkflowEngine
        self.Workflow = Workflow
        self.WorkflowStep = WorkflowStep

    def test_execute_empty_workflow(self):
        """Empty workflow should succeed with zero counts."""
        engine = self.WorkflowEngine()
        wf = self.Workflow(id="test", name="empty")
        result = engine.execute(wf)
        self.assertTrue(result.success)
        self.assertEqual(result.success_count, 0)
        self.assertEqual(result.failure_count, 0)

    def test_execute_sleep_steps(self):
        """Sleep steps should complete successfully."""
        engine = self.WorkflowEngine()
        wf = self.Workflow(
            id="test", name="sleep_test",
            steps=[
                self.WorkflowStep(id="s1", action="sleep", params={"seconds": 0.01}),
                self.WorkflowStep(id="s2", action="sleep", params={"seconds": 0.02}),
            ],
        )
        result = engine.execute(wf)
        self.assertTrue(result.success)
        self.assertEqual(result.success_count, 2)
        self.assertGreater(result.duration_ms, 0)

    def test_execute_log_step(self):
        """Log step should complete successfully."""
        engine = self.WorkflowEngine()
        wf = self.Workflow(
            id="test", name="log_test",
            steps=[
                self.WorkflowStep(id="s1", action="log", params={"message": "hello"}),
            ],
        )
        result = engine.execute(wf)
        self.assertTrue(result.success)
        self.assertEqual(result.success_count, 1)

    def test_execute_with_unknown_action(self):
        """Unknown action should fail gracefully."""
        engine = self.WorkflowEngine()
        wf = self.Workflow(
            id="test", name="bad_action",
            steps=[
                self.WorkflowStep(id="s1", action="nonexistent"),
            ],
        )
        result = engine.execute(wf)
        self.assertFalse(result.success)
        self.assertEqual(result.failure_count, 1)

    def test_execute_with_condition_skip(self):
        """Condition that evaluates to false should skip the step."""
        engine = self.WorkflowEngine()
        wf = self.Workflow(
            id="test", name="condition_skip",
            steps=[
                self.WorkflowStep(id="s1", action="sleep", params={"seconds": 0.01}),
                self.WorkflowStep(id="s2", action="sleep", params={"seconds": 0.02},
                                  depends_on=["s1"],
                                  condition="s1.success == false"),
            ],
        )
        result = engine.execute(wf)
        self.assertTrue(result.success)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.skipped_count, 1)

    def test_execute_sequential_deps(self):
        """Steps with deps should execute in order."""
        engine = self.WorkflowEngine()
        wf = self.Workflow(
            id="test", name="sequential",
            steps=[
                self.WorkflowStep(id="s1", action="sleep", params={"seconds": 0.01}),
                self.WorkflowStep(id="s2", action="sleep", params={"seconds": 0.01},
                                  depends_on=["s1"]),
                self.WorkflowStep(id="s3", action="sleep", params={"seconds": 0.01},
                                  depends_on=["s2"]),
            ],
        )
        result = engine.execute(wf)
        self.assertTrue(result.success)
        self.assertEqual(result.success_count, 3)

    def test_get_status_initial(self):
        """Initial status should show templates but no history."""
        engine = self.WorkflowEngine()
        status = engine.get_status()
        self.assertEqual(status["templates"], 3)
        self.assertEqual(status["history_count"], 0)
        self.assertIsNone(status["last_execution"])

    def test_get_status_after_execution(self):
        """Status after execution should show last execution."""
        engine = self.WorkflowEngine()
        from CORE.workflow import Workflow
        wf = Workflow(id="test", name="status_test", steps=[])
        engine.execute(wf)
        status = engine.get_status()
        self.assertEqual(status["history_count"], 1)
        self.assertIsNotNone(status["last_execution"])
        self.assertEqual(status["last_execution"]["name"], "status_test")

    def test_get_history(self):
        """get_history should return recent executions."""
        engine = self.WorkflowEngine()
        from CORE.workflow import Workflow
        wf = Workflow(id="test", name="history_test", steps=[])
        engine.execute(wf)
        history = engine.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].workflow_name, "history_test")

    def test_get_workflow_engine_singleton(self):
        """get_workflow_engine should return the same instance."""
        from CORE.workflow import get_workflow_engine
        e1 = get_workflow_engine()
        e2 = get_workflow_engine()
        self.assertIs(e1, e2)


class TestWorkflowResult(unittest.TestCase):
    """Test WorkflowResult data."""

    def test_result_fields(self):
        """WorkflowResult should have all expected fields."""
        from CORE.workflow import WorkflowResult
        result = WorkflowResult(
            workflow_id="wf_test",
            workflow_name="test",
            started_at="now",
            completed_at="later",
            duration_ms=100.0,
        )
        self.assertEqual(result.workflow_id, "wf_test")
        self.assertEqual(result.success_count, 0)
        self.assertEqual(result.failure_count, 0)
        self.assertTrue(result.success)  # True by default


class TestWorkflowTimeout(unittest.TestCase):
    """Test step timeout behavior in WorkflowEngine."""

    def setUp(self):
        from CORE.workflow import WorkflowEngine, Workflow, WorkflowStep
        self.WorkflowEngine = WorkflowEngine
        self.Workflow = Workflow
        self.WorkflowStep = WorkflowStep

    def test_sleep_step_completes(self):
        """Short sleep should complete within timeout."""
        engine = self.WorkflowEngine()
        wf = self.Workflow(
            id="test", name="timeout_ok",
            steps=[
                self.WorkflowStep(id="s1", action="sleep",
                                  params={"seconds": 0.01}, timeout=5),
            ],
        )
        result = engine.execute(wf)
        self.assertTrue(result.success)
        self.assertEqual(result.success_count, 1)

    def test_timeout_field_on_step(self):
        """Timeout field should be set on WorkflowStep."""
        step = self.WorkflowStep(id="s1", action="sleep",
                                 params={"seconds": 0.01}, timeout=10)
        self.assertEqual(step.timeout, 10)

    def test_default_timeout(self):
        """Default timeout should be 300."""
        step = self.WorkflowStep(id="s1", action="sleep",
                                 params={"seconds": 0.01})
        self.assertEqual(step.timeout, 300)

    def test_layer_timeout_does_not_block(self):
        """Layer join timeout should not prevent success for fast steps."""
        engine = self.WorkflowEngine()
        wf = self.Workflow(
            id="test", name="parallel_timeout",
            steps=[
                self.WorkflowStep(id="s1", action="sleep",
                                  params={"seconds": 0.01}),
                self.WorkflowStep(id="s2", action="sleep",
                                  params={"seconds": 0.02}),
            ],
        )
        result = engine.execute(wf)
        self.assertTrue(result.success)
        self.assertEqual(result.success_count, 2)

    def test_long_sleep_step(self):
        """A longer sleep should still complete if under the layer timeout."""
        engine = self.WorkflowEngine()
        wf = self.Workflow(
            id="test", name="long_sleep",
            steps=[
                self.WorkflowStep(id="s1", action="sleep",
                                  params={"seconds": 0.5}),
            ],
        )
        result = engine.execute(wf)
        self.assertTrue(result.success)
        self.assertEqual(result.success_count, 1)
        self.assertGreater(result.duration_ms, 400)

    def test_multiple_long_steps_sequential(self):
        """Sequential long steps should all execute."""
        engine = self.WorkflowEngine()
        wf = self.Workflow(
            id="test", name="multi_long",
            steps=[
                self.WorkflowStep(id="s1", action="sleep",
                                  params={"seconds": 0.1}),
                self.WorkflowStep(id="s2", action="sleep",
                                  params={"seconds": 0.1},
                                  depends_on=["s1"]),
            ],
        )
        result = engine.execute(wf)
        self.assertTrue(result.success)
        self.assertEqual(result.success_count, 2)
        self.assertGreater(result.duration_ms, 150)


if __name__ == "__main__":
    unittest.main()
