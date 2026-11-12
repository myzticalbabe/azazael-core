"""A workflow step can now be driven by the EP44 ReAct loop.

`CORE/structured_action.py::drive` already implements thought -> action ->
observation with plan repair and says so in its docstring, but it had no
production caller, so the row scored EXERCISE-ONLY. The missing half was the call
path: `WorkflowEngine._execute_step` dispatched only fixed actions
(execute_worm / execute_nature / run_pipeline / condition / sleep / log), so a
reasoning-driven step could not be expressed at all.

`react_drive` is that action. Both directions are asserted, because a call that
fires but changes nothing is not an integration:
  * a CLEAN run closes with no gaps and leaves the plan alone;
  * a GAP does not fail the step -- it is recorded AND the plan grows a
    `repair:<gap>` step, which is the technique (a controller that only reports
    gaps is what EP44 replaces).
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _engine_with_react_step(thought_fn, **params):
    """Return (engine, workflow, step) with one `react_drive` step."""
    from CORE.workflow import WorkflowEngine, WorkflowStep, Workflow

    step_params = {
        "app": "demo",
        "plan": ["inspect"],
        "contract": {"verbs": ["click"], "targets": ["ok"]},
        "required_keys": ["widget"],
        "thought_fn": thought_fn,
    }
    step_params.update(params)
    engine = WorkflowEngine()
    step = WorkflowStep(id="reason", action="react_drive", params=step_params)
    workflow = Workflow(id="wf_react", name="react", steps=[step])
    return engine, workflow, step


def test_clean_react_step_closes_with_no_gaps():
    def thought_fn(state):
        if state.get("last_verb"):
            return None                      # terminating thought
        return {"thought": "click the widget",
                "action": {"verb": "click", "target": "ok"},
                "state": {"widget": True}}

    engine, workflow, step = _engine_with_react_step(thought_fn)
    result = engine.execute(workflow)

    assert result.success, result.errors
    driven = result.outputs["reason"]
    assert driven["closed"] is True, driven
    assert driven["gaps"] == [], driven["gaps"]
    assert driven["steps"][0]["thought"] == "click the widget"
    assert driven["plan"] == ["inspect"], "a clean run must not touch the plan"


def test_a_gap_is_recorded_and_repairs_the_plan():
    def thought_fn(state):
        if state.get("last_verb"):
            return None
        return {"thought": "use a verb the contract does not declare",
                "action": {"verb": "teleport", "target": "ok"},
                "state": {"widget": True}}

    engine, workflow, step = _engine_with_react_step(thought_fn)
    result = engine.execute(workflow)

    assert result.success, result.errors
    driven = result.outputs["reason"]
    assert "unknown-verb:teleport" in driven["gaps"], driven["gaps"]
    assert "repair:unknown-verb:teleport" in driven["plan"], driven["plan"]
    assert driven["updates"][0]["because"] == "unknown-verb:teleport", driven["updates"]
    assert driven["closed"] is False, "gaps outstanding must not read as closed"


def test_react_step_without_cognition_is_a_named_failure_not_a_silent_pass():
    """A step with no `thought_fn` must fail loudly: a cognition-free loop would
    terminate on the first iteration and read as a completed step."""
    from CORE.workflow import WorkflowEngine, WorkflowStep, Workflow

    engine = WorkflowEngine()
    step = WorkflowStep(id="reason", action="react_drive",
                        params={"app": "demo", "plan": ["inspect"]})
    result = engine.execute(Workflow(id="wf", name="react", steps=[step]))

    assert not result.success, result
    assert result.failure_count == 1
    assert "thought_fn" in result.errors[0], result.errors
