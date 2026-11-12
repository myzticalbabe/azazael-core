# coding: utf-8
"""Tests for src/CORE/WORMS/expansion/hybrid_rewards.py — unit contract.

The blender promises a [0, 1] aggregate. update() must refuse anything
outside that contract (returning False) instead of blending-then-clamping
it, which previously recorded every negative reward as a silent 0.0.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from CORE.WORMS.expansion.hybrid_rewards import (  # noqa: E402
    HybridRewardComputer,
    RewardConfig,
    RewardSource,
)


def _computer(weights=None):
    cfg = RewardConfig(source_weights=weights or {RewardSource.ENVIRONMENT: 1.0})
    return HybridRewardComputer(cfg)


def test_update_accepts_unit_range():
    hr = _computer()
    assert hr.update(RewardSource.ENVIRONMENT, 0.0) is True
    assert hr.update(RewardSource.ENVIRONMENT, 1.0) is True
    assert hr.update(RewardSource.ENVIRONMENT, 0.42) is True


def test_update_refuses_out_of_range():
    hr = _computer()
    assert hr.update(RewardSource.ENVIRONMENT, -0.5) is False
    assert hr.update(RewardSource.ENVIRONMENT, 1.5) is False
    assert hr.update(RewardSource.ENVIRONMENT, 250.0) is False
    # Refused observations leave no trace: aggregate of nothing is 0.0.
    assert hr.compute_hybrid() == 0.0


def test_update_refuses_non_numeric():
    hr = _computer()
    assert hr.update(RewardSource.ENVIRONMENT, float("nan")) is False
    assert hr.update(RewardSource.ENVIRONMENT, float("inf")) is False
    assert hr.update(RewardSource.ENVIRONMENT, float("-inf")) is False
    assert hr.update(RewardSource.ENVIRONMENT, True) is False
    assert hr.update(RewardSource.ENVIRONMENT, "0.5") is False
    assert hr.update(RewardSource.ENVIRONMENT, None) is False
    assert hr.compute_hybrid() == 0.0


def test_single_source_passthrough():
    # One source at weight 1.0: confidence cancels, value comes back whole.
    hr = _computer()
    assert hr.update(RewardSource.ENVIRONMENT, 0.7) is True
    assert hr.compute_hybrid() == 0.7


def test_two_source_weighting():
    hr = _computer({RewardSource.ENVIRONMENT: 0.5,
                    RewardSource.USER_FEEDBACK: 0.5})
    assert hr.update(RewardSource.ENVIRONMENT, 1.0) is True
    assert hr.update(RewardSource.USER_FEEDBACK, 0.0) is True
    assert hr.compute_hybrid() == 0.5


def test_refused_source_does_not_dilute():
    # A refused USER_FEEDBACK must not drag the ENVIRONMENT value down.
    hr = _computer({RewardSource.ENVIRONMENT: 0.5,
                    RewardSource.USER_FEEDBACK: 0.5})
    assert hr.update(RewardSource.ENVIRONMENT, 0.8) is True
    assert hr.update(RewardSource.USER_FEEDBACK, -3.0) is False
    assert hr.compute_hybrid() == 0.8


# --- Mutation survivors (scripts/mutate.py 2026-10-17: 7/20 survived,
# all in the averaging / decay / confidence math because every test above
# observes at most once per source with default config and no step). ---

def test_repeated_observation_averages():
    # Kills Div@106 (total / count -> total * count): two observations
    # from one source must average, not multiply.
    import math
    hr = _computer()
    assert hr.update(RewardSource.ENVIRONMENT, 0.4) is True
    assert hr.update(RewardSource.ENVIRONMENT, 0.8) is True
    assert math.isclose(hr.compute_hybrid(), 0.6, rel_tol=1e-9)


def test_decay_applies_over_steps():
    # Kills Mult@82 (v * decay -> v / decay): at step 3 the decayed
    # value 0.97**2 must come back, not its reciprocal clamped to 1.0.
    hr = _computer()
    assert hr.update(RewardSource.ENVIRONMENT, 1.0, step=3) is True
    assert hr.compute_hybrid() == 0.97 ** 2


def test_confidence_scales_with_count_and_step():
    # Kills the conf_factor family (Add@110, Sub@110, Mult@110,
    # Mult@113, Or@111): counts 1 vs 4 at step 4 give conf 0.475 vs
    # 0.95, so the blend must weight toward the oft-observed source.
    # Independent arithmetic, not a copy of the implementation.
    import math
    d = 0.97 ** 3
    conf_a = 0.3 + 0.7 * (1 / 4)
    conf_b = 0.95  # min(0.95, 0.3 + 0.7 * (4 / 4))
    expected = (0.5 * d * 1.0 * conf_a
                + 0.5 * d * 0.0 * conf_b) / (0.5 * conf_a + 0.5 * conf_b)
    hr = _computer({RewardSource.ENVIRONMENT: 0.5,
                    RewardSource.USER_FEEDBACK: 0.5})
    assert hr.update(RewardSource.ENVIRONMENT, 1.0, step=4) is True
    for _ in range(4):
        assert hr.update(RewardSource.USER_FEEDBACK, 0.0, step=4) is True
    assert math.isclose(hr.compute_hybrid(), expected, rel_tol=1e-9)
    # Sanity: the blend favors the oft-observed (zero) source.
    assert hr.compute_hybrid() < 0.5
