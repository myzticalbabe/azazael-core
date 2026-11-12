"""`rehearse_dispatch` is rehearsed in the real dispatch path, not only in exercises.

`CORE/dispatch_plan.py::rehearse_dispatch` and `assign_roles` shipped complete and
were called ONLY by `src/CORE/technique_wiring/`, so row EP238 (`rehearse_dispatch`)
scored EXERCISE-ONLY -- adoption zero. `MetaCluster._plan_execution_order` (the
method `orchestrate()` calls at line ~450 to derive the category execution order)
planned a DAG and recorded it, but never reheased it: failures were only discovered
when dispatch already ran.

The three facts asserted here, and why each is the discriminating one:

  * an invalid plan's fallback note carries the REHEARSAL's prediction, not the raw
    plan gap list -- the rehearsal prefixes its findings `plan-gap:`, so that prefix
    in the note is evidence the technique produced the text. Without the change the
    note was `plan-fallback:invalid(duplicate-task:alpha)` with no `plan-gap:`.
  * a VALID plan's note exposes the rehearsal verdict (`rehearsed=go`) and the pool
    load, so the rehearsal is load-bearing on the success path too.
  * `orchestrate` reaches the planner at all, so the wiring executes somewhere in
    production rather than sitting in an uncalled method.

The invalid input is real, not mocked: `plan_dag` reports `duplicate-task:<id>` for a
repeated id (dispatch_plan.py), and `_plan_execution_order` builds one task per name.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _meta_cluster(categories):
    from CORE.WORMS.METACLUSTER import MetaCluster
    mc = MetaCluster()
    # Injected directly: `discover_all()` would hit the live worm registry, which
    # this test does not need and must not depend on.
    mc.categories = categories
    return mc


def test_invalid_plan_reports_the_rehearsal_prediction_not_the_raw_gaps():
    """Failure is now explained by the rehearsal, at plan-build time."""
    mc = _meta_cluster({"alpha": ["w1"], "beta": ["w2"]})

    names, levels, note = mc._plan_execution_order(["alpha", "alpha"])

    assert note.startswith("plan-fallback:invalid(")
    # `plan-gap:` is the rehearsal's own prefix (rehearse_dispatch folds
    # dag["gaps"] into "plan-gap:<g>"). Its presence proves the note came from the
    # rehearsal; the raw path would have written the bare gap.
    assert "plan-gap:" in note
    assert "duplicate-task:alpha" in note
    # The technique's own finding is carried through verbatim, not paraphrased.
    from CORE.dispatch_plan import plan_dag, rehearse_dispatch, assign_roles
    plan = plan_dag([{"id": "alpha", "deps": []}, {"id": "alpha", "deps": []}])
    expected = rehearse_dispatch(
        plan, assign_roles(["alpha"], {"alpha": "category"},
                           {"category": "metacluster"}))["predicted_failures"]
    assert expected, "fixture must produce a prediction, or this test proves nothing"
    for finding in expected:
        assert finding in note
    # Fail-open contract preserved: planning cannot break orchestration, so the
    # caller's order comes back unchanged with no levels.
    assert names == ["alpha", "alpha"]
    assert levels == {}


def test_valid_plan_reports_the_rehearsal_verdict_and_pool_load():
    """The rehearsal is load-bearing on success: its verdict rides the plan note."""
    mc = _meta_cluster({"alpha": ["w1"], "beta": ["w2"]})

    names, levels, note = mc._plan_execution_order(["alpha", "beta"])

    assert note.startswith("planned:")
    assert "rehearsed=go" in note
    assert "predicted_failures=0" in note
    # pool_load counts the categories this run dispatches into the one pool the
    # planner orchestrates -- the oversubscription signal the technique computes.
    assert "busiest_pool=metacluster(2)" in note
    assert names == ["alpha", "beta"]
    assert levels == {"alpha": 0, "beta": 0}


def test_single_category_still_skips_planning():
    """The pre-existing early exits are untouched: one category is not a DAG."""
    mc = _meta_cluster({"alpha": ["w1"]})
    names, levels, note = mc._plan_execution_order(["alpha"])
    assert (names, levels, note) == (["alpha"], {}, "plan-skipped:single")


def test_orchestrate_reaches_the_planner():
    """`orchestrate` calls `_plan_execution_order`, so the wiring really executes.

    `natures=[]` is used deliberately: the planner is invoked with an empty order,
    which returns immediately, and nothing is dispatched. The dispatch body after
    the call may still fail in a unit environment (it builds real clusters), so the
    call is guarded -- but the assertion is on the RECORDED CALL, which happens
    before any of that, so a raise cannot mask a missing edge.
    """
    mc = _meta_cluster({"alpha": ["w1"]})
    calls = []
    original = mc._plan_execution_order

    def spy(order):
        calls.append(list(order))
        return original(order)

    mc._plan_execution_order = spy
    # The dispatch body may fail here (it builds real clusters in a unit
    # environment). The exception is CAPTURED, not swallowed: the edge this test
    # asserts happens before it, and the message below reports it, so a silent
    # pass can never be mistaken for a reachable edge.
    dispatch_error = None
    try:
        mc.orchestrate(categories=[])
    except Exception as exc:  # noqa: BLE001 - see above; reported in the assertion
        dispatch_error = exc

    assert calls == [[]], (
        "orchestrate did not reach the dispatch planner; the rehearsal would "
        f"execute nowhere (planner calls recorded: {calls}, "
        f"dispatch error: {dispatch_error!r})")
