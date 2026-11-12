# coding: utf-8
"""Oracle era-robustness as a promotion criterion (PROTEUS Phase C).

`_oracle_gate` is bound into the promotion condition
(`oracle_gate.get('ok')` gates promotion), but the binding is only real
when the gate EVALUATES. These tests pin the contract:

* disabled flag -> ok + stated skip (fail-open, never blocks);
* enabled + missing model file -> ok + 'no model' skip;
* enabled + model that fails the candidate -> ok False (IT CAN BLOCK);
* enabled + model that passes -> ok True with era counts;
* gene-count mismatch -> ok + stated skip (stale model never judges);
* the promotion condition actually consults the gate (source pin).

Hermetic: the Oracle class is faked, the model path is a tmp file, the
retrain spawn is switched off. No store, no torch, no subprocess.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import evolution_cycle as evc  # noqa: E402
import CORE.WORMS.expansion.oracle as oracle_mod  # noqa: E402

GENES = {"learning_rate": 0.8, "epsilon": 0.4,
         "discount": 0.78, "epsilon_decay": 0.2}


class FakeOracle:
    """Stand-in FitnessOracle; verdict scripted per test."""
    _inst = None

    def __init__(self, passed, n_gene=4):
        self.meta = {"eras": [[0.1, 0.2, 0.3]], "n_rows": 8,
                     "trained_at": "t"}
        self.n_gene = n_gene
        self._passed = passed

    @classmethod
    def load_json(cls, path):
        assert Path(path).exists(), "gate must check the file first"
        return cls._inst

    def era_robust_decision_pure(self, vec, eras, threshold, min_prob):
        assert len(vec) == 4
        return {"passed": self._passed,
                "era_robust_p": 1.0 if self._passed else 0.0}


def _enable(monkeypatch, tmp_path, passed=True, n_gene=4):
    monkeypatch.setattr(evc, "PROTEUS_ORACLE", True)
    monkeypatch.setattr(evc, "PROTEUS_ORACLE_TRAIN", False)
    model = tmp_path / "fitness_oracle.json"
    model.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(evc, "ORACLE_JSON", str(model))
    monkeypatch.setattr(evc, "ORACLE_LOG", str(tmp_path / "last.json"))
    FakeOracle._inst = FakeOracle(passed, n_gene)
    monkeypatch.setattr(oracle_mod, "FitnessOracle", FakeOracle)


def test_disabled_gate_skips_open(monkeypatch):
    monkeypatch.setattr(evc, "PROTEUS_ORACLE", False)
    out = evc._oracle_gate(GENES, 43.0)
    assert out["ok"] is True
    assert "disabled" in out["skipped"]


def test_enabled_missing_model_skips_open(monkeypatch, tmp_path):
    monkeypatch.setattr(evc, "PROTEUS_ORACLE", True)
    monkeypatch.setattr(evc, "ORACLE_JSON",
                        str(tmp_path / "absent.json"))
    out = evc._oracle_gate(GENES, 43.0)
    assert out["ok"] is True
    assert "no model" in out["skipped"]


def test_failing_model_blocks(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, passed=False)
    out = evc._oracle_gate(GENES, 43.0)
    assert out["ok"] is False  # the gate has teeth


def test_passing_model_permits_with_counts(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, passed=True)
    out = evc._oracle_gate(GENES, 43.0)
    assert out["ok"] is True
    assert out["n_eras"] == 1
    assert out["model_rows"] == 8


def test_gene_mismatch_skips_open(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, passed=False, n_gene=9)
    out = evc._oracle_gate(GENES, 43.0)
    assert out["ok"] is True  # a stale model never judges
    assert "n_gene" in out["skipped"]


def test_promotion_condition_binds_the_gate():
    src = inspect.getsource(evc.main)
    assert "oracle_gate.get('ok')" in src, \
        "promotion must consult the oracle verdict, not just log it"
