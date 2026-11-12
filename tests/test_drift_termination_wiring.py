"""EP383 wiring: a failed confidence-divergence check must TRIP, not just record.

`verification_gate.confidence_divergence_check` already detected the
confident-but-wrong case, but its verdict terminated nothing -- the finding had
no consequence. These tests exercise the wire from the gate's real production
entry point (`verify_before_accept`) through to `CircuitBreaker.force_trip`, and
the fail-open path when the breaker is unavailable.

Import order is deliberate and load-bearing: this module imports
`CORE.circuit_breaker` BEFORE `CORE.verification_gate`, which is the harsh
direction of the cycle the wiring has to survive (circuit_breaker imports
`confidence_divergence_check` from verification_gate at module level, so
whichever is imported first must find what the other needs).
"""
import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import CORE.circuit_breaker as _cb                    # noqa: E402
import CORE.verification_gate as _vg                  # noqa: E402
from CORE.verification_gate import verify_before_accept  # noqa: E402


def _confident_but_wrong():
    """The case this whole row exists for: two sources disagree widely."""
    explorer = [{"id": "n1", "confidence": 0.95},
                {"id": "n2", "confidence": 0.90}]
    validator = [{"id": "n1", "confidence": 0.10},
                 {"id": "n2", "confidence": 0.20}]
    return explorer, validator


def _agreeing():
    explorer = [{"id": "n1", "confidence": 0.80}]
    validator = [{"id": "n1", "confidence": 0.78}]
    return explorer, validator


def test_diverged_pair_trips_the_named_breaker():
    subject = "drift-wiring:diverged"
    explorer, validator = _confident_but_wrong()

    verdict = verify_before_accept(
        subject,
        confidence_pairs={"explorer": explorer, "validator": validator},
        record=False)

    # The detection half is unchanged and still reported.
    assert verdict["accepted"] is False
    assert any(r.startswith("confdiv-high") for r in verdict["reasons"])
    # The termination half is the new wire.
    assert any(r.startswith("drift-tripped") for r in verdict["reasons"]), \
        f"no trip recorded in reasons: {verdict['reasons']}"

    breaker = _cb.breaker_registry.get(subject)
    assert breaker.is_open is True, "divergence did not take the breaker OPEN"
    # Provenance: the trip must say WHY, not just that it happened.
    assert "safety-drift" in breaker.trip_reason
    trips = breaker.stats()["drift_trips"]
    assert len(trips) == 1
    assert sorted(trips[0]["diverged_ids"]) == ["n1", "n2"]


def test_agreeing_pair_does_not_trip():
    subject = "drift-wiring:agreeing"
    explorer, validator = _agreeing()

    verdict = verify_before_accept(
        subject, confidence_pairs=(explorer, validator), record=False)

    assert any(r.startswith("confdiv-ok") for r in verdict["reasons"])
    assert not any(r.startswith("drift-tripped") for r in verdict["reasons"])

    breaker = _cb.breaker_registry.get(subject)
    assert breaker.is_open is False, "an agreeing pair must not trip the breaker"
    assert breaker.drift_trips == []


def test_missing_breaker_hook_is_a_named_gap_not_a_silent_pass():
    """Fail-open: without the hook the gate NAMES the gap and never trips."""
    subject = "drift-wiring:no-breaker"
    explorer, validator = _confident_but_wrong()
    saved = _vg._circuit_breaker
    _vg._circuit_breaker = None
    try:
        verdict = verify_before_accept(
            subject, confidence_pairs=(explorer, validator), record=False)
    finally:
        _vg._circuit_breaker = saved

    assert verdict["accepted"] is False          # detection still fires
    assert "drift-trip-skipped(no-circuit-breaker)" in verdict["reasons"]
    assert _cb.breaker_registry.get(subject).is_open is False
