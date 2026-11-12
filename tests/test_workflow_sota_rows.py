"""EP44 / EP209 / EP248 — the workflow host seams, and the defaults they must not move.

Three SOTA rows land in ``CORE/workflow.py``:

- **EP44** (relocation, S): the engine is the host for the ReAct driver's plan
  repairs. ``structured_action`` hands back ``mutations`` and states in its own
  docstring that the ENGINE applies them; nothing did, so a repair was a value
  with no reader.
- **EP209** (SAGA, L): the schedulable unit should be the *workflow* -- which owns
  its dependency resolution and failure boundary -- rather than a bare task.
- **EP248** (agent JIT, L): a ready layer should be ordered by a cost estimate.

Only the EP44 half changes behaviour (a repair now runs). EP209 and EP248 are
opt-in seams, and this file asserts that their DEFAULT is exactly today's
execution order.
"""

import sys
from pathlib import Path

import pytest

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE import workflow as wf  # noqa: E402

CONTRACT = {"verbs": ["read"], "targets": ["file"]}


def _thoughts(*proposals):
    """A thought_fn yielding the given proposals, then a terminating falsy one."""
    it = iter(proposals)

    def fn(_state):
        return next(it, None)

    return fn


def _engine():
    return wf.WorkflowEngine()


def _two_independent_logs():
    return _engine().parse_dsl({
        "name": "pair",
        "steps": [
            {"id": "a", "action": "log", "params": {"message": "a"}},
            {"id": "b", "action": "log", "params": {"message": "b"}},
        ],
    })


def _reasoning_workflow(engine):
    """One react_drive step whose second thought proposes an unknown verb."""
    return engine.parse_dsl({
        "name": "reasoning",
        "steps": [{
            "id": "reason",
            "action": "react_drive",
            "params": {
                "app": "demo",
                "plan": ["step-1"],
                "contract": CONTRACT,
                "required_keys": ["file"],
                "cite": False,          # keep the run off the hash-chained ledger
                "thought_fn": _thoughts(
                    {"thought": "look", "action": {"verb": "read", "target": "file"},
                     "state": {"file": "x"}},
                    {"thought": "type", "action": {"verb": "types", "target": "file"}},
                ),
            },
        }],
    })


# -- default behaviour is unchanged -------------------------------------------

def test_default_execution_still_runs_every_step_in_one_layer():
    result = _engine().execute(_two_independent_logs())

    assert result.success
    assert result.failure_count == 0
    assert set(result.outputs) == {"a", "b"}
    assert len(result.plan_order) == 1, "two independent steps are one layer"
    assert sorted(list(result.plan_order.values())[0]) == ["a", "b"]
    assert result.plan_mutations == [], "no react step, so nothing to repair"


def test_dependency_order_is_never_overruled_by_the_estimator():
    engine = _engine()
    flow = engine.parse_dsl({
        "name": "chain",
        "steps": [
            {"id": "first", "action": "log", "params": {"message": "1"}},
            {"id": "second", "action": "log", "params": {"message": "2"},
             "depends_on": ["first"]},
        ],
    })
    costs = {"first": 9.0, "second": 1.0}

    result = engine.execute(flow, cost_fn=lambda step: costs[step.id])

    assert result.success
    assert list(result.plan_order.values()) == [["first"], ["second"]], (
        "a cheap dependent step cannot be pulled into an earlier layer")


# -- EP248: cost-ordered layers ----------------------------------------------

def test_cost_fn_orders_a_ready_layer_cheapest_first():
    engine = _engine()
    seen = []
    costs = {"a": 9.0, "b": 1.0}

    def cost(step):
        seen.append(step.id)
        return costs[step.id]

    result = engine.execute(_two_independent_logs(), cost_fn=cost)

    assert result.success
    assert seen, "the estimator must actually be consulted"
    assert list(result.plan_order.values())[0] == ["b", "a"]
    assert set(result.outputs) == {"a", "b"}


def test_an_unmeasurable_step_keeps_its_graph_position():
    engine = _engine()

    def cost(step):
        if step.id == "a":
            raise ValueError("no estimate for this step")
        return 1.0

    result = engine.execute(_two_independent_logs(), cost_fn=cost)

    assert result.success, "an estimator that raises must not fail the workflow"
    assert result.plan_order["1"] == ["b", "a"], (
        "the unmeasured step must not be reordered on a guess")


# -- EP209: the workflow as the schedulable unit ------------------------------

def test_a_supplied_scheduler_owns_the_layer_fan_out():
    engine = _engine()
    calls = []

    def scheduler(layer, run_step, step_map, errors):
        calls.append(list(layer))
        for sid in layer:
            run_step(sid)

    result = engine.execute(_two_independent_logs(), scheduler=scheduler)

    assert len(calls) == 1, "one layer, one delegated fan-out"
    assert sorted(calls[0]) == ["a", "b"]
    assert result.success
    assert set(result.outputs) == {"a", "b"}


def test_submit_workflow_schedules_the_whole_workflow_as_one_unit():
    engine = _engine()
    submitted = []

    class FakePool:
        def submit(self, fn, *args):
            submitted.append((fn, args))
            return "future-sentinel"

    flow = _two_independent_logs()
    out = engine.submit_workflow(flow, FakePool())

    assert out == "future-sentinel"
    assert len(submitted) == 1, "the workflow is ONE schedulable unit"
    fn, args = submitted[0]
    assert args == (flow,)


def test_submit_workflow_refuses_an_executor_without_submit():
    engine = _engine()

    for bad in (None, object()):
        with pytest.raises(ValueError):
            engine.submit_workflow(_two_independent_logs(), bad)


# -- EP44: the engine applies the driver's plan repairs ------------------------

def test_engine_applies_react_plan_repairs_as_steps_that_run():
    engine = _engine()
    result = engine.execute(_reasoning_workflow(engine))

    assert result.success, result.errors
    assert result.plan_mutations, (
        "an unknown verb must hand the engine an insertion to apply")
    inserted = [m["step"] for m in result.plan_mutations]
    assert inserted[0] in result.step_results, (
        "the repair must have RUN, not merely been recorded")
    ran = result.step_results[inserted[0]]
    assert ran["success"] is True
    assert "react-plan-repair" in str(ran.get("result")), ran


def test_react_drive_without_cognition_is_a_named_error():
    engine = _engine()
    flow = engine.parse_dsl({
        "name": "no-cognition",
        "steps": [{"id": "reason", "action": "react_drive",
                   "params": {"app": "demo", "plan": [], "contract": CONTRACT,
                              "required_keys": []}}],
    })

    result = engine.execute(flow)

    assert result.success is False
    assert any("thought_fn" in e for e in result.errors), result.errors
