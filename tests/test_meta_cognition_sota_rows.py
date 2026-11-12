"""EP191 (ReTAS dialectical alignment) — the exchange the engine was missing.

`MetaCognitionWorm` already held every piece of evidence a dialectic needs
(error patterns, confidence models) and never argued with it: `observe`
records, `analyze_patterns` summarises, `recommend_from_evidence` proposes,
`adapt_strategy` switches. Nothing put a *claim* on trial, which is the
mechanism EP191 names. `dialectic()` is that exchange — thesis from the caller,
antithesis from the worm's own record, synthesis from a stated blend — and
`SOTA_ROW_RESCORE.md` keeps this worm as EP191's host because the pre-existing
host was only half-wrong for that row.

Both halves are asserted, because an exchange that reports a verdict and is
never read is decoration:

  * the verdict follows the worm's ACTUAL record (unevidenced / weakened /
    supported), and a confident claim cannot outvote a failing subject;
  * the exchange reaches the store AND the introspection report, so the
    channel has a reader.

The store is stubbed: no database, no session state.
"""


import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


class _StubStore:
    """Minimal LocalMemoryStore stand-in: records calls, returns no state."""

    def __init__(self):
        self.events = []

    def record_event(self, **kwargs):
        self.events.append(kwargs)
        return True

    def query_memories(self, **kwargs):
        return []

    def event_types(self):
        return [e.get("event_type") for e in self.events]


def _worm(store):
    from CORE.WORMS.expansion.meta_cognition import MetaCognitionWorm
    return MetaCognitionWorm(memory_store=store)


def _observe(worm, outcome, error=None, n=1, worm_type="SystemZ"):
    for _ in range(n):
        worm.observe(worm_type=worm_type, action="scan", outcome=outcome,
                     duration_ms=100.0, error=error)


def test_a_claim_without_history_is_unevidenced_not_supported():
    worm = _worm(_StubStore())
    result = worm.dialectic("the scanner is safe", worm_type="SystemZ",
                            persist=False)
    assert result["verdict"] == "unevidenced"
    assert result["antithesis"]["observations"] == 0
    assert "no evidence" in result["antithesis"]["argument"]


def test_recurring_failures_weaken_a_confident_claim():
    """A confident claim must not outvote the subject's own failure record."""
    worm = _worm(_StubStore())
    _observe(worm, "error", error="the scanner is safe", n=6)
    result = worm.dialectic({"claim": "the scanner is safe", "confidence": 1.0},
                            worm_type="SystemZ", persist=False)
    assert result["antithesis"]["matched_recurring_errors"] == 6
    assert result["antithesis"]["rebuttal_weight"] == 0.5
    assert result["verdict"] == "unsupported"
    assert result["thesis"]["claimed_confidence"] == 1.0


def test_a_succeeding_subject_supports_the_claim():
    worm = _worm(_StubStore())
    _observe(worm, "success", n=10)
    result = worm.dialectic("the scanner is safe", worm_type="SystemZ",
                            persist=False)
    assert result["verdict"] == "supported"
    assert result["confidence"] >= 0.60


def test_the_exchange_reaches_the_store_and_the_report():
    store = _StubStore()
    worm = _worm(store)
    _observe(worm, "success", n=10)
    result = worm.dialectic("the scanner is safe", worm_type="SystemZ")
    assert result["recorded"] is True
    assert "dialectic_exchange" in store.event_types()

    report = worm.generate_introspection_report()
    assert report["summary"]["total_dialectic_exchanges"] == 1
    assert report["recent_dialectics"][0]["verdict"] == result["verdict"]
    assert report["recent_dialectics"][0]["claim"] == "the scanner is safe"


def test_persist_false_writes_nothing():
    store = _StubStore()
    worm = _worm(store)
    result = worm.dialectic("the scanner is safe", worm_type="SystemZ",
                            persist=False)
    assert result["recorded"] is False
    assert store.events == []


def test_the_acceptance_gate_is_injected_and_its_failure_is_recorded():
    worm = _worm(_StubStore())
    seen = []

    def gate(claim):
        seen.append(claim)
        return {"accepted": False, "reason": "policy"}

    result = worm.dialectic("the scanner is safe", worm_type="SystemZ",
                            persist=False, acceptance_fn=gate)
    assert seen == ["the scanner is safe"]
    assert result["acceptance"] == {"accepted": False, "reason": "policy"}

    def exploding_gate(claim):
        raise RuntimeError("gate down")

    result = worm.dialectic("the scanner is safe", worm_type="SystemZ",
                            persist=False, acceptance_fn=exploding_gate)
    assert "RuntimeError" in result["acceptance"]["error"]
    assert result["verdict"] == "unevidenced"


def test_an_empty_claim_is_rejected_without_a_verdict():
    worm = _worm(_StubStore())
    result = worm.dialectic("   ", worm_type="SystemZ", persist=False)
    assert "error" in result
    assert result["verdict"] == "unsupported"
    assert result["recorded"] is False


def test_the_agent_wrapper_exposes_the_dialectic_action():
    import pytest

    from CORE.WORMS.expansion import meta_cognition as mc

    agent_cls = getattr(mc, "MetaCognitionAgent", None)
    if agent_cls is None:
        pytest.skip("agent wrapper unavailable in this environment")

    out = agent_cls()._run({"action": "dialectic",
                            "payload": {"claim": "the scanner is safe",
                                        "worm_type": "SystemZ",
                                        "persist": False}})
    assert out["claim"] == "the scanner is safe"
    assert out["verdict"] == "unevidenced"
