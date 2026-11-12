"""EP187: the training schedule follows observed contribution, and is unchanged without it.

The row's host edit was `ScheduleConfig.interval_hours` / `ScheduledTrainingWorm`:
the scheduler decided *when* to train from a single flat constant
(`interval_hours: float = 24.0`), so an arm that kept producing promoted or
holdout-passed outcomes was trained exactly as often as one that never
contributed. `contribution_weighted_interval` plus `next_interval_hours` close
that, consuming the machinery `WORMS/expansion/allocation.py` already owns.

Each assertion below is the discriminating one:

  * with NO contribution weights, `next_interval_hours()` returns
    `config.interval_hours` exactly -- the flat schedule is the default, so an
    existing caller cannot be silently re-timed by this change;
  * equal contributions give every arm the base interval (parity), which is what
    makes the inverse-share formula arguable rather than arbitrary;
  * a higher contribution earns a SHORTER interval while a zero-contribution arm
    backs off but is floored rather than dropped -- the floor is the point of the
    mechanism, so a starved arm stays discoverable after a regime change;
  * `record_contribution` republishes `ThompsonAllocator` posterior means, so the
    field has a real source and `next_interval_hours` has a real reader. Without
    that pair this would be a value-without-source, i.e. decoration rather than
    absorption.

EP187's other half -- the Hive logits cache -- is an ACCEPT-GAP recorded in
`scheduling.py`, not a deferred task: Azazael owns no inference engine (providers
are called over HTTP and the API never exposes logits), so nothing in this repo
can host it.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _config(**kw):
    from CORE.WORMS.expansion.scheduling import ScheduleConfig
    return ScheduleConfig(**kw)


def test_flat_interval_is_unchanged_without_contribution_weights():
    from CORE.WORMS.expansion.scheduling import ScheduledTrainingWorm

    worm = ScheduledTrainingWorm(_config(interval_hours=6.5))
    assert worm.next_interval_hours() == 6.5


def test_equal_contributions_keep_the_base_interval():
    from CORE.WORMS.expansion.scheduling import contribution_weighted_interval

    plan = contribution_weighted_interval(12.0, {"ga": 1.0, "cma": 1.0, "de": 1.0})
    assert plan["arms"] == 3
    for arm, hours in plan["intervals"].items():
        assert abs(hours - 12.0) < 0.01, (arm, hours)


def test_higher_contribution_earns_a_shorter_interval_and_the_floor_keeps_the_rest():
    from CORE.WORMS.expansion.scheduling import contribution_weighted_interval

    plan = contribution_weighted_interval(24.0, {"ga": 9.0, "cma": 0.0})
    assert plan["shares"]["ga"] > plan["shares"]["cma"]
    assert plan["intervals"]["ga"] < 24.0        # contribution earns frequency
    assert "cma" in plan["intervals"]            # ... the floor keeps it scheduled
    assert plan["intervals"]["cma"] > 24.0       # ... backed off, never starved
    assert plan["intervals"]["cma"] <= 168.0     # ... and the cap is applied
    assert "cma" in plan["clamped"]


def test_no_contribution_at_all_is_parity_not_a_division_by_zero():
    from CORE.WORMS.expansion.scheduling import contribution_weighted_interval

    plan = contribution_weighted_interval(24.0, {"ga": 0.0, "cma": 0.0})
    assert plan["arms"] == 2
    for arm, hours in plan["intervals"].items():
        assert abs(hours - 24.0) < 0.01, (arm, hours)


def test_non_numeric_and_empty_contributions_are_reported_not_absorbed():
    from CORE.WORMS.expansion.scheduling import contribution_weighted_interval

    plan = contribution_weighted_interval(24.0, {"ga": 1.0, "bad": "lots"})
    assert "bad" not in plan["intervals"]
    assert any("bad" in gap for gap in plan["gaps"])
    empty = contribution_weighted_interval(24.0, {})
    assert empty["intervals"] == {}
    assert empty["gaps"] == ["no-contributions"]


def test_record_contribution_republishes_the_weights_the_schedule_reads():
    from CORE.WORMS.expansion.scheduling import ScheduledTrainingWorm

    worm = ScheduledTrainingWorm(
        _config(interval_hours=24.0, contribution_weights={"ga": 1.0, "cma": 1.0}))
    # Parity first: with equal weights both arms keep the base interval, so the
    # shift below can only come from the recorded outcomes.
    assert abs(worm.next_interval_hours("ga") - 24.0) < 0.01
    assert abs(worm.next_interval_hours("cma") - 24.0) < 0.01

    for _ in range(5):
        worm.record_contribution("ga", "holdout_pass")
        worm.record_contribution("cma", "holdout_fail")

    weights = worm.config.contribution_weights
    assert weights["ga"] > weights["cma"]
    assert worm.next_interval_hours("ga") < 24.0    # earned a shorter wait
    assert worm.next_interval_hours("cma") > 24.0   # faded, still scheduled


def test_the_named_arm_is_followed_and_an_unnamed_call_takes_the_strongest():
    from CORE.WORMS.expansion.scheduling import ScheduledTrainingWorm

    worm = ScheduledTrainingWorm(
        _config(interval_hours=24.0,
                contribution_weights={"ga": 0.9, "cma": 0.1},
                contribution_arm="cma"))
    assert worm.next_interval_hours() > 24.0            # follows the named arm
    assert worm.next_interval_hours("ga") < 24.0        # ... and an explicit one wins
