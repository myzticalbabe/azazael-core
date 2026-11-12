"""Row EP238's world-substrate half: the action-conditioned transition is real.

EP238 (SepsisAgent clinical world model) was re-scored onto `CORE/dream_arena.py`
because the world substrate lives there. The rescore read that file and found the
world-GENERATION path already ships (`compile_world` -> `admit_worlds`) but is
dream-conditioned, not action-conditioned: nothing answered "given this world and
this action, what happens next?". `Environment.mutate` (arena.py:105) is an RNG
transition, so it cannot answer that either.

The assertions below are chosen so a stub that returned its input would fail them:

  * a successor is a LEGAL world on the same declared dimensions, so it can be
    handed straight back to `Environment` (the mix sums to 1, the clamps hold);
  * `harden` and `soften` move `hard_ratio` in OPPOSITE directions, by the
    magnitude asked for -- that is what makes the transition action-specific
    rather than a no-op dressed up as a prediction;
  * `hold` is the identity, so "act vs not act" needs no special case;
  * an unknown action RAISES instead of returning the input, because a silent
    no-op is indistinguishable from a successful prediction -- the exact defect
    class this repo's scanner exists to catch;
  * the producer's own dict (`compile_world`) is accepted directly, with no
    adapter in between.
"""

import sys
from pathlib import Path

import pytest

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _world(**over):
    """A legal world, written out by hand so this module's contract is under test.

    Built literally rather than through `Environment` on purpose: if the arena
    import ever fails, that is a different defect and this test should not report
    it as a prediction failure.
    """
    w = {"severity_mix": [0.5, 0.2, 0.3], "hard_ratio": 0.5,
         "era_pressure": 1.0, "n_alerts": 100, "source": "dream"}
    w.update(over)
    return w


def test_successor_is_a_legal_world_on_the_declared_dimensions():
    from CORE.dream_arena import predict_next
    nxt = predict_next(_world(), "escalate_severity", 0.5)
    mix = nxt["severity_mix"]
    assert len(mix) == 3
    assert all(0.0 <= x <= 1.0 for x in mix)
    assert abs(sum(mix) - 1.0) < 1e-6
    assert 0.05 <= nxt["hard_ratio"] <= 0.95
    assert nxt["n_alerts"] >= 10


def test_harden_and_soften_move_hardness_in_opposite_directions():
    from CORE.dream_arena import predict_next
    w = _world(hard_ratio=0.5)
    assert predict_next(w, "harden", 0.1)["hard_ratio"] == pytest.approx(0.6)
    assert predict_next(w, "soften", 0.1)["hard_ratio"] == pytest.approx(0.4)


def test_clamps_hold_at_the_env_bounds():
    from CORE.dream_arena import predict_next
    assert predict_next(_world(hard_ratio=0.9), "harden", 1.0)["hard_ratio"] == 0.95
    assert predict_next(_world(hard_ratio=0.1), "soften", 1.0)["hard_ratio"] == 0.05


def test_hold_is_the_identity_control():
    from CORE.dream_arena import predict_next
    w = _world()
    nxt = predict_next(w, "hold")
    for key in ("severity_mix", "hard_ratio", "era_pressure", "n_alerts"):
        assert nxt[key] == w[key]


def test_escalation_moves_mass_toward_the_severe_end():
    from CORE.dream_arena import predict_next
    w = _world(severity_mix=[0.5, 0.2, 0.3])
    nxt = predict_next(w, "escalate_severity", 0.5)
    assert nxt["severity_mix"][2] > w["severity_mix"][2]
    assert nxt["severity_mix"][0] < w["severity_mix"][0]


def test_unknown_action_raises_rather_than_returning_the_input():
    from CORE.dream_arena import predict_next
    with pytest.raises(ValueError):
        predict_next(_world(), "no-such-action")


def test_rollout_returns_each_successor_and_distance_measures_the_drift():
    from CORE.dream_arena import predict_next, simulate, world_distance
    w = _world()
    path = simulate(w, ["harden", "raise_pressure"], 0.2)
    assert len(path) == 2
    assert path[0] == predict_next(w, "harden", 0.2)
    assert world_distance(w, path[-1]) > 0.0
    assert world_distance(w, w) == 0.0


def test_predictor_accepts_the_compile_world_output_directly():
    """The producer's own dict is the input contract -- no adapter in between."""
    from CORE.dream_arena import compile_world, predict_next
    w = compile_world({"text": "a storm floods the ward",
                       "tags": ["threat"], "complexity": 0.9})
    nxt = predict_next(w, "harden", 0.1)
    assert nxt["hard_ratio"] >= w["hard_ratio"]
    assert nxt["n_alerts"] == w["n_alerts"]


def test_successor_is_accepted_by_the_real_arena_environment():
    """The successor feeds the actual consumer, not just this module's view."""
    from CORE.dream_arena import predict_next
    try:
        from CORE.WORMS.expansion.arena import Environment
    except ImportError as exc:
        pytest.skip("arena unavailable in this environment: %s" % exc)
    nxt = predict_next(_world(), "expand", 0.5)
    env = Environment.from_dict(nxt)
    assert abs(sum(env.severity_mix) - 1.0) < 1e-6
    assert env.n_alerts == nxt["n_alerts"]
