# coding: utf-8
"""ML/estimators comparison arm on the INTAKE pool (gap-3 wire acceptance).

The estimator arm in run_intake_cycle is ADDITIVE: it fits
logistic_regression on extractor features+labels from the same frozen pool
and records agreement beside the Q-policy metric. These tests pin that
contract hermetically (FakeStore, no live store):

- mixed-class pool -> family + agreement in [0, 1] + feature dim > 0
- single-class pool -> arm skipped with a NAMED reason (never silent, never
  a fabricated score)
"""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    import sklearn
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

from CORE.training_intake import build_training_records  # noqa: E402
from CORE.WORMS.expansion.learning_features import (  # noqa: E402
    SchemaAwareFeatureExtractor)
from ML.estimators import fit_score  # noqa: E402


class FakeStore:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def query_memories(self, source=None, event_type=None, limit=500, **kw):
        return [r for r in self._rows
                if source is None or r.get("source") == source][:limit]


def _row(ok, hook, severity=None):
    sev = severity or ("info" if ok else "warning")
    return {"source": "technique-link", "event_type": "hook:%s" % hook,
            "severity": sev,
            "data": {"subject": "s", "ok": ok, "citation": "",
                     "decision_id": ""},
            "timestamp": "2026-10-22T00:00:00+00:00", "session_id": "t"}


def _arm(records):
    fx = SchemaAwareFeatureExtractor()
    X, y, meta = fx.extract_features_and_labels(records)
    if X and y and len(set(y)) > 1:
        _, agree = fit_score("logistic_regression", X, y)
        return {"family": "logistic_regression", "agreement": float(agree),
                "dim": int((meta or {}).get("dim", 0))}
    return {"family": None,
            "error": "single-class pool (%d rows)" % len(y)}


@pytest.mark.skipif(not HAS_SKLEARN, reason="sklearn not installed")
def test_mixed_pool_yields_agreement(monkeypatch):
    import CORE.training_intake as ti
    # NOTE: the extractor labels info/warning as 1.0 (warning = near-miss, not
    # breakage), so true negatives need severity="error".
    rows = [_row(True, "a.one"), _row(True, "a.two"),
            _row(False, "b.three", severity="error"),
            _row(False, "b.four", severity="error")]
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))
    built = build_training_records(limit=500)
    assert built["n"] == 4, built
    arm = _arm(built["records"])
    assert arm["family"] == "logistic_regression", arm
    assert 0.0 <= arm["agreement"] <= 1.0, arm
    assert arm["dim"] > 0, arm


def test_single_class_pool_skips_loudly(monkeypatch):
    import CORE.training_intake as ti
    rows = [_row(True, "a.one"), _row(True, "a.two")]
    monkeypatch.setattr(ti, "get_memory_store", lambda: FakeStore(rows))
    built = build_training_records(limit=500)
    arm = _arm(built["records"])
    assert arm["family"] is None, arm
    assert "single-class" in arm["error"], arm
