# coding: utf-8
"""remediation_env.py — a gymnasium env that matches the tabular learner's
semantics, so the two can be compared on ONE scale.

WHY THIS EXISTS
---------------
The repository already contains the modern stack: `stable_baselines3`,
`gymnasium`, `sb3_contrib` and `torch` are all installed, and
`src/CORE/sb3_agent.py` wraps PPO/DQN/A2C/SAC/TD3/DDPG plus QRDQN. So the
"we need a modern learner" gap is not a missing library -- it is a missing
COMPARABILITY.

MEASURED MISMATCH (2026-09-30)
-----------------------------
  tabular learner   learning_rl.py:493  action = R.randint(0, 3)
                    -> 4 actions, REMEDIATION choices, scored by
                       _compute_reward(record, action)
                       { 0: ignore, 1: log, 2: alert, 3: remediate }

  SB3Agent          sb3_agent.py:844    "Action: discrete action space
                       (0 = info, 1 = warning, 2 = error)"
                    -> 3 actions, SEVERITY responses, on a different reward

Two learners with different action semantics and different reward models do
not produce comparable numbers. Comparing them is the apples-to-oranges error
the evolution gate already made three times, in a different dimension. So
this module supplies the missing adapter: the SAME four remediation actions,
the SAME `_compute_reward`, and an observation vector that a function
approximator can actually discriminate -- which the string `source:severity`
cannot, since all 7 alert records collapse to one state key.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not invent a reward. `_compute_reward` is imported, not copied, so
the two arms optimise an identical objective by construction rather than by
coincidence. It does not assume a sequence structure the data does not have:
an episode is an ordered pass over the record stream, and with a 1-record
pool the env is honest about being degenerate (see `state_count`).

Usage:
    from remediation_env import RemediationEnv, build_features
    env = RemediationEnv(records)
    obs, info = env.reset(seed=0)
    obs, reward, terminated, truncated, info = env.step(env.action_space.sample())
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    import gymnasium as gym
    from gymnasium import spaces
    _HAS_GYM = True
except Exception as exc:                                    # pragma: no cover
    note_failure("remediation_env:L57", exc)
    gym = None
    _HAS_GYM = False

from CORE.WORMS.expansion.learning_rl import _compute_reward


# The action semantics of the tabular learner, asserted rather than assumed.
# learning_rl.py's docstring maps them explicitly for the `error` row:
# "remediate +1.0, alert +0.4, log -0.3, ignore -1.0" -> 3, 2, 1, 0.
ACTION_IGNORE, ACTION_LOG, ACTION_ALERT, ACTION_REMEDIATE = 0, 1, 2, 3
N_ACTIONS = 4
ACTION_NAMES = ("ignore", "log", "alert", "remediate")

FEATURE_DIM = 32          # hashed bag-of-tokens, fixed width
SEVERITIES = ("info", "warning", "error", "critical")


def _tok(s: str) -> int:
    """Stable bucket for a token. Python's hash() is salted per process, so a
    model trained in one cycle could not be compared against one trained in
    the next. Hashing has to be stable across processes for the bridge to mean
    anything."""
    return int(hashlib.sha256(str(s).encode("utf-8")).hexdigest()[:8], 16)


def build_features(record: Dict[str, Any]) -> np.ndarray:
    """A fixed-width hashed feature vector for one record.

    The tabular learner's state is the string `source:severity`, which for the
    alert pool is `unknown:critical` on all 7 rows -- one state, a four-cell
    table, no discrimination possible. A bag-of-tokens hash over the fields
    that actually vary gives a function approximator something to learn from
    without changing the objective.
    """
    vec = np.zeros(FEATURE_DIM, dtype=np.float32)
    sev = str(record.get("severity", "info"))
    if sev in SEVERITIES:
        vec[SEVERITIES.index(sev)] = 1.0
    else:                                             # unseen severity, its own slot
        vec[len(SEVERITIES)] = 1.0
    for field in ("source", "component", "event_type", "error_type", "worm"):
        val = record.get(field)
        if val:
            vec[8 + _tok(val) % (FEATURE_DIM - 8)] += 1.0
    data = record.get("data")
    if isinstance(data, dict):
        for field in ("success", "resolved", "remediated"):
            if field in data:
                vec[8 + _tok(field) % (FEATURE_DIM - 8)] += 1.0
        dur = data.get("duration_ms")
        if isinstance(dur, (int, float)):
            vec[7] = min(float(dur) / 10000.0, 1.0)
    n = float(np.linalg.norm(vec))
    if n > 0:
        vec /= n
    return vec


class RemediationEnv:
    """Ordered-pass env over a record stream, with the tabular reward.

    Implemented without inheriting from gym.Env so the module is importable
    (and the reward/state semantics testable) even where gymnasium is absent;
    `gymnasium_env()` wraps it when gym is available.
    """

    def __init__(self, records: Sequence[Dict[str, Any]], horizon: int = 50,
                 seed: int = 0):
        if not records:
            raise ValueError("RemediationEnv needs at least one record")
        self.records = list(records)
        self.horizon = max(1, min(horizon, len(self.records)))
        self._rng = np.random.default_rng(seed)
        self._i = 0
        self._ep = 0

    # -- introspection, so callers can refuse to over-read a degenerate pool
    @property
    def state_count(self) -> int:
        """Distinct feature vectors -- the analogue of the tabular state count."""
        seen = {build_features(r).tobytes() for r in self.records}
        return len(seen)

    @property
    def ceiling(self) -> float:
        best = [max(_compute_reward(r, a) for a in range(N_ACTIONS))
                for r in self.records]
        return float(sum(best) / len(best)) if best else 0.0

    def is_degenerate(self) -> bool:
        """True when the pool cannot support a sequential decision.

        One state means there is no transition to learn and no credit to
        assign: the optimal policy is a constant and any learner finds it
        immediately. Said out loud rather than left for a reader to notice in
        a number that looks like a result.
        """
        return self.state_count <= 1

    # -- gym-shaped API
    def reset(self, seed: Optional[int] = None):
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self._i = 0
        self._ep += 1
        return build_features(self.records[0]), {"episode": self._ep}

    def step(self, action: int):
        rec = self.records[self._i % len(self.records)]
        reward = float(_compute_reward(rec, int(action)))
        self._i += 1
        terminated = False
        truncated = self._i >= self.horizon
        nxt = build_features(self.records[(self._i) % len(self.records)])
        return nxt, reward, terminated, truncated, {"action": int(action)}

    def sample_action(self) -> int:
        return int(self._rng.integers(0, N_ACTIONS))


def gymnasium_env(records, horizon: int = 50, seed: int = 0):
    """gymnasium adapter with Discrete(4) REMEDIATION actions (not severities)."""
    if not _HAS_GYM or gym is None:                   # pragma: no cover
        raise RuntimeError("gymnasium is not installed")

    base = RemediationEnv(records, horizon=horizon, seed=seed)

    class _Env(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self):
            super().__init__()
            self.observation_space = spaces.Box(
                low=-1.0, high=1.0, shape=(FEATURE_DIM,), dtype=np.float32)
            # 4, not 3. The tabular learner's semantics, so the two arms
            # optimise the same objective.
            self.action_space = spaces.Discrete(N_ACTIONS)

        def reset(self, *, seed=None, options=None):
            obs, info = base.reset(seed=seed)
            return obs, info

        def step(self, action):
            return base.step(action)

    return _Env(), base


def run_greedy_policy(records, horizon: int = 50):
    """Reference policy: always remediate. The ceiling, and the thing any
    learner must beat to have learned anything."""
    tot = 0.0
    n = 0
    for rec in records:
        tot += _compute_reward(rec, ACTION_REMEDIATE)
        n += 1
    return tot / n if n else 0.0


__all__ = [
    "RemediationEnv", "gymnasium_env", "build_features", "run_greedy_policy",
    "N_ACTIONS", "ACTION_NAMES", "ACTION_IGNORE", "ACTION_LOG", "ACTION_ALERT",
    "ACTION_REMEDIATE", "FEATURE_DIM",
]
