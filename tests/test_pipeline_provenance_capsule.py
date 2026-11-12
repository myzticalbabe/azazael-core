# coding: utf-8
"""Run provenance capsule on persisted metrics (#865 E2 acceptance).

`NeuralPipeline._persist_run` attaches a `graph_capsule` over a bounded run
summary, bound to the audit chain's current head. Proven here by RECOMPUTING
the body the capsule signed from the STORED summary + prev hash -- no live
store, no audit session, no training, no network.

Recomputation rather than `verify_signature`, deliberately: measured
2026-10-16 on CPython 3.14.7, `graph_capsule` adds its `sealed` flag AFTER
signing, while `verify_signature` rebuilds its payload from every key except
`hash`/`signature`/`entry_count`. `sealed` therefore sits outside the signature
but inside the rebuild, so `verify_signature(capsule)` returns False for the
module's own capsule. The signed body is {type, ts, delta, prev_hash}, and that
is what this test recomputes. The tampered-delta negative control is what keeps
a green run from meaning "the check never executed".
"""

import base64
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import CORE.NeuralPipeline as np_mod  # noqa: E402
import CORE.audit_trail as at  # noqa: E402
from CORE.NeuralPipeline import NeuralPipeline  # noqa: E402
from CORE.WORMS.dead_letter_queue import DeadLetterQueue  # noqa: E402

HEAD = "audit-head-0001"

#: The bounded summary `_persist_run` signs. Pinned here so that widening the
#: signed payload (which would grow every capsule) fails this test.
SUMMARY_KEYS = ("pipeline", "version", "agent_type", "source", "status",
                "run_index", "timestamp")

#: Exactly the keys `graph_capsule` signs; it appends `sealed` afterwards.
SIGNED_KEYS = ("type", "ts", "delta", "prev_hash")

#: Ed25519 is optional per interpreter: under 3.15t free-threaded the
#: `cryptography` import fails, `_sign_payload` returns "" and the capsule is
#: sealed=False by design -- the degradation `graph_capsule` documents. Nothing
#: signature-shaped can be asserted there, so those two tests skip rather than
#: pass on a graceful-True verifier.
needs_signing = pytest.mark.skipif(
    not at._CRYPTO_AVAILABLE,
    reason="Ed25519 unavailable on this interpreter: capsule is unsigned, "
           "sealed=False by design")


class FakeStore:
    """Captures `record_event` payloads: no SQLite, no files."""

    events: list = []

    def record_event(self, **kwargs):
        type(self).events.append(kwargs)
        return "evt-1"


@pytest.fixture(autouse=True)
def _clean_store():
    FakeStore.events.clear()
    yield
    FakeStore.events.clear()


def _pipeline(monkeypatch, tmpdir):
    """Pipeline on a fake store and a FIXED chain head (no audit session)."""
    monkeypatch.setattr(np_mod, "LocalMemoryStore", FakeStore)
    monkeypatch.setattr(np_mod, "get_head_hash", lambda *a, **kw: HEAD)
    pipe = NeuralPipeline(max_workers=2, llm_augmentation=False)
    pipe.dlq = DeadLetterQueue(storage_path=Path(tmpdir) / "dead_letters.json")
    pipe.versioner._store = False
    return pipe


def _metrics():
    return {"agent_type": "SB3", "source": "SystemZ", "status": "success",
            "timestamp": "2026-10-16T09:00:00", "train_records": 4}


def _signed_body(cap):
    """Exactly the body `graph_capsule` signed (`sealed` is added after)."""
    return {k: cap[k] for k in SIGNED_KEYS}


def _signature_valid(cap):
    """Does `cap`'s signature validate against the body it claims to sign?"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(
            at.PUBLIC_KEY_PATH.read_bytes())
        message = json.dumps(_signed_body(cap), sort_keys=True,
                             separators=(",", ":"), default=str).encode("utf-8")
        pub.verify(base64.b64decode(cap["signature"]), message)
    except (InvalidSignature, ValueError, OSError, KeyError, TypeError) as exc:
        # Two statements on purpose: the failure is recorded, not swallowed,
        # and a lone `return False` would be a new except_return_default.
        at.note_failure("test-provenance:capsule-recompute", exc)
        return False
    return True


def test_persisted_payload_carries_capsule_bound_to_head(monkeypatch, tmp_path):
    pipe = _pipeline(monkeypatch, tmp_path)
    metrics = _metrics()
    before = dict(metrics)

    pipe._persist_run(metrics)

    cap = metrics["capsule"]
    # Additive: every pre-existing key is written exactly as before.
    # Two keys are added by this path, both deliberately: `capsule` (the signed
    # delta) and `capsule_chain` (verify_capsule_chain's verdict over the chain,
    # NeuralPipeline._persist_run). The equality below covers everything else.
    assert {k: v for k, v in metrics.items()
            if k not in ("capsule", "capsule_chain")} == before
    # The capsule rides in the payload handed to the store, not a side copy.
    assert FakeStore.events[0]["data"] is metrics
    assert FakeStore.events[0]["data"]["capsule"] is cap
    # Bound to the head the helper returned, over the bounded summary.
    assert cap["prev_hash"] == HEAD
    assert cap["type"] == "graph_capsule"
    assert set(cap["delta"]) == set(SUMMARY_KEYS)
    assert cap["delta"]["agent_type"] == "SB3"
    assert cap["delta"]["source"] == "SystemZ"
    assert cap["delta"]["run_index"] == 0
    # ... and it survives the JSON round trip a real store performs.
    assert json.loads(json.dumps(cap, default=str))["delta"] == cap["delta"]


@needs_signing
def test_capsule_signature_recomputes_from_stored_summary(monkeypatch, tmp_path):
    pipe = _pipeline(monkeypatch, tmp_path)
    metrics = _metrics()

    pipe._persist_run(metrics)

    cap = metrics["capsule"]
    assert cap["sealed"] is True
    assert cap["signature"]
    assert _signature_valid(cap) is True


@needs_signing
def test_tampered_summary_fails_recomputation(monkeypatch, tmp_path):
    pipe = _pipeline(monkeypatch, tmp_path)
    metrics = _metrics()
    pipe._persist_run(metrics)

    cap = metrics["capsule"]
    tampered = dict(cap)
    tampered["delta"] = dict(cap["delta"], status="failed")
    assert _signature_valid(tampered) is False
    # The stored capsule is still the one that verifies.
    assert _signature_valid(cap) is True
