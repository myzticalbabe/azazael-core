"""The four SOTA rows landed in `CORE/dispatch_plan.py`, and the default is untouched.

Rows (hosts fixed by `SOTA_ROW_RESCORE.md` part B, not re-derived here):

  * EP130 / EP197 -- `plan_dag` exposed depth via `levels` and consulted no task
    complexity, so a learner/router host had no per-task signal to act on. It now
    also returns `complexity`, `priority` and `ready_order`.
  * EP374 -- `plan_dag` had no node-splitting path: a node that crosses a tool
    boundary stayed one node. `split_on_capability=True` fissions it.
  * EP253 -- `assign_roles` took a caller-supplied static `role_pools` map and had
    no way to act on a learned participation prior.

The discriminating assertions, and why each is the one that matters:

  * the ORIGINAL keys (`order`, `levels`, `valid`, `gaps`) are byte-identical to the
    pre-change behaviour, and `ready_order == order` when nothing declares priority
    or complexity — these are live modules called from `MetaCluster` and the L5
    wiring, so a behaviour drift here is a production regression, not a test detail.
  * `order` is NOT reordered by the new signals: only `ready_order` moves. A caller
    that ignores the new keys sees exactly what it saw before.
  * a fissioned node keeps its ORIGINAL id as the join, so an existing reader that
    only knows the id it declared still finds it in `order`.
  * `assign_roles` with a single-string pool map returns an assignment entry with NO
    extra keys, so the shape is unchanged for every current caller.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


# -- EP130 / EP197: the readiness signal --------------------------------------

def test_default_plan_is_unchanged_and_ready_order_equals_order():
    """No declared magnitudes: the four original keys are as they always were."""
    from CORE.dispatch_plan import plan_dag

    plan = plan_dag([{"id": "a", "deps": []},
                     {"id": "b", "deps": []},
                     {"id": "c", "deps": ["a", "b"]}])

    assert plan["order"] == ["a", "b", "c"]
    assert plan["levels"] == {"a": 0, "b": 0, "c": 1}
    assert plan["valid"] is True
    assert plan["gaps"] == []
    # The load-bearing one: a consumer that predates this change sees the same
    # sequence it always saw, because nothing declares a magnitude.
    assert plan["ready_order"] == plan["order"]
    assert plan["fissions"] == []
    assert plan["complexity"] == {"a": 0.0, "b": 0.0, "c": 0.0}
    assert plan["priority"] == {"a": 0.0, "b": 0.0, "c": 0.0}


def test_ready_order_uses_priority_then_complexity_without_moving_order():
    """The signal is additive: `order` stays topological, `ready_order` ranks."""
    from CORE.dispatch_plan import plan_dag

    plan = plan_dag([{"id": "a", "deps": [], "priority": 0, "complexity": 1},
                     {"id": "b", "deps": [], "priority": 5, "complexity": 0},
                     {"id": "c", "deps": [], "priority": 0, "complexity": 9}])

    assert plan["order"] == ["a", "b", "c"]          # untouched
    assert plan["levels"] == {"a": 0, "b": 0, "c": 0}
    # priority 5 wins; then the two at priority 0 rank by complexity 9 > 1.
    assert plan["ready_order"] == ["b", "c", "a"]
    assert plan["priority"]["b"] == 5.0
    assert plan["complexity"]["c"] == 9.0


def test_unusable_magnitudes_degrade_to_zero_rather_than_raising():
    """A malformed field must not cost the plan; it reads as 0.0."""
    from CORE.dispatch_plan import plan_dag

    plan = plan_dag([{"id": "a", "deps": [], "priority": "not-a-number"},
                     {"id": "b", "deps": [], "priority": None}])

    assert plan["priority"] == {"a": 0.0, "b": 0.0}
    assert plan["valid"] is True
    assert plan["ready_order"] == plan["order"]


# -- EP374: capability-boundary fission ---------------------------------------

_TWO_BOUNDARY_TASKS = [{"id": "fetch", "deps": [], "capabilities": ["net", "gpu"]},
                       {"id": "report", "deps": ["fetch"]}]
_BOUNDARIES = {"net": "remote", "gpu": "local"}


def test_fission_splits_a_node_crossing_two_boundaries_and_keeps_the_join_id():
    from CORE.dispatch_plan import plan_dag

    plan = plan_dag(_TWO_BOUNDARY_TASKS, split_on_capability=True,
                    tool_boundaries=_BOUNDARIES)

    assert plan["fissions"] == [{"task": "fetch", "boundaries": ["remote", "local"],
                                "subtasks": ["fetch#remote", "fetch#local"],
                                "join": "fetch"}]
    # Each half runs at the parent's depth; the join waits for both; the
    # dependent task still depends on the id it declared.
    assert plan["levels"]["fetch#remote"] == 0
    assert plan["levels"]["fetch#local"] == 0
    assert plan["levels"]["fetch"] == 1
    assert plan["levels"]["report"] == 2
    assert plan["order"].index("fetch") < plan["order"].index("report")
    assert plan["order"].index("fetch#remote") < plan["order"].index("fetch")
    assert plan["order"].index("fetch#local") < plan["order"].index("fetch")
    assert plan["valid"] is True


def test_fission_is_inert_unless_requested():
    """The default path must not fission: same input, one node per task."""
    from CORE.dispatch_plan import plan_dag

    plan = plan_dag(_TWO_BOUNDARY_TASKS)

    assert plan["fissions"] == []
    assert sorted(plan["order"]) == ["fetch", "report"]
    assert plan["levels"] == {"fetch": 0, "report": 1}


def test_single_boundary_task_is_not_fissioned():
    """Capabilities that share a boundary are one node, not two."""
    from CORE.dispatch_plan import plan_dag

    plan = plan_dag([{"id": "io", "deps": [], "capabilities": ["net", "http"]}],
                    split_on_capability=True,
                    tool_boundaries={"net": "remote", "http": "remote"})

    assert plan["fissions"] == []
    assert plan["order"] == ["io"]


def test_fission_inherits_the_parent_dependency_edge():
    """A fissioned node's halves keep the parent's inbound edge, so order holds."""
    from CORE.dispatch_plan import plan_dag

    plan = plan_dag([{"id": "root", "deps": []},
                     {"id": "wide", "deps": ["root"],
                      "capabilities": ["net", "gpu"]}],
                    split_on_capability=True, tool_boundaries=_BOUNDARIES)

    assert plan["levels"]["wide#remote"] == 1
    assert plan["levels"]["wide#local"] == 1
    assert plan["levels"]["root"] == 0
    assert plan["levels"]["wide"] == 2


# -- EP253: the learned participation prior -----------------------------------

def test_prior_selects_among_candidate_pools():
    """A prior turns the static pool map into a learned choice."""
    from CORE.dispatch_plan import assign_roles

    result = assign_roles(["t1", "t2"], {"t1": "infer", "t2": "infer"},
                          {"infer": ["local", "cloud"]},
                          participation={"local": 0.2, "cloud": 0.9})

    assert result["assignment"]["t1"] == {"role": "infer", "pool": "cloud",
                                          "prior": 0.9}
    assert result["pools"] == {"cloud": ["t1", "t2"]}
    assert result["pool_weights"] == {"cloud": 0.9}
    assert result["prior_used"] is True


def test_no_prior_takes_the_first_candidate_and_keeps_the_old_shape():
    """Without a prior the candidate list is ordered, not learned."""
    from CORE.dispatch_plan import assign_roles

    listed = assign_roles(["t1"], {"t1": "infer"},
                          {"infer": ["local", "cloud"]})

    assert listed["assignment"]["t1"] == {"role": "infer", "pool": "local"}
    assert listed["prior_used"] is False
    assert listed["pool_weights"] == {}

    # The shape every existing caller passes: one string per role.
    single = assign_roles(["t1"], {"t1": "infer"}, {"infer": "local"})

    assert single["assignment"]["t1"] == {"role": "infer", "pool": "local"}
    assert single["valid"] is True
    assert single["gaps"] == []
    assert single["locality_hits"] == 0


def test_prior_does_not_write_a_prior_key_when_none_was_given():
    """Guards the shape contract: the extra key appears only with a prior."""
    from CORE.dispatch_plan import assign_roles

    result = assign_roles(["t1"], {"t1": "infer"}, {"infer": "local"})

    assert "prior" not in result["assignment"]["t1"]


def test_missing_pool_for_role_still_reports_the_gap():
    """The pre-existing gap contract survives the pool-map widening."""
    from CORE.dispatch_plan import assign_roles

    result = assign_roles(["t1"], {"t1": "infer"}, {})

    assert result["gaps"] == ["no-pool-for-role:infer"]
    assert result["valid"] is False
    assert result["assignment"]["t1"] == {"role": "infer", "pool": ""}
