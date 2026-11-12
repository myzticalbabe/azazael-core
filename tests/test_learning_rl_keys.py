# coding: utf-8
"""Schema state keys: stable across processes, severity-separated."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE.WORMS.expansion.learning_rl import _schema_state_key

VEC = [0.11, 2.345, -0.005, 100.0]


def test_deterministic_in_process():
    assert _schema_state_key(VEC, "info") == _schema_state_key(VEC, "info")


def test_severity_separates():
    assert (_schema_state_key(VEC, "info")
            != _schema_state_key(VEC, "error"))


def test_stable_across_hash_seeds():
    code = ("import sys; sys.path.insert(0, 'src');"
            "from CORE.WORMS.expansion.learning_rl import _schema_state_key;"
            "print(_schema_state_key([0.11, 2.345, -0.005, 100.0], 'info'))")
    env0 = dict(os.environ, PYTHONHASHSEED="0")
    env1 = dict(os.environ, PYTHONHASHSEED="1")
    a = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120, env=env0,
                       cwd=os.path.join(os.path.dirname(__file__), '..'))
    b = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120, env=env1,
                       cwd=os.path.join(os.path.dirname(__file__), '..'))
    assert a.returncode == 0 and b.returncode == 0
    assert a.stdout.strip() == b.stdout.strip()
    assert a.stdout.strip().startswith("schema:")


def test_schedule_clamps_and_interpolates():
    # 2026-09-28: piecewise _schedule_value lists were superseded by
    # exponential annealing; pin the live formula instead.
    from CORE.WORMS.expansion.learning_rl import _annealed_epsilon as se
    assert se(0.9, 0.0, 5) == 0.9  # no decay: unchanged
    assert se(0.9, 0.0, 99) == 0.9
    assert se(0.9, 0.1, 0) == 0.9  # episode 0: full rate
    assert se(0.9, 0.1, 10**6) == 0.05  # floor clamps deep runs
    assert se(0.9, 0.1, -3) == 0.9  # negative episode: unchanged
    a = se(0.9, 0.1, 5)
    b = se(0.9, 0.1, 15)
    assert 0.05 < b < a < 0.9  # monotonic decay toward floor


def test_schedule_trains_end_to_end():
    import random
    from CORE.WORMS.expansion.learning_rl import RLTrainLoop
    rl = RLTrainLoop()
    res = rl.train_from_memory(
        records=[{"source": "t", "severity": "info", "data": {}} for _ in range(20)],
        epsilon=0.9, epsilon_decay=0.1,
        n_episodes=5, max_steps=5, persist=False, rng=random.Random(3))
    assert res["episodes"] == 5
    assert isinstance(res["avg_reward"], float)
