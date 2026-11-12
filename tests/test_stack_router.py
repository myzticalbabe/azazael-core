"""Tests for the shadow LinUCB router -- pure math, no DB, no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from stack_router import LinUCB, features, _mat_inv


def _close(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_mat_inv_identity():
    m = [[4.0, 1.0], [1.0, 3.0]]
    inv = _mat_inv(m)
    for i in range(2):
        for j in range(2):
            got = sum(m[i][k] * inv[k][j] for k in range(2))
            assert _close(got, 1.0 if i == j else 0.0, 1e-9)


def test_features_fixed_dim_and_robust():
    assert len(features({})) == 6
    assert len(features({"hour": 25, "dow": -1, "db_mb_main": -1})) == 6
    f = features({"hour": 12, "dow": 3, "db_mb_main": 400.0,
                  "outcome_1h": 100})
    assert all(v == v and abs(v) != float("inf") for v in f)  # finite


def test_synthetic_best_arm_wins():
    uc = LinUCB(["AAA", "BBB"], dim=6, alpha=1.0)
    xa = features({"hour": 9, "dow": 1, "db_mb_main": 400.0,
                   "outcome_1h": 500})
    xb = features({"hour": 21, "dow": 5, "db_mb_main": 500.0,
                   "outcome_1h": 50})
    for _ in range(12):
        for x in (xa, xb):
            uc.update("AAA", x, 0.9)
            uc.update("BBB", x, 0.2)
    assert uc.pick(xa) == "AAA"
    assert uc.pick(xb) == "AAA"


def test_exploration_bonus_matters_early():
    uc = LinUCB(["AAA", "BBB"], dim=6, alpha=2.0)
    x = features({"hour": 9})
    uc.update("AAA", x, 0.1)  # one poor obs for AAA, none for BBB
    assert uc.pick(x) == "BBB"  # uncertainty beats one bad sample


def test_deterministic_given_data():
    def run():
        uc = LinUCB(["AAA", "BBB"], dim=6)
        x = features({"hour": 9})
        uc.update("AAA", x, 0.7)
        uc.update("BBB", x, 0.4)
        return uc.pick(x)
    assert run() == run()
