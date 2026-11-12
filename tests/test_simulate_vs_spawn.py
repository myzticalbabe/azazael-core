"""EP110 simulate-vs-spawn prices a rehearsal against the spawn it replaces.

`rehearse_dispatch` was the simulate half and had a live consumer
(`METACLUSTER._plan_execution_order` calls it unconditionally); what was missing
was any comparison against an actual spawn. `simulate_vs_spawn` is that
comparison, and the verdict is deliberately INFORMATION-based rather than a bare
cost ratio -- at the default prices a rehearsal costs ~8% of a spawn per task, so
`sim_total < spawn_total` holds for every plan of two or more tasks and a ratio
rule would return "simulate" unconditionally. A rule that can only fire one way
proves nothing, so both directions are asserted here.

The final test asserts the decision reaches the live dispatch seam. A pure
function nothing calls is the exact shape this repo keeps producing (flaws
S1/S3: EXERCISE-ONLY by construction), so the wiring is part of the contract.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _dp():
    from CORE import dispatch_plan as dp
    return dp


def _plan(n, prefix="t"):
    """A valid chain of `n` tasks, planned and role-assigned."""
    dp = _dp()
    tasks = [{"id": "%s%d" % (prefix, i),
              "deps": ["%s%d" % (prefix, i - 1)] if i else []}
             for i in range(n)]
    dag = dp.plan_dag(tasks)
    roles = dp.assign_roles(dag["order"],
                            {t["id"]: "worker" for t in tasks},
                            {"worker": "pool-1"})
    return dag, roles, dp.rehearse_dispatch(dag, roles)


def test_small_valid_plan_spawns_because_the_rehearsal_added_nothing():
    """A valid plan whose rehearsal predicted nothing learns nothing."""
    dp = _dp()
    dag, roles, rehearsal = _plan(2)

    verdict = dp.simulate_vs_spawn(dag, roles, rehearsal=rehearsal)

    assert verdict["decision"] == "spawn", verdict
    assert verdict["predicted_failures"] == 0
    assert verdict["savings"] == 0.0, "nothing is avoided when the fan-out runs"


def test_large_valid_plan_simulates_because_confirmation_is_worth_buying():
    """Above the confirm threshold, paying for confirmation is the cheaper bet."""
    dp = _dp()
    dag, roles, rehearsal = _plan(20)

    verdict = dp.simulate_vs_spawn(dag, roles, rehearsal=rehearsal)

    assert verdict["decision"] == "simulate", verdict
    assert verdict["savings"] > 0, verdict
    assert verdict["n_tasks"] == 20


def test_predicted_failure_forces_simulate_and_overrides_the_size_rule():
    """A plan already known to be broken must never be spawned, however small."""
    dp = _dp()
    dag, roles, _ = _plan(2)
    broken = dp.rehearse_dispatch({"gaps": ["cycle:alpha"]}, roles)

    verdict = dp.simulate_vs_spawn(dag, roles, rehearsal=broken)

    assert verdict["decision"] == "simulate", verdict
    assert verdict["predicted_failures"] == 1, verdict


def test_confirm_threshold_is_derived_from_the_prices_not_chosen():
    """The default is spawn/sim, so it tracks the prices rather than being magic."""
    dp = _dp()

    default = dp.simulate_vs_spawn({}, {}, sim_cost_per_task=0.08,
                                   spawn_cost_per_task=1.0)
    cheaper = dp.simulate_vs_spawn({}, {}, sim_cost_per_task=0.04,
                                   spawn_cost_per_task=1.0)

    assert default["confirm_threshold"] == 12, default
    assert cheaper["confirm_threshold"] == 25, cheaper

    # Same 20-task plan, two thresholds: the derived one decides the verdict.
    dag, roles, rehearsal = _plan(20)
    assert dp.simulate_vs_spawn(dag, roles, rehearsal=rehearsal)["decision"] \
        == "simulate"
    assert dp.simulate_vs_spawn(dag, roles, rehearsal=rehearsal,
                                sim_cost_per_task=0.04)["decision"] == "spawn"


def test_degenerate_input_reports_a_verdict_instead_of_raising():
    """Costing runs on the dispatch hot path, so it must not be able to throw."""
    dp = _dp()

    for dag, roles in (({}, {}), ({"order": []}, {}), ({}, {"pools": {}})):
        verdict = dp.simulate_vs_spawn(dag, roles)
        assert verdict["decision"] in ("simulate", "spawn"), verdict
        assert verdict["basis"].startswith("EP110")


def test_the_decision_reaches_the_live_dispatch_seam():
    """The wiring is the contract: the verdict must ride the production plan."""
    from CORE.WORMS.METACLUSTER import MetaCluster

    cluster = MetaCluster()
    cluster.categories = {"alpha": ["w1", "w2"], "beta": ["w2", "w3"],
                          "gamma": ["w4"]}
    plan_out = {}

    _order, _levels, note = cluster._plan_execution_order(
        ["alpha", "beta", "gamma"], plan_out=plan_out)

    assert "cost=" in note, note
    assert "costing" in plan_out, sorted(plan_out)
    assert plan_out["costing"]["basis"].startswith("EP110")
