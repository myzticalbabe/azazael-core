"""The per-turn reasoning-mode selector reuses the cascade decision (EP266, ALAR).

`CascadeRouter.route` already makes a cheap-first / escalate-when-unsure call with
a tuned threshold (0.65) and an escalation ledger -- the same SHAPE as choosing an
explicit chain-of-thought turn vs an adaptive-latent turn. The row's host is that
existing decision point, so `select_mode` DELEGATES to `route` instead of
duplicating the threshold.

Both directions are asserted, and the expensive call itself is the evidence:
  * HIGH confidence -> MODE_EXPLICIT, and the latent reasoner is NEVER invoked;
  * LOW confidence  -> MODE_LATENT, and the latent answer is what comes back.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _router_and_stub(confidence):
    """Return (router, triage_fn, latent_fn, calls, MODE_EXPLICIT, MODE_LATENT)."""
    from CORE.cascade import CascadeRouter, MODE_EXPLICIT, MODE_LATENT

    calls = []

    def triage_fn(task):
        calls.append("triage")
        return ("explicit CoT answer", confidence)

    def latent_fn(task):
        calls.append("latent")
        return "latent answer"

    return (CascadeRouter(), triage_fn, latent_fn, calls, MODE_EXPLICIT, MODE_LATENT)


def test_high_confidence_selects_explicit_and_never_consults_the_latent_reasoner():
    router, triage_fn, latent_fn, calls, MODE_EXPLICIT, MODE_LATENT = \
        _router_and_stub(0.90)

    out = router.select_mode("an easy turn", triage_fn, latent_fn)

    assert out["mode"] == MODE_EXPLICIT, out
    assert out["escalated"] is False, out
    assert out["answer"] == "explicit CoT answer"
    assert calls == ["triage"], f"latent reasoner ran despite high confidence: {calls}"
    assert router.n_escalated == 0


def test_low_confidence_selects_latent_and_its_answer_wins():
    router, triage_fn, latent_fn, calls, MODE_EXPLICIT, MODE_LATENT = \
        _router_and_stub(0.20)

    out = router.select_mode("a hard turn", triage_fn, latent_fn)

    assert out["mode"] == MODE_LATENT, out
    assert out["escalated"] is True, out
    assert out["answer"] == "latent answer", out
    assert calls == ["triage", "latent"], calls
    assert router.n_escalated == 1
    assert router.stats()["escalation_rate"] == 1.0


def test_route_behaviour_is_unchanged_for_existing_callers():
    """The mode selector must not have moved the decision it delegates to."""
    from CORE.cascade import CascadeRouter

    router = CascadeRouter(threshold=0.5)
    out = router.route("t", lambda t: ("cheap", 0.5), lambda t: "big")

    assert out == {"answer": "cheap", "via": "triage", "confidence": 0.5,
                   "escalated": False}, out
