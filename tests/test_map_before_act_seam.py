# coding: utf-8
"""EP237 caller seam — MapBeforeAct finally has a production caller.

`WorkflowEngine._execute_step` consults the map for every step's derived
target in SHADOW mode: advice is attached to ``step.map_advice`` and
nothing in dispatch reads it. These tests pin the contract that makes
the seam safe rather than decorative:

* the target is derived per action (worm / nature / pipeline / step id);
* advice is attached AND the step result is identical with the seam on/off;
* a stage explosion still lets the step succeed (advice stays None);
* the MAP_BEFORE_ACT=0 kill-switch never builds the stage;
* the REAL substrate answers through the seam (tiny tmp tree, found + miss);
* advice is attached even when the step itself fails (forensics).
"""
import sys
from pathlib import Path

import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from CORE.codebase_graph import MapConsultation  # noqa: E402
from CORE.workflow import (  # noqa: E402
    Workflow,
    WorkflowEngine,
    WorkflowStep,
    _map_target_for,
)


class FakeStage:
    """Stand-in MapBeforeAct: records targets, answers canned or explodes."""

    def __init__(self, consultation=None, explode=None):
        self.seen = []
        self._consultation = consultation
        self._explode = explode

    def consult(self, target):
        self.seen.append(target)
        if self._explode is not None:
            raise self._explode
        return self._consultation


def _miss(target="s1"):
    return MapConsultation(target=target, found=False,
                           consulted_at="t", notes=["target not present"])


def _log_step(step_id="s1", message="hello"):
    return WorkflowStep(id=step_id, action="log",
                        params={"message": message})


def test_target_derived_per_action():
    assert _map_target_for(WorkflowStep(id="s", action="execute_worm",
                                        params={"worm": "SystemZ"})) == "SystemZ"
    assert _map_target_for(WorkflowStep(id="s", action="execute_nature",
                                        params={"nature": "forage"})) == "forage"
    assert _map_target_for(WorkflowStep(id="s", action="run_pipeline",
                                        params={"pipeline": "DataPipe"})) == "DataPipe"
    assert _map_target_for(WorkflowStep(id="s9", action="log")) == "s9"
    # Missing worm name degrades to the step id, never to empty.
    assert _map_target_for(WorkflowStep(id="s9", action="execute_worm",
                                        params={})) == "s9"


def test_shadow_advice_attached_result_unchanged(monkeypatch):
    monkeypatch.delenv("MAP_BEFORE_ACT", raising=False)
    engine = WorkflowEngine()
    engine._map_stage = FakeStage(consultation=_miss("s1"))
    step = _log_step()
    result = engine.execute(Workflow(id="w", name="w", steps=[step]))
    assert result.success
    assert step.map_advice["found"] is False
    assert step.map_advice["target"] == "s1"
    assert result.step_results["s1"]["result"] == {"logged": "hello"}
    assert result.step_results["s1"]["success"] is True


def test_stage_explosion_still_succeeds_with_no_advice(monkeypatch):
    monkeypatch.delenv("MAP_BEFORE_ACT", raising=False)
    engine = WorkflowEngine()
    engine._map_stage = FakeStage(explode=RuntimeError("scan down"))
    step = _log_step()
    result = engine.execute(Workflow(id="w", name="w", steps=[step]))
    assert result.success  # fail-open: the map never breaks the run
    assert step.map_advice is None


def test_kill_switch_never_builds_stage(monkeypatch):
    monkeypatch.setenv("MAP_BEFORE_ACT", "0")
    engine = WorkflowEngine()

    def _must_not_run():
        raise AssertionError("stage must not be built when seam is off")

    engine._get_map_stage = _must_not_run
    step = _log_step()
    result = engine.execute(Workflow(id="w", name="w", steps=[step]))
    assert result.success
    assert step.map_advice is None


def test_real_substrate_answers_through_seam(monkeypatch, tmp_path):
    monkeypatch.delenv("MAP_BEFORE_ACT", raising=False)
    (tmp_path / "alpha.py").write_text("import beta\nX = 1\n",
                                       encoding="utf-8")
    (tmp_path / "beta.py").write_text("Y = 2\n", encoding="utf-8")
    from CORE.graph_artifacts import MapBeforeAct
    engine = WorkflowEngine()
    engine._map_stage = MapBeforeAct(root=tmp_path)
    hit = WorkflowStep(id="alpha", action="log", params={"message": "x"})
    miss = WorkflowStep(id="nope", action="log", params={"message": "x"})
    result = engine.execute(Workflow(id="w", name="w", steps=[hit, miss]))
    assert result.success
    assert hit.map_advice["found"] is True
    assert "beta" in hit.map_advice["dependencies"]
    assert miss.map_advice["found"] is False


def test_substrate_absent_stands_off_loudly(monkeypatch):
    monkeypatch.delenv("MAP_BEFORE_ACT", raising=False)
    import CORE.workflow as wf
    monkeypatch.setattr(wf, "_MapBeforeAct", None)
    engine = WorkflowEngine()
    step = _log_step()
    result = engine.execute(Workflow(id="w", name="w", steps=[step]))
    assert result.success
    assert step.map_advice is None


def test_advice_attached_even_when_step_fails(monkeypatch):
    monkeypatch.delenv("MAP_BEFORE_ACT", raising=False)
    engine = WorkflowEngine()
    engine._map_stage = FakeStage(consultation=_miss("bad"))
    step = WorkflowStep(id="bad", action="nonexistent")
    with pytest.raises(ValueError):  # unknown action must still raise
        engine._execute_step(step, {}, {})
    assert step.map_advice is not None  # forensics survive the failure
