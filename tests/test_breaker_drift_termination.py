"""EP383 — a safety-drift signal terminates on the breaker.

Host: `CORE/circuit_breaker.py :: CircuitBreaker.force_trip` (+ `DeadLetter`).
The divergence detector already exists in the new layer
(`verification_gate.confidence_divergence_check`); these tests assert the
missing half — that a proven drift actually TERMINATES — and that the
fail-open paths do not trip on uncertainty.

Repo convention: tests insert src/ themselves (tests/conftest.py does not).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import CORE.circuit_breaker as cbmod
from CORE.circuit_breaker import (
    BreakerRegistry,
    CircuitBreaker,
    CircuitState,
    supervise_drift,
)


def _pair(explorer_conf: float, validator_conf: float, node: str = "n1"):
    """One shared node with two confidences; divergence = |delta|."""
    return ([{"id": node, "confidence": explorer_conf}],
            [{"id": node, "confidence": validator_conf}])


def test_confident_but_wrong_pair_trips_the_breaker_open():
    cb = CircuitBreaker("drift_trip")
    exp, val = _pair(0.95, 0.55)          # max |delta| = 0.40 > 0.30 default
    out = cb.supervise_drift(exp, val)
    assert out["checked"] is True
    assert out["passed"] is False
    assert out["tripped"] is True
    assert cb.state == CircuitState.OPEN
    assert "safety-drift" in out["reason"]
    assert "n1" in out["reason"]
    assert cb.hold() is True              # an operator-style hold is in force


def test_agreeing_vectors_do_not_trip():
    cb = CircuitBreaker("drift_ok")
    out = cb.supervise_drift(*_pair(0.80, 0.78))
    assert out["checked"] is True
    assert out["passed"] is True
    assert out["tripped"] is False
    assert cb.state == CircuitState.CLOSED
    assert cb.stats()["drift_trips"] == []


def test_empty_vectors_fail_open_and_do_not_trip():
    cb = CircuitBreaker("drift_empty")
    out = cb.supervise_drift([], [])
    assert out["checked"] is True
    assert out["passed"] is True
    assert out["tripped"] is False
    assert cb.state == CircuitState.CLOSED


def test_trip_provenance_is_recorded_and_reset_releases_it():
    cb = CircuitBreaker("drift_prov")
    cb.supervise_drift(*_pair(0.90, 0.10))
    trips = cb.stats()["drift_trips"]
    assert len(trips) == 1
    assert trips[0]["diverged_ids"] == ["n1"]
    assert trips[0]["reason"].startswith("safety-drift")
    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb.hold() is False


def test_missing_gate_module_is_a_named_gap_not_a_trip():
    cb = CircuitBreaker("drift_nogate")
    original = cbmod._drift_check
    cbmod._drift_check = None
    try:
        out = cb.supervise_drift(*_pair(0.99, 0.01))
    finally:
        cbmod._drift_check = original
    assert out["checked"] is False
    assert out["tripped"] is False
    assert "verification-gate-unavailable" in out["gaps"]
    assert cb.state == CircuitState.CLOSED


def test_registry_level_supervise_reaches_the_named_breaker():
    reg = BreakerRegistry()
    out = supervise_drift("named", *_pair(0.99, 0.01), registry=reg)
    assert out["tripped"] is True
    assert reg.get("named").state == CircuitState.OPEN
