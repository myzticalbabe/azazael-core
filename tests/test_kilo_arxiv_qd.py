# coding: utf-8
"""Kilo ARXIV-QD (#814) verification + coordinator corrections.

Kilo implemented Q1/Q2/Q3/U1/U2 but stopped before tests or a commit.
Review found three defects, all fixed here and pinned below:

* Q3 log line had ONE %s placeholder with a TWO-tuple argument: enabling
  QD_VQ_ELITES would have raised inside the QD update and, caught by the
  outer handler, skipped the WHOLE archive save that cycle.
* Q2 stored the blended threshold AS the cell fitness, so qd_score/best
  would have mixed thresholds with fitnesses and lost their referent.
  The threshold now lives in its own cell key; elite fitness stays pure.
* Q1/Q2/Q3 threshold env vars were parsed unguarded at import: a garbage
  value crashed the module import. `_env_float` fails open to default.

Pinned contract:

* baseline admission is unchanged (empty admits; beat-occupant admits);
  stored fitness is always the candidate's TRUE fitness;
* Q1 admits on novelty OR fitness; Q2 learns a threshold while fitness
  stays pure; Q3 measures without mutating;
* all three flags default OFF with a clean env (subprocess, no reload);
* U1 uncertainty is pure-python (hand-built JSON models, exact means);
* U2 is deterministic per seed and degrades with stated notes.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

# Skip tests for optional dependencies
try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import evolution_cycle as evc  # noqa: E402
from CORE.WORMS.expansion.oracle import FitnessOracle  # noqa: E402


def _cell(fit, beh):
    return {"genes": {"lr": 0.5}, "fitness": fit,
            "behavior": list(beh), "raw": [0.0, 0.0]}


def _flags(monkeypatch, q1=False, q2=False, alpha=0.3, thr=0.15):
    monkeypatch.setattr(evc, "QD_DOMINATED_NOVELTY", q1)
    monkeypatch.setattr(evc, "QD_SOFT_ARCHIVE", q2)
    monkeypatch.setattr(evc, "QD_SOFT_ARCHIVE_ALPHA", alpha)
    monkeypatch.setattr(evc, "QD_NOVELTY_THRESHOLD", thr)


def test_baseline_admission_unchanged_and_fitness_pure(monkeypatch):
    _flags(monkeypatch)
    cells = {"0,0": _cell(50.0, (0.0, 0.0))}
    assert evc._qd_admit_cell(cells, "1,1", {"lr": 0.1}, 40.0,
                              (1.0, 1.0), (1.0, 1.0)) is True
    assert evc._qd_admit_cell(cells, "0,0", {"lr": 0.1}, 40.0,
                              (0.0, 0.0), (0.0, 0.0)) is False
    assert evc._qd_admit_cell(cells, "0,0", {"lr": 0.9}, 60.0,
                              (0.0, 0.0), (0.0, 0.0)) is True
    assert cells["0,0"]["fitness"] == 60.0  # true fitness, never a blend
    assert cells["0,0"]["genes"] == {"lr": 0.9}


def test_q1_novelty_admits_weak_but_blocks_near(monkeypatch):
    _flags(monkeypatch, q1=True, thr=0.15)
    cells = {"0,0": _cell(50.0, (0.0, 0.0))}
    far = evc._qd_admit_cell(cells, "0,0", {"lr": 0.1}, 10.0,
                             (5.0, 5.0), (5.0, 5.0))
    assert far is True  # far behavior admitted despite low fitness
    cells2 = {"0,0": _cell(50.0, (0.0, 0.0))}
    near = evc._qd_admit_cell(cells2, "0,0", {"lr": 0.1}, 10.0,
                              (0.01, 0.01), (0.01, 0.01))
    assert near is False  # near + weaker: dominated, rejected
    assert cells2["0,0"]["fitness"] == 50.0


def test_q1_empty_archive_admits(monkeypatch):
    _flags(monkeypatch, q1=True)
    cells = {}
    assert evc._qd_admit_cell(cells, "2,2", {"lr": 0.1}, 5.0,
                              (2.0, 2.0), (2.0, 2.0)) is True


def test_q2_threshold_learns_while_fitness_stays_pure(monkeypatch):
    _flags(monkeypatch, q2=True, alpha=0.3)
    cells = {"0,0": _cell(50.0, (0.0, 0.0))}
    # Weak candidate: rejected, but the threshold learns toward it and
    # the elite fitness is untouched.
    assert evc._qd_admit_cell(cells, "0,0", {"lr": 0.1}, 40.0,
                              (0.0, 0.0), (0.0, 0.0)) is False
    assert cells["0,0"]["fitness"] == 50.0
    assert cells["0,0"]["threshold"] == round(0.7 * 50.0 + 0.3 * 40.0, 4)
    # Strong candidate: admitted with TRUE fitness; the threshold keeps
    # learning from its own last value (47.0), not from elite fitness.
    assert evc._qd_admit_cell(cells, "0,0", {"lr": 0.9}, 60.0,
                              (0.0, 0.0), (0.0, 0.0)) is True
    assert cells["0,0"]["fitness"] == 60.0
    assert cells["0,0"]["threshold"] == round(0.7 * 47.0 + 0.3 * 60.0, 4)


def test_q2_alpha_zero_is_plain_rule(monkeypatch):
    _flags(monkeypatch, q2=True, alpha=0.0)
    cells = {"0,0": _cell(50.0, (0.0, 0.0))}
    assert evc._qd_admit_cell(cells, "0,0", {"lr": 0.1}, 40.0,
                              (0.0, 0.0), (0.0, 0.0)) is False
    assert "threshold" not in cells["0,0"]


def test_q3_report_measures_without_mutating():
    cells = {"0,0": _cell(52.0, (0.0, 0.0)),
             "1,1": _cell(54.0, (1.0, 1.0)),
             "2,2": _cell(56.0, (-1.0, 0.5))}
    before = json.dumps(cells, sort_keys=True)
    cov, filled = evc._qd_vq_elites_report(cells)
    assert 0.0 < cov <= 1.0 and filled >= 1
    assert json.dumps(cells, sort_keys=True) == before


def test_env_float_fails_open_to_default():
    assert evc._env_float("AZAZAEL_QD_MISSING_VAR_XYZ", 0.3) == 0.3
    os.environ["AZAZAEL_QD_MISSING_VAR_XYZ"] = "garbage!!"
    try:
        assert evc._env_float("AZAZAEL_QD_MISSING_VAR_XYZ", 0.3) == 0.3
        os.environ["AZAZAEL_QD_MISSING_VAR_XYZ"] = "0.7"
        assert evc._env_float("AZAZAEL_QD_MISSING_VAR_XYZ", 0.3) == 0.7
    finally:
        del os.environ["AZAZAEL_QD_MISSING_VAR_XYZ"]


def test_qd_flags_default_off_clean_env():
    code = ("import sys; sys.path.insert(0, 'src');"
            " sys.path.insert(0, 'scripts');"
            " import evolution_cycle as e;"
            " print((e.QD_DOMINATED_NOVELTY, e.QD_SOFT_ARCHIVE,"
            " e.QD_VQ_ELITES))")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AZAZAEL_QD_")}
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), env=env, timeout=120)
    assert p.returncode == 0, p.stderr[-500:]
    assert p.stdout.strip() == "(False, False, False)"


def test_qd_escape_flag_defaults_off_clean_env():
    code = ("import sys; sys.path.insert(0, 'src');"
            " sys.path.insert(0, 'scripts');"
            " import evolution_cycle as e;"
            " print(e.QD_ESCAPE)")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AZAZAEL_QD_")}
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), env=env, timeout=120)
    assert p.returncode == 0, p.stderr[-500:]
    assert p.stdout.strip() == "False"


def test_qd_escape_flag_on_when_env_set():
    code = ("import sys; sys.path.insert(0, 'src');"
            " sys.path.insert(0, 'scripts');"
            " import evolution_cycle as e;"
            " print(e.QD_ESCAPE)")
    env = dict(os.environ)
    env["AZAZAEL_QD_ESCAPE"] = "1"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), env=env, timeout=120)
    assert p.returncode == 0, p.stderr[-500:]
    assert p.stdout.strip() == "True"


def test_qd_escape_constants():
    code = ("import sys; sys.path.insert(0, 'src');"
            " sys.path.insert(0, 'scripts');"
            " import evolution_cycle as e;"
            " print((e.QD_ESCAPE_MAX_TRIALS, e.QD_ESCAPE_BASELINE_QD_SCORE,"
            " e.QD_ESCAPE_BASELINE_FILLED, e.QD_ESCAPE_JSONL))")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AZAZAEL_QD_")}
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), env=env, timeout=120)
    assert p.returncode == 0, p.stderr[-500:]
    out = p.stdout.strip()
    assert "(3, 507.82, 9," in out
    assert "bake_kilo_qdescape.jsonl" in out

def _hand_oracle():
    o = FitnessOracle(n_gene=2, n_era=1, hidden=2, n_models=2, seed=0)
    m1 = [{"w": [[0.5, 0.0, 0.0], [0.0, 0.5, 0.0]], "b": [0.0, 0.0]},
          {"w": [[0.5, 0.5], [0.0, 0.5]], "b": [0.0, 0.0]},
          {"w": [[1.0, 0.0]], "b": [0.0]}]
    m2 = [{"w": [[0.4, 0.1, 0.0], [0.1, 0.4, 0.0]], "b": [0.1, -0.1]},
          {"w": [[0.6, 0.4], [0.1, 0.5]], "b": [0.0, 0.1]},
          {"w": [[1.0, 0.2]], "b": [0.1]}]
    o._json_models = [m1, m2]
    o._mu = [0.0, 0.0, 0.0]
    o._sd = [1.0, 1.0, 1.0]
    return o


def test_u1_uncertainty_pure_exact_and_nonnegative():
    o = _hand_oracle()
    mean, csd, nuqls = o.uncertainty_pure([[1.0, 0.0, 0.0]])
    f1 = FitnessOracle._forward_layers(o._json_models[0], [1.0, 0.0, 0.0])
    f2 = FitnessOracle._forward_layers(o._json_models[1], [1.0, 0.0, 0.0])
    assert mean[0] == (f1 + f2) / 2
    assert csd[0] >= 0.0 and nuqls[0] >= 0.0
    assert csd[0] > 0.0  # the two models genuinely disagree


def test_u1_requires_model_and_shapes():
    o = FitnessOracle(n_gene=2, n_era=1, hidden=2, n_models=2)
    with pytest.raises(RuntimeError):  # model-less query must raise, not guess
        o.uncertainty_pure([[1.0, 0.0, 0.0]])
    o2 = _hand_oracle()
    with pytest.raises(ValueError):  # shape mismatch must raise, not guess
        o2.uncertainty_pure([[1.0, 0.0]])


def test_u2_degenerate_pool_reports_note():
    o = _hand_oracle()
    out = o.overlapping_holdout_uncertainty([[1.0]], [1.0])
    assert out["note"] == "pool too small"
    assert out["n_holds"] == 0


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
def test_u2_deterministic_per_seed():
    import torch  # noqa: F401  (U2 trains; skip where torch is absent)
    o = FitnessOracle(n_gene=2, n_era=1, hidden=2, n_models=2, seed=7)
    X = [[float(i), float(i % 3), 0.0] for i in range(8)]
    y = [float(i) for i in range(8)]
    a = o.overlapping_holdout_uncertainty(X, y, n_holds=2, seed=3)
    b = o.overlapping_holdout_uncertainty(X, y, n_holds=2, seed=3)
    assert a == b
    assert set(a) >= {"holds", "holdout_var", "mean_abs_error", "n_holds"}
