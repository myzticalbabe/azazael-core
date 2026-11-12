"""EP252 Drop-Stable-Rampup sizes the training SET from observed stability.

`scheduling.py` decided *when* to train; the training-set size was a constant
(`ScheduleConfig.data_limit`). The technique schedules the size instead: grow it
while the reward series holds, back it off when a run destabilises. Both
directions are asserted here, because a rule that can only fire one way proves
nothing about the rule -- and the bounds are asserted too, since an unbounded
ramp is the failure this policy exists to avoid.

The final test asserts the decision reaches the extractor call. A pure function
nothing calls is the exact shape this repo keeps producing (flaws S1/S3:
EXERCISE-ONLY by construction), so the wiring is part of the contract.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _rampup():
    from CORE.WORMS.expansion import scheduling as sch
    return sch


def test_stable_series_ramps_the_training_set_up():
    """Every step within tolerance -> grow."""
    sch = _rampup()

    decision = sch.rampup_data_limit([0.50, 0.505, 0.502], 200)

    assert decision["action"] == "ramp", decision
    assert decision["stable"] is True
    assert decision["limit"] == 300, decision          # 200 * 1.5
    assert decision["gaps"] == []


def test_destabilising_series_backs_the_training_set_off():
    """A last-step decline past the drop tolerance -> back off."""
    sch = _rampup()

    decision = sch.rampup_data_limit([0.50, 0.55, 0.30], 200)

    assert decision["action"] == "drop", decision
    assert decision["destabilised"] is True
    assert decision["limit"] == 100, decision          # 200 * 0.5
    assert decision["gaps"] == []


def test_a_noisy_series_does_neither():
    """A large early move that ends calmly is not evidence either way."""
    sch = _rampup()

    decision = sch.rampup_data_limit([0.50, 0.60, 0.595], 200)

    assert decision["action"] == "hold", decision
    assert decision["limit"] == 200, decision          # base untouched
    assert decision["stable"] is False
    assert decision["destabilised"] is False


def test_ramp_is_clamped_to_the_maximum_limit():
    """An unbounded ramp is the failure mode; 700 * 1.5 must not escape 800."""
    sch = _rampup()

    decision = sch.rampup_data_limit([10.0, 10.2, 10.1], 700)

    assert decision["action"] == "ramp", decision
    assert decision["limit"] == sch.RAMPUP_MAX_LIMIT == 800, decision


def test_drop_is_clamped_to_the_minimum_limit():
    """30 * 0.5 would be 15, below the floor where a run can still learn."""
    sch = _rampup()

    decision = sch.rampup_data_limit([0.5, 0.5, 0.1], 30)

    assert decision["action"] == "drop", decision
    assert decision["limit"] == sch.RAMPUP_MIN_LIMIT == 25, decision


def test_a_single_observation_holds():
    """One point contains no change; a ramp from it would be invented input."""
    sch = _rampup()

    decision = sch.rampup_data_limit([0.5], 200)

    assert decision["action"] == "hold", decision
    assert decision["limit"] == 200, decision
    assert decision["gaps"], "an unusable history must name its gap"


def test_no_history_holds():
    sch = _rampup()

    for empty in ([], None):
        decision = sch.rampup_data_limit(empty, 200)
        assert decision["action"] == "hold", decision
        assert decision["limit"] == 200, decision


def test_a_zero_baseline_reward_is_an_observation_not_a_crash():
    """A run can legitimately average 0.0; dividing by it must not raise.

    A flat series is STABLE by this rule -- stability is measured on change, not
    on level -- so it ramps. Keeping a run off a zero baseline is the job of
    `min_limit` and of the caller, not of this predicate.
    """
    sch = _rampup()

    decision = sch.rampup_data_limit([0.0, 0.0], 200)

    assert decision["changes"] == [0.0], decision       # no division, no inf
    assert decision["action"] == "ramp", decision


def test_a_non_numeric_reward_holds_instead_of_raising():
    sch = _rampup()

    decision = sch.rampup_data_limit(["not-a-number", 0.5], 200)

    assert decision["action"] == "hold", decision
    assert decision["limit"] == 200, decision
    assert "non-numeric-reward" in decision["gaps"], decision


def test_a_non_numeric_base_limit_holds_instead_of_raising():
    sch = _rampup()

    decision = sch.rampup_data_limit([0.5, 0.5], "two hundred")

    assert decision["action"] == "hold", decision
    assert decision["gaps"], decision


def test_execute_extracts_with_the_rampup_limit():
    """The decision must reach `extract_from_memory(limit=...)` or it is inert.

    Every component loader is replaced, so this exercises the real `execute()`
    control flow with no database, no provider and no network.
    """
    sch = _rampup()

    seen = {}

    class _StubPipeline:
        def extract_from_memory(self, **kwargs):
            seen.update(kwargs)
            return []

    worm = sch.ScheduledTrainingWorm(sch.ScheduleConfig(data_limit=200,
                                                        record_to_memory=False))
    worm._get_pipeline = lambda: _StubPipeline()
    worm._get_engineer = lambda: None
    worm._get_rl = lambda: None
    worm._get_versioner = lambda: None
    worm._get_store = lambda: None
    worm._plateau_skip_reason = lambda: None
    worm._rampup_decision = lambda: {"limit": 137, "action": "ramp",
                                     "reason": "stable", "gaps": [],
                                     "observed": [], "changes": [], "window": 3,
                                     "stable": True, "destabilised": False}

    run = worm.execute()

    assert seen.get("limit") == 137, seen
    # Persisted, so a ramp compounds and a later drop backs off from it.
    assert worm.config.data_limit == 137, worm.config.data_limit
    assert run.success is True, run


def test_rampup_decision_holds_when_the_store_is_unavailable():
    """Fail-open: no history reads as HOLD at the configured limit."""
    sch = _rampup()

    worm = sch.ScheduledTrainingWorm(sch.ScheduleConfig(data_limit=200))
    worm._get_store = lambda: None

    decision = worm._rampup_decision()

    assert decision["action"] == "hold", decision
    assert decision["limit"] == 200, decision
    assert decision["gaps"], "an unavailable store must name its gap"


def test_rampup_decision_declines_when_recording_is_off():
    """The reward series lives in rows this config never writes."""
    sch = _rampup()

    worm = sch.ScheduledTrainingWorm(sch.ScheduleConfig(data_limit=200,
                                                        record_to_memory=False))

    decision = worm._rampup_decision()

    assert decision["action"] == "hold", decision
    assert decision["limit"] == 200, decision
    assert "recording-disabled" in decision["gaps"], decision
