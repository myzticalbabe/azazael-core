"""EP446 ATP EXTEND: chained capsules must have a real consumer.

Absorption test for engram #632 Phase 2, slice 6 (technique lane).

The defect this locks down: `audit_trail.graph_capsule` (EP446 host,
`audit_trail.py:879`) built signable units whose docstring recorded the
deferred half — "Riding the linear chain ... stays deferred: no capsule
consumer exists yet". The `_ep446` adapter said the same thing in its
result: capsules were built and sealed, never chained and never verified.
This adds the consumer in the host, `verify_capsule_chain`, for the
chaining rule the units imply (capsule[i]["prev_hash"] binds
capsule[i-1]["signature"]), and the adapter now runs the
produce→consume loop (production reach: `scripts/technique_heartbeat.py`
drives every adapter through `run_all()`).

Signature verification needs Ed25519 (`cryptography`), which is optional
per interpreter — under the 3.15t free-threaded venv the import fails and
capsules are unsigned by design (sealed=False, test_pipeline_
provenance_capsule.py). Link assertions run on any box (prev_hash equality
needs no crypto); seal-tamper assertions skip where Ed25519 is absent.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE import audit_trail as at

needs_signing = __import__("pytest").mark.skipif(
    not at._CRYPTO_AVAILABLE,
    reason="Ed25519 unavailable on this interpreter: capsule is unsigned, "
           "sealed=False by design")


def _chain_two():
    """Produce exactly what the `_ep446` adapter produces: head-bound chain."""
    cap1 = at.graph_capsule({"k1": "v1"}, "head-hash")
    cap2 = at.graph_capsule({"k2": "v2"}, cap1["signature"])
    return [cap1, cap2]


def test_empty_input_is_a_named_failure_not_a_vacuous_pass():
    out = at.verify_capsule_chain([])

    assert out == {"n": 0, "chain_ok": False, "carried": False,
                   "per_capsule": [], "reason": "no-capsules"}, out


def test_head_bound_then_signature_linked_chain_verifies():
    caps = _chain_two()
    out = at.verify_capsule_chain(caps)

    assert out["n"] == 2, out
    assert out["chain_ok"] is True, out
    assert out["per_capsule"][0]["link_ok"] is None, out   # index 0: no bind
    assert out["per_capsule"][1]["link_ok"] is True, out
    assert caps[1]["prev_hash"] == caps[0]["signature"], caps
    # carried tells signed runs from degraded ones on every interpreter.
    assert out["carried"] is bool(caps[0]["signature"]), out


def test_a_mutated_link_breaks_the_chain_on_any_interpreter():
    caps = _chain_two()
    caps[1]["prev_hash"] = "forged"
    out = at.verify_capsule_chain(caps)

    assert out["chain_ok"] is False, out
    assert "link@1" in out["reason"], out


def test_a_dropped_link_breaks_the_chain():
    caps = _chain_two()
    del caps[1]["prev_hash"]
    out = at.verify_capsule_chain(caps)

    assert out["chain_ok"] is False, out


def test_non_capsules_json_is_rejected():
    out = at.verify_capsule_chain([None, "oops", {"prev_hash": ""}])

    # One dict survives the filter; it verifies (no bind, graceful seal).
    assert out["n"] == 1, out
    assert out["chain_ok"] is True, out


@needs_signing
def test_a_mutated_signed_body_fails_its_seal():
    caps = _chain_two()
    caps[0]["delta"] = {"k1": "tampered"}
    out = at.verify_capsule_chain(caps)

    assert out["chain_ok"] is False, out
    assert out["per_capsule"][0]["sig_verified"] is False, out


@needs_signing
def test_the_signed_chain_carries_every_capsule():
    caps = _chain_two()
    out = at.verify_capsule_chain(caps)

    assert out["carried"] is True, out
    assert all(p["signature"] for p in out["per_capsule"]), out


def test_the_adapter_runs_the_produce_then_consume_loop():
    """Production reach: `_ep446` itself must chain and consume, not build-only."""
    import importlib
    import unittest.mock as mock

    l3 = importlib.import_module("CORE.technique_wiring._l3_verify")

    with mock.patch.object(at, "audit_verified_negative",
                           return_value="cite-negative-x"), \
            mock.patch.object(at, "get_head_hash", return_value="head-x"):
        out = l3._ep446()

    assert out["capsule_chained"] is True, out
    assert out["verified_negative_cited"] is True, out
    assert "appending capsules as audit entries" in out["residual"], out
